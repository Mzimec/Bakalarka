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
    "RuntimeObjectType"
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


class CardSubtype(Enum):
    """!
    @brief Creature or card subtypes supported by the game.
    """

    HUMAN = auto()
    ELF = auto()
    WIZARD = auto()
    SOLDIER = auto()


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
    SET = auto()
    ADD = auto()
    MULTIPLY = auto()


class ModifierType(Enum):
    SET = auto()
    ADD = auto()
    MULTIPLY = auto()
    REMOVE = auto()

'''
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
'''

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
    BACK  = auto()
    RESET = auto()


class ResourceType(Enum):

    MANA = auto()
    OWNED_CREATURE = auto()


class ResolutionSpeed(Enum):
    STACK = auto()
    IMMEDIATE = auto()


class CounterType(Enum):
    PLUS_ONE = auto()

class RuntimeObjectType(Enum):
    CARD = auto()
    PLAYER = auto()

