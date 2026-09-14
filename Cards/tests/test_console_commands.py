"""Commands construct one action without enumerating card/target combinations."""
import pytest

from game.console.console_commands import CommandError, card_reference
from game.console.demo_game import ConsoleDecisionMaker, build_action, create_demo_game
from game.enums import TurnPhase, ZoneType
from game.game_loop.minimal_game import ScriptedController


def make_game():
    game = create_demo_game((ScriptedController(), ScriptedController()))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return game


def test_direct_command_builds_only_requested_action_without_mutating_state(monkeypatch):
    game = make_game()
    player = game.state.active_player
    def no_enumeration(*args):
        pytest.fail("Console must not enumerate action combinations")
    monkeypatch.setattr("game.console.demo_game.available_actions", no_enumeration)
    controller = ConsoleDecisionMaker(read=lambda _: "play bolt1 bob", write=lambda _: None)
    action = controller.get_action(game.state, player)
    assert action.source.key == "alice-bolt1"
    assert action.action_generator.binding.get_targets_in_slot("target_0", "target") == frozenset({game.state.players[1]})
    assert action.source.get_zone() == ZoneType.HAND
    assert game.state.stack.is_empty()


@pytest.mark.parametrize("command", [
    "play", "play bolt1", "play bolt1 nobody", "play adept1 bob",
    "play bob-bolt1 alice", 'play "Lightning Bolt" bob', 'play "broken',
    "activate adept1 bob", "pass bob", "concede bob", "1",
])
def test_invalid_commands_do_not_mutate_game(command):
    game = make_game()
    before = [(card.key, card.get_zone(), card.is_tapped) for card in game.state.get_cards()]
    with pytest.raises(CommandError):
        build_action(game.state, game.state.active_player, command)
    assert before == [(card.key, card.get_zone(), card.is_tapped) for card in game.state.get_cards()]
    assert game.state.stack.is_empty()
    assert [player.health for player in game.state.players] == [10, 10]


def test_card_references_remain_stable_and_cast_card_cannot_be_reused():
    game = make_game()
    player = game.state.active_player
    action = build_action(game.state, player, "PLAY ALICE-BOLT1 opponent")
    game.loop.processor.process(game.state, action)
    assert card_reference(action.source) == "c7"
    with pytest.raises(CommandError, match="STACK"):
        build_action(game.state, player, "play bolt1 bob")
    assert build_action(game.state, player, "cast bolt2 self").source.key == "alice-bolt2"


def test_summon_and_activation_commands_enforce_timing_ability_and_tap_cost():
    game = make_game()
    state = game.state
    player = state.active_player
    state.turn.phase = TurnPhase.END_STEP
    with pytest.raises(CommandError, match="main phase"):
        build_action(state, player, "play adept1")
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    action = build_action(state, player, 'play "Ember Adept"')
    game.loop.processor.process(state, action)
    game.loop.processor.executor.resolve(state, state.stack.pop())
    with pytest.raises(CommandError, match="not available"):
        build_action(state, player, "activate adept1 nonexistent bob")
    ability = build_action(state, player, "activate adept1 opponent")
    game.loop.processor.process(state, ability)
    with pytest.raises(CommandError, match="already tapped"):
        build_action(state, player, "activate adept1 tap_damage bob")
    assert player.battlefield["alice-adept1"].is_tapped
    assert state.players[1].health == 10  # Damage awaits resolution.


def test_command_checks_priority_and_noninteractive_phase():
    game = make_game()
    with pytest.raises(CommandError, match="priority"):
        build_action(game.state, game.state.players[1], "play bolt1 alice")
    game.state.turn.phase = TurnPhase.UNTAP
    with pytest.raises(CommandError, match="priority"):
        build_action(game.state, game.state.active_player, "play bolt1 bob")


def test_inspection_and_errors_keep_prompt_without_consuming_priority():
    game = make_game()
    commands = iter(["hand", "inspect alice-bolt1", "inspect alice-adept1", "help", "status", "quit now", "pass"])
    output = []
    console = ConsoleDecisionMaker(read=lambda _: next(commands), write=output.append)
    action = console.get_action(game.state, game.state.active_player)
    assert action.player is game.state.active_player
    assert any("c7: Lightning Bolt" in line for line in output)
    assert any("tap_damage" in line for line in output)
    assert any("Usage: quit" in line for line in output)
    assert not any(line.startswith("[0]") for line in output)
    assert game.state.stack.is_empty()
