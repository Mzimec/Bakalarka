"""Layer graph reuse must preserve live targets, characteristics and rollback."""
from dataclasses import replace

import pytest

from game.enums import CardType, ZoneType, TurnPhase
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_state.modifier import (
    ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
    PermanentDuration, TimeStampDuration, TimeStamp, DynamicTargetingStrategy,
    AddIntModifier, AddSetModifier,
)
from game.game_state.registers.card_register import IK_POWER, IK_HAS_DAMAGE, IK_TAPPED, IK_KEY
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.target.continuous_targets import CardIncarnationsSpec, SourceControllerLifeSpec
from game.target.target_spec import QueryTargetSpec
from game.stat_type import STAT_POWER, STAT_KEYWORDS
from helper.query_system.query import EqQuery


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    player = state.players[0]
    unit = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), player)
    player.add_card(unit, ZoneType.BATTLEFIELD)
    return state, unit


def add_bonus(state, unit, *, spec=None, duration=None, modifiers=None, key="bonus"):
    definition = ContinuousEffectDefinition(
        duration or PermanentDuration(), unit, state.time_stamp,
        DynamicTargetingStrategy(spec or CardIncarnationsSpec(((unit, unit.zone_revision),))),
        modifiers or {STAT_POWER: [AddIntModifier(1)]},
    )
    effect = ContinuousEffect(key, definition, ContinuousEffectState(set()))
    state.add_continuous_effect(effect)
    return effect


def refresh(state):
    state._effects_dirty = True
    state.refresh_continuous_effects()


def test_tap_damage_and_timestamp_reuse_graph_but_update_runtime_indexes(board, monkeypatch):
    state, unit = board
    effect = add_bonus(state, unit)
    rebuilds = state._continuous_graph_rebuilds
    monkeypatch.setattr(ContinuousEffect, "detach", lambda *args: pytest.fail("Unchanged graph detached"))
    unit.is_tapped = True
    assert unit in state.query_cards(EqQuery(IK_TAPPED, True))
    unit.state.damage_marked = 1
    assert unit in state.query_cards(EqQuery(IK_HAS_DAMAGE, True))
    state.turn.phase = TurnPhase.UPKEEP
    state.refresh_continuous_effects()
    assert state._continuous_graph_rebuilds == rebuilds
    assert state._continuous_graph_reuses >= 3
    assert effect.state.currently_affected == {unit}
    assert unit.get_power(state) == 3


def test_life_threshold_and_controller_changes_rebuild_targets(board):
    state, unit = board
    add_bonus(state, unit, spec=SourceControllerLifeSpec(25))
    assert unit.get_power(state) == 2
    unit.owner.health = 25
    assert unit in state.query_cards(EqQuery(IK_POWER, 3))
    unit.owner.health = 26
    assert unit.get_power(state) == 3
    assert state._continuous_graph_reuses > 0
    unit.set_controller(state.players[1])
    assert unit in state.query_cards(EqQuery(IK_POWER, 2))


def test_leaving_and_returning_does_not_reuse_old_incarnation(board):
    state, unit = board
    effect = add_bonus(state, unit)
    unit.owner.move_card(unit, ZoneType.GRAVEYARD, state)
    assert unit.get_power(state) == 2
    unit.owner.move_card(unit, ZoneType.BATTLEFIELD, state)
    assert unit.get_power(state) == 2
    assert not effect.state.currently_affected


def test_expiration_is_checked_before_reuse(board):
    state, unit = board
    add_bonus(state, unit, duration=TimeStampDuration(TimeStamp(state.turn.number, TurnPhase.CLEANUP)))
    unit.is_tapped = True
    assert unit.get_power(state) == 3
    state.turn.phase = TurnPhase.CLEANUP
    assert unit.get_power(state) == 2
    assert state._cont_effect_manager.get("bonus") is None


def test_base_change_reindexes_and_mutable_modifier_list_rebuilds(board):
    state, unit = board
    effect = add_bonus(state, unit)
    unit.set_base_stat(STAT_POWER, 8)
    assert unit in state.query_cards(EqQuery(IK_POWER, 9))
    before = state._continuous_graph_rebuilds
    effect.modifiers[STAT_POWER].append(AddIntModifier(4))
    refresh(state)
    assert state._continuous_graph_rebuilds > before
    assert unit.get_power(state) == 13


def test_multiple_supported_layers_keep_keywords_and_power(board):
    state, unit = board
    add_bonus(state, unit, modifiers={STAT_POWER: [AddIntModifier(1)],
                                     STAT_KEYWORDS: [AddSetModifier(frozenset({"flying"}))]})
    refresh(state)
    assert state._continuous_graph_reuses > 0
    assert unit.has_keyword(state, "flying") and unit.get_power(state) == 3


@pytest.mark.parametrize("custom", ["target", "modifier"])
def test_unknown_dependencies_keep_full_evaluation(board, custom):
    state, unit = board
    class DynamicBonus(AddIntModifier):
        def modify(self, original):
            return original + unit.owner.health
    spec = QueryTargetSpec(lambda *args: EqQuery(IK_KEY, unit.key)) if custom == "target" else None
    mods = {STAT_POWER: [DynamicBonus(1)]} if custom == "modifier" else None
    add_bonus(state, unit, spec=spec, modifiers=mods)
    before = state._continuous_graph_rebuilds
    refresh(state)
    assert state._continuous_graph_rebuilds == before + 1
    assert not getattr(state, "_continuous_graph_reuses", 0)


def test_unrelated_dynamic_card_still_updates_while_graph_is_reused(board):
    state, unit = board
    class DynamicCard(Card):
        def get_power(self, state):
            return self.owner.health
    dynamic = DynamicCard(unit.definition, unit.owner)
    unit.owner.add_card(dynamic, ZoneType.BATTLEFIELD)
    add_bonus(state, unit)
    unit.owner.health = 7
    assert dynamic in state.query_cards(EqQuery(IK_POWER, 7))
    assert state._continuous_graph_reuses > 0


def test_external_membership_edits_and_new_effects_invalidate_graph(board):
    state, unit = board
    effect = add_bonus(state, unit)
    unit.state.active_cont_effects.clear()
    assert unit.get_power(state) == 3
    assert "bonus" in unit.state.active_cont_effects
    add_bonus(state, unit, key="second")
    assert unit.get_power(state) == 4
    state._cont_effect_manager.get("second").detach(state)
    state._cont_effect_manager.pop("second")
    refresh(state)
    assert unit.get_power(state) == 3


def test_checkpoint_restores_reusable_graph_and_subsequent_invalidation(board):
    state, unit = board
    add_bonus(state, unit)
    refresh(state)
    checkpoint = RuntimeCheckpoint(state)
    unit.owner.move_card(unit, ZoneType.GRAVEYARD, state)
    state.synchronise_registers()
    checkpoint.rollback()
    unit.is_tapped = True
    assert unit.get_power(state) == 3
    unit.owner.move_card(unit, ZoneType.GRAVEYARD, state)
    assert unit.get_power(state) == 2
