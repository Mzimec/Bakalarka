from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .player import Player
    from .card import Card
    from ..abilities import Ability
    from ..abilities.ability import TriggeredAbility
    from ..game_actions.event_bus import GameEvent
from ..game_actions.game_stack import GameStack, PrioritySystem

__all__ = [
    "State"
]

class State:
    def __init__(self, players: list[Player], active_player: Player | None = None) -> None:
        self.players = list(players)
        self.active_player = self.players[0] if active_player is None else active_player
        self.stack: GameStack = GameStack()
        self.priority: PrioritySystem = PrioritySystem(self)

    def get_next_player(self, player: Player) -> Player:
        cur_idx: int = -1
        for i in range(len(self.players)):
            if self.players[i] == player: cur_idx = i
        cur_idx += 1
        if cur_idx >= len(self.players): cur_idx = 0
        return self.players[cur_idx]

    @property
    def inactive_player(self) -> Player:
        return self.get_opponent(self.active_player)

    def get_opponent(self, player: Player) -> Player:
        return self.get_next_player(player)

    def switch_active_player(self) -> None:
        self.active_player = self.get_next_player(self.active_player)

    def get_card_zone(self, card: Card):
        from ..abilities import ZoneType

        for player in self.players:
            if card in player.hand:
                return ZoneType.HAND
            if card in player.battlefield.pemanents:
                return ZoneType.BATTLEFIELD
            if card in player.graveyard:
                return ZoneType.GRAVEYARD

        return None

    def get_granted_abilities(self, card: Card) -> list[Ability]:
        return []

    def get_triggered_abilities(self, event: GameEvent | None = None) -> list[TriggeredAbility]:
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
        for player in self.players:
            yield from player.hand
            yield from player.battlefield.pemanents
            yield from player.graveyard
