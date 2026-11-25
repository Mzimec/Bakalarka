"""Find playable land abilities across registered zones and current permissions."""
from __future__ import annotations
from collections.abc import Iterator
from typing import TYPE_CHECKING
from helper.query_system.query import EqQuery
from ..registers.card_register import IK_TYPE
from ...enums import CardType

if TYPE_CHECKING:
    from ..state import State
    from ..player import Player
    from ...game_actions.data_structs.ability import PlayLandAbility


class LandPlayCollector:
    def collect(self, state: State, player: Player) -> Iterator[PlayLandAbility]:
        from ...rules.lands import land_play_window_error
        # No individual permission can make a land playable outside this
        # window. Reject once before looking through lands in all zones.
        if land_play_window_error(player, state) is not None:
            return
        # Do not constrain owner or zone: an effect can grant access to exile,
        # graveyard, or a card owned by another player.
        for card in state.query_cards(EqQuery(IK_TYPE, CardType.LAND)):
            for definition in card.get_land_play_ability_defs(state).values():
                if definition.validation_error(card, player, state) is None:
                    yield definition.to_ability(card, player)
                    break  # Equivalent permissions do not duplicate one land play.


LAND_PLAY_COLLECTOR = LandPlayCollector()
