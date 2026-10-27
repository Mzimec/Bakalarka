"""Following a prefix once must equal strict replay at each decision boundary."""
import pytest

from benchmarks.search_quality import position
from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_actions import PlayerAction
from game.ai.search_simulator import SearchSimulator, SimulationBudgetExceeded


@pytest.mark.parametrize("recipe", [tactical_scenario("lethal_shock"), position("double_block"), position("remove_before_attack")])
def test_single_runtime_follow_matches_stepwise_trace_and_forks(recipe):
    builds = []
    def factory(controllers):
        builds.append(1)
        return recipe(controllers)
    sim = SearchSimulator(factory, policy_for=tactical_policy, track_information=True)
    policy = ObservationOpponentPolicy()
    node = sim.start()
    trace = []
    for _ in range(10):
        if node.terminal or node.cutoff:
            break
        selected = policy(node.player_information())
        trace.append((node, selected))
        node = sim.advance_action(node, selected)
    assert len(builds) == len(trace) + 1
    builds.clear()
    seen = []
    def choose(current):
        n = len(seen)
        if n == len(trace):
            assert current == node
            return None
        expected, selected = trace[n]
        assert current == expected
        seen.append(current)
        return selected
    followed = sim.follow_actions(choose)
    assert followed == node
    assert len(builds) == 1 and len(seen) == len(trace)
    assert sim.fork(followed) == followed
    if not followed.terminal and not followed.cutoff:
        choice = policy(node.player_information())
        assert sim.advance_action(followed, choice) == sim.advance_action(node, choice)


def test_follow_handles_nested_scry_before_factory_returns():
    from benchmarks.search_hidden_worlds import public_position, catalog
    from game.cards.starter_support import ScryOperation
    from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
    from game.game_state import Card
    from game.enums import ZoneType
    def recipe(controllers):
        game = public_position(controllers)
        owner = game.state.players[0]
        for name in ("Shock", "Island"):
            owner.add_card(Card(catalog()[name], owner), ZoneType.DECK)
        context = ResolutionContext(controller=owner)
        game.loop.processor.executor.resolve(game.state,
            ScheduledResolution(FixedExecutionPlan([ScryOperation(context, 2)]), context))
        return game
    sim = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True)
    root = sim.start()
    assert root.decision.kind == "ScryRequest"
    selected = root.player_information().actions[0]
    expected = sim.advance_action(root, selected)
    node = sim.follow_actions(lambda n: selected if not n.history else None)
    assert node == expected
    assert sim.fork(node) == node


def test_follow_respects_decision_limit_and_rejects_unoffered_actions():
    recipe = tactical_scenario("answered_shock")
    sim = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True, max_decisions=1)
    with pytest.raises(SimulationBudgetExceeded, match="Decision limit"):
        sim.follow_actions(lambda n: ObservationOpponentPolicy()(n.player_information()))
    with pytest.raises(ValueError, match="not offered"):
        sim.follow_actions(lambda n: PlayerAction("forged", "{}"))
    with pytest.raises(ValueError, match="track_information"):
        SearchSimulator(recipe).follow_actions(lambda n: None)
    assert sim.follow_actions(lambda n: None) == sim.start()


def test_follow_terminal_and_phase_cutoff_have_complete_history():
    sim = SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy,
                          track_information=True, max_phases=1)
    policy = ObservationOpponentPolicy()
    step = sim.start()
    while not step.terminal and not step.cutoff:
        step = sim.advance_action(step, policy(step.player_information()))
    followed = sim.follow_actions(lambda n: policy(n.player_information()))
    assert followed == step and followed.history
    assert followed.terminal or followed.cutoff


def test_follow_propagates_chooser_errors_without_reusing_partial_runtime():
    sim = SearchSimulator(tactical_scenario("lethal_shock"), policy_for=tactical_policy, track_information=True)
    root = sim.start()
    def broken(node):
        raise RuntimeError("Broken history selector")
    with pytest.raises(RuntimeError, match="Broken history"):
        sim.follow_actions(broken)
    assert sim.start() == root
