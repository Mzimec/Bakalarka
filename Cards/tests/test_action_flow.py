#----------------------------------------------------------------
# Test helpers
#----------------------------------------------------------------

from dummy_classes import DummyCard, DummyEffect, DummyState

from game.abilities import EffectActionNode, SubAbilityDefinition
from game.game_actions import AbilityAction, PassPriorityAction
from game.game_actions.resolution.action_executor import ActionExecutor
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.game_stack import GameStack
from game.operations import OperationExecutor


class FlowPlayer:
    def __init__(self, name="PLAYER"):
        self.name = name


class FlowState(DummyState):
    def __init__(self):
        self.stack = GameStack()

    def get_triggered_abilities(self, event=None):
        return []


def make_no_target_sub_ability(effect):
    return SubAbilityDefinition(
        action_node=EffectActionNode(effect.key, set()),
        slots={},
        effects={
            effect.key: effect,
        }
    )


def make_action(source, effect, uses_stack):
    return AbilityAction(
        action_key="test_action",
        source=source,
        cost_generator=make_no_target_sub_ability(DummyEffect("cost")).generate_actions(
            source,
            FlowState(),
        )[0],
        action_generator=make_no_target_sub_ability(effect).generate_actions(
            source,
            FlowState(),
        )[0],
        uses_stack=uses_stack,
    )


def make_processor():
    event_bus = EventBus()
    executor = ActionExecutor(OperationExecutor(), event_bus)
    return ActionProcessor(executor), executor, event_bus


#----------------------------------------------------------------
# AbilityAction context tests
#----------------------------------------------------------------

def test_ability_action_intents_carry_shared_context():
    owner = FlowPlayer()
    source = DummyCard("SOURCE", owner=owner)
    effect = DummyEffect("damage")
    action = make_action(source, effect, uses_stack=True)

    cost_intent, action_intent = action.get_intents()

    assert cost_intent.context.controller == owner
    assert cost_intent.context.source == source
    assert cost_intent.context.action_key == "test_action"
    assert not cost_intent.context.uses_stack

    assert action_intent.context.controller == owner
    assert action_intent.context.source == source
    assert action_intent.context.action_key == "test_action"
    assert action_intent.context.uses_stack


#----------------------------------------------------------------
# ActionProcessor routing tests
#----------------------------------------------------------------

def test_processor_executes_non_stack_intents_immediately():
    source = DummyCard("SOURCE", owner=FlowPlayer())
    effect = DummyEffect("damage")
    state = FlowState()
    processor, executor, event_bus = make_processor()

    results = processor.process(
        state,
        make_action(source, effect, uses_stack=False),
    )

    assert len(results) == 2
    assert all(result.success for result in results)
    assert state.stack.is_empty()
    assert len(effect.generated) == 1


def test_processor_executes_cost_and_routes_stack_intent():
    source = DummyCard("SOURCE", owner=FlowPlayer())
    effect = DummyEffect("damage")
    state = FlowState()
    processor, executor, event_bus = make_processor()

    results = processor.process(
        state,
        make_action(source, effect, uses_stack=True),
    )

    assert len(results) == 1
    assert results[0].success
    assert len(state.stack.items) == 1
    assert len(effect.generated) == 0

    intent = state.stack.pop()
    executor.execute(state, intent)

    assert len(effect.generated) == 1


#----------------------------------------------------------------
# Operation/Event tests
#----------------------------------------------------------------

def test_action_executor_emits_generated_events():
    source = DummyCard("SOURCE", owner=FlowPlayer())
    state = FlowState()
    processor, executor, event_bus = make_processor()

    action = PassPriorityAction(source.owner)
    intent = action.get_intents()[0]

    result = executor.execute(state, intent)

    assert result.success
    assert len(result.generated_events) == 1
    assert result.generated_events[0].key == "priority_passed"
    assert result.generated_events[0].controller == source.owner
    assert event_bus.emitted_events == result.generated_events
