"""Capture fast/slow production searches in RAM, then profile identical replays.

No runtime files. Profiled replay times include cProfile overhead and are not
whole-game latency estimates. Search budgets and game choices stay unchanged.
"""
import argparse
from collections import defaultdict
from contextlib import ExitStack, contextmanager, nullcontext
from copy import copy, deepcopy
import cProfile
from dataclasses import asdict, replace
import gc
import json
from pathlib import Path
import pstats
from time import perf_counter, process_time
from unittest.mock import patch

from benchmarks.environment import source_fingerprint
from benchmarks.mcts_match_time import play, emit
from benchmarks.mcts_request_time import distribution


class Captured(BaseException):
    """Intentional prefix boundary, not an engine error or a completed game."""


def detached_planner(planner):
    """Clone mutable tree counters without copying immutable observations."""
    from game.ai.search_tree_memory import EdgeTable, EdgeStatistics
    result = copy(planner)
    result.tree_memory = memory = copy(planner.tree_memory)
    memory.nodes = {key: replace(node,
        edges=EdgeTable((a, EdgeStatistics(s.visits, s.total)) for a, s in node.edges.items()),
        probabilities=dict(node.probabilities), children=dict(node.children),
        hierarchy=deepcopy(node.hierarchy)) for key, node in memory.nodes.items()}
    return result


def answer(result):
    m = result.metrics
    return (result.choice, result.value, result.estimates, result.stop_reason,
            result.used_fallback, m.iterations_completed, m.advance_attempts,
            m.simulator_advance_calls, m.world_restores, m.checkpoint_hits)


class Components:
    """Exclusive scopes partition only the hooked work, not all engine work."""
    def __init__(self):
        self.stack, self.rows, self.simulators = [], defaultdict(lambda: [0, 0., 0.]), []

    @contextmanager
    def timed(self, name):
        frame = [perf_counter(), 0.]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = perf_counter() - frame[0]
            self.stack.pop()
            if self.stack:
                self.stack[-1][1] += elapsed
            row = self.rows[name]
            row[0] += 1
            row[1] += elapsed * 1000
            row[2] += max(0., elapsed-frame[1]) * 1000

    @contextmanager
    def installed(self):
        import game.ai.simulation_world as worlds
        import game.ai.checkpoint_simulator as checkpoints
        from game.ai.observation import PlayerObservationBuilder
        from game.ai.generation_candidates import CandidateFrontier
        from game.ai.information_set_search import _Run
        from game.ai.state_value import StateValueEvaluator
        from game.ai.search_tree_memory import SearchTreeMemory
        from game.ai.search_simulator import SearchSimulator
        original_init = SearchSimulator.__init__

        def initialize(sim, *args, **kwargs):
            kwargs['profile_advance'] = True
            original_init(sim, *args, **kwargs)
            self.simulators.append(sim)

        def timed(original, name):
            def wrapped(*args, **kwargs):
                with self.timed(name):
                    return original(*args, **kwargs)
            return wrapped

        with ExitStack() as patches:
            for owner, attribute, name in (
                (worlds, 'copy_world', 'world_copy'),
                (checkpoints, '_copy_world', 'world_copy'),
                (PlayerObservationBuilder, 'build', 'observation_build'),
                (CandidateFrontier, 'take', 'candidate_frontier'),
                (_Run, 'tree_action', 'tree_selection'),
                (StateValueEvaluator, 'evaluate', 'leaf_value'),
                (SearchTreeMemory, 'load', 'tree_load'),
                (SearchTreeMemory, 'commit', 'tree_commit')):
                patches.enter_context(patch.object(owner, attribute, timed(getattr(owner, attribute), name)))
            patches.enter_context(patch.object(SearchSimulator, '__init__', initialize))
            yield self

    def summary(self):
        stages = defaultdict(lambda: [0, 0.])
        for sim in self.simulators:
            for name, (count, seconds) in sim.stages.items():
                stages[name][0] += count
                stages[name][1] += seconds * 1000
        return dict(scopes={k: dict(calls=n, inclusive_ms=i, exclusive_ms=e)
                           for k, (n, i, e) in self.rows.items()},
                    simulation_stages={k: dict(calls=n, ms=t) for k, (n, t) in stages.items()})


def profile_rows(profile, key, limit=25):
    stats = pstats.Stats(profile).stats
    rows = [dict(file=Path(f).as_posix(), line=line, function=name, primitive_calls=cc,
                 calls=nc, self_seconds=tt, cumulative_seconds=ct)
            for (f, line, name), (cc, nc, tt, ct, _) in stats.items()]
    return sorted(rows, key=lambda r: -r[key])[:limit]


