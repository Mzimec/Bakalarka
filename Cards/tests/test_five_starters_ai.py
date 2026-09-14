"""Mechanic regressions and experiment contracts for all five starter decks."""
from dataclasses import replace
from types import SimpleNamespace
import json
from pathlib import Path

import pytest
from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.cards.starter_cards import starter_catalog
from game.cards.starter_support import ScryOperation, FightOperation, SkipUntapOperation
from game.enums import CardType as T, ZoneType as Z, ManaType as M, TurnPhase as P, CounterType as C
from game.game_state import State, Player, Card, CardDefinition
from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.generation.command_action_builder import chosen_action
from game.console.demo_game import build_action, ConsoleDecisionMaker
from game.game_loop.game_loop import UntapPhaseController
from game.ai.simple_agent import SimpleAgent
from game.simulation.match_runner import run_match, run_tournament
from game.rules.lands import basic_land
from game.operations.card_operations import MoveCardOperation, DamageCreatureOperation, DamagePlayerOperation


@pytest.fixture
def game():
    state = State([Player([], SimpleAgent(), name='Alice'), Player([], SimpleAgent(), name='Bob')])
    state.turn.phase = P.PRECOMBAT_MAIN
    bus = EventBus()
    engine = ResolutionEngine(OperationExecutor(), bus)
    return SimpleNamespace(state=state, engine=engine, processor=ActionProcessor(engine), bus=bus)


def add(game, name, zone=Z.BATTLEFIELD, player=0):
    definition = starter_catalog()[name] if isinstance(name, str) else name
    card = Card(definition, game.state.players[player])
    card.owner.add_card(card, zone)
    return card


def resolve(game, *operations):
    return game.engine.resolve(game.state, ScheduledResolution(FixedExecutionPlan(list(operations)),
                                                               ResolutionContext(controller=game.state.active_player)))


def context(game, source=None):
    return ResolutionContext(controller=game.state.active_player, source=source)


def cast(game, card, *targets):
    player = game.state.active_player
    for color in M:
        player.mana_pool.add_pair(color, 20)
    definition = next(d for d in card.get_ability_defs(game.state).values() if d.is_spell)
    action = chosen_action(definition.to_ability(card, player), game.state, targets)
    assert game.processor.process(game.state, action)[0].success
    assert game.engine.resolve(game.state, game.state.stack.pop()).success


def settle_stack(game):
    while not game.state.stack.is_empty():
        assert game.engine.resolve(game.state, game.state.stack.pop()).success


@pytest.mark.parametrize('color', ARENA_STARTERS)
def test_all_five_decks_resolve_to_sixty_real_cards(color):
    cards = load_arena_starter(color).resolve(starter_catalog())
    assert len(cards) == 60
    assert all(card.abilities or T.LAND in card.types for card in cards)


REFERENCE = json.loads((Path(__file__).resolve().parents[1] / 'data/starter_card_reference.json').read_text())


@pytest.mark.parametrize('record', REFERENCE, ids=lambda record: record['name'])
def test_reference_characteristics_match_catalog(record):
    from game.mana.mana_value import ManaValue
    card = starter_catalog()[record['name']]
    assert card.mana_cost == ManaValue(record['mana_cost']).to_immutable()
    if record['power'] and record['power'] != '*':
        assert (card.power, card.toughness) == (int(record['power']), int(record['toughness']))


def test_scry_changes_draw_order_without_changing_zones(game):
    a, b, c = [add(game, 'Island', Z.DECK) for _ in range(3)]
    player = game.state.active_player
    player.controller.choose_scry = lambda s, p, cards: ((b,), (c,))
    assert resolve(game, ScryOperation(context(game), 2)).success
    assert tuple(player.deck.values()) == (c, a, b)
    assert player.draw(game.state) is b


def test_nightmare_cda_updates_and_applies_outside_battlefield(game):
    nightmare = add(game, 'Nightmare', Z.HAND)
    swamp = add(game, 'Swamp')
    assert nightmare.get_power(game.state) == 1
    add(game, 'Swamp')
    assert nightmare.get_toughness(game.state) == 2
    swamp.owner.move_card(swamp, Z.GRAVEYARD)
    assert nightmare.get_power(game.state) == 1


