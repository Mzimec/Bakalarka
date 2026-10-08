"""Paired games and event parity with selective SBA enabled/disabled.

The control uses current indexes and compiled candidate queries, but always
collects every rule. This isolates filtering, not every historical SBA change.
Run with PYTHONPATH=Cards, using the same interpreter for both variants.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from time import process_time


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def worker(args):
    from game.ai.modular_agent import ModularAgent
    from game.ai.simple_agent import SimpleAgent
    from game.simulation.match_runner import run_match
    from game.game_actions.resolution.sba_candidates import SBACandidateFilter
    from benchmarks.environment import collect_environment
    if args.variant == "all-rules":
        SBACandidateFilter.select = lambda self, state: None
    args.output.mkdir(parents=True, exist_ok=False)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = args.profile
    environment["sba_mode"] = args.variant
    save(args.output / "environment.json", environment)
    profiler = None
    if args.profile:
        from benchmarks.component_benchmark import configure_profiler
        from benchmarks.profiler import ComponentProfiler
        profiler = ComponentProfiler()
        configure_profiler(profiler)
    rows = []
    try:
        games = [(args.seed, args.starter)] if args.seed is not None else [(s, p) for s in range(1, 11) for p in (0, 1)]
        for seed, starter in games:
            path = args.output / f"{seed}-{starter}.jsonl" if args.profile else None
            cpu = process_time()
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_match(("white", "white"), output=path, seed=seed, starting_player=starter,
                                   max_turns=100, controllers=(ModularAgent(), SimpleAgent()))
            cpu = process_time() - cpu
            digest = None
            if path is not None:
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                events = [row for row in events if row["kind"] not in {"decision_timing", "result"}]
                digest = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
            rows.append(dict(seed=seed, starter=starter, seconds=result.elapsed_seconds,
                             cpu_seconds=cpu, status=result.status, turns=result.turns,
                             decisions=result.decisions, winner=result.winner_index,
                             error=result.error, events_sha256=digest))
            save(args.output / "results.json", rows)
    finally:
        if profiler:
            profiler.restore()
            save(args.output / "profile.json", dict(components=profiler.to_dict(), work=profiler.work_to_dict()))


def compare(output):
    read = lambda path: json.loads((output / path).read_text(encoding="utf-8"))
    results = {mode: read(f"paired-{mode}.json") for mode in ("all-rules", "selective")}
    logs = {mode: read(f"profile-{mode}/results.json") for mode in results}
    fields = ("seed", "starter", "status", "turns", "decisions", "winner", "error")
    for rows in (*results.values(), *logs.values()):
        assert len(rows) == 20
        assert all(all(a[key] == b[key] for key in fields) for a, b in zip(rows, results["selective"]))
        assert all(row["error"] is None for row in rows)
    assert all(a["events_sha256"] == b["events_sha256"] and a["events_sha256"] is not None
               for a, b in zip(logs["all-rules"], logs["selective"]))
    timings = {mode: sum(row["seconds"] for row in rows) for mode, rows in results.items()}
    profiles = {mode: read(f"profile-{mode}/profile.json") for mode in results}
    sba = {mode: {key: value for key, value in profile["components"].items()
                  if key.startswith("sba.")} for mode, profile in profiles.items()}
    # Filter and collect are siblings. Their totals include synchronization.
    cost = {mode: sum(value["total_ms"] for key, value in components.items()
                     if key == "sba.candidates" or key.startswith("sba.collect."))
            for mode, components in sba.items()}
    report = dict(games=20, baseline="Current code with candidate skipping disabled; same index revisions and compiled queries",
                  game_seconds=timings, time_reduction_percent=(1-timings["selective"]/timings["all-rules"])*100,
                  all_outcomes_and_event_hashes_match=True,
                  turns=sum(row["turns"] for row in logs["selective"]),
                  decisions=sum(row["decisions"] for row in logs["selective"]),
                  sba_including_filter_ms=cost, sba_components=sba,
                  sba_work={mode: {key: value for key, value in profile["work"].items() if key.startswith("sba.")}
                            for mode, profile in profiles.items()})
    save(output / "comparison.json", report)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--variant", choices=("all-rules", "selective"))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--starter", type=int, choices=(0, 1))
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    output = args.output or Path(__file__).resolve().parents[1] / "runs" / f"sba-verification-{datetime.now():%Y%m%d-%H%M%S-%f}"
    output.mkdir(parents=True, exist_ok=False)
    print(f"Results: {output}", flush=True)
    results = {mode: [] for mode in ("all-rules", "selective")}
    for index, (seed, starter) in enumerate((s, p) for s in range(1, 11) for p in (0, 1)):
        modes = ("all-rules", "selective") if index % 2 == 0 else ("selective", "all-rules")
        for mode in modes:
            target = output / f"paired/{seed}-{starter}-{mode}"
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(target),
                            "--variant", mode, "--seed", str(seed), "--starter", str(starter)], check=True)
            row, = json.loads((target / "results.json").read_text(encoding="utf-8"))
            results[mode].append(row)
            save(output / f"paired-{mode}.json", results[mode])
        print(f"{seed}/{starter}: {results['all-rules'][-1]['seconds']:.3f} -> {results['selective'][-1]['seconds']:.3f}s", flush=True)
    for mode in results:
        print(f"Profile and event verification: {mode}", flush=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(output / f"profile-{mode}"),
                        "--variant", mode, "--profile"], check=True)
    compare(output)


if __name__ == "__main__":
    main()
