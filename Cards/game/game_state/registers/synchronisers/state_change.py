from __future__ import annotations
from dataclasses import dataclass
from functools import total_ordering
from typing import Self


from ....enums import *
from helper.runtime_object import RuntimeObject
from helper.heap import Heap


@total_ordering
@dataclass(frozen=True)
class ObjectChange[RT: RuntimeObject]:
    
    obj: RT
    priority: int
    
    def __lt__(self, other: ObjectChange):
        return self.priority < other.priority


class SynchronizationQueue(Heap[ObjectChange]):
    pass