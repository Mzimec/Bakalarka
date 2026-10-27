"""New-edge latency in one prepared sampled world; no sampling in timed edges."""
import argparse
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch

from benchmarks.world_root_copy import capture_snapshot
from benchmarks.search_simulator import diagnostic_choice
from benchmarks.profiler import ComponentProfiler
from game.ai.current_position_worlds import _PreparedWorld
from game.ai.knowledge import PlayerKnowledge
from game.ai.search_simulator import SearchSimulator, _Replay
from game.ai.checkpoint_simulator import CheckpointSimulator, _Checkpoint
from game.simulation.search_config import search_generation_policy


def instrument(profiler):
    from game.ai import simulation_world, search_decision
    from game.game_actions.generation.decision_abstraction.option_space import DecisionOptionSpace
    profiler.patch_function(simulation_world, "copy_world", "restore")
    profiler.patch_function(simulation_world, "immutable_world_values", "copy_preparation")
    profiler.patch_method(SearchSimulator, "_run_replay", "engine_and_control")
    profiler.patch_method(_Replay, "decide", "decision_other")
    profiler.patch_method(_Replay, "_view_data", "observations")
    profiler.patch_function(search_decision.DecisionProjector, "build", "action_projection")
    profiler.patch_function(json, "dumps", "json_encoding")
    original = DecisionOptionSpace.__iter__
    def iterate(space):
        return profiler.profile_iterator(original(space), "generation")
    profiler.patch_replacement(DecisionOptionSpace, "__iter__", iterate)


def run(template, *, checkpoints, edges, components):
    options = dict(track_information=True, policy_for=search_generation_policy, max_phases=24)
    simulator = (CheckpointSimulator(template, checkpoint_limit=edges + 1,
                                    checkpoint_interval=1, **options)
                 if checkpoints else SearchSimulator(template, **options))
    start = perf_counter()
    node = simulator.start()
    setup_ms = (perf_counter() - start) * 1000
    nodes, rows = [node], []
    with ComponentProfiler() as profiler:
        if components:
            instrument(profiler)
        for _ in range(edges):
            if node.decision is None:
                break
            choice = diagnostic_choice(node)
            start = perf_counter()
            result = simulator.advance(node, choice)
            elapsed = (perf_counter() - start) * 1000
            rows.append({"history_length": len(node.history), "request": node.decision.kind,
                         "choice": choice.kind, "options": len(node.choices), "ms": elapsed})
            node = result
            nodes.append(node)
        stats = profiler.to_dict()
    return nodes, {"start_ms": setup_ms, "edges": rows, "components": stats,
                   "total_edge_ms": sum(r["ms"] for r in rows),
                   "checkpoint_hits": getattr(simulator, "checkpoint_hits", 0),
                   "skipped_decisions": getattr(simulator, "checkpoint_skipped_decisions", 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edges", type=int, default=24)
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--components", action="store_true")
    args = parser.parse_args()
    if args.edges < 1 or args.repetitions < 1:
        parser.error("Positive edges and repetitions required")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-edges-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    _, snapshot = capture_snapshot()
    template = _PreparedWorld(snapshot, 7)
    template.materialize(tuple(SimpleNamespace(knowledge=PlayerKnowledge()) for _ in range(2)))
    # The initial materialization, sampling, registration and first copy are
    # deliberately excluded. Every measured advance still copies and executes.
    rows, expected = [], None
    for repetition in range(args.repetitions):
        modes = ("replay", "checkpoint_reference", "checkpoint")
        if repetition % 2:
            modes = modes[::-1]
        for mode in modes:
            gc.collect()
            reference = (patch.object(_Checkpoint, "copy", unprepared_copy)
                         if mode == "checkpoint_reference" else nullcontext())
            with reference:
                nodes, result = run(template, checkpoints=mode != "replay", edges=args.edges, components=args.components)
            if expected is None:
                expected = nodes
            if nodes != expected:
                raise AssertionError("Replay/checkpoint observations, actions or recall diverged")
            result.update(mode=mode, repetition=repetition)
            rows.append(result)
            print(result["mode"], round(result["total_edge_ms"], 2), "ms", flush=True)
    medians = {mode: median(row["total_edge_ms"] for row in rows if row["mode"] == mode)
               for mode in ("replay", "checkpoint_reference", "checkpoint")}
    report = {"scope": "One prepared sampled midgame world, new chain edges (no transition cache), alternating modes; startup and sampling outside edge timers. Checkpoints every supported priority boundary. Component timing adds overhead. Not a full-game/strength comparison.",
              "runtime": {"python": platform.python_version(), "hash_seed": os.environ.get("PYTHONHASHSEED", "random")},
              "settings": vars(args), "equal": True, "median_edge_total_ms": medians, "runs": rows}
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(medians)


def unprepared_copy(checkpoint):
    from game.ai.simulation_world import copy_world
    return copy_world(checkpoint.state, checkpoint.knowledge)


if __name__ == "__main__":
    main()
