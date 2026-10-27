"""Recall and visible choices must be private, complete and locally rebound."""
from dataclasses import replace

import pytest

from benchmarks.search_hidden_worlds import public_position, catalog, beliefs
from benchmarks.search_tactics import tactical_policy
from game.ai.sampled_scenario import SampledScenario
from game.ai.search_actions import PlayerActionBuilder, UnsupportedPlayerAction
from game.ai.search_recall import PlayerRecall
from game.ai.search_simulator import SearchSimulator, starter_scenario
from game.ai.prefix_replay_simulator import PrefixReplaySimulator
from game.ai.modular_agent import ModularAgent


def simulator(seed=0, *, backend=SearchSimulator, evidence=None):
    factory = SampledScenario(public_position, catalog, beliefs() if evidence is None else evidence,
                              observer=0, seed=seed)
    return backend(factory, policy_for=tactical_policy, track_information=True)


def action(node, kind):
    return next(a for a in node.player_information().actions if a.kind == kind)


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_shared_root_information_and_actions_rebind_to_each_sampled_world(backend):
    shared, responses = None, []
    for seed in range(12):
        sim = simulator(seed, backend=backend)
        root = sim.start()
        info = root.player_information()
        assert len(info.recall.entries) == 1
        if shared is None:
            shared = info
        assert info == shared and info.key == shared.key
        assert not root.recall(1).entries
        # Choose using another world's policy action; bind to this world's raw
        # descriptor and execute through the real engine, including mana costs.
        cast = next(a for a in shared.actions if a.kind == "AbilityAction")
        paid = sim.advance_action(root, cast)
        assert paid.observation(0)["pending_abilities"]
        assert next(c for c in paid.observation(0)["battlefield"] if c["name"] == "Mountain")["tapped"]
        own_actions = [r for r in paid.recall(0).entries if r.kind == "action"]
        assert own_actions[0].describe() == cast.describe()
        reply = sim.advance_action(paid, action(paid, "PassPriorityAction"))
        assert reply.decision.player == 1
        responder = reply.player_information()
        assert "initial_search_evidence" not in responder.current.observation()
        assert all(r.kind != "action" for r in responder.recall.entries)
        has_counter = any(c["name"] == "Counterspell" for c in responder.current.observation()["hand"])
        assert any(a.kind == "AbilityAction" for a in responder.actions) == has_counter
        responses.append(responder)
        assert sim.fork(reply) == reply and sim.fork(root) == root
        # Sibling replay cannot modify the previous branch's recall.
        snapshot = reply.player_information()
        sim.advance_action(root, action(root, "PassPriorityAction"))
        assert reply.player_information() == snapshot
    assert len({r.key for r in responses}) == 2


def test_opponent_cannot_recall_root_private_hand_or_root_choices():
    opponents, own = [], []
    for hand in (("Shock",), ("Plains",)):
        evidence = (replace(beliefs()[0], known_hand=hand), beliefs()[1])
        sim = simulator(evidence=evidence)
        root = sim.start()
        own.append(root.player_information())
        next_node = sim.advance_action(root, action(root, "PassPriorityAction"))
        opponents.append(next_node.player_information())
    assert own[0].key != own[1].key
    assert opponents[0] == opponents[1]
    assert "Shock" not in str(opponents[0]) and "Plains" not in str(opponents[0])


def scry_factory(reverse=False):
    def build(controllers):
        from game.game_state import Card
        from game.enums import ZoneType
        from game.cards.starter_support import ScryOperation
        from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
        game = public_position(controllers)
        player = game.state.players[0]
        cards = [Card(catalog()[name], player) for name in ("Shock", "Plains")]
        for card in cards:
            player.add_card(card, ZoneType.DECK)
        if reverse:
            player.deck.clear()
            player.deck.update((c.key, c) for c in reversed(cards))
        context = ResolutionContext(controller=player)
        game.loop.processor.executor.resolve(game.state,
            ScheduledResolution(FixedExecutionPlan([ScryOperation(context, 2)]), context))
        return game
    return build


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_scry_private_observation_is_remembered_after_current_views_converge(backend):
    endpoints, opponent_inputs = [], []
    for reverse in (False, True):
        sim = backend(scry_factory(reverse), policy_for=tactical_policy, track_information=True)
        root = sim.start()
        assert root.decision.kind == "ScryRequest"
        assert root.recall(1).entries == ()
        choices = root.player_information().actions
        chosen = next(a for a in choices if [c["name"] for c in a.describe()["top"]] == ["Shock", "Plains"])
        arranged = sim.advance_action(root, chosen)
        endpoints.append(arranged.player_information())
        assert sim.fork(arranged) == arranged
        passed = sim.advance_action(arranged, action(arranged, "PassPriorityAction"))
        opponent_inputs.append(passed.player_information())
    # Same resulting library and current model input, different past knowledge.
    assert endpoints[0].current == endpoints[1].current
    assert endpoints[0].key != endpoints[1].key
    assert endpoints[0].recall.entries[0] != endpoints[1].recall.entries[0]
    assert opponent_inputs[0] == opponent_inputs[1]
    assert "Shock" not in str(opponent_inputs[0])


def test_recall_has_no_64_event_window_and_copies_input():
    a, b = PlayerRecall(), PlayerRecall()
    payload = {"seen": "Shock"}
    a = a.append("observation", payload)
    b = b.append("observation", {"seen": "Plains"})
    payload["seen"] = "mutated"
    for i in range(80):
        a, b = a.append("event", {"n": i}), b.append("event", {"n": i})
    assert a.entries[1:] == b.entries[1:]
    assert len(a.entries) == 81 and a.digest != b.digest
    assert a.entries[0].describe() == {"seen": "Shock"}
    a.entries[0].describe().clear()
    assert a.entries[0].describe()


