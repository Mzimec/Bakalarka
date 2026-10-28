"""One complete production game with explicit JSONL diagnostics and replay profiles.

Default output is stdout. Only --output opts into a new log file; no temporary
files, snapshots or binary profiles are written. Timers add diagnostic overhead.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import cProfile
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch
import sys

from benchmarks.environment import source_fingerprint
from benchmarks.mcts_decision_latency import Components, answer, detached_planner, profile_rows
from benchmarks.mcts_request_time import distribution
from game.simulation.match_runner import public_value


class DiagnosticLog:
    """!
    @brief Merge engine events and diagnostics into one flushed JSONL stream.
    """

    def __init__(self, stream):
        self.stream, self.sequence = stream, 0

    def emit(self, kind, **data):
        """! @brief Append one detached, globally sequenced diagnostic record. """
        self.sequence += 1
        row = dict(public_value(data), kind=kind, seq=self.sequence)
        self.stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.stream.flush()

    def write(self, text):
        """! @brief Accept complete JSONL lines from the production match logger. """
        for line in text.splitlines():
            if line.strip():
                row = json.loads(line)
                row.pop('seq', None)
                self.emit(row.pop('kind'), **row)
        return len(text)

    def flush(self):
        """! @brief Persist the explicit log without retaining native game objects. """
        self.stream.flush()


class GameDiagnostics:
    """!
    @brief Associate each live MCTS position with component costs and its choice.

    Scope exclusive times partition only hooked work. Simulator stages and
    search metrics overlap those scopes and must not be added to them.
    """

    def __init__(self, log, capture_index=87):
        self.log, self.capture_index = log, capture_index
        self.rows, self.captures = [], {}
        self.search_index, self.last_profile = 0, None
        self.started = self.last_progress = perf_counter()

    @contextmanager
    def installed(self):
        """! @brief Time production searches without changing budgets or actions. """
        from game.ai.information_set_search import InformationSetSearch
        original = InformationSetSearch.search

        def search(planner, factory, information, **kwargs):
            self.search_index += 1
            index = self.search_index
            view = information.current.observation()
            saved = detached_planner(planner) if index in (1, self.capture_index) else None
            self.log.emit('search_start', search_index=index,
                          request=information.current.request_kind, view=view,
                          information_key=information.key,
                          actions=[a.describe() for a in information.actions])
            components = Components()
            started, cpu = perf_counter(), process_time()
            with components.installed():
                result = original(planner, factory, information, **kwargs)
            seconds, cpu_seconds = perf_counter()-started, process_time()-cpu
            profile = dict(search_index=index, seconds=seconds, cpu_seconds=cpu_seconds,
                           **components.summary())
            self.last_profile = profile
            self.log.emit('search_profile', **profile, metrics=asdict(result.metrics))
            if saved is not None:
                label = 'fast' if index == 1 else 'slow'
                self.captures[label] = saved, factory, information, kwargs, result
            return result

        with patch.object(InformationSetSearch, 'search', search):
            yield self

    def observe(self, agent, request, detail):
        """! @brief Log the exact player view and detached telemetry before execution. """
        info = agent.last_information
        row = dict(decision_index=len(self.rows)+1,
                   search_index=self.search_index if detail.get('search') else None,
                   request=type(request).__name__, view=info.current.observation(),
                   detail=detail,
                   profile=self.last_profile if detail.get('search') else None)
        self.rows.append(row)
        self.log.emit('mcts_request', **row)
        if perf_counter()-self.last_progress >= 25:
            self.last_progress = perf_counter()
            metrics = (detail.get('search') or {}).get('metrics', {})
            print(json.dumps(dict(kind='progress', seconds=perf_counter()-self.started,
                turn=row['view']['turn'], phase=row['view']['phase'],
                decisions=len(self.rows), searches=self.search_index,
                request=row['request'], last_decision_ms=detail['elapsed_ms'],
                last_search_ms=metrics.get('elapsed_ms'),
                log_records=self.log.sequence)), flush=True)

    def summary(self):
        """! @brief Aggregate latency per request while preserving original positions. """
        grouped = []
        for kind in sorted({r['request'] for r in self.rows}):
            rows = [r for r in self.rows if r['request'] == kind]
            searched = [r for r in rows if r['detail'].get('search')]
            grouped.append(dict(request=kind,
                decisions=distribution([r['detail']['elapsed_ms'] for r in rows]),
                searches=distribution([r['detail']['search']['metrics']['elapsed_ms'] for r in searched]),
                reasons=dict(Counter(r['detail']['reason'] for r in rows))))
        return dict(by_request=grouped,
                    slowest_decisions=[r['decision_index'] for r in
                        sorted(self.rows, key=lambda r:-r['detail']['elapsed_ms'])[:12]],
                    stage_ms_totals={name: sum(r['detail']['stage_ms'].get(name, 0) for r in self.rows)
                        for name in sorted({k for r in self.rows for k in r['detail']['stage_ms']})})

    def replay_profiles(self):
        """! @brief Profile captured inputs after the match and verify identical search work. """
        from game.ai.information_set_search import InformationSetSearch
        for label, (planner, factory, info, kwargs, expected) in self.captures.items():
            components, profiler = Components(), cProfile.Profile()
            started = perf_counter()
            with components.installed():
                if label == 'slow':
                    profiler.enable()
                try:
                    actual = InformationSetSearch.search(planner, factory, info, **kwargs)
                finally:
                    profiler.disable()
            identical = answer(actual) == answer(expected)
            self.log.emit('replay_profile', label=label, view=info.current.observation(),
                cprofile_enabled=label == 'slow', seconds=perf_counter()-started,
                identical_answer_and_work=identical, expected=public_value(answer(expected)),
                actual=public_value(answer(actual)), metrics=asdict(actual.metrics),
                **components.summary(),
                top_cumulative=profile_rows(profiler, 'cumulative_seconds', 50) if label == 'slow' else [],
                top_self=profile_rows(profiler, 'self_seconds', 50) if label == 'slow' else [])
            print(json.dumps(dict(kind='replay_done', label=label,
                                 identical_answer_and_work=identical)), flush=True)
            if not identical:
                raise AssertionError('Profile replay changed answer or work; do not attribute its costs to the original search')


def main():
    """! @brief Run the configured duel and optionally write its single explicit log. """
    from game.ai.game_origin import GameOrigin
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.config import make_controller
    from game.simulation.match_runner import run_match
    from game.simulation.search_config import SearchAgentConfig
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--settings', type=Path, default=root/'configs/mcts_play_128_native.json')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-turns', type=int, default=100)
    parser.add_argument('--iterations', type=int)
    parser.add_argument('--capture-index', type=int, default=87)
    parser.add_argument('--profile-replay', action='store_true')
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding='utf-8'))
    if args.iterations is not None:
        settings['iterations'] = args.iterations
    settings = asdict(SearchAgentConfig.parse(settings))
    stream = args.output.open('x', encoding='utf-8') if args.output else sys.stdout
    try:
        log = DiagnosticLog(stream)
        before = source_fingerprint(root)
        log.emit('diagnostic_configuration', settings=settings, seed=args.seed, source=before,
                 colors=['white', 'red'], opponent='modular', starting_player=1,
                 capture_index=args.capture_index, timings='Instrumented whole game; replay profiling is separate',
                 scope_note='Scope exclusive times partition hooked work only. Other timing scopes overlap.')
        decks = tuple(load_arena_starter(c) for c in ('white', 'red'))
        origin = GameOrigin(decks, game_catalog, names=('MCTS', 'Modular'), starting_player_idx=1)
        agents = (make_controller(dict(type='mcts', mcts=settings), seed=args.seed,
            max_decisions=10000, search_origin=origin),
            make_controller(dict(type='modular'), seed=args.seed+1, max_decisions=10000))
        diagnostics = GameDiagnostics(log, args.capture_index)
        agents[0].on_search_decision = lambda request, detail: diagnostics.observe(agents[0], request, detail)
        started, cpu = perf_counter(), process_time()
        with diagnostics.installed():
            result = run_match(('white', 'red'), seed=args.seed, starting_player=1,
                controllers=agents, decklists=decks, names=('MCTS', 'Modular'),
                max_turns=args.max_turns, max_decisions=10000, collect_decision_stats=True,
                log_stream=log)
        summary = dict(wall_seconds=perf_counter()-started, cpu_seconds=process_time()-cpu,
                       result=asdict(result), **diagnostics.summary())
        log.emit('diagnostic_match_result', **summary)
        print(json.dumps(dict(kind='match_done', wall_seconds=summary['wall_seconds'],
            result=result.status, winner=result.winner, turns=result.turns,
            searches=diagnostics.search_index, output=str(args.output))), flush=True)
        if result.status not in ('win', 'draw', 'turn_limit'):
            raise RuntimeError(f'Match failed: {result.status}: {result.error}')
        if args.profile_replay:
            diagnostics.replay_profiles()
        log.emit('diagnostic_complete', source_unchanged=before == source_fingerprint(root))
    except BaseException as error:
        if 'log' in locals():
            log.emit('diagnostic_error', error=type(error).__name__, message=str(error))
        raise
    finally:
        if args.output:
            stream.close()


if __name__ == '__main__':
    main()
