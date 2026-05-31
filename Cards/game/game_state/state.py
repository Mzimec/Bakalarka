from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .player import Player
    from .card import Card
    from .look_up import LookUpSystem, LookUpResult
    from ..abilities import Ability
    from ..abilities.ability import TriggeredAbility
    from ..game_actions.event_bus import GameEvent
from ..game_actions.game_stack import GameStack, PrioritySystem
from ..game_loop.game_loop import Turn, TurnPhase

__all__ = [
    "State"
]

class State:
    """!
    @brief Holds mutable game-wide state such as players, stack, and priority.
    """

    def __init__(self, players: list[Player], active_player: Player | None = None) -> None:
        """!
        @brief Create a game state for the given players.
        @param players Players participating in the game.
        @param active_player Optional player who starts as active.
        """
        self.players = list(players)
        self.active_player = self.players[0] if active_player is None else active_player
        self.stack: GameStack = GameStack()
        self.priority: PrioritySystem = PrioritySystem(self)
        self.turn = Turn()
        self.lookup_system = LookUpSystem()

    def lookup(self, key: str) -> LookUpResult:
        self.lookup_system.lookup(key, self)

    def get_next_player(self, player: Player) -> Player:
        """!
        @brief Return the next player in turn order.
        @param player Player whose successor should be found.
        @return Next player, wrapping around at the end.
        """
        cur_idx: int = -1
        for i in range(len(self.players)):
            if self.players[i] == player: cur_idx = i
        cur_idx += 1
        if cur_idx >= len(self.players): cur_idx = 0
        return self.players[cur_idx]

    @property
    def inactive_player(self) -> Player:
        """!
        @brief Convenience accessor for the opponent of the active player.
        @return Inactive player.
        """
        return self.get_opponent(self.active_player)

    def get_opponent(self, player: Player) -> Player:
        """!
        @brief Return the next player as this player's opponent.
        @param player Player whose opponent should be found.
        @return Opposing player.
        """
        return self.get_next_player(player)

    def switch_active_player(self) -> None:
        """!
        @brief Advance the active player to the next player in turn order.
        """
        self.active_player = self.get_next_player(self.active_player)

    def get_card_zone(self, card: Card):
        """!
        @brief Find the zone that currently contains a card.
        @param card Card to locate.
        @return Zone containing the card, or None if it is not found.
        """
        from ..abilities import ZoneType

        for player in self.players:
            if card in player.hand:
                return ZoneType.HAND
            if card in player.battlefield.permanents:
                return ZoneType.BATTLEFIELD
            if card in player.graveyard:
                return ZoneType.GRAVEYARD

        return None

    def get_granted_abilities(self, card: Card) -> list[Ability]:
        """!
        @brief Return abilities granted to a card by external effects.
        @param card Card whose granted abilities should be collected.
        @return Granted abilities.
        """
        return []

    def get_triggered_abilities(self, event: GameEvent | None = None) -> list[TriggeredAbility]:
        """!
        @brief Collect triggered abilities currently active for an event.
        @param event Event that may satisfy trigger conditions.
        @return Triggered abilities available from cards in valid zones.
        """
        from ..abilities import TriggeredAbility, TriggeredAbilityDefinition

        triggers: list[TriggeredAbility] = []
        for card in self._iter_cards():
            zone = self.get_card_zone(card)
            if zone is None:
                continue

            for ability_def in card.card_def.abilities:
                if not isinstance(ability_def, TriggeredAbilityDefinition):
                    continue
                if ability_def.is_usable_in_zone(zone):
                    triggers.append(TriggeredAbility(source=card, data=ability_def, event=event))

        return triggers

    def _iter_cards(self):
        """!
        @brief Iterate through all cards in zones tracked by the state.
        @return Iterator over known cards.
        """
        for player in self.players:
            yield from player.hand
            yield from player.battlefield.permanents
            yield from player.graveyard
    
    def get_turn_phase(self) -> TurnPhase:
        pass
