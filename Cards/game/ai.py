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
    """!
    @brief Base controller that chooses actions for a player.
    """

    def get_action(self, state: State) -> GameAction | None:
        """!
        @brief Choose a game action for the current state.
        @param state Current game state.
        @return Chosen game action, or None when no action is chosen.
        """
        raise NotImplementedError

class HumanDM(DecisionMaker):
    """!
    @brief Decision maker that obtains actions from human input.
    """

    def __init__(self) -> None:
        """!
        @brief Create a human decision maker with a player input reader.
        """
        self.player_input = PlayerInput()

    def get_action(self, state: State) -> GameAction | None:
        """!
        @brief Repeatedly read input until a valid action is provided.
        @param state Current game state.
        @return Chosen game action, or None if input handling does not build one.
        """
        is_action_given = False
        while not is_action_given:
            input_data = self.player_input.get_command_data(state)
    
   

        



            
            
    
