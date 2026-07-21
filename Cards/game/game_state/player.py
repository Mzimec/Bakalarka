from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod

from ..constants import STARTING_HEALTH, HAND_SIZE
from .battlefield import Battlefield
if TYPE_CHECKING:
    from ..game_actions.data_structs.ability import GameAction
    from .card import Card
    from .state import State
    from .battlefield import CardCollection

import random
from ..enums import *

__all__ = [
    "Player"
]


class Player:
    """!
    @brief Runtime player state and controller connection.
    """

    def __init__(self, deck: list[Card], controller: DecisionMaker, idx = int) -> None:
        """!
        @brief Create a player with a shuffled deck and empty zones.
        @param deck Cards that start in the player's deck.
        @param controller Decision maker that chooses this player's actions.
        """
        self._controller = controller
        self._health = STARTING_HEALTH
        self._idx = idx

        self._zone_map: dict[ZoneType, CardCollection] = {
            ZoneType.BATTLEFIELD: Battlefield(),
            ZoneType.DECK: CardCollection(deck),
            ZoneType.EXILE: CardCollection(),
            ZoneType.GRAVEYARD: CardCollection(),
            ZoneType.HAND: CardCollection()
        }

    @property
    def idx(self) -> int:
        return self._idx
    
    def try_find_card(self, card_key: str, zone: ZoneType | None = None) -> Card | None:
        if zone is None:
            for z in self._zone_map.values():
                card = z.get(card_key)
                if card is not None:
                    return card
        
        else:
            return self._zone_map.get(zone).get(card_key)
        
        return None
    
    def get_cards(self, from_zones: list[ZoneType] | None = None) -> list[Card]:
        if from_zones is None:
            from_zones = [z for z in ZoneType]
        
        cards: list[Card] = []
        for zone in from_zones:
            cards.extend(self._zone_map[zone].values())
        
        return cards
        

    
    def draw(self) -> None:
        """!
        @brief Move the top card of the deck into the player's hand.
        @raises ValueError If the deck cannot provide a card.
        """
        if not self.deck:
            raise ValueError(f"Deck is None for {self}")
        
        if len(self.deck) == 0:
            raise ValueError(f"Deck is empty on draw for {self}!")
        
        card: Card = self.deck.pop()
        self.hand.append(card)

    def get_action(self, state: State) -> GameAction | None:
        """!
        @brief Ask the player's controller to choose an action.
        @param state Current game state.
        @return Chosen action, or None if the player takes no action.
        """
        return self.controller.get_action(state, self)
    

class DecisionMaker(ABC):
    """!
    @brief Base controller that chooses actions for a player.
    """

    @abstractmethod
    def get_action(self, state: State, player: Player) -> GameAction | None:
        """!
        @brief Choose a game action for the current state.
        @param state Current game state.
        @return Chosen game action, or None when no action is chosen.
        """
        pass
