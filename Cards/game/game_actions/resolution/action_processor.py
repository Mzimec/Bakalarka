"""Validate actions, commit costs and route resolutions to the stack."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..data_structs.game_action import GameAction, ScheduledResolution
    from ...game_state import State
    from .resolution_engine import ResolutionEngine, ExecutionResult


class ActionProcessor:
    """!
    @brief Validate and process complete game actions.

    Validates selected targets before any cost is paid, executes immediate
    resolutions, routes stack-using resolutions onto the game stack, and
    treats cost payment as a transaction that can be rolled back on failure.

    Trigger processing is temporarily deferred until all costs belonging
    to the action have been successfully committed.
    """

    def __init__(self, executor: ResolutionEngine) -> None:
        """!
        @brief Create a processor using the given resolution engine.

        @param executor Engine used to execute immediate resolutions and
               settle resulting state-based actions and triggers.
        """
        self.executor = executor

    def process(self, state: State, action: GameAction) -> tuple[ExecutionResult, ...]:
        """!
        @brief Validate and process all resolutions produced by a game action.

        The action and all of its selected targets are validated before
        execution begins. Cost resolutions are executed transactionally;
        if any resolution fails, already-paid costs are rolled back.

        Stack-using resolutions are pushed onto the stack, while immediate
        resolutions are executed directly. Events and triggers produced
        during cost payment are deferred until the complete cost transaction
        has successfully committed.

        @param state Current game state.
        @param action Game action chosen by a player or game rule.
        @return Results for resolutions executed immediately. Stack-routed
                resolutions do not produce an immediate execution result.
        """
        results: list[ExecutionResult] = []
        resolutions = tuple(action.get_intents())

        # A generated action may have become stale before submission, so
        # validate both the action itself and all selected targets before
        # paying any part of its cost.
        from .validator import TargetValidator
        from .resolution_engine import ExecutionResult
        from .validator import ValidationResult

        validate = getattr(action, "validation_error", None)
        if validate:
            message = validate(state)
            if message:
                return (
                    ExecutionResult(
                        False,
                        ValidationResult(False, message),
                        (),
                    ),
                )

        for resolution in resolutions:
            error = TargetValidator().validate(state, resolution)
            if not error.success:
                return (ExecutionResult(False, error, ()),)

        # Defer triggers produced during this action until all costs have
        # successfully completed.
        previous = getattr(self.executor, "_pending_triggers", None)
        self.executor._pending_triggers = []

        from .cost_transaction import CostTransaction, CostPaymentError

        previous_transaction = self.executor._cost_transaction

        # Only actions containing cost resolutions need rollback support.
        transaction = (
            CostTransaction(state)
            if any(r.context.is_cost for r in resolutions)
            else None
        )

        if transaction is not None:
            self.executor._cost_transaction = transaction

        try:
            for resolution in resolutions:
                if resolution.context.uses_stack:
                    self._route(state, resolution)
                else:
                    result = self.executor.resolve(state, resolution)
                    results.append(result)

                    # Failure of any immediate resolution aborts the whole
                    # cost transaction and discards deferred triggers.
                    if not result.success:
                        if transaction is not None:
                            transaction.rollback()

                        self.executor._pending_triggers.clear()
                        return (result,)

            else:
                # After successful processing, emit the action-level event.
                # Triggered abilities do not emit another activation event.
                if (
                    getattr(action, "ability", None) is not None
                    and getattr(action, "trigger_event", None) is None
                ):
                    from .event_bus import GameEvent, capture_event

                    event = capture_event(
                        state,
                        GameEvent(
                            (
                                "spell_cast"
                                if action.ability.is_spell
                                else "ability_activated"
                            ),
                            action.source,
                            action.controller or action.source.owner,
                            {"ability": action.ability},
                        ),
                    )

                    self.executor._emit_events(state, [event])
                    self.executor._process_triggers(state, [event])

        except CostPaymentError as error:
            # A failed cost payment is a normal action failure rather than
            # an engine error, so return it as an unsuccessful result.
            if transaction is not None:
                transaction.rollback()

            self.executor._pending_triggers.clear()

            return (
                ExecutionResult(
                    False,
                    ValidationResult(False, str(error)),
                    (),
                ),
            )

        except Exception:
            # Unexpected failures must still restore the state of any
            # partially completed cost transaction before propagating.
            if transaction is not None:
                transaction.rollback()

            self.executor._pending_triggers.clear()
            raise

        finally:
            pending = self.executor._pending_triggers
            self.executor._pending_triggers = previous
            self.executor._cost_transaction = previous_transaction

        # Publish transaction events only after all costs have successfully
        # committed.
        if transaction is not None:
            self.executor._emit_events(state, transaction.events)

        # Costs must be fully committed before SBAs or controller trigger
        # callbacks are allowed to observe the resulting state.
        self.executor._pending_triggers = pending

        try:
            self.executor.settle(state)
        finally:
            self.executor._pending_triggers = previous

        # Return newly collected triggers to an enclosing resolution if one
        # exists; otherwise process them immediately.
        if previous is not None:
            previous.extend(pending)
        elif pending and not getattr(state, "is_game_over", False):
            self.executor._trigger_resolver.process(state, pending)

        return tuple(results)

    def _route(self, state: State, resolution: ScheduledResolution) -> None:
        """!
        @brief Push a stack-using resolution onto the game stack.

        @param state Current game state.
        @param resolution Scheduled resolution to route to the stack.
        """
        state.stack.push(resolution.to_stack_item())

