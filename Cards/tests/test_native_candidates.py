"""Early proposal selection and sampled-world-local detached menu reuse."""
from dataclasses import replace, asdict
from unittest.mock import patch
import json

import pytest

from runtime_write_guard import deny_writes
from benchmarks.search_tactics import tactical_scenario
from benchmarks.mcts_top_k import make_request
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates, target_order
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import SearchSimulator
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.simulation.search_config import SearchGenerationPolicy, SearchAgentConfig
from test_candidate_ranking import crowded


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


def native_policy(**kwargs):
    return SearchGenerationPolicy(progressive_candidates=True, candidate_initial_width=2,
                                  candidate_native_ordering=True, **kwargs)


@pytest.mark.parametrize('case', ['targets', 'attackers', 'blockers'])
def test_native_first_choices_do_not_project_or_sort_complete_action_batches(case):
    request = make_request(case)
    model = DeterministicCandidates(4, 32, native_ordering=True)
    with patch('game.ai.search_decision.DecisionProjector.build', side_effect=AssertionError('Projected candidates')):
        frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
        try:
            offered, complete = frontier.take(4)
        finally:
            frontier.close()
    assert len(offered) == 4 and not complete


@pytest.mark.parametrize('case', ['targets', 'attackers', 'blockers'])
def test_early_proposals_have_exactly_the_exhaustive_native_legal_set(case):
    request = make_request(case)
    policy = SearchGenerationPolicy()(request)
    full = set(map(ChoiceDescriptor.from_option, request.option_space(policy)))
    options = tuple(DeterministicCandidates(4, 16, native_ordering=True).generate(request, policy))
    descriptors = tuple(map(ChoiceDescriptor.from_option, options))
    assert set(descriptors) == full and len(descriptors) == len(set(descriptors))


def test_early_target_order_retains_pass_and_lethal_before_executable_actions_are_ranked():
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def recipe(controllers):
        game = tactical_scenario('lethal_shock')(controllers)
        owner = game.state.players[1]
        owner.add_card(Card(starter_catalog()['Impassioned Orator'], owner), ZoneType.BATTLEFIELD)
        return game
    node = SearchSimulator(recipe, policy_for=native_policy(), track_information=True,
                           structured_records=True).start()
    info = node.player_information()
    assert len(info.actions) == 2 and any(a.kind == 'PassPriorityAction' for a in info.actions)
    assert ObservationOpponentPolicy().rank(info)[0].score > 900


def test_expanded_complete_menu_reused_without_reconstructing_the_world():
    calls = []
    def recipe(controllers):
        calls.append(1)
        return crowded(controllers)
    sim = SearchSimulator(recipe, policy_for=native_policy(), track_information=True,
                          structured_records=True, projection_cache_size=8)
    root = sim.start()
    complete = sim.expand_candidates(root, 32)
    assert complete.player_information().menu_complete
    count = len(calls)
    hits = sim.candidate_cache_hits
    again = sim.expand_candidates(root, 40)
    assert len(calls) == count and sim.candidate_cache_hits == hits+1
    assert again.player_information().candidate_width == 40
    assert again.player_information().actions == complete.player_information().actions
    assert sim.fork(again) == again
    assert len(sim._candidate_nodes) <= 8
    assert sim.fork(root) == root


def test_reservoir_admits_only_the_exact_requested_prefix_and_replays_it():
    sim = SearchSimulator(crowded, policy_for=native_policy(), track_information=True,
                          structured_records=True, projection_cache_size=8)
    root = sim.start()
    assert len(root.choices) == 2 and not root.player_information().menu_complete
    assert len(sim._candidate_nodes[root.history].choices) > len(root.choices)
    for width in (3, 6, 9, 12, 18):
        expanded = sim.expand_candidates(root, width)
        assert expanded.player_information().candidate_width == width
        assert len(expanded.choices) <= width
        assert sim.fork(expanded) == expanded


def test_transition_reuse_ignores_only_frontier_width_within_the_same_world():
    from game.ai.information_set_search import _Run
    factory = lambda: SearchSimulator(crowded, policy_for=native_policy(), track_information=True,
        structured_records=True, projection_cache_size=8)
    sim = factory()
    root = sim.start()
    action = root.player_information().actions[0]
    expanded = sim.expand_candidates(root, 6)
    run = _Run(InformationSetSearch(opponent_policy=ObservationOpponentPolicy()), 0, 42)
    assert run.transition_key(sim, root, action) == run.transition_key(sim, expanded, action)
    other = factory()
    other_root = other.start()
    assert run.transition_key(sim, root, action) != run.transition_key(other, other_root, action)


def test_native_generation_builds_only_the_consumed_few_executable_plans():
    from game.game_actions.generation.exec_plan_gen_pipeline import PreparedExecutionPlan
    request = make_request('targets')
    calls = []
    original = PreparedExecutionPlan.bind
    def counted(prepared, binding):
        calls.append(any(effect.slots for effect in prepared.effects.sequence))
        return original(prepared, binding)
    model = DeterministicCandidates(4, 32, native_ordering=True)
    with patch.object(PreparedExecutionPlan, 'bind', counted):
        frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
        try:
            options, _ = frontier.take(4)
        finally:
            frontier.close()
    assert len(options) == 4
    # Mana/source discovery may compile constant witnesses as well. Only the
    # consumed target plans (plus one source witness) are constructed, not 41.
    assert sum(calls) <= 5


