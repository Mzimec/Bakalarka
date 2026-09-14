"""Current trigger API: real zones, event-time capture, priority, and choices."""
from dataclasses import replace

import pytest

from game.enums import CardType, TurnPhase, ZoneType
from game.game_state import Card, CardDefinition, Player, State
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import TriggerAbility, TriggerAbilityDefinition, SubAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.game_action import (
    AbilityAction, FixedExecutionPlan, ResolutionContext, ScheduledResolution)
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus, GameEvent, TriggerProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.triggers.trigger_condition import (
    DamageDealtCondition, DiesCondition, EntersBattlefieldCondition, EventKeyCondition,
    LeavesBattlefieldCondition, SpellCastCondition, StepCondition, ZoneChangeCondition)
from game.operations.card_operations import MoveCardOperation, TapCardOperation


def make_state(player_count=2, active=0, controllers=None):
    controllers = controllers or [ScriptedController() for _ in range(player_count)]
    return State([Player([], controller, name=f"Player {i}") for i, controller in enumerate(controllers)], active)


def add_card(state, key, triggers=(), *, player=None, zone=ZoneType.BATTLEFIELD, creature=True):
    player = player or state.active_player
    card = Card(CardDefinition(key, triggers=frozenset(triggers), power=2 if creature else None,
                               toughness=2 if creature else None,
                               types=frozenset({CardType.CREATURE if creature else CardType.ARTIFACT})), player, key=key)
    player.add_card(card, zone)
    return card


def trigger_def(condition=None, key="trigger", **kwargs):
    return TriggerAbilityDefinition(key=key, condition=condition or EventKeyCondition("test_event"), **kwargs)


def context(card):
    return ResolutionContext(source=card, controller=card.get_controller(card.owner.game_state))


def engine():
    bus = EventBus()
    return ResolutionEngine(OperationExecutor(), bus), bus


def run_operations(state, operations, resolver=None):
    resolver = resolver or engine()[0]
    result = resolver.resolve(state, ScheduledResolution(FixedExecutionPlan(operations), ResolutionContext()))
    assert result.success
    return result


class RecordOperation(Operation):
    def __init__(self, context, output):
        super().__init__(context)
        self.output = output

    def execute(self, state):
        self.output.append(self.context.trigger_event)
        return []


class RecordEffect(Effect):
    def __init__(self, output):
        super().__init__("record")
        self.output = output

    def to_operations(self, state, context):
        yield RecordOperation(context, self.output)

    def get_info(self):
        return "Record the triggering event."


def recording_trigger(output, **kwargs):
    effect = RecordEffect(output)
    action = SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()})),
        effects=frozenset({effect}))
    return trigger_def(action_subdefs=(action,), **kwargs)


def test_event_payload_is_an_immutable_copy():
    payload = {"amount": 3}
    event = GameEvent("damage_dealt", payload=payload)
    payload["amount"] = 99
    assert event.payload["amount"] == 3
    with pytest.raises(TypeError):
        event.payload["amount"] = 4


@pytest.mark.parametrize("actual", ["test_event", "different", "card_tapped", "spell_cast"])
def test_trigger_matches_only_its_event(actual):
    state = make_state()
    source = add_card(state, "source")
    ability = TriggerAbility(trigger_def(), source, state.active_player, GameEvent(actual))
    assert ability.matches(state) == (actual == "test_event")
    assert not TriggerAbility(trigger_def(), source, state.active_player).matches(state)


def test_bus_collects_multiple_events_and_keeps_compatibility_names():
    state = make_state()
    add_card(state, "source", [trigger_def()])
    events = [GameEvent("test_event"), GameEvent("other"), GameEvent("test_event")]
    bus = EventBus()
    for method in (bus.collect_trigger_abilities, bus.collect_triggered_abilities, bus.collect_trigger_actions):
        assert len(method(state, events)) == 2


@pytest.mark.parametrize("origin", list(ZoneType))
@pytest.mark.parametrize("destination", list(ZoneType))
def test_zone_change_predicate_matrix(origin, destination):
    event = GameEvent("card_moved", payload={"from": origin.name, "to": destination.name})
    changed = origin != destination
    assert ZoneChangeCondition().matches(None, event) == changed
    assert DiesCondition().matches(None, event) == (origin == ZoneType.BATTLEFIELD
                                                     and destination == ZoneType.GRAVEYARD and changed)
    assert LeavesBattlefieldCondition().matches(None, event) == (origin == ZoneType.BATTLEFIELD and changed)
    assert EntersBattlefieldCondition().matches(None, event) == (destination == ZoneType.BATTLEFIELD and changed)


def test_own_enters_battlefield_trigger_uses_new_zone():
    state = make_state()
    source = add_card(state, "source", [trigger_def(EntersBattlefieldCondition(source_only=True))], zone=ZoneType.HAND)
    run_operations(state, [MoveCardOperation(context(source), source, ZoneType.BATTLEFIELD)])
    assert len(state.stack.items) == 1
    assert state.stack.items[0].action_resolution.context.source is source


