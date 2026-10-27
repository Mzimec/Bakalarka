"""Population updates use observed history, never MCTS branch outcomes."""
from dataclasses import asdict

import pytest

from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.search_simulator import SimulationBudgetExceeded
from test_history_world_provider import SignalWorld, prior, weights, action, target_information


def origin():
    return SignalWorld(True).start().player_information()


def test_update_conditions_population_without_resampling_full_origin():
    builds = []
    def counted(seed):
        builds.append(seed)
        return prior(seed)
    provider = IncrementalWorldProvider(counted, size=2000, seed=42, opponent_weights=weights)
    initial = provider(origin())(0)
    initial.start()
    assert len(builds) == 2000
    updated = provider(target_information())(0)
    assert updated.start().player_information() == target_information()
    assert len(builds) == 2000
    assert .75 < sum(p.node.sim.secret for p in provider._population) / 2000 < .85
    metrics = updated.initialization_metrics
    assert metrics.sampling_attempts == 0 and metrics.population_updates == 1
    assert 850 < metrics.population_survivors < 1150
    assert metrics.population_distinct_origins == metrics.population_survivors
    assert metrics.population_size == 2000 and metrics.population_refreshes == 0
    assert dict(metrics.mismatch_reasons) == {"observation/view/signal": 2000 - metrics.population_survivors}


def test_equal_observation_reuses_population_and_charges_shared_work_once():
    provider = IncrementalWorldProvider(prior, size=8)
    factory = provider(origin())
    a, b = factory(1), factory(2)
    a.start()
    b.start()
    assert a.initialization_metrics.sampling_attempts == 8
    assert not any(asdict(b.initialization_metrics).values())
    old = provider._population
    same = provider(origin())(1)
    same.start()
    assert provider._population is old
    assert same.initialization_metrics.sampling_attempts == 0
    assert same.initialization_metrics.population_updates == 0
    assert same.initialization_metrics.population_distinct_origins == 8


def test_reset_reproduces_population_and_invalidates_unprepared_requests():
    provider = IncrementalWorldProvider(prior, size=16, seed=3, opponent_weights=weights)
    a = provider(target_information())(5)
    a.start()
    first = tuple(p.node.sim.secret for p in provider._population)
    pending = provider(target_information())
    provider.reset()
    with pytest.raises(ValueError, match="superseded or reset"):
        pending(5).start()
    b = provider(target_information())(5)
    b.start()
    assert tuple(p.node.sim.secret for p in provider._population) == first
    assert b.initialization_metrics == a.initialization_metrics


def test_zero_survivors_refreshes_from_complete_history_not_impossible_old_pool():
    # A finite sample can miss a possible hidden world even under a valid prior.
    def deterministic_signal(info):
        return (1, 0) if info.current.observation()["private_signal_probability"] > .5 else (0, 1)
    for seed in range(16):
        provider = IncrementalWorldProvider(prior, size=1, seed=seed, opponent_weights=deterministic_signal)
        provider(origin())(1).start()
        if not provider._population[0].node.sim.secret:
            break
    else:
        pytest.fail("Fixture needs an initial sample missing the signal-producing world")
    updated = provider(target_information())(1)
    updated.start()
    assert updated.initialization_metrics.population_survivors == 0
    assert updated.initialization_metrics.population_refreshes == 1
    assert updated.initialization_metrics.sampling_attempts >= 1
    assert all(p.node.sim.secret for p in provider._population)


def test_failed_refresh_retains_old_population_and_reports_sampling_fallback():
    provider = IncrementalWorldProvider(prior, size=4, max_attempts=3, opponent_weights=lambda i: (0, 1))
    provider(origin())(1).start()
    old = provider._population
    info = target_information()
    result = InformationSetSearch(opponent_policy=lambda i: i.actions[0],
        budget=InformationSearchBudget(worlds=2, iterations=8)).search(provider(info), info)
    assert result.used_fallback and result.stop_reason == "history_sampling_budget"
    assert result.metrics.iterations_started == 0
    assert result.metrics.population_survivors == 0
    assert result.metrics.population_size == 0
    assert result.metrics.history_rejected_samples == 3
    assert provider._population is old and provider._information == origin()


def test_work_cap_preserves_last_complete_population_and_is_not_evidence_rejection():
    provider = IncrementalWorldProvider(prior, size=4, max_prefix_advances=1)
    provider(origin())(1).start()
    previous = provider._population
    sample = provider(target_information())(1)
    with pytest.raises(SimulationBudgetExceeded):
        sample.start()
    assert provider._population is previous
    assert provider._information == origin()
    assert sample.initialization_metrics.population_refreshes == 0
    assert sample.initialization_metrics.rejected_samples == 0


def test_search_branches_cannot_change_belief_population():
    provider = IncrementalWorldProvider(prior, size=8, opponent_weights=weights)
    sample = provider(target_information())(10)
    node = sample.start()
    before = tuple((p.node.player_information(), p.node.sim.secret) for p in provider._population)
    assert sample.advance_action(node, action("finish")).terminal
    assert sample.advance_action(node, action("other")).terminal
    assert before == tuple((p.node.player_information(), p.node.sim.secret) for p in provider._population)
    assert sample.start() is node


def test_mcts_metrics_count_one_population_preparation_for_entire_world_pool():
    provider = IncrementalWorldProvider(prior, size=8, opponent_weights=weights)
    info = target_information()
    result = InformationSetSearch(opponent_policy=lambda i: i.actions[0],
        budget=InformationSearchBudget(worlds=4, iterations=8)).search(provider(info), info)
    assert not result.used_fallback
    assert result.metrics.population_size == 8
    assert result.metrics.population_distinct_origins == 8
    assert result.metrics.population_refreshes == 1
    assert result.metrics.history_sampling_attempts >= 8


def test_real_live_controller_resets_provider_when_reused_between_games():
    from benchmarks.search_tactics import tactical_scenario, tactical_policy
    from game.ai.mcts_agent import MCTSAgent
    from game.ai.decision_maker import PriorityDecisionRequest
    resets = []
    class Provider:
        def reset(self):
            resets.append(1)
        def __call__(self, info):
            return None
    agent = MCTSAgent(world_provider=Provider(), policy_for=tactical_policy)
    for _ in range(2):
        game = tactical_scenario("lethal_shock")((agent, MCTSAgent(world_provider=lambda i: None)))
        agent.decide(PriorityDecisionRequest(game.state, game.state.players[0]))
    assert len(resets) == 2  # Construction and one game transition.


def test_live_replanning_updates_existing_population_without_fresh_samples():
    from benchmarks.live_search_agent import run
    result = run(provider_kind="incremental", population_size=4)
    assert result["passed"] and result["searched_decisions"] == 2
    searches = [r["search"] for r in result["trace"] if r["reason"] == "searched"]
    first, second = (s["metrics"] for s in searches)
    assert first["history_sampling_attempts"] == 4
    assert second["history_sampling_attempts"] == 0
    assert second["population_updates"] == 1
    assert second["population_survivors"] == 4
    assert second["population_distinct_origins"] == 4
