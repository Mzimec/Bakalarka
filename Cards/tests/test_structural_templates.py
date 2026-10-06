from dataclasses import replace
from types import SimpleNamespace

import pytest
from immutabledict import immutabledict

from game.enums import ManaType
from game.game_actions.data_structs.ability import SubAbilityDefinition, SubAbilityComposer, RuntimeSubAbility
from game.game_actions.data_structs.action_node import (
    AndActionNode, OrActionNode, EffectActionNode, ManaActionNode, ImmutableEffectToSlotMap,
)
from game.game_actions.mana_effects import AddManaEffect
from game.game_actions.generation import structural_templates as templates
from game.mana.mana_value import ImmutableManaRequirement
from game.target.target_resolver import TargetSlot


@pytest.fixture(autouse=True)
def reset_templates():
    templates._GRAPHS.clear()
    templates._OPTIONS.clear()
    yield
    templates._GRAPHS.clear()
    templates._OPTIONS.clear()


def part(key="add", slot=False):
    effect = AddManaEffect(key, ManaType.BLUE, 1)
    slots = frozenset({TargetSlot("target", object(), frozenset())}) if slot else frozenset()
    return SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({key: frozenset(s.key for s in slots)})),
        effects=frozenset({effect}), slots=slots)


def compile_parts(*parts):
    return SubAbilityComposer(parts).compile(None, None)


def test_graph_and_effect_structure_reused_but_bindings_not_shared_mutably():
    definition = part(slot=True)
    first, second = compile_parts(definition), compile_parts(definition)
    assert first is not second and first.action_node is second.action_node
    assert first.slots is second.slots and first.effects is second.effects
    mapping = next(first.action_node.generate_options()).effects
    effects, slots = templates.prepared_effects(first, mapping)
    slots.clear()
    again, fresh_slots = templates.prepared_effects(second, mapping)
    assert effects is again and len(fresh_slots) == 1
    effect, = definition.effects
    effect.amount = 7
    assert again.sequence[0].effect.amount == 7


def test_effect_key_change_rebuilds_maps_and_preserves_missing_key_error():
    definition = part()
    first = compile_parts(definition)
    effect, = definition.effects
    effect._key = "renamed"
    second = compile_parts(definition)
    assert first.effects is not second.effects and "renamed" in second.effects
    with pytest.raises(KeyError):
        templates.prepared_effects(second, next(second.action_node.generate_options()).effects)


def test_repetitions_and_definition_order_retain_suffixes_and_effect_order():
    a, b = part("a", slot=True), part("b", slot=True)
    first = compile_parts(a, a, b)
    second = compile_parts(b, a, a)
    assert set(first.slots) == {"target_0", "target_1", "target_2"}
    assert list(next(first.action_node.generate_options()).effects) == ["a", "b"]
    assert list(next(second.action_node.generate_options()).effects) == ["b", "a"]
    with pytest.raises((AttributeError, TypeError)):
        first.action_node.children.append(first.action_node)


def test_mapping_order_is_not_lost_in_normalization_cache():
    first, second = part("a"), part("b")
    compiled = compile_parts(first, second)
    forward = ImmutableEffectToSlotMap({"a": frozenset(), "b": frozenset()})
    backward = ImmutableEffectToSlotMap({"b": frozenset(), "a": frozenset()})
    a, _ = templates.prepared_effects(compiled, forward)
    b, _ = templates.prepared_effects(compiled, backward)
    assert [x.effect.key for x in a.sequence] == ["a", "b"]
    assert [x.effect.key for x in b.sequence] == ["b", "a"]


def test_changed_children_and_custom_suffix_nodes_keep_live_compilation():
    definition = part()
    children = (definition.action_node,)
    mutable = replace(definition, action_node=AndActionNode(children))
    first = compile_parts(mutable)
    object.__setattr__(mutable.action_node, "children", children + (definition.action_node,))
    second = compile_parts(mutable)
    assert first.action_node is not second.action_node
    assert len(second.action_node.children) == 2
    class Custom(EffectActionNode):
        calls = 0
        def create_with_sufix(self, suffix):
            type(self).calls += 1
            return super().create_with_sufix(suffix)
    custom = replace(definition, action_node=Custom(definition.action_node.effect_map))
    compile_parts(custom)
    compile_parts(custom)
    assert Custom.calls == 2


def test_replaced_mana_node_with_same_requirement_does_not_reuse_old_instance():
    definition = SubAbilityDefinition(action_node=ManaActionNode(ImmutableManaRequirement()))
    first = compile_parts(definition)
    replacement = ManaActionNode(ImmutableManaRequirement())
    object.__setattr__(definition, "action_node", replacement)
    second = compile_parts(definition)
    assert second.action_node is replacement and second.action_node is not first.action_node


def test_custom_normalization_preserves_dispatch_and_defers_slot_hook():
    class Custom(RuntimeSubAbility):
        calls = 0
        def normalize_effect_map(self, mapping):
            type(self).calls += 1
            return super().normalize_effect_map(mapping)
        def get_used_slots_in_esmap(self, mapping):
            pytest.fail("Slot hook must remain deferred until payment succeeds")
    compiled = compile_parts(part())
    custom = Custom(compiled.action_node, effects=compiled.effects, slots=compiled.slots)
    mapping = next(custom.action_node.generate_options()).effects
    assert templates.prepared_effects(custom, mapping)[1] is None
    assert templates.prepared_effects(custom, mapping)[1] is None
    assert Custom.calls == 2


def test_mana_stat_is_resolved_again_even_when_graph_is_reused():
    # The composer accepts hashable stat references in definitions.
    class Cost:
        stat_type = "cost"
    definition = replace(part(), mana_cost=Cost())
    from game.mana.mana_value import ManaValue
    values = iter((ManaValue.parse("{1}"), ManaValue.parse("{3}")))
    ability = SimpleNamespace(get_stat=lambda *args: next(values))
    composer = SubAbilityComposer((definition,))
    first, second = composer.compile(ability, None), composer.compile(ability, None)
    assert first.action_node is second.action_node
    assert str(first.mana_cost) != str(second.mana_cost)


def test_caches_are_bounded(monkeypatch):
    monkeypatch.setattr(templates, "_GRAPH_LIMIT", 3)
    monkeypatch.setattr(templates, "_OPTION_LIMIT", 4)
    for i in range(10):
        compiled = compile_parts(part(str(i)))
        templates.prepared_effects(compiled, next(compiled.action_node.generate_options()).effects)
    assert len(templates._GRAPHS) == 3 and len(templates._OPTIONS) == 4
