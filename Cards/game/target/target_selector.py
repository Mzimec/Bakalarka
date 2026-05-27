from typing import Any

__all__ = [
    "TargetSelector"
]

class TargetSelector:
    def select_targets(self, candidates: list) -> list[dict[Any, int]]:
        raise NotImplementedError

class SingleTargetSelector(TargetSelector):
    def select_targets(self, candidates: list) -> list[dict[Any, int]]:
        return [{c: 1} for c in candidates]