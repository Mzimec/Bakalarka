from __future__ import annotations
from typing import TYPE_CHECKING, override, overload, Any
from functools import cached_property
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from immutabledict import immutabledict
from types import MappingProxyType
import copy

if TYPE_CHECKING:
    from ...game_state import Card, State, Player
    from .. import Effect
    from ..resolution.event_bus import GameEvent
    from .action_node import (
        ActionNode,  
        AndActionNode, 
    )
    from ...target import TargetSlot
    from ...target.target_resolver import ImmutableTargetBinding
    from ...mana.mana_value import ImmutableManaValue, ManaValue, ManaValueBase
    from ...ai.mana_solver import ManaSolver

from ...game_state.modifier import ModifierSource, ContinuouosEffectModifierSource
from .game_action import AbilityAction, ExecutionPlan, GameAction
from ...game_state.stat import HasModifiableStats, ModifiableReferenceStat, ModifiablePrimitiveStat, Stat
from ...stat_type import *
from ....helper.runtime_object import KeyedCollection
from ...enums import *
from ...target.target_resolver import RepetitionTargetSlotWrapper

__all__ = [
    "ZoneType",
    "EffectBinding",
    "EffectSequence",
    "SubAbilityDefinition",
    "AbilityDefinition",
    "Ability",
    "TriggerCondition",
    "TriggerAbilityDefinition",
    "TriggerAbility",
]



@dataclass(frozen=True)
class EffectBinding:
    """!
    @brief Resolved connection between an effect object and the slots it consumes.
    """

    effect: Effect
    slots: frozenset[RepetitionTargetSlotWrapper]


@dataclass(frozen=True)
class EffectSequence: 
    sequence: tuple[EffectBinding, ...]

    def get_used_slots(self) -> frozenset[RepetitionTargetSlotWrapper]:
        res: set[RepetitionTargetSlotWrapper] = set()
        for eb in self.sequence:
            for s in eb.slots:
                    res.add(s)
        return frozenset(res)
    
    def get_used_effects(self) -> frozenset[Effect]:
        return frozenset({eb.effect for eb in self.sequence})


@dataclass(frozen=True)
class SubAbilityVariable:

    var_type: SAVariableType
    cost_subdef: SubAbilityDefinition
    action_subdef: SubAbilityDefinition
    min_value: int = 0
    max_cap: int | None = None

    def get_max_value(self, state: State, subability: RuntimeSubAbility) -> int:
        raise NotImplementedError() # TODO


@dataclass(frozen=True)
class SubAbilityDefinition:
    """!
    @brief Defines either the cost part or the effect part of an ability.
    """

    action_node: ActionNode | None = None
    mana_cost: ModifiableReferenceStat[ManaValue, ImmutableManaValue] | None = None
    slots: frozenset[TargetSlot] = field(default_factory=frozenset)
    effects: frozenset[Effect] = field(default_factory=frozenset)
    resolution_speed: ResolutionSpeed  | None = None


