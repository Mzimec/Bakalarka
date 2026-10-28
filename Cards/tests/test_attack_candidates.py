"""Visible risk labels, lazy combat drafts and native legality/completeness."""
from unittest.mock import patch

import pytest

from game.ai.attack_candidates import AttackRisk, AttackRiskAssessment, RiskAttackersStrategy
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates
from game.ai.search_descriptors import ChoiceDescriptor
from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest
from game.game_actions.generation.decision_abstraction.policies import DeclareAttackersPolicy
from game.game_state import Card, CardDefinition, Player, State
from game.enums import CardType, TurnPhase, ZoneType
from runtime_write_guard import deny_writes


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


def position():
    state = State([Player([], None, idx=0), Player([], None, idx=1)])
    state.turn.number = 5
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    state.combat.begin()
    return state


def creature(state, player, power, toughness, keywords=()):
    owner = state.players[player]
    card = Card(CardDefinition('Unit', types=frozenset({CardType.CREATURE}),
        power=power, toughness=toughness, keywords=frozenset(keywords)), owner)
    owner.add_card(card, ZoneType.BATTLEFIELD)
    card.controlled_since = 0
    return card


@pytest.mark.parametrize('attacker,blockers,expected', [
    ((3, 3, ()), [], AttackRisk.UNBLOCKED),
    ((4, 5, ()), [(2, 2, ()), (2, 2, ())], AttackRisk.SAFE),
    ((4, 4, ()), [(2, 2, ()), (2, 2, ())], AttackRisk.MULTIBLOCK),
    ((2, 2, ()), [(2, 2, ())], AttackRisk.TRADE),
    ((2, 2, ()), [(3, 3, ())], AttackRisk.UNFAVOURABLE),
    ((2, 2, ('first strike',)), [(2, 2, ())], AttackRisk.SAFE),
    ((2, 2, ()), [(2, 2, ('first strike',))], AttackRisk.UNFAVOURABLE),
    ((2, 3, ('double strike',)), [(3, 3, ())], AttackRisk.TRADE),
    ((2, 4, ()), [(2, 2, ('double strike',))], AttackRisk.TRADE),
    ((2, 5, ()), [(2, 2, ('double strike',))], AttackRisk.SAFE),
    ((2, 2, ()), [(1, 3, ('deathtouch',))], AttackRisk.UNFAVOURABLE),
    ((2, 2, ('deathtouch',)), [(3, 3, ())], AttackRisk.TRADE),
    ((2, 2, ('indestructible',)), [(20, 20, ('deathtouch',))], AttackRisk.SAFE),
    ((2, 2, ()), [(2, 2, ('indestructible',))], AttackRisk.UNFAVOURABLE),
    ((2, 2, ('flying',)), [(9, 9, ())], AttackRisk.UNBLOCKED),
    ((2, 2, ('flying',)), [(9, 9, ('reach',))], AttackRisk.UNFAVOURABLE),
    ((2, 2, ('menace',)), [(9, 9, ())], AttackRisk.UNBLOCKED),
    ((3, 3, ('menace',)), [(2, 2, ()), (2, 2, ())], AttackRisk.MULTIBLOCK),
    ((2, 2, ()), [(1, 1, ('infect',))], AttackRisk.UNKNOWN),
])
def test_static_damage_and_block_legality_labels(attacker, blockers, expected):
    state = position()
    a = creature(state, 0, *attacker)
    bs = tuple(creature(state, 1, *b) for b in blockers)
    assessment = AttackRiskAssessment(state)
    assert assessment.classify(a, state.players[1], bs) == expected
    assert assessment.pair_checks == len(bs)


def test_marked_damage_and_worst_available_single_block_are_not_ignored():
    state = position()
    a = creature(state, 0, 2, 4)
    a.state.damage_marked = 2
    trade = creature(state, 1, 2, 2)
    eats = creature(state, 1, 2, 3)
    assert AttackRiskAssessment(state).classify(a, state.players[1], (trade, eats)) == AttackRisk.UNFAVOURABLE


def test_face_down_facts_are_not_read_to_rank_a_hidden_definition():
    state = position()
    a = creature(state, 0, 2, 2)
    b = creature(state, 1, 8, 8)
    b.state.face_down = True
    with patch.object(b, 'get_power', side_effect=AssertionError('Hidden power')), \
         patch.object(b, 'get_toughness', side_effect=AssertionError('Hidden toughness')):
        assert AttackRiskAssessment(state).classify(a, state.players[1], (b,)) == AttackRisk.UNKNOWN


def test_group_drafts_are_early_and_all_in_includes_zero_power_attackers():
    state = position()
    safe = creature(state, 0, 5, 8)
    multi = creature(state, 0, 4, 4)
    trade = creature(state, 0, 2, 2)
    eaten = creature(state, 0, 1, 1)
    zero = creature(state, 0, 0, 8)
    for _ in range(2):
        creature(state, 1, 2, 2)
    request = DeclareAttackersRequest(state, state.players[0])
    options = list(RiskAttackersStrategy(8).generate(request))
    groups = [set(o.declarations) for o in options]
    assert groups[:5] == [
        {safe, multi, trade, eaten, zero}, {safe, zero}, set(),
        {safe, zero, multi}, {safe, zero, multi, trade}]


def test_first_prefix_never_starts_beam_or_exhaustive_enumeration():
    from game.ai.generation_candidates import BeamCombatStrategy
    state = position()
    for power in range(1, 11):
        creature(state, 0, power, 4)
    creature(state, 1, 2, 2)
    request = DeclareAttackersRequest(state, state.players[0])
    model = DeterministicCandidates(native_ordering=True, attack_risk=True)
    with patch.object(BeamCombatStrategy, 'generate', side_effect=AssertionError('Enumerated fallback')):
        frontier = CandidateFrontier(model.generate(request, DeclareAttackersPolicy()), 4096)
        try:
            prefix, complete = frontier.take(4)
            assert len(prefix) == 4 and not complete
        finally:
            frontier.close()


