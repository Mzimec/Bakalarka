from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterable, Mapping, Hashable, Set, Callable
from typing import override, TYPE_CHECKING, Any
from immutabledict import immutabledict

if TYPE_CHECKING:
    from .game_state import Player
    from .game_state.card_register import KeyWord

from .enums import *


class MutableFilterBuffer[VT](ABC):

    @property
    @abstractmethod
    def is_empty(self) -> bool: ...

    @abstractmethod
    def retain_all(self, items: Iterable[VT]) -> None: ...

    @abstractmethod
    def add_all(self, items: Iterable[VT]) -> None: ...

    @abstractmethod
    def remove_all(self, items: Iterable[VT]) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class SetFilterBuffer[VT](MutableFilterBuffer[VT]):

    def __init__(self, data: Iterable[VT] | None = None) -> None:
        self._data: set[VT] = set(data) if data else set()
    
    @property
    @override
    def is_empty(self):
        return not self._data
    
    @override
    def retain_all(self, items):
        self._data.intersection_update(items)
    
    @override
    def add_all(self, items):
        self._data.update(items)
    
    @override
    def remove_all(self, items):
        self._data.difference_update(items)

    @override
    def clear(self):
        self._data.clear()
    
    def to_immutable(self) -> frozenset[VT]:
        return frozenset(self._data)


class Filter[KT: Hashable, VT, IT: Iterable[VT]](ABC):

    @abstractmethod
    def filter(self, data: Mapping[KT, IT], buffer: MutableFilterBuffer[VT]) -> None: ...


@dataclass(frozen=True)
class AnyFilter[KT : Hashable, VT](Filter[KT, VT, Iterable[VT]]):

    keys: frozenset[KT]

    @override
    def filter(self, data, buffer):
        valid_iterables = (data[k] for k in self.keys if k in data)
        for it in valid_iterables:
            buffer.add_all(it)


@dataclass(frozen=True)
class AllFilter[KT: Hashable, VT](Filter[KT, VT, Iterable[VT]]):

    keys: frozenset[KT]

    @override
    def filter(self, data, buffer):
        for k in self.keys:
            if k in data:
                buffer.retain_all(data[k])
            else:
                buffer.clear()
                return
    


@dataclass(frozen=True)
class ComparableFilter[KT: Hashable, VT](Filter[KT, VT, Iterable[VT]]):

    value: KT
    compare_func: Callable[[KT, KT], bool]

    @override
    def filter(self, data, buffer):
        invalid_keys = (k for k in data.keys() if not self.compare_func(k, self.value))
        for k in invalid_keys:
            if k in data:
                buffer.remove_all(data[k])


class FilterKey[KT: Hashable]:
    pass

FT_OWNER = FilterKey[Player]()
FT_CMC = FilterKey[int]()
FT_ZONE = FilterKey[ZoneType]()
FT_CONTROLLER = FilterKey[Player]()
FT_TYPE = FilterKey[CardType]()
FT_SUBTYPE = FilterKey[CardSubtype]()
FT_POWER = FilterKey[int]()
FT_TOUGHNESS = FilterKey[int]()
FT_KEYWORD = FilterKey[KeyWord]()


class Filterable[T, IT: Iterable[T]](ABC):

    @property
    @abstractmethod
    def filtering_data(self) -> Mapping[FilterKey[Hashable], Mapping[Hashable, IT]]: ...

    @abstractmethod
    def get_filter_group[K: Hashable](self, key: FilterKey[K]) -> Mapping[K, IT] | None: ...


class FilteringStrategy[T, OUT, IT: Iterable[T]](ABC):

    @abstractmethod
    def apply(self, data: Filterable[T, IT], pool: Iterable[T]) -> OUT: ...
        

@dataclass(frozen=True)
class SetFilteringStartegy[T](FilteringStrategy[T, frozenset[T], Iterable[T]]):

    filters: immutabledict[FilterKey[Hashable], tuple[Filter[Hashable, T, Iterable[T]], ...]]

    @override
    def apply(self, data, pool):
        buffer: SetFilterBuffer[T] = SetFilterBuffer(pool)

        if buffer.is_empty:
            return frozenset()
        
        for k, v in self.filters.items():
            group = data.get_filter_group(k)
            if not group:
                continue

            for f in v:
                f.filter(group, buffer)
                if buffer.is_empty:
                    return frozenset()

        return buffer.to_immutable()