"""Sample beliefs without exposing the sampled truth through a player's input."""
from collections import Counter
from dataclasses import replace
import random

import pytest

from benchmarks.search_hidden_worlds import beliefs, catalog, public_position, response_node, run_diagnostic
from benchmarks.search_tactics import tactical_policy
from game.ai.modular_agent import ModularAgent
from game.ai.sampled_scenario import HiddenZoneBelief, SampledScenario
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_simulator import SearchSimulator
from game.ai.prefix_replay_simulator import PrefixReplaySimulator


def scenario(seed=0, evidence=None, factory=public_position, observer=0):
    return SampledScenario(factory, catalog, beliefs() if evidence is None else evidence,
                           observer=observer, seed=seed)


@pytest.mark.parametrize("backend", [SearchSimulator, PrefixReplaySimulator])
def test_unknown_hands_and_library_orders_do_not_change_root_player_input(backend):
    roots, inputs, samples = [], [], []
    global_rng = random.getstate()
    for seed in range(12):
        factory = scenario(seed)
        sim = backend(factory, policy_for=tactical_policy)
        root = sim.start()
        assert all(c["name"] != "Shock" for c in root.observation(1)["hand"])
        assert "Shock" not in str(root.observation(1))
        roots.append(root)
        samples.append(factory.samples)
        inputs.append(ActingPlayerObservation.from_node(root))
        assert sim.fork(root) == root
        reply = response_node(sim, root)
        actor = ActingPlayerObservation.from_node(reply)
        assert actor.player == 1
        assert [c["name"] for c in actor.observation()["hand"]] == list(factory.samples[1].hand)
        assert "initial_search_evidence" not in actor.observation()
        assert all(c["name"] != "Shock" for c in actor.observation()["hand"])
        assert any(c.kind == "AbilityAction" for c in reply.choices) == ("Counterspell" in factory.samples[1].hand)
        assert sim.fork(reply) == reply
    assert len(set(inputs)) == 1
    assert len({s[1].hand for s in samples}) == 2
    assert len({s[0].library for s in samples}) > 1
    # Strict replay keys include both private hands, so must NOT be tree keys.
    assert len({r.decision.observation_digest for r in roots}) > 1
    assert random.getstate() == global_rng


def test_known_cards_composition_and_engine_draw_order_are_preserved():
    evidence = (replace(beliefs()[0], known_top=("Plains", "Island")),
                replace(beliefs()[1], known_hand=("Counterspell",), known_top=("Island",)))
    factory = scenario(evidence=evidence)
    first = factory((ModularAgent(), ModularAgent()))
    second = factory((ModularAgent(), ModularAgent()))
    for owner, (belief, sampled) in enumerate(zip(evidence, factory.samples)):
        player = first.state.players[owner]
        assert tuple(c.name for c in reversed(tuple(player.deck.values()))) == sampled.library
        assert tuple(c.name for c in player.hand.values()) == sampled.hand
        assert Counter(sampled.hand + sampled.library) == dict(belief.counts)
        assert sampled.library[:len(belief.known_top)] == belief.known_top
        assert all(a is not b for a, b in zip(player.deck.values(), second.state.players[owner].deck.values()))
    assert first.state.rng is not second.state.rng
    assert first.state.rng.getstate() == second.state.rng.getstate()
    first.state.players[0].health = 1
    assert second.state.players[0].health == 20
    sim = SearchSimulator(factory, policy_for=tactical_policy)
    root = sim.start()
    known = root.observation(0)["initial_search_evidence"]["players"]
    assert [c["name"] for c in known[1]["known_hand"]] == ["Counterspell"]
    assert [c["name"] for c in known[0]["known_library_top"]] == ["Plains", "Island"]
    assert "initial_search_evidence" not in root.observation(1)


def test_draw_uses_sampled_top_and_opponent_does_not_receive_its_identity():
    from game.enums import TurnPhase
    def start_at_draw(controllers):
        game = public_position(controllers)
        game.state.turn.phase = TurnPhase.DRAW
        return game
    evidence = (replace(beliefs()[0], known_top=("Plains",)), beliefs()[1])
    sim = SearchSimulator(scenario(evidence=evidence, factory=start_at_draw), policy_for=tactical_policy)
    root = sim.start()
    assert {c["name"] for c in root.observation(0)["hand"]} == {"Shock", "Plains"}
    assert not root.observation(1).get("initial_search_evidence")
    own = root.observation(0)["knowledge"]["recent_events"]
    opponent = root.observation(1)["knowledge"]["recent_events"]
    assert next(e for e in own if e["kind"] == "card_drawn")["name"] == "Plains"
    drawn = next(e for e in opponent if e["kind"] == "card_drawn")
    assert drawn["name"] is None and drawn["card_id"] is None