def test_identical_public_matchups_share_only_the_static_risk_computation():
    state = position()
    attackers = tuple(creature(state, 0, 3, 5) for _ in range(6))
    blockers = tuple(creature(state, 1, 2, 2) for _ in range(7))
    assessment = AttackRiskAssessment(state)
    for a in attackers:
        assessment.classify(a, state.players[1], blockers)
    assert assessment.pair_checks == 7
    assert len(assessment.facts) == 6 + 7
    assert len(assessment.risks) == 1


def test_custom_combat_legality_does_not_share_attacker_evasion_profiles():
    from game.rules.combat import CombatState
    class IdentityCombat(CombatState):
        def blocker_error(self, blocker, attacker, player, *, check_assignment=True):
            return 'Unblockable identity' if attacker is self.special else super().blocker_error(
                blocker, attacker, player, check_assignment=check_assignment)
    state = position()
    state.combat = IdentityCombat(state)
    a, other = (creature(state, 0, 2, 2) for _ in range(2))
    b = creature(state, 1, 9, 9)
    state.combat.special = a
    assessment = AttackRiskAssessment(state)
    assert assessment.classify(a, state.players[1], (b,)) == AttackRisk.UNBLOCKED
    assert assessment.classify(other, state.players[1], (b,)) == AttackRisk.UNFAVOURABLE
    assert assessment.pair_checks == 2


def test_early_drafts_ignore_opponent_hand_and_eventual_menu_keeps_all_identities():
    state = position()
    for _ in range(3):
        creature(state, 0, 2, 2)
    request = DeclareAttackersRequest(state, state.players[0])
    policy = DeclareAttackersPolicy()
    model = DeterministicCandidates(native_ordering=True, attack_risk=True)
    expected = set(map(ChoiceDescriptor.from_option, request.option_space(policy)))
    before = tuple(map(ChoiceDescriptor.from_option, model.generate(request, policy)))
    hidden = Card(CardDefinition('Hidden', types=frozenset({CardType.INSTANT})), state.players[1])
    hidden.owner.add_card(hidden, ZoneType.HAND)
    after = tuple(map(ChoiceDescriptor.from_option, model.generate(request, policy)))
    assert before == after
    assert len(before) == len(set(before)) == 8
    assert set(before) == expected


def test_evasion_and_other_defenders_remain_in_the_complete_legal_menu():
    state = position()
    creature(state, 0, 2, 2, ('flying',))
    creature(state, 0, 3, 3)
    creature(state, 1, 5, 5, ('reach',))
    owner = state.players[1]
    walker = Card(CardDefinition('Walker', types=frozenset({CardType.PLANESWALKER}), loyalty=4), owner)
    owner.add_card(walker, ZoneType.BATTLEFIELD)
    request = DeclareAttackersRequest(state, state.players[0])
    policy = DeclareAttackersPolicy()
    expected = set(map(ChoiceDescriptor.from_option, request.option_space(policy)))
    actual = tuple(DeterministicCandidates(native_ordering=True, attack_risk=True).generate(request, policy))
    assert set(map(ChoiceDescriptor.from_option, actual)) == expected
    assert any(walker in option.declarations.values() for option in actual)


def test_risk_config_round_trips_and_isolates_its_candidate_identity():
    from dataclasses import asdict
    import json
    from game.simulation.search_config import SearchAgentConfig, SearchGenerationPolicy
    settings = SearchAgentConfig(deck_knowledge='both_lists', progressive_candidates=True,
        candidate_native_ordering=True, candidate_attack_risk=True, widening_constant=4)
    assert SearchAgentConfig.parse(json.loads(json.dumps(asdict(settings)))) == settings
    model = SearchGenerationPolicy(progressive_candidates=True,
        candidate_native_ordering=True, candidate_attack_risk=True).candidate_model
    assert model.attack_risk
    assert model.identity != DeterministicCandidates(native_ordering=True).identity
    for values in ({'candidate_attack_risk': True}, {'candidate_attack_risk': 'yes'}):
        with pytest.raises(ValueError):
            SearchAgentConfig(deck_knowledge='both_lists', **values)
    with pytest.raises(ValueError):
        DeterministicCandidates(attack_risk=True)


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
def test_risk_drafts_complete_real_native_search_and_preserve_terminal_wins(selection):
    from benchmarks.mcts_top_k import make_case
    from game.ai.search_simulator import SearchSimulator
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.simulation.search_config import SearchGenerationPolicy
    policy = SearchGenerationPolicy(progressive_candidates=True,
        candidate_native_ordering=True, candidate_attack_risk=True)
    def factory(seed):
        return SearchSimulator(lambda controllers: make_case('attackers', controllers)[0],
            policy_for=policy, track_information=True, structured_records=True, projection_cache_size=128)
    root = factory(0).start().player_information()
    chooser = ObservationOpponentPolicy()
    search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
        selection=selection, action_prior=HeuristicActionPrior(chooser) if selection != 'uct' else None,
        compress_forced=True,
        require_root_coverage=False, force_initial_coverage=False, widening_constant=4,
        budget=InformationSearchBudget(worlds=1, iterations=8, max_depth=4,
                                      max_extensions=8, max_advances=4096))
    result = search.search(factory, root, seed=42)
    assert not result.used_fallback
    assert result.metrics.iterations_completed == 8
    assert result.metrics.terminal_leaves > 0
    assert result.value == 1
