"""Exact immutable reader contracts, including search and visibility parity."""
from contextlib import nullcontext
from dataclasses import replace
import json
import pickle
from unittest.mock import patch

import pytest

from benchmarks.mcts_compact_observation import compact_reference
from benchmarks.mcts_decision_latency import answer
from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.action_priors import HeuristicActionPrior
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation import observation
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_observation import ActingPlayerObservation
from game.ai.search_simulator import SearchSimulator
from game.ai.structured_values import StructuredJSON, Value, ValueStore, decode, structural_key
from runtime_write_guard import deny_writes


def test_readers_share_frozen_rows_but_exports_remain_independent_and_keys_identical():
    with deny_writes():
        store = ValueStore()
        row = {'id': 1, 'types': ['CREATURE'], 'nullable': None, 'power': 3}
        first = StructuredJSON({'cards': [row], 'turn': 1}, store)
        second = StructuredJSON({'cards': [dict(row)], 'turn': 2}, store)
        a, b = first.value.readonly, second.value.readonly
        assert a['cards'][0] is b['cards'][0]
        assert a.get('absent', 9) == 9 and a['cards'][0].get('nullable', 9) is None
        with pytest.raises(TypeError):
            a['cards'][0]['power'] = 99
        with pytest.raises(TypeError):
            a['cards'][0]['types'][0] = 'LAND'
        with pytest.raises(AttributeError):
            a['cards'][0]._fields = {}
        exported = decode(first)
        exported['cards'][0]['types'].append('LAND')
        assert a['cards'][0]['types'] == ('CREATURE',)
        assert decode(first)['cards'][0]['types'] == ['CREATURE']
        assert ValueStore().freeze(a) is first.value
        assert structural_key(first) == structural_key({'cards': [row], 'turn': 1})
        assert json.loads(first.text) == decode(first)
        restored = pickle.loads(pickle.dumps(first))
        assert 'readonly' not in restored.value.items[0][1].__dict__
        assert restored == first and restored.value.readonly['cards'][0]['power'] == 3


def test_readonly_visibility_and_old_snapshots_survive_zone_and_face_down_changes():
    from test_player_observation import board, add
    from game.enums import ZoneType
    with deny_writes():
        state = board()
        secret = add(state, 'Private', ZoneType.HAND, player=0)
        visible = add(state, 'Public', power=2, toughness=3)
        views = [ActingPlayerObservation(p.idx, 'Test', StructuredJSON(
            observation(state, p, update_knowledge=False))) for p in state.players]
        assert views[0].readonly()['hand'][0]['name'] == 'Private'
        assert not views[1].readonly()['hand']
        assert 'Private' not in views[1]._json.text
        visible.state.face_down = True
        later = observation(state, state.players[0], update_knowledge=False)
        assert later['battlefield'][0]['name'] is None
        assert views[0].readonly()['battlefield'][0]['name'] == 'Public'
        secret.set_zone(ZoneType.BATTLEFIELD, state)
        assert not views[1].readonly()['hand']


def test_joint_projection_shares_only_rows_with_matching_visibility_gates():
    from test_player_observation import board, add
    from game.enums import ZoneType
    with deny_writes():
        state = board()
        secret = add(state, 'Private', ZoneType.HAND, player=0)
        public = add(state, 'Public', power=2, toughness=3)
        other = add(state, 'Other', power=1, toughness=1)
        def project():
            shared, store, views = {}, ValueStore(), []
            for player in state.players:
                compact = observation(state, player, update_knowledge=False,
                                      shared_characteristics=shared, projection_store=store)
                frozen = StructuredJSON(compact, store)
                assert decode(frozen) == observation(state, player, update_knowledge=False)
                views.append(frozen.value.readonly)
            return views
        a, b = project()
        assert a['battlefield'][0] is b['battlefield'][0]
        # Both cards share the same nested description in one builder; its
        # cached dependencies must propagate to the second card's proof too.
        private_reference = {'reference': secret}
        public.state.skip_untap = (123, 0, private_reference)
        other.state.skip_untap = (123, 0, private_reference)
        a, b = project()
        for index in (0, 1):
            assert a['battlefield'][index] is not b['battlefield'][index]
            assert a['battlefield'][index]['skip_untap'][2]['reference']['card'] == secret.command_id
            assert b['battlefield'][index]['skip_untap'][2]['reference'] is None


