"""Forced choices, early feasibility checks and canonical global references."""
from dataclasses import replace
import pytest

from game.console.command_session import CommandSession
from game.console.console_commands import CommandError, card_reference, resolve_card
from game.console.demo_game import create_demo_game, build_action, ConsoleDecisionMaker, _resolve_target
from game.enums import TurnPhase, ZoneType
from game.game_loop.minimal_game import ScriptedController
from game.game_state import Card, CardDefinition
from game.cards.demo_cards import CHOICE_CAST, CREATURE_TARGETS, SCORCH, DRAW, TO_GRAVEYARD
from game.game_actions.data_structs.ability import SubAbilityDefinition
from game.game_actions.data_structs.action_node import OrActionNode, EffectActionNode, ImmutableEffectToSlotMap


def make_game():
    game = create_demo_game((ScriptedController(), ScriptedController()), expanded=True)
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return game


def run_session(game, command, inputs):
    iterator = iter(inputs)
    output, prompts = [], []
    def read(prompt):
        prompts.append(prompt)
        return next(iterator)
    action = CommandSession(game.state, game.state.active_player, command, read, output.append).run()
    return action, prompts, output


def test_impossible_activation_returns_before_requesting_input():
    game = make_game()
    action, prompts, output = run_session(game, "activate", [])
    assert action is None and prompts == []
    assert any("Cannot activate" in line for line in output)


def test_only_card_ability_and_empty_targets_cost_are_automatic():
    game = make_game()
    player = game.state.active_player
    stone = player.hand["alice-stone1"]
    player.move_card(stone, ZoneType.BATTLEFIELD)
    action, prompts, output = run_session(game, "activate", ["confirm"])
    assert prompts == ["activate/confirm> "]
    assert action.source is stone
    assert not stone.is_tapped and not player.mana_pool
    assert any(f"Selected card: {card_reference(stone)}" in line for line in output)
    assert any("Cost: Tap" in line for line in output)


def test_tapped_source_is_not_a_legal_activation_choice():
    game = make_game()
    player = game.state.active_player
    stone = player.hand["alice-stone1"]
    player.move_card(stone, ZoneType.BATTLEFIELD)
    stone.is_tapped = True
    action, prompts, _ = run_session(game, "activate", [])
    assert action is None and not prompts


def test_unpayable_spells_and_spells_without_targets_not_offered():
    game = make_game()
    session = CommandSession(game.state, game.state.active_player, "play", None, None)
    keys = {card.key for card in session.choices("card")}
    assert "alice-blast1" not in keys
    assert "alice-flame1" not in keys
    assert "alice-bolt1" in keys
    assert "bob-bolt1" not in keys


def test_unique_target_group_is_selected_and_back_skips_forced_stages():
    game = make_game()
    player = game.state.active_player
    beast = player.hand["alice-beast1"]
    player.move_card(beast, ZoneType.BATTLEFIELD)
    action, prompts, output = run_session(game, "play", ["alice-flame1", "back", "alice-growth1", "confirm"])
    assert prompts == ["play/card> ", "play/confirm> ", "play/card> ", "play/confirm> "]
    assert action.source.key == "alice-growth1"
    assert any(f"Selected targets: {card_reference(beast)}" in line for line in output)


def test_one_feasible_mode_is_selected_even_with_two_declared_modes():
    game = make_game()
    definition = replace(CHOICE_CAST, action_subdefs=(SubAbilityDefinition(
        action_node=OrActionNode((
            EffectActionNode(ImmutableEffectToSlotMap({SCORCH.key: frozenset({"target"})})),
            EffectActionNode(ImmutableEffectToSlotMap({DRAW.key: frozenset(), TO_GRAVEYARD.key: frozenset()})),
        )), effects=frozenset({SCORCH, DRAW, TO_GRAVEYARD}), slots=frozenset({CREATURE_TARGETS})),))
    player = game.state.active_player
    player.add_card(Card(CardDefinition("Choice test", abilities=frozenset({definition})),
                         player, key="test-choice"), ZoneType.HAND)
    action, prompts, output = run_session(game, "play", ["test-choice", "confirm"])
    assert prompts == ["play/card> ", "play/confirm> "]
    assert any("Selected mode: 2" in line for line in output)
    assert action is not None


def test_feasibility_checks_never_materialize_operations(monkeypatch):
    game = make_game()
    def fail(*args):
        pytest.fail("Checking command choices must not create operations")
    monkeypatch.setattr("game.game_actions.card_effects.MoveSourceEffect.to_operations", fail)
    monkeypatch.setattr("game.game_actions.card_effects.DamagePlayerEffect.to_operations", fail)
    action, _, _ = run_session(game, "play", ["alice-bolt1", "bob", "confirm"])
    assert action.source.get_zone() == ZoneType.HAND


def test_card_ids_are_global_and_inspect_resolves_opponents_cards():
    game = make_game()
    alice, bob = game.state.players
    own, enemy = alice.hand["alice-bolt1"], bob.hand["bob-bolt1"]
    assert card_reference(own) != card_reference(enemy)
    assert resolve_card("bob-bolt1", alice, global_scope=True) is enemy
    with pytest.raises(CommandError, match="ambiguous"):
        resolve_card("bolt1", alice, global_scope=True)
    with pytest.raises(CommandError):
        build_action(game.state, alice, "play bob-bolt1 alice")
    output = []
    commands = iter([f"inspect {card_reference(enemy)}", "pass"])
    ConsoleDecisionMaker(read=lambda _: next(commands), write=output.append).get_action(game.state, alice)
    assert any(f"{card_reference(enemy)}: Lightning Bolt" in line for line in output)


def test_target_ids_have_no_implicit_player_scope_and_survive_control_change():
    game = make_game()
    alice, bob = game.state.players
    target = bob.hand["bob-beast1"]
    reference = card_reference(target)
    bob.move_card(target, ZoneType.BATTLEFIELD)
    assert _resolve_target("bob-beast1", game.state, alice) is target
    with pytest.raises(CommandError, match="ambiguous"):
        _resolve_target("beast1", game.state, alice)
    target.set_controller(alice)
    assert card_reference(target) == reference
    assert _resolve_target(reference, game.state, alice) is target
    assert resolve_card("bob-beast1", alice, ZoneType.BATTLEFIELD, controlled=True) is target
    assert _resolve_target(bob.key, game.state, alice) is bob


def test_multiple_target_groups_are_not_confused_with_one_candidate_set():
    game = make_game()
    player = game.state.active_player
    for key in ("alice-beast1", "alice-adept1"):
        player.move_card(player.hand[key], ZoneType.BATTLEFIELD)
    action, prompts, _ = run_session(game, "play", ["alice-flame1", "alice-beast1,alice-adept1", "confirm"])
    assert "play/targets> " in prompts
    assert action is not None
