"""Alternating whole-game A/B for lightweight trigger rosters and event routing."""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from unittest.mock import patch
from benchmarks.environment import collect_environment

from game.ai.modular_agent import ModularAgent
from game.ai.simple_agent import SimpleAgent
from game.game_actions.data_structs.ability import TriggerAbilityDefinition
from game.game_actions.resolution import event_bus, operation_executor
from game.game_actions.triggers import trigger_roster
from game.game_state import State
from game.game_state.stat import HasModifiableStats, ModifiableStatBase
from game.game_state.registers.card_register import IK_HAS_TRIGGERS
from game.simulation.match_runner import run_match
from helper.query_system.query import EqQuery


def reference_abilities(self, event=None):
    """Pre-change discovery, including its original allocation pattern."""
    triggers = []
    for card in self.query_cards(EqQuery(IK_HAS_TRIGGERS, True)):
        zone = card.get_zone()
        for definition in card.get_trigger_defs(self).values():
            if isinstance(definition, TriggerAbilityDefinition) and definition.is_usable_in_zone(zone):
                triggers.append(definition.to_ability(card, card.get_controller(self), event=event))
    return triggers


def reference_roster(state):
    getter = getattr(state, "get_triggered_abilities", None)
    return tuple(getter(None)) if getter is not None else ()


def reference_stat(self, stat_t, state):
    """Original characteristic read before the native-type dispatch shortcut."""
    stat = self.stats.get(stat_t)
    if stat is None:
        return None
    if not isinstance(stat, ModifiableStatBase):
        return stat.base_value
    pipeline = self._get_modifier_pipeline(stat_t, state)
    if not pipeline:
        return stat.base_value
    from game.enums import ModifierType
    from game.stat_type import STAT_POWER, STAT_TOUGHNESS
    if stat_t in (STAT_POWER, STAT_TOUGHNESS):
        if sum(mod.behavior == ModifierType.SWITCH for mod in pipeline) % 2:
            other = STAT_TOUGHNESS if stat_t == STAT_POWER else STAT_POWER
            other_stat = self.stats.get(other)
            if other_stat is None:
                return None
            other_pipeline = tuple(mod for mod in self._get_modifier_pipeline(other, state)
                                   if mod.behavior != ModifierType.SWITCH)
            return other_stat.value(state, other_pipeline)
    return stat.value(state, pipeline)


@contextmanager
def dispatch_mode(mode):
    with ExitStack() as stack:
        if mode == "reference":
            stack.enter_context(patch.object(HasModifiableStats, "get_stat", reference_stat))
            stack.enter_context(patch.object(State, "get_trigger_abilities", reference_abilities))
            stack.enter_context(patch.object(event_bus, "capture_trigger_roster", reference_roster))
            stack.enter_context(patch.object(operation_executor, "capture_trigger_roster", reference_roster))
            stack.enter_context(patch.object(trigger_roster, "event_key_possible", lambda *args: True))
        elif mode != "optimized":
            raise ValueError(mode)
        yield


def outcome(result):
    return {key: getattr(result, key) for key in ("status", "winner_index", "turns", "decisions", "error")}


def play(seed, starting_player=0, colors=("white", "white"), output=None):
    return run_match(colors, output=output, seed=seed, starting_player=starting_player,
                     max_turns=100, controllers=(ModularAgent(), SimpleAgent()),
                     collect_decision_stats=False)


def trace(path):
    return [{key: value for key, value in row.items() if key != "seq"}
            for line in path.read_text(encoding="utf-8").splitlines()
            if (row := json.loads(line))["kind"] not in ("result", "decision_timing")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.seeds < 1 or args.repetitions < 1:
        parser.error("seeds and repetitions must be positive")
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("trigger-dispatch-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    print(folder, flush=True)
    # Warm module/catalog imports equally before timing either implementation.
    for mode in ("reference", "optimized"):
        with dispatch_mode(mode):
            play(1)
    results = []
    for repetition in range(args.repetitions):
        for mode in (("reference", "optimized") if repetition % 2 == 0 else ("optimized", "reference")):
            with dispatch_mode(mode):
                import gc
                gc.collect()
                start, cpu = perf_counter(), process_time()
                matches = [outcome(play(seed, first)) for seed in range(1, args.seeds + 1) for first in (0, 1)]
                result = {"mode": mode, "wall_seconds": perf_counter() - start,
                          "cpu_seconds": process_time() - cpu, "matches": matches}
            results.append(result)
            print({key: value for key, value in result.items() if key != "matches"}, flush=True)
            (folder / "trials.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    # File logging/serialization is deliberately outside the timed workload.
    checks = []
    for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
        paths = []
        outcomes = []
        for mode in ("reference", "optimized"):
            path = folder / f"trace-{index}-{mode}.jsonl"
            with dispatch_mode(mode):
                outcomes.append(outcome(play(42 + index, colors=colors, output=path)))
            paths.append(path)
        rows = [trace(path) for path in paths]
        checks.append({"colors": colors, "seed": 42 + index, "records": len(rows[0]),
                       "equal": rows[0] == rows[1] and outcomes[0] == outcomes[1],
                       "outcomes": outcomes,
                       "sha256": hashlib.sha256(json.dumps(rows[0], sort_keys=True).encode()).hexdigest()})
    medians = {mode: median(r["wall_seconds"] for r in results if r["mode"] == mode)
               for mode in ("reference", "optimized")}
    passed = (all(r["matches"] == results[0]["matches"] for r in results)
              and all(m["status"] != "error" for r in results for m in r["matches"])
              and all(c["equal"] and all(m["status"] != "error" for m in c["outcomes"]) for c in checks))
    # An OS suspend or a long scheduling interruption is not engine time.
    # Preserve raw trials, but do not publish a speedup for an interrupted set.
    timing_valid = all(r["cpu_seconds"] / r["wall_seconds"] >= 0.9 for r in results)
    report = {"passed": passed, "timing_valid": timing_valid,
              "environment": environment,
              "games_per_trial": args.seeds * 2, "trials": results,
              "median_seconds": medians if timing_valid else None,
              "reduction": 1 - medians["optimized"] / medians["reference"] if timing_valid else None,
              "trace_checks": checks,
              "scope": "Trigger dispatch and native-stat shortcut versus both original paths. Sequential alternating pairs, warmed imports, GC collection before each trial, natural GC during matches, no profiler/decision telemetry/logging in timings; separate complete event/action log validation."}
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({key: value for key, value in report.items() if key not in ("trials", "environment")}, flush=True)
    return 0 if passed and timing_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
