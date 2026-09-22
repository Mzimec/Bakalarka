"""Contracts for the request-only engine boundary and typed ability authoring."""
from dataclasses import replace

import pytest

from game.ai.decision_maker import ModularDecisionMaker, DecisionResult
from game.game_actions.generation.decision_abstraction.auxiliary import (
    StartingPlayerRequest, StartingPlayerPolicy, StartingPlayerOption,
    ScryRequest, TriggerOrderRequest, TriggerOrderPolicy, TriggerOrderOption,
    ReplacementRequest, ReplacementPolicy, ReplacementOption,
)
from game.game_actions.generation.decision_abstraction.requests import AbilityDecisionRequest
from game.game_actions.data_structs.ability import (
    AbilityDefinition, ActivatedAbilityDefinition, CastSpellAbilityDefinition,
    ManaAbilityDefinition, PlayLandAbilityDefinition, TriggeredManaAbilityDefinition,
    ActivatedAbility, CastSpellAbility, ManaAbility, PlayLandAbility, TriggeredManaAbility,
)
from game.game_actions.permanent_effects import attachment_ability
from game.game_state import State, Player, Card, CardDefinition
from game.enums import ZoneType, TurnPhase


@pytest.fixture
def state():
    return State([Player([], ModularDecisionMaker(), idx=i) for i in range(2)])


def test_starting_player_options_exist_before_state_and_default_chooses_requester(state):
    player = state.players[1]
    request = StartingPlayerRequest(None, player, tuple(state.players))
    assert [o.selected for o in request.options] == list(state.players)
    assert player.controller.decide(request).value.selected is player
    with pytest.raises(TypeError, match="StartingPlayerPolicy"):
        list(request.option_space(TriggerOrderPolicy()))


def test_auxiliary_strategies_cannot_add_or_duplicate_candidates(state):
    first, second, foreign = object(), object(), object()

    class OrderProposals:
        def generate(self, request):
            yield TriggerOrderOption((first, first))
            yield TriggerOrderOption((first, foreign))
            yield TriggerOrderOption((second, first))

    request = TriggerOrderRequest(state, state.active_player, (first, second))
    assert list(request.option_space(TriggerOrderPolicy(strategy=OrderProposals()))) == [
        TriggerOrderOption((second, first)),
    ]

    class ReplacementProposals:
        def generate(self, request):
            yield ReplacementOption(foreign)
            yield ReplacementOption(second)

    request = ReplacementRequest(state, state.active_player, object(), (first, second))
    assert list(request.option_space(ReplacementPolicy(strategy=ReplacementProposals()))) == [
        ReplacementOption(second),
    ]


def test_scry_options_preserve_order_and_do_not_move_cards(state):
    player = state.active_player
    cards = tuple(Card(CardDefinition(str(i)), player) for i in range(2))
    for card in cards:
        player.add_card(card, ZoneType.DECK)
    before = tuple(player.deck.values())
    choices = list(ScryRequest(state, player, cards).options)
    assert len(choices) == 6
    assert {(o.top, o.bottom) for o in choices} == {
        (cards, ()), (cards[::-1], ()), ((), cards), ((), cards[::-1]),
        ((cards[0],), (cards[1],)), ((cards[1],), (cards[0],)),
    }
    assert tuple(player.deck.values()) == before


@pytest.mark.parametrize("definition,runtime", [
    (ActivatedAbilityDefinition(), ActivatedAbility),
    (CastSpellAbilityDefinition(), CastSpellAbility),
    (ManaAbilityDefinition(), ManaAbility),
    (PlayLandAbilityDefinition(), PlayLandAbility),
    (TriggeredManaAbilityDefinition(), TriggeredManaAbility),
])
def test_specialized_definitions_bind_matching_runtime_abilities(state, definition, runtime):
    card = Card(CardDefinition("Source"), state.active_player)
    ability = definition.to_ability(card, state.active_player)
    assert type(ability) is runtime
    assert ability.source is card and ability.definition is definition


@pytest.mark.parametrize("definition,kwargs", [
    (ActivatedAbilityDefinition, {"is_spell": True}),
    (CastSpellAbilityDefinition, {"is_mana_ability": True}),
    (CastSpellAbilityDefinition, {"uses_stack": False}),
    (ManaAbilityDefinition, {"uses_stack": True}),
    (PlayLandAbilityDefinition, {"is_spell": True}),
    (TriggeredManaAbilityDefinition, {"uses_stack": True}),
])
def test_definition_kind_cannot_be_changed_by_conflicting_flags(definition, kwargs):
    with pytest.raises(TypeError):
        definition(**kwargs)
    with pytest.raises((TypeError, ValueError)):
        replace(definition(), **kwargs)


def test_base_definition_is_abstract_and_attachment_definitions_keep_their_kind():
    with pytest.raises(TypeError):
        AbilityDefinition()
    assert isinstance(attachment_ability(), CastSpellAbilityDefinition)
    assert isinstance(attachment_ability(equip=True), ActivatedAbilityDefinition)


def test_console_ability_decision_does_not_push_trigger_onto_stack(state):
    from game.console.demo_game import ConsoleDecisionMaker
    from game.game_actions.resolution.event_bus import GameEvent, TriggerProcessor
    from tests.test_triggered_abilities import add_card, trigger_def
    source = add_card(state, "source", [trigger_def()])
    trigger = next(iter(source.get_trigger_defs(state).values())).to_ability(
        source, state.active_player, event=GameEvent("test_event"),
    )
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    console = ConsoleDecisionMaker(read=lambda _: "confirm", write=lambda _: None)
    request = AbilityDecisionRequest(state, state.active_player, trigger)
    result = console.decide(request)
    assert result.value.trigger_event is trigger.event
    assert state.stack.is_empty()
    state.active_player.controller = console
    TriggerProcessor().process(state, [trigger])
    assert len(state.stack.items) == 1
