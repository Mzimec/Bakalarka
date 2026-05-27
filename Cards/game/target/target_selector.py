from typing import Any

__all__ = [
    "TargetSelector"
]

class TargetSelector:
    """!
    @brief Base strategy for turning candidates into selectable target groups.
    """

    def select_targets(self, candidates: list) -> list[dict[Any, int]]:
        """!
        @brief Select valid target groups from a candidate list.
        @param candidates Legal candidates discovered by a target spec.
        @return Target groups with repetition counts.
        """
        raise NotImplementedError

class SingleTargetSelector(TargetSelector):
    """!
    @brief Selector that creates one target group for each single candidate.
    """

    def select_targets(self, candidates: list) -> list[dict[Any, int]]:
        """!
        @brief Wrap each candidate as a one-target choice.
        @param candidates Legal target candidates.
        @return One target group per candidate.
        """
        return [{c: 1} for c in candidates]
