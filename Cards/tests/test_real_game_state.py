"""Examples of the real card/player data used by the playable game loop."""
from game.ai.decision_maker import DecisionResult
from game.game_actions import PassPriorityAction
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition

import pytest

from game.enums import CardType, ZoneType
from game.game_actions.data_structs.ability import TriggerAbilityDefinition, TriggerCondition
from game.game_actions.resolution.event_bus import GameEvent
from game.game_state import Card, CardDefinition, Player, State
from game.ai.decision_maker import ModularDecisionMaker as DecisionMaker


class PassingController(DecisionMaker):
    def decide_priority(self, request):
        state = request.state; player = request.player
        return DecisionResult(PassPriorityAction(player))


class DamageCondition(TriggerCondition):
    def matches(self, state, event):
        return event.key == "damage_dealt"


def make_state():
    players = [Player([], PassingController(), name=name) for name in ("Alice", "Bob")]
    return State(players)


def test_card_copies_have_distinct_identity_and_runtime_state():
    state = make_state()
    player = state.active_player
    definition = CardDefinition("Ember Adept", types=frozenset({CardType.CREATURE}), power=2, toughness=2)
    first, second = Card(definition, player), Card(definition, player)

    player.add_card(first)
    player.add_card(second)
    first.is_tapped = True
    first.state.damage_marked = 1

    assert first.definition is second.definition
    assert first.key != second.key
    assert first != second
    assert len({first, second}) == 2
    assert len(player.deck) == 2
    assert not second.is_tapped
    assert second.state.damage_marked == 0
    assert first.state.attach_info is not second.state.attach_info
    assert first.state.counters is not second.state.counters


def test_draw_and_move_keep_collections_and_card_zone_in_sync():
    state = make_state()
    player = state.active_player
    first, second = [Card(CardDefinition("Card"), player, key=key) for key in ("first", "second")]
    player.add_card(first)
    player.add_card(second)

    assert player.draw(state) is second
    assert second.get_zone() == ZoneType.HAND
    assert list(player.deck.values()) == [first]
    assert list(player.hand.values()) == [second]

    player.move_card(second, ZoneType.BATTLEFIELD, state)
    assert not player.hand
    assert second.get_zone() == ZoneType.BATTLEFIELD
    assert second.state.zone_info.entered_at == state.time_stamp
    second.is_tapped = True
    player.battlefield.untap_all()
    assert not second.is_tapped

    player.move_card(second, ZoneType.GRAVEYARD, state)
    assert not player.battlefield
    assert player.graveyard[second.key] is second
    assert state.get_cards(from_zones=[ZoneType.GRAVEYARD]) == [second]
    assert state.lookup("0.g.second").final is second


def test_real_card_stats_and_ability_lookup():
    state = make_state()
    player = state.active_player
    ability = ActivatedAbilityDefinition(key="spark")
    card = Card(CardDefinition(
        "Ember Adept",
        types=frozenset({CardType.CREATURE}),
        abilities=frozenset({ability}),
        power=2,
        toughness=3,
    ), player, key="adept")
    player.add_card(card, ZoneType.BATTLEFIELD, state)

    assert card.is_type(state, CardType.CREATURE)
    assert card.get_power(state) == 2
    assert card.get_toughness(state) == 3
    assert card.get_controller(state) is player
    assert card.get_ability_def("spark", state) is ability
    assert set(card.get_activatable_ability_defs(state)) == {"spark"}
    runtime_ability = state.lookup("0.b.adept.spark").final
    assert runtime_ability.source is card
    assert runtime_ability.controller is player
    assert runtime_ability.definition is ability

    card.state.damage_marked = 3
    assert card.is_dead(state)
    player.move_card(card, ZoneType.GRAVEYARD, state)
    assert not card.get_activatable_ability_defs(state)


def test_trigger_discovery_uses_real_card_definitions_and_current_zone():
    state = make_state()
    player = state.active_player
    definition = TriggerAbilityDefinition(key="on_damage", condition=DamageCondition())
    card = Card(CardDefinition("Watcher", triggers=frozenset({definition})), player)
    player.add_card(card, ZoneType.BATTLEFIELD, state)
    event = GameEvent("damage_dealt")

    triggers = state.get_trigger_abilities(event)
    assert len(triggers) == 1
    assert triggers[0].source is card
    assert triggers[0].controller is player
    assert triggers[0].event is event
    assert triggers[0].matches(state)
    assert not state.get_triggered_abilities(GameEvent("other"))[0].matches(state)

    player.move_card(card, ZoneType.HAND, state)
    assert state.get_trigger_abilities(event) == []


def test_invalid_zone_moves_and_duplicate_keys_preserve_existing_cards():
    state = make_state()
    player, opponent = state.players
    card = Card(CardDefinition("Card"), player, key="same")
    duplicate = Card(card.definition, player, key="same")
    player.add_card(card)

    with pytest.raises(ValueError, match="Duplicate card key"):
        player.add_card(duplicate)
    with pytest.raises(ValueError, match="not in"):
        player.move_card(duplicate, ZoneType.HAND)
    with pytest.raises(ValueError, match="another player"):
        opponent.add_card(card)

    assert list(player.deck.values()) == [card]
    assert card.get_zone() == ZoneType.DECK
    assert not player.hand
    player.draw(state)
    with pytest.raises(ValueError, match="empty"):
        player.draw(state)
