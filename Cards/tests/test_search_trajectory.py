"""Continuous execution changes engine work, never search semantics or visibility."""
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget, _Run
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator


def compare(factory, *, selection="uct", cache=256, budget=None):
    info = factory(0).start().player_information()
    rows, traces, inputs = [], [], []
    for mode in ("replay", "continuous"):
        trace, observed = [], []
        store = _Run.store_transition
        def record(run, sim, node, action, child):
            trace.append((node, action, child))
            store(run, sim, node, action, child)
        policy = ObservationOpponentPolicy()
        def choose(info):
            observed.append(info)
            return policy(info)
        search = InformationSetSearch(opponent_policy=choose, rollout_policy=choose,
            selection=selection, transition_cache_size=cache, trajectory_mode=mode,
            budget=budget or InformationSearchBudget(worlds=2, iterations=6, max_advances=48, max_depth=3))
        with patch.object(_Run, "store_transition", record):
            result = search.search(factory, info, seed=42)
        rows.append(result)
        traces.append(trace)
        inputs.append(observed)
    a, b = rows
    assert replace(a, metrics=b.metrics) == b
    ignored = {"elapsed_ms", "cpu_ms", "world_initialization_ms", "advance_ms", "trajectory_runs",
               "checkpoint_hits", "checkpoint_entries", "checkpoint_skipped_decisions",
               'checkpoint_copies','checkpoint_evictions','checkpoint_capture_ms',
               'checkpoint_copy_rejections',
               "world_materializations", "world_restores", "structured_value_hits"}
    assert {k: v for k, v in asdict(a.metrics).items() if k not in ignored} == {
        k: v for k, v in asdict(b.metrics).items() if k not in ignored}
    assert traces[0] == traces[1]  # Includes all menus, hidden-world views and recalls.
    assert inputs[0] == inputs[1]  # Policies get the same actor-filtered information.
    assert b.metrics.trajectory_runs <= b.metrics.iterations_started
    return a, b


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "attack_pacified"])
@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
@pytest.mark.parametrize("cache", [0, 1, 256])
def test_streamed_search_matches_every_transition_and_policy_input(case, selection, cache):
    builds = []
    def factory(seed):
        def build(controllers):
            builds.append(1)
            return tactical_scenario(case)(controllers)
        return SearchSimulator(build, policy_for=tactical_policy, track_information=True)
    a, b = compare(factory, selection=selection, cache=cache)
    assert b.metrics.trajectory_runs < b.metrics.simulator_advance_calls


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("checkpoints", [0, 8])
def test_sampled_worlds_and_checkpoints_preserve_hidden_history(reuse, checkpoints):
    from test_current_position_worlds import position, prepare
    from game.ai.current_position_worlds import _PreparedWorld
    from game.ai.checkpoint_simulator import CheckpointSimulator
    origin, game = position()
    _, _, snapshot = prepare(origin, game)
    initial_rng = game.state.rng.getstate()
    def factory(seed):
        template = _PreparedWorld(snapshot, seed, reuse=reuse)
        options = dict(track_information=True, policy_for=tactical_policy, max_phases=12)
        return (CheckpointSimulator(template, checkpoint_limit=checkpoints, checkpoint_interval=1, **options)
                if checkpoints else SearchSimulator(template, **options))
    a, b = compare(factory)
    before = a.metrics.world_materializations + a.metrics.world_restores + a.metrics.checkpoint_hits
    after = b.metrics.world_materializations + b.metrics.world_restores + b.metrics.checkpoint_hits
    assert after < before
    assert game.state.rng.getstate() == initial_rng


@pytest.mark.parametrize("limits,budget", [
    ({"max_decisions": 1}, InformationSearchBudget(worlds=1, iterations=4, max_depth=8)),
    ({"max_phases": 1}, InformationSearchBudget(worlds=1, iterations=4, max_depth=20)),
    ({}, InformationSearchBudget(worlds=1, iterations=4, max_depth=8, max_advances=3)),
    ({}, InformationSearchBudget(worlds=1, iterations=4, max_depth=1, max_extensions=0)),
])
def test_limits_interrupt_identically_without_backpropagating_partial_paths(limits, budget):
    def factory(seed):
        return SearchSimulator(tactical_scenario("answered_shock"), track_information=True,
                               policy_for=tactical_policy, **limits)
    compare(factory, budget=budget)


def test_policy_error_is_propagated_outside_engine_and_next_search_is_clean():
    def factory(seed):
        return SearchSimulator(tactical_scenario("answered_shock"), track_information=True,
                               policy_for=tactical_policy)
    info = factory(0).start().player_information()
    def broken(info):
        raise ValueError("policy failed deliberately")
    search = InformationSetSearch(opponent_policy=broken,
        budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=8))
    with pytest.raises(ValueError, match="policy failed deliberately"):
        search.search(factory, info, seed=42)
    compare(factory)


def test_nested_resolution_choices_continue_without_replaying_operation_prefix():
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
    def factory(seed):
        return SearchSimulator(recipe, track_information=True, policy_for=tactical_policy)
    assert factory(0).start().decision.kind == "ScryRequest"
    compare(factory, budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=3, max_advances=32))


def test_production_configuration_selects_continuous_and_exposes_reference_mode():
    from game.simulation.search_config import make_search_controller
    from game.ai.game_origin import GameOrigin
    from game.cards.decks import load_arena_starter
    from game.cards.catalog import game_catalog
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")), game_catalog)
    agent = make_search_controller({"deck_knowledge": "both_lists"}, origin, max_decisions=100)
    assert agent.search.trajectory_mode == "continuous"
    agent = make_search_controller({"deck_knowledge": "both_lists", "trajectory_mode": "replay"},
                                   origin, max_decisions=100)
    assert agent.search.trajectory_mode == "replay"


@pytest.mark.parametrize("mode", [True, None, "unknown"])
def test_invalid_trajectory_mode_is_rejected(mode):
    from game.simulation.search_config import SearchAgentConfig
    with pytest.raises(ValueError, match="trajectory_mode"):
        InformationSetSearch(opponent_policy=lambda i: i.actions[0], trajectory_mode=mode)
    with pytest.raises(ValueError, match="trajectory_mode"):
        SearchAgentConfig(deck_knowledge="both_lists", trajectory_mode=mode)
