"""Ablate registration proofs, narrow player updates and unchanged index rows.

Uses identical recorded roots and budgets, RAM/stdout only. The reference is
the previous implementation including its already retained restore projections.
"""
import argparse
from contextlib import ExitStack, contextmanager, redirect_stdout
from collections import defaultdict
import io
import json
import gc
from pathlib import Path
from statistics import mean
from time import perf_counter
from unittest.mock import patch

from benchmarks.mcts_recorded_roots import capture
from benchmarks.mcts_matched_requests import run
from benchmarks.mcts_restore_maintenance import measure_step
from game.game_state import Card, Player
from game.game_state.registers.card_register import CardRegister
from game.game_state.registers.indexed_register import IndexedRegister
from game.game_state.registers.player_register import (
    PlayerRegister, IK_HEALTH, IK_ALIVE, IK_HAS_LOST, IK_FAILED_DRAW, IK_POISON,
)
from helper.query_system.object_register import IndexUpdate, RegisterUpdateContext


def synchronise(register):
    """! @brief Candidate path retaining equal membership rows, with live refresh. """
    if register._synchronising:
        return
    register.state.refresh_continuous_effects()
    if not register._dirty:
        return
    register._synchronising = True
    try:
        while register._dirty:
            key = next(iter(register._dirty))
            obj = register._dirty[key]
            current = register.changed_index_values(obj, register._dirty_indexes.get(key))
            previous = register._snapshots[key]
            changes = {index: IndexUpdate(previous[index]-value, value-previous[index])
                       for index, value in current.items() if value != previous[index]}
            if changes:
                register.update(obj, RegisterUpdateContext(changes))
                register._snapshots[key] = current if len(current) == len(previous) else previous | current
            register._dirty.pop(key)
            register._dirty_indexes.pop(key, None)
    finally:
        register._synchronising = False


def seed_projection(cache, state, card, snapshot):
    """! @brief Experimental publication of a just-computed native index row. """
    from game.game_state.card_snapshot_cache import _Entry
    from game.game_state.registers.card_register import _CHARACTERISTIC_KEYS
    from immutabledict import immutabledict
    if (not cache._eligible(state, card)
            or any(name in vars(card) or getattr(Card, name) is not method
                   for name, method in _INDEX_READERS.items())
            or not cache._immutable_table(card)):
        return
    signature = cache._index_definition_signature(card)
    if signature is not None:
        characteristics = immutabledict((key, snapshot[key]) for key in _CHARACTERISTIC_KEYS)
        cache._index_entries[id(card)] = _Entry(
            card, card.stats, card.definition, card.get_zone(), card.zone_revision,
            characteristics, signature, card.state.face_down)


_INDEX_READERS = {name: getattr(Card, name) for name in (
    'get_stat', 'get_power', 'get_toughness', 'get_types', 'get_subtypes',
    'get_controller', 'get_zone', 'get_trigger_defs',
    'get_activatable_ability_defs', '_get_modifier_pipeline')}
_RESOURCES = {'health': frozenset({IK_HEALTH, IK_ALIVE}),
              'has_lost': frozenset({IK_HAS_LOST, IK_ALIVE}),
              'failed_draw': frozenset({IK_FAILED_DRAW}),
              'poison_counters': frozenset({IK_POISON})}
_RESOURCE_KEYS = frozenset().union(*_RESOURCES.values())


def player_values(register, player, indexes):
    """! @brief Candidate resource-only projection; custom players remain general. """
    if (indexes is None or not indexes <= _RESOURCE_KEYS
            or type(player) is not Player or type(register) is not PlayerRegister
            or 'index_values' in vars(register)):
        return register.index_values(player)
    return {key: frozenset({getattr(player, attribute)}) for key, attribute in (
        (IK_HEALTH, 'health'), (IK_ALIVE, 'is_alive'), (IK_HAS_LOST, 'has_lost'),
        (IK_FAILED_DRAW, 'failed_draw'), (IK_POISON, 'poison_counters')) if key in indexes}


def resource_setter(attribute, indexes):
    """! @brief Replace a native setter only within the diagnostic context. """
    def assign(player, value):
        setattr(player, '_'+attribute, value)
        if player._game_state is not None:
            player._game_state.player_register.mark_changed(player, indexes)
    return assign


@contextmanager
def maintenance(enabled, *, registration=True, players=True, rows=True):
    """! @brief Install candidates only in an isolated, single-threaded benchmark.

    Production stays unchanged. Global monkeypatches are restored on every exit;
    this context must never surround a live/concurrent match.
    """
    original_register = CardRegister.register
    def register_with_projection(register, card):
        already = register.get_by_key(card.key) is card
        original_register(register, card)
        if (not already and type(register) is CardRegister
                and 'index_values' not in vars(register)
                and '_characteristic_index_values' not in vars(register)):
            seed_projection(register.state._card_snapshots, register.state, card,
                            register._snapshots[card.key])
    with ExitStack() as stack:
        if enabled and registration:
            stack.enter_context(patch.object(CardRegister, 'register', register_with_projection))
        if enabled and players:
            stack.enter_context(patch.object(PlayerRegister, 'changed_index_values', player_values))
            for name, indexes in _RESOURCES.items():
                original = getattr(Player, name)
                stack.enter_context(patch.object(Player, name, property(
                    original.fget, resource_setter(name, indexes), original.fdel, original.__doc__)))
        if enabled and rows:
            # Earlier profilers can leave an identical inherited method bound
            # explicitly on a subclass. Hook the effective native paths too;
            # changing only the base would silently miss that subclass.
            for owner in (IndexedRegister, CardRegister, PlayerRegister):
                stack.enter_context(patch.object(owner, 'synchronise', synchronise))
        yield


