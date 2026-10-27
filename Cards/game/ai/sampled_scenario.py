"""Explicit hidden-zone beliefs -> a fixed, fresh replay scenario.

This is a scenario-construction adapter, not a live-State determinizer. The
public factory owns rules, attachments, counters and the starting continuation.
It must return before requesting a decision, with both hand/library zones empty.
"""
from dataclasses import dataclass
from random import Random

from game.ai.belief import HiddenCardPool, KnownDeckModel


@dataclass(frozen=True)
class HiddenZoneBelief:
    """!
    @brief One player's remaining original-deck copies, supplied by the observer.

    Known hand and top cards are included in counts, not removed from it. Other
    cards have the exchangeability assumptions of HiddenCardPool. This does not
    infer evidence from runtime cards, tokens or transformed/copy identities.
    @var counts Counts retained by this record.
    @var hand_size Hand size retained by this record.
    @var known_hand Known hand retained by this record.
    @var known_top Known top retained by this record.
    """
    counts: tuple[tuple[str, int], ...]
    hand_size: int
    known_hand: tuple[str, ...] = ()
    known_top: tuple[str, ...] = ()

    def __post_init__(self):
        pairs = tuple(self.counts)
        if len(dict(pairs)) != len(pairs):
            raise ValueError("Duplicate card names in belief counts")
        pool = HiddenCardPool(dict(pairs))
        object.__setattr__(self, "counts", tuple(sorted(pool.counts.items())))
        object.__setattr__(self, "known_hand", tuple(self.known_hand))
        object.__setattr__(self, "known_top", tuple(self.known_top))
        # Validate constraints once; sampling below uses its own private RNG.
        pool.sample(self.hand_size, rng=Random(0), known_hand=self.known_hand, known_top=self.known_top)

    @classmethod
    def from_deck(cls, counts, *, hand_size, known_outside=(), known_hand=(), known_top=()):
        """!
        @brief Build baseline hidden-zone evidence from an explicitly supplied decklist.
        """
        pool = KnownDeckModel(counts).remaining(known_outside)
        return cls(tuple(pool.counts.items()), hand_size, tuple(known_hand), tuple(known_top))

    def sample(self, rng):
        """!
        @brief Sample a hand/library assignment from explicitly supplied baseline evidence.
        """
        return HiddenCardPool(dict(self.counts)).sample(
            self.hand_size, rng=rng, known_hand=self.known_hand, known_top=self.known_top)


class SampledScenario:
    """!
    @brief Freeze one belief sample, then rebuild that same world on every replay.

    public_factory(controllers) and catalog_factory() follow SearchSimulator's
    freshness/no-I/O contract. The public factory must not run setup/mulligans or
    advance the loop. It can build an ongoing public position, including attachments.
    Hidden cards are fresh, unmodified original-deck copies only. Public/outside
    evidence is the caller's responsibility; this adapter cannot identify tokens.

    Only observer receives the supplied composition assumptions and known-card
    evidence. Every controller still sees its own actual sampled hand through the
    ordinary observation boundary. Do not pass this privileged object to a policy.
    """
    def __init__(self, public_factory, catalog_factory, beliefs, *, observer, seed):
        beliefs = tuple(beliefs)
        if len(beliefs) != 2 or any(not isinstance(b, HiddenZoneBelief) for b in beliefs):
            raise ValueError("Provide one HiddenZoneBelief for each of two players")
        if type(observer) is not int or observer not in (0, 1):
            raise ValueError("observer must be player 0 or 1")
        if type(seed) is not int:
            raise ValueError("A deterministic integer sample seed is required")
        if len(beliefs[observer].known_hand) != beliefs[observer].hand_size:
            raise ValueError("The observer's entire hand must be known")
        self.public_factory, self.catalog_factory = public_factory, catalog_factory
        self.beliefs, self.observer = beliefs, observer
        rng = Random(seed)
        self.samples = tuple(b.sample(rng) for b in beliefs)

    def __call__(self, controllers):
        from game.enums import ZoneType
        from game.game_state import Card

        game = self.public_factory(controllers)
        players = game.state.players
        if len(players) != 2 or tuple(p.controller for p in players) != tuple(controllers):
            raise ValueError("Public factory must install the supplied two controllers")
        if any(p.hand or p.deck for p in players):
            raise ValueError("Public factory must leave hand/library zones empty")
        catalog = self.catalog_factory()
        missing = {name for sample in self.samples for name in (*sample.hand, *sample.library)} - catalog.keys()
        if missing:
            raise ValueError(f"Sampled cards missing from catalog: {sorted(missing)}")

        def add(owner, name, zone):
            card = Card(catalog[name], players[owner])
            players[owner].add_card(card, zone)
            return card

        # Register all hand slots before library slots. Known cards are a fixed
        # prefix, and unknown hand order is immaterial. Visible IDs consequently
        # don't depend on sampled library permutations or hidden card names.
        hands = []
        for owner, (belief, sample) in enumerate(zip(self.beliefs, self.samples)):
            known = len(belief.known_hand)
            names = (*sample.hand[:known], *sorted(sample.hand[known:]))
            hands.append(tuple(add(owner, name, ZoneType.HAND) for name in names))
        libraries = []
        for owner, sample in enumerate(self.samples):
            # Engine draws the last inserted card; belief library is top first.
            cards = tuple(add(owner, name, ZoneType.DECK) for name in reversed(sample.library))
            libraries.append(tuple(reversed(cards)))

        def refs(cards):
            return [{"id": c.command_id, "name": c.name} for c in cards]

        # Historical evidence, not a claim that these cards remain in these
        # zones after play. Never put an unknown sampled hand/top in this record.
        controllers[self.observer].initial_search_evidence = {
            "scope": "At scenario origin; composition is an explicitly supplied belief, not a runtime inspection.",
            "players": [{"player": owner, "remaining_composition": dict(b.counts),
                         "hand_size": b.hand_size,
                         "known_hand": refs(hands[owner][:len(b.known_hand)]),
                         "known_library_top": refs(libraries[owner][:len(b.known_top)])}
                        for owner, b in enumerate(self.beliefs)],
        }
        return game
