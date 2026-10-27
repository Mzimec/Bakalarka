"""Player memory cannot learn private draws or tentative rolled-back payments."""
import json

import pytest

from game.ai.knowledge import PlayerKnowledge, PlayerEvent, event_view
from game.ai.modular_agent import ModularAgent, observation
from game.cards.starter_cards import starter_catalog
from game.console.demo_game import create_demo_game
from game.enums import ZoneType as Z, TurnPhase
from game.game_state import Card, Player, State
from game.game_actions.data_structs.game_action import AbilityAction, FixedExecutionPlan, ResolutionContext
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.event_bus import EventBus, GameEvent, capture_event
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.operations.card_operations import MoveCardOperation


def test_scalar_event_projection_is_identical_and_detached():
    from dataclasses import asdict
    event = PlayerEvent('card_drawn', 3, 'DRAW', player=0, owner=0,
                        card_id='card:1', name='Island', from_zone='DECK', to_zone='HAND')
    assert event_view(event) == asdict(event)
    assert list(event_view(event)) == list(asdict(event))
    result = event_view(event)
    result['name'] = 'Changed'
    assert event.name == 'Island' and event_view(event)['name'] == 'Island'


def test_event_projection_preserves_deepcopy_for_nested_extension_values():
    from dataclasses import asdict
    event = PlayerEvent('extension', 3, 'MAIN', name={'nested': ['visible']})
    result = event_view(event)
    assert result == asdict(event)
    result['name']['nested'].append('changed')
    assert event.name == {'nested': ['visible']}


def test_event_projection_ignores_extra_attributes_and_keeps_recent_event_limit():
    from dataclasses import asdict
    event = PlayerEvent('card_drawn', 1, 'DRAW')
    object.__setattr__(event, 'diagnostic', ['not part of the schema'])
    assert event_view(event) == asdict(event)
    knowledge = PlayerKnowledge()
    knowledge.events = [event, PlayerEvent('card_drawn', 2, 'DRAW')]
    assert knowledge.view(recent_events=1)['recent_events'] == [asdict(knowledge.events[-1])]
    assert knowledge.view(recent_events=0)['recent_events'] == []


def board():
    return State([Player([], ModularAgent(), idx=i) for i in range(2)])


def add(state, name, zone, owner=1):
    card = Card(starter_catalog()[name], state.players[owner])
    card.owner.add_card(card, zone)
    return card


def emit(state, card, origin, destination):
    return EventBus().emit(GameEvent("card_moved", card, card.owner,
        {"from": origin, "to": destination}, triggered_abilities=()), state)


def test_hidden_draws_have_counts_but_no_identity_or_engine_references():
    views = []
    for name in ("Island", "Pacifism"):
        state = board()
        card = add(state, name, Z.HAND)
        EventBus().emit(GameEvent("card_drawn", card, card.owner, {"card": card}, triggered_abilities=()), state)
        views.append(observation(state, state.players[0])["knowledge"])
        own = observation(state, state.players[1])["knowledge"]
        assert own["recent_events"][0]["name"] == name
        assert name not in json.dumps(views[-1])
        assert views[-1]["recent_events"][0]["card_id"] is None
    assert views[0] == views[1]


def test_public_bounce_remembered_across_decisions_and_anonymous_draw():
    state = board()
    card = add(state, "Serra Angel", Z.HAND)
    emit(state, card, "BATTLEFIELD", "HAND")
    hidden = add(state, "Island", Z.HAND)
    EventBus().emit(GameEvent("card_drawn", hidden, hidden.owner, triggered_abilities=()), state)
    view = observation(state, state.players[0])["knowledge"]
    assert view["known_locations"] == [{"card_id": card.command_id, "name": "Serra Angel",
        "owner": 1, "zone": "HAND", "provenance": "event"}]
    assert observation(state, state.players[0])["knowledge"] == view
    # Exported data must not be mutable aliases into memory.
    view["known_locations"][0]["name"] = "corrupted"
    assert state.players[0].controller.knowledge.view()["known_locations"][0]["name"] == "Serra Angel"
    emit(state, hidden, "HAND", "DECK")
    assert not state.players[0].controller.knowledge.view()["known_locations"]


