from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, override
from dataclasses import dataclass, replace, field
from collections.abc import Mapping, Iterator
from time import perf_counter_ns

from ..enums import RequestType

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction

class DecisionResult:
    """Result of one player decision together with optional diagnostics."""

    def __init__(self, value: DecisionOption, info: Mapping[str, Any] | None = None) -> None:
        self.value: DecisionOption
        self.info: dict[str, Any] = dict(info) if info else dict()


class DecisionOptionSpace[T: DecisionOption](ABC):

    @abstractmethod
    def generate(self,) -> Iterator[T]: ...


class DecisionOption(ABC):
    pass


@dataclass(frozen=True)
class DecisionRequest[T: DecisionOption](ABC):
    state: State
    player: Player

    options: DecisionOptionSpace[T]


class DecisionMaker(ABC):
    """!
    @brief Base contract for human, scripted or AI player controllers.
    """

    def decide(
        self,
        request: DecisionRequest,
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
    def _decide(self, request: Request) -> DecisionResult:
        match request:
            case PriorityActionRequest():
                return self._decide_priority_action(request.state, request.player)

            case AttackerDeclarationRequest():
                return self._decide_attacker_declaration(request.state, request.player)

            case BlockerDeclarationRequest():
                return self._decide_blocker_declaration(request.state, request.player)

            case MulliganRequest():
                return self._decide_mulligan(request.state, request.player, request.mulligans_taken)

            case MulliganBottomRequest():
                return self._decide_mulligan_bottom(request.state, request.player, request.count)

            case DiscardRequest():
                return self._decide_discard(request.state, request.player, request.count)

            case _:
                raise NotImplementedError(
                    f"Unsupported request: {type(request).__name__}"
                )

    @abstractmethod
    def _decide_priority_action(self, state: State, player: Player) -> DecisionResult: ...

    @abstractmethod
    def _decide_attacker_declaration(self, state: State, player: Player) -> DecisionResult: ...

    @abstractmethod
    def _decide_blocker_declaration(self, state: State, player: Player) -> DecisionResult: ...

    @abstractmethod
    def _decide_mulligan(self, state: State, player: Player, mulligans_taken: int) -> DecisionResult: ...

    @abstractmethod
    def _decide_mulligan_bottom(self, state: State, player: Player, count: int) -> DecisionResult: ...

    @abstractmethod
    def _decide_discard(self, state: State, player: Player, count: int) -> DecisionResult: ...
