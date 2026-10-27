"""Bounded proposal ordering, legality, information isolation and real widening."""
from dataclasses import asdict
from itertools import islice
import json
from unittest.mock import patch

import pytest

from benchmarks.search_tactics import tactical_scenario
from game.ai.candidate_ranking import ranked_batches, normalize_rank_windows
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import SearchSimulator
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.simulation.search_config import SearchAgentConfig, SearchGenerationPolicy
from runtime_write_guard import deny_writes


@pytest.fixture(autouse=True)
def no_runtime_files():
    with deny_writes():
        yield


def ranked_policy(**overrides):
    return SearchGenerationPolicy(progressive_candidates=True, candidate_initial_width=2,
        candidate_rank_window=8, **overrides)


def crowded(controllers):
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    game = tactical_scenario('useful_pacifism')(controllers)
    player = game.state.players[1]
    for _ in range(8):
        player.add_card(Card(starter_catalog()['Impassioned Orator'], player), ZoneType.BATTLEFIELD)
    return game


def test_top_k_reads_one_bounded_batch_and_retains_the_unselected_tail():
    seen, closed = [], []
    def stream():
        try:
            for i in range(10000):
                seen.append(i)
                yield i
        finally:
            closed.append(True)
    frontier = CandidateFrontier(ranked_batches(stream(), 8, lambda batch: sorted(batch, reverse=True)), 10000)
    assert frontier.take(3) == ((7, 6, 5), False)
    assert seen == list(range(8))
    assert frontier.take(10)[0] == (7, 6, 5, 4, 3, 2, 1, 0, 15, 14)
    assert seen == list(range(16))
    frontier.close()
    assert closed == [True]
    assert list(ranked_batches(range(11), 4, lambda batch: sorted(batch, reverse=True))) == [
        3, 2, 1, 0, 7, 6, 5, 4, 10, 9, 8]


@pytest.mark.parametrize('width', [2, 5, 8, 9, 17])
def test_larger_frontier_replays_the_exact_same_ordered_prefix(width):
    def generate():
        return ranked_batches(range(100), 8, lambda batch: sorted(batch, reverse=True))
    small, large = CandidateFrontier(generate(), 100), CandidateFrontier(generate(), 100)
    try:
        prefix = small.take(width)[0]
        assert large.take(width + 3)[0][:width] == prefix
    finally:
        small.close()
        large.close()


def test_disabled_ranking_is_lazy_and_does_not_call_the_scorer():
    def unavailable(batch):
        raise AssertionError('Disabled ranking evaluated a batch')
    stream = ranked_batches(range(10000), 0, unavailable)
    assert tuple(islice(stream, 3)) == (0, 1, 2)
    stream.close()


@pytest.mark.parametrize('setting', [True, -1, 1, 2.5, None, '8'])
def test_bad_rank_windows_rejected(setting):
    with pytest.raises(ValueError):
        SearchAgentConfig(deck_knowledge='both_lists', progressive_candidates=True,
            widening_constant=2, candidate_rank_window=setting)


@pytest.mark.parametrize('setting', [{'typo': 8}, {'blockers': True}, 'blockers',
    [('priority', 4), ('priority', 8)], [['ability', 1]], [None]])
def test_bad_family_overrides_rejected(setting):
    with pytest.raises(ValueError):
        normalize_rank_windows(setting)


def test_config_owns_profiles_round_trips_and_wires_the_controller():
    overrides = {'blockers': 16, 'priority': 4, 'other': 0}
    config = SearchAgentConfig(deck_knowledge='both_lists', progressive_candidates=True,
        widening_constant=4, candidate_rank_window=8, candidate_rank_windows=overrides)
    overrides['blockers'] = 999
    assert dict(config.candidate_rank_windows)['blockers'] == 16
    assert SearchAgentConfig.parse(json.loads(json.dumps(asdict(config)))) == config
    from game.ai.game_origin import GameOrigin
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.search_config import make_search_controller
    origin = GameOrigin((load_arena_starter('red'), load_arena_starter('white')), game_catalog)
    agent = make_search_controller(asdict(config), origin, max_decisions=10)
    model = agent.policy_for.candidate_model
    assert model.rank_window == 8 and model.rank_windows == config.candidate_rank_windows
    assert model.identity != DeterministicCandidates().identity
    with pytest.raises(ValueError, match='requires progressive_candidates'):
        SearchAgentConfig(deck_knowledge='both_lists', candidate_rank_window=8)


