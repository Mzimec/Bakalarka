#----------------------------------------------------------------
# ActionNode tests
#----------------------------------------------------------------

from game.abilities import EffectActionNode, AndActionNode, OrActionNode

def test_effect_action_node():
    node = EffectActionNode(
        effect_key="damage",
        slot_keys={"target"}
    )

    result = node.get_used_effects()

    assert result == [
        {"damage": {"target"}}
    ]


def test_and_action_node():
    node = AndActionNode([
        EffectActionNode("damage", {"a"}),
        EffectActionNode("heal", {"b"}),
    ])

    result = node.get_used_effects()

    assert result == [
        {
            "damage": {"a"},
            "heal": {"b"},
        }
    ]

def test_empty_and_node():
    node = AndActionNode([])

    result = node.get_used_effects()

    assert result == [{}]

def test_same_effect_merges_slots():
    node = AndActionNode([
        EffectActionNode("damage", {"a"}),
        EffectActionNode("damage", {"b"}),
    ])

    result = node.get_used_effects()

    assert result == [
        {
            "damage": {"a", "b"}
        }
    ]


def test_or_action_node():
    node = OrActionNode([
        EffectActionNode("damage", {"a"}),
        EffectActionNode("heal", {"b"}),
    ])

    result = node.get_used_effects()

    assert len(result) == 2

    assert {"damage": {"a"}} in result
    assert {"heal": {"b"}} in result

def test_empty_or_node():
    node = OrActionNode([])

    result = node.get_used_effects()

    assert result == []

def test_nested_and_or_action_node():
    node = AndActionNode([
        OrActionNode([
            EffectActionNode("damage", {"enemy"}),
            EffectActionNode("burn", {"enemy"}),
        ]),
        EffectActionNode("heal", {"ally"})
    ])

    result = node.get_used_effects()

    assert len(result) == 2

    assert {
        "damage": {"enemy"},
        "heal": {"ally"},
    } in result

    assert {
        "burn": {"enemy"},
        "heal": {"ally"},
    } in result

def test_multiple_target_combinations():
    source = DummyCard("SOURCE")

    a1 = DummyCard("A1")
    a2 = DummyCard("A2")

    b1 = DummyCard("B1")
    b2 = DummyCard("B2")

    node = AndActionNode([
        EffectActionNode("damage", {"enemy"}),
        EffectActionNode("heal", {"ally"}),
    ])

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "enemy": make_slot("enemy", [a1, a2]),
            "ally": make_slot("ally", [b1, b2]),
        },
        effects={
            "damage": DummyEffect("damage"),
            "heal": DummyEffect("heal"),
        }
    )

    actions = sub.generate_actions(source, DummyState())

    assert len(actions) == 4


#----------------------------------------------------------------
# Target resolution tests
#----------------------------------------------------------------

from dummy_classes import DummyCard
from helper_funcs import make_slot

def test_target_slot_generates_bindings():
    c1 = DummyCard("A")
    c2 = DummyCard("B")

    slot = make_slot("target", [c1, c2])

    bindings = slot.get_bindings(None, None)

    assert len(bindings) == 2

    assert bindings[0]["target"] == {c1: 1}
    assert bindings[1]["target"] == {c2: 1}


def test_missing_slot_key():
    node = EffectActionNode(
        "damage",
        {"missing"}
    )

    sub = SubAbilityDefinition(
        action_node=node,
        slots={},
        effects={
            "damage": DummyEffect("damage")
        }
    )

    try:
        sub.generate_actions(
            DummyCard("SOURCE"),
            DummyState()
        )
        assert False
    except KeyError:
        pass


#----------------------------------------------------------------
# Bindings tests
#----------------------------------------------------------------

from game.abilities import EffectBinding
from game.game_actions import AbilityOperationGenerator, ResolutionContext

def test_operation_generator_keeps_binding():
    target = DummyCard("A")

    binding = {
        "target": {target: 1}
    }

    generator = AbilityOperationGenerator(tuple(), binding)

    assert generator.binding["target"][target] == 1


#----------------------------------------------------------------
# Effect operation tests
#----------------------------------------------------------------

def test_effect_generates_and_executes_operation():
    source = DummyCard("SOURCE")
    target = DummyCard("TARGET")

    effect = DummyEffect("damage")

    generator = AbilityOperationGenerator(
        effects=(EffectBinding("damage", effect, frozenset({"target"})),),
        binding={"target": {target: 1}},
    )

    operations = generator.to_operations(DummyState(), ResolutionContext(source=source))

    assert len(operations) == 1
    assert operations[0].context.source == source
    assert operations[0].context.targets == {"target": {target: 1}}

    operations[0].execute(DummyState())

    assert len(effect.executed) == 1

