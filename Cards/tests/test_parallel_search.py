"""Actual spawn/thread workers, native worlds, pooled budgets and root statistics."""
from dataclasses import replace
from inspect import getclosurevars
import os

import cloudpickle
import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.information_set_search import (
    InformationSetSearch, InformationSearchBudget, ActionEstimate,
    InformationSearchResult, InformationSearchMetrics,
)
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.parallel_search import ParallelInformationSetSearch, merge_results
from game.ai.search_simulator import SearchSimulator
from game.simulation.search_config import SearchAgentConfig


def factory(seed):
    return SearchSimulator(tactical_scenario('lethal_shock'), policy_for=tactical_policy,
                           track_information=True, structured_records=True)


def planner(selection='puct', **kwargs):
    return InformationSetSearch(opponent_policy=ObservationOpponentPolicy(),
        rollout_policy=ObservationOpponentPolicy(), selection=selection,
        compress_forced=True, require_root_coverage=False, force_initial_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=12, max_depth=2, max_advances=192), **kwargs)


def test_one_worker_is_exact_serial_without_transport_or_executor(monkeypatch):
    import game.ai.parallel_search as module
    monkeypatch.setattr(module, '_codec', lambda: pytest.fail('Serial mode must not serialize'))
    root = factory(0).start().player_information()
    base = planner()
    expected = base.search(factory, root, seed=42)
    with ParallelInformationSetSearch(base) as parallel:
        result = parallel.search(factory, root, seed=42)
        assert replace(result, metrics=expected.metrics) == expected
        assert result.metrics.parallel_backend == 'serial' and parallel._executor is None


@pytest.mark.parametrize('backend', ['thread', 'process'])
@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
def test_real_workers_preserve_native_legality_and_global_work_caps(backend, selection):
    root = factory(0).start().player_information()
    with ParallelInformationSetSearch(planner(selection), workers=2, backend=backend) as parallel:
        result = parallel.search(factory, root, seed=42)
        assert not result.used_fallback and result.choice in root.actions
        assert result.choice.kind == 'AbilityAction'  # Actual lethal, not a mocked evaluator.
        assert result.metrics.iterations_completed == 12
        assert result.metrics.advance_attempts <= 192
        assert result.metrics.parallel_workers == 2
        assert result.metrics.worlds_built == 2  # One isolated copy of the pool per worker.
        assert result.metrics.parallel_backend == backend
        assert result.metrics.parallel_worker_cpu_ms > 0 and result.metrics.parallel_payload_bytes > 0
        assert len(set(result.metrics.parallel_worker_pids if backend == 'process'
                       else result.metrics.parallel_worker_threads)) == 2
        pool = parallel._executor
        repeated = parallel.search(factory, root, seed=42)
        assert parallel._executor is pool
        assert replace(repeated, metrics=result.metrics) == result
    assert parallel._executor is None


def test_merge_is_visit_weighted_and_can_achieve_coverage_jointly():
    info = factory(0).start().player_information()
    info = replace(info, actions=info.actions[:2])
    a, b = info.actions
    base = planner()
    base.require_root_coverage = True
    metrics = InformationSearchMetrics(1, 2, 2, 4, 0, 2, 0, (), 1, 1.0)
    one = InformationSearchResult(a, None, True, False, 'iteration_budget',
        (ActionEstimate(a, 1, 1.0, .6), ActionEstimate(b, 0, None, .4)), metrics, 'root_coverage')
    two = replace(one, estimates=(ActionEstimate(a, 3, -1.0, .6), ActionEstimate(b, 2, .5, .4)))
    result = merge_results(base, info, a, (one, two), elapsed_ms=5, cpu_ms=8,
                           workers=2, requested=2, backend='process')
    row = next(r for r in result.estimates if r.action == a)
    assert row.visits == 4 and row.mean_value == -.5
    assert result.choice == a and not result.used_fallback and result.root_coverage_complete
    interrupted = replace(two, fallback_reason='extension_limit')
    assert merge_results(base, info, a, (one, interrupted), elapsed_ms=5, cpu_ms=8,
        workers=2, requested=2, backend='process').used_fallback


@pytest.mark.parametrize('backend', ['thread', 'process'])
def test_worker_rule_error_propagates_and_pool_is_cleaned(backend):
    root = factory(0).start().player_information()
    def broken(seed):
        raise RuntimeError('intentional worker rule failure')
    with ParallelInformationSetSearch(planner(), workers=2, backend=backend) as parallel:
        with pytest.raises(RuntimeError, match='intentional worker rule failure'):
            parallel.search(broken, root)
        assert parallel._executor is None and not parallel._rows
        assert not parallel.search(factory, root).used_fallback


