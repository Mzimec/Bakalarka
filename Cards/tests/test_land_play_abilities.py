"""Land abilities share discovery, generation and permission validation."""
from game.ai.decision_maker import PriorityDecisionRequest
import pytest
from game.enums import CardType, ZoneType, TurnPhase
from game.game_state import Card, CardDefinition, State, Player
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import PlayLandAbilityDefinition, PlayLandAbility
from game.game_state.collectors.land_play_collector import LAND_PLAY_COLLECTOR
from game.game_state.collectors.ability_collector import ABILITY_COLLECTOR
from game.game_actions.generation.decision_abstraction.requests import PriorityDecisionRequest, AbilityDecisionRequest
from game.rules.lands import LandPlayAction, land_play_error
from game.stat_type import STAT_ABILITIES


@pytest.fixture
def state():
    result = State([Player([], ScriptedController()), Player([], ScriptedController(), idx=1)])
    result.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return result


def land(state, zone=ZoneType.HAND, permissions=(), owner=None):
    owner = owner or state.active_player
    card = Card(CardDefinition("Land", types=frozenset({CardType.LAND}),
                               abilities=frozenset(permissions)), owner)
    owner.add_card(card, zone, state)
    return card


def execute(action, state):
    from game.console.demo_game import create_demo_game
    # Use the standard processor constructed by the demo's rules engine.
    processor = create_demo_game((ScriptedController(), ScriptedController())).loop.processor
    return processor.process(state, action)


def test_land_owns_play_ability_and_priority_does_not_duplicate_it(state):
    card = land(state)
    ability = card.get_ability_def("play_land", state).to_ability(card, state.active_player)
    assert isinstance(ability, PlayLandAbility)
    assert isinstance(card.try_find_ability("play_land", state), PlayLandAbility)
    assert list(ABILITY_COLLECTOR.collect_non_mana(state, state.active_player)) == []
    options = list(PriorityDecisionRequest(state, state.active_player).options)
    plays = [o for o in options if isinstance(o, LandPlayAction)]
    assert len(plays) == 1 and plays[0].card is card
    assert plays[0].permission is ability.definition
    assert len(list(AbilityDecisionRequest(state, state.active_player, ability).options)) == 1


@pytest.mark.parametrize("zone", [ZoneType.GRAVEYARD, ZoneType.EXILE])
def test_non_hand_lands_require_permission_and_execute_as_special_action(state, zone):
    permission = PlayLandAbilityDefinition(key="extra_zone", allowed_zones=frozenset({zone}))
    playable = land(state, zone, [permission])
    forbidden = land(state, zone)
    abilities = list(LAND_PLAY_COLLECTOR.collect(state, state.active_player))
    assert [a.source for a in abilities] == [playable]
    assert land_play_error(forbidden, state.active_player, state) is not None
    assert playable.get_zone() == zone and state.active_player.lands_played_this_turn == 0
    action = next(iter(AbilityDecisionRequest(state, state.active_player, abilities[0]).options))
    assert all(r.success for r in execute(action, state))
    assert playable.get_zone() == ZoneType.BATTLEFIELD
    assert state.stack.is_empty() and state.active_player.lands_played_this_turn == 1


def test_permission_revocation_prevents_previously_generated_action(state):
    permission = PlayLandAbilityDefinition(key="graveyard_play", allowed_zones=frozenset({ZoneType.GRAVEYARD}))
    card = land(state, ZoneType.GRAVEYARD, [permission])
    action = next(LAND_PLAY_COLLECTOR.collect(state, state.active_player)).to_game_action()
    card.set_base_stat(STAT_ABILITIES, {})
    assert list(LAND_PLAY_COLLECTOR.collect(state, state.active_player)) == []
    assert action.validation_error(state) is not None
    assert not execute(action, state)[0].success
    assert card.get_zone() == ZoneType.GRAVEYARD
    assert state.active_player.lands_played_this_turn == 0


def test_condition_and_timing_are_rechecked(state):
    enabled = [True]
    permission = PlayLandAbilityDefinition(allowed_zones=frozenset({ZoneType.EXILE}),
                                          condition=lambda card, player, state: enabled[0])
    card = land(state, ZoneType.EXILE, [permission])
    action = next(LAND_PLAY_COLLECTOR.collect(state, state.active_player)).to_game_action()
    enabled[0] = False
    assert action.validation_error(state) is not None
    enabled[0] = True
    state.turn.phase = TurnPhase.UPKEEP
    assert list(LAND_PLAY_COLLECTOR.collect(state, state.active_player)) == []
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    state.active_player.lands_played_this_turn = 1
    assert list(LAND_PLAY_COLLECTOR.collect(state, state.active_player)) == []


def test_multiple_permissions_and_zone_reentry(state):
    permissions = [PlayLandAbilityDefinition(key=key, allowed_zones=frozenset({ZoneType.EXILE}))
                   for key in ("first", "second")]
    card = land(state, ZoneType.EXILE, permissions)
    abilities = list(LAND_PLAY_COLLECTOR.collect(state, state.active_player))
    assert len(abilities) == 1
    action = abilities[0].to_game_action()
    card.owner.move_card(card, ZoneType.GRAVEYARD, state)
    card.owner.move_card(card, ZoneType.EXILE, state)
    assert action.validation_error(state) is not None


def test_permission_can_name_player_other_than_owner(state):
    player, owner = state.players
    permission = PlayLandAbilityDefinition(allowed_zones=frozenset({ZoneType.EXILE}), permitted_player=player)
    card = land(state, ZoneType.EXILE, [permission], owner)
    action = next(LAND_PLAY_COLLECTOR.collect(state, player)).to_game_action()
    assert all(r.success for r in execute(action, state))
    assert card.get_controller(state) is player and card.owner is owner


def test_simple_and_modular_agents_use_land_collector(state):
    from game.ai.simple_agent import SimpleAgent
    from game.ai.modular_agent import CandidateGenerator
    permission = PlayLandAbilityDefinition(allowed_zones=frozenset({ZoneType.GRAVEYARD}))
    card = land(state, ZoneType.GRAVEYARD, [permission])
    assert SimpleAgent().decide(PriorityDecisionRequest(state, state.active_player)).value.card is card
    candidates = CandidateGenerator().generate(state, state.active_player, set())
    assert [c.action.card for c in candidates if isinstance(c.action, LandPlayAction)] == [card]


@pytest.mark.parametrize("reason", ["phase", "opponent", "spent", "stack", "lost"])
def test_land_collection_rejects_closed_window_before_querying_cards(state, monkeypatch, reason):
    player = state.active_player
    land(state)
    if reason == "phase":
        state.turn.phase = TurnPhase.UPKEEP
    elif reason == "opponent":
        player = state.players[1]
    elif reason == "spent":
        player.lands_played_this_turn = player.land_plays_per_turn
    elif reason == "stack":
        monkeypatch.setattr(state.stack, "is_empty", lambda: False)
    else:
        player.has_lost = True
    monkeypatch.setattr(state, "query_cards", lambda query: pytest.fail("Closed land window must not scan cards"))
    assert list(LAND_PLAY_COLLECTOR.collect(state, player)) == []


def test_land_window_is_rechecked_after_an_extra_play_is_granted(state):
    card = land(state)
    player = state.active_player
    player.lands_played_this_turn = 1
    assert list(LAND_PLAY_COLLECTOR.collect(state, player)) == []
    player.land_plays_per_turn = 2
    assert [ability.source for ability in LAND_PLAY_COLLECTOR.collect(state, player)] == [card]
