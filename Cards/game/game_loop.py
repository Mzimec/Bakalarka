from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .game_state.state import State
    from .game_state.player import Player

__all__ = [
    "GameLoop"
]

class GameLoop:
    """!
    @brief High-level turn loop that advances the game through its phases.
    """

    def __init__(self, state: State, event_bus=None) -> None:
        """!
        @brief Create a game loop for an existing game state.
        @param state Game state controlled by this loop.
        @param event_bus Optional event publisher for phase events.
        """
        self.state = state
        self.event_bus = event_bus
        self.is_game_over = False

    def run_game(self) -> None:
        """!
        @brief Run full turns until the game is marked as over.
        """
        while not self.is_game_over:
            self.start_turn()
            if self.is_game_over: return
            self.main_phase()
            if self.is_game_over: return
            self.combat_phase()
            if self.is_game_over: return
            self.second_main_phase()
            if self.is_game_over: return
            self.end_turn()
            self.switch_active_player()

    def start_turn(self) -> None:
        """!
        @brief Execute the beginning-of-turn sequence.
        """
        active = self.state.active_player
        self._trigger_event("turn_start", active)
        self.untap_step()
        self.draw_step()
        self._trigger_event("upkeep", active)

    def main_phase(self) -> None:
        """!
        @brief Run the first main phase for the active player.
        """
        active = self.state.active_player
        self._trigger_event("pre_main_phase", active)
        self._play_actions(active)
    

    def combat_phase(self) -> None:
        """!
        @brief Run the combat phase.
        """
        self._trigger_event("combat_phase", self.state.active_player)

    def second_main_phase(self) -> None:
        """!
        @brief Run the second main phase for the active player.
        """
        active = self.state.active_player
        self._trigger_event("second_main_phase", active)
        self._play_actions(active)
        
        

    def end_turn(self) -> None:
        """!
        @brief Execute end-of-turn processing.
        """
        self._trigger_event("end_turn", self.state.active_player)

    def switch_active_player(self) -> None:
        """!
        @brief Pass the turn to the next player.
        """
        self.state.switch_active_player()

    def untap_step(self) -> None:
        """!
        @brief Untap all permanents controlled by the active player.
        """
        self._trigger_event("untap_step", self.state.active_player)
        self.state.active_player.battlefield.untap_all()

    def draw_step(self) -> None:
        """!
        @brief Draw a card for the active player.
        """
        active = self.state.active_player
        self._trigger_event("draw_step", active)
        active.draw()

    def _trigger_event(self, event_name: str, subject) -> None:
        """!
        @brief Publish a simple phase or turn event if an event bus exists.
        @param event_name Name of the event to publish.
        @param subject Main object associated with the event.
        """
        if self.event_bus is not None:
            self.event_bus.publish(event_name, subject)
    
    def _play_actions(self, player: Player) -> None:
        """!
        @brief Let a player choose and resolve actions until they stop acting.
        @param player Player currently allowed to act.
        """
        while True:
            game_action = player.get_action(self.state)
            if game_action is None:
                break
            game_action.pay_cost(self.state)
            self.state.stack.push(game_action.to_stack(self.state))
            self.state.stack.resolve(self.state)
            if self.is_game_over: return
    
    def end_game(self, winner: Player) -> None:
        """!
        @brief Mark the game as over and announce the winner.
        @param winner Player who won the game.
        """
        self.is_game_over = True
        print(f"Game Over! Winner: {winner}")
