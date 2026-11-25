"""Console feasibility adapter for the shared priority shortcut."""
from game.console.command_choices import feasible, UnsupportedCommandDefinition
from game.game_actions.generation.decision_abstraction.auto_pass import PRIORITY_AUTO_PASS


def _is_available(ability, state):
    try:
        return feasible(ability, state)
    except UnsupportedCommandDefinition:
        return True


def can_auto_pass(state, player):
    return PRIORITY_AUTO_PASS.can_pass(state, player, is_available=_is_available)
