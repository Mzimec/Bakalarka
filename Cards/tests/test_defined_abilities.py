"""Executable examples of cards defined without adding rules to the console."""
from dataclasses import replace

import pytest

from game.console.console_commands import CommandError
from game.console.demo_game import build_action, create_demo_game, available_actions
from game.cards.demo_cards import LIGHTNING_BOLT_CAST, EMBER_ADEPT_TAP, PLAYER_TARGET
from game.enums import CardType, TurnPhase, ZoneType
from game.game_state import Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.card_effects import DamagePlayerEffect, MoveSourceEffect
from game.game_actions.data_structs.ability import SubAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.data_structs.game_action import AbilityExecutionPlan


def make_game():
    game = create_demo_game((ScriptedController(), ScriptedController()))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return game


def test_definitions_are_shared_and_building_does_not_generate_operations(monkeypatch):
    game = make_game()
    def unexpected(*args):
        pytest.fail("Effects must generate operations only at resolution")
    monkeypatch.setattr(DamagePlayerEffect, "to_operations", unexpected)
    monkeypatch.setattr(MoveSourceEffect, "to_operations", unexpected)
    first = build_action(game.state, game.state.active_player, "play bolt1 bob")
    second = build_action(game.state, game.state.active_player, "play bolt2 alice")
    assert first.ability is second.ability is LIGHTNING_BOLT_CAST
    assert isinstance(first.cost_generator, AbilityExecutionPlan)
    assert isinstance(first.action_generator, AbilityExecutionPlan)
    assert all(intent.context.ability is LIGHTNING_BOLT_CAST for intent in first.get_intents())
    assert first.source.get_zone() == ZoneType.HAND
    assert game.state.stack.is_empty()


def test_custom_spell_runs_from_definition_with_scoped_targets_and_ordered_effects():
    game = make_game()
    player, opponent = game.state.players
    damage = DamagePlayerEffect("custom_damage", 4, "target")
    cleanup = MoveSourceEffect("custom_cleanup", ZoneType.GRAVEYARD)
    definition = replace(LIGHTNING_BOLT_CAST, key="custom_cast", action_subdefs=(
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({damage.key: frozenset({"target"})})),
            effects=frozenset({damage}), slots=frozenset({PLAYER_TARGET})),
        SubAbilityDefinition(
            action_node=EffectActionNode(ImmutableEffectToSlotMap({cleanup.key: frozenset()})),
            effects=frozenset({cleanup})),
    ))
    card = Card(CardDefinition("Custom spell", types=frozenset({CardType.INSTANT}),
                               abilities=frozenset({definition})), player, key="alice-custom")
    player.add_card(card, ZoneType.HAND)
    action = build_action(game.state, player, "play custom bob")
    assert action.ability is definition
    assert game.loop.processor.process(game.state, action)[0].success
    assert card.get_zone() == ZoneType.STACK
    intent = game.state.stack.pop()
    operations = list(intent.action_resolution.generate_operations(game.state))
    assert len(operations) == 2
    assert operations[0].context.targets.get_targets_in_slot("target_0", "target") == frozenset({opponent})
    assert not operations[1].context.targets
    assert game.loop.processor.executor.resolve(game.state, intent).success
    assert opponent.health == 6
    assert card.get_zone() == ZoneType.GRAVEYARD


def test_stale_tap_cost_prevents_queuing_effect():
    game = make_game()
    player = game.state.active_player
    card = player.hand["alice-adept1"]
    player.move_card(card, ZoneType.BATTLEFIELD)
    action = build_action(game.state, player, "activate adept1 bob")
    card.is_tapped = True
    result = game.loop.processor.process(game.state, action)
    assert not result[0].success
    assert game.state.stack.is_empty()
    assert game.state.players[1].health == 10


def test_immediate_ability_resolves_after_paying_tap_cost():
    game = make_game()
    player = game.state.active_player
    definition = replace(EMBER_ADEPT_TAP, key="immediate", uses_stack=False)
    card = Card(CardDefinition("Immediate adept", types=frozenset({CardType.CREATURE}),
                               keywords=frozenset({"haste"}),
                               power=2, toughness=2, abilities=frozenset({definition})),
                player, key="alice-custom")
    player.add_card(card, ZoneType.BATTLEFIELD)
    action = build_action(game.state, player, "activate custom immediate bob")
    results = game.loop.processor.process(game.state, action)
    assert len(results) == 2 and all(result.success for result in results)
    assert card.is_tapped
    assert game.state.players[1].health == 8
    assert game.state.stack.is_empty()


def test_replaying_cast_action_rechecks_source_zone():
    game = make_game()
    action = build_action(game.state, game.state.active_player, "play bolt1 bob")
    assert game.loop.processor.process(game.state, action)[0].success
    assert not game.loop.processor.process(game.state, action)[0].success
    assert len(game.state.stack.items) == 1


def test_ai_and_console_use_the_same_definition_pipeline():
    game = make_game()
    actions = [option.action for option in available_actions(game.state, game.state.active_player)
               if option.action.source.key == "alice-bolt1"]
    assert len(actions) == 2
    assert all(action.ability is LIGHTNING_BOLT_CAST for action in actions)
    assert {target for action in actions for target in
            action.action_generator.binding.get_targets_in_slot("target_0", "target")} == set(game.state.players)


def test_targets_are_rechecked_using_live_player_query():
    game = make_game()
    action = build_action(game.state, game.state.active_player, "play bolt1 bob")
    game.loop.processor.process(game.state, action)
    game.state.players[1].health = 0
    result = game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert not result.success
    assert game.state.players[1].health == 0


def test_empty_definition_rejects_extra_target():
    game = make_game()
    player = game.state.active_player
    definition = replace(EMBER_ADEPT_TAP, key="empty", cost_subdefs=(), action_subdefs=())
    card = Card(CardDefinition("Empty", abilities=frozenset({definition})), player, key="alice-empty")
    player.add_card(card, ZoneType.BATTLEFIELD)
    with pytest.raises(CommandError, match="without a target"):
        build_action(game.state, player, "activate empty bob")
