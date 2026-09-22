"""End-to-end auto-payment, independent payment oracle and console shortcuts."""
from game.ai.decision_maker import PriorityDecisionRequest
from game.game_actions import PassPriorityAction
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from dataclasses import replace
from itertools import product
from collections import Counter
from types import SimpleNamespace

import pytest
from game.enums import ManaType as M, CardType as T, ZoneType as Z, TurnPhase as P
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.console.demo_game import build_action, ConsoleDecisionMaker
from game.cards.demo_cards import LIGHTNING_BOLT_CAST
from game.rules.lands import basic_land, mana_ability
from game.console.command_choices import feasible
from game.console.command_session import CommandSession
from game.console.priority_choices import can_auto_pass
from game.ai.mana_solver import SourceActivatingManaSolver
from game.mana.mana_value import ManaValue
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.game_actions.data_structs.ability import SubAbilityDefinition
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_loop.game_loop import GameLoop


@pytest.fixture
def game():
    state = State([Player([], ScriptedController(), name='Alice'), Player([], ScriptedController(), name='Bob')])
    state.turn.phase = P.PRECOMBAT_MAIN
    bus = EventBus()
    processor = ActionProcessor(ResolutionEngine(OperationExecutor(), bus))
    return SimpleNamespace(state=state, processor=processor, bus=bus)


def add(game, definition, zone=Z.BATTLEFIELD, owner=None):
    owner = owner or game.state.active_player
    card = Card(definition, owner)
    owner.add_card(card, zone)
    return card


def land(game, colors):
    return add(game, CardDefinition('Mana source', types=frozenset({T.LAND}),
               abilities=frozenset(mana_ability(color) for color in colors)))


def spell(game, cost='{R}', kind=T.INSTANT):
    return add(game, CardDefinition('Paid Bolt', mana_cost=cost, types=frozenset({kind}),
                                   abilities=frozenset({LIGHTNING_BOLT_CAST})), Z.HAND)


COLOR_CHOICES = ((), (M.RED,), (M.BLUE,), (M.RED, M.BLUE))


@pytest.mark.parametrize('sources', list(product(COLOR_CHOICES, repeat=3)))
@pytest.mark.parametrize('cost', ['{R}', '{U}{R}', '{2}{R}', '{U/R}{R}', '{C}'])
def test_auto_solver_matches_independent_source_assignment(game, sources, cost):
    cards = [land(game, colors) for colors in sources]
    player = game.state.active_player
    player.mana_pool.add_pair(M.BLUE, 1)
    req = next(ManaValue(cost).payment_options())[0]
    symbols = [fragment.allowed for fragment in req.frags for _ in range(fragment.amount)]
    minimum = None
    for activated in product(*[(None, *colors) for colors in sources]):
        available = Counter(color for color in activated if color is not None)
        available[M.BLUE] += 1
        for paid in product(*symbols):
            if not (Counter(paid) - available):
                used = sum(color is not None for color in activated)
                minimum = used if minimum is None else min(minimum, used)
    result = SourceActivatingManaSolver().get_mana_plan(req, player, game.state)
    assert (result is None) == (minimum is None)
    if result:
        assert len(result.mana_plan) == minimum
        assert len({action.source for action in result.mana_plan}) == len(result.mana_plan)
    assert not any(card.is_tapped for card in cards)
    assert dict(player.mana_pool) == {M.BLUE: 1}
    assert not game.bus.emitted_events


def test_incremental_builder_offers_unfunded_spell_and_previews_sources(game):
    mountain = land(game, (M.RED,))
    card = spell(game)
    player = game.state.active_player
    assert feasible(LIGHTNING_BOLT_CAST.to_ability(card, player), game.state)
    commands = iter(['bob', 'confirm'])
    output = []
    action = CommandSession(game.state, player, 'play', lambda _: next(commands), output.append).run()
    assert action.source is card
    assert any('Auto-tap:' in line and mountain.command_id in line for line in output)
    assert not mountain.is_tapped
    assert game.processor.process(game.state, action)[0].success
    assert mountain.is_tapped and not player.mana_pool and card.get_zone() == Z.STACK
    activations = [event for event in game.bus.emitted_events if event.key == 'ability_activated']
    assert len(activations) == 1 and activations[0].source is mountain


