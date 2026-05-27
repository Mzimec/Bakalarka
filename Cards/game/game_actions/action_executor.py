from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from .event_bus import GameEvent, TriggerResolver
if TYPE_CHECKING:
    from ..game_state import State, CreatureCard
    from ..operations import OperationExecutor, Operation
    from .game_action import ActionIntent
    from .event_bus import EventBus


@dataclass(frozen=True)
class ValidationResult:
    message: str

@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    error: ValidationResult | None
    generated_events: list[GameEvent] | None


class ActionExecutor:
    def __init__(self, operation_executor: OperationExecutor, event_bus: EventBus) -> None:
        self.operation_executor = operation_executor
        self.event_bus = event_bus
        self.sba_resolver = SBAResolver()
        self.trigger_resolver = TriggerResolver()
    
    def execute(self, state: State, action: ActionIntent) -> ExecutionResult:
        validation_error = self._validate(state, action)

        if validation_error is not None:
            return ExecutionResult(success=False, error=validation_error)

        operations = action.generate_operations(state)

        events = self._execute_operations(state, operations)

        sba_events = self.sba_resolver.resolve(state)
        events.extend(sba_events)

        self._emit_events(state, events)

        self._resolve_triggers(state, events)

        return ExecutionResult(success=True, generated_events=events)


    def _execute_operations(self, state: State, operations: list[Operation]) -> list[GameEvent]:
        res: list[GameEvent] = []
        for o in operations:
            res.extend(self.operation_executor.execute(state, o))
        return res
    
    def _validate(self, state: State, action: ActionIntent) -> ValidationResult | None:
        return None
    
    def _apply_replacement_effects(self, state: State, operations: list[Operation]) -> list[Operation]:
        # some logic
        return operations
    
    def _emit_events(self, state: State, events: list[GameEvent]) -> None:
        for e in events: self.event_bus.emit(e, state)

    
    def _destroy_creature(self, state: State, creature: CreatureCard) -> None:
        owner = creature.owner
        if creature in owner.battlefield.pemanents:
            owner.battlefield.pemanents.remove(creature)

        owner.graveyard.append(creature)

        self.event_bus.emit(
            GameEvent(
                key="creature_died",
                source=creature
            ),
            state
        )

    def _resolve_triggers(self, state: State, events: list[GameEvent]) -> None:
        triggers = self.event_bus.collect_triggered_abilities(state, events)
        self.trigger_resolver.resolve(state, triggers)
    

