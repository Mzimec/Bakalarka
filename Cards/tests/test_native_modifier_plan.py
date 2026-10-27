"""Persistent characteristic plans preserve live, layered and owned behavior."""
from copy import copy

import pytest
from immutabledict import immutabledict

from benchmarks.core_reads import board
from game.enums import CardType, CounterType, Layer, ZoneType
from game.game_state import Card, CardDefinition
from game.game_state.stat import HasModifiableStats
from game.game_state.modifier import (
    AddIntModifier, AddSetModifier, ContinuousEffect, ContinuousEffectDefinition,
    ContinuousEffectState, PermanentDuration, DynamicTargetingStrategy,
)
from game.game_state.layers import SwitchPowerToughness
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.ai.simulation_world import copy_world
from game.stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_TYPES, STAT_KEYWORDS, STAT_ATTACH_MODS
from game.target.continuous_targets import CardIncarnationsSpec


def immutable_effect(state, card, modifiers, key="immutable"):
    definition = ContinuousEffectDefinition(
        PermanentDuration(), card, state.time_stamp,
        DynamicTargetingStrategy(CardIncarnationsSpec(((card, card.zone_revision),))),
        immutabledict(modifiers),
    )
    effect = ContinuousEffect(key, definition, ContinuousEffectState(set()))
    state.add_continuous_effect(effect)
    return effect


def test_unrelated_characteristics_and_repeated_reads_do_not_rediscover_sources(monkeypatch):
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    monkeypatch.setattr(type(card.modifier_sources[0]), "get_modifiers",
                        lambda *args: pytest.fail("Source rediscovered"))
    for _ in range(10):
        assert card.get_power(state) == 5
        assert card.get_types(state) == {CardType.CREATURE}


def test_counters_base_replacement_rollback_and_new_write():
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    checkpoint = RuntimeCheckpoint(state)
    card.state.counters[CounterType.PLUS_ONE] = 4
    assert card.get_power(state) == 9
    card.set_base_stat(STAT_POWER, 10)
    assert card.get_power(state) == 17
    checkpoint.rollback()
    assert card.get_power(state) == 5
    card.state.counters[CounterType.PLUS_ONE] = 2
    assert card.get_power(state) == 7


def test_immutable_attachment_replacement_and_new_modifiers_on_attachment():
    state, card = board(0)
    aura = Card(CardDefinition("Aura", attach_mods=immutabledict({
        STAT_POWER: (AddIntModifier(4),),
        STAT_KEYWORDS: (AddSetModifier(frozenset({"flying"})),),
    })), card.owner)
    aura.owner.add_card(aura, ZoneType.BATTLEFIELD, state)
    aura.attach(card, state)
    assert card.get_power(state) == 6
    assert card.get_stat(STAT_KEYWORDS, state) == {"flying"}
    aura.set_base_stat(STAT_ATTACH_MODS, immutabledict({STAT_POWER: (AddIntModifier(7),)}))
    assert card.get_power(state) == 9
    aura.detach()
    assert card.get_power(state) == 2


def test_effect_layer_and_dependency_state_edits_invalidate_plan():
    state, card = board(0)
    effect = immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    effect.state.active_layers.clear()
    assert card.get_power(state) == 2
    effect.state.active_layers.add(Layer.ADD)
    assert card.get_power(state) == 5
    effect.state.active_layers = None
    assert card.get_power(state) == 5
    plan = card._native_modifier_plan
    effect.state.inferred_dependencies[Layer.ADD] = frozenset({"other"})
    assert "_native_modifier_plan" not in vars(card)
    assert card.get_power(state) == 5
    assert card._native_modifier_plan is not plan


def test_switch_and_custom_modifiers_keep_reference_semantics_in_clone():
    state, card = board(0)
    class Dynamic:
        layer = Layer.ADD
        behavior = AddIntModifier(1).behavior
        def modify(self, value):
            return value
    immutable_effect(state, card, {
        STAT_POWER: (SwitchPowerToughness(),),
        STAT_TOUGHNESS: (AddIntModifier(6), SwitchPowerToughness()),
        STAT_TYPES: (Dynamic(),),
    })
    assert card.get_power(state) == 9
    cloned, _ = copy_world(state, {})
    other = cloned.card_register.get_by_key(card.key)
    assert other.get_power(cloned) == 9
    assert other._native_modifier_plan is not card._native_modifier_plan
    # Unsupported entries retain the fallback marker across ownership copies.
    assert other._native_modifier_plan.by_stat[STAT_TYPES] is None


