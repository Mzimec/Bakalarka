"""The direct leaf reader must agree with normal action-graph compilation."""
from dataclasses import replace
from itertools import islice

import pytest

from game.enums import ManaType
from game.game_actions.data_structs.ability import SubAbilityComposer, SubAbilityDefinition
from game.game_actions.data_structs.action_node import (
    EffectActionNode, ImmutableEffectToSlotMap, OrActionNode,
)
from game.mana.source_discovery import single_sequence
from game.rules.lands import mana_ability


def compiled_sequence(definitions):
    part = SubAbilityComposer(definitions).compile(None, None)
    if part is None or part.mana_cost or part.action_node is None:
        return None
    options = list(islice(part.action_node.generate_options(), 2))
    if len(options) != 1 or options[0].mana_req:
        return None
    effects = part.normalize_effect_map(options[0].effects)
    if effects.get_used_slots():
        return None
    return tuple(binding.effect for binding in effects.sequence)


@pytest.mark.parametrize("color", list(ManaType))
def test_intrinsic_mana_sequences_skip_compilation(color, monkeypatch):
    definition = mana_ability(color)
    for subdefs in (definition.cost_subdefs, definition.action_subdefs):
        expected = compiled_sequence(subdefs)
        with monkeypatch.context() as patch:
            patch.setattr(SubAbilityComposer, "compile", lambda *_: pytest.fail("Fixed leaf was compiled"))
            assert single_sequence(subdefs, None, None) == expected


def test_fixed_leaf_keeps_effect_order_and_ignores_unused_effects():
    effects = [next(iter(mana_ability(color).action_subdefs[0].effects))
               for color in (ManaType.RED, ManaType.BLUE, ManaType.WHITE)]
    definition = SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({effects[1].key: frozenset(), effects[0].key: frozenset()})),
        effects=frozenset(effects),
    )
    assert single_sequence((definition,), None, None) == compiled_sequence((definition,)) == (effects[1], effects[0])


def test_custom_node_still_runs_its_compilation_and_generation():
    class EmptyNode(EffectActionNode):
        def create_with_sufix(self, suffix):
            return self
        def generate_options(self):
            return iter(())
    definition = mana_ability(ManaType.RED).action_subdefs[0]
    custom = replace(definition, action_node=EmptyNode(definition.action_node.effect_map))
    assert single_sequence((custom,), None, None) is None


@pytest.mark.parametrize("variant", ["cost", "alternative", "repeated"])
def test_nontrivial_sequences_agree_with_compilation(variant):
    definition = mana_ability(ManaType.RED).action_subdefs[0]
    if variant == "cost":
        definitions = (replace(definition, mana_cost="{1}"),)
    elif variant == "alternative":
        other = mana_ability(ManaType.BLUE).action_subdefs[0]
        definitions = (replace(definition, action_node=OrActionNode((definition.action_node, other.action_node)),
                               effects=definition.effects | other.effects),)
    else:
        definitions = (definition, definition)
    assert single_sequence(definitions, None, None) == compiled_sequence(definitions)
