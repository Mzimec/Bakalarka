"""Private positional evidence, composition constraints and invalidation."""
import pytest

from game.ai.library_positions import LibraryPositions
from game.ai.runtime_clone import UnsupportedSnapshot
from game.ai.search_recall import PlayerRecall


def observe(recall, kind="PriorityDecisionRequest", *, size=5, revealed=(), hand=()):
    return recall.append("observation", {"request_kind": kind, "view": {
        "players": [{"id": 0, "library_size": size}, {"id": 1, "library_size": 5}],
        "hand": [{"id": ref} for ref in hand],
        "decision_context": {"revealed_cards": [{"id": ref, "name": ref} for ref in revealed]}}})


def scry(recall, *, size=5, revealed=("a", "b"), top=("b",), bottom=("a",)):
    recall = observe(recall, "ScryRequest", size=size, revealed=revealed)
    refs = lambda ids: [{"card": ref, "name": ref} for ref in ids]
    return recall.append("action", {"kind": "ScryOption", "top": refs(top), "bottom": refs(bottom)})


def draw(recall, ref):
    event = {"owner": 0, "card_id": ref, "name": ref, "turn": 2, "phase": "DRAW",
             "from_zone": "DECK", "to_zone": "HAND"}
    return recall.append("event", {"kind": "card_moved", **event}).append(
        "event", {"kind": "card_drawn", **event})


def positions(recall, size=5):
    return LibraryPositions.from_recall(recall, 0, (size, 5)).slots[0]


def test_scry_pins_top_and_bottom_and_draw_pair_shifts_once():
    recall = scry(PlayerRecall())
    assert positions(recall) == ("b", None, None, None, "a")
    recall = draw(recall, "b")
    assert positions(recall, 4) == (None, None, None, "a")
    recall = draw(recall, "c")
    assert positions(recall, 3) == (None, None, "a")


def test_repeated_scry_keeps_deeper_known_cards_and_ordered_bottoms():
    recall = scry(PlayerRecall(), revealed=("a", "b", "c"), top=("a", "b", "c"), bottom=())
    recall = scry(recall, revealed=("a",), top=(), bottom=("a",))
    assert positions(recall) == ("b", "c", None, None, "a")
    recall = scry(recall, revealed=("b", "c"), top=(), bottom=("c", "b"))
    assert positions(recall) == (None, None, "a", "c", "b")


def test_london_bottom_and_repeated_mulligan_reset():
    recall = observe(PlayerRecall(), "MulliganBottomRequest", hand=("a", "b"))
    recall = recall.append("action", {"kind": "MulliganBottomOption",
                                     "cards": [{"card": "a", "name": "a"}, {"card": "b", "name": "b"}]})
    assert positions(recall, 7) == (None,) * 5 + ("a", "b")
    recall = observe(recall, "MulliganRequest", size=7).append("action", {"kind": "MulliganOption", "take_mulligan": True})
    assert positions(recall, 5) == (None,) * 5


def test_unknown_library_change_is_explicit_until_shuffle():
    recall = scry(PlayerRecall()).append("event", {"kind": "card_moved", "owner": 0, "to_zone": "DECK"})
    with pytest.raises(UnsupportedSnapshot, match="library ordering"):
        positions(recall, 6)
    recall = recall.append("event", {"kind": "library_shuffled", "owner": 0})
    assert positions(recall, 6) == (None,) * 6


def test_unknown_removal_with_positional_evidence_is_not_treated_as_a_draw():
    recall = scry(PlayerRecall()).append("event", {"kind": "card_moved", "owner": 0,
        "from_zone": "DECK", "to_zone": "HAND", "card_id": "x"})
    with pytest.raises(UnsupportedSnapshot):
        positions(recall, 4)


def test_removal_of_exact_known_card_preserves_remaining_positions():
    recall = scry(PlayerRecall()).append("event", {"kind": "card_moved", "owner": 0,
        "from_zone": "DECK", "to_zone": "EXILE", "card_id": "a"})
    assert positions(recall, 4) == ("b", None, None, None)


def test_conflicting_draw_identity_and_unobserved_size_change_fail_closed():
    recall = scry(PlayerRecall())
    with pytest.raises(UnsupportedSnapshot):
        positions(draw(recall, "x"), 4)
    with pytest.raises(UnsupportedSnapshot):
        positions(observe(recall, size=4), 4)


def test_opponent_shuffle_does_not_erase_private_scry_knowledge():
    recall = scry(PlayerRecall()).append("event", {"kind": "library_shuffled", "owner": 1})
    assert positions(recall) == ("b", None, None, None, "a")
