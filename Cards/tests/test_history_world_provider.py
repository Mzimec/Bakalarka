"""History acceptance must condition worlds and opponent paths jointly."""
from random import Random
from types import SimpleNamespace

import pytest

from game.ai.history_world_provider import HistoryWorldProvider, ExploratoryHistoryPolicy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.search_actions import PlayerAction, canonical_json
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_recall import PlayerInformation, PlayerRecall
from game.ai.search_simulator import HistorySamplingExhausted, SimulationBudgetExceeded, ReplayDiverged


def action(kind):
    return PlayerAction(kind, canonical_json({"kind": kind}))


class SignalWorld:
    """Hidden bit with a visible signal; exact posterior is analytically known."""
    def __init__(self, secret):
        self.secret = secret

    def start(self):
        return SignalNode(self, 0, (PlayerRecall(), PlayerRecall()))

    def advance_action(self, node, selected):
        assert selected in node.player_information().actions
        actor = node.decision.player
        recalls = list(node.recalls)
        recalls[actor] = recalls[actor].append("action", selected.describe())
        return SignalNode(self, node.phase + 1, tuple(recalls), selected.kind if node.phase == 1 else node.signal)


class SignalNode:
    def __init__(self, sim, phase, recalls, signal=None):
        self.sim, self.phase, self.signal = sim, phase, signal
        self.terminal, self.cutoff = phase == 3, False
        self.decision = None if self.terminal else SimpleNamespace(player=int(phase == 1), kind="PriorityDecisionRequest")
        if not self.terminal:
            actor = self.decision.player
            view = self.observation(actor)
            current = ActingPlayerObservation(actor, self.decision.kind, canonical_json(view))
            actions = tuple(action(k) for k in (("wait",) if phase == 0 else ("signal", "silent") if phase == 1 else ("finish", "other")))
            recall = recalls[actor].append("observation", {"request_kind": current.request_kind,
                "view": view, "actions": [a.describe() for a in actions]})
            recalls = tuple(recall if p == actor else r for p, r in enumerate(recalls))
            self.info = PlayerInformation(current, recall, actions)
        self.recalls = recalls

    def recall(self, player):
        return self.recalls[player]

    def player_information(self):
        return self.info

    def observation(self, player):
        view = {"self": player, "players": [{"id": p, "life": 20, "has_lost": self.terminal and p == 1}
                                             for p in (0, 1)], "pending_abilities": [], "battlefield": []}
        if player == 1:
            view["private_signal_probability"] = 0.8 if self.sim.secret else 0.2
        if self.phase >= 2:
            view["signal"] = self.signal
        return view


def target_information():
    sim = SignalWorld(True)
    node = sim.advance_action(sim.start(), action("wait"))
    return sim.advance_action(node, action("signal")).player_information()


def prior(seed):
    return SignalWorld(bool(Random(seed).getrandbits(1)))


def weights(info):
    assert info.current.player == 1
    probability = info.current.observation()["private_signal_probability"]
    return (probability, 1 - probability)


def test_joint_conditioning_changes_hidden_world_prior_to_expected_posterior():
    target = target_information()
    provider = HistoryWorldProvider(prior, opponent_weights=weights, max_attempts=64)
    accepted = []
    attempts = rejects = 0
    for seed in range(1000):
        sim = provider(target)(seed)
        node = sim.start()
        assert node.player_information() == target
        accepted.append(node.sim.secret)  # Privileged assertion, never policy input.
        attempts += sim.initialization_metrics.sampling_attempts
        rejects += sim.initialization_metrics.rejected_samples
    # Prior P(secret)=.5; P(signal|secret)=.8, P(signal|not secret)=.2.
    # Thus P(secret|signal)=.8, not .5 from independently forcing a signal.
    assert 0.75 < sum(accepted) / len(accepted) < 0.85
    assert 0.44 < len(accepted) / attempts < 0.56
    assert rejects == attempts - len(accepted)


def test_same_seed_has_same_world_prefix_and_initialization_work():
    provider = HistoryWorldProvider(prior, opponent_weights=weights)
    a, b = provider(target_information())(12), provider(target_information())(12)
    x, y = a.start(), b.start()
    assert x.sim.secret == y.sim.secret
    assert x.player_information() == y.player_information()
    assert a.initialization_metrics == b.initialization_metrics
    work = a.initialization_metrics
    assert a.start() is x and a.initialization_metrics == work
    assert a.advance_action(x, action("finish")).terminal


def test_impossible_history_exhausts_with_no_fake_outcome():
    sim = HistoryWorldProvider(prior, opponent_weights=lambda info: (0, 1), max_attempts=4)(target_information())(42)
    with pytest.raises(HistorySamplingExhausted):
        sim.start()
    assert sim.initialization_metrics.sampling_attempts == 4
    assert sim.initialization_metrics.rejected_samples == 4
    assert sim.initialization_metrics.prefix_advance_attempts == 8
    assert dict(sim.initialization_metrics.mismatch_reasons) == {"observation/view/signal": 4}


