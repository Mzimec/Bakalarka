import re
import pytest

from game.console.demo_game import create_demo_game, build_action, _resolve_target
from game.console.console_commands import card_reference, resolve_card, CommandError
from game.enums import TurnPhase, ZoneType
from game.game_state import Card
from game.cards.demo_cards import LIGHTNING_BOLT


def test_short_ids_are_global_and_usable_for_commands_from_either_player():
    game = create_demo_game(expanded=True)
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    alice, bob = state.players
    refs = [card_reference(card) for card in state.get_cards()]
    assert len(set(refs)) == len(refs)
    assert all(re.fullmatch(r"c\d+", ref) for ref in refs)
    bolt = alice.hand["alice-bolt1"]
    reference = card_reference(bolt)
    assert build_action(state, alice, f"play {reference} bob").source is bolt
    assert resolve_card(reference, bob, global_scope=True) is bolt
    assert _resolve_target(reference, state, bob) is bolt
    enemy = bob.hand["bob-bolt1"]
    with pytest.raises(CommandError):
        build_action(state, alice, f"play {card_reference(enemy)} alice")


def test_removed_ids_are_not_recycled_and_same_card_keeps_its_reference():
    game = create_demo_game()
    player = game.state.active_player
    card = player.hand["alice-bolt1"]
    reference = card_reference(card)
    player.remove_card(card)
    assert game.state.card_register.get_by_reference(reference) is None
    new = Card(LIGHTNING_BOLT, player, key="new-bolt")
    player.add_card(new, ZoneType.HAND)
    assert card_reference(new) != reference
    player.add_card(card, ZoneType.HAND)
    assert card_reference(card) == reference
    assert game.state.card_register.get_by_reference(reference) is card
    player.move_card(card, ZoneType.GRAVEYARD)
    assert card_reference(card) == reference


def test_internal_key_cannot_shadow_an_assigned_short_reference():
    game = create_demo_game()
    player = game.state.active_player
    reference = card_reference(player.hand["alice-bolt1"])
    conflict = Card(LIGHTNING_BOLT, player, key=reference)
    with pytest.raises(ValueError, match="command ID"):
        player.add_card(conflict, ZoneType.HAND)
    assert conflict not in game.state.get_cards()
