"""Matched whole-game reference/template comparison with exact search traces."""
import argparse
import cProfile
import gc
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter, process_time
from contextlib import nullcontext
from unittest.mock import patch

from benchmarks.play_search import run
from benchmarks.search_checkpoints import trace


def unprepared_snapshot(snapshot):
    """Exact generic-copy baseline, without preparing constants or recipes."""
    snapshot.immutable_values = {}
    snapshot.dictionary_plans = None


class CollectionTiming:
    def __init__(self):
        self.seconds = 0.0
        self.collections = 0

    def observe(self, phase, info):
        if phase == "start":
            self.started = perf_counter()
        else:
            self.seconds += perf_counter() - self.started
            self.collections += 1

    def __enter__(self):
        gc.callbacks.append(self.observe)
        return self

    def __exit__(self, *_):
        gc.callbacks.remove(self.observe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--worlds", type=int, default=1)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-advances", type=int, default=16)
    parser.add_argument("--checkpoint-limit", type=int, default=0)
    parser.add_argument("--prepared-first", action="store_true",
                        help="Reverse pair order to check warmup/load-order bias.")
    parser.add_argument("--snapshot-only", action="store_true",
                        help="Compare public-root copy preparation; full-world reuse stays off in both runs.")
    parser.add_argument("--profile", action="store_true", help="Write separate cProfile files; timings include profiler overhead.")
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("prepared-worlds-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    results, traces = {}, []
    variants = [("reference", False), ("prepared", True)]
    if args.prepared_first:
        variants.reverse()
    for name, reuse in variants:
        target = folder / name
        target.mkdir()
        started = process_time()
        from game.ai.current_position_worlds import _Snapshot
        mode = (patch.object(_Snapshot, "_prepare_copy", unprepared_snapshot)
                if args.snapshot_only and not reuse else nullcontext())
        profiler = cProfile.Profile() if args.profile else nullcontext()
        with mode, profiler, CollectionTiming() as collections:
            result = run(target, seeds=(args.seed,), reuse_world=reuse and not args.snapshot_only,
                         iterations=args.iterations, worlds=args.worlds, max_depth=args.max_depth,
                         max_advances=args.max_advances, checkpoint_limit=args.checkpoint_limit)
        if args.profile:
            profiler.dump_stats(str(target / "runtime.prof"))
        result["process_cpu_seconds"] = process_time() - started
        result["gc_seconds"] = collections.seconds
        result["gc_collections"] = collections.collections
        (target / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        traces.append(trace(target / "match-0.jsonl"))
        results[name] = result
    a, b = (result["matches"][0] for result in results.values())
    same = bool(traces[0]) and traces[0] == traces[1] and all(
        a[key] == b[key] for key in ("status", "winner", "turns", "decisions"))
    report = {"passed": same and all(r["passed"] for r in results.values()),
              "trace_equal": same, "records": len(traces[0]), "settings": vars(args),
              "scope": f"Sequential pair, {variants[0][0]} first; not a strength test or confidence interval.",
              "runs": {name: {"wall_seconds": r["matches"][0]["elapsed_seconds"],
                              "cpu_seconds": r["process_cpu_seconds"],
                              "gc_seconds": r["gc_seconds"], "gc_collections": r["gc_collections"],
                              **{key: sum(v[key] for v in r["matches"][0]["search_diagnostics"][0]["by_request"].values())
                                 for key in ("world_materializations", "world_restores", "simulator_advance_calls")}}
                       for name, r in results.items()}}
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
