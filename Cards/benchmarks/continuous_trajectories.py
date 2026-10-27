"""Matched edge-replay versus one-runtime trajectories, window or complete games."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter, process_time

from benchmarks.world_root_copy import capture_snapshot
from benchmarks.play_search import run as run_match
from benchmarks.search_checkpoints import trace
from game.ai.current_position_worlds import _PreparedWorld
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator
from game.ai.checkpoint_simulator import CheckpointSimulator
from game.simulation.search_config import search_generation_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", action="store_true")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--advances", type=int, default=128)
    parser.add_argument("--reuse-world", action="store_true")
    parser.add_argument("--checkpoint-limit", type=int, default=0)
    parser.add_argument("--cache-size", type=int, default=2048)
    args = parser.parse_args()
    if min(args.repetitions, args.iterations, args.depth, args.advances) < 1 or args.cache_size < 0:
        parser.error("Positive budgets/repetitions and nonnegative cache size required")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("continuous-trajectories-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    if not args.match:
        _, snapshot = capture_snapshot()
        def factory(seed):
            template = _PreparedWorld(snapshot, seed, reuse=args.reuse_world)
            options = dict(track_information=True, policy_for=search_generation_policy, max_phases=24)
            return (CheckpointSimulator(template, checkpoint_limit=args.checkpoint_limit, **options)
                    if args.checkpoint_limit else SearchSimulator(template, **options))
        information = factory(0).start().player_information()
    rows, expected = [], None
    for repetition in range(args.repetitions):
        for mode in (("replay", "continuous") if repetition % 2 == 0 else ("continuous", "replay")):
            gc.collect()
            wall, cpu = perf_counter(), process_time()
            if args.match:
                target = folder / f"{repetition}-{mode}"
                target.mkdir()
                report = run_match(target, seeds=(args.seed,), iterations=args.iterations,
                    max_depth=args.depth, max_advances=args.advances, reuse_world=args.reuse_world,
                    checkpoint_limit=args.checkpoint_limit, trajectory_mode=mode,
                    transition_cache_size=args.cache_size)
                (target / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                match = report["matches"][0]
                actual = (trace(target / "match-0.jsonl"),
                          {k: match[k] for k in ("status", "winner", "turns", "decisions")})
                if not report["passed"] or not actual[0]:
                    raise AssertionError("Game must complete and contain usable search")
                diagnostics = match["search_diagnostics"][0]["by_request"].values()
                metrics = {key: sum(row[key] for row in diagnostics) for key in (
                    "simulator_advance_calls", "transition_cache_hits", "trajectory_runs",
                    "world_materializations", "world_restores")}
                measured = match["elapsed_seconds"]
            else:
                policy = ObservationOpponentPolicy()
                result = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
                    trajectory_mode=mode, transition_cache_size=args.cache_size,
                    budget=InformationSearchBudget(worlds=1, iterations=args.iterations,
                        max_depth=args.depth, max_advances=args.advances)).search(factory, information, seed=args.seed)
                # Normalize only execution/timing metrics, never estimates or budgets.
                metrics = asdict(result.metrics)
                actual = asdict(replace(result, metrics=None))
                actual["logical_metrics"] = {k: v for k, v in metrics.items() if k not in {
                    "elapsed_ms", "world_initialization_ms", "advance_ms", "trajectory_runs",
                    "checkpoint_hits", "checkpoint_entries", "checkpoint_skipped_decisions",
                    "world_materializations", "world_restores"}}
                measured = result.metrics.elapsed_ms / 1000
            if expected is None:
                expected = actual
            if actual != expected:
                raise AssertionError("Search choices, estimates, coverage, budgets or full-game trace diverged")
            rows.append(dict(mode=mode, repetition=repetition, measured_seconds=measured,
                             wall_seconds=perf_counter() - wall, cpu_seconds=process_time() - cpu,
                             metrics=metrics))
            output = dict(scope="Identical search budget, fixed seed, alternating execution modes. Complete game if match=true; otherwise one naturally reached midgame window with capture outside timing. Exact semantic comparison, not a strength test.",
                          runtime=dict(python=platform.python_version(), hash_seed=os.environ.get("PYTHONHASHSEED", "random")),
                          settings=vars(args), complete=len(rows) == args.repetitions * 2,
                          equal=True, semantic_result=expected, runs=rows,
                          median_seconds={name: median(r["measured_seconds"] for r in rows if r["mode"] == name)
                                          for name in {r["mode"] for r in rows}})
            (folder / "comparison.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
            print(mode, round(measured, 3), "seconds", metrics.get("world_materializations"), "materializations", flush=True)


if __name__ == "__main__":
    main()
