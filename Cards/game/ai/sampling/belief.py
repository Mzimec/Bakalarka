"""Exact-deck baseline over explicit evidence, never a runtime library reader."""
from collections import Counter
from dataclasses import dataclass
from math import comb
from types import MappingProxyType


def _counts(values):
    result = dict(values)
    if any(type(name) is not str or not name or type(n) is not int or n < 0 for name, n in result.items()):
        raise ValueError("Card counts require nonempty names and nonnegative integers.")
    return {name: n for name, n in result.items() if n}


def _subtract(counts, cards):
    remaining = Counter(counts)
    for name in cards:
        if remaining[name] <= 0:
            raise ValueError(f"Evidence exceeds the available copies of {name!r}.")
        remaining[name] -= 1
    return +remaining


@dataclass(frozen=True)
class SampledHiddenCards:
    """!
    @brief One joint sample of a hidden hand and top-first library.
    @var hand Jointly sampled hidden hand identities.
    @var library Sampled library identities in top-first order.
    """
    hand: tuple[str, ...]
    library: tuple[str, ...]  # Top card first.


class KnownDeckModel:
    """!
    @brief An explicitly supplied decklist, not an inference from observed names.
    """
    def __init__(self, counts):
        self.counts = MappingProxyType(_counts(counts))

    def remaining(self, known_outside=()):
        """!
        @brief Remove proven original-deck copies outside hand/library.

        Tokens and copied names must not be fed in as original-deck evidence.
        Knowledge aggregation and unknown-deck inference are separate adapters.
        """
        return HiddenCardPool(_subtract(self.counts, known_outside))


class HiddenCardPool:
    """!
    @brief Joint hand/library pool under an exchangeability baseline.

    Condition on explicitly known hand cards and a top-of-library prefix. Other
    unknown cards are uniformly shuffled without replacement. Opponent choices,
    mulligans and unknown scry history are not inferred by this baseline model.
    """
    def __init__(self, counts):
        self.counts = MappingProxyType(_counts(counts))

    def _condition(self, hand_size, known_hand, known_top):
        if type(hand_size) is not int or not len(known_hand) <= hand_size <= sum(self.counts.values()) - len(known_top):
            raise ValueError("Inconsistent hand size and known cards.")
        return _subtract(self.counts, (*known_hand, *known_top)), hand_size - len(known_hand)

    def sample(self, hand_size, *, rng, known_hand=(), known_top=()):
        """!
        @brief Jointly sample unknown hand and library identities without replacement.
        """
        known_hand, known_top = tuple(known_hand), tuple(known_top)
        remaining, unknown_hand = self._condition(hand_size, known_hand, known_top)
        cards = list(sorted(remaining.elements()))
        rng.shuffle(cards)
        return SampledHiddenCards((*known_hand, *cards[:unknown_hand]), (*known_top, *cards[unknown_hand:]))

    def probability_in_hand(self, name, hand_size, *, known_hand=(), known_top=()):
        """!
        @brief Compute baseline probability that the hidden hand contains a named card.
        """
        known_hand, known_top = tuple(known_hand), tuple(known_top)
        remaining, draws = self._condition(hand_size, known_hand, known_top)
        if name in known_hand:
            return 1.0
        total, copies = sum(remaining.values()), remaining[name]
        return 1.0 - comb(total - copies, draws) / comb(total, draws)

    def probability_next_draw(self, name, hand_size, *, known_hand=(), known_top=()):
        """!
        @brief Compute baseline next-draw probability under the known-card constraints.
        """
        known_hand, known_top = tuple(known_hand), tuple(known_top)
        remaining, unknown_hand = self._condition(hand_size, known_hand, known_top)
        if known_top:
            return float(known_top[0] == name)
        total = sum(remaining.values())
        if total == unknown_hand:  # Library empty: no card can be drawn.
            return 0.0
        return remaining[name] / total
