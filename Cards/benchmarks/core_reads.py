"""Diagnostic of frequent core reads; no production behavior is changed."""
import cProfile
from datetime import datetime
import json
from pathlib import Path
import pstats
from statistics import median
from timeit import timeit

from benchmarks.environment import collect_environment
from game.ai.modular_agent import ModularAgent
from game.ai.simple_agent import SimpleAgent
from game.enums import CardType, ZoneType
from game.game_loop.minimal_game import ScriptedController
from game.game_state import State, Player, Card, CardDefinition
from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition,
                                     ContinuousEffectState, PermanentDuration,
                                     DynamicTargetingStrategy, AddIntModifier,
                                     only_empty_builtin_sources)
from game.game_state.registers.card_register import IK_KEY
from game.simulation.match_runner import run_match
from game.stat_type import STAT_POWER
from game.target.continuous_targets import CardIncarnationsSpec
from helper.query_system.query import EqQuery


def board(bonuses):
    state = State([Player([], ScriptedController()) for _ in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3),
                state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD, state)
    for index in range(bonuses):
        definition = ContinuousEffectDefinition(
            PermanentDuration(), card, state.time_stamp,
            DynamicTargetingStrategy(CardIncarnationsSpec(((card, card.zone_revision),))),
            {STAT_POWER: [AddIntModifier(1)]},
        )
        state.add_continuous_effect(ContinuousEffect(f"bonus-{index}", definition, ContinuousEffectState(set())))
    state.synchronise_registers()
    assert card.get_power(state) == 2 + bonuses
    return state, card


def main():
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("core-reads-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    environment = collect_environment(Path(__file__).resolve().parents[1])
    environment["instrumentation"]["component_profiler_enabled"] = False
    matches = []
    profiler = cProfile.Profile()
    profiler.enable()
    for seed in (1, 2):
        result = run_match(("white", "white"), output=None, seed=seed, starting_player=0,
                           max_turns=100, controllers=(ModularAgent(), SimpleAgent()),
                           collect_decision_stats=False)
        matches.append({key: getattr(result, key) for key in ("status", "turns", "decisions", "winner_index")})
        assert result.status != "error", result.error
    profiler.disable()
    profiler.dump_stats(str(folder / "games.prof"))
    stats = pstats.Stats(profiler)
    functions = []
    for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        functions.append(dict(file=filename, line=line, name=name, calls=calls, primitive_calls=primitive,
                              self_ms=own * 1000, inclusive_ms=cumulative * 1000,
                              callers=[dict(file=f, line=l, name=n, values=v) for (f, l, n), v in callers.items()]))

    state, card = board(0)
    state_one, card_one = board(1)
    state_three, card_three = board(3)
    scope = dict(state=state, card=card, state_one=state_one, card_one=card_one,
                 state_three=state_three, card_three=card_three, stat_key=STAT_POWER,
                 sources=card.modifier_sources, empty=only_empty_builtin_sources,
                 register=state.card_register, key=card.key, query=EqQuery(IK_KEY, card.key),
                 raw_stat=card.stats[STAT_POWER])
    expressions = {
        "base_field_only_not_effective_stat": "raw_stat.base_value",
        "immutable_stat_lookup": "card.stats.get(stat_key)",
        "modifier_sources_empty": "empty(sources)",
        "timestamp_allocation": "state.time_stamp",
        "clean_continuous_refresh": "state.refresh_continuous_effects()",
        "get_stat_unmodified": "card.get_stat(stat_key, state)",
        "get_power_unmodified": "card.get_power(state)",
        "get_types_unmodified": "card.get_types(state)",
        "get_power_one_effect": "card_one.get_power(state_one)",
        "get_power_three_effects": "card_three.get_power(state_three)",
        "get_types_with_unrelated_power_effects": "card_three.get_types(state_three)",
        "direct_card_key": "register.get_by_key(key)",
        "indexed_card_key": "register.query(query)",
    }
    samples = {key: [] for key in expressions}
    names = list(expressions)
    number = 20000
    for repetition in range(7):
        order = names[repetition:] + names[:repetition]
        for name in order:
            samples[name].append(timeit(expressions[name], globals=scope, number=number) * 1e6 / number)
    assert card.get_power(state) == 2 and card_one.get_power(state_one) == 3 and card_three.get_power(state_three) == 5
    report = dict(environment=environment, matches=matches, profiled_seconds=stats.total_tt,
                  functions=sorted(functions, key=lambda row: row["self_ms"], reverse=True),
                  microseconds={key: dict(median=median(values), samples=values, expression=expressions[key])
                                for key, values in samples.items()},
                  repetitions=7, iterations_per_sample=number,
                  limitations="cProfile costs include instrumentation and overlap. Microbenchmarks are warm read-only 2-player/1-card fixtures; loop overhead included, timeit default GC disabled. Base-field and direct-key reads do not perform the same validation/effect work as public accessors. No external engine throughput comparison.")
    (folder / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(folder)
    print("Profiled seconds", stats.total_tt)
    for key, values in report["microseconds"].items():
        print(key, round(values["median"], 4), "us")


if __name__ == "__main__":
    main()
