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

    def _before_write(self):
        hook = self.__dict__.get("_cost_before_write")
        if hook is not None:
            hook()

    def __setitem__(self, key, value):
        self._before_write()
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._before_write()
        dict.__delitem__(self, key)

    def pop(self, *args):
        self._before_write()
        return dict.pop(self, *args)

    def popitem(self):
        self._before_write()
        return dict.popitem(self)

    def clear(self):
        self._before_write()
        dict.clear(self)

    def update(self, *args, **kwargs):
        self._before_write()
        dict.update(self, *args, **kwargs)

    def setdefault(self, *args):
        self._before_write()
        return dict.setdefault(self, *args)

    def __ior__(self, other):
        self.update(other)
        return self


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