class MaintenanceComparison:
    """! @brief Hold captured positions and all paired samples in memory. """

    def __init__(self, captured, repeats):
        self.captured, self.repeats = captured, repeats

    def steps(self):
        """! @brief Check every root branch in two sampled worlds, one action each. """
        samples = defaultdict(list)
        for repeat in range(self.repeats):
            for index in tuple(self.captured)[::1 if repeat % 2 == 0 else -1]:
                planner, factory, info, _ = self.captured[index]
                for seed in (123, 124):
                    for ordinal in range(len(info.actions)):
                        answers = []
                        for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                            with maintenance(enabled):
                                row, answer = measure_step(planner, factory, seed, ordinal, True)
                            answers.append(answer)
                            samples[index].append(dict(improved=enabled, repeat=repeat,
                                                       seed=seed, ordinal=ordinal, **row))
                        assert answers[0] == answers[1], (index, seed, ordinal)
        for index, rows in samples.items():
            summary = {}
            for enabled, label in ((False, 'reference'), (True, 'improved')):
                selected = [r for r in rows if r['improved'] == enabled]
                summary[label] = dict(mean_ms={key: mean(r[key] for r in selected) for key in (
                    'initialize_ms', 'total_ms', 'execution_to_request_ms', 'step_ms', 'restore_and_step_ms')},
                    characteristic_reads={phase: mean(r['characteristic_reads'].get(phase, 0) for r in selected)
                        for phase in ('initialize', 'restore_and_root_menu', 'action', 'successor_menu')})
            print(json.dumps(dict(kind='incremental_maintenance_step', root=index, equivalent=True,
                                  pairs=len(rows)//2, summary=summary, samples=rows)), flush=True)

    def searches(self):
        """! @brief Compare complete searches and assert matching work and answers. """
        samples = []
        for repeat in range(self.repeats):
            for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                output = io.StringIO()
                with maintenance(enabled), redirect_stdout(output):
                    run(1, indices=tuple(self.captured)[::1 if repeat % 2 == 0 else -1],
                        iterations=32, captures=self.captured)
                samples.extend(dict(json.loads(line), improved=enabled, pair=repeat)
                               for line in output.getvalue().splitlines())
        for index in self.captured:
            rows = [r for r in samples if r['root'] == index]
            signatures = []
            for row in rows:
                result = dict(row['result'])
                metrics = result.pop('metrics')
                signatures.append((result, {key: metrics[key] for key in (
                    'iterations_completed', 'advance_attempts', 'simulator_advance_calls',
                    'transition_cache_hits', 'worlds_built')}))
            assert all(s == signatures[0] for s in signatures[1:]), index
            summary = {label: dict(mean_seconds=mean(r['seconds'] for r in rows if r['improved'] == flag),
                                   seconds=[r['seconds'] for r in rows if r['improved'] == flag])
                       for label, flag in (('reference', False), ('improved', True))}
            print(json.dumps(dict(kind='incremental_maintenance_search', root=index,
                equivalent=True, summary=summary, samples=rows)), flush=True)

    def variants(self):
        """! @brief Isolate candidates and count GC, collecting only between samples. """
        variants = (('reference', False, {}),
                    ('registration', True, dict(players=False, rows=False)),
                    ('players_rows', True, dict(registration=False)), ('all', True, {}))
        samples, signatures = [], {}
        for repeat in range(self.repeats):
            for name, enabled, options in variants[::1 if repeat % 2 == 0 else -1]:
                # Normalize garbage inherited from earlier samples. Collection
                # DURING search stays enabled, timed and part of its wall cost.
                gc.collect()
                pauses, started = [], [0.]
                def collect(phase, info):
                    if phase == 'start':
                        started[0] = perf_counter()
                    else:
                        pauses.append(perf_counter()-started[0])
                output = io.StringIO()
                gc.callbacks.append(collect)
                try:
                    with maintenance(enabled, **options), redirect_stdout(output):
                        run(1, indices=tuple(self.captured), captures=self.captured)
                finally:
                    gc.callbacks.remove(collect)
                for line in output.getvalue().splitlines():
                    row = json.loads(line)
                    result = dict(row['result'])
                    metrics = result.pop('metrics')
                    signature = result, {key: metrics[key] for key in (
                        'iterations_completed', 'advance_attempts', 'simulator_advance_calls',
                        'transition_cache_hits', 'worlds_built')}
                    assert signatures.setdefault(row['root'], signature) == signature
                    samples.append(dict(root=row['root'], variant=name, repeat=repeat, seconds=row['seconds']))
                print(json.dumps(dict(kind='maintenance_variant_gc', repeat=repeat, variant=name,
                    gc_seconds=sum(pauses), collections=len(pauses))), flush=True)
        for index in self.captured:
            rows = [row for row in samples if row['root'] == index]
            print(json.dumps(dict(kind='maintenance_variants', root=index, equivalent=True,
                summary={name: mean(r['seconds'] for r in rows if r['variant'] == name)
                         for name, _, _ in variants}, samples=rows)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', action='store_true')
    parser.add_argument('--variants', action='store_true')
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('Repeats must be positive')
    log = Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl'
    captured = capture(log, [87, 102] if args.search or args.variants else [87, 90, 92, 102])
    comparison = MaintenanceComparison(captured, args.repeats)
    (comparison.variants if args.variants else comparison.searches if args.search else comparison.steps)()


if __name__ == '__main__':
    main()