def test_cancelling_builder_does_not_tap_sources(game):
    mountain = land(game, (M.RED,))
    spell(game)
    commands = iter(['bob', 'cancel'])
    assert CommandSession(game.state, game.state.active_player, 'play', lambda _: next(commands), lambda _: None).run() is None
    assert not mountain.is_tapped and not game.bus.emitted_events


@pytest.mark.parametrize('change', ['tap', 'control', 'zone', 'blink', 'abilities'])
def test_stale_mana_plan_fails_without_paying_or_casting(game, change):
    mountain = land(game, (M.RED,))
    card = spell(game)
    player = game.state.active_player
    action = build_action(game.state, player, f'play {card.key} bob')
    if change == 'tap':
        mountain.is_tapped = True
    elif change == 'control':
        mountain.set_controller(game.state.players[1])
    elif change == 'zone':
        player.move_card(mountain, Z.GRAVEYARD)
    elif change == 'blink':
        player.move_card(mountain, Z.EXILE)
        player.move_card(mountain, Z.BATTLEFIELD)
    else:
        from game.stat_type import STAT_ABILITIES
        mountain.set_base_stat(STAT_ABILITIES, {})
    result = game.processor.process(game.state, action)
    assert not result[0].success
    assert card.get_zone() == Z.HAND and game.state.stack.is_empty() and not player.mana_pool
    assert not game.bus.emitted_events


def test_borrowed_land_is_discovered_by_control(game):
    player, opponent = game.state.players
    mountain = add(game, basic_land('Mountain'), owner=opponent)
    mountain.set_controller(player)
    card = spell(game)
    action = build_action(game.state, player, f'play {card.key} bob')
    assert game.processor.process(game.state, action)[0].success
    assert mountain.is_tapped


def test_tap_cost_reserves_source_and_uses_another_land(game):
    base = mana_ability(M.RED)
    paid = ActivatedAbilityDefinition(key='paid', cost_subdefs=(*base.cost_subdefs, SubAbilityDefinition(mana_cost='{R}')))
    source = add(game, CardDefinition('Dual purpose', types=frozenset({T.ARTIFACT}), abilities=frozenset({base, paid})))
    other = land(game, (M.RED,))
    action = build_action(game.state, game.state.active_player, f'activate {source.key} paid')
    assert [step.source for step in action.cost_generator.mana_solver_result.mana_plan] == [other]
    assert game.processor.process(game.state, action)[0].success
    assert source.is_tapped and other.is_tapped


def test_paid_mana_source_does_not_recurse_or_pay_itself(game):
    base = mana_ability(M.RED)
    paid = replace(base, cost_subdefs=(*base.cost_subdefs, SubAbilityDefinition(mana_cost='{1}')))
    add(game, CardDefinition('Filter', types=frozenset({T.ARTIFACT}), abilities=frozenset({paid})))
    assert game.state.get_mana_sources(game.state.active_player) == ()
    card = spell(game)
    with pytest.raises(ValueError):
        build_action(game.state, game.state.active_player, f'play {card.key} bob')


def test_sick_mana_creature_requires_haste(game):
    creature = add(game, CardDefinition('Mana creature', types=frozenset({T.CREATURE}), power=1, toughness=1,
                                        abilities=frozenset({mana_ability(M.RED)})))
    card = spell(game)
    with pytest.raises(ValueError):
        build_action(game.state, game.state.active_player, f'play {card.key} bob')
    from game.stat_type import STAT_KEYWORDS
    creature.set_base_stat(STAT_KEYWORDS, frozenset({'haste'}))
    assert game.processor.process(game.state, build_action(game.state, game.state.active_player, f'play {card.key} bob'))[0].success


@pytest.mark.parametrize('phase', [P.UPKEEP, P.DRAW, P.BEGIN_COMBAT, P.END_COMBAT, P.END_STEP])
def test_quiet_priority_passes_without_reading_or_advancing_state(game, phase):
    game.state.turn.phase = phase
    land(game, (M.RED,))
    console = ConsoleDecisionMaker(auto_pass=True, read=lambda _: pytest.fail('Unexpected prompt'), write=lambda _: None)
    assert isinstance(console.decide(PriorityDecisionRequest(game.state, game.state.active_player)).value, PassPriorityAction)
    assert game.state.turn.phase == phase


