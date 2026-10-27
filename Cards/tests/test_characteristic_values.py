"""Effective-value reuse agrees with the full evaluator across live changes."""
from copy import copy
from dataclasses import replace

import pytest

from benchmarks.core_reads import board
from game.enums import CardType, CounterType, Layer, ZoneType, TurnPhase
from game.game_state import Card, CardDefinition
from game.game_state.stat import HasModifiableStats
from game.game_state.modifier import AddIntModifier, SetModifier, TimeStampedModifier
from game.game_state.layers import SwitchPowerToughness
from game.game_state.registers.card_register import IK_POWER, IK_TYPE
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.ai.simulation_world import copy_world
from game.stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_TYPES, STAT_CONTROLLER
from helper.query_system.query import EqQuery


def check(state, card, *keys):
    for key in keys or (STAT_POWER, STAT_TOUGHNESS, STAT_TYPES, STAT_CONTROLLER):
        assert card.get_stat(key, state) == HasModifiableStats.get_stat(card, key, state)


def test_repeated_value_and_index_projection_share_evaluation(monkeypatch):
    state, card = board(3)
    assert card.get_power(state) == 5
    calls = []
    original = HasModifiableStats.get_stat

    def observed(self, stat, state):
        calls.append(stat)
        return original(self, stat, state)

    monkeypatch.setattr(HasModifiableStats, "get_stat", observed)
    assert state.card_register.partial_index_values(card, {IK_POWER})[IK_POWER] == frozenset({5})
    assert card.get_power(state) == 5
    assert not calls


def test_live_modifier_map_edits_without_notification():
    state, card = board(3)
    assert card.get_power(state) == 5
    effect = state.get_cont_effect("bonus-0")
    effect.modifiers[STAT_POWER] = [AddIntModifier(8)]
    assert card.get_stat(STAT_POWER, state) == 12
    check(state, card)
    effect.modifiers[STAT_POWER].append(AddIntModifier(2))
    assert card.get_stat(STAT_POWER, state) == 14
    effect.modifiers.clear()
    assert card.get_stat(STAT_POWER, state) == 4
    check(state, card)


def test_counter_registry_and_amount_changes(monkeypatch):
    from game.game_state import modifier
    state, card = board(1)
    for amount in (1, 5, 0, -1):
        card.state.counters[CounterType.PLUS_ONE] = amount
        check(state, card)
    class DynamicCounter:
        def get_modifiers(self, amount):
            return {STAT_POWER: [AddIntModifier(state.players[0].health)]}
    monkeypatch.setitem(modifier.TYPE_TO_COUNTER, CounterType.PLUS_ONE, DynamicCounter())
    check(state, card)
    state.players[0].health -= 3
    check(state, card)


def test_attachment_grants_and_detachment():
    state, card = board(1)
    grants = [AddIntModifier(4)]
    aura = Card(CardDefinition("Aura", attach_mods={STAT_POWER: grants}), card.owner)
    card.owner.add_card(aura, ZoneType.BATTLEFIELD, state)
    aura.attach(card, state)
    assert card.get_power(state) == 7
    grants[0] = AddIntModifier(9)
    check(state, card)
    assert card.get_stat(STAT_POWER, state) == 12
    aura.detach()
    assert card.get_power(state) == 3


def test_dynamic_native_source_instance_override(monkeypatch):
    state, card = board(1)
    assert card.get_power(state) == 3
    source = card.modifier_sources[0]
    monkeypatch.setattr(source, "get_modifiers", lambda key, s:
                        [TimeStampedModifier(AddIntModifier(s.players[0].health), s.time_stamp)]
                        if key == STAT_POWER else [])
    check(state, card)
    state.players[0].health -= 4
    check(state, card)


def test_custom_pipeline_override(monkeypatch):
    state, card = board(0)
    monkeypatch.setattr(card, "_get_modifier_pipeline", lambda key, s: (AddIntModifier(7),))
    assert card.get_power(state) == 9


@pytest.mark.parametrize("guard", ("_refreshing_effects", "_layer_ceiling"))
def test_intermediate_layer_reads_never_use_final_cache(guard, monkeypatch):
    state, card = board(3)
    assert card.get_power(state) == 5
    monkeypatch.setattr(state, guard, True if guard == "_refreshing_effects" else Layer.SET)
    check(state, card)
    state.get_cont_effect("bonus-0").state.active_layers.clear()
    check(state, card)