def test_action_adapters_reject_hidden_refs_unknown_objects_and_changed_incarnations():
    from game.game_state import Card
    from game.enums import ZoneType
    from game.ai.decision_maker import PriorityDecisionRequest
    from game.game_actions.generation.decision_abstraction.options import DiscardOption
    game = public_position((ModularAgent(), ModularAgent()))
    own, opponent = game.state.players
    hidden = Card(catalog()["Shock"], opponent)
    opponent.add_card(hidden, ZoneType.HAND)
    builder = PlayerActionBuilder(PriorityDecisionRequest(game.state, own))
    with pytest.raises(UnsupportedPlayerAction, match="unobservable"):
        builder.action(DiscardOption((hidden,)))
    with pytest.raises(UnsupportedPlayerAction, match="adapter"):
        builder.action(object())
    visible = Card(catalog()["Shock"], own)
    own.add_card(visible, ZoneType.HAND)
    builder = PlayerActionBuilder(PriorityDecisionRequest(game.state, own))
    before = builder.action(DiscardOption((visible,)))
    own.add_card(visible, ZoneType.GRAVEYARD)
    after = PlayerActionBuilder(PriorityDecisionRequest(game.state, own)).action(DiscardOption((visible,)))
    assert before != after


def test_action_not_offered_or_information_not_enabled_is_rejected():
    sim = simulator()
    root = sim.start()
    chosen = action(root, "AbilityAction")
    after = sim.advance_action(root, chosen)
    with pytest.raises(ValueError, match="not offered"):
        sim.advance_action(after, chosen)
    other = simulator(1)
    with pytest.raises(ValueError, match="another simulator"):
        other.advance_action(root, chosen)
    plain = SearchSimulator(public_position, policy_for=tactical_policy).start()
    with pytest.raises(ValueError, match="track_information"):
        plain.player_information()


def test_setup_land_mana_and_prefix_backends_have_identical_information():
    from benchmarks.search_simulator import diagnostic_choice
    from game.console.demo_game import create_starter_demo_game
    factory = lambda cs: create_starter_demo_game(cs, seed=3, starting_player_idx=None)
    strict = SearchSimulator(factory, track_information=True)
    prefix = PrefixReplaySimulator(factory, track_information=True)
    a, b = strict.start(), prefix.start()
    kinds = set()
    for _ in range(16):
        assert a == b and a.player_information() == b.player_information()
        kinds.update(c.kind for c in a.player_information().actions)
        chosen = diagnostic_choice(a)
        a, b = strict.advance(a, chosen), prefix.advance(b, chosen)
    assert a == b and strict.fork(a) == prefix.fork(b)
    assert {"StartingPlayerOption", "MulliganOption", "LandPlayAction", "ManaAbilityAction"} <= kinds


def test_information_records_projected_draw_events_even_between_decisions():
    from game.enums import TurnPhase
    def draw_position(controllers):
        game = public_position(controllers)
        game.state.turn.phase = TurnPhase.DRAW
        return game
    evidence = (replace(beliefs()[0], known_top=("Plains",)), beliefs()[1])
    factory = SampledScenario(draw_position, catalog, evidence, observer=0, seed=0)
    root = SearchSimulator(factory, policy_for=tactical_policy, track_information=True).start()
    own = [e.describe() for e in root.recall(0).entries if e.kind == "event"]
    other = [e.describe() for e in root.recall(1).entries if e.kind == "event"]
    assert next(e for e in own if e["kind"] == "card_drawn")["name"] == "Plains"
    hidden_draw = next(e for e in other if e["kind"] == "card_drawn")
    assert hidden_draw["name"] is None and hidden_draw["card_id"] is None


def test_player_input_has_no_runtime_objects_or_other_players_replay_data():
    from dataclasses import fields, is_dataclass
    info = simulator().start().player_information()
    def visit(value):
        from game.ai.structured_values import StructuredJSON, RecallDigest, Value
        if type(value) is StructuredJSON:
            visit(value.value)
            return
        if type(value) is RecallDigest:
            visit(value.previous)
            visit(value.kind)
            visit(value.entry)
            return
        if type(value) is Value:
            assert type(value.digest) is bytes
            visit(value.kind)
            visit(value.items)
            return
        if value is None or type(value) in (str, int, float, bool):
            return
        if isinstance(value, tuple):
            for item in value:
                visit(item)
            return
        assert type(value).__module__ in {
            "game.ai.search_recall", "game.ai.search_actions", "game.ai.search_observation"}
        assert is_dataclass(value)
        for f in fields(value):
            visit(getattr(value, f.name))
    visit(info)
    cast = [a for a in info.actions if a.kind == "AbilityAction"]
    assert len(cast) == 2 and cast[0].key != cast[1].key  # Shock's two distinct player targets.
    assert "future_state" not in str(info) and "menu_digest" not in str(info)


def test_ambiguous_projection_does_not_silently_merge_actions(monkeypatch):
    from game.ai.search_actions import PlayerAction
    monkeypatch.setattr(PlayerActionBuilder, "action", lambda self, option: PlayerAction("same", "{}"))
    with pytest.raises(UnsupportedPlayerAction, match="same player identity"):
        simulator().start()
