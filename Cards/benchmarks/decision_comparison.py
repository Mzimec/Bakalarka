"""Sequential paired runs isolating decision telemetry and early autopass.

Run as python -m benchmarks.decision_comparison with PYTHONPATH=Cards.
Each timed game uses a fresh process; logging/parity runs are measured separately.
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

MODES = ("baseline", "stats-off", "both")


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def worker(args):
    from benchmarks.environment import collect_environment
    from game.ai.modular_agent import ModularAgent
    from game.ai.simple_agent import SimpleAgent
    from game.game_actions.generation.decision_abstraction.priority_availability import NonManaPriorityAvailability
    from game.simulation.match_runner import run_match

    if args.mode != "both":
        NonManaPriorityAvailability.prepare = lambda *args, **kwargs: None
    args.output.mkdir(parents=True, exist_ok=False)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"].update(component_profiler_enabled=False,
                                          decision_stats_enabled=args.mode == "baseline")
    save(args.output / "environment.json", environment)
    rows = []
    games = [(args.seed, args.starter)] if args.seed is not None else [(s, p) for s in range(1, args.seeds + 1) for p in (0, 1)]
    for seed, starter in games:
        path = args.output / f"{seed}-{starter}.jsonl" if args.logs else None
        cpu = process_time()
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_match(("white", "white"), path, seed=seed, starting_player=starter,
                               max_turns=100, controllers=(ModularAgent(), SimpleAgent()),
                               collect_decision_stats=args.mode == "baseline")
        cpu = process_time() - cpu
        digest, early_passes = None, 0
        if path:
            events = []
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["kind"] in {"decision_timing", "result"}:
                    continue
                row.pop("seq", None)  # Timing events change sequence numbers.
                # Only the reason for an already automatic pass may change.
                details = row.get("details", {})
                if details.get("auto_pass"):
                    early_passes += details.get("reason") == "no_available_abilities"
                    details.pop("reason", None)
                events.append(row)
            digest = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
        rows.append(dict(seed=seed, starter=starter, seconds=result.elapsed_seconds,
                         cpu_seconds=cpu, status=result.status, turns=result.turns,
                         decisions=result.decisions, winner=result.winner_index,
                         error=result.error, events_sha256=digest, early_passes=early_passes))
        save(args.output / "results.json", rows)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--starter", type=int, choices=(0, 1))
    parser.add_argument("--logs", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    output = args.output or Path(__file__).resolve().parents[1] / "runs" / f"decision-verification-{datetime.now():%Y%m%d-%H%M%S-%f}"
    output.mkdir(parents=True, exist_ok=False)
    print(f"Results: {output}", flush=True)
    rows = {mode: [] for mode in MODES}

    def run(mode, target, *extra):
        subprocess.run([sys.executable, "-m", "benchmarks.decision_comparison", "--worker",
                        "--output", str(target), "--mode", mode, "--seeds", str(args.seeds), *extra], check=True)
        return json.loads((target / "results.json").read_text(encoding="utf-8"))

    for index, (seed, starter) in enumerate((s, p) for s in range(1, args.seeds + 1) for p in (0, 1)):
        offset = index % len(MODES)
        for mode in MODES[offset:] + MODES[:offset]:
            result, = run(mode, output / f"paired/{seed}-{starter}-{mode}", "--seed", str(seed), "--starter", str(starter))
            rows[mode].append(result)
            save(output / f"paired-{mode}.json", rows[mode])
        print(f"{seed}/{starter}: " + ", ".join(f"{m}={rows[m][-1]['seconds']:.3f}s" for m in MODES), flush=True)
    logs = {}
    for mode in ("baseline", "both"):
        print(f"Event parity: {mode}", flush=True)
        logs[mode] = run(mode, output / f"logs-{mode}", "--logs")
    fields = ("seed", "starter", "status", "turns", "decisions", "winner", "error")
    for group in (*rows.values(), *logs.values()):
        assert len(group) == 2 * args.seeds
        assert all(all(a[k] == b[k] for k in fields) for a, b in zip(group, rows["baseline"]))
        assert all(row["error"] is None for row in group)
    assert all(a["events_sha256"] == b["events_sha256"] and a["events_sha256"] is not None
               for a, b in zip(logs["baseline"], logs["both"]))
    timing = {mode: sum(r["seconds"] for r in group) for mode, group in rows.items()}
    report = dict(games=2 * args.seeds, game_seconds=timing,
                  cpu_seconds={mode: sum(r["cpu_seconds"] for r in group) for mode, group in rows.items()},
                  reduction_percent={mode: (1 - timing[mode] / timing["baseline"]) * 100 for mode in MODES[1:]},
                  all_outcomes_and_event_hashes_match=True,
                  early_passes=sum(r["early_passes"] for r in logs["both"]),
                  turns=sum(r["turns"] for r in rows["both"]),
                  decisions=sum(r["decisions"] for r in rows["both"]),
                  baseline="Current code with early absence proof disabled and decision statistics enabled; existing sole-pass optimization stays enabled.")
    save(output / "comparison.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
