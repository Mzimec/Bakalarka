"""Information-set statistics must not become per-world clairvoyant choices."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.search_actions import PlayerAction, canonical_json, UnsupportedPlayerAction
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_recall import PlayerInformation, PlayerRecall
from game.ai.search_simulator import SimulationBudgetExceeded, ReplayDiverged


def choice(name):
    return PlayerAction(name, canonical_json({"kind": name}))


class Node:
    def __init__(self, tree, name):
        self.tree, self.name = tree, name
        self.spec = tree.rows[name]
        self.terminal = "winner" in self.spec
        self.cutoff = self.spec.get("cutoff", False)
        self.decision = None if self.terminal or self.cutoff else SimpleNamespace(
            player=self.spec.get("player", 0), kind=self.spec.get("kind", "PriorityDecisionRequest"))

    def observation(self, player):
        result = {"self": player, "players": [
            {"id": p, "life": 20, "hand_size": 0, "mana": {},
             "has_lost": self.terminal and self.spec["winner"] != p} for p in (0, 1)],
            "battlefield": [], "pending_abilities": ["spell"] if self.spec.get("pending") else []}
        result["score"] = self.spec.get("score", 0)
        if "combat" in self.spec:
            result["combat"] = self.spec["combat"]
        return result

    def player_information(self):
        player = self.decision.player
        # The hidden world's payoff is deliberately absent from policy input.
        view = self.observation(player)
        view.pop("score")
        view["visible_position"] = self.spec.get("information", self.name)
        current = ActingPlayerObservation(player, self.decision.kind, canonical_json(view))
        actions = tuple(choice(k) for k in self.spec["children"])
        recall = PlayerRecall().append("observation", view)
        return PlayerInformation(current, recall, actions)


class Tree:
    def __init__(self, rows):
        self.rows = rows

    def start(self):
        return Node(self, "root")

    def advance_action(self, node, action):
        assert action in node.player_information().actions
        return Node(self, node.spec["children"][action.kind])


class Score:
    def evaluate(self, view):
        return view["score"]


def first(info):
    return info.actions[0]


def search(factory, *, iterations=256, worlds=16, depth=2, advances=4096,
           extensions=4, opponent=first, evaluator=None, seed=42):
    root = factory(0).start().player_information()
    return InformationSetSearch(
        opponent_policy=opponent, evaluator=evaluator or Score(),
        budget=InformationSearchBudget(worlds=worlds, iterations=iterations,
            max_advances=advances, max_depth=depth, max_extensions=extensions)
    ).search(factory, root, seed=seed)


def test_hidden_worlds_share_guess_statistics_instead_of_optimizing_each_truth():
    def factory(seed):
        left = .9 if seed % 2 else -.9
        return Tree({
            "root": {"children": {"gamble": "guess", "safe": "safe"}},
            "guess": {"children": {"left": "left", "right": "right"}},
            "safe": {"player": 1, "score": .35, "children": {"wait": "safe"}},
            "left": {"score": left, "children": {"wait": "left"}},
            "right": {"score": -left, "children": {"wait": "right"}},
        })
    result = search(factory, iterations=2048, worlds=64, advances=8192)
    assert result.choice.kind == "safe" and result.root_coverage_complete
    gamble = next(e for e in result.estimates if e.action.kind == "gamble")
    assert gamble.mean_value < .35  # Per-world perfect information would value it at +.9.
    assert result.metrics.information_nodes == 2  # root + one shared blind guess node.


def test_opponent_uses_only_its_information_and_is_not_cooperative_maximization():
    inputs = []
    def opponent(info):
        inputs.append(info)
        assert info.current.player == 1
        return next(a for a in info.actions if a.kind == "counter")
    def factory(seed):
        return Tree({
            "root": {"children": {"cast": "reply", "safe": "safe"}},
            "reply": {"player": 1, "children": {"allow": "win", "counter": "loss"}},
            "safe": {"player": 1, "score": .2, "children": {"counter": "safe"}},
            "win": {"winner": 0}, "loss": {"winner": 1},
        })
    result = search(factory, opponent=opponent)
    assert result.choice.kind == "safe" and result.value == pytest.approx(.2)
    assert next(e for e in result.estimates if e.action.kind == "cast").mean_value == -1
    assert inputs and result.metrics.information_nodes == 1


def test_opponent_policy_does_not_receive_root_private_world_payoffs():
    observed = []
    def policy(info):
        observed.append(info)
        return first(info)
    def make(secret):
        return lambda seed: Tree({"root": {"children": {"go": "reply"}},
            "reply": {"player": 1, "children": {"go": "leaf"}},
            "leaf": {"score": secret, "children": {"wait": "leaf"}}})
    search(make(.8), iterations=16, worlds=1, opponent=policy)
    first_run = tuple(observed)
    observed.clear()
    search(make(-.8), iterations=16, worlds=1, opponent=policy)
    assert tuple(observed) == first_run


def test_actual_actor_controls_consecutive_root_decisions_and_results_are_deterministic():
    factory = lambda seed: Tree({
        "root": {"children": {"plan": "again", "pass": "safe"}},
        "again": {"player": 0, "children": {"win": "win", "lose": "lose"}},
        "safe": {"player": 1, "children": {"wait": "safe"}},
        "win": {"winner": 0}, "lose": {"winner": 1},
    })
    a, b = search(factory), search(factory)
    assert a.choice.kind == "plan" and a.value > .5
    assert replace(a, metrics=b.metrics) == b
    assert replace(a.metrics, elapsed_ms=0, world_initialization_ms=0, advance_ms=0, cpu_ms=0) == replace(
        b.metrics, elapsed_ms=0, world_initialization_ms=0, advance_ms=0, cpu_ms=0)


def test_incomplete_trajectory_does_not_backpropagate_or_claim_coverage():
    factory = lambda seed: Tree({"root": {"children": {"go": "pending", "safe": "pending"}},
        "pending": {"pending": True, "children": {"wait": "pending"}}})
    result = search(factory, iterations=8, depth=1, extensions=0)
    assert result.used_fallback and result.value is None and not result.root_coverage_complete
    assert result.stop_reason == "iteration_budget"
    assert result.metrics.interruption_reasons == (("extension_limit", 8),)
    assert result.metrics.iterations_completed == 0 and result.metrics.interrupted_trajectories == 8
    assert all(e.visits == 0 and e.mean_value is None for e in result.estimates)


def test_advance_budget_and_unvisited_actions_are_explicit():
    factory = lambda seed: Tree({"root": {"children": {"a": "win", "b": "win", "c": "win"}},
                                "win": {"winner": 0}})
    result = search(factory, advances=1)
    assert result.used_fallback and result.value is None and result.stop_reason == "advance_budget"
    assert result.metrics.advance_attempts == 1 and sum(e.visits for e in result.estimates) == 1


def test_wrong_sampled_information_is_rejected_before_any_statistics():
    def factory(seed):
        return Tree({"root": {"information": str(seed), "children": {"go": "root"}}})
    with pytest.raises(ValueError, match="does not match"):
        search(factory)


@pytest.mark.parametrize("error", [ReplayDiverged("changed"), UnsupportedPlayerAction("unsupported")])
def test_engine_and_adapter_errors_are_not_converted_to_game_losses(error):
    class Broken(Tree):
        def advance_action(self, *args):
            raise error
    factory = lambda seed: Broken({"root": {"children": {"go": "root"}}})
    with pytest.raises(type(error)):
        search(factory)


def test_simulation_cap_aborts_without_a_fake_leaf_score():
    class Capped(Tree):
        def advance_action(self, *args):
            raise SimulationBudgetExceeded("cap")
    result = search(lambda seed: Capped({"root": {"children": {"go": "root"}}}))
    assert result.stop_reason == "simulator_budget" and result.value is None


def test_terminal_outcome_overrides_custom_leaf_evaluator():
    factory = lambda seed: Tree({"root": {"children": {"win": "win"}}, "win": {"winner": 0}})
    class BrokenScore:
        def evaluate(self, view):
            raise AssertionError("Terminal outcomes do not use this evaluator")
    result = search(factory, evaluator=BrokenScore(), iterations=8)
    assert result.value == 1 and result.metrics.terminal_leaves == 8


def test_terminal_world_stops_its_trajectory_without_solving_other_hidden_worlds():
    outcomes = set()
    class TerminalTree(Tree):
        def advance_action(self, node, action):
            assert not node.terminal, 'A terminal world must never advance again'
            child = super().advance_action(node, action)
            outcomes.add(child.spec['winner'])
            return child
    def factory(seed):
        return TerminalTree({'root': {'children': {'attack': 'end'}},
                             'end': {'winner': seed % 2}})
    result = search(factory, iterations=128, worlds=16)
    assert outcomes == {0, 1}
    assert result.metrics.iterations_completed == result.metrics.terminal_leaves == 128
    assert result.metrics.advance_attempts == 128
    assert -1 < result.value < 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), 2])
def test_invalid_leaf_values_fail_explicitly(score):
    factory = lambda seed: Tree({"root": {"score": score, "children": {"go": "root"}}})
    with pytest.raises(ValueError, match="finite"):
        search(factory, depth=1)


@pytest.mark.parametrize("budget", [{"worlds": 0}, {"iterations": True}, {"max_advances": -1},
                                    {"max_depth": 0}, {"max_extensions": -1}])
def test_invalid_budgets(budget):
    with pytest.raises(ValueError):
        InformationSearchBudget(**budget)


@pytest.mark.parametrize("case", ["no_answer", "known_answer", "unknown_answer"])
def test_real_engine_information_search_handles_counterspell_and_hidden_belief(case):
    from benchmarks.information_set_search import run_case
    result = run_case(case)
    assert result["passed"], result["search"]
    assert result["search"]["metrics"]["advance_attempts"] <= result["budget"]["max_advances"]


def test_unoffered_opponent_action_is_rejected():
    factory = lambda seed: Tree({"root": {"children": {"go": "reply"}},
                                "reply": {"player": 1, "children": {"wait": "reply"}}})
    with pytest.raises(ValueError, match="not offered"):
        search(factory, opponent=lambda info: choice("cheat"))


def test_root_player_one_uses_its_own_terminal_utility():
    factory = lambda seed: Tree({"root": {"player": 1, "children": {"win": "win", "lose": "lose"}},
                                "win": {"winner": 1}, "lose": {"winner": 0}})
    result = search(factory, iterations=32)
    assert result.choice.kind == "win" and result.value == 1


def test_censored_worlds_force_fallback_even_after_each_root_action_has_a_sample():
    def factory(seed):
        return Tree({"root": {"children": {"go": "end"}},
                     "end": {"cutoff": True} if seed % 2 else {"winner": 0}})
    result = search(factory, worlds=32, iterations=128)
    assert result.root_coverage_complete and result.metrics.iterations_completed > 0
    assert result.metrics.interrupted_trajectories > 0
    assert result.used_fallback and result.value is None


def rollout_tree():
    return Tree({"root": {"children": {"go": "again"}},
                 "again": {"children": {"win": "win", "lose": "lose"}},
                 "win": {"winner": 0}, "lose": {"winner": 1}})


def test_guided_rollout_receives_only_player_information_and_is_reproducible():
    seen = []
    def policy(info):
        assert type(info) is PlayerInformation and info.current.player == 0
        seen.append(info)
        return next(a for a in info.actions if a.kind == "win")
    planner = InformationSetSearch(opponent_policy=first, rollout_policy=policy, rollout_exploration=0,
        budget=InformationSearchBudget(worlds=1, iterations=1, max_depth=2))
    a = planner.search(lambda seed: rollout_tree(), rollout_tree().start().player_information(), seed=42)
    b = planner.search(lambda seed: rollout_tree(), rollout_tree().start().player_information(), seed=42)
    assert a.value == 1 and a.metrics.rollout_policy_choices == 1
    assert a.metrics.rollout_random_choices == 0 and seen[0] == seen[1]
    assert replace(a, metrics=b.metrics) == b
    assert replace(a.metrics, elapsed_ms=0, world_initialization_ms=0, advance_ms=0, cpu_ms=0) == replace(
        b.metrics, elapsed_ms=0, world_initialization_ms=0, advance_ms=0, cpu_ms=0)


def test_full_rollout_exploration_never_invokes_guidance_and_bad_action_is_rejected():
    def invalid(info):
        return choice("not_offered")
    budget = InformationSearchBudget(worlds=1, iterations=1, max_depth=2)
    root = rollout_tree().start().player_information()
    planner = InformationSetSearch(opponent_policy=first, rollout_policy=invalid, rollout_exploration=1, budget=budget)
    result = planner.search(lambda seed: rollout_tree(), root)
    assert result.metrics.rollout_random_choices == 1 and result.metrics.rollout_policy_choices == 0
    with pytest.raises(ValueError, match="Rollout policy"):
        InformationSetSearch(opponent_policy=first, rollout_policy=invalid, rollout_exploration=0,
                             budget=budget).search(lambda seed: rollout_tree(), root)


@pytest.mark.parametrize("epsilon", [-.1, 1.1, float("nan"), float("inf")])
def test_invalid_rollout_exploration(epsilon):
    with pytest.raises(ValueError, match="rollout_exploration"):
        InformationSetSearch(opponent_policy=first, rollout_exploration=epsilon)


def test_invalid_rollout_callback():
    with pytest.raises(TypeError, match="rollout_policy"):
        InformationSetSearch(opponent_policy=first, rollout_policy=3)


def test_empty_stack_during_combat_is_extended_until_actual_damage_outcome():
    factory = lambda seed: Tree({
        "root": {"children": {"no_block": "damage", "block": "safe"}},
        "damage": {"player": 1, "score": .9,
                   "combat": {"active": True, "attackers": ["attacker"], "regular_damage_done": False},
                   "children": {"pass": "loss"}},
        "loss": {"winner": 1},
        "safe": {"score": .1, "children": {"wait": "safe"}},
    })
    result = search(factory, iterations=32, worlds=1, depth=1)
    assert result.choice.kind == "block"
    assert next(e for e in result.estimates if e.action.kind == "no_block").mean_value == -1
    capped = search(factory, iterations=8, worlds=1, depth=1, extensions=0)
    assert capped.used_fallback and dict(capped.metrics.interruption_reasons)["extension_limit"] > 0
