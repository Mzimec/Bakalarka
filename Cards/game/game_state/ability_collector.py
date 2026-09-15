"""Query-backed discovery of abilities available to a player."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from helper.query_system.query import EqQuery, HasQuery

from ..enums import ActivatableAbilityType
from .registers.card_register import (
    IK_ABILITY_KIND,
    IK_CONTROLLER,
)

if TYPE_CHECKING:
    from .player import Player
    from .state import State
    from ..game_actions.data_structs.ability import Ability


class AbilityCollector:

    def collect(
        self,
        state: State,
        player: Player,
    ) -> Iterator[Ability]:
        yield from self._collect(
            state,
            player,
            kind=None,
        )

    def collect_mana(
        self,
        state: State,
        player: Player,
    ) -> Iterator[Ability]:
        yield from self._collect(
            state,
            player,
            kind=ActivatableAbilityType.MANA,
        )

    def collect_non_mana(
        self,
        state: State,
        player: Player,
    ) -> Iterator[Ability]:
        yield from self._collect(
            state,
            player,
            kind=ActivatableAbilityType.NON_MANA,
        )

    def _candidate_query(self, player, kind):
        ability_query = (
            HasQuery(IK_ABILITY_KIND)
            if kind is None
            else EqQuery(IK_ABILITY_KIND, kind)
        )

        return (
            EqQuery(IK_CONTROLLER, player)
            & ability_query
        )

    def _collect(
        self,
        state: State,
        player: Player,
        *,
        kind: ActivatableAbilityType | None,
    ) -> Iterator[Ability]:

        if player not in state.players:
            return

        query = self._candidate_query(
            player,
            kind,
        )

        for card in state.query_cards(query):
            for definition in card.get_activatable_ability_defs(state).values():

                if kind is not None:
                    definition_kind = (
                        ActivatableAbilityType.MANA
                        if definition.is_mana_ability
                        else ActivatableAbilityType.NON_MANA
                    )

                    if definition_kind is not kind:
                        continue

                if definition.validation_error(
                    card,
                    player,
                    state,
                ):
                    continue

                yield definition.to_ability(
                    card,
                    player,
                )