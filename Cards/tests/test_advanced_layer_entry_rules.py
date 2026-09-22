"""CR 613.6, 613.8 and 614.12: interaction tests through live resolution."""
from game.ai.decision_maker import DecisionResult
from game.game_actions import PassPriorityAction
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from dataclasses import replace
from copy import copy

import pytest
from helper.query_system.query import EqQuery
from game.enums import CardType as T, ZoneType as Z, CounterType as C, Layer as L
from game.stat_type import (STAT_POWER as POWER, STAT_TOUGHNESS as TOUGHNESS, STAT_TYPES as TYPES,
                            STAT_KEYWORDS as KEYWORDS, STAT_STATIC_ABILITIES as STATIC)
from game.game_state import State, Player, Card, CardDefinition
from game.ai.decision_maker import ModularDecisionMaker as DecisionMaker
from game.game_state.continuous_rules import StaticContinuousRule
from game.game_state.layers import lose_all_abilities_modifiers
from game.game_state.modifier import (AddIntModifier, AddSetModifier, RemoveSetModifier, SetModifier,
    ContinuousEffectDefinition, ContinuousEffect, ContinuousEffectState, PermanentDuration, DynamicTargetingStrategy)
from game.game_state.registers.card_register import IK_KEY, IK_TYPE, IK_ZONE, IK_POWER
from game.target.target_spec import QueryTargetSpec
from game.game_actions.resolution.replacement_effects import prevent_damage, ReplacementResolver
from game.game_actions.resolution.entry_replacements import (enters_tapped, enters_with_counters,
                                                            enters_under_control, enters_as_copy)
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.data_structs.game_action import ResolutionContext, FixedExecutionPlan, ScheduledResolution
from game.operations.card_operations import MoveCardOperation, DamagePlayerOperation


class Controller(DecisionMaker):
    def decide_priority(self, request):
        state = request.state; player = request.player
        return DecisionResult(PassPriorityAction(player))


@pytest.fixture
def game():
    state = State([Player([], Controller(), name="Alice"), Player([], Controller(), name="Bob")])
    bus = EventBus()
    return state, ResolutionEngine(OperationExecutor(), bus), bus


UNIT = CardDefinition("Unit", types=frozenset({T.CREATURE}), power=2, toughness=4)
CREATURES = QueryTargetSpec(EqQuery(IK_TYPE, T.CREATURE) & EqQuery(IK_ZONE, Z.BATTLEFIELD))
ARTIFACTS = QueryTargetSpec(EqQuery(IK_TYPE, T.ARTIFACT) & EqQuery(IK_ZONE, Z.BATTLEFIELD))


def add(game, definition=UNIT, *, zone=Z.BATTLEFIELD, player=0):
    result = Card(definition, game[0].players[player])
    result.owner.add_card(result, zone)
    return result


def context(game, source=None):
    return ResolutionContext(source=source, controller=game[0].active_player)


def resolve(game, *operations):
    return game[1].resolve(game[0], ScheduledResolution(FixedExecutionPlan(list(operations)), context(game)))


def move(game, card, destination):
    return resolve(game, MoveCardOperation(context(game, card), card, destination))


def rule_source(game, key, spec, modifiers, *, creature=False):
    definition = replace(UNIT, name=key, types=frozenset({T.CREATURE} if creature else {T.ENCHANTMENT}),
                         continuous_effects=(StaticContinuousRule(key, spec, modifiers),))
    return add(game, definition)


def temporary(game, source, key, spec, modifiers):
    definition = ContinuousEffectDefinition(PermanentDuration(), source, game[0].time_stamp,
                                            DynamicTargetingStrategy(spec), modifiers)
    effect = ContinuousEffect(key, definition, ContinuousEffectState(set()))
    game[0].add_continuous_effect(effect)
    return effect


@pytest.mark.parametrize("reverse", [False, True])
def test_dependency_is_inferred_from_type_selection_not_timestamp(game, reverse):
    unit = add(game)
    definitions = [("landify", ARTIFACTS, {TYPES: (AddSetModifier(frozenset({T.LAND})),)}),
                   ("artifactify", CREATURES, {TYPES: (AddSetModifier(frozenset({T.ARTIFACT})),)})]
    for args in definitions[::-1] if reverse else definitions:
        rule_source(game, *args)
    game[0].synchronise_registers()
    assert unit.get_types(game[0]) == {T.CREATURE, T.ARTIFACT, T.LAND}


