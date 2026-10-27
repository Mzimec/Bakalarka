"""Search semantics independently of expensive replay, plus real-engine tactics."""
import json
from dataclasses import replace

import pytest

from game.ai.perfect_information_search import PerfectInformationSearch, SearchBudget
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import DecisionAddress, RecordedChoice, SimulationNode, ReplayDiverged, SimulationBudgetExceeded


class Tree:
    """Explicit finite adversarial trees; scores are always from player zero."""
    def __init__(self, specs):
        self.specs = specs
        self.calls = []

    def node(self, key, history=()):
        spec = self.specs[key]
        children = spec.get("children", {})
        terminal = "winner" in spec
        choices = tuple(ChoiceDescriptor(label, (label,)) for label in children)
        decision = None if terminal or spec.get("cutoff") else DecisionAddress(
            spec.get("kind", "PriorityDecisionRequest"), spec.get("player", 0), 1, "PRECOMBAT_MAIN", key, key)
        views = []
        for player in range(2):
            players = [{"id": i, "life": 20, "hand_size": 0, "has_lost": terminal and spec["winner"] != i} for i in range(2)]
            view = {"self": player, "players": players, "battlefield": [],
                    "pending_abilities": [{}] if spec.get("pending") else [],
                    "test_score": spec.get("score", 0) * (1 if player == 0 else -1)}
            if "combat" in spec:
                view["combat"] = spec["combat"]
            views.append((player, json.dumps(view)))
        return SimulationNode(history, decision, choices, terminal, spec.get("cutoff", False), 0, tuple(views), self)

    def advance(self, node, choice):
        self.calls.append((node.decision.menu_digest, choice.kind))
        key = self.specs[node.decision.menu_digest]["children"][choice.kind]
        return self.node(key, (*node.history, RecordedChoice(node.decision, choice)))


class Score:
    def evaluate(self, view):
        return view["test_score"]


def search(tree, *, depth=2, advances=100, extensions=8):
    return PerfectInformationSearch(budget=SearchBudget(depth, advances, extensions), evaluator=Score()).search(tree, tree.node("root"))


def test_same_player_keeps_maximizing_but_opponent_chooses_worst_reply():
    tree = Tree({
        "root": {"children": {"retained_priority": "own", "hope_opponent_blunders": "enemy"}},
        "own": {"player": 0, "children": {"bad": "low", "good": "mid"}},
        "enemy": {"player": 1, "children": {"blunder": "high", "refute": "low"}},
        "low": {"score": -0.5, "children": {"wait": "low"}},
        "mid": {"score": 0.4, "children": {"wait": "mid"}},
        "high": {"score": 0.9, "children": {"wait": "high"}},
    })
    result = search(tree)
    assert result.choice.kind == "retained_priority" and result.value == 0.4
    assert [r.choice.kind for r in result.variation] == ["retained_priority", "good"]
    assert [r.value for r in result.root_evaluations] == [0.4, -0.5]
    assert result.completed_depth == 2 and not result.used_fallback
    assert len(tree.calls) == len(set(tree.calls))  # Prefixes reused across depths.
    assert result.metrics.cache_hits >= 2


def test_declared_combat_is_not_evaluated_before_regular_damage():
    tree = Tree({
        "root": {"children": {"no_block": "combat", "block": "safe"}},
        "combat": {"score": .9, "player": 1,
                   "combat": {"active": True, "attackers": ["attacker"], "regular_damage_done": False},
                   "children": {"pass": "loss"}},
        "loss": {"winner": 1}, "safe": {"score": .1, "children": {"wait": "safe"}},
    })
    result = search(tree, depth=1)
    assert result.choice.kind == "block"
    assert [r.value for r in result.root_evaluations] == [-1, .1]


def test_partial_deeper_iteration_cannot_replace_completed_root_comparison():
    tree = Tree({
        "root": {"children": {"A": "a", "B": "b"}},
        "a": {"score": 0.2, "children": {"next": "great"}},
        "b": {"score": 0.6, "children": {"next": "bad"}},
        "great": {"score": 0.9, "children": {"wait": "great"}},
        "bad": {"score": -0.9, "children": {"wait": "bad"}},
    })
    result = search(tree, advances=3)
    assert result.completed_depth == 1 and result.interrupted_depth == 2
    assert result.stop_reason == "advance_budget" and result.metrics.advance_attempts == 3
    assert result.choice.kind == "B" and result.value == 0.6
    assert [r.value for r in result.root_evaluations] == [0.2, 0.6]


def test_incomplete_first_iteration_returns_unscored_legal_fallback():
    tree = Tree({"root": {"children": {"cast": "leaf", "PassPriorityAction": "leaf"}},
                 "leaf": {"score": 0.9, "children": {"wait": "leaf"}}})
    result = search(tree, advances=1)
    assert result.used_fallback and result.choice.kind == "PassPriorityAction"
    assert result.value is None and result.completed_depth == 0
    assert result.root_evaluations == result.variation == ()


