"""Compare persistent source cache on/off sequentially, then verify logged games."""
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

from benchmarks.decision_comparison import save


def worker(args):
    from benchmarks.environment import collect_environment
    from game.ai.simple_agent import SimpleAgent
    from game.ai.modular_agent import ModularAgent
    from game.mana.source_cache import StaticManaSourceCache
    from game.simulation.match_runner import run_match
    if args.mode == "off":
        StaticManaSourceCache.lookup = lambda *a: None
        StaticManaSourceCache.remember = lambda *a: None
        StaticManaSourceCache.supported = staticmethod(lambda state: False)
    args.output.mkdir(parents=True, exist_ok=False)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"].update(component_profiler_enabled=args.logs, decision_stats_enabled=False,
                                          persistent_mana_source_cache_enabled=args.mode == "on")
    save(args.output / "environment.json", environment)
    profiler = None
    if args.logs:
        from benchmarks.component_benchmark import configure_profiler
        from benchmarks.profiler import ComponentProfiler
        profiler = ComponentProfiler()
        configure_profiler(profiler)
    rows = []
    games = [(args.seed, args.starter)] if args.seed else [(s, p) for s in range(1, 11) for p in (0, 1)]
    try:
        for seed, starter in games:
            path = args.output / f"{seed}-{starter}.jsonl" if args.logs else None
            cpu = process_time()
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_match(("white", "white"), path, seed=seed, starting_player=starter,
                                   controllers=(ModularAgent(), SimpleAgent()), collect_decision_stats=False)
            cpu = process_time() - cpu
            digest = None
            if path:
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                events = [row for row in events if row["kind"] != "result"]
                digest = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
            rows.append(dict(seed=seed, starter=starter, seconds=result.elapsed_seconds,
                             cpu_seconds=cpu, status=result.status, turns=result.turns,
                             decisions=result.decisions, winner=result.winner_index, error=result.error,
                             events_sha256=digest))
            save(args.output / "results.json", rows)
    finally:
        if profiler:
            profiler.restore()
            save(args.output / "profile.json", dict(components=profiler.to_dict(), work=profiler.work_to_dict()))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=("off", "on"))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--starter", type=int)
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timing-only", action="store_true",
                        help="Skip the separate logged/profiled parity run.")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.worker:
        worker(args)
        return
    output = args.output or Path(__file__).resolve().parents[1] / "runs" / f"source-cache-{datetime.now():%Y%m%d-%H%M%S-%f}"
    output.mkdir(parents=True, exist_ok=False)
    print(f"Results: {output}", flush=True)
    results = {mode: [] for mode in ("off", "on")}

    def run(mode, target, *extra):
        subprocess.run([sys.executable, "-m", "benchmarks.source_cache_comparison", "--worker",
                        "--output", str(target), "--mode", mode, *extra], check=True)
        return json.loads((target / "results.json").read_text(encoding="utf-8"))

    for index, (repeat, seed, starter) in enumerate((r, s, p) for r in range(args.repeats) for s in range(1, 11) for p in (0, 1)):
        for mode in (("off", "on") if index % 2 == 0 else ("on", "off")):
            row, = run(mode, output / f"paired/{repeat}-{seed}-{starter}-{mode}", "--seed", str(seed), "--starter", str(starter))
            row["repeat"] = repeat
            results[mode].append(row)
            save(output / f"paired-{mode}.json", results[mode])
        print(f"{repeat + 1}: {seed}/{starter}: {results['off'][-1]['seconds']:.3f} -> {results['on'][-1]['seconds']:.3f}s", flush=True)
    logged = {}
    for mode in (() if args.timing_only else results):
        print(f"Logged/profiled parity: {mode}", flush=True)
        logged[mode] = run(mode, output / f"logged-{mode}", "--logs")
    fields = ("seed", "starter", "status", "turns", "decisions", "winner", "error")
    for rows in results.values():
        assert len(rows) == 20 * args.repeats
        assert all(all(a[k] == b[k] for k in fields) for a, b in zip(rows, results["off"]))
        assert all(row["error"] is None for row in rows)
    for rows in logged.values():
        assert len(rows) == 20
        assert all(all(a[k] == b[k] for k in fields) for a, b in zip(rows, results["off"][:20]))
    if logged:
        assert all(a["events_sha256"] == b["events_sha256"] and a["events_sha256"] is not None
                   for a, b in zip(logged["off"], logged["on"]))
    seconds = {mode: sum(r["seconds"] for r in rows) for mode, rows in results.items()}
    per_repeat = [{mode: sum(r["seconds"] for r in rows if r["repeat"] == repeat)
                   for mode, rows in results.items()} for repeat in range(args.repeats)]
    report = dict(games=20 * args.repeats, repeats=per_repeat, game_seconds=seconds,
                  reduction_percent=(1 - seconds["on"] / seconds["off"]) * 100,
                  all_outcomes_match=True,
                  all_outcomes_and_event_hashes_match=True if logged else None,
                  turns=sum(r["turns"] for r in results["on"]),
                  decisions=sum(r["decisions"] for r in results["on"]),
                  baseline="Current no-logging runtime, decision stats off, scoped discovery cache retained; persistent cache disabled.")
    save(output / "comparison.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
