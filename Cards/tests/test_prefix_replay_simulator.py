"""Differential checks against strict replay, including observations and memory."""
from dataclasses import replace

import pytest

from game.ai.search_simulator import SearchSimulator, starter_scenario
from game.ai.prefix_replay_simulator import PrefixReplaySimulator
from game.ai.perfect_information_search import PerfectInformationSearch, SearchBudget
from benchmarks.search_simulator import diagnostic_choice
from benchmarks.search_tactics import tactical_scenario, tactical_policy, CASES


def test_longer_prefix_preserves_every_decision_menu_view_and_memory():
    slow = SearchSimulator(starter_scenario(seed=3))
    fast = PrefixReplaySimulator(starter_scenario(seed=3))
    a, b = slow.start(), fast.start()
    for _ in range(40):
        assert a == b
        choice = diagnostic_choice(a)
        a, b = slow.advance(a, choice), fast.advance(b, choice)
    assert a == b and slow.fork(a) == fast.fork(b)
    # A sibling must still reconstruct the parent rather than reuse a mutated run.
    for choice in a.choices[:3]:
        assert slow.advance(a, choice) == fast.advance(b, choice)


@pytest.mark.parametrize("case", CASES)
def test_search_outcome_values_variation_and_work_budget_match_reference(case):
    slow = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy)
    fast = PrefixReplaySimulator(tactical_scenario(case), policy_for=tactical_policy)
    search = PerfectInformationSearch(budget=SearchBudget(max_depth=7 if case == "attack_pacified" else 1))
    a, b = search.search(slow, slow.start()), search.search(fast, fast.start())
    assert replace(a, metrics=b.metrics) == b
    assert replace(a.metrics, elapsed_ms=0) == replace(b.metrics, elapsed_ms=0)


def test_full_observations_are_built_only_at_parent_and_returned_boundary(monkeypatch):
    from game.ai.observation import PlayerObservationBuilder
    slow = SearchSimulator(starter_scenario(seed=3))
    fast = PrefixReplaySimulator(starter_scenario(seed=3))
    a, b = slow.start(), fast.start()
    for _ in range(8):
        choice = diagnostic_choice(a)
        a, b = slow.advance(a, choice), fast.advance(b, choice)
    original = PlayerObservationBuilder.build
    calls = []
    def tracked(builder, **kwargs):
        calls.append(builder.player.idx)
        return original(builder, **kwargs)
    monkeypatch.setattr(PlayerObservationBuilder, "build", tracked)
    assert slow.fork(a) == a
    assert len(calls) == 2 * (len(a.history) + 1)
    calls.clear()
    assert fast.fork(b) == b
    assert len(calls) == 4
