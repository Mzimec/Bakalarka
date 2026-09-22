"""Exercise the current scheduled-resolution API through its real executor."""

from dataclasses import dataclass
from types import SimpleNamespace

from game.game_actions.data_structs.ability import EffectSequence
from game.game_actions.data_structs.game_action import (
    AbilityAction,
    ConcedeAction,
    FixedExecutionPlan,
    GameAction,
    ManaAbilityAction,
    PassPriorityAction,
    ResolutionContext,
    ScheduledResolution,
)
from game.game_actions.data_structs.operation import ConcedeOperation, PassPriorityOperation
from game.game_actions.game_stack import GameStack
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus, TriggerProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.target.target_resolver import TargetBinding, TargetOption


def make_pipeline():
    state = SimpleNamespace(stack=GameStack(), get_triggered_abilities=lambda event: [])
    bus = EventBus()
    engine = ResolutionEngine(OperationExecutor(), bus)
    return state, bus, engine, ActionProcessor(engine)


def test_ability_pays_cost_then_resolves_its_stack_entry():
    state, bus, engine, processor = make_pipeline()
    player = SimpleNamespace(health=10)
    source = SimpleNamespace(owner=player)
    context = ResolutionContext(controller=player, source=source)
    action = AbilityAction(
        action_key="delayed_concession",
        source=source,
        cost_generator=FixedExecutionPlan([PassPriorityOperation(context)]),
        action_generator=FixedExecutionPlan([ConcedeOperation(context)]),
    )

    results = processor.process(state, action)

    assert len(results) == 1 and results[0].success
    assert player.health == 10
    assert [event.key for event in bus.emitted_events] == ["priority_passed"]
    assert len(state.stack.items) == 1
    pending = state.stack.pop()
    assert pending.key == "delayed_concession"
    assert pending.runtime_id is None

    result = engine.execute(state, pending)

    assert result.success
    assert player.health == 0
    assert result.generated_events[0].key == "player_conceded"
    assert [event.key for event in bus.emitted_events] == ["priority_passed", "player_conceded"]


def test_player_actions_emit_events_and_concession_changes_health():
    state, bus, engine, processor = make_pipeline()
    player = SimpleNamespace(health=10)

    assert processor.process(state, PassPriorityAction(player))[0].success
    assert player.health == 10
    assert processor.process(state, ConcedeAction(player))[0].success

    assert player.health == 0
    assert [event.key for event in bus.emitted_events] == ["priority_passed", "player_conceded"]
    assert all(event.controller is player for event in bus.emitted_events)
    assert state.stack.is_empty()


@dataclass(frozen=True)
class ChosenAction(GameAction):
    resolutions: tuple[ScheduledResolution, ...]

    def get_intents(self):
        return self.resolutions


def test_invalid_cost_targets_prevent_effect_from_entering_stack():
    state, bus, engine, processor = make_pipeline()
    player = SimpleNamespace(health=10)
    cost_context = ResolutionContext(
        controller=player,
        # A previously chosen target slot no longer exists in the effect sequence.
        targets=TargetBinding({"removed_slot": {"_0": TargetOption({"target": 1})}}).to_immutable(),
        effects=EffectSequence(()),
    )
    action = ChosenAction((
        ScheduledResolution(FixedExecutionPlan([ConcedeOperation(cost_context)]), cost_context),
        ScheduledResolution(FixedExecutionPlan([]), ResolutionContext(uses_stack=True)),
    ))

    results = processor.process(state, action)

    assert len(results) == 1
    assert not results[0].success
    assert results[0].error.message == "Targets were no longer valid."
    assert player.health == 10
    assert state.stack.is_empty()
    assert bus.emitted_events == []


def test_mana_ability_defaults_to_immediate_resolution():
    state, bus, engine, processor = make_pipeline()
    player = SimpleNamespace(health=10)
    context = ResolutionContext(controller=player)
    action = ManaAbilityAction(
        action_key="mana",
        source=SimpleNamespace(owner=player),
        cost_generator=FixedExecutionPlan([]),
        action_generator=FixedExecutionPlan([PassPriorityOperation(context)]),
    )

    results = processor.process(state, action)

    assert len(results) == 2 and all(result.success for result in results)
    assert state.stack.is_empty()
    assert [event.key for event in bus.emitted_events] == ["priority_passed"]


def test_trigger_dispatch_starts_with_active_player_and_keeps_controller_pairs():
    dispatched = []

    from game.ai.decision_maker import ModularDecisionMaker, DecisionResult, TriggerOrderOption
    from tests.test_triggered_abilities import make_state, add_card, trigger_def
    from game.game_actions.resolution.event_bus import GameEvent

    class Controller(ModularDecisionMaker):
        def __init__(self, name):
            self.name = name

        def decide_trigger_order(self, request):
            dispatched.append((self.name, list(request.candidates)))
            return DecisionResult(TriggerOrderOption(request.candidates))

    state = make_state(3, 1, controllers=[Controller(name) for name in ("first", "active", "last")])
    for player in state.players:
        add_card(state, player.name.lower().replace(" ", "-"), [trigger_def()], player=player)
    triggers = state.get_triggered_abilities(GameEvent("test_event"))
    TriggerProcessor().process(state, triggers)

    assert [name for name, _ in dispatched] == ["active", "last", "first"]
    assert [group[0].controller for _, group in dispatched] == [state.players[i] for i in (1, 2, 0)]
    assert len(state.stack.items) == 3
