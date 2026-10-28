"""Role-orbit completeness, native outcome references, privacy and real search."""
from collections import Counter
from dataclasses import asdict, replace
from itertools import product
import json
from unittest.mock import patch

import pytest

from benchmarks.mcts_top_k import make_request, make_case
from game.ai.combat_equivalence import CombatDeclarationEquivalence, CombatRoles
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates, BeamCombatStrategy
from game.ai.search_descriptors import ChoiceDescriptor
from game.game_actions.generation.decision_abstraction.selection_strategies import FullAttackersStrategy, FullBlockersStrategy
from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest, DeclareBlockersRequest
from game.game_state import Card, CardDefinition
from game.enums import CardType, ZoneType
from game.simulation.search_config import SearchGenerationPolicy, SearchAgentConfig
from runtime_write_guard import deny_writes


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


@pytest.mark.parametrize('case,expected,groups', [('attackers', 20, [9, 1]), ('blockers', 12, [5])])
def test_integer_representatives_cover_every_native_declaration_with_exact_multiplicity(case, expected, groups):
    request = make_request(case)
    context = CombatDeclarationEquivalence(request, blockers=case == 'blockers')
    assert list(map(len, context.attacker_groups)) == groups
    representatives = tuple(context.representatives())
    assert len(representatives) == expected
    assert len({r.key for r in representatives}) == expected
    native = tuple(request.options)
    counts = Counter(context.action_class(o.declarations).key for o in native)
    assert counts == {r.key: r.multiplicity for r in representatives}
    assert sum(r.multiplicity for r in representatives) == len(native)
    for action in representatives:
        option_type = request.option_type
        assert context.action_class(option_type(action.bind()).declarations).key == action.key


def test_fully_matching_ten_attackers_have_eleven_counts_not_1024_subsets():
    request = make_request('attackers')
    cards = request.state.combat.legal_attackers(request.player)
    for card in cards:
        card.state.zone_info.entered_at = cards[0].state.zone_info.entered_at
    context = CombatDeclarationEquivalence(request, blockers=False)
    assert list(map(len, context.attacker_groups)) == [10]
    rows = tuple(context.representatives())
    assert len(rows) == 11 and sum(row.multiplicity for row in rows) == 1024
    assert {len(row.binding) for row in rows} == set(range(11))


def test_multiple_defenders_keep_separate_count_coordinates():
    from test_combat import game, creature
    runtime = game.__wrapped__()
    state = runtime[0]
    creature(runtime)
    creature(runtime)
    walker = Card(CardDefinition('Walker', types=frozenset({CardType.PLANESWALKER}), loyalty=4), state.players[1])
    state.players[1].add_card(walker, ZoneType.BATTLEFIELD)
    request = DeclareAttackersRequest(state, state.players[0])
    context = CombatDeclarationEquivalence(request, blockers=False)
    representatives = tuple(context.representatives())
    assert len(context.targets) == 2 and len(representatives) == 6
    native = tuple(request.options)
    counts = Counter(context.action_class(o.declarations).key for o in native)
    assert counts == {r.key: r.multiplicity for r in representatives}
    assert sum(counts.values()) == 9


def test_no_legal_blockers_produces_one_complete_empty_declaration():
    request = make_request('blockers')
    for card in tuple(request.state.combat.potential_blockers(request.player)):
        card.is_tapped = True
    model = DeterministicCandidates(native_ordering=True, combat_equivalence=True)
    frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
    try:
        options, complete = frontier.take(4)
        assert complete and len(options) == 1 and options[0].declarations == {}
    finally:
        frontier.close()


@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_small_prefix_does_not_enter_beam_or_concrete_exhaustive_tail(case):
    request = make_request(case)
    model = DeterministicCandidates(native_ordering=True, combat_equivalence=True)
    exhaustive = FullAttackersStrategy if case == 'attackers' else FullBlockersStrategy
    with patch.object(BeamCombatStrategy, 'generate', side_effect=AssertionError('Beam entered')), \
         patch.object(exhaustive, 'generate', side_effect=AssertionError('Concrete tail entered')):
        frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
        try:
            options, complete = frontier.take(4)
            assert len(options) == 4 and not complete
        finally:
            frontier.close()


@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_eventual_stream_keeps_every_concrete_identity_and_prefix_is_monotone(case):
    request = make_request(case)
    model = DeterministicCandidates(native_ordering=True, combat_equivalence=True)
    frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
    try:
        previous = ()
        for width in (4, 6, 12, 32, 2048):
            options, complete = frontier.take(width)
            descriptors = tuple(map(ChoiceDescriptor.from_option, options))
            assert descriptors[:len(previous)] == previous
            previous = descriptors
        assert complete
        assert set(previous) == set(map(ChoiceDescriptor.from_option, request.options))
        assert len(previous) == len(set(previous))
    finally:
        frontier.close()


@pytest.mark.parametrize('change', ['damage', 'counter', 'controller', 'freshness', 'revision', 'token'])
def test_different_runtime_roles_are_not_interchangeable(change):
    request = make_request('attackers')
    cards = list(request.state.combat.legal_attackers(request.player))[1:3]
    a, b = cards
    roles = CombatRoles(request.state)
    assert roles.signature(a) == roles.signature(b)
    if change == 'damage':
        b.state.damage_marked = 1
    elif change == 'counter':
        b.state.counters['+1/+1'] = 1
    elif change == 'controller':
        b.set_controller(request.state.players[1])
    elif change == 'freshness':
        b.controlled_since = 4
    elif change == 'revision':
        b.zone_revision += 1
    else:
        b.is_token = True
    roles = CombatRoles(request.state)
    assert roles.signature(a) != roles.signature(b)