def test_dependency_probes_leave_no_events_or_real_mutations(game):
    unit = add(game)
    rule_source(game, "artifact", CREATURES, {TYPES: (AddSetModifier(frozenset({T.ARTIFACT})),)})
    rule_source(game, "land", ARTIFACTS, {TYPES: (AddSetModifier(frozenset({T.LAND})),)})
    revision, counters, original = unit.zone_revision, dict(unit.state.counters), unit.definition
    game[0].synchronise_registers()
    assert not game[2].emitted_events
    assert unit.zone_revision == revision and dict(unit.state.counters) == counters and unit.definition is original
    assert unit in game[0].query_cards(EqQuery(IK_TYPE, T.LAND))


@pytest.mark.parametrize("reverse", [False, True])
def test_characteristic_defining_effect_precedes_ordinary_effect(game, reverse):
    unit = add(game)
    spec = QueryTargetSpec(EqQuery(IK_KEY, unit.key))
    rules = [StaticContinuousRule("ordinary", spec, {TYPES: (SetModifier(frozenset({T.ARTIFACT})),)}),
             StaticContinuousRule("cda", spec, {TYPES: (SetModifier(frozenset({T.CREATURE})),)},
                                  characteristic_defining=True)]
    for rule in rules[::-1] if reverse else rules:
        add(game, CardDefinition(rule.key, types=frozenset({T.ENCHANTMENT}), continuous_effects=(rule,)))
    game[0].synchronise_registers()
    assert unit.get_types(game[0]) == {T.ARTIFACT}


def test_disappearing_dependency_is_not_retained_in_final_modifier_order(game):
    unit = add(game)
    temporary(game, unit, "a", ARTIFACTS, {TYPES: (AddSetModifier(frozenset({T.LAND})),)})
    definition = ContinuousEffectDefinition(PermanentDuration(), unit, game[0].time_stamp,
        DynamicTargetingStrategy(CREATURES), {TYPES: (SetModifier(frozenset({T.CREATURE, T.ARTIFACT})),)},
        depends_on=frozenset({"c"}))
    game[0].add_continuous_effect(ContinuousEffect("b", definition, ContinuousEffectState(set())))
    assert T.LAND in unit.get_types(game[0])
    temporary(game, unit, "c", CREATURES, {TYPES: (AddSetModifier(frozenset({T.ARTIFACT})),)})
    # C makes A independent of B. A now applies before B, which removes land.
    assert unit.get_types(game[0]) == {T.CREATURE, T.ARTIFACT}


@pytest.mark.parametrize("remover_first", [False, True])
def test_losing_abilities_prevents_static_pt_anthem_from_starting(game, remover_first):
    unit = add(game)
    def anthem():
        return rule_source(game, "anthem", CREATURES, {POWER: (AddIntModifier(5),)}, creature=True)
    def blank():
        return rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    if remover_first:
        remover, source = blank(), anthem()
    else:
        source, remover = anthem(), blank()
    game[0].synchronise_registers()
    assert not source.get_stat(STATIC, game[0]) and unit.get_power(game[0]) == 2
    move(game, remover, Z.GRAVEYARD)
    assert unit.get_power(game[0]) == 7 and "anthem" in source.get_stat(STATIC, game[0])


def test_effect_that_started_in_type_layer_continues_after_ability_loss(game):
    spec = QueryTargetSpec(EqQuery(IK_ZONE, Z.BATTLEFIELD))
    source = rule_source(game, "animate", spec, {TYPES: (AddSetModifier(frozenset({T.CREATURE})),),
                                                 POWER: (SetModifier(6),), TOUGHNESS: (SetModifier(6),)})
    rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    game[0].synchronise_registers()
    assert not source.get_stat(STATIC, game[0])
    assert source.get_power(game[0]) == 6 and source.get_toughness(game[0]) == 6


@pytest.mark.parametrize("blank_first", [False, True])
def test_same_layer_existence_dependency_suppresses_flying_granter(game, blank_first):
    unit = add(game)
    actions = [("flight", CREATURES, {KEYWORDS: (AddSetModifier(frozenset({"flying"})),)}, True),
               ("blank", CREATURES, lose_all_abilities_modifiers(), False)]
    for name, spec, mods, creature in actions[::-1] if blank_first else actions:
        rule_source(game, name, spec, mods, creature=creature)
    game[0].synchronise_registers()
    assert not unit.has_keyword(game[0], "flying")


def test_resolved_effect_is_independent_of_its_sources_abilities(game):
    unit = add(game)
    temporary(game, unit, "resolved-buff", CREATURES, {POWER: (AddIntModifier(3),)})
    rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    game[0].synchronise_registers()
    assert unit.get_power(game[0]) == 5


