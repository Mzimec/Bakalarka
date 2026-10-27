"""Native structured/forced/frontier paths against the strict real engine."""
from dataclasses import asdict, replace
import json
from unittest.mock import patch

import pytest

from benchmarks.search_tactics import tactical_scenario
from game.ai.search_simulator import SearchSimulator, ForcedBoundary
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.structured_values import StructuredJSON, ValueStore, RecallDigest, text
from game.ai.search_recall import PlayerRecall
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.simulation.search_config import SearchGenerationPolicy


def test_structured_payloads_share_subtrees_are_immutable_and_export_legacy_json():
    store = ValueStore()
    first = StructuredJSON({'cards': [{'id': 1, 'power': 2}], 'turn': 1}, store)
    second = StructuredJSON({'cards': [{'id': 1, 'power': 2}], 'turn': 2}, store)
    assert dict(first.value.items)['cards'] is dict(second.value.items)['cards']
    with pytest.raises(AttributeError):
        first.value = None
    for value in (object(), {'card': object()}, float('nan')):
        with pytest.raises((TypeError, ValueError)):
            StructuredJSON(value)
    assert json.loads(text(first)) == {'cards': [{'id': 1, 'power': 2}], 'turn': 1}


def test_append_and_fast_information_keys_need_no_json_serialization():
    from game.ai.search_actions import PlayerAction
    from game.ai.search_observation import ActingPlayerObservation
    from game.ai.search_recall import PlayerInformation
    with patch('json.dumps', side_effect=AssertionError('Internal structured path serialized JSON')):
        current = ActingPlayerObservation(0, 'test', StructuredJSON({'self': 0}))
        action = PlayerAction('test', StructuredJSON({'n': 2}))
        recall = PlayerRecall().append('observation', {'view': current._json}).append('action', action.structured)
        information = PlayerInformation(current, recall, (action,))
        assert information.fast_key and hash(action)
        assert action.describe() == {'n': 2}
    assert type(asdict(information)['current']['_json']) is str
    assert isinstance(recall.digest, RecallDigest) and len(recall.digest.text) == 64


def test_binary_row_cache_only_reuses_validated_detached_content():
    store = ValueStore()
    row = dict(id=1, power=2, toughness=3, tapped=False, name='a', counters=[])
    first = store.freeze(row)
    assert store.freeze(dict(row)) is first and store.binary_hits == 1
    row['power'] = 4
    assert store.freeze(row) != first and dict(first.items)['power'].items == 2
    for invalid in (object(), b'a', 1j):
        row['power'] = invalid
        with pytest.raises(TypeError):
            store.freeze(row)


def test_structural_tie_break_matches_canonical_json_order():
    values = [None, True, False, 0, -1, 10, 2, 0.0, -0.0, 1e-7, 1e20,
              '', 'a', 'aa', 'a\"', '\\n', '\u017e', [], [1], [1, 2], [10], {},
              {'a': 1}, {'a': 10}, {'a': [1]}, {'aa': 1}, {'a': 1, 'b': None}]
    store = ValueStore()
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'))
    assert [canonical(v) for v in sorted(values, key=lambda v: store.freeze(v).order)] == sorted(map(canonical, values))
    from game.ai.structured_values import structural_key
    assert structural_key(values) == ValueStore(0).freeze((values,)).digest.hex()


@pytest.mark.parametrize('case', ['lethal_shock', 'answered_shock', 'useful_pacifism'])
def test_native_forced_chain_matches_reference_every_observation_recall_and_action(case):
    policy = SearchGenerationPolicy(True, True)
    reference = SearchSimulator(tactical_scenario(case), policy_for=policy, track_information=True)
    native = SearchSimulator(tactical_scenario(case), policy_for=policy, track_information=True, structured_records=True)
    a, b = reference.start(), native.start()
    chooser = ObservationOpponentPolicy()
    seen = []
    def select(current):
        nonlocal a
        assert current.player_information() == a.player_information()
        for player in (0, 1):
            assert current.observation(player) == a.observation(player)
        action = chooser(current.player_information())
        seen.append(type(current).__name__)
        a = reference.advance_action(a, action)
        return action if len(seen) < 8 else None
    # Stop without advancing the reference for the final callback.
    def bounded(current):
        if len(seen) >= 7:
            assert current.player_information() == a.player_information()
            return None
        return select(current)
    end = native.follow_actions(bounded, node=b, coalesce_forced=True)
    if end.terminal:
        assert a.terminal and end.observation(0) == a.observation(0)
    else:
        assert end.player_information() == a.player_information()
    assert native.fork(end) == end


