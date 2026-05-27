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
    """!
    @brief Base class for ability effects that generate executable operations.
    """

    def __init__(self, key: str, slot_key: str, max_repetitions: int = -1) -> None:
        """!
        @brief Create an effect identified by key and primary slot.
        @param key Effect identifier used by ability definitions.
        @param slot_key Primary target slot associated with the effect.
        @param max_repetitions Maximum number of times the effect may repeat.
        """
        self.key = key
        self.slot_key = slot_key
    
    @abstractmethod
    def to_operations(self, state: State, binding: TargetBinding) -> list[Operation]:
        """!
        @brief Convert this effect into operations for the selected targets.
        @param state Current game state.
        @param binding Target binding scoped to this effect.
        @return Operations that implement the effect.
        """
        pass

    



         
