from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter, process_time

from benchmarks.profiler import ComponentProfiler
from benchmarks.environment import collect_environment

from game.ai.decision_maker import DecisionMaker
from game.ai.modular_agent import ModularAgent
from game.ai.simple_agent import SimpleAgent

from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.sba_resolver import SBAResolver

from game.game_state.registers.indexed_register import IndexedRegister
from game.game_state.registers.card_register import CardRegister

from game.game_actions.resolution import event_bus
from game.game_state import continuous_rules

from game.simulation.match_runner import run_match

from .probes import (
    install_continuous_refresh_probe,
    install_card_sync_probe,
    install_decision_generation_probe,
    install_mana_discovery_probe,
    install_mana_search_probe,
    install_sba_candidate_probe,
    install_layer_candidate_probe,
    install_cost_checkpoint_probe,
)

from game.game_state.state import State
from game.game_state.card_snapshot_cache import CardSnapshotCache
from game.mana.mana_solver import PoolManaSolver
from game.mana.mana_generator import ManaGenerator


GAMES = 20


def configure_profiler(profiler: ComponentProfiler):
    from game.ai import modular_agent
    from game.game_actions.data_structs.ability import SubAbilityComposer
    from game.game_actions.generation import structural_templates
    profiler.patch_function(modular_agent, "observation", "decision.observation")
    profiler.patch_method(SubAbilityComposer, "_build_action_graph", "generation.graph.build")
    profiler.patch_function(structural_templates, "compiled_graph", "generation.graph.lookup")
    profiler.patch_function(structural_templates, "prepared_effects", "generation.effects.prepare")
    from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
    from game.game_actions.resolution import cost_scope
    install_cost_checkpoint_probe(profiler)
    profiler.patch_function(cost_scope, "card_write_scope", "cost.scope")
    profiler.patch_method(RuntimeCheckpoint, "__init__", "cost.checkpoint")
    profiler.patch_method(RuntimeCheckpoint, "rollback", "cost.rollback")
    profiler.patch_function(
        event_bus,
        "capture_card_information",
        "lki.capture_all",
    )

    profiler.patch_function(
        event_bus,
        "capture_single_card",
        "lki.capture_card",
    )
    profiler.patch_function(event_bus, "_capture_single_card", "lki.rebuild")

    profiler.patch_function(
        event_bus,
        "capture_trigger_roster",
        "triggers.capture_roster",
    )

    profiler.patch_function(
        event_bus,
        "capture_event",
        "events.capture",
    )

    profiler.patch_method(
        CardRegister,
        "index_values",
        "index.card_values",
    )
    profiler.patch_method(CardRegister, "partial_index_values", "index.card_partial_values")
    profiler.patch_when(CardRegister, "synchronise", "index.card_sync_work",
                        lambda register: bool(register._dirty) and not register._synchronising)
    profiler.patch_when(continuous_rules, "refresh_continuous_effects", "continuous.refresh_work",
                        lambda state: not state._refreshing_effects and (
                            state._effects_dirty or state._effects_checked_at != state.time_stamp))

    profiler.patch_method(
        OperationExecutor,
        "execute_batch",
        "operations.execute_batch",
    )
    profiler.patch_dispatched_method(OperationExecutor, "execute_operation", "operations.execute")
    profiler.patch_method(State, "synchronise_registers", "index.synchronise_registers")
    from game.game_actions.resolution.sba_resolver import LethalCreaturesRule, PlayerLossRule
    from game.rules.permanents import PermanentStateRule
    for rule in (LethalCreaturesRule, PlayerLossRule, PermanentStateRule):
        profiler.patch_method(rule, "collect", f"sba.collect.{rule.__name__}")
    from game.game_actions.triggers import runtime_triggers
    profiler.patch_function(runtime_triggers, "collect_runtime", "triggers.collect_runtime")

    profiler.patch_method(
        SBAResolver,
        "resolve",
        "sba.resolve",
    )

    profiler.patch_method(
        DecisionMaker,
        "decide",
        "decision.decide",
    )

    profiler.patch_method(
    PoolManaSolver,
    "get_mana_plan",
    "mana.pool",
)

    profiler.patch_method(
        ManaGenerator,
        "generate",
        "mana.search",
    )

    profiler.patch_method(
        State,
        "get_mana_sources",
        "mana.source_discovery",
    )

    profiler.patch_method(
        State,
        "can_activate",
        "mana.can_activate",
    )

    # Lightweight structural probes.
    install_continuous_refresh_probe(profiler)
    install_card_sync_probe(profiler)

    # Actual lazy option-generation timing.
    install_decision_generation_probe(profiler)
    install_mana_discovery_probe(profiler)
    install_mana_search_probe(profiler)
    install_sba_candidate_probe(profiler)
    install_layer_candidate_probe(profiler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Profile game components over 20 seeded matches.")
    parser.add_argument("--no-decision-stats", action="store_true",
                        help="Disable engine decision telemetry (component profiler remains enabled).")
    parser.add_argument("--no-source-cache", action="store_true",
                        help="Disable persistent mana-source caching; retain scoped discovery caching.")
    args = parser.parse_args(argv)
    from benchmarks.cache_controls import source_cache_mode
    with source_cache_mode(not args.no_source_cache):
        return run_benchmark(args)


def run_benchmark(args):
    started_at = datetime.now().astimezone()
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"]["decision_stats_enabled"] = not args.no_decision_stats
    environment["instrumentation"]["persistent_mana_source_cache_enabled"] = not args.no_source_cache
    profiler = ComponentProfiler()
    configure_profiler(profiler)

    results = []

    start = perf_counter()
    cpu_start = process_time()

    try:
        for seed in range(1, 11):
            for starting_player in (0, 1):
                match_start = perf_counter()
                match_cpu_start = process_time()
                result = run_match(
                    ("white", "white"),
                    output=None,
                    seed=seed,
                    starting_player=starting_player,
                    max_turns=100,
                    collect_decision_stats=not args.no_decision_stats,
                    controllers=(
                        ModularAgent(),
                        SimpleAgent(),
                    ),
                )
                match_cpu_seconds = process_time() - match_cpu_start
                match_wall_seconds = perf_counter() - match_start

                results.append({
                    "seed": seed,
                    "starting_player": starting_player,
                    "elapsed_seconds": result.elapsed_seconds,
                    "wall_seconds": match_wall_seconds,
                    "cpu_seconds": match_cpu_seconds,
                    "status": result.status,
                    "turns": result.turns,
                    "decisions": result.decisions,
                    "winner": result.winner_index,
                })

    finally:
        cpu_seconds = process_time() - cpu_start
        elapsed = perf_counter() - start
        profiler.restore()

    total_decisions = sum(r["decisions"] for r in results)
    total_turns = sum(r["turns"] for r in results)

    components = profiler.to_dict()

    for stats in components.values():
        stats["calls_per_game"] = stats["calls"] / len(results)

        stats["calls_per_decision"] = (
            stats["calls"] / total_decisions
            if total_decisions
            else 0
        )

        stats["calls_per_turn"] = (
            stats["calls"] / total_turns
            if total_turns
            else 0
        )

        stats["exclusive_game_share"] = (
            stats["exclusive_ns"] / 1e9 / elapsed
            if elapsed
            else 0
        )

    output = {
        "schema_version": 2,
        "started_at": started_at.isoformat(),
        "environment": environment,
        "configuration": {
            "decks": ["white", "white"],
            "controllers": ["ModularAgent", "SimpleAgent"],
            "seeds": list(range(1, 11)),
            "starting_players": [0, 1],
            "max_turns": 100,
            "event_logging": False,
            "collect_decision_stats": not args.no_decision_stats,
            "persistent_mana_source_cache": not args.no_source_cache,
        },
        "timing_scope": {
            "elapsed_seconds": "Wall time of match loop, excluding imports, metadata, profiler setup/restore and report writing.",
            "cpu_seconds": "Process CPU time over the same loop, including profiler overhead.",
            "matches.elapsed_seconds": "Engine-reported game time.",
            "matches.wall_seconds": "Wall time around run_match, including controller construction.",
            "matches.cpu_seconds": "Process CPU time over the same run_match interval.",
        },
        "games": len(results),
        "elapsed_seconds": elapsed,
        "cpu_seconds": cpu_seconds,
        "cpu_to_wall_ratio": cpu_seconds / elapsed if elapsed else 0,
        "average_game_seconds": elapsed / len(results),
        "total_decisions": total_decisions,
        "total_turns": total_turns,
        "components": components,
        "work": profiler.work_to_dict(),
        "matches": results,
    }

    path = (
        Path(__file__).resolve().parent / "results"
        / f"component_benchmark-{started_at:%Y%m%d-%H%M%S-%f}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    # Exclusive creation also protects existing results if a name ever collides.
    with path.open("x", encoding="utf-8") as result_file:
        json.dump(output, result_file, indent=2)

    print_environment_report(output)
    print_report(output)
    print_work_report(output)
    print(f"\nResults saved to: {path}")


def print_environment_report(report: dict):
    environment = report["environment"]
    python = environment["python"]
    instrumentation = environment["instrumentation"]
    code = environment["code"]
    print(f"Python: {python['version'].splitlines()[0]}")
    print(f"Executable: {python['executable']}")
    print(f"System: {environment['system']['platform']}")
    print(f"Instrumentation: {json.dumps(instrumentation)}")
    print(f"Git: {code['git'].get('commit', 'unavailable')}; "
          f"source dirty: {code['git'].get('source_dirty', 'unknown')}")
    print(f"Source SHA-256: {code['sources']['sha256']}")
    print(f"Wall: {report['elapsed_seconds']:.3f}s; "
          f"CPU: {report['cpu_seconds']:.3f}s; "
          f"CPU/wall: {report['cpu_to_wall_ratio']:.3f}")


def print_report(report: dict):
    print()
    print(
        f"{'Component':35} "
        f"{'Calls':>10} "
        f"{'Total ms':>12} "
        f"{'Exclusive':>12} "
        f"{'Avg us':>10} "
        f"{'% game':>8}"
    )

    print("-" * 95)

    components = sorted(
        report["components"].items(),
        key=lambda x: x[1]["exclusive_ns"],
        reverse=True,
    )

    for name, stats in components:
        print(
            f"{name:35} "
            f"{stats['calls']:10d} "
            f"{stats['total_ms']:12.2f} "
            f"{stats['exclusive_ms']:12.2f} "
            f"{stats['average_us']:10.2f} "
            f"{stats['exclusive_game_share'] * 100:7.2f}%"
        )

def print_work_report(report: dict):
    print()
    print("Work counters")
    print("-" * 65)

    for name, value in sorted(
        report["work"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"{name:50} {value:12,d}")


if __name__ == "__main__":
    main()