def test_family_override_can_disable_ranking_for_priority():
    policy = ranked_policy(candidate_rank_windows={'priority': 0})
    with patch('game.ai.candidate_ranking.VisibleCandidateRanker.__call__', side_effect=AssertionError('Scored priority')):
        node = SearchSimulator(crowded, policy_for=policy, track_information=True).start()
    assert len(node.player_information().actions) == 2


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
def test_ranked_real_frontier_grows_searches_and_replays(selection):
    policy = ranked_policy()
    factory = lambda seed: SearchSimulator(crowded, policy_for=policy,
        track_information=True, structured_records=True)
    sim = factory(0)
    root = sim.start()
    grown = sim.expand_candidates(root, 6)
    before, after = root.player_information(), grown.player_information()
    assert before.fast_key == after.fast_key and set(before.actions) < set(after.actions)
    assert before.current == after.current and before.recall == after.recall
    assert sim.fork(grown) == grown
    chosen = next(a for a in after.actions if a not in before.actions)
    child = sim.advance_action(grown, chosen)
    assert sim.fork(child) == child
    result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
        compress_forced=True, require_root_coverage=False, widening_constant=2,
        budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=1, max_advances=256)
    ).search(factory, before, seed=42)
    assert not result.used_fallback and len(result.estimates) > len(before.actions)
    assert any(row.visits and row.action not in before.actions for row in result.estimates)


@pytest.mark.parametrize('blockers', [False, True])
def test_ranked_combat_eventually_equals_the_full_native_menu(blockers):
    from game.ai.modular_agent import ModularAgent
    from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest, DeclareBlockersRequest
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType, TurnPhase
    game = tactical_scenario('attack_pacified')((ModularAgent(), ModularAgent()))
    state, groups = game.state, [[], []]
    for owner, count in ((0, 2), (1, 3)):
        for _ in range(count):
            card = Card(starter_catalog()['Impassioned Orator'], state.players[owner])
            card.owner.add_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
            groups[owner].append(card)
    if blockers:
        state.combat.declare_attackers(state.players[0], {c: state.players[1] for c in groups[0]})
        state.turn.phase = TurnPhase.DECLARE_BLOCKERS
        request = DeclareBlockersRequest(state, state.players[1])
    else:
        request = DeclareAttackersRequest(state, state.players[0])
    full = set(map(ChoiceDescriptor.from_option, request.options))
    ranked = list(map(ChoiceDescriptor.from_option, DeterministicCandidates(2, 4, 8).generate(request, None)))
    assert len(ranked) == len(set(ranked)) and set(ranked) == full


def test_ranking_does_not_use_opponent_hidden_identities():
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def scenario(name):
        def build(controllers):
            game = crowded(controllers)
            other = game.state.players[1]
            other.add_card(Card(starter_catalog()[name], other), ZoneType.HAND)
            return game
        return build
    roots = [SearchSimulator(scenario(name), policy_for=ranked_policy(),
        track_information=True, structured_records=True).start().player_information()
        for name in ('Island', 'Mountain')]
    assert roots[0] == roots[1]


def test_ranked_top_two_include_pass_and_lethal_instead_of_the_first_creature_target():
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def build(controllers):
        game = tactical_scenario('lethal_shock')(controllers)
        other = game.state.players[1]
        other.add_card(Card(starter_catalog()['Impassioned Orator'], other), ZoneType.BATTLEFIELD)
        return game
    before = SearchSimulator(build, policy_for=SearchGenerationPolicy(
        progressive_candidates=True, candidate_initial_width=2), track_information=True).start().player_information()
    after = SearchSimulator(build, policy_for=ranked_policy(), track_information=True).start().player_information()
    chooser = ObservationOpponentPolicy()
    assert chooser.rank(after)[0].score > 900
    assert chooser.rank(before)[0].score < 900
    assert any(a.kind == 'PassPriorityAction' for a in after.actions)


def test_live_agent_regenerates_a_selected_ranked_candidate_outside_its_initial_top_k():
    from game.ai.mcts_agent import MCTSAgent
    from game.ai.modular_agent import ModularAgent
    from test_mcts_agent import request
    policy = ranked_policy()
    initial = []
    def provider(info):
        initial.extend(info.actions)
        return lambda seed: SearchSimulator(crowded, policy_for=policy,
            track_information=True, structured_records=True)
    def select(info, result):
        return next(r.action for r in result.estimates if r.action not in info.actions and r.visits)
    planner = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(),
        compress_forced=True, require_root_coverage=False, widening_constant=2,
        budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=1, max_advances=256))
    own = MCTSAgent(world_provider=provider, search=planner, policy_for=policy)
    own.search_choice = select
    game = crowded((own, ModularAgent()))
    hand = tuple(game.state.players[0].hand.values())
    result = own.decide(request(game))
    assert result.info['mcts']['reason'] == 'searched'
    assert own.last_information.actions and len(own.last_information.actions) > len(initial)
    assert result.value.source in hand and tuple(game.state.players[0].hand.values()) == hand