@pytest.mark.parametrize("destination", [ZoneType.GRAVEYARD, ZoneType.HAND, ZoneType.EXILE])
def test_own_leave_trigger_survives_source_changing_zone(destination):
    state = make_state()
    source = add_card(state, "source", [trigger_def(LeavesBattlefieldCondition(source_only=True))])
    result = run_operations(state, [MoveCardOperation(context(source), source, destination)])
    assert len(state.stack.items) == 1
    trigger = result.generated_events[0].triggered_abilities[0]
    assert trigger.source_last_known.zone == ZoneType.BATTLEFIELD
    assert trigger.source_last_known.controller is state.active_player
    assert trigger.event.payload["last_known"].types == frozenset({CardType.CREATURE})


def test_dies_requires_creature_and_battlefield_to_graveyard():
    state = make_state()
    add_card(state, "watcher", [trigger_def(DiesCondition())])
    artifact = add_card(state, "artifact", creature=False)
    creature = add_card(state, "creature")
    run_operations(state, [MoveCardOperation(context(artifact), artifact, ZoneType.GRAVEYARD),
                           MoveCardOperation(context(creature), creature, ZoneType.EXILE)])
    assert state.stack.is_empty()
    state.active_player.move_card(creature, ZoneType.BATTLEFIELD)
    run_operations(state, [MoveCardOperation(context(creature), creature, ZoneType.GRAVEYARD)])
    assert len(state.stack.items) == 1


def test_from_anywhere_graveyard_trigger_uses_after_event_eligibility():
    state = make_state()
    ability = trigger_def(ZoneChangeCondition(destination=ZoneType.GRAVEYARD, source_only=True),
                          allowed_zones=frozenset({ZoneType.GRAVEYARD}))
    source = add_card(state, "source", [ability])
    run_operations(state, [MoveCardOperation(context(source), source, ZoneType.GRAVEYARD)])
    assert len(state.stack.items) == 1


def test_event_time_capture_does_not_lose_earlier_trigger_when_source_leaves_later():
    state = make_state()
    source = add_card(state, "source", [trigger_def(EventKeyCondition("card_tapped", source_only=True))])
    run_operations(state, [TapCardOperation(context(source), source),
                           MoveCardOperation(context(source), source, ZoneType.GRAVEYARD)])
    assert len(state.stack.items) == 1
    assert state.stack.items[0].action_resolution.context.trigger_event.key == "card_tapped"


def test_new_watcher_cannot_retroactively_see_earlier_event():
    state = make_state()
    source = add_card(state, "source")
    watcher = add_card(state, "watcher", [trigger_def(EventKeyCondition("card_tapped"))], zone=ZoneType.HAND)
    run_operations(state, [TapCardOperation(context(source), source),
                           MoveCardOperation(context(watcher), watcher, ZoneType.BATTLEFIELD)])
    assert state.stack.is_empty()


def test_simultaneous_deaths_allow_watcher_to_see_every_dying_creature():
    state = make_state()
    watcher = add_card(state, "watcher", [trigger_def(DiesCondition())])
    other = add_card(state, "other")
    watcher.state.damage_marked = other.state.damage_marked = 2
    resolver, _ = engine()
    resolver.settle(state)
    assert watcher.get_zone() == other.get_zone() == ZoneType.GRAVEYARD
    assert len(state.stack.items) == 2
    assert {item.action_resolution.context.trigger_event.source for item in state.stack.items} == {watcher, other}


def test_another_creature_dies_excludes_watcher_itself_in_batch():
    state = make_state()
    watcher = add_card(state, "watcher", [trigger_def(DiesCondition(another=True))])
    other = add_card(state, "other")
    operations = [MoveCardOperation(context(card), card, ZoneType.GRAVEYARD) for card in (watcher, other)]
    events = OperationExecutor().execute_batch(state, operations)
    TriggerProcessor().process(state, EventBus().collect_trigger_abilities(state, events))
    assert len(state.stack.items) == 1
    assert state.stack.items[0].action_resolution.context.trigger_event.source is other


def test_stolen_source_trigger_keeps_controller_from_before_death():
    state = make_state()
    source = add_card(state, "source", [trigger_def(DiesCondition(source_only=True))])
    source.set_controller(state.players[1])
    run_operations(state, [MoveCardOperation(context(source), source, ZoneType.GRAVEYARD)])
    assert state.stack.items[0].action_resolution.context.controller is state.players[1]


def test_controlled_creature_dies_uses_previous_controller():
    state = make_state()
    add_card(state, "watcher", [trigger_def(DiesCondition(controlled_only=True))])
    stolen = add_card(state, "stolen", player=state.players[1])
    stolen.set_controller(state.active_player)
    run_operations(state, [MoveCardOperation(context(stolen), stolen, ZoneType.GRAVEYARD)])
    assert len(state.stack.items) == 1


@pytest.mark.parametrize("active", [0, 1, 2])
def test_apnap_order_rotates_to_active_player(active):
    state = make_state(3, active)
    for index, player in enumerate(state.players):
        add_card(state, f"source{index}", [trigger_def(key=f"trigger{index}")], player=player)
    triggers = EventBus().collect_trigger_abilities(state, [GameEvent("test_event")])
    TriggerProcessor().process(state, list(reversed(triggers)))
    assert [item.key for item in state.stack.items] == [f"trigger{(active + i) % 3}" for i in range(3)]
    assert all(not item.action_resolution.context.is_cost for item in state.stack.items)


