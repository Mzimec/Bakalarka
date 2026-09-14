"""Seeded two-player game preparation and London mulligan (CR 103.5).

Pregame changes deliberately do not emit gameplay draw/zone events. Special
opening-hand abilities and alternative mulligan formats are outside this API.
"""

from dataclasses import dataclass
from random import Random

from game.cards.decks import DeckList
from game.enums import ZoneType
from game.game_state import Card, Player, State

__all__ = [
    "SetupConfig",
    "create_game",
    "shuffle_library",
    "bottom_cards",
]


@dataclass(frozen=True)
class SetupConfig:
    """!
    @brief Immutable configuration for pregame setup.

    @var starting_life
        Initial life total assigned to each player.
    @var opening_hand_size
        Number of cards drawn for each opening hand.
    @var minimum_deck_size
        Minimum legal deck size enforced during deck resolution.
    @var maximum_copies
        Maximum number of copies of one card, or `None` for no limit.
    """

    starting_life: int = 20
    opening_hand_size: int = 7
    minimum_deck_size: int = 60
    maximum_copies: int | None = 4

    def __post_init__(self):
        """!
        @brief Validate setup configuration values.

        @throws ValueError If any configured limit is malformed or inconsistent.
        """
        for field in (self.starting_life, self.minimum_deck_size):
            if type(field) is not int or field < 1:
                raise ValueError(
                    "Starting life and minimum deck size must be positive integers."
                )

        if type(self.opening_hand_size) is not int or self.opening_hand_size < 0:
            raise ValueError("Opening hand size must be a nonnegative integer.")

        if self.minimum_deck_size < self.opening_hand_size:
            raise ValueError("The deck must be large enough for an opening hand.")

        if self.maximum_copies is not None and (
            type(self.maximum_copies) is not int
            or self.maximum_copies < 1
        ):
            raise ValueError("Maximum copies must be positive or None.")


def shuffle_library(player, rng):
    """!
    @brief Shuffle a player's existing library without replacing card identities.

    The deck container stores the top card last, so only insertion order is
    randomized while the original runtime `Card` objects are preserved.

    @param player Player whose library is shuffled.
    @param rng Random generator controlling deterministic shuffle order.
    """
    cards = list(player.deck.values())
    rng.shuffle(cards)

    player.deck.clear()
    player.deck.update(
        (card.key, card)
        for card in cards
    )


def bottom_cards(player, cards):
    """!
    @brief Put selected hand cards on the bottom of the library.

    `cards` is supplied in future draw order. Since the library's top is stored
    last, the selected sequence is reversed before being prepended to the
    existing library.

    @param player Player moving cards from hand to library.
    @param cards Distinct hand cards in desired future draw order.
    @throws ValueError If a card is duplicated or is not currently in hand.
    """
    cards = tuple(cards)

    if len(set(cards)) != len(cards) or any(
        player.hand.get(card.key) is not card
        for card in cards
    ):
        raise ValueError(
            "Choose distinct cards from your hand to put on the bottom."
        )

    library = tuple(player.deck.values())

    for card in cards:
        player.move_card(
            card,
            ZoneType.DECK,
        )

    player.deck.clear()
    player.deck.update(
        (card.key, card)
        for card in (*reversed(cards), *library)
    )