def test_progressive_frontier_extends_without_removing_candidates_and_replays():
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def scenario(controllers):
        game = tactical_scenario('useful_pacifism')(controllers)
        own, other = game.state.players
        for _ in range(8):
            other.add_card(Card(starter_catalog()['Impassioned Orator'], other), ZoneType.BATTLEFIELD)
        return game
    sim = SearchSimulator(scenario, policy_for=SearchGenerationPolicy(False, False, True, 2),
                          track_information=True, structured_records=True)
    root = sim.start()
    info = root.player_information()
    assert len(info.actions) == 2 and not info.menu_complete
    grown = sim.expand_candidates(root, 6)
    more = grown.player_information()
    assert info.fast_key == more.fast_key and set(info.actions) < set(more.actions)
    assert info.current == more.current and info.recall == more.recall
    assert sim.fork(grown) == grown
    novel = next(a for a in more.actions if a not in info.actions)
    child = sim.advance_action(grown, novel)
    assert sim.fork(child) == child


@pytest.mark.parametrize('selection', ['uct', 'puct', 'hierarchical_puct'])
def test_search_admits_new_candidates_as_visits_grow(selection):
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    def scenario(controllers):
        game = tactical_scenario('useful_pacifism')(controllers)
        for _ in range(5):
            player = game.state.players[1]
            player.add_card(Card(starter_catalog()['Impassioned Orator'], player), ZoneType.BATTLEFIELD)
        return game
    policy = SearchGenerationPolicy(False, False, True, 2)
    def factory(seed):
        return SearchSimulator(scenario, policy_for=policy, track_information=True, structured_records=True)
    root = factory(0).start().player_information()
    result = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(), selection=selection,
        compress_forced=True, require_root_coverage=False, widening_constant=2,
        budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=1, max_advances=256)
    ).search(factory, root, seed=42)
    assert not result.used_fallback
    assert len(result.estimates) > len(root.actions)


def test_explicit_model_invalidation_clears_old_statistics_even_for_same_root():
    from test_information_set_search import Tree, first
    factory = lambda seed: Tree({'root': {'children': {'a': 'win'}}, 'win': {'winner': 0}})
    search = InformationSetSearch(opponent_policy=first, tree_reuse_size=4,
        budget=InformationSearchBudget(worlds=1, iterations=3, max_depth=1))
    token = object()
    root = factory(0).start().player_information()
    search.search(factory, root, reuse_context=token)
    assert search.tree_memory.rows
    search.invalidate_model('new-weights')
    assert not search.tree_memory.rows
    result = search.search(factory, root, reuse_context=token)
    assert result.metrics.reused_root_visits == 0


def test_candidate_ranking_handles_player_targets_without_card_reads():
    sim = SearchSimulator(tactical_scenario('lethal_shock'),
        policy_for=SearchGenerationPolicy(False, False, True, 2), track_information=True, structured_records=True)
    root = sim.start()
    assert root.player_information().actions


@pytest.mark.parametrize('rolling', [False, True])
def test_tree_reroot_requires_compatible_remaining_horizon_unless_explicitly_rolling(rolling):
    from game.ai.search_tree_memory import SearchTreeMemory, TreeRow
    from test_information_set_search import Tree
    tree = Tree({'root': {'children': {'go': 'root'}}})
    info = tree.start().player_information()
    memory, token = SearchTreeMemory(), object()
    row = TreeRow(info, ((info.actions[0], 5, 2.0),), (), (1, 2))
    memory.save(token, 'model-v1', (row,), 4)
    loaded = memory.load(token, 'model-v1', info, horizon=(4, 2), rolling=rolling)
    assert bool(loaded) == rolling


def test_mode_continuations_resolve_one_or_at_a_time_and_preserve_native_options():
    from game.game_actions.data_structs.action_node import AndActionNode, OrActionNode, EffectActionNode, ImmutableEffectToSlotMap
    from game.game_actions.generation.decision_abstraction.mode_frames import resolve_modes
    a = EffectActionNode(ImmutableEffectToSlotMap({'a': frozenset()}))
    b = EffectActionNode(ImmutableEffectToSlotMap({'b': frozenset()}))
    graph = AndActionNode((OrActionNode((a, b)), OrActionNode((a, b))))
    def expand(prefix=()):
        narrowed, pending = resolve_modes(graph, prefix)
        if pending is None:
            return set(narrowed.generate_options())
        path, children = pending
        result = set()
        for i in range(len(children)):
            result.update(expand((*prefix, (path, i))))
        return result
    assert expand() == set(graph.generate_options())


