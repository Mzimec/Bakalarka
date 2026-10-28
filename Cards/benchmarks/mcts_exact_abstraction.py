"""RAM/stdout-only exact-input ablation; no game-strength claim or runtime files."""
import argparse
from dataclasses import replace
import json
from statistics import median
from time import perf_counter

from benchmarks.mcts_top_k import make_request
from benchmarks.mcts_decision_latency import answer
from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.action_priors import HeuristicActionPrior
from game.ai.exact_policy_memory import ExactPolicyMemory
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.search_simulator import SearchSimulator
from game.ai.structured_values import ValueStore
from game.simulation.search_config import SearchGenerationPolicy


def policy(mode):
    return ObservationOpponentPolicy(memory=None if mode == 'off' else
        ExactPolicyMemory(128, 64 * 1024 * 1024, canonical_ids=mode == 'canonical'))


def policy_measurements(repeats=4, calls=10):
    """Measure native menus outside generation, with first-use cost reported separately."""
    for case in ('targets', 'attackers', 'blockers'):
        request = make_request(case)
        options = tuple(request.option_space(SearchGenerationPolicy()(request)))
        info = DecisionProjector(request, options, PlayerRecall(), store=ValueStore()).build().information
        reference = ObservationOpponentPolicy().rank(info)
        rows = []
        for repeat in range(repeats):
            order = ('off', 'exact', 'canonical') if repeat % 2 == 0 else ('canonical', 'exact', 'off')
            for mode in order:
                chooser = policy(mode)
                start = perf_counter()
                assert chooser.rank(info) == reference
                first_ms = (perf_counter() - start) * 1000
                # A distinct full history must still yield the same pure ratings.
                other = replace(info, recall=info.recall.append('event', {'kind': 'benchmark_marker'}))
                start = perf_counter()
                for _ in range(calls):
                    result = chooser.rank(other)
                elapsed = (perf_counter() - start) * 1000 / calls
                assert result == reference and other.fast_key != info.fast_key
                rows.append(dict(mode=mode, first_ms=first_ms, repeated_ms=elapsed,
                                 memory=chooser.memory.summary() if chooser.memory else {}))
        print(json.dumps(dict(kind='policy', case=case, actions=len(info.actions), calls=calls,
            all_ratings_equal=True, medians={mode: {
                key: median(row[key] for row in rows if row['mode'] == mode)
                for key in ('first_ms', 'repeated_ms')} for mode in ('off', 'exact', 'canonical')}, samples=rows)), flush=True)


def search_measurements(repeats=4, iterations=128):
    """Same exact native roots, seeds, trajectories and work budgets in alternating order."""
    from game.game_state import Card
    from game.enums import ZoneType
    from game.cards.starter_cards import starter_catalog
    for case in ('priority', 'crowded'):
        def recipe(controllers):
            game = tactical_scenario('useful_pacifism')(controllers)
            if case == 'crowded':
                owner = game.state.players[1]
                for _ in range(8):
                    owner.add_card(Card(starter_catalog()['Impassioned Orator'], owner), ZoneType.BATTLEFIELD)
            return game
        def factory(seed):
            return SearchSimulator(recipe, policy_for=tactical_policy, track_information=True,
                                   structured_records=True)
        info = factory(0).start().player_information()
        rows, expected = [], None
        for repeat in range(repeats):
            for mode in (('off', 'exact') if repeat % 2 == 0 else ('exact', 'off')):
                chooser = policy(mode)
                search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
                    action_prior=HeuristicActionPrior(chooser), selection='puct', compress_forced=True,
                    require_root_coverage=False, force_initial_coverage=False,
                    budget=InformationSearchBudget(worlds=2, iterations=iterations, max_depth=4,
                                                   max_extensions=8, max_advances=8192))
                start = perf_counter()
                result = search.search(factory, info, seed=42)
                elapsed = perf_counter() - start
                signature = answer(result)
                if expected is None:
                    expected = signature
                assert signature == expected
                row = dict(mode=mode, seconds=elapsed, completed=result.metrics.iterations_completed,
                           advances=result.metrics.advance_attempts, native_calls=result.metrics.simulator_advance_calls,
                           fallback=result.used_fallback, policy_hits=result.metrics.policy_action_hits,
                           policy_misses=result.metrics.policy_action_misses,
                           normalization_ms=result.metrics.policy_normalization_ms)
                rows.append(row)
                print(json.dumps(dict(kind='search_sample', case=case, **row)), flush=True)
        print(json.dumps(dict(kind='search', case=case, iterations=iterations, same_work=True,
            same_values_visits_and_choice=True, medians_seconds={mode: median(row['seconds'] for row in rows
                if row['mode'] == mode) for mode in ('off', 'exact')}, samples=rows)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('policy', 'search', 'all'), default='all')
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--calls', type=int, default=10)
    parser.add_argument('--iterations', type=int, default=128)
    args = parser.parse_args()
    if min(args.repeats, args.calls, args.iterations) < 1:
        parser.error('counts must be positive')
    if args.mode in ('policy', 'all'):
        policy_measurements(args.repeats, args.calls)
    if args.mode in ('search', 'all'):
        search_measurements(args.repeats, args.iterations)


if __name__ == '__main__':
    main()
