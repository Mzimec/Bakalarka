"""Executable examples of terminal input reaching the real game state.

Run from Cards/: python -m pytest tests/test_game_loop_integration.py -v
Play the same example: python -m game.bin.dummy_game
"""
import subprocess
import sys

from pathlib import Path

import pytest

from game.console.demo_game import ConsoleDecisionMaker, EMBER_ADEPT, available_actions, create_demo_game
from game.enums import TurnPhase, ZoneType
from game.game_actions import FixedExecutionPlan, GameAction, ResolutionContext, ScheduledResolution
from game.game_actions.data_structs.ability import EffectSequence
from game.game_state import Card, Player, State
from game.game_loop.minimal_game import ScriptedController
from game.target.target_resolver import TargetBinding, TargetOption


def console_game(commands, health=10):
    """Use actual command parsing, with a finite input stream instead of a user."""
    inputs = iter(commands)
    output = []
    console = ConsoleDecisionMaker(read=lambda prompt: next(inputs), write=output.append)
    game = create_demo_game((console, console), health=health)
    console.event_bus = game.event_bus
    return game, output


def test_console_cast_response_and_last_in_first_out_resolution():
    # Alice casts at Bob and passes. Bob responds with a Bolt at Alice,
    # then passes; Alice passes too, resolving Bob's response first.
    # Another pair of passes resolves Alice's Bolt, then closes the phase.
    game, output = console_game([
        "play bolt1 bob", "pass", "play bolt1 alice", "pass", "pass", "pass", "pass", "pass", "pass",
    ])
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    alice, bob = state.players

    game.loop.step(state)

    assert isinstance(state, State)
    assert all(isinstance(player, Player) for player in state.players)
    assert (alice.health, bob.health) == (7, 7)
    damage = [event for event in game.event_bus.emitted_events if event.key == "damage_dealt"]
    assert [event.payload["target"] for event in damage] == ["Alice", "Bob"]
    assert [event.controller for event in damage] == [bob, alice]
    assert state.stack.is_empty()
    for player in state.players:
        assert len(player.hand) == 3  # Uncast Bolt, Adept, and the query demo spell.
        assert len(player.graveyard) == 1
        assert not player.get_zone(ZoneType.STACK)
        card = next(iter(player.graveyard.values()))
        assert isinstance(card, Card)
        assert card.get_zone() == ZoneType.GRAVEYARD
    assert state.turn.phase == TurnPhase.END_STEP
    assert any("Stack (last resolves first): cast:Lightning Bolt" in line for line in output)


def test_console_summons_real_creature_and_pays_tap_cost_before_damage():
    game, _ = console_game(["play adept1", "pass", "pass", "activate adept1 tap_damage bob", "pass", "pass", "pass", "pass"])
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    alice, bob = state.players

    game.loop.step(state)

    adept = next(iter(alice.battlefield.values()))
    assert adept.definition == EMBER_ADEPT
    assert adept.get_zone() == ZoneType.BATTLEFIELD
    assert (adept.get_power(state), adept.get_toughness(state)) == (2, 2)
    assert adept.is_tapped
    assert bob.health == 8
    keys = [event.key for event in game.event_bus.emitted_events]
    assert keys.index("card_tapped") < keys.index("damage_dealt")
    assert not any(option.label.startswith("Activate") for option in available_actions(state, alice))


def test_game_stops_after_lethal_resolution_without_another_input():
    # Untap and draw run automatically. Alice selects a Bolt targeting Bob,
    # then each player passes once. No more input is required after lethal.
    game, _ = console_game(["play bolt1 bob", "pass", "pass"], health=3)

    winner = game.run()

    assert winner is game.state.players[0]
    assert game.state.players[1].health == 0
    assert game.state.turn.phase == TurnPhase.DRAW


