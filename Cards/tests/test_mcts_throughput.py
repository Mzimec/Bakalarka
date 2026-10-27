"""Visibility, work budgets and semantics of the independent search switches."""
from dataclasses import asdict, replace
from random import Random
from unittest.mock import patch

import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.action_hierarchy import ActionHierarchy
from game.ai.decision_maker import DecisionMaker, DecisionResult, PriorityDecisionRequest
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_actions import PlayerActionBuilder
from game.ai.search_candidates import CandidateOrdering, ProgressiveWidening
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import SearchSimulator, SimulationBudgetExceeded
from game.game_actions.data_structs.game_action import AbilityAction
from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
from game.game_actions.generation.decision_abstraction.priority_frames import PrioritySourceOption, PriorityAbilityRequest
from game.simulation.search_config import SearchGenerationPolicy, SearchAgentConfig


@pytest.mark.parametrize("case", ["lethal_shock", "answered_shock", "attack_pacified"])
@pytest.mark.parametrize("observer", [0, 1])
def test_search_views_preserve_both_actor_information_and_observer_leaves(case, observer):
    full = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True)
    lean = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True,
                           view_mode="search", observer=observer)
    policy = ObservationOpponentPolicy()
    a, b = full.start(), lean.start()
    for _ in range(12):
        assert (a.terminal, a.cutoff, a.phase_steps) == (b.terminal, b.cutoff, b.phase_steps)
        assert a.observation(observer) == b.observation(observer)
        assert a._recalls == b._recalls and a.choices == b.choices
        if a.terminal or a.cutoff:
            break
        assert a.player_information() == b.player_information()
        info = b.player_information()
        assert b._projected_information is info
        before = info.current.observation()
        b.observation(info.current.player).clear()
        assert info.current.observation() == before
        action = policy(info)
        a, b = full.advance_action(a, action), lean.advance_action(b, action)
    assert lean.fork(b) == b


def test_search_diagnostic_views_are_explicit_not_a_policy_visibility_grant():
    sim = SearchSimulator(tactical_scenario("lethal_shock"), track_information=True,
                          policy_for=tactical_policy, view_mode="search", observer=0)
    root = sim.start()
    assert root.decision.player == 0
    with pytest.raises(ValueError, match="diagnostic view"):
        root.observation(1)
    assert "Counterspell" not in str(root.player_information())


@pytest.mark.parametrize("case", ["answered_shock", "useful_pacifism", "attack_pacified"])
def test_projection_cache_replays_all_memories_and_binds_fresh_local_actions(case):
    full = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True)
    fast = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True,
                           projection_cache_size=3)
    a, b = full.start(), fast.start()
    policy = ObservationOpponentPolicy()
    for _ in range(10):
        assert a == b and a._recalls == b._recalls
        if a.terminal or a.cutoff:
            break
        assert a.player_information() == b.player_information()
        selected = policy(a.player_information())
        a, b = full.advance_action(a, selected), fast.advance_action(b, selected)
    assert fast.projection_cache_hits > 0 and len(fast._projections) == 3
    assert fast.fork(b) == b
    # No recipe, live option, controller or mutable runtime graph in a cache row.
    for row in fast._projections.values():
        assert all(isinstance(d, ChoiceDescriptor) for d in row.descriptors)
        assert all(isinstance(d, ChoiceDescriptor) for _, d in row.bindings)


def test_pass_action_adapter_does_not_scan_card_visibility():
    from game.game_actions.data_structs.game_action import PassPriorityAction
    from game.ai.modular_agent import ModularAgent
    game = tactical_scenario("lethal_shock")((ModularAgent(), ModularAgent()))
    req = PriorityDecisionRequest(game.state, game.state.players[0])
    with patch("game.ai.search_actions.PlayerObservationBuilder", side_effect=AssertionError("visibility scan")):
        builder = PlayerActionBuilder(req)
        assert builder.context.request_context() is None
        assert builder.action(PassPriorityAction(req.player)).kind == "PassPriorityAction"


