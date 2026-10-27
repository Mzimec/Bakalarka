"""Compare ordinary and constant-reusing checkpoint restores in complete games."""
import argparse
from contextlib import nullcontext
from datetime import datetime
import gc
import json
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.play_search import run
from benchmarks.search_checkpoints import trace
from benchmarks.prepared_worlds import CollectionTiming
from benchmarks.search_edges import unprepared_copy
from game.ai.checkpoint_simulator import _Checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-advances", type=int, default=32)
    parser.add_argument("--checkpoint-limit", type=int, default=8)
    parser.add_argument("--reuse-world", action="store_true",
                        help="Use prepared worlds; this is where checkpoint constants are reused.")
    args = parser.parse_args()
    if min(args.repetitions, args.iterations, args.max_depth, args.max_advances, args.checkpoint_limit) < 1:
        parser.error("Budgets/repetitions must be positive")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("checkpoint-copies-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    rows, expected = [], None
    for repetition in range(args.repetitions):
        modes = ("replay", "checkpoint_reference", "checkpoint")
        if repetition % 2:
            modes = modes[::-1]
        for mode in modes:
            target = folder / f"{repetition}-{mode}"
            target.mkdir()
            gc.collect()
            reference = (patch.object(_Checkpoint, "copy", unprepared_copy)
                         if mode == "checkpoint_reference" else nullcontext())
            wall, cpu = perf_counter(), process_time()
            with reference, CollectionTiming() as collections:
                result = run(target, seeds=(args.seed,), iterations=args.iterations,
                             max_depth=args.max_depth, max_advances=args.max_advances,
                             reuse_world=args.reuse_world,
                             checkpoint_limit=0 if mode == "replay" else args.checkpoint_limit)
            timing = {"wall_seconds": perf_counter() - wall, "cpu_seconds": process_time() - cpu,
                      "gc_seconds": collections.seconds}
            (target / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            match = result["matches"][0]
            actual = (trace(target / "match-0.jsonl"),
                      {key: match[key] for key in ("status", "winner", "turns", "decisions")})
            if expected is None:
                expected = actual
            if not result["passed"] or not actual[0] or actual != expected:
                raise AssertionError("Checkpoint optimization changed complete-game search trace/outcome")
            diagnostics = match["search_diagnostics"][0]["by_request"].values()
            rows.append({"mode": mode, "repetition": repetition, **timing,
                         "match_seconds": match["elapsed_seconds"], "records": len(actual[0]),
                         **{key: sum(row[key] for row in diagnostics) for key in (
                             "world_materializations", "world_restores", "simulator_advance_calls",
                             "checkpoint_hits", "checkpoint_skipped_decisions")}})
            report = {"scope": "Same-seed complete games, reverse order on alternate repetitions; no profiling, same search budget. World template mode is explicit in settings. Not a strength comparison.",
                      "settings": vars(args), "trace_equal": True,
                      "complete": len(rows) == args.repetitions * 3, "runs": rows}
            (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
