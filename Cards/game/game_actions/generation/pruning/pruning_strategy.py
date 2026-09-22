"""Lazy candidate filters and budgets shared by every generation stage."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Iterable
from itertools import islice


class PruningStrategy[T](ABC):
    @abstractmethod
    def prune(self, candidates: Iterable[T]) -> Iterator[T]: ...


class LimitPruning[T](PruningStrategy[T]):
    """Retain the first N witnesses without consuming the remaining space."""

    def __init__(self, limit: int):
        if type(limit) is not int or limit < 0:
            raise ValueError("A pruning limit must be a nonnegative integer.")
        self.limit = limit

    def prune(self, candidates: Iterable[T]) -> Iterator[T]:
        return islice(candidates, self.limit)


class FilterPruning[T](PruningStrategy[T]):
    """Exclude candidates before expanding their options."""

    def __init__(self, predicate: Callable[[T], bool]):
        self.predicate = predicate

    def prune(self, candidates: Iterable[T]) -> Iterator[T]:
        return (candidate for candidate in candidates if self.predicate(candidate))


def apply_pruning[T](candidates: Iterable[T], strategy: PruningStrategy[T] | None) -> Iterable[T]:
    return candidates if strategy is None else strategy.prune(candidates)
