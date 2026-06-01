from typing import Protocol, Generic, TypeVar
from abc import abstractmethod
from __future__ import annotations

class HasKey(Protocol):
    key: str

    def to_string(self) -> str:
        pass

T = TypeVar("T", bound=HasKey)

class KeyedCollection(Generic[T]):

    def __init__(self, values: list[T] | None = None) -> None:
        self._data: dict[str, T] = {}

        if values:
            self.add_many(values)
    
    @classmethod
    def from_dict(cls, data: dict[str, T]) -> KeyedCollection[T]:
        obj = cls([])
        obj._data = dict(data)
        return obj

    def __getitem__(self, key: str) -> T:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)
    
    def add(self, item: T) -> None:
        self._data[item.key] = item

    def add_many(self, items: list[T]) -> None:
        for item in items:
            self.add(item)

    def remove(self, key: str) -> T:
        return self._data.pop(key)
    
    def pop(self) -> T:
        return self._data.popitem()[1]

    def get(self, key: str, default: T | None = None) -> T | None:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()
    
    def to_string(self, in_detail = False) -> str:
        return "\n" + "\n".join(
            v.to_string(in_detail) for v in self.values()
        )


class IsAttackable(Protocol):
    is_attackable: bool


