"""First-write runtime snapshots preserve identities, ordering and hook lifetime."""
import pytest

from benchmarks.core_reads import board
from game.enums import ManaType, ZoneType, CounterType
from game.game_state import Card, CardDefinition
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint


def checkpoint(state, card):
    return RuntimeCheckpoint(state, card_scope=frozenset({card}), defer_indexes=True)


def test_only_modified_zone_and_pool_contents_are_captured():
    state, card = board(0)
    player = card.owner
    player.mana_pool.add({ManaType.WHITE: 3})
    for index in range(2):
        player.add_card(Card(CardDefinition(f"Hand {index}"), player), ZoneType.HAND, state)
        player.add_card(Card(CardDefinition(f"Deck {index}"), player), ZoneType.DECK, state)
    before = dict(player.mana_pool)
    cp = checkpoint(state, card)
    assert not any(value is player.hand or value is player.mana_pool._data for _, value, _ in cp._restore)
    player.mana_pool.substract({ManaType.WHITE: 1})
    assert any(value is player.mana_pool._data for _, value, _ in cp._restore)
    player.move_card(card, ZoneType.HAND, state)
    assert any(value is player.hand for _, value, _ in cp._restore)
    assert not any(value is player.deck for _, value, _ in cp._restore)
    pool, data, hand = player.mana_pool, player.mana_pool._data, player.hand
    cp.rollback()
    assert player.mana_pool is pool and pool._data is data and pool == before
    assert player.hand is hand and card not in hand.values()
    assert card.get_zone() is ZoneType.BATTLEFIELD
    assert player.battlefield[card.key] is card
    assert not cp._runtime_hooks


def test_tracked_counter_and_effect_membership_rollback():
    state, card = board(0)
    card.state.counters[CounterType.PLUS_ONE] = 1
    card.state.active_cont_effects.add("old")
    cp = checkpoint(state, card)
    counters, memberships = card.state.counters, card.state.active_cont_effects
    assert not any(value is counters.data or value is memberships._values for _, value, _ in cp._restore)
    counters[CounterType.PLUS_ONE] = 3
    memberships.add("test")
    cp.rollback()
    assert card.state.counters is counters and counters == {CounterType.PLUS_ONE: 1}
    assert card.state.active_cont_effects is memberships and set(memberships) == {"old"}


def test_nested_checkpoint_and_commit_release_observers():
    state, card = board(0)
    pool = card.owner.mana_pool
    pool.add({ManaType.WHITE: 3})
    outer = checkpoint(state, card)
    inner = checkpoint(state, card)
    pool.add({ManaType.WHITE: 2})
    inner.rollback()
    assert pool[ManaType.WHITE] == 3
    pool.add({ManaType.WHITE: 1})
    outer.rollback()
    assert pool[ManaType.WHITE] == 3
    cp = checkpoint(state, card)
    cp.close()
    cp.close()
    assert not cp._runtime_hooks
    assert "_cost_before_write" not in vars(pool)
    pool.clear()
    assert not pool


def test_native_payment_omits_fixed_storage_with_python_generic_metadata():
    from game.game_actions.resolution.cost_runtime_journal import unchanged_register_structure
    state, card = board(0)
    assert "__orig_class__" in vars(state.card_register.storage)
    roots = unchanged_register_structure(state)
    assert any(value is state.card_register.storage for value in roots)
    cp = RuntimeCheckpoint(state, card_scope=frozenset({card}), defer_indexes=True, native_payment=True)
    assert not any(value is state.card_register._by_key for _, value, _ in cp._restore)
    card.state.tapped = True
    cp.rollback()
    assert card.state.tapped is False


def test_storage_method_adapter_retains_eager_registration_capture():
    from game.game_actions.resolution.cost_runtime_journal import unchanged_register_structure
    state, card = board(0)
    state.card_register.storage.register = lambda card: None
    assert unchanged_register_structure(state) == ()


def test_native_payment_omits_constant_metadata_but_restores_mutable_mapping_children():
    from helper.runtime_object import ImmutableKeyedCollection
    from game.enums import TurnPhase

    class Child:
        __module__ = "game.test_payment"

        def __init__(self):
            self.key = "child"
            self.values = [1]

    state, card = board(0)
    child = Child()
    collection = ImmutableKeyedCollection({child.key: child})
    state.payment_data = collection
    cp = RuntimeCheckpoint(state, card_scope=frozenset({card}), defer_indexes=True, native_payment=True)
    assert cp._native_values
    assert not any(value is collection._dict or value is TurnPhase.UPKEEP for _, value, _ in cp._restore)
    child.values.append(2)
    cp.rollback()
    assert state.payment_data is collection and collection[child.key] is child
    assert child.values == [1]


def test_extended_enum_and_immutable_mapping_attributes_keep_rollback(monkeypatch):
    from helper.runtime_object import ImmutableKeyedCollection
    from game.enums import TurnPhase
    state, card = board(0)
    collection = ImmutableKeyedCollection({})
    collection.runtime_values = [3]
    state.payment_data = collection
    monkeypatch.setattr(TurnPhase.UPKEEP, "runtime_values", [4], raising=False)
    state.payment_phase = TurnPhase.UPKEEP
    cp = RuntimeCheckpoint(state, card_scope=frozenset({card}), defer_indexes=True, native_payment=True)
    collection.runtime_values.append(5)
    TurnPhase.UPKEEP.runtime_values.append(6)
    cp.rollback()
    assert collection.runtime_values == [3]
    assert TurnPhase.UPKEEP.runtime_values == [4]


def test_generic_checkpoint_retains_immutable_container_implementation_snapshot():
    from helper.runtime_object import ImmutableKeyedCollection
    state, _ = board(0)
    state.payment_data = ImmutableKeyedCollection({})
    cp = RuntimeCheckpoint(state)
    assert not cp._native_values
    assert any(value is state.payment_data._dict for _, value, _ in cp._restore)
    cp.close()


@pytest.mark.parametrize("mutation", [
    lambda zone, key, card: zone.__setitem__(key, card),
    lambda zone, key, card: zone.update({key: card}),
    lambda zone, key, card: zone.setdefault(key, card),
    lambda zone, key, card: zone.__ior__({key: card}),
    lambda zone, key, card: zone.popitem(),
    lambda zone, key, card: zone.clear(),
    lambda zone, key, card: zone.__delitem__(next(iter(zone))),
])
def test_zone_mutators_restore_order_and_original_memberships(mutation):
    state, card = board(0)
    player = card.owner
    for index in range(3):
        other = Card(CardDefinition(f"Other {index}"), player)
        player.add_card(other, ZoneType.HAND, state)
    before = tuple(player.hand.items())
    cp = checkpoint(state, card)
    mutation(player.hand, card.key, card)
    cp.rollback()
    assert tuple(player.hand.items()) == before
    assert "_cost_before_write" not in vars(player.hand)
