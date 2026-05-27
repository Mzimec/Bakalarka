from __future__ import annotations
from typing import TYPE_CHECKING
from enum import Enum
if TYPE_CHECKING:
    from .player import Player
    from .state import State
    from ..abilities.ability import AbilityDefinition, Ability, ZoneType

__all__ = [
    "CardType",
    "CardSubtype",
    "ManaType",
    "CardDefinition",
    "CreatureCardDefinition",
    "Card",
    "PermanentCard",
    "CreatureCard"
]

class CardType(Enum):
    CREATURE = 1
    ARTIFACT = 2
    ENCHANTMENT = 3
    LAND = 4
    INSTANT = 5
    SORCERY = 6

class CardSubtype(Enum):
    HUMAN = 1
    ELF = 2
    WIZARD = 3
    SOLDIER = 4


class ManaType(Enum):
    UNCOLORED = 1
    WHITE = 2
    BLUE = 3
    BLACK = 4
    RED = 5
    GREEN = 6
    VOID = 7

class CardDefinition:
    def __init__(
            self, 
            name: str, 
            cost: list, 
            types: set[CardType], 
            local_triggers: dict = None, 
            global_triggers: dict = None,
            abilities: list[AbilityDefinition] = None) -> None:
        
        self.name = name
        self.cost = cost
        self.types = types

        self.abilities = list(abilities) if abilities else []
        self.local_triggers = {} if local_triggers is None else local_triggers
        self.global_triggers = {} if global_triggers is None else global_triggers
    
    def __repr__(self) -> str:
        return f"{self.name} | Cost: {self.cost} | Types: {self.types}"
    
class CreatureCardDefinition(CardDefinition):
        def __init__(
            self, 
            name: str, 
            cost: list, 
            type: CardType, 
            power: int, 
            toughness: int,
            local_triggers: dict = None, 
            global_triggers: dict = None,
            abilities: list[AbilityDefinition] = None) -> None:
            
            super().__init__(name, cost, type, local_triggers, global_triggers, abilities)
            self.power = power
            self.toughness = toughness

class Card:
    def __init__(self, card_def: CardDefinition, owner: Player) -> None:
        self.card_def = card_def
        self.owner = owner

    def __repr__(self) -> str:
        return self.card_def.__repr__()
    
    def get_zone(self, state: State) -> ZoneType:
        return state.get_card_zone(self)
    
    def get_abilities(self, state: State) -> list[Ability]:
        zone = self.get_zone(state)
        res: list[Ability] = []

        for ability_def in self.card_def.abilities:
            if ability_def.is_usable_in_zone(zone):
                res.append(Ability(ability_def.costs, ability_def.action_bps))
        
        if zone == ZoneType.HAND:
            res.append(self._create_play_card_ability(self))
        
        res.extend(state.get_granted_abilities(self))

        return res
    
    def _create_play_card_ability(self, card: Card) -> Ability:
        raise NotImplementedError("Play card ability generation not implemented yet")
        
class PermanentCard(Card):
    def __init__(self, card_def: CardDefinition, owner: Player) -> None:
        super().__init__(card_def, owner)
        self.is_tapped = False

class CreatureCard(PermanentCard):
    def __init__(self, card_def: CreatureCardDefinition, owner: Player) -> None:
        super().__init__(card_def, owner)
        self.damage_taken = 0

    def get_power(self, state: State) -> int:
        return state.modify_stat(self, "power", self.card_def.power)
    
    def get_toughness(self, state: State) -> int:
        return state.modify_stat(self, "toughness", self.card_def.toughness)