"""Alternating copy timings at one naturally reached midgame priority window."""
import argparse
from copy import copy
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter, process_time

from benchmarks.priority_window import WindowAgent
from game.ai.simple_agent import SimpleAgent
from game.ai.current_position_worlds import _Snapshot
from game.ai.game_origin import GameOrigin
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter
from game.enums import TurnPhase
from game.simulation.match_runner import run_match
from game.simulation.search_config import search_generation_policy


def capture_snapshot(turn=9):
    """Shared reproducible midgame fixture for native copy experiments."""
    agent = WindowAgent(turn, TurnPhase.PRECOMBAT_MAIN, 1)
    run_match(("white", "white"), None, seed=3, starting_player=0,
              controllers=(agent, SimpleAgent()), collect_decision_stats=False)
    request = agent.captured
    if request is None:
        raise RuntimeError("No priority window reached")
    origin = GameOrigin((load_arena_starter("white"),) * 2, game_catalog)
    prior = PlayerRecall()
    options = tuple(request.option_space(search_generation_policy(request)))
    info = DecisionProjector(request, options, prior).build().information
    snapshot = _Snapshot(request, info, prior, agent.knowledge, origin)
    return request, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turn", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    if args.turn < 1 or args.repetitions < 2:
        parser.error("Positive turn and at least two repetitions required")
    request, snapshot = capture_snapshot(args.turn)
    reference = copy(snapshot)
    reference.immutable_values, reference.dictionary_plans = {}, None
    snapshot.copy_state()  # Warm recipes; preparation is reported separately by full games.
    rows = {"reference": [], "prepared": []}
    sources = [("reference", reference), ("prepared", snapshot)]
    for iteration in range(args.repetitions):
        for name, source in (sources if iteration % 2 == 0 else sources[::-1]):
            # Collect unreachable previous ownership graphs outside the timer.
            gc.collect()
            wall, cpu = perf_counter(), process_time()
            restored = source.copy_state()
            rows[name].append({"wall_ms": (perf_counter() - wall) * 1000,
                               "cpu_ms": (process_time() - cpu) * 1000})
            assert restored is not source.state
            assert restored.turn.number == source.state.turn.number
            del restored
    report = {"scope": "Warmed filtered-root copy only, alternating order; GC before each sample, outside timing. Excludes capture, preparation, sampling, registration and rule replay. Not a whole-game speedup.",
              "runtime": {"python": platform.python_version(), "hash_seed": os.environ.get("PYTHONHASHSEED", "random")},
              "turn": request.state.turn.number, "phase": request.state.turn.phase.name,
              "public_root_cards": sum(len(p.get_cards()) for p in snapshot.state.players),
              "samples": rows, "median_ms": {name: median(r["wall_ms"] for r in samples)
                                              for name, samples in rows.items()}}
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("world-root-copy-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    (folder / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(folder)
    print(report["median_ms"])


if __name__ == "__main__":
    main()
