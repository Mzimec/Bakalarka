"""Native historical facts preserve rules without copying hidden draw objects."""
from types import SimpleNamespace

import pytest

from game.ai.runtime_clone import RuntimeClone
from game.ai.search_simulator import SearchSimulator, SimulationHistoryUnavailable
from game.game_actions.resolution.event_bus import GameEvent
from game.game_state.turn_history import TurnHistory, TurnHistoryUnavailable
from game.game_state.player import Player
from test_current_position_worlds import position, prepare, controllers


def damage(source, target, amount=1, life_loss=0, sr=3, tr=4):
    return GameEvent("damage_dealt", source, payload=dict(
        target_object=target, amount=amount, life_loss=life_loss,
        source_revision=sr, target_revision=tr))


@pytest.mark.parametrize("amount", [0, -1, 1, 2.5, float("nan")])
@pytest.mark.parametrize("life_loss", [0, 1, float("nan")])
def test_projected_facts_match_live_queries(amount, life_loss):
    source, target = object(), Player([], None)
    archive = TurnHistory([damage(source, target, amount, life_loss)])
    compact = archive.for_simulation()
    assert compact.lost_life(target) == archive.lost_life(target)
    for sr, tr in ((3, 4), (2, 4), (3, 5)):
        assert compact.dealt_damage(source, sr, target, tr) == archive.dealt_damage(source, sr, target, tr)
    assert not compact.dealt_damage(object(), 3, target, 4)


@pytest.mark.parametrize("key", ["life_paid", "life_lost"])
def test_life_payment_controller_and_target_are_preserved(key):
    one, two = Player([], None), Player([], None)
    archive = TurnHistory([GameEvent(key, controller=one, payload=dict(amount=2, target_object=two))])
    compact = archive.for_simulation()
    assert compact.lost_life(two)
    assert compact.lost_life(one) == (key == "life_paid")


def test_incarnations_and_identity_survive_hash_collisions_and_unhashable_sources():
    class Collision:
        def __hash__(self): return 1
        def __eq__(self, other): return True
    one, two, target = Collision(), Collision(), object()
    unhashable = []
    history = TurnHistory([damage(one, target), damage(unhashable, target)]).for_simulation()
    assert history.dealt_damage(one, 3, target, 4)
    assert not history.dealt_damage(two, 3, target, 4)
    assert history.dealt_damage(unhashable, 3, target, 4)
    assert not history.dealt_damage([], 3, target, 4)


def test_live_mutable_archive_and_explicit_compact_archive_failure():
    player = Player([], None)
    archive = TurnHistory()
    archive.events.append(GameEvent("life_lost", payload=dict(amount=1, target_object=player)))
    compact = archive.for_simulation()
    archive.events.clear()
    assert not archive.lost_life(player) and compact.lost_life(player)
    with pytest.raises(TurnHistoryUnavailable):
        _ = compact.events
    compact.reset()
    assert not compact.lost_life(player) and not compact.retains_archive


def test_unknown_event_subclasses_keep_full_copy_and_fail_closed_when_emitted_in_compact_world():
    class ExtensionEvent(GameEvent): pass
    event = ExtensionEvent("card_drawn")
    assert TurnHistory([event]).for_simulation() is None
    compact = TurnHistory(retain_archive=False)
    with pytest.raises(TurnHistoryUnavailable, match="own fact adapter"):
        compact.record(event)


def test_compact_history_clone_rebinds_references_and_isolates_mutations():
    player = Player([], None)
    source, target = SimpleNamespace(), SimpleNamespace()
    compact = TurnHistory([damage(source, target),
        GameEvent("life_lost", payload=dict(amount=1, target_object=player))]).for_simulation()
    clone = RuntimeClone()
    other = clone.copy(compact)
    s, t, p = (clone.copy(x) for x in (source, target, player))
    assert other.dealt_damage(s, 3, t, 4) and other.lost_life(p)
    assert not other.dealt_damage(source, 3, target, 4)
    other.reset()
    assert compact.dealt_damage(source, 3, target, 4) and compact.lost_life(player)


def test_hidden_draw_archive_does_not_prevent_exact_current_root_sampling():
    origin, game = position()
    state = game.state
    hidden = next(iter(state.players[1].hand.values()))
    event = GameEvent("card_drawn", hidden, state.players[1], payload={"last_known": hidden})
    state.turn_events = [event]
    state._history_turn = state.turn.number
    factory, info, snapshot = prepare(origin, game)
    for seed in range(4):
        assert factory(seed).start().player_information() == info
    assert state.turn_events == [event] and state.turn_events[0].source is hidden
    sampled = snapshot.materialize(controllers(), 23).state
    with pytest.raises(TurnHistoryUnavailable):
        _ = sampled.turn_events
    player = sampled.players[0]
    sampled.record_turn_event(GameEvent("life_lost", payload=dict(amount=1, target_object=player)))
    assert sampled.turn_history.lost_life(player)
    sampled.turn.number += 1
    sampled.record_turn_event(GameEvent("phase_started"))
    assert not sampled.turn_history.lost_life(player)
    assert not sampled.turn_history.retains_archive


