"""Explicit game recipes and observation-conditioned opening permutations.

No constructor accepts a live State, match seed, hidden hand or library. Decks
are declared assumptions: callers must explicitly grant the opponent list.
"""
from dataclasses import dataclass
from functools import partial

from game.cards.decks import DeckList
from game.game_loop.setup import SetupConfig, create_game
from game.ai.search_simulator import SearchSimulator
from game.ai.setup_deal_evidence import SetupDealEvidence
from game.ai.draw_prefix_evidence import DrawPrefixEvidence
from game.ai.opening_reveal_evidence import OpeningRevealEvidence


@dataclass(frozen=True)
class _Game:
    """!
    @brief Pair an owned native state with its executable game loop.
    @var state State retained by this record.
    @var bus Bus retained by this record.
    @var loop Loop retained by this record.
    """
    state: object
    bus: object
    loop: object


@dataclass(frozen=True)
class GameOrigin:
    """!
    @brief Recreate ordinary setup, including starting-player choice and mulligans.

    catalog_factory must return fresh definitions/callbacks without external I/O.
    Ordered deck lists and player names are part of the replay identity contract.
    Use an independent sample seed for scenario(); never pass the live match seed
    to a hidden-world provider. This recipe is also usable to create a live game.
    @var decks Decks retained by this record.
    @var catalog_factory Catalog factory retained by this record.
    @var names Names retained by this record.
    @var starting_player_idx Starting player idx retained by this record.
    @var config Config retained by this record.
    """
    decks: tuple[DeckList, DeckList]
    catalog_factory: object
    names: tuple[str, str] = ("Alice", "Bob")
    starting_player_idx: int | None = None
    config: SetupConfig = SetupConfig()

    def __post_init__(self):
        object.__setattr__(self, "decks", tuple(self.decks))
        object.__setattr__(self, "names", tuple(self.names))
        if len(self.decks) != 2 or any(not isinstance(d, DeckList) for d in self.decks):
            raise ValueError("Declare exactly two deck lists")
        if len(self.names) != 2 or any(type(n) is not str for n in self.names):
            raise ValueError("Supply exactly two player names")
        if not callable(self.catalog_factory):
            raise TypeError("Supply a fresh catalog factory")
        if not isinstance(self.config, SetupConfig):
            raise TypeError("Supply SetupConfig")
        if self.starting_player_idx is not None and (
            type(self.starting_player_idx) is not int or self.starting_player_idx not in (0, 1)
        ):
            raise ValueError("Starting player must be 0, 1 or None")

    def scenario(self, seed, *, opening_hand_ids=None, mulligan_hand_ids=None, draw_prefix=None,
                 opening_draw_deadlines=None):
        """!
        @brief Create the executable sampled scenario for this explicit game origin.
        """
        if type(seed) is not int:
            raise ValueError("A deterministic integer sample seed is required")
        hands = (None if opening_hand_ids is None else
                 tuple(None if hand is None else tuple(hand) for hand in opening_hand_ids))
        redraws = (None if mulligan_hand_ids is None else
                   tuple(() if rounds is None else tuple(None if hand is None else tuple(hand)
                         for hand in rounds) for rounds in mulligan_hand_ids))
        if draw_prefix is not None and not isinstance(draw_prefix, DrawPrefixEvidence):
            raise TypeError("Supply immutable draw-prefix evidence")
        return partial(self._build, seed=seed, opening_hand_ids=hands, mulligan_hand_ids=redraws,
                       draw_prefix=draw_prefix, opening_draw_deadlines=(None if opening_draw_deadlines is None
                           else tuple(tuple(tuple(pair) for pair in rows) for rows in opening_draw_deadlines)))

    def _build(self, controllers, *, seed, opening_hand_ids, mulligan_hand_ids, draw_prefix, opening_draw_deadlines):
        from game.game_loop.game_loop import GameLoop
        from game.game_actions.resolution.action_processor import ActionProcessor
        from game.game_actions.resolution.event_bus import EventBus
        from game.game_actions.resolution.operation_executor import OperationExecutor
        from game.game_actions.resolution.resolution_engine import ResolutionEngine

        state = create_game(self.decks, controllers, self.catalog_factory(), seed=seed,
                            names=self.names, starting_player_idx=self.starting_player_idx,
                            config=self.config, opening_hand_ids=opening_hand_ids,
                            mulligan_hand_ids=mulligan_hand_ids, opening_draw_deadlines=opening_draw_deadlines)
        if draw_prefix is not None:
            draw_prefix.apply(state)
        bus = EventBus()
        loop = GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus)))
        return _Game(state, bus, loop)


class GameOriginWorlds:
    """!
    @brief Fresh tracking simulators, optionally conditioned on own setup deals.

    HistoryWorldProvider (also used by IncrementalWorldProvider) calls condition
    once per target. Each setup shuffle returns the full own deck to a uniform
    permutation. Pinning each observed complete own deal has the same probability
    for every opponent world/path, so rejection of the remaining history still
    preserves its likelihood. Unseen deals and remaining permutations stay random.
    With the native library-event contract, known own top draws before the first
    library change/inspection also pin an exogenous initial residual prefix.
    The first public opponent hand exit can additionally constrain an initial
    permutation prefix, only for an observed zero-mulligan, untouched opening.
    This is a necessary condition of matching history, not a forced opponent play.
    Other evidence and all opponent choices still require history rejection.
    Uninstrumented custom library mutations require condition_draws=False.
    """
    def __init__(self, origin, *, policy_for=None, max_options=4096,
                 max_decisions=10000, max_phases=1000, condition_draws=True, condition_opening_reveal=True):
        if not isinstance(origin, GameOrigin):
            raise TypeError("Supply an explicit GameOrigin")
        self.origin = origin
        if type(condition_draws) is not bool:
            raise TypeError("condition_draws must be boolean")
        self.condition_draws = condition_draws
        if type(condition_opening_reveal) is not bool:
            raise TypeError("condition_opening_reveal must be boolean")
        self.condition_opening_reveal = condition_opening_reveal
        self.options = dict(policy_for=policy_for, max_options=max_options,
                            max_decisions=max_decisions, max_phases=max_phases,
                            track_information=True)

    def __call__(self, seed, *, opening_hand_ids=None, mulligan_hand_ids=None, draw_prefix=None,
                 opening_draw_deadlines=None):
        return SearchSimulator(self.origin.scenario(seed, opening_hand_ids=opening_hand_ids,
                               mulligan_hand_ids=mulligan_hand_ids, draw_prefix=draw_prefix,
                               opening_draw_deadlines=opening_draw_deadlines), **self.options)

    def condition(self, information):
        """!
        @brief Build a world factory conditioned on the supplied player information.
        """
        evidence = SetupDealEvidence.from_information(information, hand_size=self.origin.config.opening_hand_size)
        if evidence is not None:
            prefix = DrawPrefixEvidence.from_information(information, evidence) if self.condition_draws else None
            reveal = (OpeningRevealEvidence.from_information(information, hand_size=self.origin.config.opening_hand_size)
                      if self.condition_opening_reveal else None)
            return partial(self, **evidence.constraints(), draw_prefix=prefix,
                           opening_draw_deadlines=reveal.constraints() if reveal else None)
        # Stateless start choice, zero-card opening, or absent initial evidence:
        # use the declared prior and let strict history matching decide.
        return self
