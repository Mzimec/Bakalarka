from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..constants import STARTING_HEALTH, HAND_SIZE
from .battlefield import Battlefield
if TYPE_CHECKING:
    from ..abilities.ability import GameAction, ActionBlueprint, Ability
    from .card import Card
    from .state import State
    from..ai import DecisionMaker
import random

__all__ = [
    "Player"
]

class Player:
    def __init__(self, deck: list["Card"], controller: DecisionMaker) -> None:
        # flag to identify if player is AI-controlled
        self.controller = controller

        # health init
        self.health = STARTING_HEALTH

        # deck init
        self.deck = deck[:]
        random.shuffle(self.deck)

        # hand init
        self.hand = []
        #for i in range(HAND_SIZE):
            #self.draw()
        
        # graveyard init
        self.graveyard = []

        # battlefield init
        self.battlefield = Battlefield([])

    
    def draw(self) -> None:
        if not self.deck:
            raise ValueError(f"Deck is None for {self}")
        
        if len(self.deck) == 0:
            raise ValueError(f"Deck is empty on draw for {self}!")
        
        card: Card = self.deck.pop()
        self.hand.append(card)

    def get_action(self, state: State) -> GameAction | None:
        return self.controller.get_action(state, self)
    