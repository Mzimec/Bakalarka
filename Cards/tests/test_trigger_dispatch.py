"""Early dispatch preserves event-time eligibility and extension semantics."""
from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from game.enums import ZoneType
from game.game_state import State, Card, CardDefinition, Player
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import TriggerAbilityDefinition, TriggerAbility
from game.game_actions.resolution.event_bus import GameEvent, capture_event, capture_trigger_roster
from game.game_actions.triggers.trigger_condition import EventKeyCondition, DiesCondition
from game.game_actions.triggers.trigger_roster import TriggerBinding, event_key_possible


def board(definition=None):
    state = State([Player([], ScriptedController()) for _ in range(2)])
    definition = definition or TriggerAbilityDefinition(key="trigger", condition=EventKeyCondition("test"))
    card = Card(CardDefinition("Watcher", triggers=frozenset({definition})), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD, state)
    return state, card, definition


def test_capture_allocates_execution_ability_only_for_relevant_event(monkeypatch):
    state, card, definition = board()
    original = TriggerAbility.__init__
    calls = []
    def counting(self, *args, **kwargs):
        calls.append(args)
        original(self, *args, **kwargs)
    monkeypatch.setattr(TriggerAbility, "__init__", counting)
    before = capture_trigger_roster(state)
    assert isinstance(before[0], TriggerBinding)
    assert not calls
    assert not capture_event(state, GameEvent("unrelated"), before=before).triggered_abilities
    assert not calls
    triggers = capture_event(state, GameEvent("test", card), before=before).triggered_abilities
    assert len(calls) == len(triggers) == 1
    assert triggers[0].definition is definition
    assert triggers[0].source is card


def test_roster_keeps_controller_before_control_change_and_public_api_stays_runtime():
    state, card, definition = board()
    before = capture_trigger_roster(state)
    card.set_controller(state.players[1])
    after = capture_trigger_roster(state)
    assert before[0].controller is state.players[0]
    assert after[0].controller is state.players[1]
    ability = state.get_triggered_abilities(GameEvent("unrelated"))[0]
    assert isinstance(ability, TriggerAbility)
    assert not ability.matches(state)
    card.owner.move_card(card, ZoneType.HAND, state)
    assert not capture_trigger_roster(state)


@pytest.mark.parametrize("method", ["get_triggered_abilities", "get_trigger_abilities"])
def test_custom_state_getter_is_not_bypassed(method):
    state, card, definition = board()
    supplied = definition.to_ability(card, state.players[1])
    setattr(state, method, MethodType(lambda self, event: [supplied], state))
    assert capture_trigger_roster(state) == (supplied,)
    assert capture_trigger_roster(SimpleNamespace(get_triggered_abilities=lambda event: [supplied])) == (supplied,)


def test_custom_definition_factory_and_matching_are_preserved():
    calls = []
    class CustomDefinition(TriggerAbilityDefinition):
        def to_ability(self, card, player, **kwargs):
            calls.append(kwargs.get("event"))
            ability = super().to_ability(card, player, **kwargs)
            ability.matches = lambda state: True
            return ability
    state, card, definition = board(CustomDefinition(key="custom", condition=EventKeyCondition("test")))
    roster = capture_trigger_roster(state)
    assert isinstance(roster[0], TriggerAbility)
    captured = capture_event(state, GameEvent("other"), before=roster, after=roster)
    assert len(captured.triggered_abilities) == 1
    assert len(calls) == 2


def test_subclass_does_not_inherit_restrictive_event_metadata():
    class CustomDeath(DiesCondition):
        def matches_trigger(self, trigger, state):
            return trigger.event.key == "custom"
    definition = TriggerAbilityDefinition(key="custom", condition=CustomDeath())
    state, card, _ = board(definition)
    assert event_key_possible(definition, GameEvent("custom"))
    assert capture_event(state, GameEvent("custom")).triggered_abilities


def test_instance_predicate_override_is_not_filtered():
    condition = EventKeyCondition("test")
    object.__setattr__(condition, "matches_trigger", lambda trigger, state: True)
    definition = TriggerAbilityDefinition(key="custom", condition=condition)
    state, card, _ = board(definition)
    assert capture_event(state, GameEvent("custom")).triggered_abilities


def test_slotted_external_condition_keeps_general_matching():
    class ExternalCondition:
        __slots__ = ()
        def matches(self, state, event):
            return event.key == "custom"
    state, card, _ = board(TriggerAbilityDefinition(key="custom", condition=ExternalCondition()))
    assert capture_event(state, GameEvent("custom")).triggered_abilities


def test_intervening_if_keeps_original_invocation_order():
    calls = []
    definition = TriggerAbilityDefinition(key="test", condition=EventKeyCondition("test"),
                                          intervening_if=lambda state, event: calls.append(event.key) or True)
    state, card, _ = board(definition)
    assert not capture_event(state, GameEvent("other")).triggered_abilities
    assert calls == ["other"]


def test_gain_remove_and_restore_trigger_definitions_rebuild_roster():
    from game.stat_type import STAT_TRIGGERS
    from helper.mutability_objs import ImmutableDict
    from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
    state, card, definition = board()
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_TRIGGERS, ImmutableDict({"new": replace(definition, key="new")}))
    assert [binding.definition.key for binding in capture_trigger_roster(state)] == ["new"]
    card.set_base_stat(STAT_TRIGGERS, ImmutableDict())
    assert not capture_trigger_roster(state)
    checkpoint.rollback()
    assert [binding.definition.key for binding in capture_trigger_roster(state)] == ["trigger"]
