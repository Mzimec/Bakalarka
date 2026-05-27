from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from action_executor import ActionExecutor
    from game_action import ActionIntent, GameAction
    from ..game_state import State


class ActionProcessor:
    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def process(self, state: State, action: GameAction) -> None:
        intents = action.get_intents()
        for intent in intents:
            if intent.context.uses_stack:
                self._route(intent)
            else:
                self.executor.execute(state, intent)
    
    def _route(self, state: State, action: ActionIntent):
        state.stack.push(action)
