"""Action graph, composition, target binding and lazy execution regressions."""
import pytest
from dummy_classes import DummyEffect, DummyCard, DummyState
from helper_funcs import make_slot
from game.game_actions.data_structs.action_node import (
    EffectActionNode, AndActionNode, OrActionNode, ImmutableEffectToSlotMap)
from game.game_actions.data_structs.ability import (
    SubAbilityDefinition, SubAbilityComposer, AbilityDefinition, EffectBinding, EffectSequence)
from game.game_actions.data_structs.game_action import AbilityExecutionPlan, ResolutionContext
from game.game_actions.generation.command_action_builder import ability_actions
from game.target.target_resolver import TargetBinding, TargetOption, RepetitionTargetSlotWrapper
from game.game_state import Card, CardDefinition, Player, State
from game.enums import CardType, ZoneType, TurnPhase
from game.game_loop.minimal_game import ScriptedController


def node(key, *slots):
    return EffectActionNode(ImmutableEffectToSlotMap({key: frozenset(slots)}))


def maps(graph):
    return [option.effects for option in graph.generate_options()]


def test_effect_action_node():
    assert maps(node("damage", "target")) == [{"damage": frozenset({"target"})}]


def test_and_action_node():
    assert maps(AndActionNode((node("damage", "a"), node("heal", "b")))) == [{"damage": {"a"}, "heal": {"b"}}]


def test_empty_and_node():
    assert maps(AndActionNode(())) == [{}]


def test_same_effect_merges_slots():
    assert maps(AndActionNode((node("damage", "a"), node("damage", "b")))) == [{"damage": {"a", "b"}}]


def test_or_action_node():
    assert maps(OrActionNode((node("damage", "a"), node("heal", "b")))) == [{"damage": {"a"}}, {"heal": {"b"}}]


def test_empty_or_node():
    assert maps(OrActionNode(())) == []


def test_nested_and_or_action_node():
    graph = AndActionNode((OrActionNode((node("damage", "enemy"), node("burn", "enemy"))), node("heal", "ally")))
    assert maps(graph) == [{"damage": {"enemy"}, "heal": {"ally"}}, {"burn": {"enemy"}, "heal": {"ally"}}]


def test_repeated_modes_are_deduplicated_and_source_is_unchanged():
    graph = OrActionNode((node("damage", "a"), node("damage", "a")))
    assert len(maps(graph)) == 1
    assert maps(graph.create_with_sufix("_3")) == [{"damage": {"a_3"}}]
    assert maps(graph) == [{"damage": {"a"}}]


def setup_ability(graph, effects, slots):
    player = Player([], ScriptedController())
    state = State([player])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    definition = AbilityDefinition(action_subdefs=(SubAbilityDefinition(action_node=graph,
        effects=frozenset(effects), slots=frozenset(slots)),))
    source = Card(CardDefinition("Source", types=frozenset({CardType.ARTIFACT}), abilities=frozenset({definition})), player)
    player.add_card(source, ZoneType.BATTLEFIELD)
    return state, definition.to_ability(source, player)


@pytest.mark.parametrize("a_count,b_count", [(1, 1), (1, 3), (2, 2), (3, 4)])
def test_multiple_target_combinations(a_count, b_count):
    a = [DummyCard(f"a{i}") for i in range(a_count)]
    b = [DummyCard(f"b{i}") for i in range(b_count)]
    damage, heal = DummyEffect("damage"), DummyEffect("heal")
    state, ability = setup_ability(AndActionNode((node("damage", "a"), node("heal", "b"))),
                                   [damage, heal], [make_slot("a", a), make_slot("b", b)])
    actions = list(ability_actions(ability, state))
    assert len(actions) == a_count * b_count
    assert not damage.generated and not heal.generated


def test_target_slot_generates_bindings():
    targets = [DummyCard("a"), DummyCard("b")]
    slot = make_slot("target", targets)
    options = list(slot.target_resolver.generate_target_options(None, None, DummyState()))
    assert options == [{target: 1} for target in targets]


@pytest.mark.parametrize("missing", ["slot", "effect"])
def test_missing_graph_definitions_fail_explicitly(missing):
    effect = DummyEffect("damage")
    state, ability = setup_ability(node("damage", "target"), [] if missing == "effect" else [effect],
                                  [] if missing == "slot" else [make_slot("target", [DummyCard("a")])])
    with pytest.raises(KeyError):
        list(ability_actions(ability, state))


def test_operation_generator_keeps_binding():
    target = DummyCard("a")
    binding = TargetBinding({"target": {"target_0": TargetOption({target: 1})}}).to_immutable()
    plan = AbilityExecutionPlan(EffectSequence(()), binding)
    assert plan.binding.get_targets_in_slot("target_0", "target") == {target}


def test_effect_generates_and_executes_operation():
    effect, target = DummyEffect("damage"), DummyCard("target")
    state, ability = setup_ability(node("damage", "target"), [effect], [make_slot("target", [target])])
    action = next(ability_actions(ability, state))
    assert not effect.generated
    operations = list(action.get_intents()[1].generate_operations(state))
    assert len(operations) == 1
    assert operations[0].context.targets.get_targets_in_slot("target_0", "target") == {target}
    operations[0].execute(state)
    assert len(effect.executed) == 1


def test_effect_scope_isolated_and_multiple_effects_execute():
    damage, heal = DummyEffect("damage"), DummyEffect("heal")
    enemy, ally = DummyCard("enemy"), DummyCard("ally")
    state, ability = setup_ability(AndActionNode((node("damage", "enemy"), node("heal", "ally"))),
        [damage, heal], [make_slot("enemy", [enemy]), make_slot("ally", [ally])])
    action = next(ability_actions(ability, state))
    for operation in action.get_intents()[1].generate_operations(state):
        operation.execute(state)
    assert set(damage.executed[0]["context"].targets) == {"enemy"}
    assert set(heal.executed[0]["context"].targets) == {"ally"}


def test_distinct_targets_validation():
    target = DummyCard("shared")
    state, ability = setup_ability(AndActionNode((node("damage", "a"), node("heal", "b"))),
        [DummyEffect("damage"), DummyEffect("heal")],
        [make_slot("a", [target], frozenset({"b"})), make_slot("b", [target], frozenset({"a"}))])
    assert list(ability_actions(ability, state)) == []


def test_repeated_subability_gets_distinct_runtime_slots():
    effect = DummyEffect("damage")
    slot = make_slot("target", [DummyCard("a")])
    state, ability = setup_ability(node("damage", "target"), [effect], [slot])
    subdef = ability.definition.action_subdefs[0]
    compiled = SubAbilityComposer((subdef, subdef)).compile(ability, state)
    assert set(compiled.slots) == {"target_0", "target_1"}
