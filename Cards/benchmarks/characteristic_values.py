"""Matched whole-game comparison of native characteristic reuse and full reads."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import gc
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from timeit import timeit
from types import FunctionType
from unittest.mock import patch

from benchmarks.environment import collect_environment
from benchmarks.trigger_dispatch import play, outcome, trace
from benchmarks.core_reads import board
from game.game_state import Card, State
from game.game_state.stat import HasModifiableStats
from game.game_state.modifier import TimeStamp


def reference_refresh(self):
    from .continuous_rules import refresh_continuous_effects
    refresh_continuous_effects(self)


# Preserve the original relative import as well as the original function body;
# resolving an absolute import has a different cost in this very frequent path.
reference_refresh = FunctionType(reference_refresh.__code__, {"__package__": "game.game_state"})


def reference_timestamp(self):
    return TimeStamp(self.turn.number, self.turn.phase)


@contextmanager
def read_mode(mode):
    if mode == "reference":
        with (patch.object(Card, "get_stat", HasModifiableStats.get_stat),
              patch.object(State, "time_stamp", property(reference_timestamp)),
              patch.object(State, "refresh_continuous_effects", reference_refresh)):
            yield
    elif mode == "optimized":
        yield
    else:
        raise ValueError(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--search", action="store_true", help="Also compare two fixed-budget logged MCTS games.")
    args = parser.parse_args()
    if min(args.seeds, args.repetitions) < 1:
        parser.error("seeds and repetitions must be positive")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("characteristic-values-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    environment = collect_environment(folder.parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    for mode in ("reference", "optimized"):
        with read_mode(mode):
            play(1)
    trials = []
    for repetition in range(args.repetitions):
        for mode in (("reference", "optimized") if repetition % 2 == 0 else ("optimized", "reference")):
            with read_mode(mode):
                gc.collect()
                wall, cpu = perf_counter(), process_time()
                matches = [outcome(play(seed, first)) for seed in range(1, args.seeds + 1) for first in (0, 1)]
                row = dict(mode=mode, wall_seconds=perf_counter()-wall,
                           cpu_seconds=process_time()-cpu, matches=matches)
            trials.append(row)
            print({k: v for k, v in row.items() if k != "matches"}, flush=True)
            (folder / "trials.json").write_text(json.dumps(trials, indent=2))
    checks = []
    for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
        rows, outcomes = [], []
        for mode in ("reference", "optimized"):
            path = folder / f"trace-{index}-{mode}.jsonl"
            with read_mode(mode):
                outcomes.append(outcome(play(42+index, colors=colors, output=path)))
            rows.append(trace(path))
        checks.append(dict(colors=colors, records=len(rows[0]), equal=rows[0] == rows[1] and outcomes[0] == outcomes[1], outcomes=outcomes))
    samples = {}
    for count in (0, 1, 3):
        state, card = board(count)
        for expression in ("card.get_power(state)", "card.get_types(state)"):
            key = f"{count} effects: {expression}"
            measurements = {mode: [] for mode in ("reference", "optimized")}
            for repetition in range(7):
                for mode in (("reference", "optimized") if repetition % 2 == 0 else ("optimized", "reference")):
                    with read_mode(mode):
                        measurements[mode].append(timeit(expression, globals=dict(card=card, state=state), number=20000)*1e6/20000)
            samples[key] = {mode: dict(median_us=median(values), samples_us=values) for mode, values in measurements.items()}
    timing_valid = all(t["cpu_seconds"]/t["wall_seconds"] >= .9 for t in trials)
    passed = (all(t["matches"] == trials[0]["matches"] for t in trials)
              and all(m["status"] != "error" for t in trials for m in t["matches"])
              and all(c["equal"] and all(m["status"] != "error" for m in c["outcomes"]) for c in checks))
    medians = {mode: median(t["wall_seconds"] for t in trials if t["mode"] == mode) for mode in ("reference", "optimized")}
    report = dict(environment=environment, passed=passed, timing_valid=timing_valid,
                  games_per_trial=args.seeds*2, trials=trials, trace_checks=checks,
                  median_seconds=medians if timing_valid else None,
                  reduction=1-medians["optimized"]/medians["reference"] if timing_valid else None,
                  microbenchmarks=samples,
                  scope="Native characteristic reuse and timestamp/refresh accessor overhead versus original paths; sequential warmed alternating games, natural GC, no profiler/logging/decision telemetry during timing. Separate full traces and warm read-only timeit fixtures (GC disabled).")
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.search:
        from benchmarks.play_search import run as search_game
        from benchmarks.search_checkpoints import trace as search_trace
        search_rows, search_paths = [], []
        for mode in ("reference", "optimized"):
            target = folder / f"search-{mode}"
            target.mkdir()
            with read_mode(mode):
                gc.collect()
                wall, cpu = perf_counter(), process_time()
                result = search_game(target, seeds=(42,), iterations=4, max_depth=1, max_advances=16)
                search_rows.append(dict(mode=mode, wall_seconds=perf_counter()-wall,
                                        cpu_seconds=process_time()-cpu, result=result))
            search_paths.append(search_trace(target / "match-0.jsonl"))
        equal = search_paths[0] == search_paths[1] and bool(search_paths[0])
        outcomes = [{k: row["result"]["matches"][0][k] for k in ("status", "winner", "turns", "decisions")} for row in search_rows]
        search_passed = equal and outcomes[0] == outcomes[1] and all(row["result"]["passed"] for row in search_rows)
        report["search"] = dict(passed=search_passed, records=len(search_paths[0]), trials=search_rows,
                                scope="One matched seed, logged production MCTS, 4 iterations, depth 1, 16 advances, one world; correctness smoke test, not a reliable speed/strength estimate.")
        report["passed"] = passed = passed and search_passed
        (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed and timing_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
