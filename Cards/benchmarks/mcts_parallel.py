"""Captured main-phase MCTS: shared-tree baseline and equivalent root shards.

The sequential-shards executor is a benchmark reference only. It executes the
same independent worker jobs, seeded worlds and aggregation as both backends.
Use a work cap without a wall limit; compare work/answers before timings.
"""
import argparse
from concurrent.futures import Future
from dataclasses import asdict, replace
import json
from hashlib import sha256
from pathlib import Path
from statistics import median

from benchmarks.engine_inner import fingerprint
from game.ai.simple_agent import SimpleAgent
from game.ai.knowledge import PlayerKnowledge
from game.ai.game_origin import GameOrigin
from game.ai.current_position_worlds import CurrentPositionWorlds
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.action_priors import HeuristicActionPrior
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.parallel_search import ParallelInformationSetSearch
from game.simulation.search_config import SearchGenerationPolicy
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter


class _Captured(BaseException):
    pass


class _SequentialJobs:
    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future
    def shutdown(self, **kwargs):
        pass


def capture(own_deck, other_deck, seed, turn, *, request_kind=PriorityDecisionRequest):
    policy = SearchGenerationPolicy()
    class Probe(SimpleAgent):
        def __init__(self):
            super().__init__()
            self.knowledge = PlayerKnowledge()
        def receive_player_event(self, event, *, game_token, player):
            self.knowledge.bind(game_token, player)
            self.knowledge.observe(event)
        def _decide(self, request):
            if (isinstance(request, request_kind) and request.state.turn.number >= turn
                    and (request_kind is not PriorityDecisionRequest
                         or request.state.turn.phase.name == 'PRECOMBAT_MAIN')):
                options = tuple(request.option_space(policy(request)))
                if len(options) > 1:
                    prior = PlayerRecall()
                    self.information = DecisionProjector(request, options, prior).build().information
                    provider = CurrentPositionWorlds(origin, policy_for=policy, view_mode='search',
                        structured_records=True, native_forced=True, projection_cache_size=128)
                    self.worlds = provider.prepare(request, self.information, prior, self.knowledge)
                    if self.worlds is None:
                        raise RuntimeError(provider.unsupported_reason)
                    raise _Captured()
            return super()._decide(request)
    agent = Probe()
    origin = GameOrigin((load_arena_starter(own_deck), load_arena_starter(other_deck)), game_catalog,
                        names=('Search', 'Simple'), starting_player_idx=0)
    try:
        game = origin.scenario(seed)((agent, SimpleAgent()))
        while not game.state.is_game_over:
            game.loop.step(game.state)
    except _Captured:
        pass
    if not hasattr(agent, 'worlds'):
        raise RuntimeError(f'No nontrivial captured {request_kind.__name__} root')
    return agent.worlds, agent.information


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=64)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--own-deck', default='red')
    parser.add_argument('--other-deck', default='white')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--turn', type=int, default=9)
    args = parser.parse_args()
    if args.workers < 2 or args.repeats < 1:
        parser.error('Use at least two workers and one warm repetition')
    args.output.mkdir(parents=True, exist_ok=False)
    cards = Path(__file__).resolve().parents[1]
    stamp = fingerprint(cards)
    script_stamp = sha256(Path(__file__).read_bytes()).hexdigest()
    worlds, information = capture(args.own_deck, args.other_deck, args.seed, args.turn)
    def base():
        policy = ObservationOpponentPolicy()
        return InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
            selection='puct', action_prior=HeuristicActionPrior(policy), compress_forced=True,
            require_root_coverage=False, force_initial_coverage=False,
            budget=InformationSearchBudget(worlds=2, iterations=args.iterations, max_depth=4,
                                            max_advances=4096, max_extensions=8))
    planners = {'shared_serial': ParallelInformationSetSearch(base()),
        'sequential_shards': ParallelInformationSetSearch(base(), workers=args.workers, backend='thread'),
        'threads': ParallelInformationSetSearch(base(), workers=args.workers, backend='thread'),
        'processes': ParallelInformationSetSearch(base(), workers=args.workers, backend='process')}
    sequential = _SequentialJobs()
    planners['sequential_shards']._pool = lambda count: sequential
    results = []
    try:
        for repeat in range(args.repeats + 1):
            order = list(planners)
            if repeat % 2:
                order.reverse()
            for name in order:
                value = planners[name].search(worlds, information, seed=42)
                if name == 'sequential_shards':
                    # Inline jobs charge the caller thread too; do not count
                    # their CPU a second time as if they ran on other threads.
                    value = replace(value, metrics=replace(value.metrics, parallel_backend='sequential_reference',
                        cpu_ms=value.metrics.cpu_ms-value.metrics.parallel_worker_cpu_ms))
                results.append(dict(variant=name, cold=repeat == 0, result=asdict(value)))
                print(name, repeat, round(value.metrics.elapsed_ms, 1), 'ms', flush=True)
    finally:
        for planner in planners.values():
            planner.close()
    def semantic(row):
        result = row['result']
        metrics = result['metrics']
        return [result[key] for key in ('choice', 'value', 'used_fallback', 'root_coverage_complete', 'estimates', 'fallback_reason')] + [
            {key: metrics[key] for key in ('iterations_started', 'iterations_completed', 'advance_attempts',
                'simulator_advance_calls', 'trajectory_runs', 'interruption_reasons', 'forced_transitions')}]
    equivalent = [r for r in results if r['variant'] != 'shared_serial']
    assert all(semantic(r) == semantic(equivalent[0]) for r in equivalent)
    summary = {}
    for variant in planners:
        warm = [r['result']['metrics'] for r in results if r['variant'] == variant and not r['cold']]
        summary[variant] = dict(wall_ms=median(r['elapsed_ms'] for r in warm),
            cpu_ms=median(r['cpu_ms'] for r in warm), completed=warm[0]['iterations_completed'],
            advances=warm[0]['advance_attempts'], engine_calls=warm[0]['simulator_advance_calls'])
    assert stamp == fingerprint(cards)
    assert script_stamp == sha256(Path(__file__).read_bytes()).hexdigest()
    (args.output/'results.json').write_text(json.dumps(dict(fingerprint=stamp, source_unchanged=True,
        benchmark_fingerprint=script_stamp, information_key=information.key,
        arguments=vars(args)|{'output': str(args.output)},
        shard_work_and_results_equal=True, summary=summary, samples=results), indent=2), encoding='utf-8')
    print(summary)


if __name__ == '__main__':
    main()