def test_casting_reductions_do_not_change_printed_mana_cost(game):
    add(game, 'Warden of Evos Isle')
    seer = add(game, 'Cloudkin Seer', Z.HAND)
    words = add(game, 'Winged Words', Z.HAND)
    assert seer.get_mana_cost(game.state).cmc() == 3
    assert seer.get_casting_cost(game.state).cmc() == 2
    assert words.get_mana_cost(game.state).cmc() == 3
    assert words.get_casting_cost(game.state).cmc() == 2



@pytest.mark.parametrize('flying_name', [
    name for name, definition in starter_catalog().items()
    if T.CREATURE in definition.types and 'flying' in definition.keywords
])
def test_winged_words_casts_with_two_islands_and_a_flyer(game, flying_name):
    """!
    @brief Cast through the console and payment pipeline with each starter flyer.
    """
    words = add(game, 'Winged Words', Z.HAND)
    islands = [add(game, 'Island') for _ in range(2)]
    # Read the undiscounted cost first to exercise effect-cache invalidation.
    assert words.get_casting_cost(game.state).cmc() == 3
    add(game, flying_name)
    drawn = [add(game, 'Island', Z.DECK) for _ in range(2)]

    action = build_action(game.state, game.state.active_player, f'play {words.key}')
    assert all(not island.is_tapped for island in islands)
    assert game.processor.process(game.state, action)[0].success
    assert words.get_zone() == Z.STACK
    assert all(island.is_tapped for island in islands)
    assert not game.state.active_player.mana_pool
    # Resolve cast triggers above the spell before checking its draw effect.
    settle_stack(game)
    assert words.get_zone() == Z.GRAVEYARD
    assert all(card.get_zone() == Z.HAND for card in drawn)


def test_winged_words_requires_own_battlefield_flyer(game):
    """!
    @brief Remove the discount when the last controlled flyer leaves play.
    """
    words = add(game, 'Winged Words', Z.HAND)
    for _ in range(2):
        add(game, 'Island')
    add(game, 'Cloudkin Seer', player=1)
    flyer = add(game, 'Cloudkin Seer', Z.HAND)
    assert words.get_casting_cost(game.state).cmc() == 3
    with pytest.raises(ValueError):
        build_action(game.state, game.state.active_player, f'play {words.key}')
    flyer.owner.move_card(flyer, Z.BATTLEFIELD)
    assert words.get_casting_cost(game.state).cmc() == 2
    flyer.owner.move_card(flyer, Z.GRAVEYARD)
    assert words.get_casting_cost(game.state).cmc() == 3
    with pytest.raises(ValueError):
        build_action(game.state, game.state.active_player, f'play {words.key}')


def test_caryatid_produces_two_mana_in_one_activation_and_leaves_excess(game):
    caryatid = add(game, 'Ilysian Caryatid')
    from game.stat_type import STAT_KEYWORDS
    caryatid.set_base_stat(STAT_KEYWORDS, frozenset({'haste'}))
    add(game, 'Rumbling Baloth')
    spell = add(game, 'Unsummon', Z.HAND)
    victim = add(game, 'Typhoid Rats', player=1)
    action = build_action(game.state, game.state.active_player, f'play {spell.key} {victim.key}')
    assert len(action.cost_generator.mana_solver_result.mana_plan) == 1
    assert game.processor.process(game.state, action)[0].success
    assert caryatid.is_tapped and dict(game.state.active_player.mana_pool) == {M.BLUE: 1}


def test_fight_deathtouch_and_simultaneous_death(game):
    rats = add(game, 'Typhoid Rats')
    baloth = add(game, 'Rumbling Baloth', player=1)
    assert resolve(game, FightOperation(context(game, rats), rats, baloth)).success
    assert rats.get_zone() == baloth.get_zone() == Z.GRAVEYARD


def test_rabid_bite_uses_both_targets_and_only_deals_one_way(game):
    baloth = add(game, 'Rumbling Baloth')
    rats = add(game, 'Typhoid Rats', player=1)
    bite = add(game, 'Rabid Bite', Z.HAND)
    cast(game, bite, (rats,), (baloth,))  # Slots are ordered by key: other, own.
    assert rats.get_zone() == Z.GRAVEYARD
    assert baloth.get_zone() == Z.BATTLEFIELD and baloth.state.damage_marked == 0


