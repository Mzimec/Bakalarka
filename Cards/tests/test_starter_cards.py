"""Integration coverage for the Arena Beginner white/red starter slice."""
from game.cards.decks import load_arena_starter
from game.enums import CardType, SAVariableType, TurnPhase, ZoneType
from game.game_actions.data_structs.ability import SubAbilityDefinition, SubAbilityVariable
from game.game_actions.generation.command_action_builder import chosen_action
from game.game_state import Card
from game.game_state.modifier import AddManaCostModifier
from game.mana.mana_value import ManaValue
from game.game_loop.minimal_game import ScriptedController
from game.console.demo_game import build_action, create_starter_demo_game
from game.rules.lands import basic_land
from game.cards.starter_cards import RED_STARTER_CARDS, SHOCK, WHITE_STARTER_CARDS, starter_catalog


def test_white_and_red_catalogs_cover_the_playable_starter_lists():
    assert set(WHITE_STARTER_CARDS) == {
        "Charmed Stray", "Fencing Ace", "Hallowed Priest", "Impassioned Orator",
        "Moorland Inquisitor", "Angel of Vitality", "Leonin Warleader", "Serra Angel",
        "Spiritual Guardian", "Angelic Guardian", "Inspiring Commander", "Goring Ceratops",
        "Tactical Advantage", "Confront the Assault", "Bond of Discipline", "Pacifism",
        "Angelic Reward",
    }
    assert set(RED_STARTER_CARDS) == {
        "Goblin Gang Leader", "Goblin Trashmaster", "Goblin Tunneler", "Immortal Phoenix",
        "Molten Ravager", "Nest Robber", "Ogre Battledriver", "Siege Dragon",
        "Tin Street Cadet", "Volcanic Dragon", "Burn Bright", "Inescapable Blaze", "Shock",
        "Storm Strike", "Goblin Gathering", "Raid Bombardment",
    }
    catalog = starter_catalog()
    for colour in ("white", "red"):
        resolved = load_arena_starter(colour).resolve(catalog)
        assert len(resolved) == 60
        assert all(card.mana_cost is not None
                   for card in resolved if CardType.LAND not in card.types)


def test_starter_setup_uses_twenty_life_and_real_opening_hands():
    game = create_starter_demo_game((ScriptedController(), ScriptedController()), seed=17)
    assert tuple(player.health for player in game.state.players) == (20, 20)
    assert tuple(len(player.hand) for player in game.state.players) == (7, 7)
    assert tuple(len(player.deck) for player in game.state.players) == (53, 53)
    assert game.state.active_player is game.state.players[0]
    assert game.state.turn.phase == TurnPhase.UNTAP


def test_casting_shock_automatically_taps_a_mountain_and_pays_before_stack_entry():
    game = create_starter_demo_game((ScriptedController(), ScriptedController()), seed=3)
    state = game.state
    caster, opponent = state.players
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    state.priority.reset()
    mountain = Card(basic_land("Mountain"), caster, key="test-mountain")
    caster.add_card(mountain, ZoneType.BATTLEFIELD, state)
    shock = Card(SHOCK, caster, key="test-shock")
    caster.add_card(shock, ZoneType.HAND, state)

    action = build_action(state, caster, "play test-shock bob")
    plan = action.cost_generator.mana_solver_result
    assert plan is not None and len(plan.mana_plan) == 1
    assert not mountain.is_tapped and not caster.mana_pool

    result = game.loop.processor.process(state, action)
    assert result[0].success
    assert mountain.is_tapped and not caster.mana_pool
    assert shock.get_zone() == ZoneType.STACK
    assert opponent.health == 20

    resolved = game.loop.processor.executor.resolve(state, state.stack.pop())
    assert resolved.success
    assert opponent.health == 18
    assert shock.get_zone() == ZoneType.GRAVEYARD


def test_mana_cost_modifier_changes_a_printed_cost_value():
    printed = ManaValue("{1}{R}")
    result = AddManaCostModifier(ManaValue("{1}")).modify(printed)
    assert result.cmc() == 3


def test_unbounded_y_variable_has_a_safe_generation_cap():
    variable = SubAbilityVariable(
        var_type=SAVariableType.Y,
        cost_subdef=SubAbilityDefinition(), action_subdef=SubAbilityDefinition(), min_value=2,
    )
    capped = SubAbilityVariable(
        var_type=variable.var_type,
        cost_subdef=variable.cost_subdef, action_subdef=variable.action_subdef,
        min_value=2, max_cap=4,
    )
    assert variable.get_max_value(None, None) == 20
    assert capped.get_max_value(None, None) == 4
