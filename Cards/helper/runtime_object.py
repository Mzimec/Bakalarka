from __future__ import annotations
from typing import Protocol, TYPE_CHECKING, Self, override
from abc import abstractmethod, ABC
from dataclasses import dataclass
from collections.abc import Iterable
from immutabledict import immutabledict




class KeyedObject(Protocol):
    
    @property
    def key(self) -> str: ...


class KeyedCollectionBase[T: KeyedObject](ABC):

    @classmethod
    def from_iter(cls, objs: Iterable[T]) -> Self:
        return cls({obj.key: obj for obj in objs})


class KeyedCollection[T : KeyedObject](KeyedCollectionBase[T], dict[str, T]):
    
    def pop_last(self) -> T:
        return self.popitem()[1]

    def to_immutable(self) -> ImmutableKeyedCollection[T]:
        return ImmutableKeyedCollection(self)


class ImmutableKeyedCollection[T: KeyedObject](KeyedCollectionBase[T], immutabledict[str, T]):
    
    def to_mutable(self) -> KeyedCollection[T]:
        return KeyedCollection(self)
    


class RuntimeObject(ABC):

    def __init__(self) -> None:
        self.runtime_id: int | None = None

    @property
    @abstractmethod
    def key(self) -> str: ...

    def __eq__(self, other: object) -> bool:
        return (
            type(self) == type(other)
            and self.key == other.key
        )
    
    def __hash__(self):
        return hash(self.key)
    