def test_keys_exclude_privileged_metadata_but_include_private_evidence_and_request():
    sim = SearchSimulator(scenario(), policy_for=tactical_policy)
    root = sim.start()
    actor = ActingPlayerObservation.from_node(root)
    disguised = replace(root, history=("private history",), choices=(),
                        decision=replace(root.decision, observation_digest="hidden", menu_digest="hidden"))
    assert ActingPlayerObservation.from_node(disguised).key == actor.key
    changed_request = replace(root, decision=replace(root.decision, kind="ScryRequest"))
    assert ActingPlayerObservation.from_node(changed_request).key != actor.key
    view = actor.observation()
    view["hand"].clear()
    assert actor.observation()["hand"]  # Copies returned to policies are detached.
    evidence = (replace(beliefs()[0], known_top=("Plains",)), beliefs()[1])
    informed = SearchSimulator(scenario(evidence=evidence), policy_for=tactical_policy).start()
    assert ActingPlayerObservation.from_node(informed).key != actor.key
    with pytest.raises(ValueError, match="decision node"):
        ActingPlayerObservation.from_node(replace(root, decision=None))


def test_observer_one_gets_only_their_assumptions_and_private_hand():
    evidence = (replace(beliefs()[0], known_hand=()), replace(beliefs()[1], known_hand=("Counterspell",)))
    sim = SearchSimulator(scenario(evidence=evidence, observer=1), policy_for=tactical_policy)
    root = sim.start()
    assert "initial_search_evidence" not in root.observation(0)
    info = root.observation(1)["initial_search_evidence"]["players"]
    assert info[0]["known_hand"] == [] and info[1]["known_hand"][0]["name"] == "Counterspell"


def test_root_private_hand_changes_do_not_change_opponent_policy_input():
    roots, opponents = [], []
    for own_hand in (("Shock",), ("Plains",)):
        evidence = (replace(beliefs()[0], known_hand=own_hand), beliefs()[1])
        sim = SearchSimulator(scenario(evidence=evidence), policy_for=tactical_policy)
        root = sim.start()
        roots.append(ActingPlayerObservation.from_node(root))
        passed = sim.advance(root, next(c for c in root.choices if c.kind == "PassPriorityAction"))
        assert passed.decision.player == 1
        opponents.append(ActingPlayerObservation.from_node(passed))
    assert roots[0].key != roots[1].key
    assert opponents[0] == opponents[1]


def test_sampled_hands_do_not_rebuild_or_remove_public_attachments():
    from game.enums import ZoneType
    from game.game_state import Card
    def attached(controllers):
        game = public_position(controllers)
        p = game.state.players[1]
        angel = Card(catalog()["Serra Angel"], p)
        p.add_card(angel, ZoneType.BATTLEFIELD)
        aura = Card(catalog()["Pacifism"], game.state.players[0])
        aura.owner.add_card(aura, ZoneType.BATTLEFIELD)
        aura.attach(angel, game.state)
        return game
    sim = SearchSimulator(scenario(factory=attached), policy_for=tactical_policy)
    root = sim.start()
    board = root.observation(0)["battlefield"]
    angel = next(c for c in board if c["name"] == "Serra Angel")
    aura = next(c for c in board if c["name"] == "Pacifism")
    assert aura["attached_to"]["card"] == angel["id"]
    assert "can't attack" in angel["keywords"] and "can't block" in angel["keywords"]
    assert sim.fork(root) == root


def test_invalid_evidence_or_populated_hidden_zones_fail_explicitly():
    with pytest.raises(ValueError, match="entire hand"):
        scenario(evidence=(replace(beliefs()[0], known_hand=()), beliefs()[1]))
    with pytest.raises(ValueError, match="exceeds"):
        replace(beliefs()[1], known_hand=("Shock",))
    with pytest.raises(ValueError, match="Duplicate"):
        HiddenZoneBelief((("Island", 2), ("Island", 3)), 1)
    with pytest.raises(ValueError, match="integer"):
        scenario(seed=True)
    def populated(controllers):
        from game.game_state import Card
        game = public_position(controllers)
        p = game.state.players[0]
        p.add_card(Card(catalog()["Island"], p))
        return game
    with pytest.raises(ValueError, match="empty"):
        scenario(factory=populated)((ModularAgent(), ModularAgent()))


def test_hidden_world_diagnostic():
    result = run_diagnostic(8)
    assert result["passed"] and result["distinct_root_observations"] == 1
    assert 0 < result["sampled_counterspell_hands"] < 8
