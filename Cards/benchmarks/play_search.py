"""Complete logged games through the production MCTS configuration path."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
import platform
from pathlib import Path

from game.simulation.config import run_config


def run(folder, *, seeds=(42,), max_turns=80, condition_opening_reveal=True,
        selection="uct", prior="uniform", transition_cache_size=2048, iterations=4,
        max_depth=1, max_advances=16, worlds=1, checkpoint_limit=0, reuse_world=False,
        trajectory_mode="continuous"):
    rows = []
    for index, seed in enumerate(seeds):
        config = {
            "seed": seed, "starting_player": index % 2, "max_turns": max_turns, "max_decisions": 3000,
            "players": [
                {"name": "MCTS", "deck": "red", "agent": {"type": "mcts", "mcts": {
                    "deck_knowledge": "both_lists", "search_seed": 7001 + index,
                    "worlds": worlds, "population_size": 1, "iterations": iterations, "max_depth": max_depth,
                    "max_advances": max_advances, "max_attempts": 8, "max_prefix_advances": 48,
                    "max_simulator_decisions": 256, "max_phases": 256,
                    "condition_opening_reveal": condition_opening_reveal,
                    "selection": selection, "prior": prior,
                    "transition_cache_size": transition_cache_size,
                    "checkpoint_limit": checkpoint_limit,
                    "reuse_world": reuse_world,
                    "trajectory_mode": trajectory_mode,
                }}},
                {"name": "Simple", "deck": "white", "agent": {"type": "simple"}},
            ],
        }
        path = folder / f"config-{index}.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        result = run_config(path, output=folder / f"match-{index}.jsonl")
        rows.append(asdict(result))
        summary = result.search_diagnostics[0]
        print(f"seed={seed}, {result.status}, turns={result.turns}, "
              f"searched={summary['searched_decisions']}/{summary['search_attempts']}, "
              f"fallbacks={summary['fallback_decisions']}, elapsed={result.elapsed_seconds:.2f}s", flush=True)
    complete = all(row["status"] in {"win", "draw"} for row in rows)
    zero_search = [row["seed"] for row in rows if row["search_diagnostics"][0]["searched_decisions"] == 0]
    return {"scope": "Complete production-config games with configurable budgets (defaults deliberately tiny). Coverage/robustness diagnostic, not a strength comparison. PASS requires all games complete and at least one usable search in every game; completion alone is insufficient.",
            "passed": complete and not zero_search, "complete_matches": complete,
            "runtime": {"python": platform.python_version(), "hash_seed": os.environ.get("PYTHONHASHSEED", "random")},
            "zero_search_seeds": zero_search, "matches": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43])
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument("--selection", choices=("uct", "puct", "hierarchical_puct"), default="uct")
    parser.add_argument("--prior", choices=("uniform", "heuristic"), default="uniform")
    parser.add_argument("--transition-cache-size", type=int, default=2048)
    parser.add_argument("--checkpoint-limit", type=int, default=0)
    parser.add_argument("--reuse-world", action="store_true",
                        help="Experimental full-world templates; slower in the measured larger pair.")
    parser.add_argument("--trajectory-mode", choices=("continuous", "replay"), default="continuous")
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-advances", type=int, default=16)
    parser.add_argument("--worlds", type=int, default=1)
    parser.add_argument("--no-opening-reveal-conditioning", action="store_true",
                        help="A/B baseline: do not constrain the opponent's first public hand exit.")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("play-search-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    print(folder, flush=True)
    result = run(folder, seeds=args.seeds, max_turns=args.max_turns,
                 condition_opening_reveal=not args.no_opening_reveal_conditioning,
                 selection=args.selection, prior=args.prior, transition_cache_size=args.transition_cache_size,
                 iterations=args.iterations, max_depth=args.max_depth, max_advances=args.max_advances,
                 worlds=args.worlds, checkpoint_limit=args.checkpoint_limit, reuse_world=args.reuse_world,
                 trajectory_mode=args.trajectory_mode)
    (folder / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("PASS" if result["passed"] else "MISS")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
