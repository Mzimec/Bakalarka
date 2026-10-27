from contextlib import nullcontext
import pytest

from runtime_write_guard import deny_writes
from benchmarks.mcts_decision_latency import Components, answer, detached_planner
from benchmarks.search_tactics import tactical_scenario
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator
from game.simulation.search_config import SearchGenerationPolicy


def test_components_preserve_search_answer_and_work():
    policy = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True)
    def factory(seed):
        return SearchSimulator(tactical_scenario('answered_shock'), policy_for=policy,
            track_information=True, structured_records=True)
    profiler, results = Components(), []
    with deny_writes():
        for enabled in (False, True):
            with profiler.installed() if enabled else nullcontext():
                root = factory(0).start().player_information()
                planner = InformationSetSearch(selection='puct', opponent_policy=ObservationOpponentPolicy(),
                    require_root_coverage=False, force_initial_coverage=False, compress_forced=True,
                    tree_reuse_size=16, budget=InformationSearchBudget(worlds=1, iterations=8,
                        max_depth=2, max_advances=256))
                results.append(planner.search(factory, root, seed=42, reuse_context=policy))
        assert answer(results[0]) == answer(results[1])
        assert profiler.summary()['scopes']['tree_selection']['calls'] > 0
        assert profiler.summary()['simulation_stages']
        cloned = detached_planner(planner)
        assert cloned.tree_memory.context is planner.tree_memory.context
        assert cloned.tree_memory.nodes
        for key, old in planner.tree_memory.nodes.items():
            new = cloned.tree_memory.nodes[key]
            assert new is not old and new.information is old.information
            assert new.edges is not old.edges
            for action in old.edges:
                assert new.edges[action] is not old.edges[action]
                new.edges[action].visits += 1
                assert new.edges[action].visits != old.edges[action].visits


def test_component_hooks_restore_after_failure():
    import game.ai.simulation_world as worlds
    original_copy, original_init = worlds.copy_world, SearchSimulator.__init__
    with deny_writes(), pytest.raises(RuntimeError):
        with Components().installed():
            raise RuntimeError('diagnostic interrupted')
    assert worlds.copy_world is original_copy
    assert SearchSimulator.__init__ is original_init
