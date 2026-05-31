from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
if TYPE_CHECKING:
    from .card import PermanentCard, Card
    
__all__ = [
    "Battlefield"
]


class CardPack:

    def __init__(self, cards: list[Card] = []) -> None:
        self._card_order: list[str] = []
        self._cards: dict[str, Card] = {}

        for card in cards:
            self.append(card)

    def append(self, card: Card) -> None:
        if self._cards.get(card.key):
            raise RuntimeWarning(f"  Trying to add card with key: '{card.key}' that is already present in CardPack.")
        self._card_order.append(card.key)
        self._cards[card.key] = card

    def remove(self, card_key: str) -> None:
        if not self._cards.get(card_key):
            raise RuntimeWarning(f"  Trying to remove card with key: '{card_key}' that is not present in CardPack.")
        self._cards.pop(card_key)
        self._card_order.remove(card_key)

    def remove(self, card: Card) -> None:
        self.remove(card.key)
    
    def pop(self) -> Card:
        card_key = self._card_order.pop()
        return self._cards.pop(card_key)
    
    def get(self, card_key: str) -> Card | None:
        return self._cards.get(card_key)
    
    def to_string(self, in_detail: bool = False) -> str:
        res: str = ""
        for key in self._card_order.reverse():
            res += "\n"
            res += self._cards.get(key).to_string()
            


class Battlefield(CardPack):
    """!
    @brief Zone that contains permanent cards currently on the battlefield.
    """

    def untap_all(self) -> None:
        """!
        @brief Untap every permanent currently on the battlefield.
        """
        for permanent in self.permanents:
            permanent.is_tapped = False
    
