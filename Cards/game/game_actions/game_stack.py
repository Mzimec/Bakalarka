from __future__ import annotations

from typing import TYPE_CHECKING
from .game_action import PassPriorityAction

if TYPE_CHECKING:
    from .game_action import ActionIntent
    from .action_executor import ActionExecutor
    from .action_processor import ActionProcessor
    from ..game_state import State, Player

class GameStack:
    """!
    @brief Last-in-first-out stack of pending action intents.
    """

    def __init__(self) -> None:
        """!
        @brief Create an empty game stack.
        """
        self.items: list[ActionIntent] = []

    def push(
        self,
        item: ActionIntent
    ) -> None:
        """!
        @brief Push an item onto the top of the stack.
        @param item Stack item to add.
        """

        self.items.append(item)

    def pop(self) -> ActionIntent:
        """!
        @brief Remove and return the top stack item.
        @return The most recently pushed stack item.
        """

        if not self.items:
            raise RuntimeError("Stack is empty.")

        return self.items.pop()

    def peek(self) -> ActionIntent | None:
        """!
        @brief Return the top stack item without removing it.
        @return Top stack item, or None when the stack is empty.
        """

        if not self.items:
            return None

        return self.items[-1]

    def is_empty(self) -> bool:
        """!
        @brief Check whether the stack contains any items.
        @return True if the stack is empty.
        """
        return len(self.items) == 0


class PrioritySystem:
    """!
    @brief Tracks which players have passed priority in the current window.
    """

    def __init__(self, state: State) -> None:
        """!
        @brief Initialize priority with the active player.
        @param state Current game state.
        """
        self.state = state
        self.current_player = state.active_player
        self.passed_players: set[Player] = set()

    def pass_priority(self, player: Player) -> None:
        """!
        @brief Mark a player as passing and advance priority.
        @param player Player passing priority.
        """
        self.passed_players.add(player)
        self.current_player = self.state.get_next_player(player)

    def reset(self) -> None:
        """!
        @brief Return priority to the active player and clear pass history.
        """
        self.current_player = self.state.active_player
        self.passed_players.clear()

    def all_passed(self, state: State) -> bool:
        """!
        @brief Check whether every player has passed in this priority window.
        @param state Current game state.
        @return True if all players have passed.
        """
        return len(self.passed_players) >= len(state.players)
    

class PriorityLoop:
    """!
    @brief Runs priority until the stack is empty or a stack item resolves.
    """

    def __init__(self, action_processor: ActionProcessor, action_executor: ActionExecutor) -> None:
        """!
        @brief Create a priority loop with routing and execution services.
        @param action_processor Processor for newly chosen actions.
        @param action_executor Executor for resolved stack items.
        """
        self.action_processor = action_processor
        self.action_executor = action_executor

    def run(self,state: State) -> None:
        """!
        @brief Run priority passing and resolve stack items when all players pass.
        @param state Current game state.
        """
        priority = state.priority
        while True:
            player = priority.current_player
            action = player.get_action(state)

            if isinstance(action, PassPriorityAction):
                priority.pass_priority(player)
            else:
                self.action_processor.process(state, action)
                priority.reset()

            if not priority.all_passed(state):
                continue

            if state.stack.is_empty():
                return

            payload = state.stack.pop()
            self.action_executor.execute(state, payload)

            priority.reset()

class ReactionWindow:
    """!
    @brief Runs a shorter priority window for reactions.
    """

    def __init__(self, action_processor: ActionProcessor) -> None:
        """!
        @brief Create a reaction window using the given action processor.
        @param action_processor Processor for actions chosen during the window.
        """
        self.action_processor = action_processor
    
    def run(self, state: State) -> None:
        """!
        @brief Process reactions until all players pass.
        @param state Current game state.
        """

        priority = state.priority

        while not priority.all_passed(state):
            player = priority.current_player
            action = player.get_action(state)
            self.action_processor.process(state, action)             



class StackResolver:
    """!
    @brief Resolves the top item of the game stack.
    """

    def __init__(self, executor: ActionExecutor) -> None:
        """!
        @brief Create a stack resolver using the given action executor.
        @param executor Executor used to resolve stack items.
        """
        self.executor = executor

    def resolve_top(self, state: State) -> None:
        """!
        @brief Pop and execute the top stack item.
        @param state Current game state.
        """
        payload = state.stack.pop()
        self.executor.execute(state, payload)