def test_passing_advances_turn_draws_and_untaps_only_active_player():
    game = create_demo_game((ScriptedController(), ScriptedController()))
    state = game.state
    alice, bob = state.players
    for player in state.players:
        adept = next(card for card in player.hand.values() if card.definition == EMBER_ADEPT)
        player.move_card(adept, ZoneType.BATTLEFIELD, state)
        adept.is_tapped = True
    alice_adept = next(iter(alice.battlefield.values()))
    bob_adept = next(iter(bob.battlefield.values()))
    alice_hand = len(alice.hand)

    game.loop.step(state)  # Alice untaps.
    assert not alice_adept.is_tapped and bob_adept.is_tapped
    game.loop.step(state)  # Alice draws and both pass.
    assert len(alice.hand) == alice_hand + 1
    game.loop.step(state)  # Main.
    game.loop.step(state)  # End: Bob becomes active.
    assert state.active_player is bob
    assert state.turn.number == 2
    assert state.priority.current_player is bob
    game.loop.step(state)  # Bob untaps.
    assert not bob_adept.is_tapped


def test_empty_deck_loss_during_draw_needs_no_priority_input():
    game, _ = console_game([])
    alice, bob = game.state.players
    for card in list(alice.deck.values()):
        alice.move_card(card, ZoneType.EXILE, game.state)

    assert game.run() is bob
    assert alice.has_lost and alice.loss_reason == "empty_library"
    assert alice.health == 10  # Drawing from an empty library does not change life.


def test_creature_timing_and_card_leaves_hand_as_soon_as_cast():
    game = create_demo_game((ScriptedController(), ScriptedController()))
    state = game.state
    alice, bob = state.players
    state.turn.phase = TurnPhase.END_STEP
    assert not any(option.label.startswith("Summon") for option in available_actions(state, alice))
    assert available_actions(state, bob) == []  # Bob does not hold priority.
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    summon = next(option.action for option in available_actions(state, alice)
                  if option.label.startswith("Summon"))
    game.loop.processor.process(state, summon)
    assert summon.source.key not in alice.hand
    assert summon.source.get_zone() == ZoneType.STACK
    assert not any(option.action.source is summon.source for option in available_actions(state, alice))
    assert len(state.stack.items) == 1


def test_invalid_console_input_and_status_do_not_take_a_game_action():
    game, output = console_game(["unknown", "-1", "999", "status", "help", "concede"])
    assert game.run() is game.state.players[1]
    assert sum("Invalid input" in line for line in output) == 3
    assert [event.key for event in game.event_bus.emitted_events
            if event.key in {"player_conceded", "damage_dealt", "card_tapped"}] == ["player_conceded"]


def test_rejected_cost_preserves_previous_players_pass():
    prompts = []

    class RecordingController(ScriptedController):
        def get_action(self, state, player):
            prompts.append(player.name)
            return super().get_action(state, player)

    class InvalidCostAction(GameAction):
        def get_intents(self):
            context = ResolutionContext(
                targets=TargetBinding({"removed_slot": {"_0": TargetOption({"target": 1})}}).to_immutable(),
                effects=EffectSequence(()),
            )
            return (ScheduledResolution(FixedExecutionPlan([]), context),)

    game = create_demo_game((RecordingController(), RecordingController([InvalidCostAction()])))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN

    game.loop.step(game.state)

    # Alice passes; Bob's rejected choice changes nothing. Bob then passes,
    # closing the window without asking Alice to pass a second time.
    assert prompts == ["Alice", "Bob", "Bob"]
    assert game.state.turn.phase == TurnPhase.END_STEP
    assert all(event.key == "phase_started" for event in game.event_bus.emitted_events)


@pytest.mark.parametrize("command", ["quit", ""])
def test_command_line_entry_point_stops_cleanly_on_quit_or_end_of_input(command):
    result = subprocess.run(
        cwd=Path(__file__).resolve().parents[1],
        args=[sys.executable, "-B", "-m", "game.bin.dummy_game"],
        input=command + "\n" if command else "", capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Game stopped." in result.stdout
    assert "Traceback" not in result.stderr
