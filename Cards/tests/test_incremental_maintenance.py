"""Opt-in maintenance experiments must preserve rules and rollback."""
from unittest.mock import patch

import pytest

from game.game_state import Card, CardDefinition, Player, State
from game.enums import CardType, ZoneType
from game.game_loop.minimal_game import ScriptedController
from game.game_state.registers.card_register import CardRegister, IK_POWER
from game.game_state.registers.player_register import (
    IK_HEALTH, IK_ALIVE, IK_NAME, IK_POISON, IK_FAILED_DRAW, IK_HAS_LOST,
)
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.stat_type import STAT_POWER
from helper.query_system.query import EqQuery
from runtime_write_guard import deny_writes
from benchmarks.mcts_incremental_maintenance import maintenance


@pytest.fixture(autouse=True)
def experimental_paths():
    with maintenance(True):
        yield


def make_state(player_type=Player):
    return State([player_type([], ScriptedController()), Player([], ScriptedController())])


def add_unit(state):
    card = Card(CardDefinition('Unit', types=frozenset({CardType.CREATURE}),
                               power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    return card


def test_new_registration_reuses_exact_projection_without_another_characteristic_read():
    with deny_writes():
        state = make_state()
        with patch.object(CardRegister, '_characteristic_index_values',
                          wraps=CardRegister._characteristic_index_values) as reader:
            card = add_unit(state)
            assert reader.call_count == 1
            assert state._card_snapshots.has_current_index_projection(state, card)
            assert card in state.query_cards(EqQuery(IK_POWER, 2))
            state.card_register.index_values(card)
            assert reader.call_count == 1


def test_reregistering_a_dirty_card_cannot_publish_an_old_membership():
    state = make_state()
    card = add_unit(state)
    card.set_base_stat(STAT_POWER, 9)
    state.card_register.register(card)
    assert card in state.query_cards(EqQuery(IK_POWER, 9))
    assert card not in state.query_cards(EqQuery(IK_POWER, 2))


def test_registration_with_custom_reader_stays_cold():
    state = make_state()
    card = Card(CardDefinition('Custom', power=2), state.players[0])
    card.get_power = lambda state: state.players[0].health
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    assert id(card) not in state._card_snapshots._index_entries


@pytest.mark.parametrize('attribute,value,key', [
    ('health', 0, IK_HEALTH), ('has_lost', True, IK_HAS_LOST),
    ('failed_draw', True, IK_FAILED_DRAW), ('poison_counters', 7, IK_POISON),
])
def test_player_resource_updates_match_full_projection_and_rollback(attribute, value, key):
    with deny_writes():
        state = make_state()
        player, register = state.players[0], state.player_register
        state.synchronise_registers()
        checkpoint = RuntimeCheckpoint(state)
        original = register._snapshots[player.key]
        setattr(player, attribute, value)
        # Invalidation must still reach life-dependent continuous effects.
        assert state._effects_dirty
        assert key in register._dirty_indexes[player.key]
        assert player in state.query_players(EqQuery(key, value))
        assert register._snapshots[player.key] == register.index_values(player)
        assert state.is_game_over == (attribute in ('health', 'has_lost'))
        checkpoint.rollback()
        assert register._snapshots[player.key] == original
        assert not state.is_game_over


def test_multiple_partial_notifications_and_full_notification_merge_safely():
    state = make_state()
    player, register = state.players[0], state.player_register
    player.health = 11
    player.poison_counters = 4
    assert {IK_HEALTH, IK_ALIVE, IK_POISON} <= register._dirty_indexes[player.key]
    register.mark_changed(player)
    player.has_lost = True
    assert register._dirty_indexes[player.key] is None
    register.synchronise()
    assert register._snapshots[player.key] == register.index_values(player)


def test_custom_player_dependencies_keep_complete_index_projection():
    class NamedByLife(Player):
        @property
        def name(self):
            return f'Life {self.health}'
    state = make_state(NamedByLife)
    player = state.players[0]
    player.health = 9
    assert player in state.query_players(EqQuery(IK_NAME, 'life 9'))


def test_redundant_sync_retains_row_but_applies_later_real_change():
    state = make_state()
    card = add_unit(state)
    state.synchronise_registers()
    register = state.card_register
    previous, token = register._snapshots[card.key], register.membership_token((IK_POWER,))
    card.set_base_stat(STAT_POWER, 2)
    register.synchronise()
    current = register._snapshots[card.key]
    assert current is previous, (
        {key: (value, current.get(key)) for key, value in previous.items() if current.get(key) != value},
        register.synchronise.__func__,
    )
    assert register.membership_token((IK_POWER,)) == token
    card.set_base_stat(STAT_POWER, 8)
    assert card in state.query_cards(EqQuery(IK_POWER, 8))
    assert register._snapshots[card.key] is not previous


def test_is_game_over_still_refreshes_even_when_player_register_is_clean(monkeypatch):
    state = make_state()
    original = state.refresh_continuous_effects
    calls = []
    def refresh():
        if not calls:
            state.players[0].health = 0
        calls.append(True)
        original()
    monkeypatch.setattr(state, 'refresh_continuous_effects', refresh)
    assert not state.player_register._dirty
    assert state.is_game_over
    assert calls


def test_narrow_player_update_still_retargets_life_dependent_effects():
    from test_continuous_dependencies import add_bonus
    from game.target.continuous_targets import SourceControllerLifeSpec
    state = make_state()
    card = add_unit(state)
    add_bonus(state, card, spec=SourceControllerLifeSpec(25))
    assert card.get_power(state) == 2
    checkpoint = RuntimeCheckpoint(state)
    card.owner.health = 25
    assert card in state.query_cards(EqQuery(IK_POWER, 3))
    card.owner.health = 24
    assert card in state.query_cards(EqQuery(IK_POWER, 2))
    checkpoint.rollback()
    assert card.get_power(state) == 2
