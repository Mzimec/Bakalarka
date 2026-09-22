"""Inspect should explain printed abilities, including static rules and triggers."""
from game.ai.decision_maker import PriorityDecisionRequest

import pytest

from game.console.demo_game import ConsoleDecisionMaker, create_starter_demo_game
from game.ai.simple_agent import SimpleAgent
from game.game_state.card import Card
from game.enums import ZoneType, TurnPhase
from game.cards.starter_cards import starter_catalog


@pytest.mark.parametrize("name, expected", [
    ("Ilysian Caryatid", "two mana"),
    ("Colossal Majesty", "At the beginning of your upkeep"),
    ("Murder", "Destroy target creature."),
    ("Angel of Vitality", "plus 1"),
    ("Plains", "{W}"),
    ("Rumbling Baloth", "No printed abilities."),
])
def test_inspect_prints_complete_rules(name, expected):
    game = create_starter_demo_game(controllers=(SimpleAgent(), SimpleAgent()))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    player = game.state.active_player
    card = Card(starter_catalog()[name], player, key="inspected")
    player.add_card(card, ZoneType.HAND, game.state)
    commands = iter(["inspect inspected", "pass"])
    output = []
    ConsoleDecisionMaker(read=lambda _: next(commands), write=output.append).decide(PriorityDecisionRequest(game.state, player)).value
    text = "\n".join(output)
    assert expected in text
    assert "Rules text (printed abilities):" in text
    assert "Available commands:" in text
    assert "  Effect:" not in text


def test_all_starter_definitions_have_readable_text():
    catalog = starter_catalog()
    assert len(catalog) == 88
    assert all(card.oracle_text.strip() for card in catalog.values())
