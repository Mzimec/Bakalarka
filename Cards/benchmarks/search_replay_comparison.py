"""Paired strict/prefix replay measurements on the same growing game history."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter, process_time

from benchmarks.search_simulator import diagnostic_choice
from game.ai.search_simulator import SearchSimulator, starter_scenario
from game.ai.prefix_replay_simulator import PrefixReplaySimulator
from game.ai.perfect_information_search import PerfectInformationSearch, SearchBudget


def timed(call):
    wall, cpu = perf_counter(), process_time()
    result = call()
    return result, {"wall_ms": (perf_counter() - wall) * 1000, "cpu_ms": (process_time() - cpu) * 1000}


def compare_position(sims, nodes, *, repeats, search_advances):
    assert nodes[0] == nodes[1]
    samples = [{"fork": [], "advance": [], "search": []} for _ in sims]
    choices = tuple(dict.fromkeys((diagnostic_choice(nodes[0]), *nodes[0].choices[:2])))
    for repeat in range(repeats):
        order = (0, 1) if repeat % 2 == 0 else (1, 0)
        forks, children, searches = {}, {}, {}
        for i in order:
            forks[i], timing = timed(lambda: sims[i].fork(nodes[i]))
            samples[i]["fork"].append(timing)
            children[i] = []
            for choice in choices:
                child, timing = timed(lambda: sims[i].advance(nodes[i], choice))
                children[i].append(child)
                samples[i]["advance"].append(timing)
            search = PerfectInformationSearch(budget=SearchBudget(max_depth=2, max_advances=search_advances))
            searches[i], timing = timed(lambda: search.search(sims[i], nodes[i]))
            samples[i]["search"].append({**timing, "metrics": asdict(searches[i].metrics),
                                          "completed_depth": searches[i].completed_depth,
                                          "stop_reason": searches[i].stop_reason})
        assert forks[0] == forks[1] == nodes[0]
        assert children[0] == children[1]
        assert replace(searches[0], metrics=searches[1].metrics) == searches[1]
        assert replace(searches[0].metrics, elapsed_ms=0) == replace(searches[1].metrics, elapsed_ms=0)
    summary = {}
    for operation in samples[0]:
        strict = median(s["wall_ms"] for s in samples[0][operation])
        prefix = median(s["wall_ms"] for s in samples[1][operation])
        summary[operation] = {"strict_median_ms": strict, "prefix_median_ms": prefix,
                              "speedup": strict / prefix if prefix else None}
    return {"history_decisions": len(nodes[0].history), "request": nodes[0].decision.kind,
            "turn": nodes[0].decision.turn, "phase": nodes[0].decision.phase,
            "root_options": len(nodes[0].choices), "compared_children": len(choices),
            "equivalent": True, "summary": summary,
            "samples": {"strict": samples[0], "prefix": samples[1]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--depths", type=int, nargs="+", default=[8, 32, 64])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--search-advances", type=int, default=12)
    args = parser.parse_args()
    if min(*args.depths, args.repeats, args.search_advances) < 1:
        parser.error("Depths, repetitions and budget must be positive")
    sims = [kind(starter_scenario(seed=args.seed)) for kind in (SearchSimulator, PrefixReplaySimulator)]
    nodes = [sim.start() for sim in sims]
    rows = []
    for depth in range(max(args.depths) + 1):
        assert nodes[0] == nodes[1]
        if nodes[0].terminal or nodes[0].cutoff:
            break
        if depth in args.depths:
            row = compare_position(sims, nodes, repeats=args.repeats, search_advances=args.search_advances)
            rows.append(row)
            print(f"history={depth}, options={row['root_options']}: "
                  f"advance {row['summary']['advance']['speedup']:.2f}x; "
                  f"search {row['summary']['search']['speedup']:.2f}x; equivalent=True", flush=True)
        if depth < max(args.depths):
            choice = diagnostic_choice(nodes[0])
            nodes = [sim.advance(node, choice) for sim, node in zip(sims, nodes)]
    output = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-replay-comparison-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {"seed": args.seed, "requested_depths": args.depths, "repeats": args.repeats,
              "search_advances": args.search_advances, "python": sys.version, "positions": rows,
              "coverage_complete": {row["history_decisions"] for row in rows} == set(args.depths),
              "scope": "Paired replay/search measurements, not single-game latency or ISMCTS throughput."}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
