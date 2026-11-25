"""Measure useful index work and compare synchronization implementations."""
import __future__
import argparse
from collections import Counter
from contextlib import contextmanager, ExitStack
import cProfile
from datetime import datetime
import gc
import json
from pathlib import Path
import pstats
from statistics import median
import sys
import textwrap
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.environment import collect_environment
from benchmarks.trigger_dispatch import play, outcome, trace
from game.game_state import State, Card, card as card_module, characteristic_values, continuous_rules
from game.game_state.registers.indexed_register import IndexedRegister
from game.game_state.registers.card_register import CardRegister


TARGETS = {
    "sync": (IndexedRegister, "synchronise"),
    "refresh": (continuous_rules, "refresh_continuous_effects"),
    "mark_layer": (CardRegister, "mark_layer_changed"),
    "card_stat": (Card, "get_stat"),
    "native_value": (characteristic_values, "read_native_value"),
}


def load_reference(path):
    sources = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for key, (owner, name) in TARGETS.items():
        namespace = dict(getattr(owner, name).__globals__)
        exec(compile(textwrap.dedent(sources[key]), str(path), "exec",
                     flags=__future__.annotations.compiler_flag), namespace)
        result[key] = namespace[name]
    return result


@contextmanager
def mode(name, reference):
    with ExitStack() as stack:
        if name == "reference":
            for key, (owner, attribute) in TARGETS.items():
                stack.enter_context(patch.object(owner, attribute, reference[key]))
            stack.enter_context(patch.object(card_module, "read_native_value", reference["native_value"]))
        yield


def diagnose():
    counts, causes, values_read = Counter(), Counter(), Counter()
    original_refresh = State.refresh_continuous_effects
    original_sync = IndexedRegister.synchronise
    original_mark = IndexedRegister.mark_changed
    original_values = CardRegister.changed_index_values

    def refresh(state):
        reason = ("recursive" if state._refreshing_effects else "dirty" if state._effects_dirty
                  else "timestamp" if state._effects_checked_at != state.time_stamp else "clean")
        counts["refresh." + reason] += 1
        before = getattr(state, "_continuous_graph_rebuilds", 0), getattr(state, "_continuous_graph_reuses", 0)
        result = original_refresh(state)
        if reason not in ("recursive", "clean"):
            counts["refresh.graph_rebuild"] += getattr(state, "_continuous_graph_rebuilds", 0) - before[0]
            counts["refresh.graph_reuse"] += getattr(state, "_continuous_graph_reuses", 0) - before[1]
        return result

    def sync(register):
        reason = "recursive" if register._synchronising else "dirty" if register._dirty else "empty"
        counts[type(register).__name__ + ".sync." + reason] += 1
        return original_sync(register)

    def mark(register, obj, indexes=None):
        frame = sys._getframe(1)
        chain = []
        for _ in range(3):
            if frame is None:
                break
            chain.append(frame.f_code.co_name)
            frame = frame.f_back
        causes[type(register).__name__ + ":" + ">".join(chain)] += 1
        return original_mark(register, obj, indexes)

    def values(register, card, indexes):
        result = original_values(register, card, indexes)
        counts["card.projections"] += 1
        counts["card.projections.full_request" if indexes is None else "card.projections.partial_request"] += 1
        counts["card.values.read"] += len(result)
        previous = register._snapshots.get(card.key, {})
        for key, value in result.items():
            values_read["read:" + key.name] += 1
            if previous.get(key) != value:
                values_read["changed:" + key.name] += 1
                counts["card.values.changed"] += 1
        return result

    with (patch.object(State, "refresh_continuous_effects", refresh),
          patch.object(IndexedRegister, "synchronise", sync),
          patch.object(IndexedRegister, "mark_changed", mark),
          patch.object(CardRegister, "changed_index_values", values)):
        matches = [outcome(play(seed)) for seed in range(1, 4)]
    return dict(counts=counts, causes=causes, values=values_read, matches=matches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True,
                        help="Trusted local archive of pre-change Python implementations.")
    args = parser.parse_args()
    reference = load_reference(args.reference)
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("index-sync-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    (folder / "reference-sources.json").write_text(args.reference.read_text(encoding="utf-8"), encoding="utf-8")
    environment = collect_environment(folder.parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    print(folder, flush=True)
    for name in ("reference", "optimized"):
        with mode(name, reference):
            play(1)
    trials = []
    for name in ("reference", "optimized", "optimized", "reference"):
        with mode(name, reference):
            gc.collect()
            wall, cpu = perf_counter(), process_time()
            matches = [outcome(play(seed, first)) for seed in range(1, 11) for first in (0, 1)]
            row = dict(mode=name, wall_seconds=perf_counter()-wall, cpu_seconds=process_time()-cpu, matches=matches)
        trials.append(row)
        print({k: v for k, v in row.items() if k != "matches"}, flush=True)
    checks = []
    for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
        records, outcomes = [], []
        for name in ("reference", "optimized"):
            path = folder / f"trace-{index}-{name}.jsonl"
            with mode(name, reference):
                outcomes.append(outcome(play(42+index, colors=colors, output=path)))
            records.append(trace(path))
        checks.append(dict(colors=colors, records=len(records[0]), equal=records[0] == records[1] and outcomes[0] == outcomes[1],
                           completed=all(r["status"] == "win" for r in outcomes)))
    diagnostic = {}
    for name in ("reference", "optimized"):
        with mode(name, reference):
            diagnostic[name] = diagnose()
    profiler = cProfile.Profile()
    profiler.enable()
    for seed in range(1, 4):
        play(seed)
    profiler.disable()
    profiler.dump_stats(str(folder / "optimized.prof"))
    rows = [dict(file=k[0], line=k[1], function=k[2], primitive=v[0], calls=v[1], self_seconds=v[2], total_seconds=v[3])
            for k, v in pstats.Stats(profiler).stats.items()]
    (folder / "cprofile-optimized.json").write_text(json.dumps(sorted(rows, key=lambda r: r["self_seconds"], reverse=True), indent=2))
    medians = {name: median(t["wall_seconds"] for t in trials if t["mode"] == name) for name in ("reference", "optimized")}
    passed = (all(t["matches"] == trials[0]["matches"] for t in trials)
              and all(m["status"] == "win" for t in trials for m in t["matches"])
              and all(c["equal"] and c["completed"] for c in checks)
              and diagnostic["reference"]["matches"] == diagnostic["optimized"]["matches"])
    report = dict(environment=environment, trials=trials, trace_checks=checks, diagnostic=diagnostic,
                  median_seconds=medians, reduction=1-medians["optimized"]/medians["reference"], passed=passed,
                  timing_valid=all(t["cpu_seconds"]/t["wall_seconds"] >= .9 for t in trials),
                  scope="20 warmed games per ABBA trial, natural GC, no profiling/logging/telemetry during timing. Diagnostics, cProfile and four full trace checks are separate.")
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed and report["timing_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
