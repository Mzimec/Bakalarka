from __future__ import annotations
from typing import TYPE_CHECKING, override
from abc import ABC, abstractmethod
from collections.abc import Iterator, Set

if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from ...helper.runtime_object import RuntimeObject

__all__ = [
    "TargetSpec"
]


class TargetSpec(ABC):
    """!
    @brief Base object that finds legal target candidates.
    """
    
    @abstractmethod
    def generate_candidates(
        self, 
        source: Card, 
        controller: Player,
        state: State, 
        reserved: Set[RuntimeObject] | None = None
    ) -> Iterator[RuntimeObject]:
        """!
        @brief Return candidates that may be targeted by the source.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Legal target candidates.
        """
        ...
    
    def is_valid_target(
        self,
        target: RuntimeObject,
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None
    ) -> bool:
        
        if reserved and target in reserved:
            return False

        return any(
            candidate == target 
            for candidate in self.generate_candidates(source, controller, state)
        )
    

class TargetAllyUnitSpec(TargetSpec):
    """!
    @brief Target spec that selects allied units controlled by the source owner.
    """

    @override
    def generate_candidates(self, source, controller, state, reserved):
        """!
        @brief Return allied battlefield units for the source owner.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Allied unit candidates.
        """
        for target in controller.battlefield.units:
            yield target
