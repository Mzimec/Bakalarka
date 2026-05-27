from __future__ import annotations
from typing import TYPE_CHECKING, Any
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..game_state.card import Card
    from ..game_state.state import State
    from .target_spec import TargetSpec
    from .target_selector import TargetSelector 

__all__ = [
    "TargetBinding",
    "TargetConstraint",
    "TargetSlot",
    "TargetResolver"
]


TargetBinding = dict[str, dict[Any, int]]

class TargetConstraint:
    pass

@dataclass(frozen=True)
class TargetSlot:
    key: str 
    target_resolver: TargetResolver
    distinct_from: frozenset[str]
    
    def get_bindings(self, source: Card, state: State) -> list[TargetBinding]:
        groups = self.target_resolver.resolve_targets(source, state)
        return [{self.key: d} for d in groups]

class TargetResolver:
    def __init__(self, target_spec: TargetSpec, target_selector: TargetSelector) -> None:
        self.target_spec = target_spec
        self.target_selector = target_selector
    
    def resolve_targets(self, source: Card, state: State) -> list[dict[Any, int]]:
        candidates = self.target_spec.get_candidates(source, state)
        groups = self.target_selector.select_targets(candidates)
        return groups