def test_shuffle_forgets_library_locations_but_preserves_observed_history():
    state = board()
    card = add(state, "Serra Angel", Z.DECK)
    emit(state, card, "BATTLEFIELD", "DECK")
    EventBus().emit(GameEvent("library_shuffled", controller=card.owner, triggered_abilities=()), state)
    view = state.players[0].controller.knowledge.view()
    assert view["known_locations"] == []
    assert view["observed_cards"][0]["name"] == "Serra Angel"


def test_visibility_and_name_are_frozen_before_delayed_publication():
    state = board()
    hidden = add(state, "Pacifism", Z.EXILE)
    hidden.state.face_down = True
    event = capture_event(state, GameEvent("card_moved", hidden, hidden.owner,
        {"from": "HAND", "to": "EXILE"}, triggered_abilities=()))
    hidden.state.face_down = False
    EventBus().emit(event, state)
    assert state.players[0].controller.knowledge.events[-1].name is None
    public = capture_event(state, GameEvent("card_moved", hidden, hidden.owner,
        {"from": "EXILE", "to": "HAND"}, triggered_abilities=()))
    hidden.state.face_down = True
    EventBus().emit(public, state)
    assert state.players[0].controller.knowledge.events[-1].name == "Pacifism"


def test_reusing_controller_in_another_game_resets_memory():
    state = board()
    controller = state.players[0].controller
    card = add(state, "Pacifism", Z.GRAVEYARD)
    emit(state, card, "HAND", "GRAVEYARD")
    assert controller.knowledge.events
    next_state = State([Player([], controller, idx=0), Player([], ModularAgent(), idx=1)])
    assert observation(next_state, next_state.players[0])["knowledge"]["observed_cards"] == []
    assert not controller.knowledge.events


def test_later_operation_in_batch_cannot_reveal_earlier_hidden_event():
    state = board()
    card = add(state, "Pacifism", Z.EXILE)
    card.state.face_down = True

    class HiddenEvent(Operation):
        def execute(self, state):
            return [GameEvent("card_moved", card, card.owner, {"from": "HAND", "to": "EXILE"})]

    class TurnFaceUp(Operation):
        def execute(self, state):
            card.state.face_down = False
            return []

    ctx = ResolutionContext(source=card, controller=card.owner)
    events = OperationExecutor().execute_batch(state, [HiddenEvent(ctx), TurnFaceUp(ctx)])
    for event in events:
        EventBus().emit(event, state)
    assert state.players[0].controller.knowledge.events[0].name is None


def test_raw_engine_events_cannot_be_injected_as_memory():
    with pytest.raises(TypeError):
        PlayerKnowledge().observe(GameEvent("card_drawn"))


class FailingCost(Operation):
    def reserve_cost(self, state, resources):
        return None

    def execute(self, state):
        raise RuntimeError("failed after movement")


def test_failed_cost_does_not_publish_knowledge_but_committed_move_does():
    game = create_demo_game((ModularAgent(), ModularAgent()), full_rules=True)
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    card = add(state, "Serra Angel", Z.BATTLEFIELD, owner=0)
    ctx = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    def action(costs):
        return AbilityAction("pay", card, FixedExecutionPlan([]), FixedExecutionPlan(costs), controller=card.owner)
    before = [observation(state, p)["knowledge"] for p in state.players]
    with pytest.raises(RuntimeError, match="failed after movement"):
        game.loop.processor.process(state, action([MoveCardOperation(ctx, card, Z.GRAVEYARD), FailingCost(ctx)]))
    assert card.get_zone() == Z.BATTLEFIELD
    assert [observation(state, p)["knowledge"] for p in state.players] == before
    assert game.loop.processor.process(state, action([MoveCardOperation(ctx, card, Z.GRAVEYARD)]))[0].success
    assert all(p.controller.knowledge.events[-1].to_zone == "GRAVEYARD" for p in state.players)