class SubAbilityComposer:
    
    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, subability: SubAbilityComposer) -> None: ...
    @overload
    def __init__(self, subdefs: Iterable[SubAbilityDefinition]) -> None: ...
    def __init__(
        self, 
        first: SubAbilityComposer | Iterable[SubAbilityDefinition] | None = None
    ) -> None:
        if isinstance(first, SubAbilityComposer):
            self._subdefs: dict[SubAbilityDefinition, int] = dict(first._subdefs)
            self._resolution_speed: ResolutionSpeed | None = first._resolution_speed
            self._speed_count: int = first._speed_count
        
        else:
            self._subdefs = {}
            self._resolution_speed = None
            self._speed_count = 0
            if first:
                self.extend_subdefs(first)
        
        self._subdefs_proxy: MappingProxyType[SubAbilityDefinition, int] = MappingProxyType(self._subdefs)
    
    def extend_subdefs(self, subdefs: Iterable[SubAbilityDefinition]) -> None:

        for subdef in subdefs:
            if self._resolution_speed is not None and self._resolution_speed != subdef.resolution_speed:
                raise ValueError("  SubAbilityDefinitions with different resolution speeds cannot be combined.")
            
            if subdef.resolution_speed:
                self._speed_count += 1
                if not self._resolution_speed:
                    self._resolution_speed = subdef.resolution_speed

            self._subdefs[subdef] = self._subdefs.get(subdef, 0) + 1

    def remove_subdefs(self, subdefs: Iterable[SubAbilityDefinition]) -> None:

        for subdef in subdefs:
            if subdef not in self._subdefs:
                continue

            if subdef.resolution_speed:
                self._speed_count -= 1

            self._subdefs[subdef] -= 1
            
            if self._subdefs[subdef] <= 0:
                del self._subdefs[subdef]

        if self._speed_count <= 0:
            self._resolution_speed = None
            self._speed_count = 0

    def _compile_mana_cost(self, ability: Ability, state: State) -> ImmutableManaValue | None:
        accumulated_mana: ManaValue | None = None
        for k, v in self._subdefs.items():
            smc: ImmutableManaValue | None = None
            if k.mana_cost:
                smc = ability.get_stat(k.mana_cost.stat_type, state)
                    

                if not smc:
                    continue

            for _ in range(v):
                if accumulated_mana is None:
                    accumulated_mana = ManaValue()
                accumulated_mana.add(smc)
            
        return ImmutableManaValue(accumulated_mana) if accumulated_mana is not None else None
    
    def _compile_action_graph(self) -> tuple[immutabledict[str, RepetitionTargetSlotWrapper], immutabledict[str, Effect], ActionNode | None]:
        slots: dict[str, RepetitionTargetSlotWrapper] = {}
        effects: dict[str, Effect] = {}
        children: list[ActionNode] = []

        i: int = 0
        for subdef, count in self._subdefs.items():
            if not subdef.action_node:
                continue

            for effect in subdef.effects:
                effects[effect.key] = effect

            for _ in range(count):
                sufix: str = f"_{i}"
                i += 1
                children.append(subdef.action_node.create_with_sufix(sufix))

                for slot in subdef.slots:
                    slots[slot.key + sufix] = RepetitionTargetSlotWrapper(sufix, slot)

        action_node: ActionNode | None
        if not children:
            action_node = None
        elif len(children) == 1:
            action_node = children[0]
        else:
            action_node = AndActionNode(children)

        return immutabledict(slots), immutabledict(effects), action_node

    def compile(self, ability: Ability, state: State) -> RuntimeSubAbility | None:
        if not self._subdefs:
            return None
        
        compiled_mana_cost: ImmutableManaValue | None = self._compile_mana_cost(ability, state)
        compiled_slots: immutabledict[str, RepetitionTargetSlotWrapper]
        compiled_effects: immutabledict[str, Effect]
        compiled_action_node: ActionNode | None

        compiled_slots, compiled_effects, compiled_action_node = self._compile_action_graph()

        return RuntimeSubAbility(
            action_node = compiled_action_node, 
            mana_cost=compiled_mana_cost,
            slots=compiled_slots,
            effects=compiled_effects,
            resolution_speed=self._resolution_speed
        )


@dataclass(frozen=True)
class RuntimeSubAbility:

    action_node: ActionNode | None
    mana_cost: ImmutableManaValue | None = None 
    slots: immutabledict[str, RepetitionTargetSlotWrapper] = field(default_factory=immutabledict)
    effects: immutabledict[str, Effect] = field(default_factory=immutabledict)
    resolution_speed: ResolutionSpeed | None= None

    def normalize_effect_map(self, map: Mapping[str, Iterable[str]]) -> EffectSequence:
        """!
        @brief Convert effect keys from the action tree into concrete effect objects.
        @param map Mapping from effect keys to target slot keys.
        @return Immutable sequence of concrete effect bindings.
        """
        bindings: list[EffectBinding] = []
        for k, v in map.items():
            if k not in self.effects:
                raise KeyError(k)
            
            slots: set[RepetitionTargetSlotWrapper] = set()

            for key in v:
                slot = self.slots.get(key)

                if slot is None:
                    raise KeyError(f"  No TargetSlot for '{key}' in self.slots.")

                slots.add(slot)

            bindings.append(EffectBinding(
                effect=self.effects[k],
                slots=frozenset(slots)
            ))

        return EffectSequence(sequence=tuple(bindings))

    def get_used_slots_in_esmap(self, effects_to_slots: Mapping[str, Iterable[str]]) -> set[RepetitionTargetSlotWrapper]:
        """!
        @brief Collect every target slot referenced by a selected effect mapping.
        @param effects_to_slots Mapping selected for one generated action option.
        @return Target slots required by the selected effects.
        """
        keys: set[str] = set()
        for slot_keys in effects_to_slots.values():
            keys.update(slot_keys)

        res: set[RepetitionTargetSlotWrapper] = set()
        for k in keys:
            if k in self.slots:
                res.add(self.slots[k])

        return res

    def get_used_slots_in_tbinding(self, binding: Mapping[str, Mapping[str, Any]]) -> set[RepetitionTargetSlotWrapper]:
        res: set[RepetitionTargetSlotWrapper] = set()
        for v in binding.values():
            for k in v.keys():
                if k in self.slots:
                    res.add(self.slots[k])
        return res


