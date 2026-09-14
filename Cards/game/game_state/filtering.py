"""Composable filters for runtime card and player selections."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterable, Mapping, Hashable, Set, Callable
from typing import override, TYPE_CHECKING, Any
from immutabledict import immutabledict

from game.game_state import Player

from game.enums import *


class MutableFilterBuffer[VT](ABC):
    """!
    @brief Mutable working set used while composing filter operations.

    Concrete filters mutate this buffer instead of allocating a new result for
    every filtering step.
    """

    @property
    @abstractmethod
    def is_empty(self) -> bool:
        """!
        @brief Return whether the buffer currently contains no values.
        """
        ...

    @abstractmethod
    def retain_all(self, items: Iterable[VT]) -> None:
        """!
        @brief Keep only values also present in `items`.
        """
        ...

    @abstractmethod
    def add_all(self, items: Iterable[VT]) -> None:
        """!
        @brief Add all supplied values to the buffer.
        """
        ...

    @abstractmethod
    def remove_all(self, items: Iterable[VT]) -> None:
        """!
        @brief Remove all supplied values from the buffer.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """!
        @brief Remove every value from the buffer.
        """
        ...


class SetFilterBuffer[VT](MutableFilterBuffer[VT]):
    """!
    @brief Set-backed implementation of the mutable filtering buffer.
    """

    def __init__(self, data: Iterable[VT] | None = None) -> None:
        """!
        @brief Initialize the buffer from an optional iterable.

        @param data Initial values.
        """
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
        """!
        @brief Return an immutable snapshot of the current buffer.

        @return Current values as a `frozenset`.
        """
        return frozenset(self._data)


class Filter[KT: Hashable, VT, IT: Iterable[VT]](ABC):
    """!
    @brief Operation over one indexed filtering group.

    A filter receives a mapping from characteristic values to matching runtime
    objects and mutates the shared candidate buffer accordingly.
    """

    @abstractmethod
    def filter(
        self,
        data: Mapping[KT, IT],
        buffer: MutableFilterBuffer[VT],
    ) -> None:
        """!
        @brief Apply this filter to a candidate buffer.

        @param data Mapping from indexed values to matching objects.
        @param buffer Mutable candidate set being refined.
        """
        ...


@dataclass(frozen=True)
class AnyFilter[KT: Hashable, VT](Filter[KT, VT, Iterable[VT]]):
    """!
    @brief Add objects matching at least one selected index key.

    This filter performs union semantics across `keys`.
    """

    keys: frozenset[KT]

    @override
    def filter(self, data, buffer):
        valid_iterables = (
            data[k]
            for k in self.keys
            if k in data
        )

        for it in valid_iterables:
            buffer.add_all(it)


@dataclass(frozen=True)
class AllFilter[KT: Hashable, VT](Filter[KT, VT, Iterable[VT]]):
    """!
    @brief Retain objects that occur under every selected index key.

    Missing keys make the conjunction impossible and therefore clear the
    candidate buffer immediately.
    """

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
    """!
    @brief Remove objects belonging to index keys that fail a comparison.

    The comparison is evaluated as `compare_func(index_key, value)`.
    """

    value: KT
    compare_func: Callable[[KT, KT], bool]

    @override
    def filter(self, data, buffer):
        invalid_keys = (
            k
            for k in data.keys()
            if not self.compare_func(k, self.value)
        )

        for k in invalid_keys:
            if k in data:
                buffer.remove_all(data[k])


class FilterKey[KT: Hashable]:
    """!
    @brief Typed identity token selecting one filtering dimension.

    Instances are intentionally used as keys rather than strings so separate
    filter dimensions remain distinct even when their value types coincide.
    """
    pass


FT_OWNER = FilterKey[Player]()
FT_CMC = FilterKey[int]()
FT_ZONE = FilterKey[ZoneType]()
FT_CONTROLLER = FilterKey[Player]()
FT_TYPE = FilterKey[CardType]()
FT_SUBTYPE = FilterKey[CardSubtype]()
FT_POWER = FilterKey[int]()
FT_TOUGHNESS = FilterKey[int]()
FT_KEYWORD = FilterKey[str]()


class Filterable[T, IT: Iterable[T]](ABC):
    """!
    @brief Source exposing indexed groups usable by filtering strategies.
    """

    @property
    @abstractmethod
    def filtering_data(
        self,
    ) -> Mapping[FilterKey[Hashable], Mapping[Hashable, IT]]:
        """!
        @brief Return all available filtering groups.
        """
        ...

    @abstractmethod
    def get_filter_group[K: Hashable](
        self,
        key: FilterKey[K],
    ) -> Mapping[K, IT] | None:
        """!
        @brief Return one indexed filtering group.

        @param key Typed filter-group identifier.
        @return Matching key-to-values mapping, or `None` if unavailable.
        """
        ...


class FilteringStrategy[T, OUT, IT: Iterable[T]](ABC):
    """!
    @brief Strategy for applying composed filters to an initial candidate pool.
    """

    @abstractmethod
    def apply(
        self,
        data: Filterable[T, IT],
        pool: Iterable[T],
    ) -> OUT:
        """!
        @brief Apply this strategy to an initial candidate pool.
        """
        ...


@dataclass(frozen=True)
class SetFilteringStrategy[T](
    FilteringStrategy[T, frozenset[T], Iterable[T]]
):
    """!
    @brief Compose indexed filters using a mutable set-backed candidate pool.

    Filters within each group execute in declaration order. Each filter mutates
    the same candidate buffer, allowing union, intersection and subtraction
    operations to be combined without allocating intermediate result sets.
    """

    filters: immutabledict[
        FilterKey[Hashable],
        tuple[Filter[Hashable, T, Iterable[T]], ...],
    ]

    @override
    def apply(self, data, pool):
        """!
        @brief Apply configured filters to the supplied candidate pool.

        Missing filter groups are ignored. Evaluation stops early once the
        candidate buffer becomes empty.

        @param data Object exposing indexed filtering groups.
        @param pool Initial candidate objects.
        @return Immutable set containing the final filtered result.
        """
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