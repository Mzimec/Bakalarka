from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .player import Player
    from .card import Card
    
    from ..abilities import Ability
    from ..game_actions.data_structs.ability import TriggerAbility
    from ..game_actions.resolution.event_bus import GameEvent

from ..game_actions.game_stack import GameStack, PrioritySystem
from ..game_loop.game_loop import Turn
from .look_up import LookUpSystem, LookUpResult
from .modifier import ContinuousEffectsManager, ContinuousEffect

from ..enums import *

__all__ = [
    "State"
]

class State:
    """!
    @brief Holds mutable game-wide state such as players, stack, and priority.
    """

    def __init__(self, players: list[Player], active_player_idx: int = 0) -> None:
        """!
        @brief Create a game state for the given players.
        @param players Players participating in the game.
        @param active_player Optional player who starts as active.
        """
        self._players = tuple(players)
        self._active_player_idx: int = active_player_idx
        self._stack: GameStack = GameStack()
        self._priority: PrioritySystem = PrioritySystem(self)
        self._turn = Turn()
        self._lookup_system = LookUpSystem()
        self._cont_effect_manager = ContinuousEffectsManager()

    @property
    def active_player(self) -> Player:
        return self._players[self._active_player]
    
    def lookup(self, key: str) -> LookUpResult:
        return self.lookup_system.lookup(key, self)
    
    def _cycle_player_idx(self, idx) -> int:
        if idx >= len(self._players): idx = 0
        if idx <  0: idx = len(self._players) - 1
        return idx

    def get_next_player(self, player: Player) -> Player:
        """!
        @brief Return the next player in turn order.
        @param player Player whose successor should be found.
        @return Next player, wrapping around at the end.
        """

        return self._cycle_player_idx(player.idx + 1)
        

    def switch_active_player(self) -> None:
        """!
        @brief Advance the active player to the next player in turn order.
        """
        return self._cycle_player_idx(self._active_player_idx + 1)

    def get_cards(self, from_players: list[Player] | None = None, from_zones: list[ZoneType] | None = None) -> list[Card]:
        """!
        @brief Iterate through all cards in zones tracked by the state.
        @return Iterator over known cards.
        """
        if from_players is None:
            from_players = self._players
        
        if from_zones is None:
            from_zones = [z for z in ZoneType]

        cards: list[Card] = []
        for player in from_players:
            cards.extend(player.get_cards(from_zones))
        
        return cards
    
    def get_cont_effect(self, key: str) -> ContinuousEffect | None:
        return self._cont_effect_manager.get(key)

    
    '''
    TODO
    '''
    def get_turn_phase(self) -> TurnPhase:
        pass

    def get_granted_abilities(self, card: Card) -> list[Ability]:
        """!
        @brief Return abilities granted to a card by external effects.
        @param card Card whose granted abilities should be collected.
        @return Granted abilities.
        """
        return []

    def get_triggered_abilities(self, event: GameEvent | None = None) -> list[TriggerAbility]:
        """!
        @brief Collect triggered abilities currently active for an event.
        @param event Event that may satisfy trigger conditions.
        @return Triggered abilities available from cards in valid zones.
        """
        from ..game_actions.data_structs.ability import TriggerAbility, TriggerAbilityDefinition

        triggers: list[TriggerAbility] = []
        for card in self.get_cards():
            zone = card.get_zone()

            for ability_def in card.card_def.abilities.values():
                if not isinstance(ability_def, TriggerAbilityDefinition):
                    continue
                if ability_def.is_usable_in_zone(zone):
                    triggers.append(TriggerAbility(source=card, data=ability_def, event=event))

        return triggers