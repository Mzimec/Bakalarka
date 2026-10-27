from game.ai.observation import observation
from test_player_observation import board, add, find
from game.enums import ZoneType


def test_shared_raw_characteristics_preserve_every_observer_view_and_hidden_reference():
    state = board()
    secret = add(state, "Private", ZoneType.HAND, player=0)
    public = add(state, "Public", power=3, toughness=4)
    # Opaque extension data still has the normal viewer-specific adapter.
    public.state.skip_untap = (123, 0, {"reference": secret})
    expected = [observation(state, p, update_knowledge=False) for p in state.players]
    cache = {}
    actual = [observation(state, p, update_knowledge=False, shared_characteristics=cache) for p in state.players]
    assert actual == expected
    assert find(actual[0], public)["skip_untap"][2]["reference"]["card"] == secret.command_id
    assert find(actual[1], public)["skip_untap"][2]["reference"] is None
    assert cache
    find(actual[0], public)["types"].append("fake")
    assert "fake" not in find(actual[1], public)["types"]


def test_each_new_projection_gets_fresh_characteristics():
    state = board()
    card = add(state, "Creature", power=2, toughness=3)
    before = observation(state, state.players[0], shared_characteristics={})
    card.state.damage_marked = 2
    after = observation(state, state.players[0], shared_characteristics={})
    assert find(before, card)["damage_marked"] == 0
    assert find(after, card)["damage_marked"] == 2


def test_public_characteristics_are_read_once_per_joint_projection(monkeypatch):
    from game.ai.observation import PlayerObservationBuilder
    state = board()
    public = add(state, "Shared", power=2, toughness=2)
    calls = []
    original = PlayerObservationBuilder._identity_values
    def read(builder, card):
        calls.append(card)
        return original(builder, card)
    monkeypatch.setattr(PlayerObservationBuilder, "_identity_values", read)
    cache = {}
    for player in state.players:
        observation(state, player, shared_characteristics=cache)
    assert calls == [public]