def test_stony_strength_targets_counter_and_untap(game):
    target = add(game, 'Rumbling Baloth')
    target.is_tapped = True
    cast(game, add(game, 'Stony Strength', Z.HAND), (target,))
    assert not target.is_tapped and target.state.counters[C.PLUS_ONE] == 1


def test_waterknot_taps_on_entry_and_prevents_untap(game):
    target = add(game, 'Rumbling Baloth')
    cast(game, add(game, 'Waterknot', Z.HAND), (target,))
    settle_stack(game)
    assert target.is_tapped
    UntapPhaseController().execute_turn_based_actions(game.state)
    assert target.is_tapped


def test_sleep_skip_expires_even_if_control_changed(game):
    target = add(game, 'Rumbling Baloth')
    resolve(game, SkipUntapOperation(context(game), target, game.state.active_player))
    target.set_controller(game.state.players[1])
    UntapPhaseController().execute_turn_based_actions(game.state)
    target.set_controller(game.state.active_player)
    target.is_tapped = True
    UntapPhaseController().execute_turn_based_actions(game.state)
    assert not target.is_tapped


def test_murder_respects_indestructible(game):
    target = add(game, replace(starter_catalog()['Rumbling Baloth'], keywords=frozenset({'indestructible'})))
    cast(game, add(game, 'Murder', Z.HAND), (target,))
    assert target.get_zone() == Z.BATTLEFIELD


def test_eternal_thirst_grants_trigger_to_enchanted_creature(game):
    target = add(game, 'Rumbling Baloth')
    victim = add(game, 'Typhoid Rats', player=1)
    cast(game, add(game, 'Eternal Thirst', Z.HAND), (target,))
    assert target.has_keyword(game.state, 'lifelink')
    resolve(game, MoveCardOperation(context(game), victim, Z.GRAVEYARD))
    settle_stack(game)
    assert target.state.counters[C.PLUS_ONE] == 1


def test_packhunter_counters_other_copies(game):
    first = add(game, 'Baloth Packhunter')
    second = add(game, 'Baloth Packhunter', Z.HAND)
    cast(game, second)
    settle_stack(game)
    assert first.state.counters[C.PLUS_ONE] == 2 and not second.state.counters


def test_world_shaper_returns_lands_tapped(game):
    source = add(game, 'World Shaper')
    land = add(game, 'Forest', Z.GRAVEYARD)
    resolve(game, MoveCardOperation(context(game), source, Z.GRAVEYARD))
    settle_stack(game)
    assert land.get_zone() == Z.BATTLEFIELD and land.is_tapped


def test_unicorn_requires_all_able_blockers(game):
    unicorn = add(game, 'Prized Unicorn')
    blockers = [add(game, 'Rumbling Baloth', player=1) for _ in range(2)]
    game.state.turn.number = 2
    game.state.active_player.last_turn_started = 2
    game.state.turn.phase = P.DECLARE_ATTACKERS
    game.state.combat.begin()
    game.state.combat.declare_attackers(game.state.active_player, {unicorn: game.state.players[1]})
    game.state.turn.phase = P.DECLARE_BLOCKERS
    with pytest.raises(ValueError):
        game.state.combat.validate_blockers(game.state.players[1], {blockers[0]: unicorn})
    choices = game.state.players[1].controller.choose_blockers(game.state, game.state.players[1])
    assert len(game.state.combat.validate_blockers(game.state.players[1], choices)) == 2


def test_sengir_tracks_damage_then_death(game):
    sengir = add(game, 'Sengir Vampire')
    victim = add(game, 'Rumbling Baloth', player=1)
    resolve(game, DamageCreatureOperation(context(game, sengir), victim, 1))
    resolve(game, MoveCardOperation(context(game), victim, Z.GRAVEYARD))
    settle_stack(game)
    assert sengir.state.counters[C.PLUS_ONE] == 1


def test_inspect_shows_types_and_cost(game):
    card = add(game, 'Cloudkin Seer', Z.HAND)
    commands = iter([f'inspect {card.key}', 'pass'])
    output = []
    ConsoleDecisionMaker(read=lambda _: next(commands), write=output.append).get_action(game.state, game.state.active_player)
    assert any('Types: CREATURE' in line for line in output)
    assert 'Mana cost: {2}{U}' in output


