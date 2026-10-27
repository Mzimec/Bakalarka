from types import SimpleNamespace

import pytest

from game.ai.action_priors import HeuristicActionPrior, normalize_prior
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.simulation.search_config import SearchAgentConfig, make_search_controller
from game.ai.game_origin import GameOrigin
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter
from test_information_set_search import Tree, Score, first, choice


def position(*, winning=False):
    return Tree({"root": {"children": {"left": "left", "right": "right"}},
                 "left": {"score": 0, "children": {"wait": "left"}},
                 "right": ({"winner": 0} if winning else
                           {"score": 0, "children": {"wait": "right"}})})


def run(*, prior=None, iterations=100, winning=False, selection="puct"):
    engine = InformationSetSearch(opponent_policy=first, evaluator=Score(),
        selection=selection, action_prior=prior,
        budget=InformationSearchBudget(worlds=4, iterations=iterations, max_depth=1))
    return engine.search(lambda seed: position(winning=winning), position().start().player_information(), seed=42)


def test_puct_uses_actor_only_prior_once_per_information_node_and_reports_probabilities():
    inputs = []
    def prior(info):
        inputs.append(info)
        assert "score" not in info.current.observation()
        return {choice("left"): 9, choice("right"): 1}
    result = run(prior=prior)
    assert len(inputs) == result.metrics.prior_evaluations == 1  # Shared over four worlds.
    assert result.metrics.selection == "puct" and not result.used_fallback
    assert [e.prior_probability for e in result.estimates] == pytest.approx([.9, .1])
    assert result.estimates[0].visits > 5 * result.estimates[1].visits
    again = run(prior=prior)
    assert again.estimates == result.estimates and again.choice == result.choice


def test_puct_forces_coverage_even_for_zero_prior_and_can_overrule_bad_prior():
    prior = lambda info: {choice("left"): 1, choice("right"): 0}
    one = run(prior=prior, iterations=1, winning=True)
    assert one.used_fallback and not one.root_coverage_complete
    assert [e.visits for e in one.estimates] == [1, 0]
    two = run(prior=prior, iterations=2, winning=True)
    assert two.root_coverage_complete and two.choice.kind == "right"
    result = run(prior=prior, winning=True)
    assert result.choice.kind == "right" and result.value == 1
    assert result.estimates[1].visits > result.estimates[0].visits


def test_uct_has_no_prior_cost_and_balances_equal_rewards():
    result = run(selection="uct")
    assert result.metrics.prior_evaluations == 0
    assert [e.prior_probability for e in result.estimates] == [None, None]
    assert [e.visits for e in result.estimates] == [50, 50]
    with pytest.raises(ValueError, match="only used"):
        InformationSetSearch(opponent_policy=first, action_prior=lambda info: {})


@pytest.mark.parametrize("weights", [
    {}, {choice("left"): 1}, {choice("left"): 1, choice("alien"): 1},
    {choice("left"): 0, choice("right"): 0},
    *({choice("left"): bad, choice("right"): 1} for bad in (-1, float("inf"), float("nan"), True)),
])
def test_invalid_prior_rejected(weights):
    with pytest.raises(ValueError, match="prior"):
        run(prior=lambda info: weights)


def test_normalization_and_softmax_stable_for_extreme_scores_and_uniform_floor():
    info = position().start().player_information()
    assert list(normalize_prior(info, {a: 1e308 for a in info.actions}).values()) == [.5, .5]
    policy = SimpleNamespace(rank=lambda info: [SimpleNamespace(action=a, score=s)
        for a, s in zip(info.actions, [1e308, -1e308])])
    prior = HeuristicActionPrior(policy, uniform_mix=.1)
    assert list(prior(info).values()) == pytest.approx([.95, .05])


@pytest.mark.parametrize("settings", [
    {"selection": "unknown"}, {"prior": "unknown"}, {"prior": "heuristic"},
    {"exploration": -1}, {"exploration": float("inf")}, {"prior_temperature": 0},
    {"prior_uniform_mix": 1.1}, {"prior_temperature": True},
])
def test_invalid_selection_config(settings):
    with pytest.raises(ValueError):
        SearchAgentConfig.parse({"deck_knowledge": "both_lists", **settings})


def test_production_controller_wires_selection_and_prior():
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")), game_catalog)
    controller = make_search_controller({"deck_knowledge": "both_lists", "selection": "puct",
        "prior": "heuristic", "exploration": 2, "prior_temperature": 3, "prior_uniform_mix": .2},
        origin, max_decisions=100)
    assert controller.search.selection == "puct" and controller.search.exploration == 2
    assert isinstance(controller.search.action_prior, HeuristicActionPrior)
    assert controller.search.action_prior.temperature == 3
    assert controller.search.action_prior.uniform_mix == .2
