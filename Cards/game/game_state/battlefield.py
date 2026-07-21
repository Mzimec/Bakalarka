from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass


from .card import Card
from ...helper.runtime_object import KeyedCollection
from helper import *
    
__all__ = [
    "Battlefield"
]

            
class CardCollection(KeyedCollection[Card]):
    pass

class Battlefield(CardCollection):
    """!
    @brief Zone that contains permanent cards currently on the battlefield.
    """

    def untap_all(self) -> None:
        """!
        @brief Untap every permanent currently on the battlefield.
        """
        for permanent in self.permanents:
            permanent.is_tapped = False
    