def test_ai_logs_limits_as_unfinished_and_never_overwrites(tmp_path):
    result = run_match(('white', 'red'), tmp_path / 'match.jsonl', max_turns=2)
    assert result.status == 'turn_limit'
    rows = [json.loads(line) for line in (tmp_path / 'match.jsonl').read_text().splitlines()]
    assert rows[0]['kind'] == 'match' and rows[-1]['status'] == 'turn_limit'
    assert [row['seq'] for row in rows] == list(range(1, len(rows) + 1))
    with pytest.raises(FileExistsError):
        run_match(('white', 'red'), tmp_path / 'match.jsonl')


def test_tournament_schedules_both_starters_and_records_all_pairings(tmp_path):
    results = run_tournament(tmp_path / 'league', max_turns=1)
    assert len(results) == 20
    assert all(result.status == 'turn_limit' for result in results)
    assert len({(result.colors, result.starting_player) for result in results}) == 20
    assert (tmp_path / 'league/summary.txt').exists()


def test_simultaneous_lifelink_is_one_gain_per_source_and_triggers_priest(game):
    priest = add(game, 'Hallowed Priest')
    add(game, 'Angel of Vitality')
    source = add(game, replace(starter_catalog()['Rumbling Baloth'], keywords=frozenset({'lifelink'})))
    targets = [add(game, 'Rumbling Baloth', player=1) for _ in range(2)]
    before = game.state.active_player.health
    game.engine.resolve_simultaneous(game.state, [DamageCreatureOperation(context(game, source), target, 1) for target in targets])
    settle_stack(game)
    assert game.state.active_player.health == before + 3
    assert priest.state.counters[C.PLUS_ONE] == 1


def test_bundle_cannot_split_caryatid_output_between_colors(game):
    from game.ai.mana_solver import SourceActivatingManaSolver
    from game.mana.mana_value import ManaValue
    from game.stat_type import STAT_KEYWORDS
    source = add(game, 'Ilysian Caryatid')
    source.set_base_stat(STAT_KEYWORDS, frozenset({'haste'}))
    add(game, 'Rumbling Baloth')
    requirement = next(ManaValue('{U}{G}').payment_options())[0]
    assert SourceActivatingManaSolver().get_mana_plan(requirement, game.state.active_player, game.state) is None
    requirement = next(ManaValue('{G}{G}').payment_options())[0]
    plan = SourceActivatingManaSolver().get_mana_plan(requirement, game.state.active_player, game.state)
    assert len(plan.mana_plan) == 1 and dict(plan.payment) == {M.GREEN: 2}


def test_seed_repeats_decisions_and_events(tmp_path):
    for name in ('first', 'second'):
        result = run_match(('blue', 'green'), tmp_path / f'{name}.jsonl', seed=12, max_turns=4)
        assert result.status == 'turn_limit'
    def rows(name):
        records = [json.loads(line) for line in (tmp_path / f'{name}.jsonl').read_text().splitlines()]
        records[-1].pop('log')
        records[-1].pop('elapsed_seconds')
        return records
    assert rows('first') == rows('second')


def test_lki_keeps_characteristics_after_zone_reset(game):
    from game.game_actions.resolution.event_bus import capture_card_information

    card = add(game, 'Sengir Vampire')
    card.state.counters[C.PLUS_ONE] = 2
    card.set_controller(game.state.players[1])
    snapshot = capture_card_information(game.state)[id(card)]
    resolve(game, MoveCardOperation(context(game), card, Z.GRAVEYARD))
    assert snapshot.controller is game.state.players[1]
    assert snapshot.power == 6 and snapshot.toughness == 6
    assert snapshot.counters[C.PLUS_ONE] == 2
    assert snapshot.zone == Z.BATTLEFIELD
    assert snapshot.zone_revision != card.zone_revision
    assert not card.state.counters
    with pytest.raises(TypeError):
        snapshot.counters[C.PLUS_ONE] = 99


def test_sengir_simultaneous_death_uses_old_source_identity(game):
    source = add(game, 'Sengir Vampire')
    victim = add(game, 'Rumbling Baloth', player=1)
    resolve(game, DamageCreatureOperation(context(game, source), victim, 1))
    old_revision = source.zone_revision
    game.engine.resolve_simultaneous(game.state, [
        MoveCardOperation(context(game), source, Z.GRAVEYARD),
        MoveCardOperation(context(game), victim, Z.GRAVEYARD),
    ])
    triggers = [trigger for event in game.bus.emitted_events
                for trigger in (event.triggered_abilities or ())
                if trigger.source is source and event.source is victim]
    assert len(triggers) == 1
    assert triggers[0].source_revision == old_revision
    assert not game.state.stack.is_empty()
    # Returning the physical card cannot redirect its old trigger to the new object.
    resolve(game, MoveCardOperation(context(game), source, Z.BATTLEFIELD))
    settle_stack(game)
    assert not source.state.counters


