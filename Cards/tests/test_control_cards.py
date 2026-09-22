"""Control spells through real casting, targeting, payment and resolution."""
from game.ai.decision_maker import ScryOption, DecisionResult, PriorityDecisionRequest

from pathlib import Path
from dataclasses import replace
import pytest

from game.ai.simple_agent import SimpleAgent
from game.cards.catalog import game_catalog
from game.cards.decks import DeckList
from game.cards.starter_cards import _trigger
from game.cards.starter_support import EventCondition
from game.enums import CardType as T, ZoneType as Z, TurnPhase as P, ManaType as M
from game.game_state import State, Player, Card
from game.game_actions.control_effects import SpellTargetSpec
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.console.demo_game import build_action


@pytest.fixture
def game():
    state = State([Player([], SimpleAgent(), idx=0), Player([], SimpleAgent(), idx=1)])
    state.turn.phase = P.PRECOMBAT_MAIN
    engine = ResolutionEngine(OperationExecutor(), EventBus())
    return state, engine, ActionProcessor(engine)


def add(game, name, zone=Z.HAND, player=0):
    state = game[0]
    definition = game_catalog()[name] if isinstance(name, str) else name
    card = Card(definition, state.players[player])
    card.owner.add_card(card, zone)
    return card


def cast(game, card, target=None):
    state, engine, processor = game
    state.priority.current_player = card.owner
    for mana in M:
        card.owner.mana_pool.add_pair(mana, 10)
    command = f"play {card.command_id}"
    if target is not None:
        command += " " + target.command_id
    action = build_action(state, card.owner, command)
    assert all(r.success for r in processor.process(state, action))
    return action


def resolve(game):
    return game[1].resolve(game[0], game[0].stack.pop())


def test_counter_removes_spell_and_prevents_its_resolution(game):
    victim = add(game, "Sworn Guardian")
    cast(game, victim)
    counter = add(game, "Counterspell", player=1)
    cast(game, counter, victim)
    assert resolve(game).success
    assert victim.get_zone() == counter.get_zone() == Z.GRAVEYARD
    assert game[0].stack.is_empty()


def test_counter_can_be_countered(game):
    creature = add(game, "Sworn Guardian")
    cast(game, creature)
    first = add(game, "Counterspell", player=1)
    cast(game, first, creature)
    second = add(game, "Counterspell")
    cast(game, second, first)
    assert resolve(game).success
    assert first.get_zone() == Z.GRAVEYARD
    assert resolve(game).success
    assert creature.get_zone() == Z.BATTLEFIELD


@pytest.mark.parametrize("name", ["Inescapable Blaze", "Supreme Verdict"])
def test_uncounterable_spells_remain_legal_targets(game, name):
    spell = add(game, name)
    if name == "Inescapable Blaze":
        target = add(game, "Sworn Guardian", Z.BATTLEFIELD, 1)
        cast(game, spell, target)
    else:
        cast(game, spell)
    counter = add(game, "Counterspell", player=1)
    cast(game, counter, spell)
    assert resolve(game).success
    assert spell.get_zone() == Z.STACK
    assert len(game[0].stack.items) == 1


def test_counter_requires_pending_spell_not_a_permanent(game):
    target = add(game, "Sworn Guardian", Z.BATTLEFIELD)
    counter = add(game, "Counterspell", player=1)
    game[0].priority.current_player = counter.owner
    for _ in range(2):
        add(game, "Island", Z.BATTLEFIELD, 1)
    with pytest.raises(ValueError):
        build_action(game[0], counter.owner, f"play {counter.command_id} {target.command_id}")
    assert counter.get_zone() == Z.HAND


def test_counter_fizzles_when_target_left_stack(game):
    victim = add(game, "Sworn Guardian")
    cast(game, victim)
    counter = add(game, "Counterspell", player=1)
    cast(game, counter, victim)
    victim.owner.move_card(victim, Z.EXILE)
    result = resolve(game)
    assert not result.success
    assert victim.get_zone() == Z.EXILE
    assert counter.get_zone() == Z.GRAVEYARD


def test_counter_uses_zone_replacement_for_graveyard_move(game):
    from game.game_actions.resolution.replacement_effects import replace_zone
    victim = add(game, "Sworn Guardian")
    cast(game, victim)
    counter = add(game, "Counterspell", player=1)
    cast(game, counter, victim)
    game[0].replacement_rules.append(replace_zone("exile", Z.STACK, Z.GRAVEYARD, Z.EXILE).bind())
    assert resolve(game).success
    assert victim.get_zone() == Z.EXILE
    assert game[0].stack.is_empty()


