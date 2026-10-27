"""Compare a new history reconstruction with updating an existing finite population."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from benchmarks.live_history_search import simulator, scenario, passing
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.observation_policy import ObservationOpponentPolicy


def targets():
    seed = next(n for n in range(100) if "Counterspell" in scenario(n).samples[1].hand)
    sim, policy = simulator(seed), ObservationOpponentPolicy()
    node = sim.start()
    for choose in (policy, passing, policy, passing):
        node = sim.advance_action(node, choose(node.player_information()))
    before = node.player_information()
    node = sim.advance_action(node, policy(before))
    return before, node.player_information()


def run(*, size=8, repeats=3):
    if any(type(n) is not int or n < 1 for n in (size, repeats)):
        raise ValueError("Size and repeats must be positive integers")
    before, after = targets()
    rows = []
    for repeat in range(repeats):
        incremental = IncrementalWorldProvider(simulator, size=size, seed=42 + repeat, max_attempts=128)
        initial = incremental(before)(0)
        started = perf_counter()
        initial.start()
        initial_ms = (perf_counter() - started) * 1000
        for mode in (("fresh", "incremental") if repeat % 2 == 0 else ("incremental", "fresh")):
            factory = (HistoryWorldProvider(simulator, max_attempts=128)(after) if mode == "fresh" else incremental(after))
            metrics = []
            started = perf_counter()
            for i in range(size):
                sample = factory((repeat + 1) * 1000 + i)
                if sample.start().player_information() != after:
                    raise AssertionError("Updated root no longer matches player evidence")
                metrics.append(asdict(sample.initialization_metrics))
            elapsed = (perf_counter() - started) * 1000
            rows.append({"mode": mode, "repeat": repeat, "elapsed_ms": elapsed,
                         "metrics": {k: sum(m[k] for m in metrics) for k in metrics[0]},
                         "previous_population_setup_ms": initial_ms if mode == "incremental" else None})
    return {"scope": "Belief update after one additional own cast; equal population size, different approximation semantics. Not whole-search timing.",
            "passed": True, "size": size, "repeats": repeats,
            "fresh_median_ms": median(r["elapsed_ms"] for r in rows if r["mode"] == "fresh"),
            "incremental_median_ms": median(r["elapsed_ms"] for r in rows if r["mode"] == "incremental"),
            "samples": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = run(size=args.size, repeats=args.repeats)
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("incremental-belief-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"PASS: fresh {result['fresh_median_ms']:.1f} ms, incremental {result['incremental_median_ms']:.1f} ms\n{path}")


if __name__ == "__main__":
    main()