def test_shallow_projection_rebuilds_plan_for_its_new_stat_table():
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    old = card._native_modifier_plan
    projection = copy(card)
    projection.set_base_stat(STAT_POWER, 10)
    assert projection.get_power(state) == 13
    assert card.get_power(state) == 5
    assert card._native_modifier_plan is old


def test_mutable_effect_definitions_stay_live():
    state, card = board(1)
    assert card.get_power(state) == 3
    state.get_cont_effect("bonus-0").modifiers[STAT_POWER].append(AddIntModifier(8))
    assert card.get_power(state) == 11


def test_instance_source_override_invalidates_existing_plan(monkeypatch):
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    monkeypatch.setattr(card.modifier_sources[0], "get_modifiers", lambda stat, state: [])
    assert card.get_power(state) == 2


def test_shallow_projection_with_shared_original_inputs_never_keeps_stale_plan():
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    assert card.get_power(state) == 5
    projection = copy(card)
    assert projection.get_power(state) == 5
    card.state.counters[CounterType.PLUS_ONE] = 4
    assert projection.get_power(state) == card.get_power(state) == 9


def test_unrelated_dynamic_modifier_is_not_evaluated_to_prepare_native_plan():
    state, card = board(0)
    class Dynamic:
        fail = False
        behavior = AddIntModifier(1).behavior
        @property
        def layer(self):
            if self.fail:
                pytest.fail("Unrelated dynamic modifier inspected")
            return Layer.ADD
        def modify(self, value):
            return value
    dynamic = Dynamic()
    immutable_effect(state, card, {STAT_POWER: (dynamic,)})
    dynamic.fail = True
    assert card.get_stat(STAT_TYPES, state) == {CardType.CREATURE}


def test_mutable_stat_tables_keep_live_replacement_semantics():
    state, card = board(0)
    immutable_effect(state, card, {STAT_POWER: (AddIntModifier(3),)})
    card._stats = dict(card.stats)
    assert card.get_stat(STAT_POWER, state) == 5
    from dataclasses import replace
    card.stats[STAT_POWER] = replace(card.stats[STAT_POWER], base_value=10)
    assert card.get_stat(STAT_POWER, state) == 13


def test_empty_native_sources_are_not_rescanned_per_characteristic(monkeypatch):
    state, card = board(0)
    assert card.get_power(state) == 2
    from game.game_state import native_modifier_plan
    monkeypatch.setattr(native_modifier_plan, "only_empty_builtin_sources",
                        lambda *args: pytest.fail("Empty sources rescanned"))
    for _ in range(10):
        assert card.get_power(state) == 2
        assert card.get_types(state) == {CardType.CREATURE}


def test_empty_source_flag_tracks_writes_rollback_and_new_write():
    state, card = board(0)
    assert card.get_power(state) == 2
    checkpoint = RuntimeCheckpoint(state)
    card.state.counters[CounterType.PLUS_ONE] = 3
    assert card.get_power(state) == 5
    checkpoint.rollback()
    assert card.get_power(state) == 2
    card.state.counters[CounterType.PLUS_ONE] = 7
    assert card.get_power(state) == 9
    card.state.counters.clear()
    assert card.get_power(state) == 2


def test_external_sources_with_untracked_callbacks_remain_live():
    state, card = board(0)
    from helper.change_tracking import TrackedDict
    counters = TrackedDict()
    card.modifier_sources[1].counters = counters
    assert card.get_power(state) == 2
    counters[CounterType.PLUS_ONE] = 4
    assert card.get_stat(STAT_POWER, state) == 6


def test_shallow_projection_cannot_inherit_an_empty_flag_for_original_owner():
    state, card = board(0)
    assert card.get_power(state) == 2
    projection = copy(card)
    card.state.counters[CounterType.PLUS_ONE] = 4
    assert projection.get_power(state) == card.get_power(state) == 6


def test_live_eligibility_reads_backing_contents_even_when_empty_flag_is_cached():
    from game.game_state.native_modifier_plan import live_sources_empty, sources_empty
    state, card = board(0)
    assert sources_empty(card) and live_sources_empty(card)
    # Eligibility has a stronger legacy contract than characteristic reads:
    # even a direct backing edit must immediately reject the empty fast path.
    card.state.counters.data[CounterType.PLUS_ONE] = 1
    assert not live_sources_empty(card)
    card.state.counters.data.clear()
    card.state.active_cont_effects._values.add("raw")
    assert not live_sources_empty(card)
    card.state.active_cont_effects._values.clear()
    card.state.attached.data["raw"] = card
    assert not live_sources_empty(card)
    card.state.attached.data.clear()
    assert live_sources_empty(card)
