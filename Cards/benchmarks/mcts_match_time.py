"""Whole-game MCTS timing through production controllers, stdout and RAM only.

Two games per opponent swap red/white and the starting player. This is a small
latency sample, not a playing-strength evaluation. Every settings override is
printed; no run directory, model, temporary file or result file is written.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter, process_time

from benchmarks.environment import source_fingerprint
from game.ai.game_origin import GameOrigin
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter
from game.simulation.config import make_controller
from game.simulation.match_runner import run_match
from game.simulation.search_config import SearchAgentConfig


def emit(kind, **data):
    print(json.dumps(dict(kind=kind, **data), ensure_ascii=True), flush=True)


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return dict(count=0)
    return dict(count=len(ordered), min=ordered[0], median=median(ordered),
                max=ordered[-1], mean=sum(ordered)/len(ordered))


def play(settings, *, opponent, seed, swap, max_turns, progress_seconds=30):
    colors = ('white', 'red') if swap else ('red', 'white')
    starting = int(swap)
    decks = tuple(load_arena_starter(c) for c in colors)
    names = ('MCTS', opponent.capitalize())
    origin = GameOrigin(decks, game_catalog, names=names, starting_player_idx=starting)
    agents = (make_controller(dict(type='mcts', mcts=settings), seed=seed,
                             max_decisions=10000, search_origin=origin),
              make_controller(dict(type=opponent), seed=seed+1,
                             max_decisions=10000))
    samples, stop_reasons, fallbacks = [], Counter(), Counter()
    last = started = perf_counter()
    cpu = process_time()

    def observe(request, detail):
        nonlocal last
        metrics = (detail.get('search') or {}).get('metrics')
        if metrics:
            samples.append(dict(turn=request.state.turn.number,
                **{k: metrics.get(k, 0) for k in (
                    'iterations_completed', 'iterations_started', 'worlds_built',
                    'root_actions', 'root_actions_visited', 'elapsed_ms', 'cpu_ms',
                    'advance_attempts', 'interrupted_trajectories')}))
            stop_reasons[(detail.get('search') or {}).get('stop_reason', 'unknown')] += 1
        if detail['used_fallback']:
            fallbacks[detail['reason']] += 1
        if perf_counter()-last >= progress_seconds:
            last = perf_counter()
            emit('progress', opponent=opponent, seed=seed, colors=colors, starting=starting,
                 elapsed_seconds=last-started,
                 turn=request.state.turn.number if request.state else 0,
                 searched=len(samples), last_search=samples[-1] if samples else None,
                 fallback_reasons=dict(fallbacks))

    agents[0].on_search_decision = observe
    emit('match_start', opponent=opponent, seed=seed, colors=colors, starting=starting)
    result = run_match(colors, seed=seed, starting_player=starting, controllers=agents,
        decklists=decks, names=names, max_turns=max_turns, max_decisions=10000,
        collect_decision_stats=True)
    total_wall, total_cpu = perf_counter()-started, process_time()-cpu
    return dict(opponent=opponent, swap=swap, result=asdict(result),
        wall_seconds=total_wall, cpu_seconds=total_cpu,
        cpu_wall_ratio=total_cpu/total_wall,
        completed_trajectories_per_search=distribution([s['iterations_completed'] for s in samples]),
        root_coverage_per_search=distribution([
            s['root_actions_visited']/s['root_actions'] for s in samples if s['root_actions']]),
        stop_reasons=dict(stop_reasons), fallback_reasons=dict(fallbacks), search_samples=samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--settings', type=Path, default=root/'configs/mcts_play_128.json')
    parser.add_argument('--opponents', nargs='+', choices=('simple', 'modular'), default=['simple', 'modular'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--seats', nargs='+', type=int, choices=(0, 1), default=[0, 1])
    parser.add_argument('--max-turns', type=int, default=100)
    parser.add_argument('--iterations', type=int)
    parser.add_argument('--max-search-ms', type=float)
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text(encoding='utf-8'))
    for field in ('iterations', 'max_search_ms'):
        if getattr(args, field) is not None:
            settings[field] = getattr(args, field)
    settings = asdict(SearchAgentConfig.parse(settings))
    before = source_fingerprint(root)
    emit('configuration', settings=settings, source=before, python=platform.python_version(),
         timing_scope='Serial unlogged whole games including live decision telemetry; no trained model.')
    rows = []
    for seed in args.seeds:
        for opponent in args.opponents:
            for seat in args.seats:
                row = play(settings, opponent=opponent, seed=seed, swap=bool(seat), max_turns=args.max_turns)
                rows.append(row)
                emit('match_result', **row)
                if row['result']['status'] not in ('win', 'draw'):
                    emit('incomplete', reason='Match did not reach a terminal result; excluded from complete-game timing.')
    emit('summary', games=len(rows), completed=sum(r['result']['status'] in ('win', 'draw') for r in rows),
         source_unchanged=before == source_fingerprint(root),
         by_opponent={op: dict(
             complete_seconds=distribution([r['result']['elapsed_seconds'] for r in rows
                 if r['opponent']==op and r['result']['status'] in ('win', 'draw')]),
             mcts_wins=sum(r['result']['winner_index']==0 for r in rows if r['opponent']==op))
             for op in args.opponents},
         warning='Small sample for latency, not proof of strong MCTS or a reliable win rate.')


if __name__ == '__main__':
    main()
