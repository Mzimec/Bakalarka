from game.ai.history_diagnostics import difference_path, recall_mismatch
from game.ai.search_recall import PlayerRecall


def test_mismatch_records_observer_field_path_without_private_values():
    a = PlayerRecall().append("event", {"card_id": "private-card-a", "name": "Secret A"})
    b = PlayerRecall().append("event", {"card_id": "private-card-b", "name": "Secret B"})
    assert recall_mismatch(a.entries, b.entries) == "event/card_id"
    assert difference_path({"players": [{"hand_size": 6}]}, {"players": [{"hand_size": 7}]}) == "/players/0/hand_size"
    assert difference_path([1], [1, 2]) == "/length"
    assert difference_path({"a": 1}, {}) == "/a"


def test_missing_history_and_entry_kind_are_distinct_from_value_mismatches():
    a = PlayerRecall().append("action", {"kind": "pass"})
    b = PlayerRecall().append("event", {"kind": "draw"})
    assert recall_mismatch(a.entries, b.entries) == "recall/kind"
    assert recall_mismatch(a.entries, ()) == "recall/length"