def test_blank_replacement_source_then_restore_does_not_refill_shield(game):
    rule = prevent_damage("shield", 3, total=True,
        predicate=lambda state, effect, op: op.target is effect.source.get_controller(state))
    source = add(game, replace(UNIT, replacement_effects=(rule,)))
    player = source.owner
    before = player.health
    resolve(game, DamagePlayerOperation(context(game), player, 2))
    assert player.health == before
    remover = rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    resolve(game, DamagePlayerOperation(context(game), player, 2))
    assert player.health == before - 2
    move(game, remover, Z.GRAVEYARD)
    resolve(game, DamagePlayerOperation(context(game), player, 2))
    assert player.health == before - 3


def test_losing_all_abilities_removes_intrinsic_basic_land_mana(game):
    from game.rules.lands import basic_land
    land = add(game, basic_land("Forest"))
    spec = QueryTargetSpec(EqQuery(IK_TYPE, T.LAND))
    blank = rule_source(game, "blank", spec, lose_all_abilities_modifiers())
    game[0].synchronise_registers()
    # PlayLand is an engine permission, not a removable printed mana ability.
    assert not land.get_activatable_ability_defs(game[0])
    move(game, blank, Z.GRAVEYARD)
    assert any(d.is_mana_ability for d in land.get_activatable_ability_defs(game[0]).values())


@pytest.mark.parametrize("origin", [Z.HAND, Z.DECK, Z.GRAVEYARD, Z.EXILE, Z.STACK])
def test_self_entry_tapped_and_counters_from_every_zone(game, origin):
    rules = (enters_tapped("tap"), enters_with_counters("counters", C.PLUS_ONE, 2))
    unit = add(game, replace(UNIT, replacement_effects=rules), zone=origin)
    assert move(game, unit, Z.BATTLEFIELD).success
    assert unit.is_tapped and unit.state.counters[C.PLUS_ONE] == 2 and unit.get_power(game[0]) == 4
    moves = [event for event in game[2].emitted_events if event.key == "card_moved"]
    assert len(moves) == 1 and moves[0].payload["from"] == origin.name
    assert not any(event.key == "card_tapped" for event in game[2].emitted_events)


def test_incoming_global_entry_effect_does_not_apply_to_itself(game):
    orb = add(game, CardDefinition("Orb", types=frozenset({T.ARTIFACT}),
        replacement_effects=(enters_tapped("global", self_only=False),)), zone=Z.HAND)
    move(game, orb, Z.BATTLEFIELD)
    assert not orb.is_tapped
    unit = add(game, zone=Z.HAND)
    move(game, unit, Z.BATTLEFIELD)
    assert unit.is_tapped


def test_humility_suppresses_incoming_self_entry_abilities(game):
    rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    unit = add(game, replace(UNIT, enters_tapped=True,
        replacement_effects=(enters_with_counters("counters", C.PLUS_ONE, 2),)), zone=Z.HAND)
    move(game, unit, Z.BATTLEFIELD)
    assert not unit.is_tapped and not unit.state.counters


def test_graveyard_ability_loss_does_not_suppress_battlefield_entry_ability(game):
    grave = QueryTargetSpec(EqQuery(IK_ZONE, Z.GRAVEYARD))
    rule_source(game, "jailer", grave, lose_all_abilities_modifiers())
    unit = add(game, replace(UNIT, enters_tapped=True), zone=Z.GRAVEYARD)
    game[0].synchronise_registers()
    assert not unit.get_stat(STATIC, game[0])
    move(game, unit, Z.BATTLEFIELD)
    assert unit.is_tapped


def test_entry_characteristics_include_existing_type_effects(game):
    rule_source(game, "artifacts", CREATURES, {TYPES: (AddSetModifier(frozenset({T.ARTIFACT})),)})
    def artifacts_only(state, effect, op):
        incoming, projected = op.entry_characteristics(state)
        return T.ARTIFACT in incoming.get_types(projected)
    game[0].replacement_rules.append(enters_tapped("artifact-tap", self_only=False, predicate=artifacts_only).bind())
    unit = add(game, zone=Z.HAND)
    move(game, unit, Z.BATTLEFIELD)
    assert unit.is_tapped