def test_early_order_does_not_inspect_opponent_hidden_identities():
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def scenario(name):
        def build(controllers):
            game = crowded(controllers)
            owner = game.state.players[1]
            owner.add_card(Card(starter_catalog()[name], owner), ZoneType.HAND)
            return game
        return build
    roots = [SearchSimulator(scenario(name), policy_for=native_policy(), track_information=True,
        structured_records=True, projection_cache_size=8).start().player_information()
        for name in ('Island', 'Mountain')]
    assert roots[0] == roots[1]


def test_authored_resolver_retains_its_own_first_group_contract():
    from dummy_classes import DummyEffect
    from helper_funcs import make_slot
    from test_ability_pipeline import setup_ability, node
    from game.target.target_resolver import TargetResolver
    from game.game_state import Card, CardDefinition
    from game.enums import CardType, ZoneType
    from game.game_actions.generation.decision_abstraction.requests import AbilityDecisionRequest
    class FirstOnly(TargetResolver):
        def generate_target_options(self, source, controller, state, reserved=None):
            yield next(super().generate_target_options(source, controller, state, reserved))
    targets = []
    slot = make_slot('target', targets)
    slot = replace(slot, target_resolver=FirstOnly(slot.target_resolver.target_spec, slot.target_resolver.target_selector))
    state, ability = setup_ability(node('effect', 'target'), (DummyEffect('effect'),), (slot,))
    for power in (1, 9):
        card = Card(CardDefinition(str(power), types=frozenset({CardType.CREATURE}), power=power, toughness=power), ability.controller)
        ability.controller.add_card(card, ZoneType.BATTLEFIELD)
        targets.append(card)
    request = AbilityDecisionRequest(state, ability.controller, ability)
    native = tuple(DeterministicCandidates(native_ordering=True).generate(request, None))
    assert tuple(map(ChoiceDescriptor.from_option, native)) == tuple(map(ChoiceDescriptor.from_option, request.options))


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
def test_cached_and_uncached_widening_preserve_choices_values_and_visits(selection):
    runs = []
    for cache in (False, True):
        sims = []
        def factory(seed):
            sim = SearchSimulator(crowded, policy_for=native_policy(), track_information=True,
                structured_records=True, projection_cache_size=16)
            if not cache:
                sim._candidate_cache_limit = 0
            sims.append(sim)
            return sim
        root = factory(0).start().player_information()
        result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
            compress_forced=True, require_root_coverage=False, widening_constant=4,
            transition_cache_size=2048 if cache else 0,
            budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=2, max_advances=256)
        ).search(factory, root, seed=42)
        assert not result.used_fallback
        runs.append((result, sum(s.candidate_cache_hits for s in sims)))
    a, b = runs[0][0], runs[1][0]
    assert (a.choice, a.value, a.estimates, a.stop_reason) == (b.choice, b.value, b.estimates, b.stop_reason)
    assert a.metrics.advance_attempts == b.metrics.advance_attempts
    assert b.metrics.candidate_expansions < a.metrics.candidate_expansions
    assert runs[1][1] > 0


def test_proposal_scope_closes_and_does_not_change_ordinary_target_order():
    request = make_request('targets')
    options = DeterministicCandidates(2, 8, native_ordering=True).generate(request, SearchGenerationPolicy()(request))
    next(options)
    options.close()
    values = (3, 1, 2)
    assert target_order(values, None, None, None) is values


def test_native_candidates_also_support_descriptor_only_simulators():
    sim = SearchSimulator(crowded, policy_for=native_policy(), structured_records=True)
    root = sim.start()
    assert len(root.choices) == 2
    assert sim.fork(root) == root


@pytest.mark.parametrize('case', ['lethal_shock', 'useful_pacifism', 'answered_shock'])
def test_native_ordering_composes_with_staged_ability_construction(case):
    from test_ability_construction import fixture, expand
    from game.ai.generation_candidates import candidate_ranking
    from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
    from game.game_actions.generation.decision_abstraction.priority_frames import PrioritySourceOption
    game, request = fixture(case)
    policy = PriorityGenerationPolicy(include_mana=False, include_concede=False, equivalence=None)
    expected = set(map(ChoiceDescriptor.from_option, request.option_space(policy)))
    with candidate_ranking(native=True):
        staged = request.option_space(replace(policy, staged_sources=True, staged_ability=True))
        leaves = [leaf for option in staged for leaf in (
            expand(option._construction, game.state) if type(option) is PrioritySourceOption else (option,))]
    assert set(map(ChoiceDescriptor.from_option, leaves)) == expected


def test_native_config_is_explicit_and_round_trips():
    config = SearchAgentConfig(deck_knowledge='both_lists', progressive_candidates=True,
                              widening_constant=4, candidate_native_ordering=True)
    assert SearchAgentConfig.parse(json.loads(json.dumps(asdict(config)))) == config
    for values in ({'candidate_native_ordering': 'yes'},
                   {'candidate_native_ordering': True},
                   {'candidate_native_ordering': True, 'progressive_candidates': True,
                    'widening_constant': 4, 'candidate_rank_window': 8}):
        with pytest.raises(ValueError):
            SearchAgentConfig(deck_knowledge='both_lists', **values)
