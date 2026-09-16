from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction

class DecisionMaker(ABC):
    """!
    @brief Base contract for human, scripted or AI player controllers.
    """

    @abstractmethod
    def get_action(self, state: State, player: Player) -> GameAction | None:
        """!
        @brief Choose a game action for the supplied player.

        @param state Current game state.
        @param player Player making the decision.
        @return Chosen game action, or `None` when no action is chosen.
        """
        pass

    def process_triggers(self, state: State, triggers) -> None:
        """!
        @brief Handle triggered abilities controlled by this player.

        Controllers that do not override trigger processing reject non-empty
        trigger batches rather than silently ignoring mandatory decisions.
        """
        if list(triggers):
            raise NotImplementedError("This controller does not handle triggered abilities.")

    def choose_attackers(self, state, player):
        """!
        @brief Choose attackers for combat.

        @return Mapping describing attacker declarations.
        """
        return {}

    def choose_blockers(self, state, player):
        """!
        @brief Choose blockers for combat.

        @return Mapping describing blocker declarations.
        """
        return {}

    def choose_discards(self, state, player, count):
        """!
        @brief Choose cards to discard during cleanup.

        Default behavior selects the last `count` cards from the hand.
        """
        return tuple(player.hand.values())[-count:] if count else ()

    def choose_mulligan(self, state, player, mulligans_taken):
        """!
        @brief Decide whether to take another mulligan.

        Default controllers always keep their current hand.
        """
        return False

    def choose_mulligan_bottom(self, state, player, count):
        """!
        @brief Choose cards to place on the bottom after a London mulligan.

        Default behavior chooses the first `count` cards in current hand order.
        """
        return tuple(player.hand.values())[:count]