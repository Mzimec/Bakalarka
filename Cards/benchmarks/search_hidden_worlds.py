"""Visibility and replay diagnostic over explicit sampled hidden worlds."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from random import Random
from time import perf_counter

from benchmarks.search_tactics import tactical_policy
from game.ai.sampled_scenario import HiddenZoneBelief, SampledScenario
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_simulator import SearchSimulator


def catalog():
    from game.cards.starter_cards import starter_catalog
    from game.cards.control_cards import control_catalog
    return {**starter_catalog(), **control_catalog()}


def public_position(controllers):
    from game.console.demo_game import DemoGame
    from game.enums import ZoneType, TurnPhase
    from game.game_state import Card, Player, State
    from game.game_loop.game_loop import GameLoop
    from game.game_actions.resolution.action_processor import ActionProcessor
    from game.game_actions.resolution.operation_executor import OperationExecutor
    from game.game_actions.resolution.resolution_engine import ResolutionEngine
    from game.game_actions.resolution.event_bus import EventBus

    state = State([Player([], c, idx=i) for i, c in enumerate(controllers)])
    state.rng = Random(11)  # Independent of the hidden-world sampling RNG.
    state.turn.number = 5
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    state.players[1].health = 2
    definitions = catalog()
    for owner, name in ((0, "Mountain"), (1, "Island"), (1, "Island")):
        card = Card(definitions[name], state.players[owner])
        card.owner.add_card(card, ZoneType.BATTLEFIELD)
        card.controlled_since = 0
    bus = EventBus()
    loop = GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus)))
    return DemoGame(state, bus, loop)


def beliefs():
    return (
        HiddenZoneBelief.from_deck({"Mountain": 1, "Shock": 1, "Island": 2, "Plains": 1},
                                  hand_size=1, known_outside=("Mountain",), known_hand=("Shock",)),
        HiddenZoneBelief.from_deck({"Island": 5, "Counterspell": 1},
                                  hand_size=1, known_outside=("Island", "Island")),
    )


def response_node(simulator, root, shared_action=None):
    cast = next(c for c in root.choices if c.kind == "AbilityAction")
    node = (simulator.advance_action(root, shared_action) if shared_action is not None
            else simulator.advance(root, cast))
    while node.decision.player == 0:
        node = simulator.advance(node, next(c for c in node.choices if c.kind == "PassPriorityAction"))
    return node


def run_diagnostic(samples=32, *, track_information=False):
    if type(samples) is not int or samples < 1:
        raise ValueError("samples must be a positive integer")
    rows, root_keys, root_choices, root_view = [], set(), None, None
    information_keys, shared_info, shared_action = set(), None, None
    started = perf_counter()
    for seed in range(samples):
        scenario = SampledScenario(public_position, catalog, beliefs(), observer=0, seed=seed)
        sim = SearchSimulator(scenario, policy_for=tactical_policy, track_information=track_information)
        root = sim.start()
        actor = ActingPlayerObservation.from_node(root)
        if root_view is None:
            root_view, root_choices = actor.observation(), root.choices
        assert actor.observation() == root_view and root.choices == root_choices
        root_keys.add(actor.key)
        if track_information:
            info = root.player_information()
            if shared_info is None:
                shared_info = info
                shared_action = next(a for a in info.actions if a.kind == "AbilityAction")
            assert info == shared_info
            information_keys.add(info.key)
        reply = response_node(sim, root, shared_action)
        responder = ActingPlayerObservation.from_node(reply)
        assert responder.player == 1
        assert "initial_search_evidence" not in responder.observation()
        can_counter = any(c.kind == "AbilityAction" for c in reply.choices)
        assert can_counter == ("Counterspell" in scenario.samples[1].hand)
        assert sim.fork(reply) == reply and sim.fork(root) == root
        for belief, sample in zip(scenario.beliefs, scenario.samples):
            assert Counter(sample.hand + sample.library) == dict(belief.counts)
        rows.append({"seed": seed, "root_observation_key": actor.key,
                     "response_observation_key": responder.key, "can_counter": can_counter,
                     "sampled_world_for_debug_only": [asdict(s) for s in scenario.samples]})
        if track_information:
            reply_info = reply.player_information()
            assert all(e.kind != "action" for e in reply_info.recall.entries)
            rows[-1].update({"root_information_key": info.key, "reply_information_key": reply_info.key,
                             "reply_recall_entries": len(reply_info.recall.entries),
                             "offered_reply_actions": len(reply_info.actions)})
    return {"passed": True, "samples": samples, "distinct_root_observations": len(root_keys),
            "sampled_counterspell_hands": sum(row["can_counter"] for row in rows),
            "model_probability_counterspell_in_hand": 0.25,
            "information_tracking": track_information,
            "distinct_root_information_keys": len(information_keys) if track_information else None,
            "elapsed_ms": (perf_counter() - started) * 1000,
            "scope": "Controlled fresh scenarios; visibility/count/replay checks, not ISMCTS or a strength comparison. Sampled worlds are privileged debug data.",
            "root_player_input": root_view, "worlds": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--information", action="store_true", help="Validate player recall and cross-world action binding")
    args = parser.parse_args()
    result = run_diagnostic(args.samples, track_information=args.information)
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-hidden-worlds-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"PASS: {result['samples']} worlds, {result['distinct_root_observations']} root view, "
          f"{result['sampled_counterspell_hands']} Counterspell hands; {result['elapsed_ms']:.1f} ms")
    print(path)


if __name__ == "__main__":
    main()
