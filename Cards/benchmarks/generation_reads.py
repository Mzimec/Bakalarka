"""Whole-game comparison of native immutable ability definition reuse."""
import __future__
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime
import gc
import json
from pathlib import Path
from statistics import median
import textwrap
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.environment import collect_environment
from benchmarks.trigger_dispatch import play, outcome, trace
from game.game_state.card import Card
from game.mana import discovery_context


def references(path):
    sources = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for key, fn in (("abilities", Card.get_ability_defs), ("scope", discovery_context.mana_discovery_scope.__wrapped__)):
        namespace = dict(fn.__globals__)
        exec(compile(textwrap.dedent(sources[key]), str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
        result[key] = namespace[fn.__name__]
    return result


@contextmanager
def mode(name, ref):
    with ExitStack() as stack:
        if name == "reference":
            stack.enter_context(patch.object(Card, "get_ability_defs", ref["abilities"]))
            stack.enter_context(patch.object(discovery_context, "mana_discovery_scope", ref["scope"]))
        yield


def diagnostic(name, ref):
    counts = dict(requests=0, builds=0)
    with mode(name, ref):
        public, build = Card.get_ability_defs, Card._build_ability_defs

        def read(self, state):
            counts["requests"] += 1
            return public(self, state)

        def construct(self, state):
            counts["builds"] += 1
            return build(self, state)

        with patch.object(Card, "get_ability_defs", read), patch.object(Card, "_build_ability_defs", construct):
            matches = [outcome(play(seed)) for seed in range(1, 4)]
    if name == "reference":
        counts["builds"] = counts["requests"]  # Old public getter builds directly.
    return dict(counts=counts, matches=matches)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="Trusted local source archive.")
    parser.add_argument("--search", action="store_true")
    args = parser.parse_args()
    ref = references(args.reference)
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("generation-read-cache-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    (folder / "reference-sources.json").write_text(args.reference.read_text(encoding="utf-8"), encoding="utf-8")
    print(folder, flush=True)
    environment = collect_environment(folder.parents[1])
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
            row = dict(mode=name, wall_seconds=perf_counter()-wall, cpu_seconds=process_time()-cpu, matches=matches)
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
        checks.append(dict(colors=colors, records=len(records[0]), equal=records[0] == records[1] and outcomes[0] == outcomes[1],
                           completed=all(m["status"] == "win" for m in outcomes)))
    diagnostics = {name: diagnostic(name, ref) for name in ("reference", "optimized")}
    medians = {name: median(t["wall_seconds"] for t in trials if t["mode"] == name) for name in ("reference", "optimized")}
    passed = (all(t["matches"] == trials[0]["matches"] for t in trials)
              and all(m["status"] == "win" for t in trials for m in t["matches"])
              and all(c["equal"] and c["completed"] for c in checks)
              and diagnostics["reference"]["matches"] == diagnostics["optimized"]["matches"])
    valid = all(t["cpu_seconds"]/t["wall_seconds"] >= .9 for t in trials)
    report = dict(environment=environment, trials=trials, trace_checks=checks, diagnostics=diagnostics,
                  passed=passed, timing_valid=valid,
                  median_seconds=medians if valid else None,
                  reduction=1-medians["optimized"]/medians["reference"] if valid else None,
                  scope="Warmed ABBA, 20 games per trial, natural GC, no profiler/logging/decision telemetry in timings. Counts and four deck trace pairs measured separately.")
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.search:
        from benchmarks.play_search import run as search_game
        from benchmarks.search_checkpoints import trace as search_trace
        rows, records = [], []
        for name in ("reference", "optimized"):
            target = folder / f"search-{name}"
            target.mkdir()
            with mode(name, ref):
                wall, cpu = perf_counter(), process_time()
                result = search_game(target, seeds=(42,), iterations=4, max_depth=1, max_advances=16)
                rows.append(dict(mode=name, wall_seconds=perf_counter()-wall, cpu_seconds=process_time()-cpu, result=result))
            records.append(search_trace(target / "match-0.jsonl"))
        outcomes = [{k: r["result"]["matches"][0][k] for k in ("status", "winner", "turns", "decisions")} for r in rows]
        search_passed = (bool(records[0]) and records[0] == records[1] and outcomes[0] == outcomes[1]
                         and all(r["result"]["passed"] for r in rows))
        report["search"] = dict(passed=search_passed, records=len(records[0]), trials=rows,
                                scope="One matched logged production MCTS game per mode, seed 42, 4 iterations, depth 1, 16 advances, one world; compatibility check only.")
        report["passed"] = passed = passed and search_passed
        (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({k: report[k] for k in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed and report["timing_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
