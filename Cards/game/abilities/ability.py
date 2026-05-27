from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
if TYPE_CHECKING:
    from ..game_state import Card, State
    from ..game_actions.game_action import OperationGenerator, GameAction
    from ..game_actions import Effect
    from ..game_actions.event_bus import GameEvent
    from .action_node import ActionNode, EffectToSlotMap
    from ..target import TargetSlot, TargetBinding

from itertools import product
from ..game_actions.game_action import AbilityAction, AbilityOperationGenerator

__all__ = [
    "ZoneType",
    "EffectBinding",
    "EffectSequence",
    "SubAbilityDefinition",
    "AbilityDefinition",
    "Ability",
    "TriggerCondition",
    "TriggeredAbilityDefinition",
    "TriggeredAbility",
]


class ZoneType(Enum):

    HAND = 1
    BATTLEFIELD = 2
    GRAVEYARD = 3
    EXILE = 4


@dataclass(frozen=True)
class EffectBinding:

    key: str
    effect: Effect
    slots: frozenset[str]


EffectSequence = tuple[EffectBinding, ...]

    
@dataclass(frozen=True)
class SubAbilityDefinition:
    action_node: ActionNode
    slots: dict[str, TargetSlot]
    effects: dict[str, Effect]
    uses_stack: bool = False
    
    def _get_effect_bindings(self) -> list[EffectToSlotMap]:
        return self.action_node.get_used_effects()
    
    def _normalize_effect_map(self, map: EffectToSlotMap) -> EffectSequence:
        bindings: list[EffectBinding] = []
        for k, v in map.items():
            if k not in self.effects:
                raise KeyError(k)
            bindings.append(EffectBinding(
                key=k,
                effect=self.effects.get(k),
                slots=frozenset(v)
            ))
        return tuple(bindings)

    
    def _get_all_used_slots(self, effects_to_slots: EffectToSlotMap) -> set[TargetSlot]:
        keys: set[str] = set()

        for slot_keys in effects_to_slots.values():
            keys |= slot_keys

        return {self.slots[k] for k in keys}
    
    def _validate(self, binding: TargetBinding) -> bool:
        for slot_key, targets in binding.items():
            slot = self.slots[slot_key]

            current_targets = set(targets.keys())

            for distinct_slot_key in slot.distinct_from:
                other_targets = binding.get(distinct_slot_key)

                if other_targets is None:
                    continue

                if current_targets & set(other_targets.keys()):
                    return False

        return True
    
    def _get_target_bindings(self, source: Card, state: State, t_slots: set[TargetSlot]) -> list[TargetBinding]:
        all_options = [ts.get_bindings(source, state) for ts in t_slots]
        res: list[TargetBinding] = []

        for combination in product(*all_options):
            merged_binding: TargetBinding = dict()
            for tb in combination:
                for k, v in tb.items():
                    if k not in merged_binding: merged_binding[k] = v
                    else: raise RuntimeError(f"Key: {k} should be unique across the TargetSlot sets!")
            
            if self._validate(merged_binding): res.append(merged_binding)
        
        return res
    
    def generate_actions(self, source: Card, state: State) -> list[OperationGenerator]:
        res: list[AbilityAction] = []
        effects_to_slots = self._get_effect_bindings()

        for ets in effects_to_slots:
            used_slots = self._get_all_used_slots(ets)
            target_bindings = self._get_target_bindings(source, state, used_slots)
            effect_bindings = self._normalize_effect_map(ets)


            for binding in target_bindings: 
                res.append(AbilityOperationGenerator(
                    effects=effect_bindings, 
                    binding=binding
                ))
        
        return res


@dataclass(frozen=True)
class AbilityDefinition:

    key: str
    cost_action: SubAbilityDefinition | None
    action: SubAbilityDefinition | None
    allowed_zones: set[ZoneType] = field(default_factory=lambda: {ZoneType.BATTLEFIELD})
    uses_stack: bool = True
    
    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        return zone in self.allowed_zones


@dataclass(frozen=True)
class Ability:
    source: Card
    data: AbilityDefinition
    
    def _generate_game_action(self, c_generator: OperationGenerator, a_generator: OperationGenerator) -> GameAction:
        return AbilityAction(
            action_key=self.data.key, 
            source=self.source, 
            cost_generator=c_generator,
            action_generator=a_generator,
            uses_stack=self.data.uses_stack
        )
    
    def generate_actions(self, state: State) -> list[AbilityAction]:
        cost_prototypes = (
            self.data.cost_action.generate_actions(self.source, state)
            if self.data.cost_action is not None
            else [AbilityOperationGenerator(effects=tuple(), binding={})]
        )
        action_prototypes = (
            self.data.action.generate_actions(self.source, state)
            if self.data.action is not None
            else [AbilityOperationGenerator(effects=tuple(), binding={})]
        )
        res: list[AbilityAction] = []
        
        for cp in cost_prototypes:
            for ap in action_prototypes:
                res.append(self._generate_game_action(cp, ap))

        return res


class TriggerCondition(ABC):

    @abstractmethod
    def matches(self, state: State, event: GameEvent) -> bool:
        pass

@dataclass(frozen=True)
class TriggeredAbilityDefinition:
    key: str
    cost_action: SubAbilityDefinition | None
    action: SubAbilityDefinition | None
    condition: TriggerCondition
    allowed_zones: set[ZoneType] = field(default_factory=lambda: {ZoneType.BATTLEFIELD})
    uses_stack: bool = True

    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        return zone in self.allowed_zones


@dataclass(frozen=True)
class TriggeredAbility(Ability):
    data: TriggeredAbilityDefinition
    event: GameEvent | None = None
    
    def matches(self, state: State) -> bool:
        if self.event is None:
            return False
        return self.data.condition.matches(state, self.event)

    

 
