from dataclasses import replace
from random import Random

import pytest

from game.ai.action_hierarchy import ActionHierarchy, ActionPath
from game.ai.search_actions import PlayerAction, canonical_json
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from test_information_set_search import Tree, Score, first, choice


def ability(source, target, mana=0):
    return PlayerAction("AbilityAction", canonical_json({"source": {"card": source},
        "ability_key": "activate", "effect": {"targets": {"player": target}},
        "cost": {"mana": mana}}))


def test_prefixes_share_source_and_target_but_preserve_payment_alternatives():
    a, b, c = ability("a", 0), ability("a", 1), ability("a", 1, 2)
    assert ActionPath.from_action(a).parts[:3] == ActionPath.from_action(b).parts[:3]
    assert ActionPath.from_action(a).parts[3] != ActionPath.from_action(b).parts[3]
    assert ActionPath.from_action(b).parts[:4] == ActionPath.from_action(c).parts[:4]
    h = ActionHierarchy((a, b, c), {a: .8, b: .2, c: 0})
    selected = []
    for _ in range(3):
        action, new = h.select(1.4, Random(42))
        assert new
        selected.append(action)
        h.record(action, 1 if action == c else 0)
    assert set(selected) == {a, b, c} and h.root.remaining == 0
    assert h.root.visits == 3 and h.root.total == 1
    assert h.node_count < sum(len(ActionPath.from_action(a).parts) + 1 for a in (a, b, c))
    assert h.select(0, Random(42))[0] == c


def test_hierarchy_does_not_update_on_interrupted_or_merely_selected_paths():
    a, b = ability("a", 0), ability("a", 1)
    h = ActionHierarchy((a, b), {a: 1, b: 0})
    assert h.select(1, Random(1)) == (a, True)
    assert h.select(1, Random(1)) == (a, True)
    assert h.root.visits == 0 and h.root.remaining == 2


def test_interrupted_search_does_not_publish_prefix_visits_as_completed_action_coverage():
    factory = lambda seed: Tree({"root": {"children": {"a": "pending", "b": "pending"}},
        "pending": {"pending": True, "children": {"wait": "pending"}}})
    result = InformationSetSearch(opponent_policy=first, selection="hierarchical_puct",
        budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=1, max_extensions=0)).search(
            factory, factory(0).start().player_information())
    assert result.used_fallback and not result.root_coverage_complete
    assert result.metrics.interrupted_trajectories == 8
    assert all(e.visits == 0 for e in result.estimates)


def test_hierarchical_search_backpropagates_complete_trajectories_and_overrules_bad_prior():
    factory = lambda seed: Tree({"root": {"children": {"bad": "bad", "win": "win"}},
        "bad": {"winner": 1}, "win": {"winner": 0}})
    planner = InformationSetSearch(opponent_policy=first, evaluator=Score(),
        selection="hierarchical_puct", action_prior=lambda info: {choice("bad"): 1, choice("win"): 0},
        budget=InformationSearchBudget(worlds=2, iterations=24, max_depth=1))
    a = planner.search(factory, factory(0).start().player_information(), seed=9)
    b = planner.search(factory, factory(0).start().player_information(), seed=9)
    assert a.choice.kind == "win" and a.value == 1 and not a.used_fallback
    assert a.metrics.hierarchy_nodes > 0 and a.metrics.hierarchy_selection_steps >= 24
    assert a.metrics.prior_evaluations == 1 and a.metrics.iterations_completed == 24
    assert replace(a, metrics=b.metrics) == b


@pytest.mark.parametrize("name,selection", [("uct", "uct"), ("flat_puct", "puct"),
                                           ("hierarchical_puct", "hierarchical_puct")])
def test_play_presets_share_budgets_and_wire_the_requested_algorithm(name, selection):
    import json
    from pathlib import Path
    from game.simulation.search_config import SearchAgentConfig, make_search_controller
    from game.ai.game_origin import GameOrigin
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    settings = json.loads((Path(__file__).resolve().parents[1] / "configs" / f"mcts_{name}.json").read_text())
    config = SearchAgentConfig.parse({"deck_knowledge": "both_lists", **settings})
    assert (config.iterations, config.max_advances, config.max_depth, config.worlds) == (64, 1024, 4, 2)
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")), game_catalog)
    controller = make_search_controller({"deck_knowledge": "both_lists", **settings}, origin, max_decisions=100)
    assert controller.search.selection == selection


@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
def test_real_hidden_world_search_variants_handle_a_known_counterspell(selection):
    from benchmarks.information_set_search import world_factory
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    factory = world_factory("known_answer")
    info = factory(0).start().player_information()
    result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
        action_prior=None if selection == "uct" else HeuristicActionPrior(),
        budget=InformationSearchBudget(worlds=1, iterations=12, max_depth=1,
                                      max_extensions=8, max_advances=160)).search(factory, info, seed=42)
    assert not result.used_fallback and result.root_coverage_complete
    assert result.metrics.terminal_leaves == 0
    assert all(e.mean_value is not None and e.mean_value < 1 for e in result.estimates)
