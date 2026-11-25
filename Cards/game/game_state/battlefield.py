"""Battlefield attachments and the objects that can carry them."""

from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
from collections.abc import Iterable, Mapping, ItemsView, ValuesView


from .card import Card
from helper.runtime_object import KeyedCollection
from helper import *
from .card_repository import CardRepository

__all__ = ["Battlefield"]


def _empty_collection(cls):
    return dict.__new__(cls)


class _CardValues(ValuesView):
    def __iter__(self):
        zone = self._mapping
        cards = zone._repository._cards
        for identifier in dict.values(zone):
            yield cards[identifier]

    def __reversed__(self):
        zone = self._mapping
        cards = zone._repository._cards
        for identifier in reversed(dict.values(zone)):
            yield cards[identifier]


class _CardItems(ItemsView):
    def __iter__(self):
        zone = self._mapping
        cards = zone._repository._cards
        for key, identifier in dict.items(zone):
            yield key, cards[identifier]

    def __reversed__(self):
        zone = self._mapping
        cards = zone._repository._cards
        for key, identifier in reversed(dict.items(zone)):
            yield key, cards[identifier]


class CardCollection(KeyedCollection[Card]):
    """!
    @brief Ordered zone membership by stable ID with a Card-facing mapping API.

    Cards are stored under their stable `card.key`. Re-adding the same runtime
    object is allowed, while a different card using an occupied key is rejected.
    """

    def __init__(
        self,
        cards: Iterable[Card] | Mapping[str, Card] = (), *, repository=None,
    ) -> None:
        """!
        @brief Initialize the collection from cards or a card mapping.

        @param cards Iterable of cards or mapping whose values are cards.
        """
        super().__init__()
        self._repository = CardRepository() if repository is None else repository

        for card in cards.values() if isinstance(cards, Mapping) else cards:
            self.append(card)

    def bind(self, repository):
        """Move resolution to the game's repository, preserving membership/order."""
        if repository is not self._repository:
            for card in self.values():
                repository.add(card)
            self._repository = repository

    @property
    def card_ids(self):
        return tuple(dict.__iter__(self))

    def __iter__(self):
        return dict.__iter__(self)

    def __getitem__(self, key):
        return self._repository._cards[dict.__getitem__(self, key)]

    def get(self, key, default=None):
        return self[key] if key in self else default

    def values(self):
        return _CardValues(self)

    def items(self):
        return _CardItems(self)

    def copy(self):
        return dict(self.items())

    def __eq__(self, other):
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result

    def __repr__(self):
        return repr(dict(self.items()))

    def __reduce_ex__(self, protocol):
        return _empty_collection, (type(self),), (self.__dict__, dict(dict.items(self)))

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)  # Extension reduction's legacy object state.
            return
        attributes, identifiers = state
        object.__setattr__(self, '__dict__', attributes)
        dict.update(self, identifiers)

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
        if not isinstance(value, Card) or key != value.key:
            raise ValueError('Card zones accept cards under their stable card key')
        self._before_write()
        self._repository.add(value)
        dict.__setitem__(self, key, value.key)

    def __delitem__(self, key):
        self._before_write()
        dict.__delitem__(self, key)

    def pop(self, *args):
        if not 1 <= len(args) <= 2:
            raise TypeError('pop expected one or two arguments')
        key = args[0]
        if key not in self:
            if len(args) == 2:
                return args[1]
            raise KeyError(key)
        self._before_write()
        return self._repository._cards[dict.pop(self, key)]

    def popitem(self):
        self._before_write()
        key, identifier = dict.popitem(self)
        return key, self._repository._cards[identifier]

    def clear(self):
        self._before_write()
        dict.clear(self)

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        for key, card in values.items():
            self[key] = card

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __ior__(self, other):
        self.update(other)
        return self

    def __or__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        return self.copy() | dict(other)

    def __ror__(self, other):
        if not isinstance(other, dict):
            return NotImplemented
        return dict(other) | self.copy()


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
