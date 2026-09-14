"""Selection policies over legal target candidates."""

from __future__ import annotations
from typing import TYPE_CHECKING, override
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement
from collections.abc import Iterable, Iterator

if TYPE_CHECKING:
    from helper.runtime_object import RuntimeObject

from .target_resolver import TargetOption

__all__ = ["TargetSelector", "MinMaxTegetSelector", "SingleTargetSelector"]


class TargetSelector(ABC):
    """!
    @brief Base strategy for turning candidates into selectable target groups.
    """

    @abstractmethod
    def generate_target_options(
        self, candidates: Iterable[RuntimeObject]
    ) -> Iterator[TargetOption]:
        """!
        @brief Generate every legal target group from the supplied candidates.

        @param candidates Legal individual targets discovered by a target spec.
        @return Iterator of target groups with repetition counts.
        """
        ...

    @abstractmethod
    def count_options(self, candidates: Iterable[RuntimeObject]) -> int:
        """!
        @brief Count selectable target groups for the supplied candidates.
        """
        ...

    def is_valid_option(self, option: TargetOption) -> bool:
        """!
        @brief Validate a supplied target group without enumerating alternatives.

        @param option Target group with multiplicities.
        @return Whether the group satisfies this selector.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class MinMaxTegetSelector(TargetSelector):
    """!
    @brief Select between a minimum and maximum number of targets.

    Targets may optionally be selected repeatedly. Repeated selections are
    represented by multiplicities in the resulting `TargetOption`.
    """

    min_target_count: int
    max_target_count: int
    is_target_repeatable: bool

    def is_valid_option(self, option):
        """!
        @brief Validate target count, multiplicities, and repetition policy.
        """
        if any(type(count) is not int or count < 1 for count in option.values()):
            return False

        count = sum(option.values())

        return self.min_target_count <= count <= self.max_target_count and (
            self.is_target_repeatable or all(n == 1 for n in option.values())
        )

    @override
    def generate_target_options(self, candidates) -> Iterator[TargetOption]:
        """!
        @brief Enumerate every legal target group.

        Non-repeatable targets use ordinary combinations while repeatable
        targets use combinations with replacement.

        @param candidates Legal individual target objects.
        @return Iterator of immutable target groups.
        """
        candidates_list: tuple[RuntimeObject] = tuple(candidates)

        if self.min_target_count == 0:
            yield TargetOption()

        for count in range(max(1, self.min_target_count), self.max_target_count + 1):
            if self.is_target_repeatable:
                combos = combinations_with_replacement(candidates_list, count)
            else:
                combos = combinations(candidates_list, count)

            for combo in combos:
                # Collapse repeated occurrences into the multiplicity form used
                # by TargetOption.
                target_group: dict[RuntimeObject, int] = {}

                for c in combo:
                    target_group[c] = target_group.get(c, 0) + 1

                yield TargetOption(target_group)

    def count_options(self, candidates):
        """!
        @brief Count selectable target groups without constructing them.

        @param candidates Legal individual target objects.
        @return Number of target groups considered by this selector.
        """
        from math import comb

        n = sum(1 for _ in candidates)
        total = 0

        for count in range(max(1, self.min_target_count), self.max_target_count + 1):
            if count <= n:
                total += comb(n, count)

        return total


class SingleTargetSelector(MinMaxTegetSelector):
    """!
    @brief Convenience selector for exactly one non-repeatable target.
    """

    def __init__(self) -> None:
        super().__init__(min_target_count=1, max_target_count=1, is_target_repeatable=False)