@pytest.mark.parametrize('settings', [
    {'parallel_workers': 0}, {'parallel_workers': True}, {'parallel_workers': 62},
    {'parallel_backend': 'typo'}, {'parallel_workers': 2, 'world_backend': 'history'},
])
def test_invalid_parallel_configuration_is_rejected(settings):
    with pytest.raises(ValueError, match='parallel|Parallel'):
        SearchAgentConfig(deck_knowledge='both_lists', **settings)


def test_snapshot_transport_strips_identity_recipes_and_provider_captures():
    from test_current_position_worlds import position, prepare
    from game.ai.current_position_worlds import CurrentPositionWorlds
    from game.simulation.search_config import search_generation_policy
    origin, game = position()
    request, info, snapshot = prepare(origin, game)
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    build = provider._factory(snapshot)
    assert 'self' not in getclosurevars(build).nonlocals
    assert not {'immutable_values', 'dictionary_plans'} & snapshot.__getstate__().keys()
    restored = cloudpickle.loads(cloudpickle.dumps(build))
    assert restored(42).start().player_information() == info
    assert all(player.controller is None for player in snapshot.state.players)


def test_spawn_workers_run_a_sanitized_current_position_snapshot():
    from test_current_position_worlds import position, prepare
    origin, game = position()
    worlds, info, snapshot = prepare(origin, game)
    before = game.state.rng.getstate()
    with ParallelInformationSetSearch(planner(), workers=2) as parallel:
        result = parallel.search(worlds, info, seed=42)
        assert not result.used_fallback and result.choice in info.actions
    assert game.state.rng.getstate() == before


def test_reuse_is_per_worker_and_invalidated_by_model_or_session_change():
    root = factory(0).start().player_information()
    context = object()
    with ParallelInformationSetSearch(planner(tree_reuse_size=32), workers=2) as parallel:
        first = parallel.search(factory, root, seed=42, reuse_context=context)
        reused = parallel.search(factory, root, seed=42, reuse_context=context)
        assert not reused.used_fallback and reused.metrics.reused_root_visits == 12
        assert sum(r.visits for r in reused.estimates) == 24
        assert reused.metrics.iterations_completed == 12
        parallel.invalidate_model('new-weights')
        assert not parallel.search(factory, root, reuse_context=context).metrics.reused_root_visits
        assert not parallel.search(factory, root, reuse_context=object()).metrics.reused_root_visits


def test_uneven_budgets_and_effective_workers_do_not_multiply_work():
    root = factory(0).start().player_information()
    base = planner()
    base.budget = replace(base.budget, iterations=5, max_advances=7)
    with ParallelInformationSetSearch(base, workers=3, backend='thread') as parallel:
        result = parallel.search(factory, root)
        assert result.metrics.iterations_started <= 5 and result.metrics.advance_attempts <= 7
        assert result.metrics.parallel_workers == 3
        parallel.budget = replace(parallel.budget, iterations=1)
        assert base.budget.iterations == 1
        one = parallel.search(factory, root)
        assert one.metrics.parallel_workers == 1 and one.metrics.parallel_requested_workers == 3
        assert one.metrics.advance_attempts <= 7 and one.metrics.iterations_started <= 1


def test_expired_common_deadline_keeps_one_attempt_per_worker_without_disabling_timer():
    root = factory(0).start().player_information()
    with ParallelInformationSetSearch(planner(max_time_ms=.01, min_trajectories=1), workers=2) as parallel:
        result = parallel.search(factory, root)
        assert result.metrics.iterations_started == 2
        assert result.metrics.minimum_trajectories == 2 and result.metrics.time_limit_ms == .01
        assert result.stop_reason == 'time_budget'


def test_play_cli_overrides_workers_and_backend(monkeypatch):
    import sys
    from game.bin import play
    from game.simulation.match_runner import MatchResult
    captured = {}
    def run(colors, output, **kwargs):
        captured.update(kwargs)
        return MatchResult(colors, 42, 0, 'aborted', None, None, 0, 0, None)
    monkeypatch.setattr(play, 'run_match', run)
    monkeypatch.setattr(sys, 'argv', ['play', '--ai-agent', 'mcts', '--mcts-known-decks',
                                    '--mcts-workers', '3', '--mcts-parallel-backend', 'thread'])
    assert play.main() == 0
    agent = captured['controllers'][1]
    assert isinstance(agent.search, ParallelInformationSetSearch)
    assert agent.search.workers == 3 and agent.search.backend == 'thread'
    assert captured['metadata']['players'][1]['controller']['mcts']['parallel_workers'] == 3
    agent.close_search()


