from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..game_state import Card, State
    from ..target import TargetBinding
    from ..operations import Operation

__all__ = [
    "Effect"
]

class Effect:
    def __init__(self, key: str, slot_key: str, max_repetitions: int = -1) -> None:
        self.key = key
        self.slot_key = slot_key
    
    @abstractmethod
    def to_operations(self, state: State, binding: TargetBinding) -> list[Operation]:
        pass

    



         