@pytest.mark.parametrize("advances,depth", [(256, 3), (19, 3), (256, 1)])
def test_forced_chain_cache_preserves_every_choice_leaf_and_hard_budget(advances, depth):
    def factory(seed):
        return SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy,
                               track_information=True)
    info = factory(0).start().player_information()
    results, inputs = [], []
    for enabled in (False, True):
        seen = []
        def choose(info):
            seen.append(info)
            return ObservationOpponentPolicy()(info)
        planner = InformationSetSearch(opponent_policy=choose, rollout_policy=choose,
            compress_forced=True, forced_chain_cache=enabled,
            budget=InformationSearchBudget(worlds=1, iterations=24, max_depth=depth, max_advances=advances))
        results.append(planner.search(factory, info, seed=42))
        inputs.append(seen)
    a, b = results
    assert replace(a, metrics=b.metrics) == b
    ignored = {"elapsed_ms", "cpu_ms", "world_initialization_ms", "advance_ms", "forced_chain_hits", "forced_chain_steps"}
    assert {k: v for k, v in asdict(a.metrics).items() if k not in ignored} == {
        k: v for k, v in asdict(b.metrics).items() if k not in ignored}
    assert inputs[0] == inputs[1]
    assert b.metrics.advance_attempts <= advances
    if advances == 256:
        assert b.metrics.forced_chain_hits > 0 and b.metrics.forced_chain_steps > 0


@pytest.mark.parametrize("case", ["lethal_shock", "useful_pacifism", "redundant_pacifism"])
def test_staged_sources_have_exactly_the_flat_legal_completions_without_execution(case):
    from game.ai.modular_agent import ModularAgent
    game = tactical_scenario(case)((ModularAgent(), ModularAgent()))
    game.state.priority.current_player = owner = game.state.players[0]
    req = PriorityDecisionRequest(game.state, owner)
    policy = PriorityGenerationPolicy(include_mana=False, include_concede=False, equivalence=None)
    before = (tuple(owner.hand.values()), tuple(c.is_tapped for c in owner.battlefield.values()),
              game.state.rng.getstate(), game.state.players[1].health)
    flat = tuple(req.option_space(policy))
    sources = tuple(req.option_space(replace(policy, staged_sources=True)))
    expanded = []
    for option in sources:
        if type(option) is PrioritySourceOption:
            with pytest.raises(ValueError, match="completed"):
                option.get_intents()
            child = PriorityAbilityRequest(game.state, owner, option.ability, _policy=option._policy)
            options = tuple(child.option_space())
            assert ChoiceDescriptor.from_option(option.witness) in tuple(map(ChoiceDescriptor.from_option, options))
            expanded.extend(options)
        else:
            expanded.append(option)
    assert set(map(ChoiceDescriptor.from_option, expanded)) == set(map(ChoiceDescriptor.from_option, flat))
    assert before == (tuple(owner.hand.values()), tuple(c.is_tapped for c in owner.battlefield.values()),
                      game.state.rng.getstate(), game.state.players[1].health)
    assert getattr(game.state, "_mana_discovery_context", None) is None


def test_source_discovery_only_consumes_one_witness_per_ability_and_closes_streams():
    from game.ai.modular_agent import ModularAgent
    from game.game_actions.generation.decision_abstraction.action_pipelines import AbilityDecisionGenerationPipeline
    game = tactical_scenario("lethal_shock")((ModularAgent(), ModularAgent()))
    game.state.priority.current_player = game.state.players[0]
    req = PriorityDecisionRequest(game.state, game.state.players[0])
    yields, closed = [], []
    original = AbilityDecisionGenerationPipeline._generate
    def counted(self, request, policy):
        try:
            for option in original(self, request, policy):
                yields.append(request.ability)
                yield option
        finally:
            closed.append(request.ability)
    with patch.object(AbilityDecisionGenerationPipeline, "_generate", counted):
        options = tuple(req.option_space(SearchGenerationPolicy(True)(req)))
    sources = [o for o in options if type(o) is PrioritySourceOption]
    assert sources and all(yields.count(o.ability) == 1 and closed.count(o.ability) == 1 for o in sources)


