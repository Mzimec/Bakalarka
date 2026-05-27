from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .abilities.ability import GameAction
    from .game_state.state import State
    from .input import PlayerInput
from .input import CommandData

__all__ = [
    "DecisionMaker",
    "HumanDM"
]

class DecisionMaker:
    def get_action(self, state: State) -> GameAction | None:
        raise NotImplementedError

class HumanDM(DecisionMaker):
    def __init__(self) -> None:
        self.player_input = PlayerInput()

    def get_action(self, state: State) -> GameAction | None:
        is_action_given = False
        while not is_action_given:
            input_data = self.player_input.get_command_data(state)
    
   

        



            
            
    