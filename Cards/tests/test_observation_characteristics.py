from unittest.mock import patch

from runtime_write_guard import deny_writes
from benchmarks.mcts_observation_reuse import observation_reference
from game.ai.observation import PlayerObservationBuilder, observation
from game.enums import CounterType, ZoneType
from game.stat_type import STAT_KEYWORDS, STAT_POWER
from test_player_observation import board, add, find


def test_native_rows_are_reused_but_runtime_damage_and_new_stats_stay_fresh():
    with deny_writes():
        state = board()
        card = add(state, 'Native', power=2, toughness=3)
        original = PlayerObservationBuilder._base_characteristics
        reads = []
        def read(builder, value):
            reads.append(value)
            return original(builder, value)
        with patch.object(PlayerObservationBuilder, '_base_characteristics', read):
            before = observation(state, state.players[0], update_knowledge=False)
            card.state.damage_marked = 1
            after = observation(state, state.players[0], update_knowledge=False)
            assert reads == [card]
            assert find(before, card)['damage_marked'] == 0
            assert find(after, card)['damage_marked'] == 1
            card.set_base_stat(STAT_KEYWORDS, frozenset({'flying'}))
            assert find(observation(state, state.players[0]), card)['keywords'] == ['flying']
            assert len(reads) == 2


def test_modified_cards_custom_readers_and_changed_visibility_match_reference():
    with deny_writes():
        state = board()
        card = add(state, 'Native', power=2, toughness=3)
        def check():
            for player in state.players:
                with observation_reference():
                    expected = observation(state, player, update_knowledge=False)
                assert observation(state, player, update_knowledge=False) == expected
        check()
        card.state.counters[CounterType.PLUS_ONE] = 2
        check()
        card.state.counters.clear()
        card.set_base_stat(STAT_POWER, 7)
        check()
        original = card.get_stat
        card.get_stat = lambda stat, runtime: frozenset({'custom'}) if stat == STAT_KEYWORDS else original(stat, runtime)
        check()
        del card.get_stat
        card.state.face_down = True
        check()
        card.state.face_down = False
        card.set_zone(ZoneType.HAND, state)
        check()


def test_unknown_stat_readers_never_enter_persistent_cache():
    from game.game_state.stat import ModifiableReferenceStat
    class Dynamic(ModifiableReferenceStat):
        def value(self, state, modifiers=None):
            return frozenset({str(state.turn.number)})
    with deny_writes():
        state = board()
        card = add(state, 'Dynamic')
        from immutabledict import immutabledict
        card._stats = immutabledict({**card.stats, STAT_KEYWORDS: Dynamic(frozenset(), STAT_KEYWORDS)})
        from game.ai.observation_characteristics import native_characteristics
        reader = lambda: {'keywords': [str(state.turn.number)]}
        first = native_characteristics(card, state, reader)
        state.turn.number += 1
        second = native_characteristics(card, state, reader)
        assert first['keywords'] != second['keywords']
