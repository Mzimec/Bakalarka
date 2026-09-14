"""Parse whole combat declarations without mutating the game state."""

from game.console.console_commands import CommandError, parse_command, resolve_card, resolve_player
from game.enums import ZoneType


def resolve_defender(reference, state, player):
    try:
        return resolve_player(reference, state, player)
    except CommandError:
        return resolve_card(reference, player, global_scope=True)


def parse_attackers(raw, state, player):
    """!
    @brief ``attack <card>... [defender]`` or ``attack <card>:<defender>...``.
    """
    command = parse_command(raw)
    if command.name == "pass" and not command.arguments:
        return {}
    if command.name != "attack" or not command.arguments:
        raise CommandError("Usage: attack <card>... [defender], or pass.")
    args = list(command.arguments)
    opponents = [p for p in state.active_players if p is not player]
    default = opponents[0] if len(opponents) == 1 else None
    if len(args) > 1 and ":" not in args[-1]:
        try:
            default = resolve_defender(args[-1], state, player)
            if state.combat.defending_player(default) is None:
                raise CommandError("Not a defending player or planeswalker.")
        except CommandError:
            pass
        else:
            args.pop()
    result = {}
    for arg in args:
        parts = arg.split(":")
        if len(parts) > 2:
            raise CommandError("Use <card>:<defender> for each attacker.")
        card = resolve_card(parts[0], player, ZoneType.BATTLEFIELD, controlled=True)
        defender = resolve_defender(parts[1], state, player) if len(parts) == 2 else default
        if defender is None:
            raise CommandError("Specify a defender for each attacker.")
        if card in result:
            raise CommandError("A creature cannot be declared twice.")
        result[card] = defender
    try:
        return state.combat.validate_attackers(player, result)
    except ValueError as error:
        raise CommandError(str(error)) from error


def parse_blockers(raw, state, player):
    """!
    @brief ``block <blocker>:<attacker>...`` or ``pass``.
    """
    command = parse_command(raw)
    if command.name == "pass" and not command.arguments:
        return {}
    if command.name != "block" or not command.arguments:
        raise CommandError("Usage: block <blocker>:<attacker>..., or pass.")
    result = {}
    for arg in command.arguments:
        parts = arg.split(":")
        if len(parts) != 2:
            raise CommandError("Use <blocker>:<attacker> for each blocker.")
        blocker = resolve_card(parts[0], player, ZoneType.BATTLEFIELD, controlled=True)
        attacker = resolve_card(parts[1], player, global_scope=True)
        if blocker in result:
            raise CommandError("A creature cannot block multiple attackers.")
        result[blocker] = attacker
    try:
        return state.combat.validate_blockers(player, result)
    except ValueError as error:
        raise CommandError(str(error)) from error
