from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, override
from dataclasses import dataclass, replace, field
from collections.abc import Mapping
from time import perf_counter_ns

from ..enums import RequestType

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction

class DecisionResult:
    """Result of one player decision together with optional diagnostics."""

    def __init__(self, value: GameAction | None, info: Mapping[str, Any] | None = None) -> None:
        self.value: GameAction | None = value
        self.info: dict[str, Any] = dict(info) if info else dict()


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
        result.info["elapsed_time"] = elapsed

        return result
    
    @abstractmethod
    def _decide(self, request: Request) -> DecisionResult: ...


class ModularDecisionMaker(DecisionMaker, ABC):

    @override
    def _decide(self, request):
        result: DecisionResult
        
        match request.request_type:
            case RequestType.PRIORITY_ACTION:
                result = _decide_priority_action()
            case RequestType.ATTACKER_DECLARATION:
                result = _decide_atacker_declaration()
            case RequestType.BLOCKER_DECLARATIION:
                result = _decide_blocker_declaration()
            case RequestType.MULLIGAN:
                result = _decide_mulligan()
            case RequestType.MULLIGAN_BOTTOM:
                result = _decide_mulligan_bottom()
            case _:
                result = _decide_discard()

        return result
