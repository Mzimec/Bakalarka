#----------------------------------------------------------------
# Test helpers
#----------------------------------------------------------------

from dummy_classes import DummyCard, DummyEffect, DummyState
from helper_funcs import make_slot

from game.abilities import (
    EffectActionNode,
    SubAbilityDefinition,
    TriggerCondition,
    TriggeredAbility,
    TriggeredAbilityDefinition,
    ZoneType,
)
from game.game_actions import AbilityAction, GameEvent
from game.game_actions.event_bus import EventBus, TriggerResolver


class EventKeyCondition(TriggerCondition):
    def __init__(self, expected_key: str):
        self.expected_key = expected_key
        self.checked_events = []

    def matches(self, state, event):
        self.checked_events.append(event)
        return event.key == self.expected_key


class DummyController:
    def __init__(self):
        self.trigger_choices = []

    def choose_trigger_action(self, state, trigger, actions):
        self.trigger_choices.append({
            "state": state,
            "trigger": trigger,
            "actions": actions,
        })
        return actions[-1]


class TriggerPlayer:
    def __init__(self, name: str):
        self.name = name
        self.controller = DummyController()


class DummyStack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)


class TriggerState(DummyState):
    def __init__(self, players=None, active_player=None, triggered_abilities=None):
        self.players = list(players) if players is not None else []
        self.active_player = active_player if active_player is not None else (
            self.players[0] if self.players else None
        )
        self.stack = DummyStack()
        self.triggered_abilities = list(triggered_abilities) if triggered_abilities else []
        self.requested_events = []

    def get_next_player(self, player):
        idx = self.players.index(player)
        return self.players[(idx + 1) % len(self.players)]

    def get_triggered_abilities(self, event=None):
        self.requested_events.append(event)
        return [
            TriggeredAbility(
                source=ability.source,
                data=ability.data,
                event=event,
            )
            for ability in self.triggered_abilities
        ]


def make_triggered_ability(
    source,
    condition,
    action=None,
    key="trigger",
    allowed_zones=None,
    uses_stack=True,
):
    return TriggeredAbility(
        source=source,
        data=TriggeredAbilityDefinition(
            key=key,
            cost_action=None,
            action=action,
            condition=condition,
            allowed_zones=allowed_zones or {ZoneType.BATTLEFIELD},
            uses_stack=uses_stack,
        )
    )


def make_effect_action(effect, slot_key="target", candidates=None):
    candidates = [] if candidates is None else candidates
    return SubAbilityDefinition(
        action_node=EffectActionNode(effect.key, {slot_key}),
        slots={
            slot_key: make_slot(slot_key, candidates),
        },
        effects={
            effect.key: effect,
        }
    )


def make_no_target_action(effect):
    return SubAbilityDefinition(
        action_node=EffectActionNode(effect.key, set()),
        slots={},
        effects={
            effect.key: effect,
        }
    )


#----------------------------------------------------------------
# Trigger condition tests
#----------------------------------------------------------------

def test_triggered_ability_matches_event():
    event = GameEvent("unit_died")
    condition = EventKeyCondition("unit_died")

    ability = make_triggered_ability(
        source=DummyCard("SOURCE"),
        condition=condition,
    )

    triggered = TriggeredAbility(
        source=ability.source,
        data=ability.data,
        event=event,
    )

    assert triggered.matches(DummyState())
    assert condition.checked_events == [event]


def test_triggered_ability_rejects_wrong_event():
    condition = EventKeyCondition("unit_died")

    ability = make_triggered_ability(
        source=DummyCard("SOURCE"),
        condition=condition,
    )

    triggered = TriggeredAbility(
        source=ability.source,
        data=ability.data,
        event=GameEvent("turn_start"),
    )

    assert not triggered.matches(DummyState())


def test_triggered_ability_without_event_does_not_match():
    ability = make_triggered_ability(
        source=DummyCard("SOURCE"),
        condition=EventKeyCondition("unit_died"),
    )

    assert not ability.matches(DummyState())


def test_triggered_ability_zone_check():
    ability = make_triggered_ability(
        source=DummyCard("SOURCE"),
        condition=EventKeyCondition("unit_died"),
        allowed_zones={ZoneType.GRAVEYARD},
    )

    assert ability.data.is_usable_in_zone(ZoneType.GRAVEYARD)
    assert not ability.data.is_usable_in_zone(ZoneType.BATTLEFIELD)


#----------------------------------------------------------------
# EventBus tests
#----------------------------------------------------------------

def test_event_bus_collects_matching_triggered_abilities():
    matching = make_triggered_ability(
        source=DummyCard("MATCHING"),
        condition=EventKeyCondition("unit_died"),
    )
    non_matching = make_triggered_ability(
        source=DummyCard("NON_MATCHING"),
        condition=EventKeyCondition("turn_start"),
    )
    event = GameEvent("unit_died")
    state = TriggerState(triggered_abilities=[matching, non_matching])
    bus = EventBus()

    triggers = bus.collect_triggered_abilities(state, [event])

    assert len(triggers) == 1
    assert triggers[0].source.id == "MATCHING"
    assert triggers[0].event == event
    assert state.requested_events == [event]


