"""Console priority shortcut: auto-pass when no meaningful action is available."""

from game.enums import CardType, TurnPhase
from game.console.command_choices import feasible, UnsupportedCommandDefinition
from game.rules.lands import land_play_error


def can_auto_pass(state, player):
    if (
        state.is_game_over
        or state.priority.current_player is not player
        or state.turn.phase in {TurnPhase.UNTAP, TurnPhase.CLEANUP}
    ):
        return False

    # Land play is not represented by an AbilityDefinition,
    # so check it separately in the active player's main phases.
    if (
        state.active_player is player
        and state.turn.phase in {
            TurnPhase.PRECOMBAT_MAIN,
            TurnPhase.POSTCOMBAT_MAIN,
        }
    ):
        for card in player.hand.values():
            if (
                CardType.LAND in card.get_types(state)
                and land_play_error(card, player, state) is None
            ):
                return False

    # Ordinary mana abilities alone should not force a priority prompt.
    # If mana can enable a meaningful action, feasible() on that action
    # will find a valid payment through the mana solver.
    ordinary_mana = {
        (source.source, source.ability_key)
        for source in state.get_mana_sources(player)
    }

    for card in state.get_cards():
        for definition in card.get_ability_defs(state).values():
            if definition.validation_error(card, player, state):
                continue

            if (
                definition.is_mana_ability
                and (card, definition.key) in ordinary_mana
            ):
                continue

            # Keep unsupported or variable definitions interactive rather than
            # accidentally passing over a potentially legal choice.
            if definition.subdefs or "X" in str(card.get_mana_cost(state)):
                return False

            try:
                if feasible(definition.to_ability(card, player), state):
                    return False
            except UnsupportedCommandDefinition:
                return False

    return True