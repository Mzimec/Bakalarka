"""Diagnostic scopes must not change search or count child costs as own work."""
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from runtime_write_guard import deny_writes
from benchmarks.mcts_request_time import RequestProfiler, distribution, settings_for
from benchmarks.search_tactics import tactical_scenario
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator
from game.simulation.search_config import SearchGenerationPolicy


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


def test_distribution_handles_empty_samples_and_nearest_rank_percentile():
    assert distribution([])['mean_ms'] is None
    row = distribution([10., 1., 3.])
    assert row == dict(count=3, total_ms=14., mean_ms=14/3, median_ms=3., p95_ms=10., max_ms=10.)


def test_action_own_time_excludes_nested_decisions_and_scopes_restore_on_errors():
    profiler = RequestProfiler()
    with patch('benchmarks.mcts_request_time.perf_counter', side_effect=[0., 1., 3., 5.]):
        with pytest.raises(RuntimeError):
            with profiler.timed('submitted_action', 'live', 'AbilityAction'):
                with profiler.timed('decision', 'live_MCTSAgent', 'AbilityResolutionRequest'):
                    pass
                raise RuntimeError('test')
    assert profiler.calls['submitted_action', 'live', 'AbilityAction'] == [(5000., 3000.)]
    assert profiler.calls['decision', 'live_MCTSAgent', 'AbilityResolutionRequest'] == [(2000., 2000.)]
    assert profiler.stack.get() == ()


def test_live_averages_do_not_dilute_search_with_forced_requests():
    profiler = RequestProfiler()
    for searched, reason, elapsed in ((True, 'searched', 20.), (False, 'forced_choice', 2.)):
        profiler.live.append(dict(request='PriorityDecisionRequest', options=2 if searched else 1,
            searched=searched, reason=reason, elapsed_ms=elapsed, fallback=False, iterations=4 if searched else 0,
            advances=8 if searched else 0, stages={'generation_ms': 1., **({'search_ms': 18.} if searched else {})}))
    row = profiler.summary()['live_roots'][0]
    assert row['decision']['mean_ms'] == 11.
    assert row['searched']['mean_ms'] == 20.
    assert row['forced']['mean_ms'] == 2.
    assert row['search_stages']['search_ms']['mean_ms'] == 18.
    assert row['iterations'] == 4 and row['options_mean'] == 1.5


@pytest.mark.parametrize('native', [False, True])
def test_instrumentation_preserves_all_search_estimates_and_restores_hooks(native):
    from game.ai.decision_maker import DecisionMaker
    original = DecisionMaker.decide
    policy = SearchGenerationPolicy(progressive_candidates=native, candidate_native_ordering=native)
    results = []
    profiler = RequestProfiler()
    for enabled in (False, True):
        with profiler.installed() if enabled else nullcontext():
            def factory(seed):
                return SearchSimulator(tactical_scenario('answered_shock'), policy_for=policy,
                    track_information=True, structured_records=True)
            root = factory(0).start().player_information()
            result = InformationSetSearch(selection='puct', opponent_policy=ObservationOpponentPolicy(),
                compress_forced=True, require_root_coverage=False, force_initial_coverage=False,
                widening_constant=4 if native else 0,
                budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=2, max_advances=256)
            ).search(factory, root, seed=42)
            assert not result.used_fallback
            results.append((result.choice, result.value, result.estimates, result.stop_reason,
                            result.metrics.iterations_completed, result.metrics.advance_attempts))
        assert DecisionMaker.decide is original
    assert results[0] == results[1]
    summary = profiler.summary()
    assert any(r['domain'] == 'simulation' and r['section'] == 'submitted_action' for r in summary['scopes'])
    assert any(r['request'] == 'PriorityDecisionRequest' and r['stage'] == 'generation'
               for r in summary['simulation_stages'])
    assert profiler.mode.get() == 'live' and profiler.request.get() == 'outside_decision'


def test_benchmark_variants_have_equal_search_limits_and_explicit_generation():
    full, native = settings_for('full'), settings_for('native')
    for field in ('iterations', 'worlds', 'max_depth', 'max_advances', 'max_extensions', 'max_search_ms'):
        assert full[field] == native[field]
    assert not full['progressive_candidates'] and full['widening_constant'] == 0
    assert native['progressive_candidates'] and native['candidate_native_ordering']


def test_retention_summary_separates_structure_old_visits_and_peak_memory():
    profiler = RequestProfiler()
    for structure, visits, size in ((2, 0, 100), (3, 4, 150)):
        profiler.live.append(dict(request='PriorityDecisionRequest', options=2, searched=True,
            reason='searched', elapsed_ms=1, fallback=False, iterations=4, advances=8, stages={},
            search_components=dict(reused_structure_nodes=structure, reused_root_visits=visits,
                                   reuse_charged_bytes=size)))
    row = profiler.summary()['retention']
    assert row['searches'] == row['searches_with_structure'] == 2
    assert row['searches_with_old_visits'] == 1
    assert row['totals']['reused_structure_nodes'] == 5
    assert row['totals']['reused_root_visits'] == 4
    assert row['peak_charged_bytes'] == 150
