"""Lazy candidate filters and budgets shared by every generation stage."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Iterable
from itertools import islice


class PruningStrategy[T](ABC):
    """!
    @brief Base strategy for lazily pruning generated candidates.
    """
    @abstractmethod
    def prune(self, candidates: Iterable[T]) -> Iterator[T]:
        """!
        @brief Yield candidates retained by this pruning strategy.
        @param candidates Candidate stream to prune.
        @return Iterator of retained candidates.
        """
        ...


class LimitPruning[T](PruningStrategy[T]):
    """!
    @brief Retain the first N witnesses without consuming the remaining space.
    """

    def __init__(self, limit: int) -> None:
        """!
        @brief Store the maximum number of candidates to keep.
        @param limit Nonnegative candidate limit.
        @throws ValueError If `limit` is not a nonnegative integer.
        """
        if type(limit) is not int or limit < 0:
            raise ValueError("A pruning limit must be a nonnegative integer.")
        self.limit: int = limit

    def prune(self, candidates: Iterable[T]) -> Iterator[T]:
        """!
        @brief Yield at most the configured number of candidates.
        @param candidates Candidate stream to limit.
        @return Iterator over the retained prefix.
        """
        return islice(candidates, self.limit)


class FilterPruning[T](PruningStrategy[T]):
    """!
    @brief Exclude candidates before expanding their options.
    """

    def __init__(self, predicate: Callable[[T], bool]) -> None:
        """!
        @brief Store the predicate used to filter candidates.
        @param predicate Function returning `True` for retained candidates.
        """
        self.predicate: Callable[[T], bool] = predicate

    def prune(self, candidates: Iterable[T]) -> Iterator[T]:
        """!
        @brief Yield candidates accepted by the predicate.
        @param candidates Candidate stream to filter.
        @return Iterator over accepted candidates.
        """
        return (candidate for candidate in candidates if self.predicate(candidate))


def apply_pruning[T](candidates: Iterable[T], strategy: PruningStrategy[T] | None) -> Iterable[T]:
    """!
    @brief Apply an optional pruning strategy to a candidate stream.
    @param candidates Candidate stream.
    @param strategy Optional pruning strategy.
    @return Original candidates when no strategy is provided, otherwise pruned candidates.
    """
    return candidates if strategy is None else strategy.prune(candidates)
