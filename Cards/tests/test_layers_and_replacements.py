"""Ordering and interactions, using real registries, resolution and SBA."""
from game.ai.decision_maker import ReplacementOrderOption, DecisionResult, ReplacementAcceptanceOption, ReplacementAcceptanceRequest, ReplacementOption, ReplacementOrderRequest, ReplacementRequest
from game.game_actions import PassPriorityAction
from dataclasses import replace
from copy import copy
from itertools import permutations

import pytest
from helper.query_system.query import EqQuery
from game.enums import CardType as T, ZoneType as Z, Layer as L, CounterType as C, TurnPhase as P
from game.stat_type import STAT_POWER as POWER, STAT_TOUGHNESS as TOUGHNESS, STAT_TYPES as TYPES, STAT_KEYWORDS as KEYWORDS
from game.game_state import Card, CardDefinition, Player, State
from game.ai.decision_maker import ModularDecisionMaker as DecisionMaker
from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
    PermanentDuration, TimeStampDuration, TimeStamp, DynamicTargetingStrategy, SetModifier,
    AddIntModifier, MultiplyIntModifier, AddSetModifier, RemoveSetModifier)
from game.game_state.layers import LayeredModifier, SwitchPowerToughness, dependency_order
from game.game_state.continuous_rules import StaticContinuousRule
from game.game_state.registers.card_register import IK_KEY, IK_POWER, IK_TYPE, IK_ZONE
from game.target.target_spec import QueryTargetSpec
from game.game_actions.resolution.replacement_effects import (ReplacementEffectDefinition,
    ReplacementResolver, prevent_damage, damage_multiplier, replace_zone)
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.data_structs.game_action import ResolutionContext, ScheduledResolution, FixedExecutionPlan
from game.operations.card_operations import DamagePlayerOperation, DamageCreatureOperation, MoveCardOperation


class Controller(DecisionMaker):
    preferred = None
    accept = True

    def __init__(self):
        self.choices = []

    def decide_priority(self, request):
        state = request.state; player = request.player
        return DecisionResult(PassPriorityAction(player))

    def decide_replacement(self, request):
        state = request.state; player = request.player; operation = request.operation; effects = request.candidates
        self.choices.append((player, tuple(effect.key for effect in effects)))
        return DecisionResult(ReplacementOption(next((e for e in effects if e.key == self.preferred), effects[0])))

    def decide_replacement_acceptance(self, request):
        state = request.state; player = request.player; operation = request.operation; effect = request.effect
        return DecisionResult(ReplacementAcceptanceOption(self.accept))


@pytest.fixture
def game():
    state = State([Player([], Controller(), name="Alice"), Player([], Controller(), name="Bob")])
    bus = EventBus()
    return state, ResolutionEngine(OperationExecutor(), bus), bus


def card(game, *, definition=None, player=0):
    definition = definition or CardDefinition("Unit", types=frozenset({T.CREATURE}), power=2, toughness=5)
    result = Card(definition, game[0].players[player])
    result.owner.add_card(result, Z.BATTLEFIELD)
    return result


def context(game, source=None):
    return ResolutionContext(source=source, controller=game[0].active_player)


def resolve(game, *operations):
    return game[1].resolve(game[0], ScheduledResolution(FixedExecutionPlan(list(operations)), context(game)))


def effect(game, target, key, modifiers, *, depends=(), query=None, duration=None):
    state = game[0]
    definition = ContinuousEffectDefinition(duration or PermanentDuration(), target, state.time_stamp,
        DynamicTargetingStrategy(QueryTargetSpec(query or EqQuery(IK_KEY, target.key))), modifiers, frozenset(depends))
    result = ContinuousEffect(key, definition, ContinuousEffectState(set()))
    state.add_continuous_effect(result)
    return result


@pytest.mark.parametrize("order", tuple(permutations(("set", "add", "cda"))))
def test_pt_sublayers_override_timestamp_order(game, order):
    unit = card(game)
    mods = {"set": SetModifier(4), "add": AddIntModifier(3), "cda": LayeredModifier(SetModifier(10), L.PT_CDA)}
    for key in order:
        effect(game, unit, key, {POWER: [mods[key]]})
    assert unit.get_power(game[0]) == 7


