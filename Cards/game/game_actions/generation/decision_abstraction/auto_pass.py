"""Optional conservative preflight for interactive priority selection."""
from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING
from game.enums import TurnPhase
from game.game_state.collectors.ability_collector import ABILITY_COLLECTOR
from game.game_state.collectors.land_play_collector import LAND_PLAY_COLLECTOR
from game.game_state.registers.card_register import IK_HAS_TRIGGERS, IK_STATIC_REPLACEMENT
from helper.query_system.query import EqQuery

if TYPE_CHECKING:
    from game.game_state import Player, State
    from game.game_actions.data_structs.ability import Ability


class PriorityAutoPass:
    """!
    @brief Conservative preflight that proves when priority can be passed safely.
    """
    def can_pass(
        self,
        state: State,
        player: Player,
        *,
        is_available: Callable[[Ability, State], bool] | None = None,
    ) -> bool:
        """!
        @brief Prove a quiet window without building the entire option space.
        By default any nontrivial ability defers to the normal pipeline. A UI
        may supply a lazy feasibility probe to also skip unpayable actions.
        Agents use a separate absence proof for their no-standalone-mana policy,
        then detect a sole pass in the normal candidate pipeline if uncertain.
        """
        if (state.is_game_over or state.priority.current_player is not player
                or state.turn.phase in {TurnPhase.UNTAP, TurnPhase.CLEANUP}):
            return False
        if next(LAND_PLAY_COLLECTOR.collect(state, player), None) is not None:
            return False

        ordinary_mana = None
        for ability in ABILITY_COLLECTOR.collect(state, player):
            definition = ability.definition
            if definition.is_mana_ability:
                if ordinary_mana is None:
                    ordinary_mana = {(source.source, source.ability_key)
                                     for source in state.get_mana_sources(player)}
                    # Arbitrary rules may make tapping a mana source meaningful.
                    if ordinary_mana and (
                        getattr(state, "runtime_triggers", ())
                        or getattr(state, "replacement_rules", ())
                        or state._cont_effect_manager.query()
                        or state.query_cards(EqQuery(IK_HAS_TRIGGERS, True)
                                             | EqQuery(IK_STATIC_REPLACEMENT, True))
                    ):
                        return False
                if (ability.source, ability.key) in ordinary_mana:
                    continue
            if is_available is None:
                return False
            # A fixed-parameter probe cannot rule out these choices.
            if definition.subdefs or "X" in str(ability.source.get_mana_cost(state)):
                return False
            if is_available(ability, state):
                return False
        return True


PRIORITY_AUTO_PASS = PriorityAutoPass()
