"""Candidate specifications backed by predicates and query indexes."""

from __future__ import annotations
from typing import TYPE_CHECKING, override
from abc import ABC, abstractmethod
from collections.abc import Iterator, Set
from dataclasses import dataclass
from typing import Callable
from helper.query_system.query import Query

if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from helper.runtime_object import RuntimeObject

__all__ = ["TargetSpec", "QueryTargetSpec", "PlayerQueryTargetSpec"]


class TargetSpec(ABC):
    """!
    @brief Base object that discovers legal target candidates.
    """

    @abstractmethod
    def generate_candidates(
        self,
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None,
    ) -> Iterator[RuntimeObject]:
        """!
        @brief Yield candidates permitted by this target specification.

        @param source Card that is choosing targets.
        @param controller Player choosing targets.
        @param state Current game state.
        @param reserved Objects excluded by cross-slot constraints.
        @return Iterator of legal target candidates.
        """
        ...

    def is_valid_target(
        self,
        target: RuntimeObject,
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None,
    ) -> bool:
        """!
        @brief Check whether one runtime object belongs to this target specification.

        The base implementation performs a membership check by enumerating the
        current candidates. Indexed specifications may override this with a
        direct query-membership test.

        @param target Candidate object.
        @param source Card that is choosing targets.
        @param controller Player choosing targets.
        @param state Current game state.
        @param reserved Objects excluded by cross-slot constraints.
        @return Whether the target is currently legal.
        """
        if reserved and target in reserved:
            return False

        return any(
            candidate == target for candidate in self.generate_candidates(source, controller, state)
        )


@dataclass(frozen=True)
class QueryTargetSpec(TargetSpec):
    """!
    @brief Card target specification backed by the live query indexes.

    `query` may be a fixed query or a factory evaluated for the current source,
    controller and game state.
    """

    query: Query | Callable[[Card, Player, State], Query]

    def query_for(self, source, controller, state):
        """!
        @brief Resolve the query used for this targeting request.
        """
        return self.query(source, controller, state) if callable(self.query) else self.query

    def register(self, state):
        """!
        @brief Return the indexed register searched by this specification.
        """
        return state.card_register

    def generate_candidates(self, source, controller, state, reserved=None):
        """!
        @brief Yield indexed objects satisfying this specification.

        Reserved objects are removed after the underlying query has selected its
        candidate set.
        """
        for card in self.register(state).query(self.query_for(source, controller, state)):
            if not reserved or card not in reserved:
                yield card

    def is_valid_target(self, target, source, controller, state, reserved=None):
        """!
        @brief Check target membership directly against the live index.

        This avoids enumerating every matching candidate merely to validate one
        already-selected target.
        """
        if reserved and target in reserved:
            return False

        return self.register(state).contains(self.query_for(source, controller, state), target)


class PlayerQueryTargetSpec(QueryTargetSpec):
    """!
    @brief Query-backed target specification operating on players instead of cards.
    """

    def register(self, state):
        """!
        @brief Return the player register searched by this specification.
        """
        return state.player_register


class TargetAllyUnitSpec(TargetSpec):
    """!
    @brief Target specification selecting allied units controlled by the chooser.
    """

    @override
    def generate_candidates(self, source, controller, state, reserved):
        """!
        @brief Yield allied battlefield units for the targeting controller.

        @param source Card that is choosing targets.
        @param controller Player choosing targets.
        @param state Current game state.
        @param reserved Objects excluded by cross-slot constraints.
        @return Iterator of allied unit candidates.
        """
        for target in controller.battlefield.units:
            yield target