@pytest.mark.parametrize('staged', [False, True])
def test_spawn_supports_native_construction_and_incremental_candidate_menus(staged):
    from game.simulation.search_config import SearchGenerationPolicy
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    policy = SearchGenerationPolicy(staged, staged, True, 2)
    def scenario(controllers):
        game = tactical_scenario('useful_pacifism')(controllers)
        owner = game.state.players[1]
        for _ in range(8):
            owner.add_card(Card(starter_catalog()['Impassioned Orator'], owner), ZoneType.BATTLEFIELD)
        return game
    def worlds(seed):
        return SearchSimulator(scenario, policy_for=policy, track_information=True, structured_records=True)
    sim = worlds(0)
    root = sim.start()
    base = planner(widening_constant=2)
    base.budget = replace(base.budget, max_depth=1, max_advances=512)
    with ParallelInformationSetSearch(base, workers=2) as parallel:
        result = parallel.search(worlds, root.player_information(), seed=42)
        assert not result.used_fallback
        assert result.metrics.candidate_expansions > 0
        complete = sim.expand_candidates(root, 64)
        assert result.choice in complete.player_information().actions
        child = sim.advance_action(complete, result.choice)
        assert child.history  # Executed by a fresh local binding, not a worker's frame.


def test_spawn_root_matching_does_not_require_a_fixed_python_hash_seed(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = tmp_path/'unseeded_probe.py'
    script.write_text('''from benchmarks.mcts_parallel import capture
from game.ai.parallel_search import ParallelInformationSetSearch
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
def main():
    worlds, info = capture('blue', 'black', 43, 13)
    policy = ObservationOpponentPolicy()
    base = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
        compress_forced=True, require_root_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=2,
            max_advances=512, max_extensions=32))
    with ParallelInformationSetSearch(base, workers=2) as parallel:
        result = parallel.search(worlds, info, seed=42)
        assert not result.used_fallback
if __name__ == '__main__':
    main()
''', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    env.pop('PYTHONHASHSEED', None)
    completed = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert not completed.returncode, completed.stdout+completed.stderr


def test_live_match_closes_its_pool_and_preserves_diagnostics():
    from game.ai.game_origin import GameOrigin
    from game.ai.simple_agent import SimpleAgent
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.search_config import make_search_controller
    from game.simulation.match_runner import run_match
    origin = GameOrigin((load_arena_starter('red'), load_arena_starter('white')), game_catalog,
                        names=('Search','Simple'), starting_player_idx=0)
    own = make_search_controller(dict(deck_knowledge='both_lists', parallel_workers=2,
        worlds=1, iterations=4, max_depth=1, max_advances=128), origin, max_decisions=1000)
    result = run_match(tuple(deck.name for deck in origin.decks), None, seed=42, starting_player=0,
        max_turns=1, controllers=(own, SimpleAgent()), names=origin.names, decklists=origin.decks)
    assert result.status == 'turn_limit' and not result.error
    assert own.search._executor is None
    assert any(row['parallel_searches'] for row in own.search_summary()['by_request'].values())


def test_long_recall_transport_is_iterative_and_preserves_keys_without_json(monkeypatch):
    from game.ai.search_recall import PlayerRecall
    from game.ai.structured_values import RecallDigest
    import game.ai.structured_values as values
    recall = PlayerRecall()
    for turn in range(2200):
        recall = recall.append('event', {'turn': turn})
    assert 'text' not in recall.digest.__dict__
    with monkeypatch.context() as patch:
        patch.setattr(values.json, 'dumps', lambda *a, **kw: pytest.fail('Transport must not export JSON'))
        restored = cloudpickle.loads(cloudpickle.dumps(recall, protocol=5))
    assert type(restored.digest) is RecallDigest
    assert restored.entries == recall.entries and restored.digest.fast == recall.digest.fast
    assert 'text' not in restored.digest.__dict__
    cursor, count = restored.digest, 0
    while isinstance(cursor, RecallDigest):
        count += 1
        cursor = cursor.previous
    assert count == 2200
    assert restored.digest.text == recall.digest.text
    left, right = restored.append('event', {'turn': 2200}), recall.append('event', {'turn': 2200})
    assert left.digest.fast == right.digest.fast and left.digest.text == right.digest.text
    cached = cloudpickle.loads(cloudpickle.dumps(restored))
    assert cached.digest.__dict__['text'] == restored.digest.text
