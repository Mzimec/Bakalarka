"""Exact reductions of projection/hash work must preserve player facts and leaves."""
from unittest.mock import patch
from dataclasses import replace

import pytest

from game.ai.generation_candidates import CandidateFrontier
from game.ai.observation import PlayerObservationBuilder, observation
from game.ai.structured_values import StructuredJSON, ValueStore, structural_key, _scalar_digest
from game.enums import CounterType, ZoneType
from game.stat_type import STAT_POWER
from runtime_write_guard import deny_writes
from test_player_observation import board, add, find


@pytest.fixture(autouse=True)
def no_files():
    with deny_writes():
        yield


def test_native_numeric_rows_follow_modifiers_rollback_and_custom_readers():
    state = board()
    card = add(state, 'Numeric', power=2, toughness=3)
    original = card.stats
    def row():
        return find(observation(state, state.players[0], update_knowledge=False), card)
    assert row()['power'] == 2
    with patch.object(card, 'get_power', return_value=9):
        assert row()['power'] == 9
    assert row()['power'] == 2
    card.state.counters[CounterType.PLUS_ONE] = 2
    assert row()['power'] == 4 and row()['toughness'] == 5
    card.state.counters.clear()
    card.set_base_stat(STAT_POWER, 7)
    assert row()['power'] == 7
    card._stats = original
    assert row()['power'] == 2


def test_joint_rule_descriptions_recheck_private_references_in_both_directions():
    state = board()
    secret = add(state, 'Secret', ZoneType.HAND, player=0)
    public = add(state, 'Public')
    public.state.skip_untap = (1, 0, {'secret': secret})
    for players in (state.players, tuple(reversed(state.players))):
        shared, store = {}, ValueStore()
        for player in players:
            actual = StructuredJSON(observation(state, player, update_knowledge=False,
                shared_characteristics=shared, projection_store=store), store).value.thaw()
            assert actual == observation(state, player, update_knowledge=False)
            reference = find(actual, public)['skip_untap'][2]['secret']
            assert (reference is not None) == (player is secret.owner)


def test_joint_description_cache_does_not_cross_mutation_or_return_mutable_outputs():
    state = board()
    card = add(state, 'Public')
    card.state.skip_untap = (1, 0, {'version': 1})
    store = ValueStore()
    first = StructuredJSON(observation(state, state.players[0], update_knowledge=False,
                                      shared_characteristics={}, projection_store=store), store)
    card.state.skip_untap[2]['version'] = 2
    second = StructuredJSON(observation(state, state.players[0], update_knowledge=False,
                                       shared_characteristics={}, projection_store=store), store)
    assert first.value.readonly['battlefield'][0]['skip_untap'][2]['version'] == 1
    assert second.value.readonly['battlefield'][0]['skip_untap'][2]['version'] == 2


def test_typed_scalar_cache_preserves_reference_digests_and_is_bounded():
    values = [None, False, True, 0, 1, -1, 0.0, -0.0, 1.0, '', '1', 'x'*129]
    for value in values:
        # structural_key hashes its argument tuple, not the bare scalar.
        assert structural_key(value) == ValueStore(0).freeze((value,)).digest.hex()
    assert len({structural_key(v) for v in (True, 1, 1.0, '1')}) == 4
    assert structural_key(0.0) != structural_key(-0.0)
    for i in range(4096):
        structural_key(i)
    assert _scalar_digest.cache_info().currsize <= 2048
    with pytest.raises(ValueError):
        structural_key(float('nan'))


def test_immutable_table_proof_survives_stats_replacement_but_never_custom_stats():
    from game.game_state.stat import ModifiablePrimitiveStat
    from immutabledict import immutabledict
    state = board()
    card = add(state, 'Public', power=2, toughness=3)
    cache = state._card_snapshots
    original = card.stats
    assert cache.has_immutable_inputs(card)
    class Dynamic(ModifiablePrimitiveStat):
        def value(self, state, modifiers=None):
            return state.turn.number
    card._stats = immutabledict({**original, STAT_POWER: Dynamic(2, STAT_POWER)})
    assert not cache.has_immutable_inputs(card)
    card._stats = original
    assert cache.has_immutable_inputs(card)
    card._stats = None
    assert not cache.has_immutable_inputs(card)
    card._stats = original


def test_frontier_reuses_prefix_without_consuming_tail_and_bounds_tuple_cache():
    consumed = []
    def generate():
        for i in range(40):
            consumed.append(i)
            yield i
    frontier = CandidateFrontier(generate(), 50)
    a, complete = frontier.take(2)
    assert a == (0, 1) and not complete and consumed == [0, 1, 2]
    assert frontier.take(2)[0] is a
    for width in range(3, 42):
        prefix, complete = frontier.take(width)
        assert prefix == tuple(range(min(width, 40)))
    assert complete and len(frontier._prefixes) <= 8
    frontier.close()


