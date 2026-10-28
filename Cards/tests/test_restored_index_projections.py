"""Owned cache transfer must preserve native values and reject stale proofs."""
import pytest

from benchmarks.core_reads import board
from game.ai.simulation_world import copy_world
from game.enums import CardType, CounterType, ZoneType
from game.game_state import Card, CardDefinition
from game.game_state.registers.card_register import IK_CONTROLLER, IK_POWER
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from game.stat_type import STAT_POWER
from helper.query_system.query import EqQuery
from runtime_write_guard import deny_writes


@pytest.mark.parametrize('fast_copy', [False, True])
def test_index_rows_rebind_controllers_and_do_not_recompute_unchanged_cards(fast_copy, monkeypatch):
    state, card = board(0)
    state.card_register.index_values(card)
    source = state._card_snapshots
    assert source.has_current_index_projection(state, card)
    with deny_writes():
        copied, _ = copy_world(state, (), fast_copy=fast_copy)
        other = copied.card_register.get_by_key(card.key)
        entry = copied._card_snapshots._index_entries[id(other)]
        assert entry.card is other and entry.card is not card
        assert entry.snapshot[IK_CONTROLLER] == frozenset({copied.players[0]})
        assert next(iter(entry.snapshot[IK_CONTROLLER])) is copied.players[0]
        assert copied._card_snapshots.has_current_index_projection(copied, other)
        assert copied._card_snapshots._index_entries is not source._index_entries
        assert not copied._card_snapshots._entries  # No LKI history is transferred.
        original = copied.card_register._characteristic_index_values
        reads = []
        def record(*args):
            reads.append(args[1].key)
            return original(*args)
        monkeypatch.setattr(copied.card_register, '_characteristic_index_values', record)
        copied._effects_dirty = True
        copied.refresh_continuous_effects()
        assert other in copied.query_cards(EqQuery(IK_POWER, 2))
        assert not reads
        other.set_base_stat(STAT_POWER, 9)
        assert other in copied.query_cards(EqQuery(IK_POWER, 9))
        assert card.get_power(state) == 2
        assert reads


def test_stale_incarnation_and_definition_metadata_are_not_transferred():
    state, base = board(0)
    ability = ActivatedAbilityDefinition(key='native')
    card = Card(CardDefinition('Ability unit', types=frozenset({CardType.CREATURE}),
                               power=2, toughness=3, abilities=(ability,)), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD, state)
    state.synchronise_registers()
    state.card_register.index_values(base)
    state.card_register.index_values(card)
    assert state._card_snapshots.has_current_index_projection(state, card)
    # Mutate frozen authored metadata deliberately, without a card notification.
    object.__setattr__(ability, 'allowed_zones', frozenset({ZoneType.GRAVEYARD}))
    # Likewise, a cached older incarnation cannot be promoted to a fresh proof.
    base.zone_revision += 1
    with deny_writes():
        copied, _ = copy_world(state, ())
    for original in (base, card):
        other = copied.card_register.get_by_key(original.key)
        assert id(other) not in copied._card_snapshots._index_entries


def test_modified_cards_and_cold_reference_keep_full_recalculation():
    state, card = board(2)
    card.state.counters[CounterType.PLUS_ONE] = 3
    state.synchronise_registers()
    with deny_writes():
        retained, _ = copy_world(state, ())
        reference, _ = copy_world(state, (), retain_index_projections=False)
    assert not reference._card_snapshots._index_entries
    a, b = (s.card_register.get_by_key(card.key) for s in (retained, reference))
    assert id(a) not in retained._card_snapshots._index_entries
    assert a.get_power(retained) == b.get_power(reference) == 7
    a.state.counters[CounterType.PLUS_ONE] = 5
    assert a.get_power(retained) == 9
    assert b.get_power(reference) == card.get_power(state) == 7


def test_instance_characteristic_override_does_not_inherit_a_native_cached_row():
    state, card = board(0)
    state.card_register.index_values(card)
    assert state._card_snapshots.has_current_index_projection(state, card)
    card.get_power = lambda state: 17
    with deny_writes():
        copied, _ = copy_world(state, ())
    other = copied.card_register.get_by_key(card.key)
    assert id(other) not in copied._card_snapshots._index_entries
    copied.card_register.mark_changed(other)
    assert other in copied.query_cards(EqQuery(IK_POWER, 17))
    assert copied._card_snapshots is not state._card_snapshots