@pytest.mark.parametrize("multiply_first", [False, True])
def test_add_and_multiply_share_sublayer_and_follow_timestamps(game, multiply_first):
    unit = card(game)
    mods = [MultiplyIntModifier(2), AddIntModifier(3)] if multiply_first else [AddIntModifier(3), MultiplyIntModifier(2)]
    for index, mod in enumerate(mods):
        effect(game, unit, str(index), {POWER: [mod]})
    assert unit.get_power(game[0]) == (7 if multiply_first else 10)


def test_dependency_overrides_timestamp_only_in_same_layer(game):
    unit = card(game)
    effect(game, unit, "multiply", {POWER: [MultiplyIntModifier(2)]}, depends=("add",))
    effect(game, unit, "add", {POWER: [AddIntModifier(3)]})
    assert unit.get_power(game[0]) == 10
    effect(game, unit, "set", {POWER: [SetModifier(4)]}, depends=("multiply",))
    assert unit.get_power(game[0]) == 14


def test_dependency_cycle_does_not_release_downstream_effect_early():
    items = ["downstream", "a", "b"]
    deps = {"downstream": {"a"}, "a": {"b"}, "b": {"a"}}
    assert list(dependency_order(items, key=lambda x: x, dependencies=lambda x: deps[x],
                                 timestamp=items.index)) == ["a", "downstream", "b"]


@pytest.mark.parametrize("switches", range(4))
@pytest.mark.parametrize("counters", range(3))
def test_switch_uses_opposite_value_after_modifiers_and_counters(game, switches, counters):
    unit = card(game)
    unit.state.counters[C.PLUS_ONE] = counters
    effect(game, unit, "buff", {POWER: [AddIntModifier(1)], TOUGHNESS: [AddIntModifier(2)]})
    for index in range(switches):
        effect(game, unit, f"switch{index}", {POWER: [SwitchPowerToughness()], TOUGHNESS: [SwitchPowerToughness()]})
    expected = (3 + counters, 7 + counters)
    assert (unit.get_power(game[0]), unit.get_toughness(game[0])) == (expected[::-1] if switches % 2 else expected)


def test_type_setting_and_adding_obey_same_layer_timestamp(game):
    unit = card(game)
    effect(game, unit, "artifact", {TYPES: [SetModifier(frozenset({T.ARTIFACT}))]})
    effect(game, unit, "creature", {TYPES: [AddSetModifier(frozenset({T.CREATURE}))]})
    assert unit.get_types(game[0]) == {T.ARTIFACT, T.CREATURE}
    effect(game, unit, "land", {TYPES: [SetModifier(frozenset({T.LAND}))]})
    assert unit.get_types(game[0]) == {T.LAND}


def test_later_layer_targets_see_earlier_layer_type_change(game):
    unit = card(game)
    effect(game, unit, "artifact-buff", {POWER: [AddIntModifier(5)]}, query=EqQuery(IK_TYPE, T.ARTIFACT))
    effect(game, unit, "artifact", {TYPES: [AddSetModifier(frozenset({T.ARTIFACT}))]})
    assert unit.get_power(game[0]) == 7
    assert unit in game[0].query_cards(EqQuery(IK_POWER, 7))


def test_self_invalidating_selection_applies_once_instead_of_oscillating(game):
    unit = card(game)
    effect(game, unit, "buff-two-power", {POWER: [AddIntModifier(1)]}, query=EqQuery(IK_POWER, 2))
    assert unit.get_power(game[0]) == 3
    game[0].notify_card_changed(unit)
    game[0].synchronise_registers()
    assert unit.get_power(game[0]) == 3


def test_multi_layer_effect_keeps_its_initial_recipients(game):
    unit = card(game)
    effect(game, unit, "animate", {TYPES: [RemoveSetModifier(frozenset({T.CREATURE}))],
                                  POWER: [SetModifier(8)]}, query=EqQuery(IK_TYPE, T.CREATURE))
    assert T.CREATURE not in unit.get_types(game[0]) and unit.get_power(game[0]) == 8


