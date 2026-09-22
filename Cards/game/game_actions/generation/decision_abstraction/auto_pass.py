"""Optional conservative preflight for interactive priority selection."""
from game.enums import TurnPhase
from game.game_state.collectors.ability_collector import ABILITY_COLLECTOR
from game.game_state.collectors.land_play_collector import LAND_PLAY_COLLECTOR
from game.game_state.registers.card_register import IK_HAS_TRIGGERS, IK_STATIC_REPLACEMENT
from helper.query_system.query import EqQuery


class PriorityAutoPass:
    def can_pass(self, state, player, *, is_available=None):
        """Prove a quiet window without building the entire option space.

        By default any nontrivial ability defers to the normal pipeline. A UI
        may supply a lazy feasibility probe to also skip unpayable actions.
        Agents instead detect a sole pass in their normal candidate pipeline,
        avoiding duplicate discovery and mana-source compilation.
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
