from __future__ import annotations
from dataclasses import dataclass
from typing import Generic, TypeVar, TYPE_CHECKING, Any
from immutabledict import immutabledict

if TYPE_CHECKING:
    from .mana.mana_value import ManaValue
    from ..helper.runtime_object import KeyedCollection
    from .game_actions.data_structs.ability import Ability,TriggerAbility
    from .game_state import Player
    from .game_state.modifier import Modifier

from .enums import *

__all__ = [
    "StatType",
    "STAT_MANA_COST",
    "STAT_TYPES",
    "STAT_SUBTYPES",
    "STAT_ABILITIES",
    "STAT_TRIGGERS",
    "STAT_CONTROLLER",
    "STAT_POWER",
    "STAT_TOUGHNESS",
    "STAT_ATTACH_MODS"
]


T = TypeVar("T")

@dataclass(frozen=True)
class StatType(Generic[T]):

    name: str


STAT_MANA_COST: StatType[ManaValue]                                 = StatType("mana cost")
STAT_TYPES: StatType[frozenset[CardType]]                           = StatType("types")
STAT_SUBTYPES: StatType[frozenset[CardSubtype]]                     = StatType("subtypes")
STAT_ABILITIES: StatType[KeyedCollection[Ability]]                  = StatType("abilities")
STAT_TRIGGERS: StatType[KeyedCollection[TriggerAbility]]            = StatType("triggers")
STAT_CONTROLLER: StatType[Player]                                   = StatType("controller")
STAT_POWER: StatType[int]                                           = StatType("power")
STAT_TOUGHNESS: StatType[int]                                       = StatType("toughness")
STAT_ATTACH_MODS: StatType[immutabledict[StatType[Any], Modifier]]  = StatType("modifiers to attach")
