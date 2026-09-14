"""Human input regressions against the public, maintained console API."""
import pytest
from game.console.human_input import ActionBuilderSession, HumanDecisionMaker, _resolve_command
from game.console.demo_game import create_demo_game
from game.game_loop.minimal_game import ScriptedController
from game.enums import TurnPhase, ZoneType
from game.game_actions import PassPriorityAction


@pytest.mark.parametrize("raw,expected", [("play", "play"), ("p", "play"), ("a", "activate"),
    ("PLAY", "play"), ("pass", "pass"), ("", "pass"), ("xyz", None), ("b", "block")])
def test_command_aliases(raw, expected):
    assert _resolve_command(raw) == expected


def session(commands, cls=ActionBuilderSession):
    game = create_demo_game((ScriptedController(), ScriptedController()))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    inputs, output = iter(commands), []
    adapter = cls(game.state, game.state.active_player, read=lambda _: next(inputs), write=output.append)
    return game, adapter, output


@pytest.mark.parametrize("raw", ["", "pass", "PASS"])
def test_session_pass(raw):
    game, adapter, _ = session([raw])
    action = adapter.run()
    assert isinstance(action, PassPriorityAction)
    assert action.player is game.state.active_player


def test_invalid_command_and_cancel_retry_without_mutation():
    game, adapter, output = session(["garbage", "play", "cancel", "pass"])
    before = tuple(game.state.active_player.hand)
    assert isinstance(adapter.run(), PassPriorityAction)
    assert tuple(game.state.active_player.hand) == before and game.state.stack.is_empty()
    assert any("Invalid input" in line for line in output)


def test_builder_returns_chosen_action_without_paying_it():
    game, adapter, _ = session(["play", "alice-bolt1", "bob", "confirm"])
    action = adapter.run()
    assert action.source.get_zone() == ZoneType.HAND
    assert game.state.stack.is_empty()
    assert game.loop.processor.process(game.state, action)[0].success
    assert action.source.get_zone() == ZoneType.STACK


def test_back_replaces_target_before_confirmation():
    game, adapter, _ = session(["play", "alice-bolt1", "bob", "back", "alice", "confirm"])
    action = adapter.run()
    game.loop.processor.process(game.state, action)
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert [player.health for player in game.state.players] == [7, 10]


def test_human_controller_uses_same_public_input_path():
    game, _, _ = session([])
    inputs = iter(["play bolt1 bob"])
    human = HumanDecisionMaker(read=lambda _: next(inputs), write=lambda _: None)
    action = human.get_action(game.state, game.state.active_player)
    assert action.source.key == "alice-bolt1"


@pytest.mark.parametrize("commands", [["quit"], ["play", "cancel", "quit"]])
def test_quit_propagates_clean_session_exit(commands):
    _, adapter, _ = session(commands)
    with pytest.raises(EOFError):
        adapter.run()