def test_entry_characteristics_include_own_static_type_effect(game):
    self_spec = QueryTargetSpec(lambda source, player, state: EqQuery(IK_KEY, source.key))
    animate = StaticContinuousRule("creature", self_spec, {TYPES: (AddSetModifier(frozenset({T.CREATURE})),),
                                                        POWER: (SetModifier(2),), TOUGHNESS: (SetModifier(2),)})
    incoming = add(game, CardDefinition("Artifact", types=frozenset({T.ARTIFACT}), continuous_effects=(animate,)), zone=Z.HAND)
    def creatures_only(state, effect, op):
        card, projected = op.entry_characteristics(state)
        return T.CREATURE in card.get_types(projected)
    game[0].replacement_rules.append(enters_tapped("creature-tap", self_only=False, predicate=creatures_only).bind())
    move(game, incoming, Z.BATTLEFIELD)
    assert incoming.is_tapped


def test_entry_projection_leaves_real_indexes_memberships_and_zones_untouched(game):
    source = rule_source(game, "buff", CREATURES, {POWER: (AddIntModifier(2),)})
    unit = add(game, zone=Z.HAND)
    game[0].synchronise_registers()
    cards = tuple(game[0].get_cards())
    snapshots = {c.key: (c.runtime_id, c.zone_revision, c.get_zone(), frozenset(c.state.active_cont_effects)) for c in cards}
    index_before = dict(game[0].card_register._snapshots)
    operation = MoveCardOperation(context(game), unit, Z.BATTLEFIELD)
    hypothetical, projected = operation.entry_characteristics(game[0])
    assert hypothetical.get_power(projected) == 4 and hypothetical.get_zone() == Z.BATTLEFIELD
    assert unit.get_power(game[0]) == 2 and unit.get_zone() == Z.HAND
    assert game[0].card_register._snapshots == index_before
    assert snapshots == {c.key: (c.runtime_id, c.zone_revision, c.get_zone(), frozenset(c.state.active_cont_effects)) for c in cards}
    assert not game[2].emitted_events and not game[0].stack.items


def test_copy_entry_acquires_copied_entry_replacements_and_restores_original_on_exit(game):
    target = add(game, replace(UNIT, name="Copied", enters_tapped=True,
        replacement_effects=(enters_with_counters("bonus", C.PLUS_ONE, 2),)))
    rule = enters_as_copy("copy", lambda state, effect, op: target)
    original = replace(UNIT, name="Original", power=1, replacement_effects=(rule,))
    clone = add(game, original, zone=Z.HAND)
    move(game, clone, Z.BATTLEFIELD)
    assert clone.name == "Copied" and clone.is_tapped and clone.state.counters[C.PLUS_ONE] == 2
    assert clone.get_power(game[0]) == 4 and clone.owner is game[0].active_player
    move(game, clone, Z.GRAVEYARD)
    assert clone.definition is original and clone.get_power(game[0]) == 1


def test_entry_control_change_is_visible_to_subsequent_replacements(game):
    bob = game[0].players[1]
    change = enters_under_control("control", lambda state, effect, op: bob)
    def bobs_only(state, effect, op):
        incoming, projected = op.entry_characteristics(state)
        return incoming.get_controller(projected) is bob
    game[0].replacement_rules.extend([enters_tapped("bob-tap", self_only=False, predicate=bobs_only).bind(), change.bind()])
    unit = add(game, zone=Z.HAND)
    move(game, unit, Z.BATTLEFIELD)
    assert unit.get_controller(game[0]) is bob and unit.is_tapped


def test_replacing_entry_destination_does_not_apply_entry_flags(game):
    from game.game_actions.resolution.replacement_effects import replace_zone
    unit = add(game, replace(UNIT, enters_tapped=True), zone=Z.HAND)
    game[0].replacement_rules.append(replace_zone("exile", Z.HAND, Z.BATTLEFIELD, Z.EXILE).bind())
    move(game, unit, Z.BATTLEFIELD)
    assert unit.get_zone() == Z.EXILE and not unit.is_tapped and not unit.state.counters


@pytest.mark.parametrize("count", [1, 2, 4])
def test_tokens_use_entry_replacements_before_simultaneous_creation(game, count):
    from game.rules.permanents import CreateTokenOperation
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import EntersBattlefieldCondition
    definition = replace(UNIT, replacement_effects=(enters_with_counters("bonus", C.PLUS_ONE, 2),),
        triggers=frozenset({TriggerAbilityDefinition(key="etb", condition=EntersBattlefieldCondition())}))
    game[0].replacement_rules.append(enters_tapped("all", self_only=False).bind())
    operation = CreateTokenOperation(context(game), definition, count)
    resolve(game, operation)
    assert len(operation.created) == count
    assert all(c.is_tapped and c.state.counters[C.PLUS_ONE] == 2 for c in operation.created)
    assert len(game[0].stack.items) == count * count


