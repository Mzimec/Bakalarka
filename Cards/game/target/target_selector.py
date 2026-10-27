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

    def generate_completions(self, candidates, prefix=()):
        if prefix:
            raise NotImplementedError("This selector has no reviewed prefix adapter")
        yield from self.generate_target_options(candidates)


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
        yield from self.generate_completions(candidates)

    def generate_completions(self, candidates, prefix=()):
        """Complete a canonical target prefix without enumerating other groups.

        Empty prefixes retain the ordinary size-then-combination enumeration.
        Nondecreasing positions give each group exactly one construction path.
        """
        candidates_list = tuple(candidates)
        prefix = tuple(prefix)
        if len(prefix) > self.max_target_count or any(c not in candidates_list for c in prefix):
            return
        positions = tuple(candidates_list.index(c) for c in prefix)
        if positions != tuple(sorted(positions)) or (not self.is_target_repeatable and len(set(prefix)) != len(prefix)):
            return
        from collections import Counter
        if (prefix or self.min_target_count == 0) and self.is_valid_option(TargetOption(Counter(prefix))):
            yield TargetOption(Counter(prefix))
        start = (positions[-1] + (not self.is_target_repeatable)) if positions else 0
        remaining = candidates_list[start:]
        for count in range(max(1, self.min_target_count, len(prefix) + 1), self.max_target_count + 1):
            if self.is_target_repeatable:
                combos = combinations_with_replacement(remaining, count - len(prefix))
            else:
                combos = combinations(remaining, count - len(prefix))

            for combo in combos:
                # Collapse repeated occurrences into the multiplicity form used
                # by TargetOption.
                target_group: dict[RuntimeObject, int] = {}

                for c in (*prefix, *combo):
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
