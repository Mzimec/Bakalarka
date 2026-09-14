"""Battlefield attachments and the objects that can carry them."""

from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
from collections.abc import Iterable, Mapping


from .card import Card
from helper.runtime_object import KeyedCollection
from helper import *

__all__ = ["Battlefield"]


class CardCollection(KeyedCollection[Card]):
    """!
    @brief Key-addressable collection of runtime card objects.

    Cards are stored under their stable `card.key`. Re-adding the same runtime
    object is allowed, while a different card using an occupied key is rejected.
    """

    def __init__(
        self,
        cards: Iterable[Card] | Mapping[str, Card] = (),
    ) -> None:
        """!
        @brief Initialize the collection from cards or a card mapping.

        @param cards Iterable of cards or mapping whose values are cards.
        """
        super().__init__()

        for card in cards.values() if isinstance(cards, Mapping) else cards:
            self.append(card)

    def append(self, card: Card) -> None:
        """!
        @brief Add a card under its runtime key.

        @param card Card to add.
        @throws ValueError If another card already occupies the same key.
        """
        if card.key in self and self[card.key] is not card:
            raise ValueError(f"Duplicate card key: {card.key}")

        self[card.key] = card


class Battlefield(CardCollection):
    """!
    @brief Zone that contains permanent cards currently on the battlefield.
    """

    def untap_all(self) -> None:
        """!
        @brief Untap every permanent currently on the battlefield.
        """
        for permanent in self.values():
            permanent.is_tapped = False