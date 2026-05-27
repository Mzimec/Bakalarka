from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..game_state.card import Card
    from ..game_state.state import State

__all__ = [
    "TargetSpec"
]


class TargetSpec:
    """!
    @brief Base object that finds legal target candidates.
    """

    def get_candidates(self, source: Card, state: State) -> list:
        """!
        @brief Return candidates that may be targeted by the source.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Legal target candidates.
        """
        raise NotImplementedError

class TargetAllyUnitSpec(TargetSpec):
    """!
    @brief Target spec that selects allied units controlled by the source owner.
    """

    def get_candidates(self, source: Card, state: State) -> list:
        """!
        @brief Return allied battlefield units for the source owner.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Allied unit candidates.
        """
        return source.owner.battlefield.units
