"""Reconstruct only histories justified by the explicit scenario and policy."""
from dataclasses import replace

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.information_set_search import SampledRootMismatch
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.scenario_world_provider import ScenarioWorldProvider
from game.ai.search_recall import PlayerRecall
from game.ai.search_simulator import SearchSimulator, SimulationBudgetExceeded


def factory(seed):
    return SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy, track_information=True)


def test_provider_reconstructs_own_choices_and_opponent_responses_to_later_root():
    policy = ObservationOpponentPolicy()
    sim = factory(0)
    root = sim.start()
    cast = sim.advance_action(root, policy(root.player_information()))
    reply = sim.advance_action(cast, next(a for a in cast.player_information().actions if a.kind == "PassPriorityAction"))
    counter = sim.advance_action(reply, policy(reply.player_information()))
    own = sim.advance_action(counter, next(a for a in counter.player_information().actions if a.kind == "PassPriorityAction"))
    assert own.decision.player == 0
    assert len(own.observation(0)["pending_abilities"]) == 2
    info = own.player_information()
    provider = ScenarioWorldProvider(factory, opponent_policy=policy)
    resumed = provider(info)(42)
    reconstructed = resumed.start()
    assert reconstructed == own
    action = info.actions[0]
    assert resumed.advance_action(reconstructed, action) == sim.advance_action(own, action)


def test_wrong_opponent_history_is_rejected_instead_of_discarding_evidence():
    policy = ObservationOpponentPolicy()
    sim = factory(0)
    root = sim.start()
    cast = sim.advance_action(root, policy(root.player_information()))
    reply = sim.advance_action(cast, next(a for a in cast.player_information().actions if a.kind == "PassPriorityAction"))
    counter = sim.advance_action(reply, policy(reply.player_information()))
    own = sim.advance_action(counter, next(a for a in counter.player_information().actions if a.kind == "PassPriorityAction"))
    provider = ScenarioWorldProvider(factory, opponent_policy=lambda info: next(a for a in info.actions if a.kind == "PassPriorityAction"))
    with pytest.raises(SampledRootMismatch):
        provider(own.player_information())(42).start()


def test_prefix_budget_is_explicit_and_never_a_game_outcome():
    sim, policy = factory(0), ObservationOpponentPolicy()
    node = sim.start()
    for _ in range(2):
        node = sim.advance_action(node, policy(node.player_information()))
    provider = ScenarioWorldProvider(factory, opponent_policy=policy, max_prefix_advances=1)
    with pytest.raises(SimulationBudgetExceeded, match="prefix advance"):
        provider(node.player_information())(42).start()


def test_changed_current_view_and_truncated_recall_are_not_accepted():
    sim = factory(0)
    root = sim.start()
    provider = ScenarioWorldProvider(factory, opponent_policy=ObservationOpponentPolicy())
    info = root.player_information()
    with pytest.raises(SampledRootMismatch):
        provider(replace(info, recall=PlayerRecall()))(0).start()
    with pytest.raises(SampledRootMismatch):
        provider(replace(info, actions=info.actions[:1]))(0).start()