def test_live_widening_does_not_rebuild_unchanged_board_projection():
    from game.ai.search_simulator import SearchSimulator
    from test_candidate_ranking import crowded
    from test_native_candidates import native_policy
    sim = SearchSimulator(crowded, policy_for=native_policy(), track_information=True,
                          structured_records=True, view_mode='search', observer=0)
    root = sim.start()
    def follow(node):
        if node.player_information().candidate_width < 6:
            sim.expand_candidates(node, 6)
        return None
    # Expanding from inside the native callback keeps the same request alive.
    # Supplying old views avoids all extra board readers.
    original = PlayerObservationBuilder.build
    builds = []
    def counted(builder, **kwargs):
        builds.append(1)
        return original(builder, **kwargs)
    with patch.object(PlayerObservationBuilder, 'build', counted):
        node = sim.follow_actions(follow, node=root)
    assert node.player_information().candidate_width == 6
    reference = SearchSimulator(crowded, policy_for=native_policy(), track_information=True,
                                structured_records=True, view_mode='search', observer=0)
    expected = reference.expand_candidates(reference.start(), 6)
    assert node.player_information() == expected.player_information()
    assert len(builds) == 1  # The initial replay still builds the actor root once.


def test_extension_does_not_request_observer_view_at_nonpriority_boundary():
    from test_information_set_search import Node, Tree, search
    class CheapBoundary(Node):
        def observation(self, player):
            if not self.terminal and self.decision.kind == 'DeclareAttackersRequest':
                assert player == self.decision.player, 'Unnecessary root observer projection'
            return super().observation(player)
    class Scenario(Tree):
        def start(self):
            return CheapBoundary(self, 'root')
        def advance_action(self, node, action):
            return CheapBoundary(self, node.spec['children'][action.kind])
    rows = {'root': {'children': {'attack': 'combat'}},
            'combat': {'player': 1, 'kind': 'DeclareAttackersRequest', 'children': {'all': 'win'}},
            'win': {'winner': 0}}
    result = search(lambda seed: Scenario(rows), iterations=2, worlds=1, depth=1)
    assert result.value == 1 and result.metrics.terminal_leaves == 2


def test_settled_leaf_is_scored_before_generating_unused_wider_menu():
    from test_information_set_search import Node, Tree, search
    class Leaf(Node):
        def player_information(self):
            info = super().player_information()
            if self.name == 'leaf':
                return replace(info, candidate_model='test-frontier', menu_complete=False, candidate_width=1)
            return info
    class Scenario(Tree):
        def start(self):
            return Leaf(self, 'root')
        def advance_action(self, node, action):
            return Leaf(self, node.spec['children'][action.kind])
        def expand_candidates(self, node, width):
            pytest.fail('A scored leaf cannot use its wider menu')
    rows = {'root': {'children': {'a': 'leaf'}},
            'leaf': {'score': .25, 'children': {'wait': 'leaf'}}}
    result = search(lambda seed: Scenario(rows), iterations=2, worlds=1, depth=1)
    assert result.value == .25 and result.metrics.heuristic_leaves == 2


def test_forced_prefix_projection_reuse_preserves_answer_and_reduces_board_builds():
    from benchmarks.search_tactics import tactical_scenario, tactical_policy
    from game.ai.search_simulator import SearchSimulator
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    class WithoutForced(dict):
        def setdefault(self, key, value):
            if len(value.views) == 1:
                return value
            return super().setdefault(key, value)
    results, builds = [], []
    original = PlayerObservationBuilder.build
    for reuse in (False, True):
        count = []
        def factory(seed):
            sim = SearchSimulator(tactical_scenario('answered_shock'), policy_for=tactical_policy,
                                  track_information=True, structured_records=True, projection_cache_size=256)
            if not reuse:
                sim._projections = WithoutForced()
            return sim
        def read(builder, **kwargs):
            count.append(1)
            return original(builder, **kwargs)
        root = factory(0).start().player_information()
        planner = InformationSetSearch(selection='puct', opponent_policy=ObservationOpponentPolicy(),
            require_root_coverage=False, compress_forced=True,
            budget=InformationSearchBudget(worlds=1, iterations=16, max_depth=6, max_advances=1024))
        with patch.object(PlayerObservationBuilder, 'build', read):
            results.append(planner.search(factory, root, seed=42))
        builds.append(len(count))
    before, after = results
    assert replace(before, metrics=after.metrics) == after
    assert before.metrics.advance_attempts == after.metrics.advance_attempts
    assert after.metrics.projection_cache_hits > before.metrics.projection_cache_hits
    assert builds[1] < builds[0]


