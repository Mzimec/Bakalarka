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
    """!
    @brief Placeholder base for future target validation constraints.
    """

    pass

@dataclass(frozen=True)
class TargetSlot:
    """!
    @brief Named target requirement used by effects in an ability.
    """

    key: str 
    target_resolver: TargetResolver
    distinct_from: frozenset[str]
    
    def get_bindings(self, source: Card, state: State) -> list[TargetBinding]:
        """!
        @brief Resolve this slot into target bindings keyed by slot name.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Target bindings available for this slot.
        """
        groups = self.target_resolver.resolve_targets(source, state)
        return [{self.key: d} for d in groups]

class TargetResolver:
    """!
    @brief Combines candidate discovery with target selection strategy.
    """

    def __init__(self, target_spec: TargetSpec, target_selector: TargetSelector) -> None:
        """!
        @brief Create a resolver from a candidate spec and selector.
        @param target_spec Object that finds legal target candidates.
        @param target_selector Object that groups candidates into target choices.
        """
        self.target_spec = target_spec
        self.target_selector = target_selector
    
    def resolve_targets(self, source: Card, state: State) -> list[dict[Any, int]]:
        """!
        @brief Produce legal target groups for a source in the current state.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Target groups with repetition counts.
        """
        candidates = self.target_spec.get_candidates(source, state)
        groups = self.target_selector.select_targets(candidates)
        return groups
