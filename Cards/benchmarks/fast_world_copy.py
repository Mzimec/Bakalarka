"""Matched reference/fast world restores and previously unseen search edges."""
import argparse
from datetime import datetime
from functools import partial
import gc
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter, process_time
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.search_edges import run
from benchmarks.world_root_copy import capture_snapshot
from game.ai import checkpoint_simulator, simulation_world
from game.ai.current_position_worlds import _PreparedWorld
from game.ai.knowledge import PlayerKnowledge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copies", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--edges", type=int, default=24)
    args = parser.parse_args()
    if min(args.copies, args.repetitions, args.edges) < 1:
        parser.error("All counts must be positive")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("fast-world-copy-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    _, snapshot = capture_snapshot()
    template = _PreparedWorld(snapshot, 7)
    template.materialize(tuple(SimpleNamespace(knowledge=PlayerKnowledge()) for _ in range(2)))
    restore = simulation_world.copy_world
    copies, filtered, edges, expected = [], [], [], None
    for iteration in range(args.copies):
        for fast in ([False, True] if iteration % 2 == 0 else [True, False]):
            gc.collect()
            start = perf_counter()
            state = snapshot.copy_state(fast_copy=fast)
            filtered.append(dict(fast=fast, repetition=iteration, wall_ms=(perf_counter() - start) * 1000))
            assert state is not snapshot.state
            del state
            for prepared in (False, True):
                gc.collect()
                start, cpu = perf_counter(), process_time()
                state, memories = restore(template.state, template.knowledge, fast_copy=fast,
                                          immutable_values=template.immutable_values if prepared else ())
                copies.append(dict(fast=fast, constants=prepared, repetition=iteration,
                                   wall_ms=(perf_counter() - start) * 1000,
                                   cpu_ms=(process_time() - cpu) * 1000))
                assert state is not template.state and state.rng.getstate() == template.state.rng.getstate()
                assert all(memory._game_token is state.knowledge_token for memory in memories)
                del state, memories
    for iteration in range(args.repetitions):
        for fast in ([False, True] if iteration % 2 == 0 else [True, False]):
            gc.collect()
            copier = partial(restore, fast_copy=fast)
            with patch.object(simulation_world, "copy_world", copier), \
                 patch.object(checkpoint_simulator, "_copy_world", copier):
                nodes, result = run(template, checkpoints=True, edges=args.edges, components=False)
            if expected is None:
                expected = nodes
            if nodes != expected:
                raise AssertionError("Reference/fast decisions, observations or recall differ")
            result.update(fast=fast, repetition=iteration)
            edges.append(result)
            print("fast" if fast else "reference", round(result["total_edge_ms"], 2), "ms", flush=True)
    copy_medians = {f"{'fast' if fast else 'reference'}_{'constants' if prepared else 'generic'}":
                    median(row["wall_ms"] for row in copies if row["fast"] == fast and row["constants"] == prepared)
                    for fast in (False, True) for prepared in (False, True)}
    edge_medians = {"fast" if fast else "reference": median(row["total_edge_ms"] for row in edges if row["fast"] == fast)
                    for fast in (False, True)}
    report = dict(scope="Owned 120-card midgame fixture. Alternating copies with GC outside timing; generic and proven-constant copies. New checkpoint chain edges with sampling/setup outside timing; exact full-node equality. Not a full-game or playing-strength result.",
                  runtime=dict(python=platform.python_version(), hash_seed=os.environ.get("PYTHONHASHSEED", "random")),
                  settings=vars(args), equal=True, median_copy_ms=copy_medians,
                  median_filtered_root_ms={"fast" if fast else "reference": median(row["wall_ms"] for row in filtered if row["fast"] == fast)
                                           for fast in (False, True)},
                  median_edge_total_ms=edge_medians, copies=copies, filtered=filtered, edges=edges)
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(copy_medians, edge_medians, report["median_filtered_root_ms"])


if __name__ == "__main__":
    main()