def test_readonly_field_indexes_are_charged_to_tree_memory():
    from game.ai.search_tree_memory import _charge
    with deny_writes():
        value = StructuredJSON({'card': {'power': 3, 'keywords': ['flying']}}).value
        before = _charge(value, set(), 10**9)
        assert value.readonly['card']['keywords'] == ('flying',)
        after = _charge(value, set(), 10**9)
        assert after > before + 2 * 100


@pytest.mark.parametrize('dependency', ['attachment', 'last_known'])
def test_joint_rows_track_negative_visibility_and_private_last_known(dependency):
    from test_player_observation import board, add
    from game.enums import ZoneType
    from game.game_actions.resolution.event_bus import capture_single_card
    with deny_writes():
        state = board()
        secret = add(state, 'Private', ZoneType.HAND, player=1)
        public = add(state, 'Public', power=2, toughness=3)
        if dependency == 'attachment':
            # A filtered-out reference must also be recorded, otherwise the
            # first viewer's empty list could be reused for the informed owner.
            public.state.attached[secret.key] = secret
        else:
            public.state.skip_untap = (123, 0, capture_single_card(state, secret))
        shared, store = {}, ValueStore()
        for player in state.players:
            compact = observation(state, player, update_knowledge=False,
                                  shared_characteristics=shared, projection_store=store)
            assert decode(StructuredJSON(compact, store)) == observation(
                state, player, update_knowledge=False)


def test_joint_projection_builds_public_row_once_and_new_boundary_is_fresh():
    from test_player_observation import board, add
    from game.ai.observation import PlayerObservationBuilder
    with deny_writes():
        state = board()
        card = add(state, 'Public', power=2, toughness=3)
        read, calls, store = PlayerObservationBuilder._card, [], ValueStore()
        def record(builder, obj):
            calls.append(obj)
            return read(builder, obj)
        def project():
            shared, views = {}, []
            for player in state.players:
                views.append(StructuredJSON(observation(state, player, update_knowledge=False,
                    shared_characteristics=shared, projection_store=store), store))
            return views
        with patch.object(PlayerObservationBuilder, '_card', record):
            old = project()
            assert calls == [card]
            card.state.damage_marked = 2
            new = project()
            assert calls == [card, card]
        assert decode(old[0])['battlefield'][0]['damage_marked'] == 0
        assert decode(new[0])['battlefield'][0]['damage_marked'] == 2


@pytest.mark.parametrize('case', ['lethal_shock', 'answered_shock', 'useful_pacifism',
                                'redundant_pacifism', 'attack_pacified'])
def test_compact_policy_reads_no_deep_thaw_and_matches_reference(case):
    with deny_writes():
        sim = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy,
                              track_information=True, structured_records=True)
        info = sim.start().player_information()
        policy = ObservationOpponentPolicy()
        with compact_reference():
            expected = policy.rank(info)
        with patch.object(Value, 'thaw', side_effect=AssertionError('deep decode in pure policy')):
            actual = policy.rank(info)
        assert actual == expected
        assert policy(replace(info, actions=tuple(reversed(info.actions)))) == expected[0].action


@pytest.mark.parametrize('structured', [False, True])
def test_whole_search_matches_deep_decode_reference(structured):
    def factory(seed):
        return SearchSimulator(tactical_scenario('answered_shock'), policy_for=tactical_policy,
                              track_information=True, structured_records=structured)
    results = []
    with deny_writes():
        for reference in (True, False):
            with compact_reference() if reference else nullcontext():
                root = factory(0).start().player_information()
                planner = InformationSetSearch(selection='puct', opponent_policy=ObservationOpponentPolicy(),
                    action_prior=HeuristicActionPrior(), require_root_coverage=False,
                    force_initial_coverage=False, compress_forced=True,
                    budget=InformationSearchBudget(worlds=1, iterations=12, max_depth=3, max_advances=512))
                results.append(planner.search(factory, root, seed=42))
        assert answer(results[0]) == answer(results[1])