def test_opt_scries_before_drawing(game):
    bottom = add(game, "Island", Z.DECK)
    top = add(game, "Plains", Z.DECK)
    player = game[0].players[0]
    player.controller.decide_scry = lambda request: DecisionResult(ScryOption((), request.candidates))
    spell = add(game, "Opt")
    cast(game, spell)
    assert resolve(game).success
    assert bottom.get_zone() == Z.HAND
    assert top.get_zone() == Z.DECK


def test_verdict_destroys_both_sides_and_preserves_indestructible(game):
    first = add(game, "Sworn Guardian", Z.BATTLEFIELD)
    second = add(game, "Cloudkin Seer", Z.BATTLEFIELD, 1)
    definition = replace(game_catalog()["Sworn Guardian"], keywords=frozenset({"indestructible"}))
    survivor = add(game, definition, Z.BATTLEFIELD, 1)
    land = add(game, "Island", Z.BATTLEFIELD)
    spell = add(game, "Supreme Verdict")
    cast(game, spell)
    assert resolve(game).success
    assert first.get_zone() == second.get_zone() == Z.GRAVEYARD
    assert survivor.get_zone() == land.get_zone() == Z.BATTLEFIELD


def test_verdict_death_trigger_sees_other_simultaneously_dying_creatures(game):
    from game.cards.starter_cards import GainLifeEffect
    condition = EventCondition(
        lambda state, tr: tr.event.key == "card_moved"
        and tr.event.payload.get("from") == "BATTLEFIELD"
        and tr.event.payload.get("to") == "GRAVEYARD",
        looks_back=True,
    )
    definition = replace(
        game_catalog()["Sworn Guardian"],
        triggers=frozenset({_trigger("observe_deaths", condition, (GainLifeEffect("gain", 1),))}),
    )
    add(game, definition, Z.BATTLEFIELD)
    add(game, "Sworn Guardian", Z.BATTLEFIELD, 1)
    spell = add(game, "Supreme Verdict")
    cast(game, spell)
    assert resolve(game).success
    assert len(game[0].stack.items) == 2


def test_counter_mana_solver_requires_two_blue_sources(game):
    victim = add(game, "Sworn Guardian")
    cast(game, victim)
    counter = add(game, "Counterspell", player=1)
    game[0].priority.current_player = counter.owner
    island = add(game, "Island", Z.BATTLEFIELD, 1)
    plains = add(game, "Plains", Z.BATTLEFIELD, 1)
    with pytest.raises(ValueError):
        build_action(game[0], counter.owner, f"play {counter.command_id} {victim.command_id}")
    second = add(game, "Island", Z.BATTLEFIELD, 1)
    action = build_action(game[0], counter.owner, f"play {counter.command_id} {victim.command_id}")
    assert all(r.success for r in game[2].process(game[0], action))
    assert island.is_tapped and second.is_tapped and not plains.is_tapped


def test_stage1_deck_is_playable_and_target_list_has_no_placeholder_definitions():
    root = Path(__file__).resolve().parents[1] / "data/decks/control"
    playable = DeckList.from_arena((root / "azorius_stage1.txt").read_text())
    assert len(playable.resolve(game_catalog())) == 60
    target = DeckList.from_arena((root / "azorius_target.txt").read_text())
    assert target.size == 60
    with pytest.raises(ValueError, match="missing card definitions"):
        target.resolve(game_catalog())


def test_counter_does_not_target_triggered_ability(game):
    seer = add(game, "Cloudkin Seer")
    cast(game, seer)
    assert resolve(game).success
    assert len(game[0].stack.items) == 1
    counter = add(game, "Counterspell", player=1)
    assert list(SpellTargetSpec().generate_candidates(counter, counter.owner, game[0])) == []


def test_ai_counter_targets_enemy_spell(game):
    spell = add(game, "Sworn Guardian")
    cast(game, spell)
    counter = add(game, "Counterspell", player=1)
    for _ in range(2):
        add(game, "Island", Z.BATTLEFIELD, 1)
    game[0].priority.current_player = counter.owner
    action = counter.owner.controller.decide(PriorityDecisionRequest(game[0], counter.owner)).value
    assert action.source is counter
    assert counter.owner.controller._targets(action) == (spell,)