def test_event_bus_collects_from_multiple_events():
    first = make_triggered_ability(
        source=DummyCard("FIRST"),
        condition=EventKeyCondition("first_event"),
    )
    second = make_triggered_ability(
        source=DummyCard("SECOND"),
        condition=EventKeyCondition("second_event"),
    )
    state = TriggerState(triggered_abilities=[first, second])
    bus = EventBus()

    triggers = bus.collect_triggered_abilities(
        state,
        [
            GameEvent("first_event"),
            GameEvent("second_event"),
        ]
    )

    assert len(triggers) == 2
    assert {trigger.source.id for trigger in triggers} == {"FIRST", "SECOND"}


def test_event_bus_emit_stores_event():
    event = GameEvent("unit_died", payload={"unit": "A"})
    bus = EventBus()

    bus.emit(event, DummyState())

    assert bus.emitted_events == [event]


#----------------------------------------------------------------
# Triggered action generation tests
#----------------------------------------------------------------

def test_triggered_ability_generates_actions():
    source = DummyCard("SOURCE")
    target_a = DummyCard("A")
    target_b = DummyCard("B")
    effect = DummyEffect("damage")

    ability = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=make_effect_action(effect, candidates=[target_a, target_b]),
    )

    actions = ability.generate_actions(DummyState())

    assert len(actions) == 2
    assert all(isinstance(action, AbilityAction) for action in actions)
    assert all(action.source == source for action in actions)


def test_triggered_ability_action_intent_generates_operations():
    source = DummyCard("SOURCE")
    target = DummyCard("TARGET")
    effect = DummyEffect("damage")

    ability = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=make_effect_action(effect, candidates=[target]),
    )

    action = ability.generate_actions(DummyState())[0]
    cost_intent, action_intent = action.get_intents()

    assert cost_intent.generate_operations(DummyState()) == tuple()

    operations = action_intent.generate_operations(DummyState())

    assert len(operations) == 1
    assert operations[0].context.source == source
    assert operations[0].context.targets == {"target": {target: 1}}

    operations[0].execute(DummyState())

    assert len(effect.executed) == 1


def test_triggered_ability_preserves_uses_stack_flag():
    source = DummyCard("SOURCE")
    effect = DummyEffect("damage")

    ability = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=make_no_target_action(effect),
        uses_stack=False,
    )

    action = ability.generate_actions(DummyState())[0]
    cost_intent, action_intent = action.get_intents()

    assert not cost_intent.context.uses_stack
    assert not action_intent.context.uses_stack


#----------------------------------------------------------------
# TriggerResolver tests
#----------------------------------------------------------------

def test_trigger_resolver_pushes_trigger_intents_to_stack():
    player = TriggerPlayer("P1")
    source = DummyCard("SOURCE", owner=player)
    effect = DummyEffect("damage")
    trigger = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=make_no_target_action(effect),
    )
    state = TriggerState(players=[player], active_player=player)
    resolver = TriggerResolver()

    resolver.resolve(state, [trigger])

    assert len(state.stack.items) == 2
    assert state.stack.items[0].context.source == source
    assert state.stack.items[1].context.source == source


def test_trigger_resolver_orders_triggers_apnap():
    active_player = TriggerPlayer("ACTIVE")
    inactive_player = TriggerPlayer("INACTIVE")

    active_source = DummyCard("ACTIVE_SOURCE", owner=active_player)
    inactive_source = DummyCard("INACTIVE_SOURCE", owner=inactive_player)

    active_trigger = make_triggered_ability(
        source=active_source,
        condition=EventKeyCondition("unit_died"),
        action=make_no_target_action(DummyEffect("active_effect")),
    )
    inactive_trigger = make_triggered_ability(
        source=inactive_source,
        condition=EventKeyCondition("unit_died"),
        action=make_no_target_action(DummyEffect("inactive_effect")),
    )

    state = TriggerState(
        players=[active_player, inactive_player],
        active_player=active_player,
    )
    resolver = TriggerResolver()

    resolver.resolve(state, [inactive_trigger, active_trigger])

    pushed_sources = [intent.context.source for intent in state.stack.items]

    assert pushed_sources == [
        active_source,
        active_source,
        inactive_source,
        inactive_source,
    ]


def test_trigger_resolver_uses_controller_choice_for_multiple_actions():
    player = TriggerPlayer("P1")
    source = DummyCard("SOURCE", owner=player)
    first_target = DummyCard("FIRST")
    second_target = DummyCard("SECOND")
    effect = DummyEffect("damage")

    trigger = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=make_effect_action(effect, candidates=[first_target, second_target]),
    )
    state = TriggerState(players=[player], active_player=player)
    resolver = TriggerResolver()

    resolver.resolve(state, [trigger])

    assert len(player.controller.trigger_choices) == 1
    assert len(player.controller.trigger_choices[0]["actions"]) == 2

    action_intent = state.stack.items[1]
    operations = action_intent.generate_operations(state)

    assert operations[0].context.targets == {"target": {second_target: 1}}


def test_trigger_resolver_raises_when_trigger_generates_no_actions():
    player = TriggerPlayer("P1")
    source = DummyCard("SOURCE", owner=player)
    empty_action = SubAbilityDefinition(
        action_node=EffectActionNode("damage", {"target"}),
        slots={
            "target": make_slot("target", []),
        },
        effects={
            "damage": DummyEffect("damage"),
        }
    )
    trigger = make_triggered_ability(
        source=source,
        condition=EventKeyCondition("unit_died"),
        action=empty_action,
    )
    state = TriggerState(players=[player], active_player=player)
    resolver = TriggerResolver()

    try:
        resolver.resolve(state, [trigger])
        assert False
    except RuntimeError:
        pass
