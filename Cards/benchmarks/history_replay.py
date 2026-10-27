"""Compare prefix reconstruction by repeated replay and one forward runtime."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.scenario_world_provider import resume_to_information
from game.ai.search_simulator import SearchSimulator


def passing(info):
    return next((a for a in info.actions if a.kind == "PassPriorityAction"), info.actions[0])


class Stepwise:
    """Expose the reference protocol without the optimized optional method."""
    def __init__(self, simulator):
        self.simulator = simulator

    def start(self):
        return self.simulator.start()

    def advance_action(self, node, action):
        return self.simulator.advance_action(node, action)


def run(lengths=(8, 24, 48), repeats=3):
    if any(type(n) is not int or n < 1 for n in (*lengths, repeats)):
        raise ValueError("Lengths and repeats must be positive integers")
    recipe = tactical_scenario("answered_shock")
    records = []
    for length in lengths:
        sim = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True)
        target_node = sim.follow_actions(lambda n: None if len(n.history) == length else passing(n.player_information()))
        if target_node.terminal or target_node.cutoff:
            raise ValueError("Requested history exceeds this diagnostic scenario")
        target = target_node.player_information()
        rows = []
        for repeat in range(repeats):
            # Alternate order; report individual timings as well as medians.
            for backend in (("stepwise", "forward") if repeat % 2 == 0 else ("forward", "stepwise")):
                builds = []
                def counted(controllers):
                    builds.append(1)
                    return recipe(controllers)
                sim = SearchSimulator(counted, policy_for=tactical_policy, track_information=True)
                runner = Stepwise(sim) if backend == "stepwise" else sim
                started = perf_counter()
                node = resume_to_information(runner, target, passing, length + 1)
                elapsed = (perf_counter() - started) * 1000
                if node != target_node:
                    raise AssertionError("Backend changed history, observations, choices or recall")
                rows.append({"backend": backend, "repeat": repeat, "elapsed_ms": elapsed, "runtime_builds": len(builds)})
        before = median(r["elapsed_ms"] for r in rows if r["backend"] == "stepwise")
        after = median(r["elapsed_ms"] for r in rows if r["backend"] == "forward")
        records.append({"history_decisions": length, "stepwise_median_ms": before, "forward_median_ms": after,
                        "speedup": before / after, "samples": rows})
    return {"scope": "Strict prefix initialization only, fixed recipe; not total MCTS or full-game speedup.",
            "passed": True, "repeats": repeats, "results": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lengths", type=int, nargs="+", default=[8, 24, 48])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = run(tuple(args.lengths), args.repeats)
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("history-replay-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for row in result["results"]:
        print(f"PASS {row['history_decisions']} decisions: {row['stepwise_median_ms']:.1f} -> "
              f"{row['forward_median_ms']:.1f} ms ({row['speedup']:.2f}x)")
    print(path)


if __name__ == "__main__":
    main()