def test_new_tokens_global_replacement_does_not_affect_other_simultaneous_tokens(game):
    from game.rules.permanents import CreateTokenOperation
    operation = CreateTokenOperation(context(game), replace(UNIT,
        replacement_effects=(enters_tapped("global", self_only=False),)), 2)
    resolve(game, operation)
    assert all(not token.is_tapped for token in operation.created)
    later = add(game, zone=Z.HAND)
    move(game, later, Z.BATTLEFIELD)
    assert later.is_tapped


def test_token_creation_replaced_by_other_zone_creates_no_token(game):
    from game.rules.permanents import CreateTokenOperation
    from game.game_actions.resolution.replacement_effects import replace_zone
    game[0].replacement_rules.append(replace_zone("no-entry", None, Z.BATTLEFIELD, Z.EXILE).bind())
    operation = CreateTokenOperation(context(game), UNIT)
    resolve(game, operation)
    assert not operation.created and not game[0].get_cards() and not game[2].emitted_events


def test_aura_entry_uses_replacements_and_attaches_before_etb_snapshot(game):
    from game.enums import CardSubtype
    from game.rules.permanents import AttachOperation
    from game.game_actions.data_structs.ability import TriggerAbilityDefinition
    from game.game_actions.triggers.trigger_condition import EntersBattlefieldCondition
    target = add(game)
    aura = add(game, CardDefinition("Aura", types=frozenset({T.ENCHANTMENT}),
        subtypes=frozenset({CardSubtype.AURA}), replacement_effects=(enters_tapped("self-tap"),),
        triggers=frozenset({TriggerAbilityDefinition(key="etb", condition=EntersBattlefieldCondition(source_only=True))})), zone=Z.HAND)
    resolve(game, AttachOperation(context(game, aura), aura, target, entering=True))
    assert aura.is_tapped and aura.attached_to is target and len(game[0].stack.items) == 1


def test_replaced_aura_entry_does_not_attach(game):
    from game.enums import CardSubtype
    from game.rules.permanents import AttachOperation
    from game.game_actions.resolution.replacement_effects import replace_zone
    target = add(game)
    aura = add(game, CardDefinition("Aura", types=frozenset({T.ENCHANTMENT}),
        subtypes=frozenset({CardSubtype.AURA})), zone=Z.HAND)
    game[0].replacement_rules.append(replace_zone("exile", Z.HAND, Z.BATTLEFIELD, Z.EXILE).bind())
    resolve(game, AttachOperation(context(game, aura), aura, target, entering=True))
    assert aura.get_zone() == Z.EXILE and aura.attached_to is None and not target.state.attached


def test_entry_target_spec_uses_projected_indexes_without_targeting_restrictions(game):
    rule_source(game, "artifact", CREATURES, {TYPES: (AddSetModifier(frozenset({T.ARTIFACT})),)})
    game[0].replacement_rules.append(enters_tapped("artifact-entry", self_only=False, target_spec=ARTIFACTS).bind())
    incoming = add(game, replace(UNIT, keywords=frozenset({"shroud"})), zone=Z.HAND)
    move(game, incoming, Z.BATTLEFIELD)
    assert incoming.is_tapped


def test_stored_activation_is_rejected_after_ability_loss_but_stack_ability_survives(game):
    from game.game_actions.data_structs.ability import AbilityDefinition, SubAbilityDefinition
    from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
    from game.game_actions.permanent_effects import CreateTokenEffect
    from game.game_actions.resolution.action_processor import ActionProcessor
    from game.console.demo_game import build_action
    from game.enums import TurnPhase
    token = CreateTokenEffect("token", UNIT)
    ability = ActivatedAbilityDefinition(key="make", action_subdefs=(SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({token.key: frozenset()})), effects=frozenset({token})),))
    source = add(game, replace(UNIT, abilities=frozenset({ability})))
    game[0].turn.phase = TurnPhase.PRECOMBAT_MAIN
    selected = build_action(game[0], source.owner, f"activate {source.key} make")
    processor = ActionProcessor(game[1])
    assert processor.process(game[0], selected)[0].success
    rule_source(game, "blank", CREATURES, lose_all_abilities_modifiers())
    assert not processor.process(game[0], selected)[0].success
    assert game[1].resolve(game[0], game[0].stack.pop()).success
    assert any(card.is_token for card in game[0].get_cards())
