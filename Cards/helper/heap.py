from __future__ import annotations
from typing import Protocol, Self
from collections.abc import Iterable
import heapq


class Comparable(Protocol):

    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __ge__(self, other: Self) -> bool: ...
    def __le__(self, other: Self) -> bool: ...
    def __gt__(self, other: Self) -> bool: ...
    def __lt__(self, other: Self) -> bool: ...


class Heap[CT: Comparable]:

    def __init__(self, data: Iterable[CT] | None = None) -> None:
        self._data: list[CT] = [] if data is None else list(data)
        if self._data:
            heapq.heapify(self._data)

    def push(self, obj: CT) -> None:
        heapq.heappush(self._data, obj)
    
    def pop(self) -> CT:
        if not self._data:
            raise IndexError("  Tried to pop from empty heap.")
        return heapq.heappop(self._data)
    
    def peek(self) -> CT:
        if not self._data:
            raise IndexError("  Tried to peek into empty heap.")
        return self._data[0]
    
    def remove(self, obj: CT) -> None:
        for i, t in enumerate(self._data):
            if t.key == obj:
                del self._data[i]
                return
            
    def __len__(self) -> int:
        return len(self._data)