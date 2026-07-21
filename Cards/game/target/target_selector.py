from __future__ import annotations
from typing import TYPE_CHECKING, override
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement
from collections.abc import Iterable, Iterator

if TYPE_CHECKING:
    from ...helper.runtime_object import RuntimeObject

from .target_resolver import TargetOption

__all__ = [
    "TargetSelector"
]

class TargetSelector(ABC):
    """!
    @brief Base strategy for turning candidates into selectable target groups.
    """

    @abstractmethod
    def generate_target_options(self, candidates: Iterable[RuntimeObject]) -> Iterator[TargetOption]:
        """!
        @brief Select valid target groups from a candidate list.
        @param candidates Legal candidates discovered by a target spec.
        @return Target groups with repetition counts.
        """
        ...
    
    @abstractmethod
    def count_options(self, candidates: Iterable[RuntimeObject]) -> int:
        ...


@dataclass(frozen=True)
class MinMaxTegetSelector(TargetSelector):

    min_target_count: int
    max_target_count: int
    is_target_repeatable: bool

    @override
    def generate_target_options(self, candidates) -> Iterator[TargetOption]:

        candidates_list: tuple[RuntimeObject] = tuple(candidates)

        if self.min_target_count == 0:
            yield TargetOption()

        for count in range(max(1, self.min_target_count), self.max_target_count + 1):
            if self.is_target_repeatable:
                combos = combinations_with_replacement(candidates_list, count)
            else:
                combos = combinations(candidates_list, count)
                
            for combo in combos:
                target_group: dict[RuntimeObject, int] = {}
                for c in combo:
                    target_group[c] = target_group.get(c, 0) + 1

                yield TargetOption(target_group)
                
    
    def count_options(self, candidates):
        from math import comb

        n = sum(1 for _ in candidates)
        total = 0

        for count in range(max(1, self.min_target_count), self.max_target_count + 1):
            if count <= n:
                total += comb(n, count)
        
        return total

