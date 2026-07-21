from __future__ import annotations
from typing import TYPE_CHECKING, override
from dataclasses import dataclass, field
from immutabledict import immutabledict

if TYPE_CHECKING:
    from .player import Player
    from .state import State
    from ..game_actions.data_structs.ability import AbilityDefinition, AbilityCollection, TriggerAbilityDefinition, ManaValue
    from ..game_actions import GameEvent

from ...helper.runtime_object import KeyedCollection, RuntimeObject, ImmutableKeyedCollection
from .modifier import ModifierSource, ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource, TimeStamp, Attachable, Modifier
from .stat import Stat, HasModifiableStats, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..enums import *
from ..stat_type import *
from helper.mutability_objs import *

__all__ = [
    "CardType",
    "CardSubtype",
    "ManaType",
    "CardDefinition",
    "Card"
]


PERMANENT_TYPES: frozenset[CardType] = frozenset({
    CardType.CREATURE,
    CardType.ARTIFACT,
    CardType.ENCHANTMENT,
    CardType.LAND,
})


class ZoneInfo:

    def __init__(self) -> None:
        self.zone: ZoneType = ZoneType.DECK
        self.entered_at: TimeStamp = TimeStamp(0, TurnPhase.UNTAP)


class AttachInfo:

    def __init__(self) -> None:
        self.attached_to: AttachedModifierSource | None = None
        self.attached_at: TimeStamp | None = None


@dataclass(frozen=True)
class CardDefinition:
    """!
    @brief Static card data shared by all runtime copies of that card.
    """

    name: str
    mana_cost: ManaValue | None
    color_identity: set[ManaType]

    types: frozenset[CardType]
    subtypes: frozenset[CardSubtype]

    triggers: frozenset[TriggerAbilityDefinition]
    abilities: frozenset[AbilityDefinition]
            
    power: int | None = None
    toughness: int | None = None

    attach_mods: immutabledict[StatType, Modifier] | None = None

    def is_permanent(self) -> bool:
        return (
            CardType.INSTANT not in self.types 
            and CardType.SORCERY not in self.types
        )


@dataclass(frozen=True)
class CardData:

    name: str
    color_identity: set[ManaType]


class CardRuntimeState:

    def __init__(
            self,
            tapped: bool | None = None,
            counters: dict[CounterType, int] | None = None,
            active_cont_effects: set[str] = set(), 
            attach_info: AttachInfo | None = None,
            attached: dict[str, Attachable] | None = None,
            damage_marked: int | None = None,
            face_down: bool = False,
        ) -> None:
 
        self.zone_info: ZoneInfo = ZoneInfo()
        self.tapped: bool = tapped
        self.counters: dict[CounterType, int] | None = dict(counters) if counters is not None else None
        self.active_cont_effects: set[str] | None = set(active_cont_effects) if active_cont_effects is not None else None
        self.attach_info: AttachInfo | None = attach_info
        self.attached: dict[str, Attachable] | None = dict(attached) if attached is not None else None
        self.damage_marked: int = damage_marked
        self.face_down: bool = face_down

        self._anchored_modifiers: list[str] = []
    
    @classmethod
    def create_permanent_card_state(
        cls,
        tapped: bool = False,
        counters: dict[CounterType, int] = {}, 
        attach_info: AttachInfo = AttachInfo(), 
        damage_marked: int = 0
    ) -> CardRuntimeState:
        
        return cls(
            tapped=tapped,
            counters=counters,
            attach_info=attach_info,
            damage_marked=damage_marked
        )