def test_controller_completes_a_source_before_returning_a_game_action():
    class Chooser(DecisionMaker):
        def __init__(self):
            self.seen = []
        def _decide(self, request):
            self.seen.append(type(request).__name__)
            options = tuple(request.option_space(SearchGenerationPolicy(True)(request)))
            return DecisionResult(next((o for o in options if type(o) is PrioritySourceOption), options[-1]))
    own, other = Chooser(), Chooser()
    game = tactical_scenario("lethal_shock")((own, other))
    game.state.priority.current_player = game.state.players[0]
    action = own.decide(PriorityDecisionRequest(game.state, game.state.players[0])).value
    assert type(action) is AbilityAction
    assert own.seen == ["PriorityDecisionRequest", "PriorityAbilityRequest"]
    assert not game.state.stack.items and game.state.players[1].health == 2
    assert not any(c.is_tapped for c in game.state.players[0].battlefield.values())


@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
def test_source_prefix_consumes_advances_but_only_completed_action_consumes_depth(selection):
    policy = SearchGenerationPolicy(True)
    def factory(seed):
        return SearchSimulator(tactical_scenario("lethal_shock"), policy_for=policy, track_information=True)
    sim = factory(0)
    root = sim.start()
    source = next(a for a in root.player_information().actions if a.kind == "PrioritySourceOption")
    child = sim.advance_action(root, source)
    assert child.decision.kind == "PriorityAbilityRequest"
    assert child.observation(0)["pending_abilities"] == []
    assert sim.fork(child) == child
    result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
        rollout_policy=ObservationOpponentPolicy(), compress_forced=True,
        budget=InformationSearchBudget(worlds=1, iterations=12, max_depth=1, max_advances=160)
    ).search(factory, root.player_information(), seed=42)
    assert not result.used_fallback and result.choice.kind == "PrioritySourceOption"
    assert result.value > 0
    assert result.metrics.advance_attempts > result.metrics.iterations_completed
    capped = SearchSimulator(tactical_scenario("lethal_shock"), policy_for=policy, track_information=True, max_decisions=1)
    with pytest.raises(SimulationBudgetExceeded, match="Decision limit"):
        capped.follow_actions(lambda n: next((a for a in n.player_information().actions
            if a.kind == "PrioritySourceOption"), n.player_information().actions[0]))


def test_source_prior_does_not_hide_an_immediate_lethal_behind_a_bad_first_target():
    from game.ai.action_priors import HeuristicActionPrior
    policy = ObservationOpponentPolicy()
    def factory(seed):
        return SearchSimulator(tactical_scenario("lethal_shock"), policy_for=SearchGenerationPolicy(True),
                               track_information=True)
    info = factory(0).start().player_information()
    result = InformationSetSearch(opponent_policy=policy, rollout_policy=policy, selection="puct",
        action_prior=HeuristicActionPrior(policy), force_initial_coverage=False, require_root_coverage=False,
        compress_forced=True, budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=1, max_advances=256)
    ).search(factory, info, seed=42)
    assert result.choice.kind == "PrioritySourceOption" and result.value > .9


def test_progressive_candidate_order_is_deterministic_retains_pass_and_eventually_allows_every_action():
    from test_information_set_search import Tree
    tree = Tree({"root": {"children": {"z": "root", "PassPriorityAction": "root", "a": "root"}}})
    info = tree.start().player_information()
    weights = {a: (10 if a.kind == "z" else 1) for a in info.actions}
    ordering = CandidateOrdering()
    order = ordering.order(info, weights)
    assert order == ordering.order(replace(info, actions=tuple(reversed(info.actions))), weights)
    assert order[0].kind == "PassPriorityAction"
    assert len(ProgressiveWidening(1, .5).select(order, 0)) == 1
    assert ProgressiveWidening(1, .5).select(order, 100) == order


@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
def test_widening_is_explicit_and_full_coverage_is_not_claimed_for_partial_search(selection):
    from test_information_set_search import Tree, first
    factory = lambda seed: Tree({"root": {"children": {f"a{i}": "win" for i in range(20)}}, "win": {"winner": 0}})
    search = InformationSetSearch(opponent_policy=first, selection=selection, widening_constant=1,
        widening_exponent=.5, require_root_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=1))
    result = search.search(factory, factory(0).start().player_information(), seed=42)
    assert not result.used_fallback and not result.root_coverage_complete
    assert len(result.estimates) == 20 and result.metrics.root_actions_visited <= 3
    assert result.metrics.candidate_orderings == 1 and result.metrics.eligible_actions == 3
    strict = InformationSetSearch(opponent_policy=first, selection=selection, widening_constant=1,
        budget=search.budget).search(factory, factory(0).start().player_information(), seed=42)
    assert strict.used_fallback and strict.fallback_reason == "root_coverage"


