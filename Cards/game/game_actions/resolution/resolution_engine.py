from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
from collections.abc import Iterable
from collections import deque

if TYPE_CHECKING:
    from .sba_resolver import SBAResolver
    from .event_bus import TriggerProcessor, EventBus, GameEvent
    from .operation_executor import OperationExecutor
    from ...game_state import State
    from ..data_structs.game_action import ScheduledResolution
    from ..data_structs.operation import Operation
    from .validator import ValidationResult, TargetValidator


@dataclass(frozen=True)
class ExecutionResult:
    """!
    @brief Result returned after trying to execute an action intent.
    """

    success: bool
    error: ValidationResult | None
    generated_events: tuple[GameEvent] | None


class ResolutionEngine:

    def __init__(
        self,
        operation_executor: OperationExecutor,
        event_bus: EventBus
    ) -> None:
    
        self._operation_executor = operation_executor
        self._event_bus = event_bus
        self._sba_resolver = SBAResolver()
        self._trigger_resolver = TriggerProcessor()
    
    def resolve(self, state: State, resolution: ScheduledResolution) -> ExecutionResult:

        error = self._validate(state, resolution)

        if error:
            return ExecutionResult(
                success=False,
                error=error,
                generated_events=None
            )
        
        operations = list(resolution.generate_operations(state))

        operations = self._apply_replacement_effects(state, operations)

        events = self._execute_operations(state, operations)

        self._emit_events(state, events)

        events = self._resolve_state(state, events)

        self._process_triggers(state, events)

        return ExecutionResult(
            success=True,
            error=None,
            generated_events=tuple(events)
        )
    
    def _validate(self, state: State, resolution: ScheduledResolution) -> ValidationResult:
        validator = TargetValidator()
        return validator.validate(state, resolution)

    def _apply_replacement_effects(self, state: State, to_replace: Iterable[Operation]) -> tuple[Operation]:
        queue = deque(to_replace)
        replaced: list[Operation] = []

        while len(to_replace) != 0:
            popped = queue.popleft()
            replacement = self._try_replace(state, popped)

            if not replacement:
                replaced.append(popped)
            else:
                queue.extend(replacement)
        
        return tuple(replaced)

    def _try_replace(self, state: State, to_replace) -> Iterable[Operation] | None:
        #TODO udelat cely replacement system.
        raise None
    
    def _execute_operations(self, state: State, operations: Iterable[Operation]) -> list[GameEvent]:
        res: list[GameEvent] = []
        for operation in operations:
            res.extend(
                self._operation_executor.execute(state, operation)
                )
        return res
    
    def _resolve_state(self, state: State, es: list[GameEvent]) -> list[GameEvent]:
        events = list(es)
        while True:
            sba_events = self._sba_resolver.resolve(state)
            if not sba_events:
                return events
            
            events.extend(sba_events)

            self._emit_events(state, sba_events)
    
    def _emit_events(self, state: State, events: Iterable[GameEvent]) -> None:
        for e in events: 
            self._event_bus.emit(e, state) 
    
    def _process_triggers(self, state: State, events: Iterable[GameEvent]) -> None:
        triggers = self._event_bus.collect_trigger_abilities(state, events)
        self._trigger_resolver.process(state, triggers)


    
    