class Card(RuntimeObject, HasModifiableStats, Attachable):
    """!
    @brief Runtime card instance owned by a player.
    """

    def __init__(self, prototype: CardDefinition, owner: Player) -> None:
        """!
        @brief Create a runtime card from static card data.
        @param card_def Static card definition.
        @param owner Player who owns the card.
        """

        self._key: str = ""

        self._definition: CardDefinition = CardDefinition(
            name=prototype.name,
            color_identity=prototype.color_identity
        )

        self._owner = owner

        self._state = CardRuntimeState.create_permanent_card_state() if prototype.is_permanent() else CardRuntimeState()

        self._stats: immutabledict[StatType, Stat] = immutabledict({
            STAT_MANA_COST: ModifiableReferenceStat(prototype.mana_cost, STAT_MANA_COST),

            STAT_TYPES: ModifiableReferenceStat(ImmutableSet(prototype.types), STAT_TYPES),
            STAT_SUBTYPES: ModifiableReferenceStat(ImmutableSet(prototype.sub_types), STAT_SUBTYPES),

            STAT_TRIGGERS: ModifiableReferenceStat(ImmutableKeyedCollection.from_iter(prototype.triggers), STAT_TRIGGERS),
            STAT_ABILITIES: ModifiableReferenceStat(ImmutableKeyedCollection.from_iter(prototype.abilities), STAT_ABILITIES),

            STAT_POWER: ModifiablePrimitiveStat(prototype.power, STAT_POWER),
            STAT_TOUGHNESS: ModifiablePrimitiveStat(prototype.toughness, STAT_TOUGHNESS),

            STAT_CONTROLLER: ModifiablePrimitiveStat(owner, STAT_CONTROLLER),

            STAT_ATTACH_MODS: ModifiableReferenceStat(ImmutableDict(prototype.attach_mods), STAT_ATTACH_MODS)
        })

        self._modifier_sources: tuple[ModifierSource] = (
            ContinuouosEffectModifierSource(self._state.active_cont_effects), 
            CounterModifierSource(self._state.counters), 
            AttachedModifierSource(self._state.attached)
        )


    #--------------------------------------------------------------
    #--------------------------------------------------------------
    # Getters for private attributtes
    #--------------------------------------------------------------
    #--------------------------------------------------------------
    
    @property
    def definition(self) -> CardDefinition:
        return self._definition
    
    @property
    def owner(self) -> Player:
        return self._owner

    #--------------------------------------------------------------
    # RuntimeObject properties
    #--------------------------------------------------------------

    @property
    @override
    def key(self):
        return self._key
    
    #--------------------------------------------------------------
    # HasStats properties
    #--------------------------------------------------------------
    
    @property
    @override
    def stats(self):
        return self._stats
    
    #--------------------------------------------------------------
    # HasModifiers properties
    #--------------------------------------------------------------
    
    @property
    @override
    def modifier_sources(self):
        return self._modifier_sources
    
    #--------------------------------------------------------------
    # Attachable properties
    #--------------------------------------------------------------

    @property
    @override
    def attached_to(self):
        return self._state.attach_info.attached_to

    @attached_to.setter
    @override
    def attached_to(self, value: AttachedModifierSource | None):
        self._state.attach_info.attached_to = value
    
    @property
    @override
    def last_attach_at(self) -> TimeStamp | None:
        return self._state.attach_info.attached_at
    
    @last_attach_at.setter
    @override
    def last_attach_at(self, value: TimeStamp | None) -> None:
        self._state.attach_info.attached_at = value
    

    #--------------------------------------------------------------
    #--------------------------------------------------------------
    # Methods
    #--------------------------------------------------------------
    #--------------------------------------------------------------

    #--------------------------------------------------------------
    # Attachable override methdods
    #--------------------------------------------------------------

    @override
    def modifiers_to_attach(self, state):
        return self.get_stat(STAT_ATTACH_MODS, state)
    
    #--------------------------------------------------------------
    # Own methods
    #--------------------------------------------------------------

    def __str__(self) -> str:
        raise NotImplementedError("to_string not implemented yet")

    # ManaValue methods
    def get_mana_cost(self, state: State) -> ManaValue:
        raise NotImplementedError()
    

    # Types methods
    def get_types(self, state: State) -> frozenset[CardType]:
        granted_types: frozenset[CardType] | None = state.get_granted_types(self)
        if granted_types is None:
            return self.definition.types
        return self.definition.types | granted_types
    
    def is_type(self, state: State, type: CardType) -> bool:
        return type in self.get_types(state)
    
    def get_subtypes(self, state: State) -> frozenset[CardType]:
        granted_subtypes: frozenset[CardType] | None = state.get_granted_subtypes(self)
        if granted_subtypes is None:
            return self.definition.subtypes
        return self.definition.subtypes | granted_subtypes
    
    def is_subtype(self, state: State, subtype: CardType) -> bool:
        return subtype in self.get_subtypes(state)
    

    # Abilities methods
    def get_ability_defs(self, state: State) -> AbilityCollection:
        granted_abilities: AbilityCollection | None = state.get_granted_ability_defs(self)
        if granted_abilities is None:
            return self.definition.abilities
        return self.definition.abilities | granted_abilities

    def get_activatable_ability_defs(self, state: State) -> AbilityCollection:
        """!
        @brief Collect abilities currently available to this card.
        @param state Current game state.
        @return Runtime abilities available from this card's current zone.
        """

        return AbilityCollection.from_dict({
            k: v for k, v in self.get_ability_defs(state).items()
            if v.is_usable_in_zone(self.zone)
        })

    def get_ability_def(self, key: str, state: State) -> AbilityDefinition | None:
        return self.get_ability_defs(state).get(key)
    

    # Triggers methods
    def get_trigger_defs(self, state: State) -> KeyedCollection[TriggerAbilityDefinition]:
        granted_triggers: KeyedCollection[TriggerAbilityDefinition] | None = state.get_granted_trigger_def(self)
        if granted_triggers is None:
            return self.definition.triggers
        return self.definition.triggers | granted_triggers

    def get_triggers_with_event(self, event: GameEvent, state: State) -> KeyedCollection[TriggerAbilityDefinition]:
        return KeyedCollection.from_dict({
            k: v for k, v in self.get_trigger_defs(state).items()
            if v.condition.matches(state, event)
        })
    
    def get_trigger_def(self, key: str, state: State) -> TriggerAbilityDefinition | None:
        return self.get_trigger_defs(state).get(key)
    

    # Power and Tougness methods
    def get_power(self, state: State) -> int | None:
        raise NotImplementedError()
    
    def get_toughness(self, state: State) -> int | None:
        raise NotImplementedError()
    
    def is_dead(self, state: State) -> bool:
        tougness = self.get_toughness(state)
        if tougness is None:
            return False
        return self.state.damage_marked >= tougness
    

    # Summoning sick
    def is_summoning_sick(self, state: State) -> bool:
        state.is_summoning_sick(self)
