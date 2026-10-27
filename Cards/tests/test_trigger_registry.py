"""Incremental rosters preserve event-time eligibility and extension fallbacks."""
from dataclasses import replace

from game.ai.simulation_world import copy_world
from game.enums import ZoneType
from game.game_actions.data_structs.ability import TriggerAbilityDefinition
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_actions.resolution.event_bus import capture_event, capture_trigger_roster, GameEvent
from game.game_actions.triggers.trigger_condition import EventKeyCondition
from game.game_actions.triggers.trigger_registry import TriggerRoster
from game.game_state import Card, CardDefinition
from tests.test_trigger_dispatch import board


def test_unchanged_native_roster_does_not_discover_sources_again(monkeypatch):
    state, card, definition = board()
    before = capture_trigger_roster(state)
    # An unchanged capture does not rediscover a native card's definitions.
    monkeypatch.setattr(Card, "get_trigger_defs", lambda *args: (_ for _ in ()).throw(AssertionError("rediscovery")))
    assert capture_trigger_roster(state) is before
    assert len(capture_event(state, GameEvent("test"), before, after=before).triggered_abilities) == 1
    assert not capture_event(state, GameEvent("unrelated"), before, after=before).triggered_abilities


def test_native_controller_zone_and_rollback_keep_separate_snapshots():
    state, card, definition = board()
    before = capture_trigger_roster(state)
    checkpoint = RuntimeCheckpoint(state)
    card.set_controller(state.players[1])
    after = capture_trigger_roster(state)
    assert before[0].controller is state.players[0]
    assert after[0].controller is state.players[1]
    card.owner.move_card(card, ZoneType.HAND, state)
    assert not capture_trigger_roster(state)
    checkpoint.rollback()
    assert capture_trigger_roster(state)[0].controller is state.players[0]
    card.owner.move_card(card, ZoneType.HAND, state)
    assert not capture_trigger_roster(state)


def test_key_buckets_keep_unknown_predicates_and_original_order():
    state, card, definition = board()
    other = replace(definition, key="other", condition=EventKeyCondition("other"))
    callbacks = []
    unknown = replace(definition, key="unknown", intervening_if=lambda state, event: callbacks.append(event.key) or True)
    from game.game_actions.triggers.trigger_roster import TriggerBinding
    extras = [replace(other, key=f"extra-{i}", condition=EventKeyCondition("extra")) for i in range(3)]
    roster = TriggerRoster(TriggerBinding(item, card, card.owner) for item in (definition, unknown, other, *extras))
    assert [item.definition.key for item in roster.for_event(GameEvent("test"))] == ["trigger", "unknown"]
    assert [item.definition.key for item in roster.for_event(GameEvent("other"))] == ["unknown", "other"]
    capture_event(state, GameEvent("unrelated"), roster, after=roster)
    assert callbacks == ["unrelated"]


def test_custom_factories_are_still_called_at_each_capture():
    calls = []
    class Custom(TriggerAbilityDefinition):
        def to_ability(self, *args, **kwargs):
            calls.append(True)
            return super().to_ability(*args, **kwargs)
    state, card, definition = board(Custom(key="custom", condition=EventKeyCondition("test")))
    capture_trigger_roster(state)
    calls.clear()
    capture_trigger_roster(state)
    capture_trigger_roster(state)
    assert len(calls) == 2


def test_new_cards_unregister_and_clone_own_registry_bindings():
    state, card, definition = board()
    assert len(capture_trigger_roster(state)) == 1
    second = Card(CardDefinition("Second", triggers=frozenset({definition})), card.owner)
    second.owner.add_card(second, ZoneType.BATTLEFIELD, state)
    assert len(capture_trigger_roster(state)) == 2
    cloned, _ = copy_world(state, {})
    assert not cloned._trigger_registry._bindings
    other = cloned.card_register.get_by_key(card.key)
    cloned_roster = capture_trigger_roster(cloned)
    assert cloned_roster is not capture_trigger_roster(state)
    assert cloned_roster[0].source is other
    card.owner.remove_card(second)
    assert len(capture_trigger_roster(state)) == 1
    assert len(capture_trigger_roster(cloned)) == 2


def test_query_adapter_is_preserved():
    state, card, definition = board()
    assert capture_trigger_roster(state)
    state.query_cards = lambda query: ()
    assert not capture_trigger_roster(state)
