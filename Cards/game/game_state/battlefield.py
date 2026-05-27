from __future__ import annotations
from typing import TYPE_CHECKING, Callable
if TYPE_CHECKING:
    from .card import PermanentCard
    
__all__ = [
    "Battlefield"
]

class Battlefield:
    def __init__(self, permanents: list[PermanentCard]) -> None:
        self.pemanents = permanents

    def untap_all(self) -> None:
        for permanent in self.pemanents:
            permanent.is_tapped = False
    