def test_prefix_limit_and_rule_errors_are_not_rejected_as_impossible_worlds():
    sim = HistoryWorldProvider(prior, max_attempts=8, max_prefix_advances=1)(target_information())(42)
    with pytest.raises(SimulationBudgetExceeded, match="prefix advance"):
        sim.start()
    assert sim.initialization_metrics.sampling_attempts == 1
    assert sim.initialization_metrics.rejected_samples == 0
    class Broken(SignalWorld):
        def advance_action(self, node, selected):
            raise ReplayDiverged("Broken replay")
    sim = HistoryWorldProvider(lambda seed: Broken(True))(target_information())(42)
    with pytest.raises(ReplayDiverged, match="Broken replay"):
        sim.start()
    assert sim.initialization_metrics.rejected_samples == 0


@pytest.mark.parametrize("bad", [(1,), (0, 0), (-1, 1), (float("nan"), 1), (float("inf"), 1)])
def test_invalid_probability_models_fail_instead_of_becoming_rejections(bad):
    sim = HistoryWorldProvider(prior, opponent_weights=lambda info: bad)(target_information())(1)
    with pytest.raises(ValueError, match="weights"):
        sim.start()
    assert sim.initialization_metrics.rejected_samples == 0


def test_very_large_finite_weights_are_normalized_without_overflow():
    sim = HistoryWorldProvider(prior, opponent_weights=lambda info: (1e308, 1e308))(target_information())(1)
    assert sim.start().player_information() == target_information()


def test_sampling_failure_discards_partial_search_pool_and_reports_work():
    target = target_information()
    accepted = HistoryWorldProvider(prior, opponent_weights=lambda info: (1, 0))(target)
    rejected = HistoryWorldProvider(prior, opponent_weights=lambda info: (0, 1), max_attempts=3)(target)
    calls = []
    def factory(seed):
        calls.append(seed)
        return (accepted if len(calls) == 1 else rejected)(seed)
    result = InformationSetSearch(opponent_policy=lambda info: info.actions[0],
        budget=InformationSearchBudget(worlds=2, iterations=8)).search(factory, target)
    assert result.used_fallback and result.value is None
    assert result.stop_reason == "history_sampling_budget"
    assert result.metrics.worlds_built == 1
    assert result.metrics.iterations_started == 0
    assert result.metrics.history_sampling_attempts == 4
    assert result.metrics.history_rejected_samples == 3
    assert result.metrics.history_prefix_advances == 8
    assert dict(result.metrics.history_mismatch_reasons) == {"observation/view/signal": 3}


def test_exploratory_model_keeps_every_alternative_and_only_receives_actor_input():
    sim = prior(0)
    info = sim.advance_action(sim.start(), action("wait")).player_information()
    seen = []
    def prefer(info):
        seen.append(info)
        return info.actions[0]
    model = ExploratoryHistoryPolicy(prefer, exploration=.2)
    assert model(info) == pytest.approx((.9, .1))
    assert seen == [info] and info.current.player == 1
    with pytest.raises(ValueError):
        ExploratoryHistoryPolicy(prefer, exploration=0)


def test_real_hidden_counter_reconditions_world_and_preserves_local_rebinding():
    from benchmarks.live_history_search import simulator, scenario, passing
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.scenario_world_provider import ScenarioWorldProvider
    from game.ai.information_set_search import SampledRootMismatch
    policy = ObservationOpponentPolicy()
    seed = next(n for n in range(100) if "Counterspell" in scenario(n).samples[1].hand)
    sim = simulator(seed)
    node = sim.start()
    node = sim.advance_action(node, policy(node.player_information()))
    node = sim.advance_action(node, passing(node.player_information()))
    node = sim.advance_action(node, policy(node.player_information()))
    node = sim.advance_action(node, passing(node.player_information()))
    target = node.player_information()
    assert target.current.player == 0
    assert len(target.current.observation()["pending_abilities"]) == 2
    old = ScenarioWorldProvider(simulator, opponent_policy=passing)
    with pytest.raises(SampledRootMismatch):
        old(target)(seed).start()
    conditioned = HistoryWorldProvider(simulator, max_attempts=128)(target)(42)
    rebuilt = conditioned.start()
    assert rebuilt.player_information() == target
    assert len(rebuilt.observation(1)["hand"]) == 0
    assert any(c["name"] == "Counterspell" for c in rebuilt.observation(0)["stack"])
    chosen = policy(target)
    child = conditioned.advance_action(rebuilt, chosen)
    assert len(child.observation(0)["pending_abilities"]) == 3
    assert rebuilt.player_information() == target  # Advancing cannot mutate its parent.


def test_live_controller_searches_after_actual_opponent_counter():
    from benchmarks.live_history_search import run
    result = run()
    assert result["passed"]
    assert result["response_searches"] == 1 and result["response_fallbacks"] == 0
    row = next(row for row in result["trace"] if row["player"] == 0 and row["used_search"])
    assert row["search"]["metrics"]["worlds_built"] == 2
    assert row["search"]["metrics"]["history_sampling_attempts"] >= 2
    assert row["search"]["root_coverage_complete"]