def test_keyword_removal_and_addition_are_live(game):
    unit = card(game)
    effect(game, unit, "flying", {KEYWORDS: [AddSetModifier(frozenset({"flying"}))]})
    assert unit.has_keyword(game[0], "flying")
    effect(game, unit, "remove", {KEYWORDS: [SetModifier(frozenset())]})
    assert not unit.has_keyword(game[0], "flying")


def test_static_definition_dependencies_and_source_departure(game):
    spec = QueryTargetSpec(EqQuery(IK_TYPE, T.CREATURE) & EqQuery(IK_ZONE, Z.BATTLEFIELD))
    definitions = (StaticContinuousRule("double", spec, {POWER: [MultiplyIntModifier(2)]}, frozenset({"plus"})),
                   StaticContinuousRule("plus", spec, {POWER: [AddIntModifier(3)]}))
    source = card(game, definition=CardDefinition("Anthem", types=frozenset({T.ARTIFACT}), continuous_effects=definitions))
    unit = card(game)
    game[0].synchronise_registers()
    assert unit.get_power(game[0]) == 10
    resolve(game, MoveCardOperation(context(game), source, Z.HAND))
    assert unit.get_power(game[0]) == 2


def test_layer_effect_expires_and_restores_indexes(game):
    unit = card(game)
    effect(game, unit, "until-cleanup", {POWER: [SetModifier(9)]},
           duration=TimeStampDuration(TimeStamp(game[0].turn.number, P.CLEANUP)))
    game[0].turn.phase = P.CLEANUP
    game[0].synchronise_registers()
    assert unit.get_power(game[0]) == 2 and unit in game[0].query_cards(EqQuery(IK_POWER, 2))


@pytest.mark.parametrize("first,damage", [("double", 7), ("shield", 4)])
def test_affected_player_chooses_replacement_order(game, first, damage):
    state, engine, _ = game
    target = state.players[1]
    target.decision_maker.preferred = first
    state.replacement_rules.extend([damage_multiplier("double", 2).bind(), prevent_damage("shield", 3).bind()])
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 5))
    assert target.health == before - damage
    assert target.decision_maker.choices[0][0] is target
    assert not state.active_player.decision_maker.choices


@pytest.mark.parametrize("damage", range(1, 7))
def test_damage_replacement_applies_once_without_mutating_original(game, damage):
    target = game[0].players[1]
    original = DamagePlayerOperation(context(game), target, damage)
    game[0].replacement_rules.append(damage_multiplier("double", 2).bind())
    before = target.health
    resolve(game, original)
    assert target.health == before - 2 * damage and original.amount == damage


def test_prevention_budget_is_spent_only_when_selected(game):
    target = game[0].players[1]
    target.decision_maker.preferred = "all"
    partial = prevent_damage("partial", 3, total=True).bind()
    game[0].replacement_rules.extend([partial, prevent_damage("all", 20).bind()])
    resolve(game, DamagePlayerOperation(context(game), target, 5))
    assert partial.remaining_budget == 3
    game[0].replacement_rules.pop()
    before = target.health
    game[1].resolve_simultaneous(game[0], [DamagePlayerOperation(context(game), target, 2),
                                         DamagePlayerOperation(context(game), target, 3)])
    assert partial.remaining_budget == 0 and target.health == before - 2


@pytest.mark.parametrize("accept", [False, True])
def test_optional_replacement_acceptance(game, accept):
    target = game[0].players[1]
    target.decision_maker.accept = accept
    replacement = prevent_damage("optional", 10, optional=True, uses=1).bind()
    game[0].replacement_rules.append(replacement)
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 3))
    assert target.health == before - (0 if accept else 3)
    assert replacement.remaining_uses == (0 if accept else 1)


def test_mandatory_priority_precedes_players_preferred_effect(game):
    target = game[0].players[1]
    target.decision_maker.preferred = "double"
    game[0].replacement_rules.extend([damage_multiplier("double", 2).bind(),
                                     prevent_damage("self", 3, priority=0).bind()])
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 5))
    assert target.health == before - 4 and not target.decision_maker.choices


