from __future__ import annotations
from typing import TYPE_CHECKING, Callable
if TYPE_CHECKING:
    from .card import PermanentCard
    
__all__ = [
    "Battlefield"
]

class Battlefield:
    """!
    @brief Zone that contains permanent cards currently on the battlefield.
    """

    def __init__(self, permanents: list[PermanentCard]) -> None:
        """!
        @brief Create a battlefield with the given permanents.
        @param permanents Permanents that start on the battlefield.
        """
        self.permanents = permanents

    def untap_all(self) -> None:
        """!
        @brief Untap every permanent currently on the battlefield.
        """
        for permanent in self.permanents:
            permanent.is_tapped = False
    
