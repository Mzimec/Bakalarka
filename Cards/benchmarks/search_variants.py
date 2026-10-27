"""Interleaved real-engine UCT/flat PUCT/hierarchical PUCT diagnostics.

Equal beliefs, seed, leaf evaluator, opponent, depth and work budgets. Search
time includes world setup. Controlled tactical fixtures do not measure match
strength, a whole game's speed, or equal work to an upstream MageZero simulation.
"""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
import platform
from pathlib import Path
from statistics import median
from time import perf_counter_ns, process_time_ns

from benchmarks.engine_inner import fingerprint
from benchmarks.information_set_search import CASES, world_factory
from game.ai.action_priors import HeuristicActionPrior
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_encoding import ActionEncoder, SCHEMA_VERSION

VARIANTS = ("uct", "puct", "hierarchical_puct")


def run_live(output, *, seeds=(42,)):
    """Complete logged production games using the actual comparable presets."""
    from game.simulation.config import run_config
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    initial_fingerprint = fingerprint(root)
    matches = []
    for seed in seeds:
        for name in ("uct", "flat_puct", "hierarchical_puct"):
            settings = json.loads((root / "configs" / f"mcts_{name}.json").read_text())
            config = {"seed": seed, "starting_player": 0, "max_turns": 80, "max_decisions": 3000,
                "players": [{"name": name, "deck": "red", "agent": {"type": "mcts",
                    "mcts": {"deck_knowledge": "both_lists", **settings}}},
                    {"name": "Simple", "deck": "white", "agent": {"type": "simple"}}]}
            path = output / f"{name}-{seed}.json"
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            print(f"Starting {name}, seed={seed}", flush=True)
            match = run_config(path, output=output / f"{name}-{seed}.jsonl")
            matches.append({"variant": name, **asdict(match)})
            print(f"{name}: {match.status}, {match.elapsed_seconds:.3f}s", flush=True)
            # Retain completed games if a later process is interrupted.
            (output / "matches.json").write_text(json.dumps(matches, indent=2), encoding="utf-8")
    result = {"scope": "Logged production games, one seed is a smoke test, not a strength or latency ranking.",
        "matches": matches, "fingerprint": initial_fingerprint,
        "source_unchanged": initial_fingerprint == fingerprint(root),
        "passed": all(m["status"] in {"win", "draw"} and
            m["search_diagnostics"][0]["searched_decisions"] > 0 for m in matches)}
    (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def run(output, *, repetitions=3, iterations=48, worlds=4):
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    budget = InformationSearchBudget(worlds=worlds, iterations=iterations, max_advances=iterations * 16,
                                     max_depth=1, max_extensions=8)
    policy = ObservationOpponentPolicy()
    rows = []
    # Exclude interpreter/import/first-use initialization from the comparison.
    warm = world_factory("no_answer")
    InformationSetSearch(opponent_policy=policy,
        budget=InformationSearchBudget(worlds=1, iterations=3, max_depth=1)).search(
            warm, warm(0).start().player_information(), seed=42)
    initial_fingerprint = fingerprint(root)
    for repeat in range(repetitions):
        order = VARIANTS[repeat % 3:] + VARIANTS[:repeat % 3]
        for case in CASES:
            factory = world_factory(case)
            info = factory(0).start().player_information()
            for selection in order:
                planner = InformationSetSearch(opponent_policy=policy, budget=budget, selection=selection,
                    action_prior=None if selection == "uct" else HeuristicActionPrior(policy))
                wall_start, cpu_start = perf_counter_ns(), process_time_ns()
                result = planner.search(factory, info, seed=42 + repeat)
                cpu_ms = (process_time_ns() - cpu_start) / 1e6
                wall_ms = (perf_counter_ns() - wall_start) / 1e6
                rows.append({"case": case, "repeat": repeat, "selection": selection,
                    "choice_kind": result.choice.kind, "value": result.value,
                    "used_fallback": result.used_fallback, "stop_reason": result.stop_reason,
                    "cpu_ms": cpu_ms, "wall_ms": wall_ms, "cpu_wall_ratio": cpu_ms / wall_ms,
                    "root_coverage_complete": result.root_coverage_complete,
                    "metrics": asdict(result.metrics),
                    "estimates": [{"kind": e.action.kind, "visits": e.visits,
                                   "mean_value": e.mean_value} for e in result.estimates]})
    encoder = ActionEncoder()
    info = world_factory("unknown_answer")(0).start().player_information()
    encoder.encode_menu(info)
    samples = []
    for _ in range(100):
        started = perf_counter_ns()
        state, actions = encoder.encode_menu(info)
        samples.append(perf_counter_ns() - started)
    summaries = []
    for selection in VARIANTS:
        for case in CASES:
            group = [r for r in rows if r["selection"] == selection and r["case"] == case]
            summaries.append({"selection": selection, "case": case,
                "median_search_ms": median(r["metrics"]["elapsed_ms"] for r in group),
                "fallbacks": sum(r["used_fallback"] for r in group),
                "completed_iterations": [r["metrics"]["iterations_completed"] for r in group],
                "root_actions": group[0]["metrics"]["root_actions"]})
    result = {"scope": __doc__, "budget": asdict(budget), "repetitions": repetitions,
        "fingerprint": initial_fingerprint, "source_unchanged": initial_fingerprint == fingerprint(root),
        "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                    "hash_seed": os.environ.get("PYTHONHASHSEED", "random")},
        "encoder": {"schema_version": SCHEMA_VERSION, "median_menu_us": median(samples) / 1000,
            "max_menu_us": max(samples) / 1000, "cards": len(state.cards), "actions": len(actions)},
        "summaries": summaries, "trials": rows}
    (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(summaries, indent=2))
    print(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=48)
    parser.add_argument("--worlds", type=int, default=4)
    parser.add_argument("--live", action="store_true", help="Run complete production games using the three presets")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42])
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    output = args.output or Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime(
        "search-variants-%Y%m%d-%H%M%S-%f")
    result = (run_live(output, seeds=tuple(args.seeds)) if args.live else
              run(output, repetitions=args.repetitions, iterations=args.iterations, worlds=args.worlds))
    if (not result["source_unchanged"] or (not result["passed"] if args.live else
            any(r["used_fallback"] for r in result["trials"]))):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