def test_effect_scope_isolated():
    source = DummyCard("SOURCE")

    enemy = DummyCard("ENEMY")
    ally = DummyCard("ALLY")

    damage = DummyEffect("damage")

    generator = AbilityOperationGenerator(
        effects=(EffectBinding("damage", damage, frozenset({"enemy"})),),
        binding={
            "enemy": {enemy: 1},
            "ally": {ally: 1},
        },
    )

    generator.to_operations(DummyState(), ResolutionContext(source=source))

    generated_targets = damage.generated[0]["context"].targets

    assert "enemy" in generated_targets
    assert "ally" not in generated_targets




#----------------------------------------------------------------
# Subability tests
#----------------------------------------------------------------

from dummy_classes import DummyEffect, DummyState
from game.abilities import SubAbilityDefinition

def test_sub_ability_generates_action_prototypes():
    source = DummyCard("SOURCE")

    target_a = DummyCard("A")
    target_b = DummyCard("B")

    damage_effect = DummyEffect("damage")

    node = EffectActionNode(
        effect_key="damage",
        slot_keys={"target"}
    )

    slot = make_slot("target", [target_a, target_b])

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "target": slot
        },
        effects={
            "damage": damage_effect
        }
    )

    actions = sub.generate_actions(source, DummyState())

    assert len(actions) == 2


def test_distinct_targets_validation():
    source = DummyCard("SOURCE")

    shared_target = DummyCard("X")

    slot_a = make_slot("a", [shared_target], frozenset({"b"}))
    slot_b = make_slot("b", [shared_target])

    node = AndActionNode([
        EffectActionNode("damage", {"a"}),
        EffectActionNode("heal", {"b"}),
    ])

    damage = DummyEffect("damage")
    heal = DummyEffect("heal")

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "a": slot_a,
            "b": slot_b,
        },
        effects={
            "damage": damage,
            "heal": heal,
        }
    )

    actions = sub.generate_actions(source, DummyState())

    # invalid because same target used in distinct slots
    assert len(actions) == 0


def test_missing_effect_key():
    node = EffectActionNode(
        "damage",
        {"target"}
    )

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "target": make_slot(
                "target",
                [DummyCard("A")]
            )
        },
        effects={}
    )

    try:
        sub.generate_actions(
            DummyCard("SOURCE"),
            DummyState()
        )
        assert False
    except KeyError:
        pass


#----------------------------------------------------------------
# Execution tests
#----------------------------------------------------------------

def test_game_action_prototype_execute():
    source = DummyCard("SOURCE")
    target = DummyCard("TARGET")

    damage = DummyEffect("damage")

    node = EffectActionNode(
        effect_key="damage",
        slot_keys={"target"}
    )

    slot = make_slot("target", [target])

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "target": slot
        },
        effects={
            "damage": damage
        }
    )

    actions = sub.generate_actions(source, DummyState())

    assert len(actions) == 1
    generator = actions[0]

    operations = generator.to_operations(DummyState(), ResolutionContext(source=source))
    assert len(operations) == 1
    operations[0].execute(DummyState())

    assert len(damage.executed) == 1

    executed_targets = damage.executed[0]["context"].targets

    assert "target" in executed_targets


#----------------------------------------------------------------
# Multi effect tests
#----------------------------------------------------------------

def test_multiple_effects_execute():
    source = DummyCard("SOURCE")

    t1 = DummyCard("A")
    t2 = DummyCard("B")

    damage = DummyEffect("damage")
    heal = DummyEffect("heal")

    node = AndActionNode([
        EffectActionNode("damage", {"enemy"}),
        EffectActionNode("heal", {"ally"}),
    ])

    enemy_slot = make_slot("enemy", [t1])
    ally_slot = make_slot("ally", [t2])

    sub = SubAbilityDefinition(
        action_node=node,
        slots={
            "enemy": enemy_slot,
            "ally": ally_slot,
        },
        effects={
            "damage": damage,
            "heal": heal,
        }
    )

    actions = sub.generate_actions(source, DummyState())

    assert len(actions) == 1

    generator = actions[0]

    operations = generator.to_operations(DummyState(), ResolutionContext(source=source))
    for operation in operations:
        operation.execute(DummyState())

    assert len(damage.executed) == 1
    assert len(heal.executed) == 1
