from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum, auto
from abc import ABC, abstractmethod
if TYPE_CHECKING:
    from ..game_state import Card, State, Player
    from ..game_actions.game_action import OperationGenerator, GameAction
    from ..game_actions import Effect
    from ..game_actions.event_bus import GameEvent
    from .action_node import ActionNode, EffectToSlotMap
    from ..target import TargetSlot, TargetBinding

from itertools import product
from ..game_actions.game_action import AbilityAction, AbilityOperationGenerator
from helper import *

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
    """!
    @brief Card zones that can restrict where an ability is usable.
    """

    HAND = auto()
    BATTLEFIELD = auto()
    GRAVEYARD = auto()
    EXILE = auto()
    DECK = auto()



@dataclass(frozen=True)
class EffectBinding:
    """!
    @brief Resolved connection between an effect object and the slots it consumes.
    """

    key: str
    effect: Effect
    slots: list[TargetSlot]


@dataclass(frozen=True)
class EffectSequence: 
    sequence: tuple[EffectBinding, ...]

    def get_used_slots(self) -> list[TargetSlot]:
        res: list[TargetSlot] = []
        seen: set[TargetSlot] = set()
        for eb in self.sequence:
            for s in eb.slots:
                if s not in seen:
                    seen.add(s)
                    res.append(s)
        return res
    
    def get_used_effects(self) -> list[Effect]:
        res: list[Effect] = []
        seen: set[Effect] = set()
        for eb in self.sequence:
            if eb.effect not in seen:
                seen.add(eb.effect)
                res.append(eb.effect)
        return res

    
@dataclass(frozen=True)
class SubAbilityDefinition:
    """!
    @brief Defines either the cost part or the effect part of an ability.
    """

    action_node: ActionNode
    slots: dict[str, TargetSlot]
    effects: dict[str, Effect]
    uses_stack: bool = False
    
    def _get_effect_bindings(self) -> list[EffectToSlotMap]:
        """!
        @brief Ask the action tree for all possible effect-to-slot mappings.
        @return Possible mappings from effect keys to target slot keys.
        """
        return self.action_node.get_used_effects()
    
    def _normalize_effect_map(self, map: EffectToSlotMap) -> EffectSequence:
        """!
        @brief Convert effect keys from the action tree into concrete effect objects.
        @param map Mapping from effect keys to target slot keys.
        @return Immutable sequence of concrete effect bindings.
        """
        bindings: list[EffectBinding] = []
        for k, v in map.items():
            if k not in self.effects:
                raise KeyError(k)
            
            slots = []

            for key in v:
                slot = self.slots.get(key)

                if slot is None:
                    raise KeyError(f"  No TargetSlot for '{key}' in self.slots.")

                slots.append(slot)

            bindings.append(EffectBinding(
                key=k,
                effect=self.effects.get(k),
                slots=list(slots)
            ))

        return EffectSequence(sequence=tuple(bindings))

    
    def _get_all_used_slots(self, effects_to_slots: EffectToSlotMap) -> set[TargetSlot]:
        """!
        @brief Collect every target slot referenced by a selected effect mapping.
        @param effects_to_slots Mapping selected for one generated action option.
        @return Target slots required by the selected effects.
        """
        keys: set[str] = set()

        for slot_keys in effects_to_slots.values():
            keys |= slot_keys

        return {self.slots[k] for k in keys}
    
    def _validate(self, binding: TargetBinding) -> bool:
        """!
        @brief Check cross-slot constraints such as distinct target requirements.
        @param binding Concrete target choices keyed by target slot.
        @return True if the binding satisfies all slot constraints.
        """
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
        """!
        @brief Generate every valid merged target binding for the required slots.
        @param source Card that owns or creates the ability.
        @param state Current game state.
        @param t_slots Target slots that must be resolved.
        @return Valid merged target bindings.
        """
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
        """!
        @brief Create operation generators for all legal target/effect combinations.
        @param source Card that owns or creates the ability.
        @param state Current game state.
        @return Operation generators ready to become action intents.
        """
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
    
    def get_effect_sequences(self) -> list[EffectSequence]:
        effects_to_slots = self._get_effect_bindings()
        res = [self._normalize_effect_map(ets) for ets in effects_to_slots]
        if len(res) < 1:
            raise RuntimeError("There must be atleast one EffectSequence in SubAbilityDefinition.")
        return res


@dataclass(frozen=True)
class AbilityDefinition:
    """!
    @brief Static data that describes how a card ability can be used.
    """

    key: str
    cost_action: SubAbilityDefinition | None
    action: SubAbilityDefinition | None
    allowed_zones: set[ZoneType] = field(default_factory=lambda: {ZoneType.BATTLEFIELD})
    uses_stack: bool = True
    
    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        """!
        @brief Return whether the ability is available from the given card zone.
        @param zone Zone to check.
        @return True if the ability is available in the zone.
        """
        return zone in self.allowed_zones

    def to_ability(self, card: Card, player: Player) -> Ability:
        return Ability(source=card, controller=player, data=self)



@dataclass(frozen=True)
class Ability:
    """!
    @brief Runtime ability bound to a specific source card.
    """

    source: Card
    controller: Player
    data: AbilityDefinition
    
    def _generate_game_action(self, c_generator: OperationGenerator, a_generator: OperationGenerator) -> GameAction:
        """!
        @brief Wrap chosen cost and effect generators into an executable game action.
        @param c_generator Generator for the cost operations.
        @param a_generator Generator for the ability effect operations.
        @return Game action representing one concrete ability choice.
        """
        return AbilityAction(
            action_key=self.data.key, 
            source=self.source, 
            cost_generator=c_generator,
            action_generator=a_generator,
            uses_stack=self.data.uses_stack
        )
    
    def generate_actions(self, state: State) -> list[AbilityAction]:
        """!
        @brief Generate all concrete game actions available for this ability.
        @param state Current game state.
        @return All legal action choices produced by this ability.
        """
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
    
    def is_activatable(self) -> bool:
        return self.data.is_usable_in_zone(self.source.get_zone())


class TriggerCondition(ABC):
    """!
    @brief Base predicate used to decide whether an event should trigger an ability.
    """

    @abstractmethod
    def matches(self, state: State, event: GameEvent) -> bool:
        """!
        @brief Return true when the event satisfies this trigger condition.
        @param state Current game state.
        @param event Event being tested.
        @return True if this condition is satisfied.
        """
        pass

@dataclass(frozen=True)
class TriggeredAbilityDefinition:
    """!
    @brief Static data for an ability that is produced by a matching game event.
    """

    key: str
    cost_action: SubAbilityDefinition | None
    action: SubAbilityDefinition | None
    condition: TriggerCondition
    allowed_zones: set[ZoneType] = field(default_factory=lambda: {ZoneType.BATTLEFIELD})
    uses_stack: bool = True

    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        """!
        @brief Return whether this triggered ability can trigger from the given zone.
        @param zone Zone to check.
        @return True if the triggered ability is active in the zone.
        """
        return zone in self.allowed_zones



@dataclass(frozen=True)
class TriggeredAbility(Ability):
    """!
    @brief Runtime triggered ability paired with the event that caused it.
    """

    data: TriggeredAbilityDefinition
    event: GameEvent | None = None
    
    def matches(self, state: State) -> bool:
        """!
        @brief Evaluate the stored trigger event against the ability condition.
        @param state Current game state.
        @return True if the stored event matches the trigger condition.
        """
        if self.event is None:
            return False
        return self.data.condition.matches(state, self.event)
    

class AbilityCollection(KeyedCollection[AbilityDefinition]):
    pass


    

 
