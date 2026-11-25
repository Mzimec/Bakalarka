"""Conservative absence proof for the standard agents' priority policy."""
from __future__ import annotations
from collections.abc import Iterable
from typing import TYPE_CHECKING
from itertools import chain

from game.enums import TurnPhase
from game.game_state.collectors.priority_ability_collector import PRIORITY_ABILITY_COLLECTOR
from game.rules.lands import land_play_window_error

if TYPE_CHECKING:
    from game.game_state import Player, State
    from game.game_actions.data_structs.ability import Ability


class PreparedPriorityCollector:
    """!
    @brief One request prefetched abilities, consumed once by its normal pipeline.
    Reiteration or use for another request delegates to live discovery. The
    prefetched stream is never kept in the agent or cached between decisions.
    """

    def __init__(self, state: State, player: Player, abilities: Iterable[Ability] | None, *, empty: bool) -> None:
        """!
        @brief Store prefetched ability discovery for one priority request.
        """
        self.state, self.player = state, player
        self._abilities = abilities
        self.empty = empty

    def collect_lands(self, state: State, player: Player) -> Iterable[Ability]:
        """!
        @brief Delegate live land-play collection.
        @param state Current game state.
        @param player Player being queried.
        @return Iterable of land-play abilities.
        """
        return PRIORITY_ABILITY_COLLECTOR.collect_lands(state, player)

    def collect_abilities(self, state: State, player: Player, *, include_mana: bool = True) -> Iterable[Ability]:
        """!
        @brief Return prefetched abilities once, otherwise delegate live collection.
        @param state Current game state.
        @param player Player being queried.
        @param include_mana Whether mana abilities are included.
        @return Iterable of priority abilities.
        """
        if (not include_mana and state is self.state and player is self.player
                and self._abilities is not None):
            abilities, self._abilities = self._abilities, None
            return abilities
        return PRIORITY_ABILITY_COLLECTOR.collect_abilities(state, player, include_mana=include_mana)


class NonManaPriorityAvailability:
    """!
    @brief Prove an empty space before constructing actions or searching payments.
    Applies to the built-in agent policy: no standalone mana or concede actions,
    and attempted abilities excluded. Nonempty means unknown, not playable.
    Nothing is cached between decisions; discovery uses the live indexes.
    """

    def prepare(self, state: State, player: Player, attempted: Iterable[tuple[object, str]] = ()) -> PreparedPriorityCollector | None:
        """!
        @brief Return a reusable discovery result, or None for an unprobed window.
        A nonempty result transfers the first candidate and remaining iterator
        into normal generation, so an unsuccessful proof never repeats discovery.
        """
        if (state.is_game_over or state.priority.current_player is not player
                or state.turn.phase in {TurnPhase.UNTAP, TurnPhase.CLEANUP}):
            return None
        # An open land window is deliberately unknown. Ordinary discovery owns
        # every permission, including graveyard/exile and another owner's cards.
        # Do not scan those cards once here and again in the fallback pipeline.
        if land_play_window_error(player, state) is None:
            return None
        abilities = (
            ability for ability in PRIORITY_ABILITY_COLLECTOR.collect_abilities(state, player, include_mana=False)
            if (ability.source.command_id, ability.key) not in attempted
        )
        first = next(abilities, None)
        candidates = chain((first,), abilities) if first is not None else iter(())
        return PreparedPriorityCollector(state, player, candidates, empty=first is None)

    def can_pass(self, state: State, player: Player, attempted: Iterable[tuple[object, str]] = ()) -> bool:
        """!
        @brief Standalone predicate; callers generating on failure should use prepare.
        """
        prepared = self.prepare(state, player, attempted)
        return prepared is not None and prepared.empty


NON_MANA_PRIORITY_AVAILABILITY = NonManaPriorityAvailability()
