from __future__ import annotations
from itertools import product
from dataclasses import dataclass
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from ..game_actions import Effect
    from ..target import TargetSlot

__all__ = [
    "EffectToSlotMap",
    "ActionNode",
    "AndActionNode",
    "OrActionNode",
    "EffectActionNode"
]

EffectKey = str
SlotKey = str   
EffectToSlotMap = dict[EffectKey, set[SlotKey]]


class ActionNode(ABC):
    """!
    @brief Base node for describing which effects an ability can use.
    """

    @abstractmethod
    def get_used_effects(self) -> list[EffectToSlotMap]:
        """!
        @brief Return every possible effect-to-target-slot mapping for this node.
        @return A list of possible mappings from effect keys to target slot keys.
        """
        pass

class AndActionNode(ActionNode):
    """!
    @brief Combines child nodes so all of them must be present in one action option.
    """

    def __init__(self, children: list[ActionNode]) -> None:
        """!
        @brief Store the nodes that must be combined together.
        @param children Action nodes that are all required.
        """
        self.children = children
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
        """!
        @brief Build all combinations of child mappings and merge their slot usage.
        @return All merged effect-to-slot mappings produced by the children.
        """
        all_options = [c.get_used_effects() for c in self.children]

        res: list[EffectToSlotMap] = []

        for combination in product(*all_options):
            merged_ao: EffectToSlotMap = dict()
            for ao in combination:
                for k, v in ao.items():
                    if k in merged_ao: merged_ao[k] = merged_ao[k].union(v)
                    else: merged_ao[k] = v
            res.append(merged_ao)
        
        return res
               
class OrActionNode(ActionNode):
    """!
    @brief Represents a choice where any one child action option may be used.
    """

    def __init__(self, children: list[ActionNode]) -> None:
        """!
        @brief Store the alternative nodes.
        @param children Action nodes that represent available alternatives.
        """
        self.children = children
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
        """!
        @brief Collect all action options produced by the alternative child nodes.
        @return The union of all child effect-to-slot mappings.
        """
        res: list[EffectToSlotMap] = []
        for c in self.children:
            res.extend(c.get_used_effects())
        return res
 
class EffectActionNode(ActionNode):
    """!
    @brief Leaf node that connects one effect key to the slots it needs.
    """

    def __init__(self, effect_key: EffectKey, slot_keys: set[str]) -> None:
        """!
        @brief Create a leaf for one effect and the target slots scoped to it.
        @param effect_key Identifier of the effect used by this node.
        @param slot_keys Target slot keys consumed by the effect.
        """
        self.effect_key = effect_key
        self.slot_keys = set(slot_keys)
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
        """!
        @brief Return this single effect binding as one possible action option.
        @return A single mapping containing this effect and its target slots.
        """
        return [{self.effect_key: self.slot_keys}]