def _opening_hands(state, config):
    """!
    @brief Draw opening hands and perform simultaneous London mulligans.

    Players are processed in active-player-first order. Each mulligan round
    first collects every remaining player's decision, then redraws all players
    who chose to mulligan, then collects and applies their bottom-card choices.
    This prevents one player's replacement hand from influencing another
    player's decision in the same round.

    @param state Prepared game state with libraries and RNG.
    @param config Setup configuration.
    @return Mulligan count for each player in original state order.
    """
    players = (
        state.players[state.active_player_idx :]
        + state.players[: state.active_player_idx]
    )

    counts = {
        player: 0
        for player in players
    }

    for player in players:
        shuffle_library(
            player,
            state.rng,
        )

        for _ in range(config.opening_hand_size):
            player.draw(state)

    remaining = list(players)

    while remaining:
        # Everyone declares before anyone sees their replacement hand.
        redraw = []

        for player in remaining:
            choose = getattr(
                player.controller,
                "choose_mulligan",
                None,
            )

            take = (
                choose(
                    state,
                    player,
                    counts[player],
                )
                if choose
                and counts[player] < config.opening_hand_size
                else False
            )

            if type(take) is not bool:
                raise ValueError(
                    "Mulligan decision must be True or False."
                )

            if take:
                redraw.append(player)

        # All players taking this mulligan redraw only after every decision for
        # the current round has already been fixed.
        for player in redraw:
            for card in tuple(player.hand.values()):
                player.move_card(
                    card,
                    ZoneType.DECK,
                    state,
                )

            shuffle_library(
                player,
                state.rng,
            )

            for _ in range(config.opening_hand_size):
                player.draw(state)

            counts[player] += 1

        # Bottom the required cards after every mulligan before asking whether
        # that player wants to mulligan again.
        choices = []

        for player in redraw:
            choose = getattr(
                player.controller,
                "choose_mulligan_bottom",
                None,
            )

            cards = tuple(
                choose(
                    state,
                    player,
                    counts[player],
                )
                if choose
                else tuple(player.hand.values())[: counts[player]]
            )

            if (
                len(cards) != counts[player]
                or len(set(cards)) != len(cards)
                or any(
                    player.hand.get(
                        getattr(card, "key", None)
                    )
                    is not card
                    for card in cards
                )
            ):
                raise ValueError(
                    "Select exactly one distinct hand card per mulligan taken."
                )

            choices.append(
                (player, cards)
            )

        for player, cards in choices:
            bottom_cards(
                player,
                cards,
            )

        # Only players who mulliganed remain eligible for another mulligan round.
        remaining = redraw

    return tuple(
        counts[player]
        for player in state.players
    )


def create_game(
    decks: tuple[DeckList, DeckList],
    controllers,
    catalog,
    *,
    seed=None,
    starting_player_idx=None,
    names=("Alice", "Bob"),
    config=SetupConfig(),
):
    """!
    @brief Build and prepare a deterministic two-player game state.

    Decks are validated and resolved first. If no starting player is supplied,
    a seeded coin flip chooses a winner who may select the starting player
    through `choose_starting_player(players)`. The choice is made before runtime
    cards are created or opening hands are drawn.

    Pregame shuffling, drawing and mulligan movement intentionally bypass normal
    gameplay event semantics.

    @param decks Exactly two deck lists.
    @param controllers Exactly two player controllers.
    @param catalog Card-definition catalog used to resolve deck lists.
    @param seed Optional RNG seed for reproducible setup.
    @param starting_player_idx Explicit starting player index, or `None`.
    @param names Two player names.
    @param config Pregame setup configuration.
    @return Fully prepared `State` ready for `GameLoop.run`.
    @throws ValueError If setup arguments or controller choices are invalid.
    """
    if len(decks) != 2 or len(controllers) != 2 or len(names) != 2:
        raise ValueError(
            "Game setup supports exactly two players."
        )

    if starting_player_idx is not None and (
        type(starting_player_idx) is not int
        or starting_player_idx not in (0, 1)
    ):
        raise ValueError(
            "Starting player index must be 0 or 1."
        )

    resolved = [
        deck.resolve(
            catalog,
            minimum_size=config.minimum_deck_size,
            maximum_copies=config.maximum_copies,
        )
        for deck in decks
    ]

    # One RNG instance drives all setup randomness, making the entire pregame
    # sequence reproducible from the supplied seed.
    rng = Random(seed)

    players = tuple(
        Player(
            [],
            controller,
            idx=index,
            name=names[index],
        )
        for index, controller in enumerate(controllers)
    )

    if starting_player_idx is None:
        winner = rng.randrange(2)

        choose = getattr(
            controllers[winner],
            "choose_starting_player",
            None,
        )

        starter = (
            choose(players)
            if choose
            else players[winner]
        )

        if not any(
            starter is player
            for player in players
        ):
            raise ValueError(
                "Choose one of the two players to start."
            )

        starting_player_idx = players.index(starter)

    # Runtime card identities are created only after the starting-player choice,
    # so that choice cannot depend on opening library identities or order.
    for index, (player, definitions) in enumerate(
        zip(players, resolved)
    ):
        player.health = config.starting_life

        for number, definition in enumerate(definitions):
            player.add_card(
                Card(
                    definition,
                    player,
                    key=f"p{index}-card{number}",
                ),
                ZoneType.DECK,
            )

    state = State(
        list(players),
        active_player_idx=starting_player_idx,
    )

    state.rng = rng
    state.setup_seed = seed
    state.mulligans_taken = _opening_hands(
        state,
        config,
    )

    # Setup mutates zones directly; refresh derived register state once before
    # normal gameplay begins.
    state.synchronise_registers()

    return state