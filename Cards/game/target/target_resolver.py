from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass
from immutabledict import immutabledict
from abc import ABC
from collections.abc import Mapping, Iterable, Set, Iterator

if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from .target_spec import TargetSpec
    from .target_selector import TargetSelector 
    from ...helper.runtime_object import RuntimeObject

__all__ = [
    "TargetBinding",
    "TargetConstraint",
    "TargetSlot",
    "TargetResolver"
]

TargetOption = immutabledict[RuntimeObject, int]

class TargetBindingBase(Mapping[str, Mapping[str, TargetOption]], ABC):

    def get_targets_in_slot(self, runtime_key: str, key: str) -> frozenset[RuntimeObject] | None:
        if key not in self:
            return None
        inner = self[key]
        if runtime_key not in inner:
            return None
        return frozenset(inner[runtime_key].keys())
    
    def are_targets_valid(
        self, 
        source: Card, 
        controller: Player,
        state: State,
        slots: Iterable[RepetitionTargetSlotWrapper]
    ) -> bool:
        
        slot_dict = immutabledict({slot.runtime_key: slot for slot in slots})
        for v in self.values():
            for k, to in v.items():
                if k not in slot_dict:
                    return False
                for target in to.keys():
                    if not slot_dict[k].is_target_valid(target, source, controller, state):
                        return False
        return True


class ImmutableTargetBinding(immutabledict[str, immutabledict[str, TargetOption]], TargetBindingBase):

    def to_mutable(self) -> TargetBinding:
        return TargetBinding({k: dict(v) for k, v in self.items()})


class TargetBinding(dict[str, dict[str, TargetOption]], TargetBindingBase):

    def to_immutable(self) -> ImmutableTargetBinding:
        return ImmutableTargetBinding({k: immutabledict(v) for k, v in self.items()})


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


@dataclass(frozen=True)
class RepetitionTargetSlotWrapper:

    runtime_key: str
    slot: TargetSlot     

    def get_reserved_objs(
        self, 
        binding: TargetBindingBase
    ) -> frozenset[RuntimeObject]:
        
        reserved: set[RuntimeObject] = set()
        for distinct in self.slot.distinct_from:
            if distinct in binding:
                for k in binding[distinct].keys():
                    targets: frozenset[RuntimeObject] | None = binding.get_targets_in_slot(k, distinct)
                    if targets:
                        reserved.update(targets)
        
        return frozenset(reserved)
    
    def count_options(
        self, 
        source: Card,
        controller: Player, 
        state: State, 
        binding: TargetBindingBase
    ) -> int:

        reserved = self.get_reserved_objs(binding)
        return self.slot.target_resolver.count_options(source, controller, state, reserved)
    
    def is_target_valid(
        self, 
        target: RuntimeObject, 
        source: Card, 
        controller: Player, 
        state: State
    ) -> bool:
        
        return self.slot.target_resolver.target_spec.is_valid_target(target, source, controller, state)
    

@dataclass(frozen=True)
class TargetResolver:
    """!
    @brief Combines candidate discovery with target selection strategy.
    """

    target_spec: TargetSpec
    target_selector: TargetSelector
    
    
    def generate_target_options(
        self, 
        source: Card,
        controller: Player, 
        state: State, 
        reserved: Set[RuntimeObject] | None = None
    ) -> Iterator[TargetOption]:
        
        """!
        @brief Produce legal target groups for a source in the current state.
        @param source Card that is choosing targets.
        @param state Current game state.
        @return Target groups with repetition counts.
        """

        candidates = self.target_spec.generate_candidates(source, controller, state, reserved)
        yield from self.target_selector.generate_target_options(candidates)
    
    def count_options(
        self, 
        source: Card,
        controller: Player,
        state: State,
        reserved: Set[RuntimeObject] | None = None
    ) -> int:
        
        candidates = self.target_spec.generate_candidates(source, controller, state, reserved)
        return self.target_selector.count_options(candidates)

