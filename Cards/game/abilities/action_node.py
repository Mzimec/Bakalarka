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

    @abstractmethod
    def get_used_effects(self) -> list[EffectToSlotMap]:
        pass

class AndActionNode(ActionNode):
    def __init__(self, children: list[ActionNode]) -> None:
        self.children = children
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
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
    def __init__(self, children: list[ActionNode]) -> None:
        self.children = children
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
        res: list[EffectToSlotMap] = []
        for c in self.children:
            res.extend(c.get_used_effects())
        return res
 
class EffectActionNode(ActionNode):
    def __init__(self, effect_key: EffectKey, slot_keys: set[str]) -> None:
        self.effect_key = effect_key
        self.slot_keys = set(slot_keys)
    
    def get_used_effects(self) -> list[EffectToSlotMap]:
        return [{self.effect_key: self.slot_keys}]
