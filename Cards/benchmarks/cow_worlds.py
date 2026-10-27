"""COW index experiment: alternating real-world copies and optional full games."""
import argparse
from contextlib import nullcontext
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import platform
from statistics import median
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.world_root_copy import capture_snapshot
from benchmarks.prepared_worlds import CollectionTiming
from game.ai.current_position_worlds import _PreparedWorld
from game.ai.knowledge import PlayerKnowledge
from game.ai.runtime_clone import RuntimeClone, DictionaryCopyPlans
from game.ai.simulation_world import copy_world, immutable_world_values, prepare_cow_indexes


def timed(call):
    wall, cpu = perf_counter(), process_time()
    result = call()
    return result, {"wall_ms": (perf_counter() - wall) * 1000,
                    "cpu_ms": (process_time() - cpu) * 1000}


def micro(turn, repetitions):
    from types import SimpleNamespace
    from game.enums import ZoneType
    request, snapshot = capture_snapshot(turn)
    controllers = tuple(SimpleNamespace(knowledge=PlayerKnowledge()) for _ in range(2))
    template = _PreparedWorld(snapshot, 7)
    template.materialize(controllers)
    plain = template.state
    cow, _ = copy_world(plain, ())
    _, preparation = timed(lambda: prepare_cow_indexes(cow))
    sources = {"plain": plain, "cow": cow}
    shared = {name: immutable_world_values(state) for name, state in sources.items()}
    plans = {name: DictionaryCopyPlans(shared[name]) for name in sources}
    for name, state in sources.items():
        copy_world(state, (), immutable_values=shared[name], dictionary_plans=plans[name])
        plans[name].collecting = False

    def restore(name):
        return copy_world(sources[name], (), immutable_values=shared[name],
                          dictionary_plans=plans[name])[0]

    def mutate(state):
        # Native writes, invalidation, continuous refresh and query-index sync.
        cards = list(state.players[0].hand.values())[:2]
        for card in cards:
            state.players[0].move_card(card, ZoneType.GRAVEYARD)
        state.players[0].health -= 1
        state.synchronise_registers()
        return [(c.command_id, c.zone.name) for c in state.card_register.storage.get_all()]

    rows = {name: [] for name in sources}
    # Isolate provider overhead only: runtime-key rebinding belongs to the full
    # world measurement above. A provider alone is not a valid graph-copy root.
    key_memos = {name: {id(value): value for group in state.card_register.idx_provider.data.values()
                        for value in group} for name, state in sources.items()}
    for iteration in range(repetitions):
        outcomes = []
        for name in (tuple(sources) if iteration % 2 == 0 else tuple(reversed(sources))):
            gc.collect()  # Outside timing, automatic collections remain enabled.
            restored, fork = timed(lambda: restore(name))
            outcome, writes = timed(lambda: mutate(restored))
            provider_copy, provider = timed(lambda: RuntimeClone(key_memos[name]).copy(
                sources[name].card_register.idx_provider))
            rows[name].append({"fork": fork, "writes": writes, "provider_copy": provider})
            outcomes.append(outcome)
            del restored
            del provider_copy
        if outcomes[0] != outcomes[1]:
            raise AssertionError("COW changed native mutation outcome")
    providers = {name: state.card_register.idx_provider for name, state in sources.items()}
    report = {
        "scope": f"Alternating warmed copies of one sampled turn-{request.state.turn.number} white/white world, then up to two native hand-to-graveyard moves plus life change and index sync. GC before samples outside timing. Not game latency.",
        "turn": request.state.turn.number,
        "cards": len(plain.card_register.storage),
        "card_index_groups": len(providers["plain"].data),
        "shareable_groups": len(providers["cow"]._shareable),
        "card_index_buckets": sum(map(len, providers["plain"].data.values())),
        "preparation": preparation, "samples": rows,
        "provider_copy_scope": "Keys pinned to originals only for this isolated component timer; full-world fork timing rebinds all runtime keys normally.",
        "median_ms": {name: {
            **{part: median(row[part]["wall_ms"] for row in samples)
               for part in ("fork", "writes", "provider_copy")},
            "fork_and_writes": median(row["fork"]["wall_ms"] + row["writes"]["wall_ms"] for row in samples),
        } for name, samples in rows.items()},
    }
    return report


def full_games(folder, repetitions, seed):
    from benchmarks.play_search import run
    from benchmarks.search_checkpoints import trace
    from game.ai import current_position_worlds
    original = current_position_worlds._PreparedWorld

    def experimental(*args, **kwargs):
        return original(*args, **kwargs, cow_indexes=True)

    rows, expected = [], None
    variants = ("default", "plain_template", "cow_template")
    for repeat in range(repetitions):
        for name in (variants if repeat % 2 == 0 else variants[::-1]):
            target = folder / f"{repeat}-{name}"
            target.mkdir()
            gc.collect()
            mode = (patch.object(current_position_worlds, "_PreparedWorld", experimental)
                    if name == "cow_template" else nullcontext())
            with mode, CollectionTiming() as collections:
                result, timing = timed(lambda: run(target, seeds=(seed,), reuse_world=name != "default"))
            (target / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            match = result["matches"][0]
            actual = (trace(target / "match-0.jsonl"),
                      {key: match[key] for key in ("status", "winner", "turns", "decisions")})
            if expected is None:
                expected = actual
            if not result["passed"] or not actual[0] or actual != expected:
                raise AssertionError("Full-game search trace/outcome changed")
            diagnostics = match["search_diagnostics"][0]["by_request"].values()
            rows.append({"variant": name, "repeat": repeat, "timing": timing,
                         "match_seconds": match["elapsed_seconds"],
                         "gc_seconds": collections.seconds, "records": len(actual[0]),
                         **{key: sum(row[key] for row in diagnostics) for key in (
                             "world_materializations", "world_restores", "simulator_advance_calls")}})
    return {"scope": "Repeated same-seed games, reversed order on second repetition; default tiny search budget (4 iterations, 1 world, depth 1, 16 advances). No strength or parallelism claim.",
            "trace_equal": True, "seed": seed, "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turn", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--full-games", action="store_true")
    parser.add_argument("--game-repetitions", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.turn < 1 or args.repetitions < 2 or args.game_repetitions < 1:
        parser.error("Positive turn/game repetitions and at least two samples required")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("cow-worlds-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    print(folder, flush=True)
    report = {"runtime": {"python": platform.python_version(), "hash_seed": os.environ.get("PYTHONHASHSEED", "random")},
              "settings": vars(args), "micro": micro(args.turn, args.repetitions)}
    path = folder / "comparison.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["micro"]["median_ms"], indent=2), flush=True)
    if args.full_games:
        report["games"] = full_games(folder, args.game_repetitions, args.seed)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
