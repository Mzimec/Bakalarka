from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..data_structs.game_action import GameAction, ScheduledResolution
    from ...game_state import State
    from .resolution_engine import ResolutionEngine, ExecutionResult


class ActionProcessor:
    """!
    @brief Routes action intents either to the stack or directly to execution.
    """

    def __init__(self, executor: ResolutionEngine) -> None:
        """!
        @brief Create a processor using the given action executor.
        @param executor Executor used for intents that do not use the stack.
        """
        self.executor = executor

    def process(self, state: State, action: GameAction) -> tuple[ExecutionResult]:
        """!
        @brief Process all intents produced by a game action.
        @param state Current game state.
        @param action Game action chosen by a player or rule.
        @return Results for intents that were executed immediately.
        """
        results: list[ExecutionResult] = []
        resolutions = action.get_intents()
        for resolution in resolutions:
            if resolution.context.uses_stack:
                self._route(state, resolution)
            else:
                results.append(self.executor.resolve(state, resolution))
        return tuple(results)
    
    def _route(self, state: State, resolution: ScheduledResolution) -> None:
        """!
        @brief Put a stack-using action intent onto the game stack.
        @param state Current game state.
        @param action Action intent to push.
        """
        state.stack.push(resolution.to_stack_item())
