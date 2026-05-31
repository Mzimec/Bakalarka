from __future__ import annotations
from typing import TYPE_CHECKING
from enum import Enum, auto
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
    """!
    @brief Main card types supported by the game.
    """

    CREATURE = auto()
    ARTIFACT = auto()
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

    UNCOLORED = auto()
    WHITE = auto()
    BLUE = auto()
    BLACK = auto()
    RED = auto()
    GREEN = auto()
    VOID = auto()

class CardDefinition:
    """!
    @brief Static card data shared by all runtime copies of that card.
    """

    def __init__(
            self, 
            name: str, 
            cost: list, 
            types: set[CardType], 
            local_triggers: dict = None, 
            global_triggers: dict = None,
            abilities: list[AbilityDefinition] = None) -> None:
        """!
        @brief Create a static card definition.
        @param name Display name of the card.
        @param cost Mana or resource cost.
        @param types Set of card types.
        @param local_triggers Triggers that belong only to this card.
        @param global_triggers Triggers that can observe broader game events.
        @param abilities Ability definitions printed on this card.
        """
        
        self.name = name
        self.cost = cost
        self.types = types

        self.abilities = list(abilities) if abilities else []
        self.local_triggers = {} if local_triggers is None else local_triggers
        self.global_triggers = {} if global_triggers is None else global_triggers
    
    def __repr__(self) -> str:
        """!
        @brief Return a readable representation of this card definition.
        @return Card name, cost, and types.
        """
        return f"{self.name} | Cost: {self.cost} | Types: {self.types}"
    
class CreatureCardDefinition(CardDefinition):
        """!
        @brief Static card data for creature cards.
        """

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
            """!
            @brief Create a static creature card definition.
            @param name Display name of the creature.
            @param cost Mana or resource cost.
            @param type Creature card type information.
            @param power Base power value.
            @param toughness Base toughness value.
            @param local_triggers Triggers that belong only to this card.
            @param global_triggers Triggers that can observe broader game events.
            @param abilities Ability definitions printed on this card.
            """
            
            super().__init__(name, cost, type, local_triggers, global_triggers, abilities)
            self.power = power
            self.toughness = toughness

class Card:
    """!
    @brief Runtime card instance owned by a player.
    """

    def __init__(self, card_def: CardDefinition, owner: Player) -> None:
        """!
        @brief Create a runtime card from static card data.
        @param card_def Static card definition.
        @param owner Player who owns the card.
        """
        self.key: str = ""
        self.card_def = card_def
        self.owner = owner

    def __repr__(self) -> str:
        """!
        @brief Return a readable representation of the card.
        @return Representation of the underlying card definition.
        """
        return self.card_def.__repr__()
    
    def get_zone(self, state: State) -> ZoneType:
        """!
        @brief Ask the game state where this card currently is.
        @param state Current game state.
        @return Zone containing this card.
        """
        return state.get_card_zone(self)
    
    def get_abilities(self, state: State) -> list[Ability]:
        """!
        @brief Collect abilities currently available to this card.
        @param state Current game state.
        @return Runtime abilities available from this card's current zone.
        """
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
        """!
        @brief Create the special ability used to play this card from hand.
        @param card Card being played.
        @return Ability that plays the card.
        """
        raise NotImplementedError("Play card ability generation not implemented yet")
    
    def to_string(self) -> str:
        pass
        
class PermanentCard(Card):
    """!
    @brief Runtime card that can exist on the battlefield as a permanent.
    """

    def __init__(self, card_def: CardDefinition, owner: Player) -> None:
        """!
        @brief Create an untapped permanent card.
        @param card_def Static card definition.
        @param owner Player who owns the permanent.
        """
        super().__init__(card_def, owner)
        self.is_tapped = False

class CreatureCard(PermanentCard):
    """!
    @brief Runtime creature permanent with combat stats and damage.
    """

    def __init__(self, card_def: CreatureCardDefinition, owner: Player) -> None:
        """!
        @brief Create a creature card with no damage marked.
        @param card_def Static creature card definition.
        @param owner Player who owns the creature.
        """
        super().__init__(card_def, owner)
        self.damage_taken = 0

    def get_power(self, state: State) -> int:
        """!
        @brief Get this creature's current power after state modifiers.
        @param state Current game state.
        @return Modified power value.
        """
        return state.modify_stat(self, "power", self.card_def.power)
    
    def get_toughness(self, state: State) -> int:
        """!
        @brief Get this creature's current toughness after state modifiers.
        @param state Current game state.
        @return Modified toughness value.
        """
        return state.modify_stat(self, "toughness", self.card_def.toughness)
