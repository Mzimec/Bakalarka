"""Runtime delayed, reflexive, state and mana-trigger behaviour."""

from dataclasses import replace

import pytest

from game.enums import ManaType
from game.game_actions.data_structs.action_node import (
    EffectActionNode,
    ImmutableEffectToSlotMap,
)
from game.game_actions.data_structs.ability import SubAbilityDefinition
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.game_action import (
    FixedExecutionPlan,
    ResolutionContext,
    ScheduledResolution,
)
from game.game_actions.data_structs.operation import Operation
from game.game_actions.mana_activation import ManaActivationEventOperation
from game.game_actions.mana_effects import AddManaEffect, AddManaOperation, SpendManaOperation
from game.game_actions.resolution.event_bus import GameEvent
from game.game_actions.triggers.runtime_triggers import (
    ReflexiveTriggerOperation,
    RegisterTriggerOperation,
    register_trigger,
)
from game.game_actions.triggers.trigger_condition import EventKeyCondition

from tests.test_triggered_abilities import (
    add_card,
    context,
    engine,
    make_state,
    recording_trigger,
    run_operations,
    trigger_def,
)


class EmitEventOperation(Operation):
    """! @brief Test operation that emits one named event. """

    def __init__(self, context, key):
        super().__init__(context)
        self.key = key

    def execute(self, state):
        return [
            GameEvent(
                self.key,
                self.context.source,
                self.context.controller,
            )
        ]


class SetLifeOperation(Operation):
    """! @brief Test operation that changes one player's life without an event. """

    def __init__(self, context, player, amount):
        super().__init__(context)
        self.player = player
        self.amount = amount

    def execute(self, state):
        self.player.health = self.amount
        return []


class SetLifeEffect(Effect):
    """! @brief Test trigger effect that records and then clears its state condition. """

    def __init__(self, output, amount):
        super().__init__("set_life")
        self.output = output
        self.amount = amount

    def to_operations(self, state, context):
        self.output.append(context.trigger_event)
        yield SetLifeOperation(context, context.controller, self.amount)

    def get_info(self):
        return "Set life for the trigger controller."


def life_setting_trigger(output, amount):
    """!
    @brief Build a no-target trigger that records and changes controller life.

    @param output List receiving the trigger event before the life change.
    @param amount Life total to set during trigger resolution.
    @return Trigger definition using the normal action-generation pipeline.
    """
    effect = SetLifeEffect(output, amount)
    action = SubAbilityDefinition(
        action_node=EffectActionNode(
            ImmutableEffectToSlotMap({effect.key: frozenset()})
        ),
        effects=frozenset({effect}),
    )
    return trigger_def(action_subdefs=(action,))


def mana_trigger_definition():
    """! @brief Build a triggered mana ability that adds one red mana. """
    effect = AddManaEffect("add_bonus_red", ManaType.RED)
    action = SubAbilityDefinition(
        action_node=EffectActionNode(
            ImmutableEffectToSlotMap({effect.key: frozenset()})
        ),
        effects=frozenset({effect}),
    )
    return trigger_def(
        EventKeyCondition("ability_activated"),
        key="bonus-red",
        uses_stack=False,
        is_mana_ability=True,
        action_subdefs=(action,),
    )


def resolve_stack(state, resolver):
    """! @brief Resolve every item currently on the stack. """
    while not state.stack.is_empty():
        assert resolver.resolve(state, state.stack.pop()).success


@pytest.mark.parametrize("once,expected", [(True, 1), (False, 2)])
def test_delayed_runtime_trigger_uses_normal_stack_pipeline(once, expected):
    output = []
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()

    register_trigger(
        state,
        context(source),
        recording_trigger(output),
        once=once,
    )

    for _ in range(2):
        run_operations(
            state,
            [EmitEventOperation(context(source), "test_event")],
            resolver,
        )

    assert len(state.stack.items) == expected
    resolve_stack(state, resolver)
    assert len(output) == expected


def test_delayed_runtime_trigger_keeps_original_controller_after_control_change():
    output = []
    state = make_state()
    source = add_card(state, "source")
    original = state.active_player
    resolver, _ = engine()

    register_trigger(state, context(source), recording_trigger(output))
    source.set_controller(state.players[1])
    run_operations(state, [EmitEventOperation(context(source), "test_event")], resolver)

    assert state.stack.items[0].action_resolution.context.controller is original
    resolve_stack(state, resolver)
    assert len(output) == 1


@pytest.mark.parametrize("actual_key,expected", [("paid", 1), ("replaced", 0)])
def test_reflexive_trigger_depends_on_actual_completed_event(actual_key, expected):
    output = []
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()
    definition = recording_trigger(output, condition=EventKeyCondition("paid"))

    run_operations(
        state,
        [
            ReflexiveTriggerOperation(
                context(source),
                definition,
                GameEvent(actual_key, source, state.active_player),
            )
        ],
        resolver,
    )

    assert len(state.stack.items) == expected
    resolve_stack(state, resolver)
    assert len(output) == expected


def test_state_trigger_captures_state_even_without_operation_events():
    output = []
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()

    register_trigger(
        state,
        context(source),
        life_setting_trigger(output, 20),
        once=False,
        predicate=lambda state: state.active_player.health < 10,
    )

    run_operations(
        state,
        [SetLifeOperation(context(source), state.active_player, 5)],
        resolver,
    )

    assert len(state.stack.items) == 1
    resolver.settle(state)
    assert len(state.stack.items) == 1
    resolve_stack(state, resolver)
    assert len(output) == 1
    assert state.active_player.health == 20
    resolver.settle(state)
    assert state.stack.is_empty()


def test_state_trigger_rearms_when_stack_copy_is_countered():
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()

    register_trigger(
        state,
        context(source),
        life_setting_trigger([], 20),
        once=False,
        predicate=lambda state: state.active_player.health < 10,
    )

    run_operations(
        state,
        [SetLifeOperation(context(source), state.active_player, 5)],
        resolver,
    )

    assert len(state.stack.items) == 1
    state.stack.pop()
    resolver.settle(state)
    assert len(state.stack.items) == 1


def test_register_trigger_operation_rolls_back_with_failed_cost_transaction():
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()
    resolver._pending_triggers = []
    resolver._cost_transaction = None
    before = tuple(getattr(state, "runtime_triggers", ()))

    # The operation itself is ordinary and therefore participates in the same
    # state journal as any other cost operation when used by real cost plans.
    run_operations(
        state,
        [RegisterTriggerOperation(context(source), recording_trigger([]))],
        resolver,
    )

    assert len(getattr(state, "runtime_triggers", ())) == len(before) + 1


def test_triggered_mana_ability_resolves_before_next_cost_spends_pool():
    state = make_state()
    source = add_card(state, "source")
    resolver, _ = engine()
    definition = mana_trigger_definition()

    register_trigger(
        state,
        context(source),
        definition,
        once=True,
    )

    mana_context = replace(
        context(source),
        ability=definition,
        is_cost=True,
    )

    previous = resolver._executing_cost
    resolver._executing_cost = True

    try:
        events = resolver._execute_operations(
            state,
            [
                AddManaOperation(mana_context, ManaType.RED, 1),
                ManaActivationEventOperation(mana_context),
                SpendManaOperation(mana_context, {ManaType.RED: 2}),
            ],
        )
    finally:
        resolver._executing_cost = previous

    assert state.active_player.mana_pool.get(ManaType.RED, 0) == 0
    assert state.stack.is_empty()
    assert [
        trigger.key
        for event in events
        for trigger in event.triggered_abilities or ()
    ] == []
