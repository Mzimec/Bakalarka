"""Beliefs use supplied evidence only and preserve a finite deck's copy counts."""
from collections import Counter
from random import Random

import pytest

from game.ai.belief import HiddenCardPool, KnownDeckModel


def test_known_deck_removes_only_explicit_evidence_and_copies_inputs():
    counts = {"land": 5, "answer": 3}
    model = KnownDeckModel(counts)
    counts["land"] = 99
    pool = model.remaining(["land", "answer"])
    assert dict(pool.counts) == {"land": 4, "answer": 2}
    assert dict(model.counts) == {"land": 5, "answer": 3}
    with pytest.raises(ValueError, match="exceeds"):
        model.remaining(["token"])


@pytest.mark.parametrize("seed", range(20))
def test_sampling_preserves_counts_known_hand_and_library_prefix(seed):
    pool = HiddenCardPool({"land": 6, "answer": 3, "threat": 2})
    sample = pool.sample(4, rng=Random(seed), known_hand=["answer"], known_top=["threat", "land"])
    assert len(sample.hand) == 4 and len(sample.library) == 7
    assert "answer" in sample.hand and sample.library[:2] == ("threat", "land")
    assert Counter(sample.hand + sample.library) == pool.counts
    assert sample == pool.sample(4, rng=Random(seed), known_hand=["answer"], known_top=["threat", "land"])


def test_probabilities_condition_on_known_cards_and_marginalize_unknown_hand():
    pool = HiddenCardPool({"land": 3, "answer": 2})
    assert pool.probability_in_hand("answer", 2) == pytest.approx(0.7)
    assert pool.probability_next_draw("answer", 2) == pytest.approx(0.4)
    assert pool.probability_in_hand("answer", 2, known_hand=["land"]) == pytest.approx(0.5)
    assert pool.probability_next_draw("answer", 2, known_hand=["answer"]) == pytest.approx(0.25)
    assert pool.probability_in_hand("answer", 2, known_top=["answer"]) == pytest.approx(0.5)
    assert pool.probability_in_hand("answer", 2, known_hand=["answer"]) == 1
    assert pool.probability_next_draw("answer", 2, known_top=["land"]) == 0
    assert pool.probability_next_draw("answer", 2, known_top=["answer"]) == 1
    assert pool.probability_next_draw("answer", 5) == 0
    assert pool.probability_in_hand("missing", 5) == 0
    assert HiddenCardPool({}).probability_in_hand("answer", 0) == 0
    assert HiddenCardPool({}).probability_next_draw("answer", 0) == 0


@pytest.mark.parametrize("hand,known,top", [(6, (), ()), (-1, (), ()),
    (0, ("land",), ()), (4, (), ("land", "land")),
    (3, ("answer", "answer"), ("answer",)), (1, ("missing",), ())])
def test_inconsistent_evidence_is_rejected_by_sampling_and_probabilities(hand, known, top):
    pool = HiddenCardPool({"land": 3, "answer": 2})
    with pytest.raises(ValueError):
        pool.sample(hand, rng=Random(1), known_hand=known, known_top=top)
    for method in (pool.probability_in_hand, pool.probability_next_draw):
        with pytest.raises(ValueError):
            method("answer", hand, known_hand=known, known_top=top)


@pytest.mark.parametrize("counts", [{"land": -1}, {"land": 1.5}, {"land": True}, {"": 1}])
def test_invalid_counts(counts):
    with pytest.raises(ValueError):
        KnownDeckModel(counts)