def test_native_until_choice_stops_on_each_logical_work_boundary_and_can_fork():
    sim = SearchSimulator(tactical_scenario('lethal_shock'), policy_for=SearchGenerationPolicy(),
                          track_information=True, structured_records=True)
    root = sim.start()
    passed = next(a for a in root.player_information().actions if a.kind == 'PassPriorityAction')
    seen = []
    end = sim.advance_until_choice(root, passed, on_forced=lambda current: seen.append(current.decision.kind) or False)
    assert seen == ['PriorityDecisionRequest'] and sim.native_forced_steps == 1
    assert len(end.history) == len(root.history) + 1
    assert sim.fork(end) == end


def test_frontier_is_lazy_and_closes_its_native_generation_context():
    from game.ai.generation_candidates import CandidateFrontier
    seen, closed = [], []
    def values():
        try:
            for i in range(10000):
                seen.append(i)
                yield i
        finally:
            closed.append(True)
    frontier = CandidateFrontier(values(), 4096)
    assert frontier.take(4) == ((0, 1, 2, 3), False)
    assert seen == [0, 1, 2, 3, 4]  # One bounded completeness probe, not 10 000 actions.
    assert frontier.take(8)[0] == tuple(range(8))
    frontier.close()
    assert closed == [True]


def test_combat_beam_eventually_has_exactly_the_full_legal_menu_without_duplicates():
    from game.ai.modular_agent import ModularAgent
    from game.ai.generation_candidates import DeterministicCandidates
    from game.game_actions.generation.decision_abstraction.requests import DeclareAttackersRequest
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    from game.ai.search_descriptors import ChoiceDescriptor
    game = tactical_scenario('attack_pacified')((ModularAgent(), ModularAgent()))
    player = game.state.players[0]
    for _ in range(3):
        card = Card(starter_catalog()['Impassioned Orator'], player)
        player.add_card(card, ZoneType.BATTLEFIELD)
        card.controlled_since = 0
    request = DeclareAttackersRequest(game.state, player)
    full = set(map(ChoiceDescriptor.from_option, request.options))
    proposals = list(map(ChoiceDescriptor.from_option, DeterministicCandidates(2, 4).generate(request, None)))
    assert len(proposals) == len(set(proposals)) and set(proposals) == full


def test_blocker_frontier_preserves_exhaustive_native_assignments():
    from game.ai.modular_agent import ModularAgent
    from game.ai.generation_candidates import DeterministicCandidates
    from game.game_actions.generation.decision_abstraction.requests import DeclareBlockersRequest
    from game.game_state import Card
    from game.cards.starter_cards import starter_catalog
    from game.enums import ZoneType
    from game.ai.search_descriptors import ChoiceDescriptor
    game = tactical_scenario('attack_pacified')((ModularAgent(), ModularAgent()))
    state = game.state
    groups = [[], []]
    for owner, count in ((0, 2), (1, 3)):
        for _ in range(count):
            card = Card(starter_catalog()['Impassioned Orator'], state.players[owner])
            card.owner.add_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
            groups[owner].append(card)
    state.combat.declare_attackers(state.players[0], {c: state.players[1] for c in groups[0]})
    from game.enums import TurnPhase
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    request = DeclareBlockersRequest(state, state.players[1])
    full = set(map(ChoiceDescriptor.from_option, request.options))
    proposals = list(map(ChoiceDescriptor.from_option, DeterministicCandidates(2, 4).generate(request, None)))
    assert len(proposals) == len(set(proposals)) and set(proposals) == full


@pytest.mark.parametrize('depth,advances', [(1, 3), (1, 64), (4, 64)])
def test_native_and_ordinary_drivers_share_horizon_and_work_limits(depth, advances):
    def factory(native):
        return lambda seed: SearchSimulator(tactical_scenario('answered_shock'),
            policy_for=SearchGenerationPolicy(), track_information=True,
            structured_records=True, native_forced=native)
    results = []
    for native in (False, True):
        worlds = factory(native)
        root = worlds(0).start().player_information()
        results.append(InformationSetSearch(opponent_policy=ObservationOpponentPolicy(),
            compress_forced=True, require_root_coverage=False,
            budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=depth,
                max_advances=advances, max_extensions=2)).search(worlds, root, seed=42))
    a, b = results
    assert (a.choice, a.value, a.estimates, a.stop_reason, a.used_fallback) == (
            b.choice, b.value, b.estimates, b.stop_reason, b.used_fallback)
    for counter in ('iterations_completed', 'advance_attempts', 'forced_transitions', 'interruption_reasons'):
        assert getattr(a.metrics, counter) == getattr(b.metrics, counter)