def test_native_damage_trigger_and_life_loss_rules_match_in_sampled_state():
    from game.enums import ZoneType
    from game.cards.starter_extensions import sengir_condition
    from game.cards.starter_support import lost_life_this_turn
    from game.game_actions.resolution.event_bus import capture_single_card
    origin, game = position()
    state = game.state
    source = next(c for c in state.players[0].get_cards() if c.name == "Charmed Stray")
    target = next(c for c in state.players[1].get_cards() if c.name == "Charmed Stray")
    for card in (source, target):
        card.owner.move_card(card, ZoneType.BATTLEFIELD)
    lki = capture_single_card(state, target)
    hit = damage(source, target, sr=source.zone_revision, tr=target.zone_revision)
    life = damage(source, state.players[1], life_loss=1, sr=source.zone_revision)
    state.turn_events = [hit, life]
    state._history_turn = state.turn.number
    target.owner.move_card(target, ZoneType.GRAVEYARD)
    death = GameEvent("card_moved", target, payload=dict(
        to="GRAVEYARD", **{"from": "BATTLEFIELD"}, last_known=lki))
    trigger = SimpleNamespace(event=death, source=source, source_revision=source.zone_revision)
    assert sengir_condition(state, trigger)
    assert lost_life_this_turn(state, state.players[1])
    _, _, snapshot = prepare(origin, game)
    sampled = snapshot.materialize(controllers(), 123).state
    sampled_source = next(c for c in sampled.players[0].get_cards() if c.key == source.key)
    sampled_target = next(c for c in sampled.players[1].get_cards() if c.key == target.key)
    sampled_death = GameEvent("card_moved", sampled_target, payload=death.payload)
    sampled_trigger = SimpleNamespace(event=sampled_death, source=sampled_source,
                                     source_revision=source.zone_revision)
    assert sengir_condition(sampled, sampled_trigger)
    assert lost_life_this_turn(sampled, sampled.players[1])
    assert not lost_life_this_turn(sampled, sampled.players[0])
    sampled_trigger.source_revision += 1
    assert not sengir_condition(sampled, sampled_trigger)


def test_irrelevant_hidden_draw_definition_is_never_read_by_snapshot(monkeypatch):
    from game.game_state.card import Card
    origin, game = position()
    hidden = next(iter(game.state.players[1].hand.values()))
    game.state.turn_events = [GameEvent("card_drawn", hidden, hidden.owner)]
    original = Card.definition
    def forbid(card):
        if card is hidden:
            raise AssertionError("Read historical hidden identity")
        return original.__get__(card, Card)
    monkeypatch.setattr(Card, "definition", property(forbid))
    factory, info, _ = prepare(origin, game)
    assert factory(77).start().player_information() == info


def test_unknown_hidden_damage_provenance_still_requires_an_adapter():
    from game.ai.current_position_worlds import CurrentPositionWorlds
    from game.ai.decision_maker import PriorityDecisionRequest
    from game.ai.search_decision import DecisionProjector
    from game.ai.search_recall import PlayerRecall
    from game.simulation.search_config import search_generation_policy
    origin, game = position()
    state = game.state
    hidden = next(iter(state.players[1].hand.values()))
    state.turn_events = [damage(hidden, state.players[0])]
    request = PriorityDecisionRequest(state, state.players[0])
    prior = PlayerRecall()
    info = DecisionProjector(request, tuple(request.option_space(search_generation_policy(request))), prior).build().information
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    assert provider.prepare(request, info, prior, state.players[0].controller.knowledge) is None
    assert provider.unsupported_reason.startswith("unsupported_snapshot")


def test_raw_history_extension_fails_as_explicit_simulation_limitation():
    def extension(controllers):
        history = TurnHistory(retain_archive=False)
        _ = history.events
    with pytest.raises(SimulationHistoryUnavailable, match="Raw turn-event"):
        SearchSimulator(extension).start()


@pytest.mark.parametrize("stage", ["start", "advance"])
def test_uncaptured_history_is_not_a_game_loss_or_world_sampling_rejection(stage):
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from test_information_set_search import Tree, first
    rows = {"root": {"children": {"a": "root", "b": "root"}}}
    root = Tree(rows).start().player_information()
    class Extension(Tree):
        def start(self):
            if stage == "start": raise SimulationHistoryUnavailable("extension")
            return super().start()
        def advance_action(self, *args):
            raise SimulationHistoryUnavailable("extension")
    result = InformationSetSearch(opponent_policy=first,
        budget=InformationSearchBudget(worlds=1, iterations=2)).search(lambda seed: Extension(rows), root)
    assert result.used_fallback and result.fallback_reason == "unsupported_historical_information"
    assert result.metrics.iterations_completed == 0 and result.value is None
    assert all(e.visits == 0 for e in result.estimates)
    assert result.stop_reason == "unsupported_historical_information"
    assert result.metrics.iterations_started == (0 if stage == "start" else 1)