def test_static_replacement_resets_on_blink_and_follows_controller(game):
    definition = prevent_damage("shield", 3, total=True,
        predicate=lambda state, effect, op: op.target is effect.source.get_controller(state))
    source = card(game, definition=CardDefinition("Shield", types=frozenset({T.ARTIFACT}), replacement_effects=(definition,)))
    before = source.owner.health
    resolve(game, DamagePlayerOperation(context(game), source.owner, 4))
    assert source.owner.health == before - 1
    resolve(game, MoveCardOperation(context(game), source, Z.EXILE), MoveCardOperation(context(game), source, Z.BATTLEFIELD))
    source.set_controller(game[0].players[1])
    target = game[0].players[1]
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 4))
    assert target.health == before - 1
    resolve(game, MoveCardOperation(context(game), source, Z.HAND), DamagePlayerOperation(context(game), target, 4))
    assert target.health == before - 5


def test_sba_death_replaced_by_exile_has_no_dies_trigger(game):
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import DiesCondition
    unit = card(game, definition=CardDefinition("Unit", types=frozenset({T.CREATURE}), power=2, toughness=2,
        triggers=frozenset({TriggerAbilityDefinition(key="dies", condition=DiesCondition(source_only=True))})))
    game[0].replacement_rules.append(replace_zone("exile", Z.BATTLEFIELD, Z.GRAVEYARD, Z.EXILE).bind())
    resolve(game, DamageCreatureOperation(context(game), unit, 2))
    assert unit.get_zone() == Z.EXILE and not game[0].stack.items
    moves = [event for event in game[2].emitted_events if event.key == "card_moved"]
    assert len(moves) == 1 and moves[0].payload["to"] == "EXILE"


def test_applicability_is_rechecked_after_destination_changes(game):
    unit = card(game)
    game[0].replacement_rules.extend([
        replace_zone("exile-to-hand", Z.BATTLEFIELD, Z.EXILE, Z.HAND).bind(),
        replace_zone("grave-to-exile", Z.BATTLEFIELD, Z.GRAVEYARD, Z.EXILE).bind()])
    resolve(game, MoveCardOperation(context(game), unit, Z.GRAVEYARD))
    assert unit.get_zone() == Z.HAND


def test_split_event_inherits_applied_effects(game):
    target = game[0].players[1]
    def split(state, effect, operation):
        first, second = copy(operation), copy(operation)
        first.amount, second.amount = 1, operation.amount - 1
        return (first, second)
    game[0].replacement_rules.append(ReplacementEffectDefinition("split", lambda s, e, o: isinstance(o, DamagePlayerOperation) and o.amount > 1, split).bind())
    changed = ReplacementResolver().replace(game[0], [DamagePlayerOperation(context(game), target, 5)])
    assert [op.amount for op in changed] == [1, 4]


def test_unpreventable_damage_does_not_consume_shield(game):
    target = game[0].players[1]
    shield = prevent_damage("shield", 3, total=True).bind()
    game[0].replacement_rules.append(shield)
    operation = DamagePlayerOperation(context(game), target, 2)
    operation.unpreventable = True
    before = target.health
    resolve(game, operation)
    assert target.health == before - 2 and shield.remaining_budget == 3


def test_replacement_duration_expires(game):
    target = game[0].players[1]
    game[0].replacement_rules.append(prevent_damage("shield", 5,
        duration=TimeStampDuration(TimeStamp(game[0].turn.number, P.CLEANUP))).bind())
    game[0].turn.phase = P.CLEANUP
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 3))
    assert target.health == before - 3


def test_replacement_of_damage_preserves_lifelink_snapshot(game):
    source = card(game, definition=CardDefinition("Link", types=frozenset({T.CREATURE}), power=2, toughness=2,
                                               keywords=frozenset({"lifelink"})))
    game[0].replacement_rules.append(damage_multiplier("double", 2).bind())
    before = source.owner.health
    resolve(game, DamagePlayerOperation(context(game, source), game[0].players[1], 2))
    assert source.owner.health == before + 4


def test_resolved_shield_outlives_spell_source(game):
    from game.game_actions.replacement_effects import InstallReplacementOperation
    source = card(game, definition=CardDefinition("Shield spell", types=frozenset({T.INSTANT})))
    target = game[0].players[1]
    resolve(game, InstallReplacementOperation(context(game, source), prevent_damage("shield", 3, total=True)),
            MoveCardOperation(context(game), source, Z.GRAVEYARD))
    before = target.health
    resolve(game, DamagePlayerOperation(context(game), target, 5))
    assert target.health == before - 2


