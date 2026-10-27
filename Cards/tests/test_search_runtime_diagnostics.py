import json

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget, _Run
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator
from game.ai.runtime_clone import RuntimeClone, UnsupportedSnapshot, snapshot_failure_trace
from game.ai.search_statistics import SearchStatistics


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "attack_pacified"])
def test_cached_search_preserves_visits_values_and_budget_across_native_worlds(case):
    def factory(seed):
        return SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True)
    info = factory(0).start().player_information()
    results = []
    for size in (0, 2048):
        policy = ObservationOpponentPolicy()
        search = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
            transition_cache_size=size,
            budget=InformationSearchBudget(worlds=2, iterations=16, max_advances=128, max_depth=1))
        results.append(search.search(factory, info, seed=42))
    a, b = results
    assert (a.choice, a.estimates, a.used_fallback, a.stop_reason) == (b.choice, b.estimates, b.used_fallback, b.stop_reason)
    assert a.metrics.advance_attempts == b.metrics.advance_attempts
    assert a.metrics.iterations_completed == b.metrics.iterations_completed
    assert b.metrics.transition_cache_hits > 0
    assert b.metrics.simulator_advance_calls < a.metrics.simulator_advance_calls
    assert b.metrics.simulator_advance_calls + b.metrics.transition_cache_hits == b.metrics.advance_attempts


def test_transition_cache_is_bounded_and_never_crosses_sampled_worlds():
    policy = ObservationOpponentPolicy()
    run = _Run(InformationSetSearch(opponent_policy=policy, transition_cache_size=1), 0, 42)
    a = SearchSimulator(tactical_scenario("lethal_shock"), policy_for=tactical_policy, track_information=True)
    b = SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy, track_information=True)
    ra, rb = a.start(), b.start()
    action = next(x for x in ra.player_information().actions if x.kind == "PassPriorityAction")
    first = run.advance(a, ra, action)
    assert run.advance(a, ra, action) is first
    other = run.advance(b, rb, action)
    assert other._owner is not first._owner
    assert run.cache_hits == 1 and run.advance_calls == 2 and len(run.transitions) == 1
    assert run.advance(a, ra, action).observation(0) == first.observation(0)
    view = first.observation(0)
    view.clear()
    assert first.observation(0)  # Returned views cannot corrupt cached immutable data.


def test_snapshot_trace_identifies_structure_without_hidden_card_or_mapping_names():
    from test_current_position_worlds import position
    _, game = position()
    card = next(iter(game.state.players[1].hand.values()))
    with pytest.raises(UnsupportedSnapshot) as caught:
        RuntimeClone(allowed_cards=set()).copy({"turn_events": [{"payload": {"secret-card-name": card}}]})
    path = snapshot_failure_trace(caught.value)
    assert "turn_events" in path and "payload" in path and "Card" in path
    assert "secret-card-name" not in json.dumps(path)
    assert card.name not in json.dumps(path) and card.command_id not in json.dumps(path)


def test_summary_separates_forced_decisions_and_search_latency_and_request_causes():
    stats = SearchStatistics()
    for searched, time in ((False, 1), (False, 2), (True, 100)):
        stats.record({"reason": "advance_budget" if searched else "forced_choice", "options": 8 if searched else 1,
            "used_search": searched, "used_fallback": searched, "request_kind": "DeclareAttackersRequest",
            "stage_ms": {"snapshot_ms": 5 if searched else 0}}, time)
    report = stats.summary()
    assert report["decision_ms_median"] == 2 and report["search_decision_ms_median"] == 100
    assert report["stage_ms_total"]["snapshot_ms"] == 5
    assert report["by_request"]["DeclareAttackersRequest"]["options_max"] == 8
    assert report["by_request"]["DeclareAttackersRequest"]["reasons"]["advance_budget"] == 1
