from game.ai.decision_maker import PriorityDecisionRequest
from game.game_actions import PassPriorityAction
from game.game_loop.minimal_game import DealDamageAction, MinimalPlayer, MinimalState, ScriptedController, create_minimal_game


def test_scripted_controller_returns_actions_then_passes():
    player = MinimalPlayer("A", ScriptedController())
    opponent = MinimalPlayer("B", ScriptedController())
    action = DealDamageAction(player, opponent, 1)
    controller = ScriptedController([action])
    state = MinimalState((player, opponent))

    assert controller.decide(PriorityDecisionRequest(state, player)).value is action
    assert isinstance(controller.decide(PriorityDecisionRequest(state, player)).value, PassPriorityAction)


def test_minimal_game_connects_action_resolution_events_and_game_loop():
    game = create_minimal_game()
    first, second = game.state.players
    first.controller = ScriptedController([
        DealDamageAction(first, second, 4),
        DealDamageAction(first, second, 6),
    ])

    loser = game.run()

    assert loser is second
    assert second.health == 0
    assert [event.key for event in game.event_bus.emitted_events] == ["damage_dealt", "damage_dealt", "player_lost"]
    assert [event.payload["amount"] for event in game.event_bus.emitted_events if event.key == "damage_dealt"] == [4, 6]
