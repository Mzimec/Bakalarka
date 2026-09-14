"""Resolve plans through validation, replacements, operations and state-based actions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import dataclass, replace
from collections.abc import Iterable
from collections import deque

from .sba_resolver import SBAResolver
from .event_bus import TriggerProcessor
from .validator import TargetValidator

if TYPE_CHECKING:
    from .sba_resolver import SBAResolver
    from .event_bus import TriggerProcessor, EventBus, GameEvent
    from .operation_executor import OperationExecutor
    from ...game_state import State
    from ..data_structs.game_action import ScheduledResolution
    from ..data_structs.operation import Operation
    from .validator import ValidationResult


@dataclass(frozen=True)
class ExecutionResult:
    """!
    @brief Result returned after trying to resolve one scheduled action.

    @var success
        Whether the resolution completed successfully.
    @var error
        Validation failure when resolution was rejected, otherwise `None`.
    @var generated_events
        Events produced by successful execution, or `None` when resolution
        failed before execution.
    """

    success: bool
    error: ValidationResult | None
    generated_events: tuple[GameEvent] | None


class ResolutionEngine:
    """!
    @brief Resolve scheduled actions through validation, replacement and execution.

    Coordinates operation generation, replacement effects, event publication,
    state-based actions and triggered abilities. Cost resolutions additionally
    use a rollback boundary so partially paid costs cannot escape on failure.
    """

    def __init__(
        self,
        operation_executor: OperationExecutor,
        event_bus: EventBus,
        sba_rules=None,
    ) -> None:
        """!
        @brief Create a resolution engine and its state/trigger resolvers.

        @param operation_executor Executor used for atomic runtime operations.
        @param event_bus Event publisher and trigger collector.
        @param sba_rules Optional state-based action rules. Defaults to the
               standard lethal-creature, permanent-state and player-loss rules.
        """
        self._operation_executor = operation_executor
        self._event_bus = event_bus

        if sba_rules is None:
            from .sba_resolver import LethalCreaturesRule, PlayerLossRule
            from game.rules.permanents import PermanentStateRule

            sba_rules = [
                LethalCreaturesRule(),
                PermanentStateRule(),
                PlayerLossRule(),
            ]

        self._sba_resolver = SBAResolver(
            operation_executor,
            sba_rules,
            self._apply_replacement_effects,
        )
        self._trigger_resolver = TriggerProcessor(self)

        # Non-None while trigger processing is intentionally deferred by an
        # enclosing transaction or action.
        self._pending_triggers = None

        # Active rollback boundary for reversible cost payment.
        self._cost_transaction = None

        # Enables stricter per-operation validation while paying a cost.
        self._executing_cost = False

    def resolve(self, state, resolution):
        """! @brief Keep a state trigger pending throughout its own resolution. """
        scheduled = getattr(resolution, "action_resolution", resolution)
        registration = scheduled.context.trigger_registration
        if registration is not None:
            registration.resolving = True
        try:
            return self._resolve_registered(state, resolution)
        finally:
            if registration is not None:
                registration.resolving = False
                registration.pending = registration.queued = False
                if registration.predicate is not None and self._pending_triggers is None:
                    self.settle(state)

    def _resolve_registered(
        self,
        state: State,
        resolution: ScheduledResolution,
    ) -> ExecutionResult:
        """!
        @brief Resolve one scheduled intent.

        Cost intents resolved outside an existing action transaction receive
        their own rollback boundary. Non-cost resolutions, and costs already
        inside a transaction, proceed directly through `_resolve`.

        @param state Current game state.
        @param resolution Scheduled resolution or stack wrapper to execute.
        @return Execution result describing success, validation failure and events.
        """
        scheduled = getattr(
            resolution,
            "action_resolution",
            resolution,
        )

        if (
            not scheduled.context.is_cost
            or self._cost_transaction is not None
        ):
            return self._resolve(state, resolution)

        # Direct cost resolution gets the same transactional boundary as costs
        # processed through ActionProcessor.
        from .cost_transaction import CostTransaction, CostPaymentError
        from .validator import ValidationResult

        transaction = CostTransaction(state)
        previous = self._pending_triggers

        self._cost_transaction = transaction
        self._pending_triggers = []

        try:
            result = self._resolve(state, resolution)

            if not result.success:
                transaction.rollback()
                return result

        except CostPaymentError as error:
            transaction.rollback()
            return ExecutionResult(
                False,
                ValidationResult(False, str(error)),
                (),
            )

        except Exception:
            transaction.rollback()
            raise

        finally:
            pending = self._pending_triggers
            self._cost_transaction = None
            self._pending_triggers = previous

        # Cost-generated events become externally visible only after the whole
        # transaction has succeeded.
        self._emit_events(state, transaction.events)

        if previous is not None:
            # Nested resolution inherits the outer trigger-defer boundary.
            previous.extend(pending)

        else:
            # At top level, settle state before putting captured triggers onto
            # the stack.
            self._pending_triggers = pending
            try:
                self.settle(state)
            finally:
                self._pending_triggers = None

            if not getattr(state, "is_game_over", False):
                self._trigger_resolver.process(state, pending)

        return result

    def _resolve(
        self,
        state: State,
        resolution: ScheduledResolution,
    ) -> ExecutionResult:
        """!
        @brief Run the core resolution pipeline for one prepared intent.

        Performs preparation and validation, checks intervening-if conditions,
        materializes operations, validates costs, applies replacements,
        executes operations and finally processes SBAs/triggers when not
        deferred by an enclosing transaction.

        @param state Current game state.
        @param resolution Scheduled resolution or stack wrapper.
        @return Execution result for this intent.
        """
        # Stack entries wrap a scheduled resolution; direct actions pass the
        # resolution itself. Supporting both keeps one execution path.
        if hasattr(resolution, "action_resolution"):
            resolution = resolution.action_resolution

        from .validator import prepare_resolution, ValidationResult

        resolution, error = prepare_resolution(state, resolution)

        if error.success:
            error = self._validate(state, resolution)

        if not error.success:
            context = resolution.context

            # A spell whose resolution has become illegal still leaves the
            # stack and moves to its graveyard.
            if (
                context.ability is not None
                and context.ability.is_spell
                and not context.is_cost
            ):
                from ...enums import ZoneType
                from ...operations.card_operations import MoveCardOperation

                if context.source.get_zone() == ZoneType.STACK:
                    events = self._execute_operations(
                        state,
                        [
                            MoveCardOperation(
                                context,
                                context.source,
                                ZoneType.GRAVEYARD,
                            )
                        ],
                    )
                    self._emit_events(state, events)
                    self._process_triggers(
                        state,
                        self._resolve_state(state, events),
                    )

            return ExecutionResult(
                success=False,
                error=error,
                generated_events=None,
            )

        condition = getattr(
            resolution.context.ability,
            "intervening_if",
            None,
        )

        # Intervening-if triggered abilities check their condition again on
        # resolution. Failure means the ability resolves without effects.
        if (
            not resolution.context.is_cost
            and condition is not None
            and not condition(
                state,
                resolution.context.trigger_event,
            )
        ):
            if self._pending_triggers is None:
                self.settle(state)

            return ExecutionResult(
                True,
                None,
                (),
            )

        # Generate instructions against the current state. Each operation is
        # then replaced/executed against the state left by the previous one.
        operations = resolution.generate_operations(state)

        if resolution.context.is_cost:
            from .cost_validation import validate_cost_operations

            operations = tuple(operations)

            # Operation-owned mutable state must participate in rollback too.
            if self._cost_transaction is not None:
                self._cost_transaction.watch_operations(operations)

            message = validate_cost_operations(
                state,
                operations,
            )

            if message:
                return ExecutionResult(
                    False,
                    ValidationResult(False, message),
                    (),
                )

        previous_cost = self._executing_cost
        self._executing_cost = resolution.context.is_cost

        try:
            events = self._execute_operations(
                state,
                operations,
            )
        finally:
            self._executing_cost = previous_cost

        self._emit_events(state, events)

        # Nested/transactional execution defers SBA processing until the outer
        # boundary commits.
        if self._pending_triggers is None:
            events = self._resolve_state(state, events)

        self._process_triggers(state, events)

        return ExecutionResult(
            success=True,
            error=None,
            generated_events=tuple(events),
        )

    def execute(
        self,
        state: State,
        resolution: ScheduledResolution,
    ) -> ExecutionResult:
        """!
        @brief Resolve an intent using the name expected by existing callers.

        @param state Current game state.
        @param resolution Scheduled resolution to execute.
        @return Result returned by `resolve`.
        """
        return self.resolve(state, resolution)

    def resolve_simultaneous(self, state, operations):
        """!
        @brief Resolve a simultaneous operation group and settle resulting state.

        Replacement effects are chosen for the whole batch before execution,
        then events are emitted and state-based actions/triggers are processed.

        @param state Current game state.
        @param operations Operations belonging to one simultaneous event group.
        @return Successful execution result containing all resulting events.
        """
        replaced = self._apply_replacement_effects(
            state,
            tuple(operations),
        )

        events = self._operation_executor.execute_batch(
            state,
            replaced,
        )

        self._emit_events(state, events)

        if self._pending_triggers is None:
            events = self._resolve_state(state, events)

        self._process_triggers(state, events)

        return ExecutionResult(
            True,
            None,
            tuple(events),
        )

    def _validate(
        self,
        state: State,
        resolution: ScheduledResolution,
    ) -> ValidationResult:
        """!
        @brief Validate a prepared resolution immediately before execution.

        Cost resolutions additionally verify currently available mana,
        ability-level legality and each cost effect before ordinary target
        validation.

        @param state Current game state.
        @param resolution Prepared scheduled resolution.
        @return Successful or failed validation result.
        """
        from .validator import ValidationResult

        if (
            resolution.context.ability is not None
            and resolution.context.is_cost
        ):
            solver_result = getattr(
                resolution.generator,
                "mana_solver_result",
                None,
            )

            # A source-activating mana solver records the complete payment,
            # including mana produced by nested mana-ability actions. That mana
            # does not exist in the pool during this preflight check, so only
            # pool-only plans can be checked directly here.
            if (
                solver_result
                and solver_result.payment
                and not solver_result.mana_plan
            ):
                pool = resolution.context.controller.mana_pool

                if any(
                    pool.get(mana, 0) < count
                    for mana, count in solver_result.payment.items()
                ):
                    return ValidationResult(
                        False,
                        "Selected mana payment is no longer available.",
                    )

            error = resolution.context.ability.validation_error(
                resolution.context.source,
                resolution.context.controller,
                state,
            )

            if error:
                return ValidationResult(False, error)

            if resolution.context.effects:
                from dataclasses import replace

                # Validate each cost effect against the targets scoped to that
                # individual effect binding.
                for binding in resolution.context.effects.sequence:
                    error = binding.effect.validation_error(
                        state,
                        replace(
                            resolution.context,
                            targets=resolution.generator._scope_binding(
                                binding.slots
                            ),
                        ),
                    )

                    if error:
                        return ValidationResult(False, error)

        validator = TargetValidator()
        return validator.validate(
            state,
            resolution,
        )

    def _apply_replacement_effects(
        self,
        state: State,
        to_replace: Iterable[Operation],
    ) -> tuple[Operation]:
        """!
        @brief Pass operations through replacement-effect arbitration.

        @param state Current game state.
        @param to_replace Operations to transform.
        @return Final operations after replacement processing.
        """
        from .replacement_effects import ReplacementResolver

        return ReplacementResolver().replace(
            state,
            to_replace,
        )

    def _execute_operations(
        self,
        state: State,
        operations: Iterable[Operation],
    ) -> list[GameEvent]:
        """!
        @brief Replace and execute operations sequentially.

        During cost payment, each operation is revalidated immediately before
        execution so earlier payments or replacements cannot invalidate later
        cost components unnoticed.

        @param state Current game state.
        @param operations Operations to execute in order.
        @return Events generated by all executed operations.
        """
        res: list[GameEvent] = []
        revisions = {}

        if self._executing_cost:
            operations = tuple(operations)

            # Remember required card incarnations before any part of the cost
            # mutates the state.
            revisions = {
                id(op): (
                    op.card,
                    op.card.zone_revision,
                )
                for op in operations
                if hasattr(op, "card")
                and hasattr(op.card, "zone_revision")
            }

        for operation in operations:
            if self._executing_cost:
                from .cost_transaction import CostPaymentError
                from .cost_validation import validate_cost_operations

                expected = revisions.get(id(operation))

                if (
                    expected
                    and expected[0].zone_revision != expected[1]
                ):
                    raise CostPaymentError(
                        "A required cost object changed zones during payment."
                    )

                # Revalidate against state produced by all earlier cost
                # operations rather than relying only on initial preflight.
                error = validate_cost_operations(
                    state,
                    (operation,),
                )

                if error:
                    raise CostPaymentError(error)

            for replaced in self._apply_replacement_effects(
                state,
                (operation,),
            ):
                if self._executing_cost:
                    from .cost_transaction import CostPaymentError
                    from .cost_validation import validate_replaced_cost_operation

                    # Replacement effects may alter the concrete cost operation,
                    # so validate the actual operation that will execute.
                    error = validate_replaced_cost_operation(
                        state,
                        replaced,
                    )

                    if error:
                        raise CostPaymentError(error)

                generated = self._operation_executor.execute(state, replaced)
                res.extend(generated)
                from ..mana_activation import ManaActivationEventOperation
                if self._executing_cost and isinstance(replaced, ManaActivationEventOperation):
                    # Nested mana generation has completed. Resolve its mana
                    # triggers now, before spending the pool in the next cost.
                    # Remove only those captured triggers from later dispatch.
                    immediate = []
                    for index, event in enumerate(res):
                        captured = event.triggered_abilities or ()
                        immediate.extend(t for t in captured if t.definition.is_mana_ability)
                        res[index] = replace(event, triggered_abilities=tuple(
                            t for t in captured if not t.definition.is_mana_ability
                        ))
                    if immediate:
                        self._trigger_resolver.process(state, immediate)

        return res

    def _resolve_state(
        self,
        state: State,
        es: list[GameEvent],
    ) -> list[GameEvent]:
        """!
        @brief Repeatedly apply state-based actions until the state is stable.

        Events generated by every SBA pass are appended to the original event
        batch and emitted immediately.

        @param state Current game state.
        @param es Events produced before SBA processing.
        @return Combined original and SBA-generated events.
        """
        events = list(es)
        self.last_sba_events = []

        while True:
            sba_events = self._sba_resolver.resolve(state)

            if not sba_events:
                return events

            events.extend(sba_events)
            self.last_sba_events.extend(sba_events)

            self._emit_events(
                state,
                sba_events,
            )

    def _emit_events(
        self,
        state: State,
        events: Iterable[GameEvent],
    ) -> None:
        """!
        @brief Publish events immediately or buffer them inside a cost transaction.

        @param state Current game state.
        @param events Events to publish or buffer.
        """
        if self._cost_transaction is not None:
            from .event_bus import capture_event

            # Trigger eligibility is still captured at event time even though
            # publication itself waits until the cost transaction commits.
            self._cost_transaction.events.extend(
                capture_event(state, event)
                for event in events
            )
            return

        for event in events:
            self._event_bus.emit(
                event,
                state,
            )

    def _process_triggers(
        self,
        state: State,
        events: Iterable[GameEvent],
    ) -> None:
        """!
        @brief Collect event triggers and either defer or process them.

        @param state Current game state.
        @param events Events whose captured triggers should be handled.
        """
        triggers = self._event_bus.collect_trigger_abilities(
            state,
            events,
        )

        # Mana triggers bypass both the stack and the ordinary deferred queue.
        immediate = [t for t in triggers if t.definition.is_mana_ability]
        if immediate:
            self._trigger_resolver.process(state, immediate)
        triggers = [t for t in triggers if not t.definition.is_mana_ability]
        if self._pending_triggers is not None:
            self._pending_triggers.extend(triggers)
        else:
            self._trigger_resolver.process(
                state,
                triggers,
            )

    def settle(self, state):
        """!
        @brief Bring the game to a stable post-resolution state.

        Synchronizes indexes, emits deferred events, repeatedly resolves
        state-based actions, and collects/processes triggers according to the
        current trigger-defer boundary.

        @param state Current game state.
        """
        if hasattr(state, "synchronise_registers"):
            state.synchronise_registers()

        deferred = list(
            getattr(
                state,
                "_deferred_events",
                (),
            )
        )

        if deferred:
            state._deferred_events.clear()
            self._emit_events(
                state,
                deferred,
            )

        self._process_triggers(
            state,
            self._resolve_state(
                state,
                deferred,
            ),
        )
        from ..triggers.runtime_triggers import collect_runtime
        from .event_bus import GameEvent
        check = GameEvent("state_trigger_check")
        captured = collect_runtime(state, check, states_only=True)
        if captured:
            self._process_triggers(state, [GameEvent(
                "state_trigger_check", triggered_abilities=tuple(captured)
            )])