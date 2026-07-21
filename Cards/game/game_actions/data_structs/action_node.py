from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, override, Self
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Iterator
from immutabledict import immutabledict

if TYPE_CHECKING:
    from ...mana.mana_value import ManaRequirement, ImmutableManaRequirement


from ...enums import *
from helper.dict_helper import MutableSetMapping

__all__ = [
    "EffectToSlotMap",
    "ActionNode",
    "AndActionNode",
    "OrActionNode",
    "EffectActionNode"
]

EffectKey = str
SlotKey = str   
class EffectToSlotMap(MutableSetMapping[EffectKey, SlotKey]):

    def to_immutable(self) -> ImmutableEffectToSlotMap:
        return ImmutableEffectToSlotMap({
            k: frozenset(v) for k, v in self.items()
        })


class ImmutableEffectToSlotMap(immutabledict[EffectKey, frozenset[SlotKey]]):
    
    def to_mutable(self) -> EffectToSlotMap:
        return EffectToSlotMap({
            k: set(v) for k, v in self.items()
        })


@dataclass(frozen=True)
class ActionNodeOption:

    effects: ImmutableEffectToSlotMap
    mana_req: ImmutableManaRequirement


class ActionNode(ABC):
    """!
    @brief Base node for describing which effects an ability can use.
    """

    @abstractmethod
    def generate_options(self) -> Iterator[ActionNodeOption]:
        """!
        @brief Return every possible effect-to-target-slot mapping for this node.
        @return A list of possible mappings from effect keys to target slot keys.
        """
        ...

    @abstractmethod
    def create_with_sufix(self, sufix: str) -> Self: 
        ...


@dataclass(frozen=True)
class AndActionNode(ActionNode):
    """!
    @brief Combines child nodes so all of them must be present in one action option.
    """

    children: tuple[ActionNode, ...]

    def _combine_lazy(
        self,
        child_idx: int,
        effects: EffectToSlotMap,
        mana_req: ManaRequirement
    ) -> Generator[ActionNodeOption, None, None]:
        
        if child_idx >= len(self.children):
            yield ActionNodeOption(effects.to_immutable(), mana_req.to_immutable())
            return
        
        current_child = self.children[child_idx]

        for option in current_child.generate_options():
            added: dict[EffectKey, frozenset[SlotKey]] = {}

            for k, v in option.effects.items():
                if k in effects:
                    diff = v - effects[k]

                    if diff:
                        added[k] = diff

                else:
                    added[k] = v

            effects.mutate(added, set.update)    
            mana_req.add(option.mana_req)

            yield from self._combine_lazy(child_idx + 1, effects, mana_req)

            effects.mutate(added, set.difference_update)
            mana_req.remove(option.mana_req)
    
    @override
    def generate_options(self):
        """!
        @brief Build all combinations of child mappings and merge their slot usage.
        @return All merged effect-to-slot mappings produced by the children.
        """
        seen: set[ActionNodeOption] = set()
        for option in self._combine_lazy(0, EffectToSlotMap(), ManaRequirement()):
            if option in seen:
                continue
            seen.add(option)
            yield option
    
    @override
    def create_with_sufix(self, sufix):
        return AndActionNode(children=tuple([
            child.create_with_sufix(sufix) 
            for child in self.children
        ]))


@dataclass(frozen=True)
class OrActionNode(ActionNode):
    """!
    @brief Represents a choice where any one child action option may be used.
    """

    children: tuple[ActionNode, ...]
    
    @override
    def generate_options(self):
        """!
        @brief Collect all action options produced by the alternative child nodes.
        @return The union of all child effect-to-slot mappings.
        """
        seen: set[ActionNodeOption] = set()
        for child in self.children:
            for option in child.generate_options():
                if option in seen:
                    continue
                seen.add(option)
                yield option

    
    @override
    def create_with_sufix(self, sufix):
        return OrActionNode(children=tuple([
            child.create_with_sufix(sufix) 
            for child in self.children
        ]))


@dataclass(frozen=True)
class EffectActionNode(ActionNode):
    """!
    @brief Leaf node that connects one effect key to the slots it needs.
    """

    effect_map: ImmutableEffectToSlotMap
    
    @override
    def generate_options(self):
        """!
        @brief Return this single effect binding as one possible action option.
        @return A single mapping containing this effect and its target slots.
        """
        yield ActionNodeOption(
                effects=self.effect_map,
                mana_req=ImmutableManaRequirement()
            )
    
    @override
    def create_with_sufix(self, sufix):
        return EffectActionNode(
            effect_map=ImmutableEffectToSlotMap({
                ek: frozenset({sk + sufix for sk in v})
                for ek, v in self.effect_map.items()
            })
        )


@dataclass(frozen=True)
class ManaActionNode(ActionNode):
    """!
    @brief Leaf node that contributes a mana requirement but no effect.
        Generated by ManaCost.to_action_node().
    """

    mana_req: ImmutableManaRequirement

    @override
    def generate_options(self):
        yield ActionNodeOption(
                effects=ImmutableEffectToSlotMap(),
                mana_req=self.mana_req
            )
    
    @override
    def create_with_sufix(self, sufix):
        return self




