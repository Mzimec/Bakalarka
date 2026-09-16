from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from dataclasses import dataclass, replace, field
from collections.abc import Mapping

from ..enums import RequestType

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction


@dataclass(frozen=True)
class DecisionResult[T]:
    """Result of one player decision together with optional diagnostics."""

    value: T
    info: Mapping[str, Any] = field(default_factory=dict)

    def with_info(self, **info) -> DecisionResult[T]:
        """Return a copy enriched with additional diagnostic information."""
        return replace(
            self,
            info={
                **self.info,
                **info,
            },
        )


@dataclass(frozen=True)
class Request(ABC):
    state: State
    player: Player

    request_type: RequestType


@dataclass(frozen=True)
class PriorityActionRequest(Request):
    request_type = RequestType.PRIORITY_ACTION


@dataclass(frozen=True)
class AttackerDeclarationRequest(Request):
    request_type = RequestType.ATTACKER_DECLARATION


@dataclass(frozen=True)
class BlockerDeclarationRequest(Request):
    request_type = RequestType.BLOCKER_DECLARATIION


@dataclass(frozen=True)
class MulliganRequest(Request):
    mulligans_taken: int
    request_type = RequestType.MULLIGAN


@dataclass(frozen=True)
class MulliganBottomRequest(Request):
    count: int
    request_type = RequestType.MULLIGAN_BOTTOM

@dataclass(frozen=True)
class DiscardRequest(Request):
    count: int
    request_type = RequestType.DISCARD



class DecisionMaker(ABC):
    """!
    @brief Base contract for human, scripted or AI player controllers.
    """

    @abstractmethod
    def get_action(self, state: State, player: Player) -> DecisionResult[GameAction | None]:
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

class TimedDecisionMaker:

    def __init__(self, decision_maker: DecisionMaker) -> None:
        self._total: float = 0
        self._decision_maker: DecisionMaker = decision_maker

    def get_timed_action(self, state: State, player: Player) -> tuple[GameAction | None, float]:
        pass
