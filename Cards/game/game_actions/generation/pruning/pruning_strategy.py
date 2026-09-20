from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Iterable

class PruningStrategy(ABC):

    @abstractmethod
    def prune[T](
        self, 
        prune_from: Iterable[T],
    ) -> Iterator[T]: ...

        
        




class LimitPruning(PruningStrategy):
    """Retain the first N witnesses without materializing the remaining space."""

    def __init__(self, limit: int):
        if type(limit) is not int or limit < 0:
            raise ValueError("A pruning limit must be a nonnegative integer.")
        self.limit = limit

    def prune(self, prune_from):
        from itertools import islice
        return islice(prune_from, self.limit)