def test_switch_uses_opposite_characteristic_live():
    state, card = board(1)
    assert card.get_power(state) == 3
    effect = state.get_cont_effect("bonus-0")
    effect.state.active_layers = None
    effect.modifiers[STAT_POWER] = (SwitchPowerToughness(),)
    effect.modifiers[STAT_TOUGHNESS] = (AddIntModifier(6), SwitchPowerToughness())
    check(state, card)
    assert card.get_stat(STAT_POWER, state) == 9


def test_types_keep_general_evaluation_and_index_value_type():
    state, card = board(1)
    effect = state.get_cont_effect("bonus-0")
    effect.state.active_layers = None
    effect.modifiers[STAT_TYPES] = (SetModifier(frozenset({CardType.LAND})),)
    check(state, card)
    value = card.get_types(state)
    assert value == {CardType.LAND}
    assert type(value) is type(HasModifiableStats.get_stat(card, STAT_TYPES, state))
    assert state.card_register.partial_index_values(card, {IK_TYPE})[IK_TYPE] == value
    effect.modifiers.pop(STAT_TYPES)
    check(state, card)


def test_rollback_and_new_write():
    state, card = board(1)
    assert card.get_power(state) == 3
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_POWER, 10)
    assert card.get_power(state) == 11
    checkpoint.rollback()
    assert card.get_power(state) == 3
    card.set_base_stat(STAT_POWER, 20)
    assert card.get_power(state) == 21
    assert card in state.query_cards(EqQuery(IK_POWER, 21))


def test_owned_clone_and_shallow_projection_cache_isolation():
    state, card = board(3)
    assert card.get_power(state) == 5
    cloned, _ = copy_world(state, {})
    other = cloned.card_register.get_by_key(card.key)
    other.set_base_stat(STAT_POWER, 10)
    assert other.get_power(cloned) == 13
    assert card.get_power(state) == 5
    assert other._characteristic_values is not card._characteristic_values
    projection = copy(card)
    original_entries = card._characteristic_values
    projection._stats = type(card.stats)({**card.stats, STAT_POWER: replace(card.stats[STAT_POWER], base_value=8)})
    assert projection.get_stat(STAT_POWER, state) == 11
    assert card._characteristic_values is original_entries
    assert card.get_power(state) == 5


def test_timestamp_order_and_dependencies_are_live():
    state, card = board(2)
    one, two = (state.get_cont_effect(f"bonus-{i}") for i in range(2))
    one.modifiers[STAT_POWER] = (SetModifier(7),)
    two.modifiers[STAT_POWER] = (SetModifier(9),)
    one.state.active_layers = two.state.active_layers = None
    one.state.timestamp_order, two.state.timestamp_order = 1, 2
    assert card.get_stat(STAT_POWER, state) == 9
    one.state.timestamp_order = 3
    assert card.get_stat(STAT_POWER, state) == 7
    two.state.inferred_dependencies[Layer.SET] = frozenset({one.key})
    assert card.get_stat(STAT_POWER, state) == 9
    check(state, card)


def test_timestamp_reuse_phase_change_and_rollback():
    state, _ = board(0)
    first = state.time_stamp
    assert state.time_stamp is first
    checkpoint = RuntimeCheckpoint(state)
    state.turn.number += 1
    state.turn.phase = TurnPhase.END_STEP
    changed = state.time_stamp
    assert changed != first and changed.turn_number == state.turn.number
    assert state.time_stamp is changed
    checkpoint.rollback()
    assert state.time_stamp == first
    assert changed != state.time_stamp


def test_one_shot_modifier_iterable_is_not_consumed_by_cache_proof():
    state, card = board(1)
    effect = state.get_cont_effect("bonus-0")
    effect.modifiers[STAT_POWER] = iter((AddIntModifier(7),))
    assert card.get_stat(STAT_POWER, state) == 9


def test_none_modifier_list_has_no_contribution():
    state, card = board(1)
    state.get_cont_effect("bonus-0").modifiers[STAT_POWER] = None
    assert card.get_stat(STAT_POWER, state) == 2


def test_context_binding_on_native_modifier_uses_general_reader():
    state, card = board(1)
    effect = state.get_cont_effect("bonus-0")
    value = AddIntModifier(1)
    object.__setattr__(value, "for_context", lambda source, s: AddIntModifier(s.players[0].health))
    effect.modifiers[STAT_POWER] = (value,)
    assert card.get_stat(STAT_POWER, state) == 2 + state.players[0].health
    state.players[0].health -= 3
    assert card.get_stat(STAT_POWER, state) == 2 + state.players[0].health
