"""Conservative console shortcut: pass priority, never bypass a game-loop step."""

from game.enums import TurnPhase
from game.console.command_choices import feasible, UnsupportedCommandDefinition


def can_auto_pass(state, player):
    if (
        state.is_game_over
        or state.priority.current_player is not player
        or not state.stack.is_empty()
        or player.mana_pool
        or state.turn.phase in {TurnPhase.UNTAP, TurnPhase.CLEANUP}
        or (
            state.active_player is player
            and state.turn.phase in {TurnPhase.PRECOMBAT_MAIN, TurnPhase.POSTCOMBAT_MAIN}
        )
    ):
        return False
    # A tap-for-mana action is worth offering even without a payable spell
    # if producing mana or tapping might have another consequence.
    reactive = bool(
        state.get_trigger_abilities()
        or state.replacement_rules
        or getattr(state, "delayed_triggers", ())
    )
    if not reactive:
        reactive = any(card.definition.replacement_effects for card in state.get_cards())
    ordinary_mana = {
        (source.source, source.ability_key) for source in state.get_mana_sources(player)
    }
    for card in state.get_cards():
        for definition in card.get_ability_defs(state).values():
            if definition.validation_error(card, player, state):
                continue
            if (
                definition.is_mana_ability
                and not reactive
                and (card, definition.key) in ordinary_mana
            ):
                continue
            # Variable/custom definitions need a player's judgment, including
            # X spells which are uncastable at X=0 but have legal X>0 choices.
            if definition.subdefs or "X" in str(card.get_mana_cost(state)):
                return False
            try:
                if feasible(definition.to_ability(card, player), state):
                    return False
            except UnsupportedCommandDefinition:
                return False
    return True
