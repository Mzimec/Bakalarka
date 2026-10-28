"""Compare cold vs rebound native index projections; all samples go to stdout."""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from unittest.mock import patch

from benchmarks.mcts_recorded_roots import capture
from benchmarks.mcts_matched_requests import RequestStepMeasurement, run


@contextmanager
def retained_indexes(enabled, copies=None):
    """! @brief Ablate only transferred index proofs, including checkpoint copies. """
    import game.ai.simulation_world as worlds
    import game.ai.checkpoint_simulator as checkpoints
    original = worlds.copy_world
    def copy(*args, **kwargs):
        result = original(*args, **dict(kwargs, retain_index_projections=enabled))
        if copies is not None:
            copies.append(len(result[0]._card_snapshots._index_entries))
        return result
    with patch.object(worlds, 'copy_world', copy), patch.object(checkpoints, '_copy_world', copy):
        yield


def endpoint(node):
    """! @brief Compare detached histories, observations and recall, not owner tokens. """
    return (node.history, node.phase_steps, node.terminal, node.cutoff, node._views,
            node._recalls, node.player_information() if node.decision else None)


def measure_step(planner, factory, seed, ordinal, enabled):
    """! @brief Count characteristic reads before restore, during A and during B. """
    from game.game_state.registers.card_register import CardRegister
    from game.ai.search_simulator import _Replay
    reads, copies, phase = Counter(), [], ['initialize']
    original_read, original_decide = CardRegister._characteristic_index_values, _Replay.decide
    measurement = None
    def read(*args, **kwargs):
        reads[phase[0]] += 1
        return original_read(*args, **kwargs)
    def decide(replay, request):
        if measurement is not None and replay.simulator is simulator and replay.cursor == len(replay.history):
            if measurement.calls == 1:
                phase[0] = 'successor_menu'
        return original_decide(replay, request)
    with retained_indexes(enabled, copies), \
            patch.object(CardRegister, '_characteristic_index_values', staticmethod(read)), \
            patch.object(_Replay, 'decide', decide):
        started = perf_counter()
        simulator = factory(seed)
        root = simulator.start()
        initialized = perf_counter()
        action = root.player_information().actions[ordinal]
        measurement = RequestStepMeasurement(simulator, root, action, planner, seed)
        choose = measurement.choose
        def selected(node):
            result = choose(node)
            if measurement.calls == 1:
                phase[0] = 'action'
            return result
        measurement.choose = selected
        phase[0] = 'restore_and_root_menu'
        row = measurement.execute()
        finished = perf_counter()
    return dict(retained=enabled, initialize_ms=1000*(initialized-started),
                total_ms=1000*(finished-started), action=action.kind,
                characteristic_reads=dict(reads), copied_projection_rows=copies, **row), endpoint(measurement.child)


def steps(captured, repeats):
    samples = defaultdict(list)
    indices = tuple(captured)
    for repeat in range(repeats):
        for index in indices if repeat % 2 == 0 else reversed(indices):
            planner, factory, info, _ = captured[index]
            for seed in (123, 124):
                for ordinal in range(len(info.actions)):
                    answers = []
                    for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
                        row, answer = measure_step(planner, factory, seed, ordinal, enabled)
                        answers.append(answer)
                        samples[index].append(dict(repeat=repeat, seed=seed, ordinal=ordinal, **row))
                    assert answers[0] == answers[1], (index, seed, ordinal)
    for index, rows in samples.items():
        summary = {}
        for enabled in (False, True):
            selected = [r for r in rows if r['retained'] == enabled]
            names = ('initialize_ms', 'total_ms', 'execution_to_request_ms', 'step_ms', 'restore_and_step_ms')
            summary['retained' if enabled else 'cold'] = dict(
                mean_ms={k: mean(r[k] for r in selected) for k in names},
                median_ms={k: median(r[k] for r in selected) for k in names},
                mean_characteristic_reads={p: mean(r['characteristic_reads'].get(p, 0) for r in selected)
                    for p in ('initialize', 'restore_and_root_menu', 'action', 'successor_menu')},
                mean_copied_projection_rows=mean(sum(r['copied_projection_rows']) for r in selected))
        print(json.dumps(dict(kind='restore_maintenance_step', root=index,
            request=captured[index][2].current.request_kind, pairs=len(rows)//2,
            equivalent=True, summary=summary, samples=rows)), flush=True)


def searches(captured, repeats):
    samples = []
    for repeat in range(repeats):
        for enabled in ((False, True) if repeat % 2 == 0 else (True, False)):
            output = io.StringIO()
            with retained_indexes(enabled), redirect_stdout(output):
                run(1, indices=tuple(captured) if repeat % 2 == 0 else tuple(reversed(captured)),
                    iterations=32, captures=captured)
            for line in output.getvalue().splitlines():
                samples.append(dict(json.loads(line), retained=enabled, repeat=repeat))
    for index in captured:
        rows = [r for r in samples if r['root'] == index]
        signatures = []
        for row in rows:
            result = dict(row['result'])
            metrics = result.pop('metrics')
            signatures.append((result, {k: metrics[k] for k in (
                'iterations_completed', 'advance_attempts', 'simulator_advance_calls',
                'transition_cache_hits', 'worlds_built')}))
        assert all(s == signatures[0] for s in signatures[1:]), index
        print(json.dumps(dict(kind='restore_maintenance_search', root=index, equivalent=True,
            summary={name: dict(mean_seconds=mean(r['seconds'] for r in rows if r['retained'] == flag),
                               median_seconds=median(r['seconds'] for r in rows if r['retained'] == flag))
                     for name, flag in (('cold', False), ('retained', True))}, samples=rows)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--search', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('Repeats must be positive')
    log = Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl'
    captured = capture(log, [87, 102] if args.search else [87, 90, 92, 102])
    (searches if args.search else steps)(captured, args.repeats)


if __name__ == '__main__':
    main()
