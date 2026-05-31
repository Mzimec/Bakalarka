from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod

from ..constants import STARTING_HEALTH, HAND_SIZE
from .battlefield import Battlefield
if TYPE_CHECKING:
    from ..abilities.ability import GameAction, Ability
    from .card import Card, ZoneType
    from .state import State
    from .battlefield import CardPack
import random

__all__ = [
    "Player"
]


class Player:
    """!
    @brief Runtime player state and controller connection.
    """

    def __init__(self, deck: list[Card], controller: DecisionMaker) -> None:
        """!
        @brief Create a player with a shuffled deck and empty zones.
        @param deck Cards that start in the player's deck.
        @param controller Decision maker that chooses this player's actions.
        """
        self._controller = controller
        self._health = STARTING_HEALTH

        self._zone_map: dict[ZoneType, CardPack] = {
            ZoneType.BATTLEFIELD: Battlefield(),
            ZoneType.DECK: CardPack(deck),
            ZoneType.EXILE: CardPack(),
            ZoneType.GRAVEYARD: CardPack(),
            ZoneType.HAND: CardPack()
        }
    
    def try_find_card(self, card_key: str, zone: ZoneType | None = None) -> Card | None:
        if zone is None:
            for z in self._zone_map.values():
                card = z.get(card_key)
                if card is not None:
                    return card
        
        else:
            return self._zone_map.get(zone).get(card_key)
        
        return None
        

    
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
