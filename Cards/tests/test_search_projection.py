"""Projection reuse must preserve visibility, fresh reads and wire identities."""
from dataclasses import asdict, replace
from hashlib import sha256
import json

from game.ai.observation import PlayerObservationBuilder
from game.ai.search_actions import canonical_json
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_recall import PlayerInformation
from game.ai.structured_values import text
from game.game_state import Card
from game.enums import ZoneType, CounterType
from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.modular_agent import ModularAgent
from game.ai.search_simulator import SearchSimulator


def test_build_reuse_matches_uncached_description_and_does_not_cross_observers_or_mutations():
    game = tactical_scenario("answered_shock")((ModularAgent(), ModularAgent()))
    state = game.state
    original = next(iter(state.players[0].hand.values()))
    twin = Card(original.definition, original.owner)
    original.owner.add_card(twin, ZoneType.HAND)
    hidden = next(iter(state.players[1].hand.values()))

    class Probe(PlayerObservationBuilder):
        def replacements(self):
            # Same object at different nesting depths must retain truncation.
            payload = {"own_card": original, "other_card": hidden, "nested": [1, 2]}
            return [self.describe(payload), self.describe(payload, 10)]

    class Uncached(Probe):
        def build(self, *, update_knowledge=True):
            return self._build(update_knowledge=update_knowledge)

    for player in state.players:
        builder = Probe(state, player)
        view = builder.build(update_knowledge=False)
        assert view == Uncached(state, player).build(update_knowledge=False)
        refs = view["replacement_effects"][0]
        assert (refs["own_card"] is not None) == (player is original.owner)
        assert (refs["other_card"] is not None) == (player is hidden.owner)
        assert view["replacement_effects"][1]["description_unavailable"]
        # Policy mutation of returned dictionaries cannot poison a later build.
        for card in view["hand"]:
            card["abilities"].clear()
        # Use a counter that does not add power to this noncreature spell.
        original.state.counters[CounterType.LOYALTY] = original.state.counters.get(CounterType.LOYALTY, 0) + 1
        assert builder.build(update_knowledge=False) == Uncached(state, player).build(update_knowledge=False)


def test_information_reuse_keeps_hashes_serialization_and_detached_policy_inputs():
    sim = SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy,
                          track_information=True)
    node = sim.start()
    info = node.player_information()
    before = asdict(info)
    reference = PlayerInformation(ActingPlayerObservation.from_node(node), node.recall(0), info.actions)
    assert info == reference
    expected_current = sha256(json.dumps((info.current.player, info.current.request_kind,
                                         text(info.current._json)), separators=(",", ":")).encode()).hexdigest()
    action_keys = [sha256(canonical_json((a.kind, a._json)).encode()).hexdigest() for a in info.actions]
    knowledge = sha256(canonical_json((2, expected_current, info.recall.digest)).encode()).hexdigest()
    menu = sha256(canonical_json(sorted(action_keys)).encode()).hexdigest()
    expected = sha256(canonical_json((2, knowledge, menu)).encode()).hexdigest()
    assert info.knowledge_key == knowledge and info.menu_digest == menu
    assert info.key == expected and [a.key for a in info.actions] == action_keys
    assert asdict(info) == before
    info.current.observation().clear()
    info.actions[0].describe().clear()
    info.recall.entries[0].describe().clear()
    assert node.player_information() == reference and node.player_information().key == expected
    # Replacing immutable fields cannot inherit a previously computed digest.
    changed = replace(info, current=replace(info.current, _json='{"self":0}'))
    assert changed.key != expected


def test_raw_view_projection_preserves_reference_json_roundtrip():
    from game.ai.search_decision import DecisionProjector
    from game.ai.search_recall import PlayerRecall
    from game.ai.observation import decision_observation
    from game.ai.decision_maker import PriorityDecisionRequest
    game = tactical_scenario("answered_shock")((ModularAgent(), ModularAgent()))
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    options = tuple(request.option_space(tactical_policy(request)))
    view = decision_observation(request)
    raw = DecisionProjector(request, options, PlayerRecall(), view=view).build()
    old = DecisionProjector(request, options, PlayerRecall(), view=json.loads(json.dumps(view, sort_keys=True))).build()
    assert raw.information == old.information
    assert raw.information.key == old.information.key
    assert raw._bindings == old._bindings


def test_retained_replay_view_is_normalized_and_detached_from_policy_information():
    from game.ai.search_decision import DecisionProjector
    from game.ai.search_recall import PlayerRecall
    from game.ai.observation import decision_observation
    from game.ai.decision_maker import PriorityDecisionRequest
    game = tactical_scenario("answered_shock")((ModularAgent(), ModularAgent()))
    request = PriorityDecisionRequest(game.state, game.state.players[0])
    options = tuple(request.option_space(tactical_policy(request)))
    view = decision_observation(request)
    # Preserve JSON normalization (tuples become lists, integer keys become
    # strings). Simply forwarding the input dict would change recall hashes.
    view["probe"] = {10: ("x",), 2: ("y",)}
    prior = PlayerRecall()
    reference = DecisionProjector(request, options, prior, view=view).build()
    retained = DecisionProjector(request, options, prior, view=view, retain_view=True).build()
    assert reference._view is None
    assert retained.information == reference.information
    assert retained._view == reference.information.current.observation()
    key = retained.information.key
    retained._view["probe"].clear()
    view["probe"].clear()
    assert retained.information.key == key
    assert retained.information.current.observation()["probe"] == {"10": ["x"], "2": ["y"]}
    assert retained.information.recall == reference.information.recall


def test_replay_reuses_decoded_view_without_changing_reference_nodes(monkeypatch):
    from game.ai import search_simulator
    original = search_simulator.DecisionProjector.build
    def roundtrip(projector):
        result = original(projector)
        return replace(result, _view=result.information.current.observation())
    settings = dict(policy_for=tactical_policy, track_information=True, max_phases=8)
    optimized = SearchSimulator(tactical_scenario("answered_shock"), **settings)
    first = optimized.start()
    children = [optimized.advance(first, choice) for choice in first.choices]
    monkeypatch.setattr(search_simulator.DecisionProjector, "build", roundtrip)
    baseline = SearchSimulator(tactical_scenario("answered_shock"), **settings)
    root = baseline.start()
    assert root == first
    assert [baseline.advance(root, choice) for choice in root.choices] == children