def test_hierarchical_widening_cannot_select_an_excluded_action_or_fake_remaining_coverage():
    from test_action_hierarchy import ability
    a, b, c = ability("a", 0), ability("a", 1), ability("b", 0)
    hierarchy = ActionHierarchy((a, b, c), {a: .8, b: .1, c: .1})
    assert hierarchy.select(1, Random(1), allowed_actions=(b,))[0] == b
    hierarchy.record(b, .5)
    assert hierarchy.select(1, Random(1), allowed_actions=(b,)) == (b, False)
    assert hierarchy.select(1, Random(1), allowed_actions=(b, c))[0] == c


@pytest.mark.parametrize("selection", ["uct", "puct", "hierarchical_puct"])
def test_tree_memory_requires_same_session_exact_information_and_same_search_model(selection):
    from test_information_set_search import Tree, first
    factory = lambda seed: Tree({"root": {"children": {"a": "win", "b": "loss"}},
                               "win": {"winner": 0}, "loss": {"winner": 1}})
    search = InformationSetSearch(opponent_policy=first, selection=selection, tree_reuse_size=4,
        budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=1))
    info, token = factory(0).start().player_information(), object()
    first_result = search.search(factory, info, seed=42, reuse_context=token)
    reused = search.search(factory, info, seed=43, reuse_context=token)
    assert reused.metrics.reused_information_nodes == 1 and reused.metrics.reused_root_visits == 8
    if selection != "uct":
        assert first_result.metrics.prior_evaluations == 1 and reused.metrics.prior_evaluations == 0
    assert sum(e.visits for e in reused.estimates) == 16
    assert reused.metrics.iterations_completed == first_result.metrics.iterations_completed == 8
    assert all(set(vars(row)) == {"information", "statistics", "probabilities", "horizon"} for row in search.tree_memory.rows.values())
    new_session = search.search(factory, info, seed=42, reuse_context=object())
    assert new_session.metrics.reused_root_visits == 0
    # No implicit cross-call cache for standalone experiments.
    assert search.search(factory, info, seed=42).metrics.reused_information_nodes == 0
    search.search(factory, info, reuse_context=token)
    search.budget = replace(search.budget, max_depth=2)
    assert search.search(factory, info, reuse_context=token).metrics.reused_information_nodes == 0
    search.reset()
    assert not search.tree_memory.rows


def test_reused_coverage_never_masks_failed_preparation_of_a_fresh_world_pool():
    from test_information_set_search import Tree, first
    from game.ai.search_simulator import HistorySamplingExhausted
    from game.ai.search_recall import PlayerRecall
    factory = lambda seed: Tree({"root": {"children": {"go": "win"}}, "win": {"winner": 0}})
    planner = InformationSetSearch(opponent_policy=first, tree_reuse_size=4,
        budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=1))
    info, token = factory(0).start().player_information(), object()
    assert not planner.search(factory, info, reuse_context=token).used_fallback
    class Unavailable:
        def start(self):
            raise HistorySamplingExhausted("No fresh world")
    result = planner.search(lambda seed: Unavailable(), info, reuse_context=token)
    assert result.used_fallback and result.metrics.reused_root_visits == 4
    assert result.metrics.iterations_completed == 0 and result.fallback_reason == "history_sampling_budget"
    assert not planner.tree_memory.rows


@pytest.mark.parametrize("changes", [{"simulator_views": "invalid"}, {"projection_cache_size": True},
    {"staged_priority": 1}, {"tree_reuse_size": -1}, {"widening_constant": .5},
    {"widening_constant": float("nan")}, {"widening_exponent": 0},
    {"world_backend": "history", "tree_reuse_size": 4}])
def test_new_search_switches_are_validated(changes):
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge="both_lists", **changes)