def test_controller_selects_order_and_no_fake_cost_entries_are_pushed():
    class ReverseController(ScriptedController):
        def order_triggers(self, state, triggers):
            return reversed(triggers)
    state = make_state(controllers=[ReverseController(), ScriptedController()])
    source = add_card(state, "source")
    triggers = [TriggerAbility(trigger_def(key=key), source, state.active_player, GameEvent("test_event"))
                for key in ("one", "two", "three")]
    TriggerProcessor().process(state, triggers)
    assert [item.key for item in state.stack.items] == ["three", "two", "one"]


def test_invalid_controller_order_is_rejected():
    class BadController(ScriptedController):
        def order_triggers(self, state, triggers):
            return []
    state = make_state(controllers=[BadController(), ScriptedController()])
    source = add_card(state, "source", [trigger_def()])
    with pytest.raises(ValueError, match="every pending trigger"):
        TriggerProcessor().process(state, state.get_triggered_abilities(GameEvent("test_event")))


def test_trigger_event_reaches_resolving_effect_after_source_leaves():
    output = []
    state = make_state()
    source = add_card(state, "source", [recording_trigger(output, condition=DiesCondition(source_only=True))])
    resolver, _ = engine()
    run_operations(state, [MoveCardOperation(context(source), source, ZoneType.GRAVEYARD)], resolver)
    resolver.resolve(state, state.stack.pop())
    assert len(output) == 1 and output[0].source is source


@pytest.mark.parametrize("initial,at_resolution,expected", [(False, False, 0), (False, True, 0),
                                                            (True, False, 0), (True, True, 1)])
def test_intervening_if_checked_at_trigger_time_and_resolution(initial, at_resolution, expected):
    output = []
    state = make_state()
    state.trigger_enabled = initial
    definition = recording_trigger(output, condition=EventKeyCondition("card_tapped"),
                                    intervening_if=lambda state, event: state.trigger_enabled)
    source = add_card(state, "source", [definition])
    resolver, _ = engine()
    run_operations(state, [TapCardOperation(context(source), source)], resolver)
    assert len(state.stack.items) == int(initial)
    state.trigger_enabled = at_resolution
    if state.stack.items:
        assert resolver.resolve(state, state.stack.pop()).success
    assert len(output) == expected


def test_cost_triggers_wait_until_ability_is_on_stack():
    state = make_state()
    source = add_card(state, "source", [trigger_def(DiesCondition(source_only=True))])
    action = AbilityAction("activated", source, FixedExecutionPlan([]),
                           FixedExecutionPlan([MoveCardOperation(context(source), source, ZoneType.GRAVEYARD)]))
    resolver, _ = engine()
    assert all(result.success for result in ActionProcessor(resolver).process(state, action))
    assert [item.key for item in state.stack.items] == ["activated", "trigger"]


def test_sba_and_trigger_dispatch_do_not_interrupt_cost_and_immediate_effect():
    observed = []
    class HealingOperation(Operation):
        def execute(self, state):
            observed.append(self.context.source.get_zone())
            self.context.source.state.damage_marked = 0
            return []
    state = make_state()
    source = add_card(state, "source")
    source.state.damage_marked = 2
    action = AbilityAction("immediate", source, FixedExecutionPlan([HealingOperation(context(source))]),
                           FixedExecutionPlan([]), uses_stack=False)
    resolver, _ = engine()
    ActionProcessor(resolver).process(state, action)
    assert observed == [ZoneType.BATTLEFIELD]
    assert source.get_zone() == ZoneType.BATTLEFIELD


def test_deferred_turn_event_is_flushed_once_at_priority():
    state = make_state()
    add_card(state, "source", [trigger_def(StepCondition(TurnPhase.UPKEEP))])
    event = GameEvent("phase_started", controller=state.active_player, payload={"phase": "UPKEEP"})
    state._deferred_events.append(event)
    resolver, bus = engine()
    resolver.settle(state)
    resolver.settle(state)
    assert len(state.stack.items) == 1
    assert [emitted.key for emitted in bus.emitted_events] == ["phase_started"]


@pytest.mark.parametrize("amount", [0, 1, 3])
@pytest.mark.parametrize("combat", [False, True])
def test_combat_damage_condition_excludes_zero_and_noncombat(amount, combat):
    event = GameEvent("damage_dealt", payload={"amount": amount, "combat": combat})
    assert DamageDealtCondition(combat_only=True).matches(None, event) == (amount > 0 and combat)


def test_spell_cast_condition_does_not_trigger_on_land_or_zone_change():
    condition = SpellCastCondition()
    assert condition.matches(None, GameEvent("spell_cast"))
    assert not condition.matches(None, GameEvent("land_played"))
    assert not condition.matches(None, GameEvent("card_moved", payload={"to": "STACK"}))
