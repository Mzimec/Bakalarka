"""Matched A/B against locally archived pre-change function sources."""
from __future__ import annotations

import __future__
import argparse
from contextlib import contextmanager, ExitStack
import gc
import json
from pathlib import Path
from statistics import median
import sys
import textwrap
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.environment import collect_environment
from benchmarks.trigger_dispatch import play, outcome, trace
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_state.card import Card
from game.game_state import modifier
from game.rules import lands


def references(path):
    """Load our saved Python implementations, with their original module globals."""
    originals = dict(watch=RuntimeCheckpoint.watch, empty=modifier.only_empty_builtin_sources,
                     abilities=Card.get_ability_defs, land_abilities=lands.basic_land_abilities)
    saved = json.loads(path.read_text(encoding="utf-8"))
    functions = {}
    for key, original in originals.items():
        namespace = dict(original.__globals__)
        code = compile(textwrap.dedent(saved[key]), str(path), "exec",
                       flags=__future__.annotations.compiler_flag)
        exec(code, namespace)
        functions[key] = namespace[original.__name__]
    return functions


@contextmanager
def mode(name, ref):
    if name == "optimized":
        yield
        return
    optimized_empty = modifier.only_empty_builtin_sources
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(RuntimeCheckpoint, "watch", ref["watch"]))
            stack.enter_context(patch.object(Card, "get_ability_defs", ref["abilities"]))
            stack.enter_context(patch.object(lands, "basic_land_abilities", ref["land_abilities"]))
            for module in list(sys.modules.values()):
                if module is None or not getattr(module, "__name__", "").startswith("game."):
                    continue
                if getattr(module, "only_empty_builtin_sources", None) is optimized_empty:
                    stack.enter_context(patch.object(module, "only_empty_builtin_sources", ref["empty"]))
            yield
    finally:
        # Also restore aliases imported lazily during the reference game.
        for module in list(sys.modules.values()):
            if module is not None and getattr(module, "__name__", "").startswith("game."):
                if getattr(module, "only_empty_builtin_sources", None) is ref["empty"]:
                    module.only_empty_builtin_sources = optimized_empty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True,
                        help="Trusted local reference-sources.json saved before this change.")
    args = parser.parse_args()
    folder = args.reference.parent
    ref = references(args.reference)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    for name in ("reference", "optimized"):
        with mode(name, ref):
            play(1)
    trials = []
    for name in ("reference", "optimized", "optimized", "reference"):
        with mode(name, ref):
            gc.collect()
            wall, cpu = perf_counter(), process_time()
            matches = [outcome(play(seed, first)) for seed in range(1, 11) for first in (0, 1)]
            row = dict(mode=name, wall_seconds=perf_counter()-wall,
                       cpu_seconds=process_time()-cpu, matches=matches)
        trials.append(row)
        print({k: v for k, v in row.items() if k != "matches"}, flush=True)
    checks = []
    for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
        records, outcomes = [], []
        for name in ("reference", "optimized"):
            path = folder / f"trace-{index}-{name}.jsonl"
            with mode(name, ref):
                outcomes.append(outcome(play(42+index, colors=colors, output=path)))
            records.append(trace(path))
        checks.append(dict(colors=colors, records=len(records[0]),
                           equal=records[0] == records[1] and outcomes[0] == outcomes[1]))
    medians = {name: median(t["wall_seconds"] for t in trials if t["mode"] == name)
               for name in ("reference", "optimized")}
    passed = (all(t["matches"] == trials[0]["matches"] for t in trials)
              and all(m["status"] == "win" for t in trials for m in t["matches"])
              and all(c["equal"] for c in checks))
    report = dict(environment=environment, trials=trials, trace_checks=checks,
                  median_seconds=medians, reduction=1-medians["optimized"]/medians["reference"],
                  passed=passed, timing_valid=all(t["cpu_seconds"]/t["wall_seconds"] >= .9 for t in trials),
                  scope="20 games per trial, warmed ABBA, natural GC, no profiling or decision telemetry; separate logged trace checks.")
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