def test_simultaneous_replacement_choices_use_apnap(game):
    state = game[0]
    calls = []
    def choose(state, player, operation, effects):
        calls.append(player)
        return effects[0]
    for player in state.players:
        player.decision_maker.decide_replacement = lambda request: DecisionResult(ReplacementOption(
            choose(request.state, request.player, request.operation, request.candidates)
        ))
    state.replacement_rules.extend([prevent_damage("first", 1).bind(), prevent_damage("second", 1).bind()])
    game[1].resolve_simultaneous(state, [DamagePlayerOperation(context(game), state.players[1], 3),
                                       DamagePlayerOperation(context(game), state.players[0], 3)])
    assert calls == list(state.players)


def test_player_orders_simultaneous_damage_competing_for_shield(game):
    target = card(game)
    player = target.owner
    player.decision_maker.decide_replacement_order = lambda request: DecisionResult(ReplacementOrderOption(
        tuple(reversed(request.candidates))
    ))
    game[0].replacement_rules.append(prevent_damage("shared", 3, total=True).bind())
    before = player.health
    game[1].resolve_simultaneous(game[0], [DamagePlayerOperation(context(game), player, 3),
                                         DamageCreatureOperation(context(game), target, 5)])
    assert player.health == before - 3 and target.state.damage_marked == 2
    assert target.get_zone() == Z.BATTLEFIELD


def test_replaced_draw_does_not_emit_draw_event(game):
    from game.operations.card_operations import DrawCardOperation
    source = card(game)
    source.owner.move_card(source, Z.DECK)
    def draw_to_exile(state, effect, operation):
        top = next(reversed(operation.context.controller.deck.values()))
        return (MoveCardOperation(operation.context, top, Z.EXILE),)
    game[0].replacement_rules.append(ReplacementEffectDefinition("draw-exile",
        lambda state, effect, op: isinstance(op, DrawCardOperation) and bool(op.context.controller.deck),
        draw_to_exile).bind())
    resolve(game, DrawCardOperation(context(game)))
    assert source.get_zone() == Z.EXILE
    assert not any(event.key == "card_drawn" for event in game[2].emitted_events)


def test_console_replacement_choices_reject_invalid_input(game):
    from game.console.demo_game import ConsoleDecisionMaker
    inputs = iter(["0", "2", "maybe", "no", "1 1", "2 1"])
    console = ConsoleDecisionMaker(read=lambda _: next(inputs), write=lambda _: None)
    player = game[0].active_player
    operations = (DamagePlayerOperation(context(game), player, 2), DamagePlayerOperation(context(game), player, 3))
    effects = (prevent_damage("first", 3, total=True).bind(), damage_multiplier("second", 2).bind())
    game[0].replacement_rules.extend(effects)
    assert console.decide(ReplacementRequest(game[0], player, operations[0], effects)).value.selected is effects[1]
    assert not console.decide(ReplacementAcceptanceRequest(game[0], player, operations[0], effects[0])).value.accept
    assert console.decide(ReplacementOrderRequest(game[0], player, operations)).value.items == operations[::-1]


def test_install_replacement_effect_factory_runs_at_resolution(game):
    from game.game_actions.replacement_effects import InstallReplacementEffect
    from game.game_actions.data_structs.ability import EffectSequence, EffectBinding
    from game.game_actions.data_structs.game_action import AbilityExecutionPlan
    from game.target.target_resolver import TargetBinding
    calls = []
    def factory(state, context):
        calls.append(state.turn.phase)
        return prevent_damage("shield", 2, total=True)
    install = InstallReplacementEffect("install", factory)
    plan = AbilityExecutionPlan(EffectSequence((EffectBinding(install, frozenset()),)), TargetBinding().to_immutable())
    assert not calls
    game[1].resolve(game[0], ScheduledResolution(plan, context(game)))
    assert calls == [game[0].turn.phase]
    assert game[0].replacement_rules[0].remaining_budget == 2