def test_opaque_trigger_is_not_accepted_merely_because_the_name_matches():
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    request = make_request('attackers')
    card = request.state.combat.legal_attackers(request.player)[0]
    card._definition = replace(card.definition, triggers=frozenset({TriggerAbilityDefinition(key='opaque')}))
    assert CombatRoles(request.state).signature(card) is None


def test_attachment_and_face_down_roles_stay_singleton():
    request = make_request('attackers')
    angel = next(c for c in request.state.players[1].get_cards() if c.name == 'Serra Angel')
    assert CombatRoles(request.state).signature(angel) is None
    card = request.state.combat.legal_attackers(request.player)[0]
    card.state.face_down = True
    with patch.object(card, 'get_power', side_effect=AssertionError('Hidden characteristics')):
        assert CombatRoles(request.state).signature(card) is None


@pytest.mark.parametrize('hidden_name', ['Island', 'Shock', 'Counterspell'])
def test_hidden_hands_do_not_change_public_role_classes_or_first_candidates(hidden_name):
    from game.cards.catalog import game_catalog
    request = make_request('attackers')
    player = request.state.players[1]
    model = DeterministicCandidates(native_ordering=True, combat_equivalence=True)
    def prefix():
        frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
        try:
            return tuple(map(ChoiceDescriptor.from_option, frontier.take(4)[0]))
        finally:
            frontier.close()
    before = prefix()
    hidden = Card(game_catalog()[hidden_name], player)
    player.add_card(hidden, ZoneType.HAND)
    with patch.object(hidden, 'get_power', side_effect=AssertionError('Read hidden card')), \
         patch.object(hidden, 'get_ability_defs', side_effect=AssertionError('Read hidden rules')):
        assert prefix() == before


def test_block_orbits_agree_with_actual_native_damage_and_state_based_actions():
    from test_combat import game, creature, declare, damage
    outcomes = {}
    for choices in product((None, 0, 1), repeat=2):
        runtime = game.__wrapped__()
        state = runtime[0]
        attackers = [creature(runtime, power=2, toughness=2) for _ in range(2)]
        blockers = [creature(runtime, 1, 2, 2) for _ in range(2)]
        state.combat.declare_attackers(state.players[0], attackers)
        from game.enums import TurnPhase
        state.turn.phase = TurnPhase.DECLARE_BLOCKERS
        mapping = {b: attackers[i] for b, i in zip(blockers, choices) if i is not None}
        context = CombatDeclarationEquivalence(DeclareBlockersRequest(state, state.players[1]), blockers=True)
        key = context.action_class(mapping).key
        state.combat.declare_blockers(state.players[1], mapping)
        damage(runtime)
        result = (tuple(p.health for p in state.players), tuple(sorted(
            (c.owner.idx, c.get_zone().name, c.state.damage_marked) for c in (*attackers, *blockers))))
        if key in outcomes:
            assert outcomes[key] == result
        outcomes[key] = result
    assert len(outcomes) == 4


def test_config_roundtrip_and_candidate_identity_distinguish_role_proposals():
    settings = SearchAgentConfig(deck_knowledge='both_lists', progressive_candidates=True,
        candidate_native_ordering=True, candidate_combat_equivalence=True, widening_constant=4)
    assert SearchAgentConfig.parse(json.loads(json.dumps(asdict(settings)))) == settings
    model = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
        candidate_combat_equivalence=True).candidate_model
    assert model.combat_equivalence
    assert model.identity != DeterministicCandidates(native_ordering=True).identity
    for values in ({'candidate_combat_equivalence': True}, {'candidate_combat_equivalence': 'yes'}):
        with pytest.raises(ValueError):
            SearchAgentConfig(deck_knowledge='both_lists', **values)


@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_root_reservoir_stops_at_role_boundary_and_replays_widening(case):
    from game.ai.search_simulator import SearchSimulator
    policy = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
        candidate_combat_equivalence=True)
    sim = SearchSimulator(lambda controllers: make_case(case, controllers)[0], policy_for=policy,
        track_information=True, structured_records=True, projection_cache_size=128)
    node = sim.start()
    assert len(node.choices) == 4
    prepared = sim._candidate_nodes[node.history]
    assert len(prepared.choices) == (16 if case == 'attackers' else 12)
    for width in (6, 12):
        expanded = sim.expand_candidates(node, width)
        assert expanded.player_information().candidate_width == width
        assert sim.fork(expanded) == expanded


def test_reservoir_without_lookahead_does_not_enter_a_more_expensive_tail():
    def stream():
        yield from range(12)
        raise AssertionError('Entered concrete permutations')
    frontier = CandidateFrontier(stream(), 4096)
    values, complete = frontier.take(12, lookahead=False)
    assert len(values) == 12 and not complete


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
@pytest.mark.parametrize('case', ['attackers', 'blockers'])
def test_counted_prefixes_complete_real_search_without_fallback(selection, case):
    from game.ai.search_simulator import SearchSimulator
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    policy = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
        candidate_combat_equivalence=True)
    def factory(seed):
        return SearchSimulator(lambda controllers: make_case(case, controllers)[0], policy_for=policy,
            track_information=True, structured_records=True, projection_cache_size=128)
    root = factory(0).start().player_information()
    chooser = ObservationOpponentPolicy()
    search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser, selection=selection,
        action_prior=HeuristicActionPrior(chooser) if selection != 'uct' else None,
        compress_forced=True, require_root_coverage=False, force_initial_coverage=False,
        widening_constant=4, budget=InformationSearchBudget(worlds=1, iterations=8,
            max_depth=4, max_extensions=8, max_advances=4096))
    result = search.search(factory, root, seed=42)
    assert not result.used_fallback and result.metrics.iterations_completed == 8
