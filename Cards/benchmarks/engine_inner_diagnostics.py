"""Separate work counters and read-only fixtures for engine_inner comparisons."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
from timeit import timeit


def work_counts():
    from unittest.mock import patch
    from benchmarks.trigger_dispatch import play, outcome
    from game.game_state.card import Card
    from game.game_state.stat import HasModifiers
    from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
    counts = Counter()
    stat, pipeline, triggers = Card.get_stat, HasModifiers._get_modifier_pipeline, Card.get_trigger_defs
    initialize, close = RuntimeCheckpoint.__init__, RuntimeCheckpoint.close

    def read(self, *args, **kwargs):
        counts["card.stat_reads"] += 1
        return stat(self, *args, **kwargs)

    def modifiers(self, *args, **kwargs):
        counts["stat.pipeline_reads"] += 1
        return pipeline(self, *args, **kwargs)

    def definitions(self, *args, **kwargs):
        counts["card.trigger_definition_reads"] += 1
        return triggers(self, *args, **kwargs)

    def checkpoint(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        counts["checkpoint.calls." + self.mode] += 1
        counts["checkpoint.initial_records"] += len(self._restore)
        counts["checkpoint.seen_objects"] += len(self._seen)

    def finish(self):
        if not getattr(self, "_diagnostic_closed", False):
            self._diagnostic_closed = True
            counts["checkpoint.final_records"] += len(self._restore)
        return close(self)

    with patch.object(Card, "get_stat", read), patch.object(HasModifiers, "_get_modifier_pipeline", modifiers), \
            patch.object(Card, "get_trigger_defs", definitions), patch.object(RuntimeCheckpoint, "__init__", checkpoint), \
            patch.object(RuntimeCheckpoint, "close", finish):
        matches = [outcome(play(seed)) for seed in (1, 2, 3)]
    return dict(counts=counts, matches=matches)


def read_fixtures():
    from benchmarks.core_reads import board
    from immutabledict import immutabledict
    from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition,
        ContinuousEffectState, PermanentDuration, DynamicTargetingStrategy, AddIntModifier)
    from game.target.continuous_targets import CardIncarnationsSpec
    from game.stat_type import STAT_POWER
    base, unit = board(0)
    modified, other = board(0)
    for index in range(3):
        definition = ContinuousEffectDefinition(PermanentDuration(), other, modified.time_stamp,
            DynamicTargetingStrategy(CardIncarnationsSpec(((other, other.zone_revision),))),
            immutabledict({STAT_POWER: (AddIntModifier(1),)}))
        modified.add_continuous_effect(ContinuousEffect(f"fixed-{index}", definition, ContinuousEffectState(set())))
    assert unit.get_power(base) == 2 and other.get_power(modified) == 5
    readers = {
        "power_unmodified": lambda: unit.get_power(base),
        "types_unmodified": lambda: unit.get_types(base),
        "power_three_immutable_effects": lambda: other.get_power(modified),
        "types_with_unrelated_power_effects": lambda: other.get_types(modified),
    }
    result = {}
    for name, reader in readers.items():
        reader()
        samples = [timeit(reader, number=20000) * 1e6 / 20000 for _ in range(5)]
        result[name] = dict(median_us=median(samples), samples_us=samples)
    return dict(read_only_microseconds=result,
                scope="Warm read-only one-card fixtures, 5 samples x 20000 calls, timeit default disabled GC; not whole-game or MCTS throughput.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = dict(work=work_counts(), micro=read_fixtures())
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["work"]["counts"]), flush=True)


if __name__ == "__main__":
    main()
