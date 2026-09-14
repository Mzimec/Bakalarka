"""Stack storage and tracking of player priority."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .data_structs.game_action import ActionIntent
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

    def push(self, item: ActionIntent) -> None:
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
    @brief Expose priority state maintained by the active PriorityWindow.

    PriorityWindow owns passing and stack resolution. This object provides the
    current player and pass history to validators, controllers and turn setup.
    """

    def __init__(self, state: State) -> None:
        """!
        @brief Initialize priority with the active player.
        @param state Current game state.
        """
        self.state = state
        self.current_player = state.active_player
        self.passed_players: set[Player] = set()


    def reset(self) -> None:
        """!
        @brief Return priority to the active player and clear pass history.
        """
        self.current_player = self.state.active_player
        self.passed_players.clear()