def test_pending_source_counter_trigger_does_not_follow_blink(game):
    source = add(game, 'Sengir Vampire')
    victim = add(game, 'Rumbling Baloth', player=1)
    resolve(game, DamageCreatureOperation(context(game, source), victim, 1))
    resolve(game, MoveCardOperation(context(game), victim, Z.GRAVEYARD))
    assert not game.state.stack.is_empty()
    resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
    resolve(game, MoveCardOperation(context(game), source, Z.BATTLEFIELD))
    settle_stack(game)
    assert not source.state.counters


def test_control_change_preserves_incarnation_but_zone_change_resets_it(game):
    card = add(game, 'Rumbling Baloth')
    card.state.counters[C.PLUS_ONE] = 2
    card.state.damage_marked = 1
    card.is_tapped = True
    revision = card.zone_revision
    card.set_controller(game.state.players[1])
    assert card.zone_revision == revision
    assert card.state.counters[C.PLUS_ONE] == 2
    assert card.state.damage_marked == 1 and card.is_tapped
    resolve(game, MoveCardOperation(context(game), card, Z.EXILE))
    resolve(game, MoveCardOperation(context(game), card, Z.BATTLEFIELD))
    assert card.zone_revision == revision + 2
    assert card.get_controller(game.state) is card.owner
    assert not card.state.counters and card.state.damage_marked == 0
    assert not card.is_tapped


def test_new_sengir_incarnation_does_not_inherit_damage_history(game):
    source = add(game, 'Sengir Vampire')
    victim = add(game, 'Rumbling Baloth', player=1)
    resolve(game, DamageCreatureOperation(context(game, source), victim, 1))
    resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
    resolve(game, MoveCardOperation(context(game), source, Z.BATTLEFIELD))
    resolve(game, MoveCardOperation(context(game), victim, Z.GRAVEYARD))
    assert game.state.stack.is_empty()
    assert not source.state.counters


@pytest.mark.parametrize("leave_again", [False, True])
def test_phoenix_return_follows_only_the_triggering_zone_change(game, leave_again):
    source = add(game, 'Immortal Phoenix')
    resolve(game, MoveCardOperation(context(game), source, Z.GRAVEYARD))
    pending = game.state.stack.pop()
    if leave_again:
        resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
        resolve(game, MoveCardOperation(context(game), source, Z.GRAVEYARD))
    assert game.engine.resolve(game.state, pending).success
    assert source.get_zone() == (Z.GRAVEYARD if leave_again else Z.HAND)


def test_trigger_context_preserves_source_lki_through_stack(game):
    source = add(game, 'Immortal Phoenix')
    source.state.counters[C.PLUS_ONE] = 2
    resolve(game, MoveCardOperation(context(game), source, Z.GRAVEYARD))
    pending = game.state.stack.peek().action_resolution.context
    assert pending.source_last_known.power == 7
    assert pending.source_last_known.controller is game.state.active_player
    assert pending.matches_source_incarnation(zone_changes=1)
    assert not pending.matches_source_incarnation()


@pytest.mark.parametrize('leave', [False, True])
def test_activated_stack_ability_reads_resolution_or_departure_power(game, leave):
    from game.cards.starter_cards import _activated_ability
    from game.cards.starter_support import RuleEffect
    from game.stat_type import STAT_KEYWORDS
    from game.operations.card_operations import DamagePlayerOperation

    def damage(state, ctx):
        yield DamagePlayerOperation(ctx, state.players[1], ctx.source_information(state).power)
    definition = _activated_ability('last_power', action_effects=(RuleEffect('last_power', damage),))
    source = add(game, replace(starter_catalog()['Rumbling Baloth'],
                               abilities=frozenset({definition})))
    action = chosen_action(definition.to_ability(source, game.state.active_player), game.state, ())
    assert game.processor.process(game.state, action)[0].success
    source.state.counters[C.PLUS_ONE] = 2
    source.set_base_stat(STAT_KEYWORDS, frozenset({'lifelink'}))
    old_revision = source.zone_revision
    if leave:
        resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
        resolve(game, MoveCardOperation(context(game), source, Z.BATTLEFIELD))
        source.state.counters[C.PLUS_ONE] = 20
    before = [p.health for p in game.state.players]
    settle_stack(game)
    assert game.state.players[1].health == before[1] - 6
    assert game.state.players[0].health == before[0] + 6
    damage_event = next(e for e in reversed(game.bus.emitted_events) if e.key == 'damage_dealt')
    assert damage_event.payload['source_revision'] == old_revision


