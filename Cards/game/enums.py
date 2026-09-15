"""Named zones, phases, card characteristics and rule categories."""

from __future__ import annotations
from enum import Enum, auto

__all__ = [
    "ZoneType",
    "SAVariableType",
    "TurnPhase",
    "CardType",
    "CardSubtype",
    "ManaType",
    "Layer",
    "ModifierType",
    "TargetType",
    "StepType",
    "_MetaResult",
    "ResourceType",
    "ResolutionSpeed",
    "CounterType",
    "RuntimeObjectType",
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
    STACK = auto()


class SAVariableType(Enum):

    X = auto()
    Y = auto()


class TurnPhase(Enum):
    UNTAP = auto()
    UPKEEP = auto()
    DRAW = auto()

    PRECOMBAT_MAIN = auto()

    BEGIN_COMBAT = auto()
    DECLARE_ATTACKERS = auto()
    AFTER_ATTACKERS = auto()
    DECLARE_BLOCKERS = auto()
    AFTER_BLOCKERS = auto()
    FIRST_COMBAT_DAMAGE = auto()
    SECOND_COMBAT_DAMAGE = auto()
    END_COMBAT = auto()

    POSTCOMBAT_MAIN = auto()

    END_STEP = auto()
    CLEANUP = auto()


class CardType(Enum):
    """!
    @brief Main card types supported by the game.
    """

    ARTIFACT = auto()
    CREATURE = auto()
    ENCHANTMENT = auto()
    LAND = auto()
    INSTANT = auto()
    SORCERY = auto()
    PLANESWALKER = auto()


class CardSubtype(Enum):
    ARCHER = auto()
    BIRD = auto()
    CENTAUR = auto()
    DEMON = auto()
    DJINN = auto()
    DRAKE = auto()
    DRUID = auto()
    HORSE = auto()
    MERFOLK = auto()
    NIGHTMARE = auto()
    OCTOPUS = auto()
    ORC = auto()
    PLANT = auto()
    RAT = auto()
    SCOUT = auto()
    SERPENT = auto()
    SHAMAN = auto()
    SKELETON = auto()
    SPHINX = auto()
    SPIDER = auto()
    UNICORN = auto()
    VAMPIRE = auto()

    """!
    @brief Creature or card subtypes supported by the game.
    """

    HUMAN = auto()
    ELF = auto()
    WIZARD = auto()
    SOLDIER = auto()
    BEAST = auto()
    CAT = auto()
    ANGEL = auto()
    DINOSAUR = auto()
    SPIRIT = auto()
    CLERIC = auto()
    GOBLIN = auto()
    WARRIOR = auto()
    ROGUE = auto()
    PHOENIX = auto()
    ELEMENTAL = auto()
    DRAGON = auto()
    WALL = auto()
    PLAINS = auto()
    ISLAND = auto()
    SWAMP = auto()
    MOUNTAIN = auto()
    FOREST = auto()
    AURA = auto()
    EQUIPMENT = auto()


class ManaType(Enum):
    """!
    @brief Mana colors and special mana types used by card costs.
    """

    COLORLESS = auto()
    WHITE = auto()
    BLUE = auto()
    BLACK = auto()
    RED = auto()
    GREEN = auto()


class Layer(Enum):
    COPY = 1
    CONTROL = 2
    TEXT = 3
    TYPE = 4
    COLOR = 5
    ABILITY = 6
    PT_CDA = 71
    PT_SET = 72
    PT_MODIFY = 73
    PT_SWITCH = 74
    RULES = 80
    # Compatibility names; arithmetic is not a separate MTG layer.
    SET = PT_SET
    ADD = PT_MODIFY
    MULTIPLY = PT_MODIFY


class ModifierType(Enum):
    SWITCH = auto()
    SET = auto()
    ADD = auto()
    MULTIPLY = auto()
    REMOVE = auto()


"""
class StatType(Enum):
    MANA_COST = auto()
    TYPES = auto()
    SUBTYPES = auto()
    ABILITIES = auto()
    TRIGGERS = auto()
    CONTROLLER = auto()
    POWER = auto()
    TOUGHNESS = auto()
    ATTACH_MODS = auto()
"""


class TargetType(Enum):
    PLAYER = auto()
    CARD = auto()
    STACK_ITEM = auto()


class StepType(int, Enum):
    """!
    @brief Ordered steps of a single action-building session.
    """

    COMMAND = auto()
    SOURCE = auto()
    COST_MODE = auto()
    COST = auto()
    MODE = auto()
    TARGET = auto()
    CONFIRM = auto()
    DONE = auto()


class _MetaResult(Enum):
    """!
    @brief Outcome of a meta-command entered by the player.
    """

    BACK = auto()
    RESET = auto()


class ResourceType(Enum):

    MANA = auto()
    OWNED_CREATURE = auto()


class ResolutionSpeed(Enum):
    STACK = auto()
    IMMEDIATE = auto()


class CounterType(Enum):
    PLUS_ONE = auto()
    MINUS_ONE = auto()
    LOYALTY = auto()


class RuntimeObjectType(Enum):
    CARD = auto()
    PLAYER = auto()

class ActivatableAbilityType(Enum):
    MANA = auto()
    NON_MANA = auto()
