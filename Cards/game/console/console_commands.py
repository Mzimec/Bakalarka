"""Parse player commands and resolve references without enumerating actions."""

from __future__ import annotations

from dataclasses import dataclass
import shlex

from game.enums import ZoneType
from game.game_state import Card, Player, State
from helper.query_system.query import EqQuery
from game.game_state.registers.card_register import IK_NAME, IK_OWNER, IK_ZONE, IK_CONTROLLER
from game.game_state.registers.player_register import IK_NAME as IK_PLAYER_NAME


class CommandError(ValueError):
    """!
    @brief An invalid player command that can be corrected at the prompt.
    """


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    arguments: tuple[str, ...] = ()


def parse_command(raw: str) -> ParsedCommand:
    try:
        tokens = shlex.split(raw)
    except ValueError as error:
        raise CommandError(f"Invalid command: {error}.") from error
    if not tokens:
        return ParsedCommand("pass")
    name = tokens[0].casefold()
    aliases = {
        "p": "play",
        "cast": "play",
        "a": "activate",
        "info": "inspect",
        "options": "help",
        "exit": "quit",
    }
    return ParsedCommand(aliases.get(name, name), tuple(tokens[1:]))


def card_reference(card: Card) -> str:
    """!
    @brief Canonical identity is global within the game and survives zone/control changes.
    """
    return getattr(card, "command_id", card.key)


def resolve_card(
    reference: str,
    player: Player,
    zone: ZoneType | None = None,
    *,
    controlled=False,
    global_scope=False,
) -> Card:
    reference = reference.casefold()
    state = player.game_state
    if state is None:
        raise CommandError("Player does not belong to a game state.")
    register = state.card_register
    if global_scope:
        card = register.get_by_reference(reference)
        if card is not None:
            return card
        matches = list(state.query_cards(EqQuery(IK_NAME, reference)))
        if not matches:
            matches = [card for card in state.get_cards() if card.key.endswith("-" + reference)]
        if len(matches) != 1:
            ids = ", ".join(card_reference(card) for card in matches)
            raise CommandError(
                f"Unknown or ambiguous card '{reference}'. Use a global card ID"
                + (f": {ids}." if ids else ".")
            )
        return matches[0]
    relation = IK_CONTROLLER if controlled else IK_OWNER
    scope = EqQuery(relation, player)
    matches = list(
        {
            card.key: card
            for key in (reference, f"{player.name.casefold()}-{reference}")
            if (card := register.get_by_reference(key)) is not None
            and register.contains(scope, card)
        }.values()
    )
    if not matches:
        query = scope & EqQuery(IK_NAME, reference)
        if zone is not None:
            query = query & EqQuery(IK_ZONE, zone)
        matches = state.query_cards(query)
    if not matches:
        raise CommandError(
            f"Unknown card '{reference}' for {player.name}. Type hand or status for card IDs."
        )
    if len(matches) > 1:
        ids = ", ".join(card_reference(card) for card in matches)
        raise CommandError(f"Ambiguous card '{reference}'; use a card ID: {ids}.")
    card = matches[0]
    if zone is not None and card.get_zone() != zone:
        raise CommandError(f"{card_reference(card)} is in {card.get_zone().name}, not {zone.name}.")
    return card


def resolve_player(reference: str, state: State, player: Player) -> Player:
    reference = reference.casefold()
    by_id = state.player_register.get_by_key(reference)
    if by_id is not None:
        return by_id
    if reference in {"self", "me"}:
        return player
    if reference == "opponent":
        matches = [candidate for candidate in state.players if candidate is not player]
    else:
        matches = state.query_players(EqQuery(IK_PLAYER_NAME, reference))
    if len(matches) != 1:
        raise CommandError(
            f"Unknown or ambiguous player '{reference}'. Use self, opponent or a player name."
        )
    return matches[0]
