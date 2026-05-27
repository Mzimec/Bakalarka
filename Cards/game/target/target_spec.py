from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..game_state.card import Card
    from ..game_state.state import State

__all__ = [
    "TargetSpec"
]


class TargetSpec:
    def get_candidates(self, source: Card, state: State) -> list:
        raise NotImplementedError

class TargetAllyUnitSpec(TargetSpec):
    def get_candidates(self, source: Card, state: State) -> list:
        return source.owner.battlefield.units
