"""Compare eager and write-triggered cost index snapshots in whole games."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import gc
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.environment import collect_environment
from benchmarks.trigger_dispatch import play, outcome, trace
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from helper.query_system.object_register import BitSet


@contextmanager
def mode(name):
    if name == "deferred":
        yield
    else:
        original = RuntimeCheckpoint.__init__

        def eager(self, *args, **kwargs):
            kwargs["defer_indexes"] = False
            original(self, *args, **kwargs)

        with patch.object(RuntimeCheckpoint, "__init__", eager):
            yield


def diagnostic(name):
    rows = []
    with mode(name):
        init, close = RuntimeCheckpoint.__init__, RuntimeCheckpoint.close

        def measured_init(self, *args, **kwargs):
            started = perf_counter()
            init(self, *args, **kwargs)
            elapsed = perf_counter() - started
            row = dict(mode=self.mode, initial_records=len(self._restore),
                       initial_bitsets=sum(type(obj) is BitSet for _, obj, _ in self._restore),
                       initial_seconds=elapsed)
            self._diagnostic_row = row
            rows.append(row)

        def measured_close(self):
            row = getattr(self, "_diagnostic_row", None)
            if row is not None:
                row.update(final_records=len(self._restore),
                           final_bitsets=sum(type(obj) is BitSet for _, obj, _ in self._restore))
            close(self)

        with patch.object(RuntimeCheckpoint, "__init__", measured_init), patch.object(RuntimeCheckpoint, "close", measured_close):
            matches = [outcome(play(seed)) for seed in range(1, 4)]
    return dict(checkpoints=rows, matches=matches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if min(args.seeds, args.repetitions) < 1:
        parser.error("Counts must be positive")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("cost-index-checkpoint-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    environment = collect_environment(folder.parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    for name in ("eager", "deferred"):
        with mode(name):
            play(1)
    trials = []
    for iteration in range(args.repetitions):
        for name in (("eager", "deferred") if iteration % 2 == 0 else ("deferred", "eager")):
            with mode(name):
                gc.collect()
                wall, cpu = perf_counter(), process_time()
                matches = [outcome(play(seed, first)) for seed in range(1, args.seeds+1) for first in (0, 1)]
                row = dict(mode=name, wall_seconds=perf_counter()-wall,
                           cpu_seconds=process_time()-cpu, matches=matches)
            trials.append(row)
            print({k: v for k, v in row.items() if k != "matches"}, flush=True)
            (folder / "trials.json").write_text(json.dumps(trials, indent=2))
    checks = []
    for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
        traces, outcomes = [], []
        for name in ("eager", "deferred"):
            path = folder / f"trace-{index}-{name}.jsonl"
            with mode(name):
                outcomes.append(outcome(play(42+index, colors=colors, output=path)))
            traces.append(trace(path))
        checks.append(dict(colors=colors, records=len(traces[0]),
                           equal=traces[0] == traces[1] and outcomes[0] == outcomes[1],
                           completed=all(m["status"] == "win" for m in outcomes)))
    diagnostics = {name: diagnostic(name) for name in ("eager", "deferred")}
    medians = {name: median(t["wall_seconds"] for t in trials if t["mode"] == name)
               for name in ("eager", "deferred")}
    passed = (all(t["matches"] == trials[0]["matches"] for t in trials)
              and all(m["status"] == "win" for t in trials for m in t["matches"])
              and all(c["equal"] and c["completed"] for c in checks)
              and diagnostics["eager"]["matches"] == diagnostics["deferred"]["matches"])
    valid = all(t["cpu_seconds"]/t["wall_seconds"] >= .9 for t in trials)
    report = dict(environment=environment, trials=trials, trace_checks=checks, diagnostics=diagnostics,
                  median_seconds=medians, reduction=1-medians["deferred"]/medians["eager"],
                  passed=passed, timing_valid=valid,
                  scope="Warmed alternating whole games, natural GC, no profiler/logging/decision telemetry during timings. Separate full traces and three-game checkpoint record diagnostics. Eager mode disables deferred index capture in the same implementation.")
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed and valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