def test_simultaneous_departure_keeps_anthem_in_source_lki(game):
    from game.cards.starter_cards import _activated_ability
    from game.cards.starter_support import RuleEffect
    from game.operations.card_operations import DamagePlayerOperation

    def damage(state, ctx):
        yield DamagePlayerOperation(ctx, state.players[1], ctx.source_information(state).power)
    definition = _activated_ability('last_power', action_effects=(RuleEffect('last_power', damage),))
    anthem = add(game, 'Goblin Trashmaster')
    source = add(game, replace(starter_catalog()['Goblin Tunneler'], abilities=frozenset({definition})))
    assert game.processor.process(game.state, chosen_action(
        definition.to_ability(source, game.state.active_player), game.state, ()))[0].success
    expected = source.get_power(game.state)
    game.engine.resolve_simultaneous(game.state, [
        MoveCardOperation(context(game), anthem, Z.GRAVEYARD),
        MoveCardOperation(context(game), source, Z.GRAVEYARD),
    ])
    before = game.state.players[1].health
    settle_stack(game)
    assert game.state.players[1].health == before - expected


def test_event_relative_etb_buff_does_not_follow_returned_creature(game):
    add(game, 'Ogre Battledriver')
    creature = add(game, 'Rumbling Baloth', Z.HAND)
    resolve(game, MoveCardOperation(context(game), creature, Z.BATTLEFIELD))
    old_trigger = game.state.stack.pop()
    resolve(game, MoveCardOperation(context(game), creature, Z.EXILE))
    resolve(game, MoveCardOperation(context(game), creature, Z.BATTLEFIELD))
    assert game.engine.resolve(game.state, old_trigger).success
    assert creature.get_power(game.state) == 4
    assert not creature.has_keyword(game.state, 'haste')
    settle_stack(game)
    assert creature.get_power(game.state) == 6


def test_lki_retains_name_colors_keywords_and_ability_definitions(game):
    from game.game_actions.resolution.event_bus import capture_single_card
    from game.stat_type import STAT_COLORS, STAT_KEYWORDS
    source = add(game, 'Sengir Vampire')
    info = capture_single_card(game.state, source)
    source.set_base_stat(STAT_COLORS, frozenset({M.GREEN}))
    source.set_base_stat(STAT_KEYWORDS, frozenset())
    assert info.name == 'Sengir Vampire'
    assert info.colors == frozenset({M.BLACK})
    assert 'flying' in info.keywords
    assert info.triggers and info.abilities


def test_cost_rollback_restores_departure_history(game):
    from game.game_actions.resolution.cost_transaction import CostTransaction
    source = add(game, 'Rumbling Baloth')
    revision = source.zone_revision
    previous = dict(getattr(source, '_incarnation_history', {}))
    transaction = CostTransaction(game.state)
    MoveCardOperation(context(game), source, Z.GRAVEYARD).execute(game.state)
    assert revision in source._incarnation_history
    transaction.rollback()
    assert source.zone_revision == revision and source.get_zone() == Z.BATTLEFIELD
    assert getattr(source, '_incarnation_history', {}) == previous


def test_lki_keeps_distinct_departed_incarnations(game):
    source = add(game, 'Rumbling Baloth')
    first = replace(context(game, source), source_revision=source.zone_revision)
    source.state.counters[C.PLUS_ONE] = 1
    resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
    resolve(game, MoveCardOperation(context(game), source, Z.BATTLEFIELD))
    second = replace(context(game, source), source_revision=source.zone_revision)
    source.state.counters[C.PLUS_ONE] = 3
    resolve(game, MoveCardOperation(context(game), source, Z.EXILE))
    assert first.source_information(game.state).power == 5
    assert second.source_information(game.state).power == 7