@dataclass(frozen=True)
class AbilityDefinition:
    """!
    @brief Static data that describes how a card ability can be used.
    """
    uses_stack: bool = True
    subdefs: immutabledict[SAVariableType, SubAbilityVariable] = field(default_factory=immutabledict)
    allowed_zones: frozenset[ZoneType] = field(default_factory=lambda: frozenset({ZoneType.BATTLEFIELD})) 
    
    def is_usable_in_zone(self, zone: ZoneType) -> bool:
        """!
        @brief Return whether the ability is available from the given card zone.
        @param zone Zone to check.
        @return True if the ability is available in the zone.
        """
        return zone in self.allowed_zones

    def to_ability(self, card: Card, player: Player) -> Ability:
        return Ability(source=card, controller=player, definition=self)


@dataclass(frozen=True)
class AbilityData:
    
    definition: AbilityDefinition 
    source: Card
    caster: Player


class Ability(HasModifiableStats): 
    """!
    @brief Runtime ability bound to a specific source card.
    """
    @overload
    def __init__(self, ability: Ability) -> None: ...
    @overload
    def __init__(self, definition: AbilityDefinition, source: Card, caster: Player) -> None: ...
    def __init__(
        self, 
        first: Ability | AbilityDefinition,
        second: Card | None = None,
        third: Player | None = None
    ) -> None:
        
        if isinstance(first, Ability):
            self._data: AbilityData  = first._data
            self._stats = first._stats
            self._modifier_sources = first._modifier_sources

        else:
            if second is None or third is None:
                raise ValueError("Both 'source' and 'caster' must be provided for a new Ability.")

            self._data = AbilityData(first, second, third)

            stats_builder: dict[StatType, Stat] = {STAT_CONTROLLER: ModifiablePrimitiveStat(third, STAT_CONTROLLER)}

            for param in first.subdefs.values():
                if param.cost_subdef and param.cost_subdef.mana_cost is not None:
                    if param.cost_subdef.mana_cost.stat_type in stats_builder:
                        raise KeyError("  Duplicity in keys in stats dict.")
                    stats_builder[param.cost_subdef.mana_cost.stat_type] = param.cost_subdef.mana_cost

            self._stats: immutabledict[StatType, Stat] = immutabledict(stats_builder)

            self._modifier_sources: tuple[ModifierSource, ...] = second.modifier_sources
    
    # HasStat properties
    @property
    @override
    def stats(self):
        return self._stats
    
    # HasModifiers properties
    @property
    @override
    def modifier_sources(self):
        return self._modifier_sources
    
    @property
    def definition(self) -> AbilityDefinition:
        return self._data.definition
    
    def to_game_action(self, c_generator: ExecutionPlan, a_generator: ExecutionPlan) -> GameAction:
        """!
        @brief Wrap chosen cost and effect generators into an executable game action.
        @param c_generator Generator for the cost operations.
        @param a_generator Generator for the ability effect operations.
        @return Game action representing one concrete ability choice.
        """
        return AbilityAction(
            action_key="", # TODO
            source=self._data.source, 
            cost_generator=c_generator,
            action_generator=a_generator,
            uses_stack=self.definition.uses_stack
        )

    def is_activatable(self) -> bool:
        return self.definition.is_usable_in_zone(self._data.source.get_zone())
    


# TODO Checknout fungovani vseho dole.

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
class TriggerAbilityDefinition(AbilityDefinition):
    """!
    @brief Static data for an ability that is produced by a matching game event.
    """

    condition: TriggerCondition = field(default=None)

    @override
    def to_ability(self, card, player):
        ... # TODO


@dataclass(frozen=True)
class TriggerAbility(Ability):
    """!
    @brief Runtime triggered ability paired with the event that caused it.
    """

    event: GameEvent | None = None
    
    def matches(self, state: State) -> bool:
        """!
        @brief Evaluate the stored trigger event against the ability condition.
        @param state Current game state.
        @return True if the stored event matches the trigger condition.
        """
        if self.event is None or not isinstance(self.definition, TriggerAbilityDefinition):
            return False
        return self.definition.condition.matches(state, self.event)
    

class AbilityCollection(KeyedCollection[AbilityDefinition]):
    pass



    