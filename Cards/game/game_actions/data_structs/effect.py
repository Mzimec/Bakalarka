from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, override
from collections.abc import Iterator, Iterable

if TYPE_CHECKING:
    from ...game_state import State
    from ...operations import Operation
    from .game_action import ResolutionContext 

from ....helper.runtime_object import RuntimeObject
from ...enums import *

__all__ = [
    "Effect"
]

class Effect(RuntimeObject):
    """!
    @brief Base class for ability effects that generate executable operations.
    """

    def __init__(self, key: str) -> None:
        """!
        @brief Create an effect identified by key and primary slot.
        @param key Effect identifier used by ability definitions.
        @param slot_key Primary target slot associated with the effect.
        @param max_repetitions Maximum number of times the effect may repeat.
        """
        self._key: str = key
    
    @property
    @override
    def key(self):
        return self._key

    @abstractmethod
    def to_operations(self, state: State, context: ResolutionContext) -> Iterator[Operation]:
        """!
        @brief Convert this effect into operations for the selected targets.
        @param state Current game state.
        @param binding Target binding scoped to this effect.
        @return Operations that implement the effect.
        """
        ...

    @abstractmethod
    def get_info(self) -> str:
        pass

    



         
