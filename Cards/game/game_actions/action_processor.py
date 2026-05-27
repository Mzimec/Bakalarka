from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .action_executor import ActionExecutor, ExecutionResult
    from .game_action import ActionIntent, GameAction
    from ..game_state import State


class ActionProcessor:
    """!
    @brief Routes action intents either to the stack or directly to execution.
    """

    def __init__(self, executor: ActionExecutor) -> None:
        """!
        @brief Create a processor using the given action executor.
        @param executor Executor used for intents that do not use the stack.
        """
        self.executor = executor

    def process(self, state: State, action: GameAction) -> list[ExecutionResult]:
        """!
        @brief Process all intents produced by a game action.
        @param state Current game state.
        @param action Game action chosen by a player or rule.
        @return Results for intents that were executed immediately.
        """
        results: list[ExecutionResult] = []
        intents = action.get_intents()
        for intent in intents:
            if intent.context.uses_stack:
                self._route(state, intent)
            else:
                results.append(self.executor.execute(state, intent))
        return results
    
    def _route(self, state: State, action: ActionIntent) -> None:
        """!
        @brief Put a stack-using action intent onto the game stack.
        @param state Current game state.
        @param action Action intent to push.
        """
        state.stack.push(action)