def main():
    from game.ai.information_set_search import InformationSetSearch
    from game.simulation.search_config import SearchAgentConfig
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slow-seconds', type=float, default=20)
    parser.add_argument('--max-searches', type=int, default=70)
    parser.add_argument('--max-turns', type=int, default=22)
    parser.add_argument('--capture-index', type=int, default=0,
                        help='Capture this search index instead of using a latency threshold')
    parser.add_argument('--no-cprofile', action='store_true',
                        help='Use only lightweight component/stage timers for replay')
    parser.add_argument('--compare-observation', action='store_true',
                        help='ABBA previous/current observation readers on captured roots; no profiler')
    parser.add_argument('--compare-compact', action='store_true',
                        help='ABBA deep decode / shared read-only readers on captured roots; no profiler')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = asdict(SearchAgentConfig.parse(json.loads(
        (root/'configs/mcts_play_128_native.json').read_text(encoding='utf-8'))))
    before = source_fingerprint(root)
    original = InformationSetSearch.search
    rows, captures = [], {}

    def search(planner, factory, information, **kwargs):
        saved = detached_planner(planner)
        started, cpu = perf_counter(), process_time()
        result = original(planner, factory, information, **kwargs)
        seconds, cpu_seconds = perf_counter()-started, process_time()-cpu
        view = information.current.observation()
        row = dict(index=len(rows)+1, request=information.current.request_kind,
            turn=view['turn'], phase=view['phase'], battlefield=len(view['battlefield']),
            graveyard=len(view['graveyard']), hand=len(view['hand']),
            pending=len(view['pending_abilities']), root_prefix=len(information.actions),
            seconds=seconds, cpu_seconds=cpu_seconds, metrics=asdict(result.metrics))
        rows.append(row)
        capture = (saved, factory, information, kwargs, result, row)
        if not captures:
            captures['fast'] = capture
        if (len(rows) == args.capture_index if args.capture_index else seconds >= args.slow_seconds):
            captures['slow'] = capture
        emit('latency_search', **{k: v for k, v in row.items() if k != 'metrics'},
             metrics={k: row['metrics'][k] for k in ('iterations_completed', 'advance_attempts',
                 'world_initialization_ms', 'advance_ms', 'world_restores', 'simulator_advance_calls',
                 'checkpoint_capture_ms', 'checkpoint_hits', 'transition_cache_hits', 'root_actions')})
        if 'slow' in captures or len(rows) >= args.max_searches:
            raise Captured()
        return result

    emit('latency_configuration', settings=settings, source=before,
         stop_at_first_search_seconds=args.slow_seconds, capture_index=args.capture_index,
         cprofile=not args.no_cprofile, max_searches=args.max_searches)
    try:
        with patch.object(InformationSetSearch, 'search', search):
            result = play(settings, opponent='modular', seed=42, swap=True,
                          max_turns=args.max_turns, progress_seconds=30)
            emit('latency_prefix_end', status=result['result']['status'])
    except Captured:
        emit('latency_prefix_end', status='captured_prefix', searches=len(rows))
    for kind in sorted({r['request'] for r in rows}):
        group = [r for r in rows if r['request'] == kind]
        emit('latency_request', request=kind, latency_ms=distribution([r['seconds']*1000 for r in group]),
             mean_advances=sum(r['metrics']['advance_attempts'] for r in group)/len(group),
             mean_restores=sum(r['metrics']['world_restores'] for r in group)/len(group))
    for label, (planner, factory, info, kwargs, expected, row) in captures.items():
        if args.compare_observation or args.compare_compact:
            if args.compare_compact:
                from benchmarks.mcts_compact_observation import compact_reference as reference
            else:
                from benchmarks.mcts_observation_reuse import observation_reference as reference
            for repeat, variant in enumerate(('reference', 'optimized', 'optimized', 'reference')):
                candidate = detached_planner(planner)
                gc.collect()
                with reference() if variant == 'reference' else nullcontext():
                    started, cpu = perf_counter(), process_time()
                    actual = original(candidate, factory, info, **kwargs)
                    seconds, cpu_seconds = perf_counter()-started, process_time()-cpu
                assert answer(actual) == answer(expected), 'Observation ablation changed answer/work'
                emit('compact_ablation' if args.compare_compact else 'observation_ablation',
                     label=label, repeat=repeat, variant=variant,
                     seconds=seconds, cpu_seconds=cpu_seconds, identical_answer_and_work=True,
                     metrics={k: getattr(actual.metrics, k) for k in (
                         'iterations_completed', 'advance_attempts', 'simulator_advance_calls',
                         'world_restores', 'world_initialization_ms', 'advance_ms')})
            continue
        gc.collect()
        components, profile = Components(), cProfile.Profile()
        started, cpu = perf_counter(), process_time()
        with components.installed():
            if not args.no_cprofile:
                profile.enable()
            try:
                actual = original(planner, factory, info, **kwargs)
            finally:
                if not args.no_cprofile:
                    profile.disable()
        seconds, cpu_seconds = perf_counter()-started, process_time()-cpu
        assert answer(actual) == answer(expected), 'Profiled replay changed search work or answer'
        emit('latency_profile', label=label, baseline=row, profiled_seconds=seconds,
             cprofile_enabled=not args.no_cprofile,
             cpu_seconds=cpu_seconds, identical_answer_and_work=True, metrics=asdict(actual.metrics),
             **components.summary(),
             top_cumulative=profile_rows(profile, 'cumulative_seconds') if not args.no_cprofile else [],
             top_self=profile_rows(profile, 'self_seconds') if not args.no_cprofile else [])
    emit('latency_final', source_unchanged=before == source_fingerprint(root),
         slow_captured='slow' in captures, prefix_searches=len(rows),
         warning='Diagnostic prefix and instrumented replays, not new whole-game times.')


if __name__ == '__main__':
    main()