def test_pending_resolution_extends_horizon_and_searches_opponents_response():
    tree = Tree({
        "root": {"children": {"cast": "stack", "pass": "safe"}},
        "stack": {"player": 1, "pending": True, "score": -0.9,
                  "children": {"allow": "win", "counter": "bad"}},
        "safe": {"score": 0.2, "children": {"wait": "safe"}},
        "win": {"winner": 0}, "bad": {"score": -0.4, "children": {"wait": "bad"}},
    })
    result = search(tree, depth=1)
    assert result.choice.kind == "pass" and result.root_evaluations[0].value == -0.4
    limited = search(tree, depth=1, extensions=0)
    assert limited.used_fallback and limited.stop_reason == "extension_limit"


def test_alpha_beta_prunes_without_changing_exact_root_values():
    tree = Tree({
        "root": {"children": {"start": "max"}},
        "max": {"children": {"first": "first", "second": "second"}},
        "first": {"player": 1, "children": {"a": "five", "b": "six"}},
        "second": {"player": 1, "children": {"a": "one", "b": "nine"}},
        **{key: {"score": score, "children": {"wait": key}} for key, score in
           (("five", .5), ("six", .6), ("one", .1), ("nine", .9))},
    })
    result = search(tree, depth=3)
    assert result.value == .5 and result.metrics.pruned_choices == 1
    assert ("second", "b") not in tree.calls


@pytest.mark.parametrize("winner,expected", [(0, 1), (1, -1), (None, 0)])
def test_terminal_results_override_custom_evaluator(winner, expected):
    tree = Tree({"end": {"winner": winner, "score": .8}})
    result = PerfectInformationSearch(evaluator=Score()).search(tree, tree.node("end"), player=0)
    assert result.value == expected and result.choice is None and result.stop_reason == "terminal"


def test_terminal_win_dominates_even_a_saturated_heuristic():
    tree = Tree({"root": {"children": {"heuristic": "leaf", "win": "win"}},
                 "leaf": {"score": 1, "children": {"wait": "leaf"}}, "win": {"winner": 0}})
    result = search(tree, depth=1)
    assert result.choice.kind == "win" and result.value == 1
    assert result.root_evaluations[0].value < 1


def test_simulator_cutoff_and_budget_are_not_terminal_losses_or_draws():
    tree = Tree({"root": {"children": {"go": "end"}}, "end": {"cutoff": True}})
    result = search(tree)
    assert result.used_fallback and result.value is None and result.stop_reason == "simulator_cutoff"
    def budget(*args):
        raise SimulationBudgetExceeded("too many options")
    tree.advance = budget
    assert search(tree).stop_reason == "simulator_budget"
    def broken(*args):
        raise ReplayDiverged("changed world")
    tree.advance = broken
    with pytest.raises(ReplayDiverged):
        search(tree)


@pytest.mark.parametrize("kwargs", [{"max_depth": 0}, {"max_advances": True}, {"max_extensions": -1}])
def test_invalid_budgets(kwargs):
    with pytest.raises(ValueError):
        SearchBudget(**kwargs)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), 1.1])
def test_invalid_leaf_values_fail_explicitly(score):
    tree = Tree({"root": {"children": {"go": "leaf"}}, "leaf": {"score": score, "children": {"wait": "leaf"}}})
    with pytest.raises(ValueError, match="finite"):
        search(tree)


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "useful_pacifism", "redundant_pacifism", "attack_pacified"])
def test_actual_engine_tactics_and_replayed_principal_variation(case):
    from benchmarks.search_tactics import run_case
    result = run_case(case)
    assert result["passed"], result["search"]
    assert result["search"]["completed_depth"] == result["budget"]["max_depth"]
    assert result["search"]["metrics"]["advance_attempts"] <= result["budget"]["max_advances"]


def test_repeated_search_is_deterministic_and_keeps_root_unchanged():
    from benchmarks.search_tactics import tactical_scenario, tactical_policy
    from game.ai.search_simulator import SearchSimulator
    simulator = SearchSimulator(tactical_scenario("lethal_shock"), policy_for=tactical_policy)
    root = simulator.start()
    before = root.observation(0)
    engine = PerfectInformationSearch(budget=SearchBudget(max_depth=1))
    first, second = engine.search(simulator, root), engine.search(simulator, root)
    assert replace(first, metrics=second.metrics) == second
    assert replace(first.metrics, elapsed_ms=0) == replace(second.metrics, elapsed_ms=0)
    assert root.observation(0) == before and simulator.fork(root) == root
