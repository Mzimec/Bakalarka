from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar
from dataclasses import dataclass, replace, field
from collections.abc import Mapping
from time import perf_counter_ns

from ..enums import RequestType

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction


@dataclass(frozen=True)
class DecisionResult:
    """Result of one player decision together with optional diagnostics."""

    value: GameAction | None
    info: Mapping[str, Any] = field(default_factory=dict)

    def with_info(self, **info) -> DecisionResult:
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

    request_type: ClassVar[RequestType]


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

    def decide(
        self,
        request: Request,
    ) -> DecisionResult:
        started = perf_counter_ns()

        result = self._decide(request)

        elapsed = perf_counter_ns() - started

        return result.with_info(
            elapsed_ns=elapsed,
        )

    @abstractmethod
    def _decide(self, request: Request) -> DecisionResult: ...
