"""Cheaper event copies retain immutable payloads and event-time observations."""
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from game.game_actions.resolution import event_bus
from game.game_actions.resolution.event_bus import GameEvent, capture_event, _with_event_data


@pytest.mark.parametrize("changes", [
    {"payload": {"amount": 4}}, {"player_events": ()}, {"triggered_abilities": ()},
    {"payload": {}, "player_events": (), "triggered_abilities": ()},
])
def test_native_copy_matches_replace_and_freezes_new_payload(changes):
    original = GameEvent("damage", object(), object(), {"amount": 2})
    result = _with_event_data(original, **changes)
    expected = replace(original, **changes)
    assert result == expected
    assert result.player_events == expected.player_events
    assert result.triggered_abilities == expected.triggered_abilities
    assert dict(original.payload) == {"amount": 2}
    if "payload" in changes:
        changes["payload"]["later"] = True
        assert "later" not in result.payload
    with pytest.raises(TypeError):
        result.payload["amount"] = 99


def test_subclass_preserves_extra_fields_and_post_init():
    calls = []

    @dataclass(frozen=True)
    class SpecialEvent(GameEvent):
        marker: int = 7

        def __post_init__(self):
            super().__post_init__()
            calls.append(self.marker)

    result = _with_event_data(SpecialEvent("special", marker=12), player_events=())
    assert type(result) is SpecialEvent and result.marker == 12
    assert result.player_events == () and calls == [12, 12]


def test_lki_and_incarnations_are_visible_to_player_projection_before_trigger_capture(monkeypatch):
    source = SimpleNamespace(zone_revision=5)
    old = SimpleNamespace(zone_revision=4)
    player = SimpleNamespace(idx=0, controller=SimpleNamespace(receive_player_event=lambda event: None))
    state = SimpleNamespace(players=(player,))
    seen = []

    def projection(event, state, player):
        seen.append(event)
        assert event.payload["last_known"] is old
        return "visible"

    monkeypatch.setattr("game.ai.knowledge.project_player_event", projection)
    original = GameEvent("card_moved", source, payload={"from": "BATTLEFIELD", "to": "GRAVEYARD"})
    result = capture_event(state, original, before=(), after=(), information={id(source): old})
    assert len(seen) == 1 and "object_revisions" not in seen[0].payload
    assert result.player_events == ((0, "visible"),)
    assert result.payload["last_known"] is old
    assert dict(result.payload["object_revisions"]) == {id(source): 5, id(old): 4}
    assert "last_known" not in original.payload
    assert result.triggered_abilities == ()


def test_already_captured_player_events_are_not_recomputed(monkeypatch):
    source = SimpleNamespace(zone_revision=3)
    old = SimpleNamespace(zone_revision=2)
    event = GameEvent("card_moved", source, player_events=((0, "original observation"),))
    monkeypatch.setattr(event_bus, "replace", lambda *args, **kwargs: pytest.fail("Native capture used dataclass reflection"))
    result = capture_event(SimpleNamespace(), event, before=(), after=(), information={id(source): old})
    assert result.player_events is event.player_events
    assert result.payload["last_known"] is old
    assert result.triggered_abilities == ()


def test_subclass_capture_keeps_original_post_init_sequence():
    observed = []

    @dataclass(frozen=True)
    class SpecialEvent(GameEvent):
        def __post_init__(self):
            super().__post_init__()
            observed.append(("last_known" in self.payload, "object_revisions" in self.payload,
                             self.player_events is not None, self.triggered_abilities is not None))

    source = SimpleNamespace(zone_revision=2)
    old = SimpleNamespace(zone_revision=1)
    result = capture_event(SimpleNamespace(), SpecialEvent("card_moved", source),
                           before=(), after=(), information={id(source): old})
    assert type(result) is SpecialEvent
    assert observed == [(False, False, False, False), (True, False, False, False),
                        (True, False, True, False), (True, True, True, False),
                        (True, True, True, True)]
