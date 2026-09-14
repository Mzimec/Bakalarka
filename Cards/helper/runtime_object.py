"""Stable runtime identity and collections keyed by object identifiers."""

from __future__ import annotations
from typing import Protocol, TYPE_CHECKING, Self, override
from abc import abstractmethod, ABC
from dataclasses import dataclass
from collections.abc import Iterable
from immutabledict import immutabledict


class KeyedObject(Protocol):
    """!
    @brief Protocol for objects identified by a stable key.
    """

    @property
    def key(self) -> str: ...


class KeyedCollectionBase[T: KeyedObject](ABC):
    """!
    @brief Construct keyed collections from an iterable of objects.
    """

    @classmethod
    def from_iter(cls, objs: Iterable[T]) -> Self:
        return cls({obj.key: obj for obj in objs})


class KeyedCollection[T: KeyedObject](KeyedCollectionBase[T], dict[str, T]):
    """!
    @brief Mutable collection indexed by each contained object key.
    """

    def pop_last(self) -> T:
        return self.popitem()[1]

    def to_immutable(self) -> ImmutableKeyedCollection[T]:
        """!
        @brief Return an immutable representation of this value.
        """
        return ImmutableKeyedCollection(self)


class ImmutableKeyedCollection[T: KeyedObject](KeyedCollectionBase[T], immutabledict[str, T]):
    """!
    @brief Immutable collection indexed by each contained object key.
    """

    def to_mutable(self) -> KeyedCollection[T]:
        """!
        @brief Return a mutable representation of this value.
        """
        return KeyedCollection(self)


class RuntimeObject(ABC):
    """!
    @brief Base identity shared by cards, effects and other runtime objects.
    """

    def __init__(self) -> None:
        self.runtime_id: int | None = None

    @property
    @abstractmethod
    def key(self) -> str: ...

    def __eq__(self, other: object) -> bool:
        return type(self) == type(other) and self.key == other.key

    def __hash__(self):
        return hash(self.key)