@pytest.mark.parametrize('reason', ['main', 'instant', 'activation', 'pool', 'stack', 'variable'])
def test_auto_pass_preserves_decisions(game, reason):
    player = game.state.active_player
    game.state.turn.phase = P.UPKEEP
    land(game, (M.RED,))
    if reason == 'main':
        game.state.turn.phase = P.PRECOMBAT_MAIN
        add(game, basic_land('Mountain'), Z.HAND)
    elif reason == 'instant':
        spell(game)
    elif reason == 'activation':
        add(game, CardDefinition('Ability', types=frozenset({T.ARTIFACT}), abilities=frozenset({ActivatedAbilityDefinition()})))
    elif reason == 'pool':
        player.mana_pool.add_pair(M.RED, 1)
        spell(game, '{R}{R}')
    elif reason == 'stack':
        card = spell(game)
        assert game.processor.process(game.state, build_action(game.state, player, f'play {card.key} bob'))[0].success
        land(game, (M.RED,))
        spell(game)
    else:
        spell(game, '{X}{R}')
    assert not can_auto_pass(game.state, player)


def test_console_autopass_can_be_disabled(game):
    # Commands are read at an interactive decision, not during a quiet shortcut.
    add(game, basic_land('Mountain'), Z.HAND)
    commands = iter(['autopass off', 'pass'])
    console = ConsoleDecisionMaker(auto_pass=True, read=lambda _: next(commands), write=lambda _: None)
    console.decide(PriorityDecisionRequest(game.state, game.state.active_player)).value
    assert not console.auto_pass


def test_draw_step_still_draws_with_auto_pass(game):
    state = game.state
    state.turn.number = 2
    state.turn.phase = P.DRAW
    drawn = add(game, CardDefinition('Drawn', types=frozenset({T.CREATURE})), Z.DECK)
    console = ConsoleDecisionMaker(auto_pass=True, read=lambda _: pytest.fail('Unexpected prompt'), write=lambda _: None)
    for player in state.players:
        player.controller = console
    GameLoop(None, game.processor).step(state)
    assert drawn.get_zone() == Z.HAND
    assert state.turn.phase == P.PRECOMBAT_MAIN


def test_replaced_mana_production_rolls_back_taps_and_events(game):
    from game.game_actions.resolution.replacement_effects import ReplacementEffectDefinition
    from game.game_actions.mana_effects import AddManaOperation
    mountain = land(game, (M.RED,))
    card = spell(game)
    rule = ReplacementEffectDefinition('no-mana', lambda s, e, op: isinstance(op, AddManaOperation),
                                       lambda s, e, op: (), uses=1).bind()
    game.state.replacement_rules.append(rule)
    result = game.processor.process(game.state, build_action(game.state, game.state.active_player, f'play {card.key} bob'))
    assert not result[0].success
    assert not mountain.is_tapped and card.get_zone() == Z.HAND
    assert rule.remaining_uses == 1 and not game.bus.emitted_events


def test_auto_activation_has_trigger_and_is_not_skipped_when_tapping_matters(game):
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import EventKeyCondition
    captured = []
    class Listener(ScriptedController):
        def decide_trigger_order(self, request):
            captured.extend(request.candidates)
            return super().decide_trigger_order(request)
    player = game.state.active_player
    player.controller = Listener()
    trigger = TriggerAbilityDefinition(key='mana-watch', condition=EventKeyCondition('ability_activated'))
    add(game, CardDefinition('Watch', types=frozenset({T.ENCHANTMENT}), triggers=frozenset({trigger})))
    land(game, (M.RED,))
    game.state.turn.phase = P.UPKEEP
    assert not can_auto_pass(game.state, player)
    card = spell(game)
    assert game.processor.process(game.state, build_action(game.state, player, f'play {card.key} bob'))[0].success
    assert len(captured) == 1 and captured[0].key == 'mana-watch'


@pytest.mark.parametrize("reason", ["main", "pool", "stack"])
def test_auto_pass_skips_windows_without_a_meaningful_action(game, reason):
    state, player = game.state, game.state.active_player
    land(game, (M.RED,))
    state.turn.phase = P.PRECOMBAT_MAIN if reason == "main" else P.UPKEEP
    if reason == "pool":
        player.mana_pool.add_pair(M.RED, 1)
    elif reason == "stack":
        card = spell(game)
        assert game.processor.process(state, build_action(state, player, f'play {card.key} bob'))[0].success
    assert can_auto_pass(state, player)
