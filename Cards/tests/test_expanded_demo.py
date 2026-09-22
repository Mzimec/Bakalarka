"""Integration examples for incremental commands and the expanded engine rules."""
from game.ai.decision_maker import DecisionResult, PriorityDecisionRequest
from pathlib import Path

import pytest

from game.console.demo_game import create_demo_game, build_action, ConsoleDecisionMaker
from game.console.command_session import CommandSession
from game.game_loop.minimal_game import ScriptedController
from game.enums import ZoneType, TurnPhase
from game.game_actions.generation.command_action_builder import chosen_action
from game.game_state.registers.card_register import IK_POWER
from helper.query_system.query import EqQuery


class TriggerController(ScriptedController):
    def decide_ability(self, request):
        return DecisionResult(chosen_action(request.ability, request.state))


def make_game():
    game = create_demo_game((TriggerController(), TriggerController()), expanded=True)
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return game


def resolve(game, command):
    action = build_action(game.state, game.state.active_player, command)
    results = game.loop.processor.process(game.state, action)
    assert all(result.success for result in results)
    result = game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert result.success
    return action


def session(game, command, inputs):
    output = []
    iterator = iter(inputs)
    action = CommandSession(game.state, game.state.active_player, command,
                            lambda _: next(iterator), output.append).run()
    return action, output


def test_building_with_options_is_read_only_until_confirmed_action_is_processed():
    game = make_game()
    action, output = session(game, "play", ["options", "alice-bolt1", "options", "bob", "options", "confirm"])
    assert action.source.get_zone() == ZoneType.HAND
    assert game.state.players[1].health == 10 and game.state.stack.is_empty()
    assert any("put" in line.lower() or "STACK" in line for line in output)
    game.loop.processor.process(game.state, action)
    assert action.source.get_zone() == ZoneType.STACK


def test_back_discards_targets_and_cancel_does_not_pay_cost():
    game = make_game()
    resolve(game, "play adept1")
    action, _ = session(game, "activate", ["bob", "back", "alice", "cancel"])
    assert action is None
    assert not game.state.players[0].battlefield["alice-adept1"].is_tapped
    assert game.state.stack.is_empty()


def test_console_enters_builder_and_returns_to_prompt_after_cancel():
    game = make_game()
    inputs = iter(["play", "cancel", "play", "alice-bolt1", "bob", "confirm"])
    console = ConsoleDecisionMaker(read=lambda _: next(inputs), write=lambda _: None)
    action = console.decide(PriorityDecisionRequest(game.state, game.state.active_player)).value
    assert action.source.key == "alice-bolt1"


def test_modal_command_and_builder_choose_only_selected_effect():
    game = make_game()
    action, output = session(game, "play", ["alice-choice1", "options", "2", "bob", "confirm"])
    assert any("Draw" in line for line in output)
    game.loop.processor.process(game.state, action)
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert game.state.players[1].health == 7
    other = make_game()
    before = len(other.state.players[0].hand)
    resolve(other, "play choice1 mode=1")
    assert len(other.state.players[0].hand) == before  # One cast, one draw.


def test_multiple_targets_damage_and_state_based_deaths():
    game = make_game()
    resolve(game, "play adept1")
    resolve(game, "play beast1")
    resolve(game, "play flame1 alice-adept1,alice-beast1")
    assert {"alice-adept1", "alice-beast1", "alice-flame1"} <= game.state.players[0].graveyard.keys()


def test_duplicate_targets_rejected_for_nonrepeatable_selector():
    game = make_game()
    resolve(game, "play beast1")
    with pytest.raises(ValueError, match="target count"):
        build_action(game.state, game.state.active_player, "play flame1 alice-beast1,alice-beast1")


def test_master_updates_stats_indexes_control_and_leaving_source():
    game = make_game()
    player, opponent = game.state.players
    resolve(game, "play master1")
    resolve(game, "play beast1")
    beast = player.battlefield["alice-beast1"]
    assert beast.get_power(game.state) == 3
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 3))
    beast.set_controller(opponent)
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 2))
    beast.set_controller(player)
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 3))
    player.move_card(player.battlefield["alice-master1"], ZoneType.HAND)
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 2))
    assert not beast.state.active_cont_effects


def test_master_bonus_loss_can_cause_lethal_state_based_action():
    game = make_game()
    resolve(game, "play master1")
    resolve(game, "play beast1")
    resolve(game, "play flame1 alice-beast1")
    player = game.state.active_player
    beast = player.battlefield["alice-beast1"]
    assert beast.state.damage_marked == 2 and beast.get_toughness(game.state) == 3
    player.battlefield["alice-master1"].is_tapped = True
    resolve(game, "play recall1")
    assert beast.get_zone() == ZoneType.GRAVEYARD


def test_replacement_shield_prevents_damage_and_stops_after_leaving():
    game = make_game()
    resolve(game, "play guardian1")
    resolve(game, "play bolt1 alice")
    assert game.state.active_player.health == 8
    guardian = game.state.active_player.battlefield["alice-guardian1"]
    guardian.owner.move_card(guardian, ZoneType.HAND)
    resolve(game, "play bolt2 alice")
    assert game.state.active_player.health == 5


def test_full_prevention_does_not_loop_or_execute_original_operation():
    game = make_game()
    from game.game_actions.resolution.replacement_rules import ControllerDamageShield
    resolve(game, "play guardian1")
    guardian = game.state.active_player.battlefield["alice-guardian1"]
    game.state.replacement_rules[:] = [ControllerDamageShield(guardian, 10)]
    resolve(game, "play bolt1 alice")
    assert game.state.active_player.health == 10


def test_tap_trigger_is_above_the_activated_ability_and_resolves_independently():
    game = make_game()
    resolve(game, "play watcher1")
    action = build_action(game.state, game.state.active_player, "activate watcher1 tap_damage bob")
    game.loop.processor.process(game.state, action)
    assert [item.key for item in game.state.stack.items] == ["tap_damage", "when_tapped_draw"]
    before = len(game.state.active_player.hand)
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert len(game.state.active_player.hand) == before + 1
    assert game.state.players[1].health == 10
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert game.state.players[1].health == 8


def test_console_can_build_mandatory_trigger():
    game = make_game()
    inputs = iter(["confirm"])
    console = ConsoleDecisionMaker(read=lambda _: next(inputs), write=lambda _: None)
    game.state.active_player._controller = console
    resolve(game, "play watcher1")
    game.loop.processor.process(game.state, build_action(game.state, game.state.active_player,
                                                       "activate watcher1 tap_damage bob"))
    assert game.state.stack.pop().key == "when_tapped_draw"


def test_invalidated_spell_targets_move_spell_out_of_stack_zone():
    game = make_game()
    resolve(game, "play beast1")
    player = game.state.active_player
    action = build_action(game.state, player, "play flame1 alice-beast1")
    game.loop.processor.process(game.state, action)
    player.move_card(player.battlefield["alice-beast1"], ZoneType.HAND)
    assert not game.loop.processor.executor.resolve(game.state, game.state.stack.pop()).success
    assert action.source.get_zone() == ZoneType.GRAVEYARD


def test_mana_production_payment_and_stale_payment_validation():
    from game.enums import ManaType
    game = make_game()
    player = game.state.active_player
    with pytest.raises(ValueError, match="No legal action"):
        build_action(game.state, player, "play blast1 bob")
    resolve(game, "play stone1")
    mana_action = build_action(game.state, player, "activate stone1")
    assert all(result.success for result in game.loop.processor.process(game.state, mana_action))
    assert game.state.stack.is_empty() and player.mana_pool[ManaType.RED] == 2
    action, output = session(game, "play", ["alice-blast1", "bob", "options", "confirm"])
    assert player.mana_pool[ManaType.RED] == 2
    assert any("Pay mana" in line for line in output)
    player.mana_pool.substract({ManaType.RED: 1})
    assert not game.loop.processor.process(game.state, action)[0].success
    assert action.source.get_zone() == ZoneType.HAND
    player.mana_pool.add({ManaType.RED: 1})
    assert game.loop.processor.process(game.state, action)[0].success
    assert not player.mana_pool
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert game.state.players[1].health == 7


def test_pool_solver_reserves_restricted_colors_before_generic_mana():
    from game.enums import ManaType
    from game.ai.mana_solver import PoolManaSolver
    from game.mana.mana_value import ImmutableManaRequirement
    game = make_game()
    player = game.state.active_player
    player.mana_pool.add({ManaType.RED: 1, ManaType.BLUE: 1})
    plan = PoolManaSolver().get_mana_plan(ImmutableManaRequirement({
        frozenset(ManaType): 1, frozenset({ManaType.RED}): 1}), player, game.state)
    assert dict(plan.payment) == {ManaType.RED: 1, ManaType.BLUE: 1}
    assert sum(player.mana_pool.values()) == 2


def test_cleanup_clears_damage_and_phase_transition_clears_mana():
    from game.enums import ManaType
    game = make_game()
    resolve(game, "play beast1")
    beast = game.state.active_player.battlefield["alice-beast1"]
    beast.state.damage_marked = 1
    game.state.active_player.mana_pool.add({ManaType.RED: 1})
    game.state.turn.phase = TurnPhase.CLEANUP
    game.loop.step(game.state)
    assert beast.state.damage_marked == 0
    assert all(not player.mana_pool for player in game.state.players)


def test_temporary_bonus_expires_and_does_not_follow_card_returning_to_battlefield():
    game = make_game()
    resolve(game, "play beast1")
    resolve(game, "play growth1 alice-beast1")
    player = game.state.active_player
    beast = player.battlefield["alice-beast1"]
    assert beast.get_power(game.state) == 4
    player.move_card(beast, ZoneType.HAND)
    player.move_card(beast, ZoneType.BATTLEFIELD)
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 2))
    assert not beast.state.active_cont_effects
    game.state.turn.phase = TurnPhase.CLEANUP
    game.loop.step(game.state)
    assert not game.state._cont_effect_manager._continuous_effects


def test_temporary_bonus_is_removed_at_cleanup_from_unchanged_permanent():
    game = make_game()
    resolve(game, "play beast1")
    resolve(game, "play growth1 alice-beast1")
    beast = game.state.active_player.battlefield["alice-beast1"]
    game.state.turn.phase = TurnPhase.CLEANUP
    game.loop.step(game.state)
    assert beast.get_power(game.state) == 2
    assert beast in game.state.query_cards(EqQuery(IK_POWER, 2))


def test_builder_selects_sacrifice_cost_after_effect_target_and_revalidates_it():
    game = make_game()
    resolve(game, "play beast1")
    resolve(game, "play altar1")
    player = game.state.active_player
    beast = player.battlefield["alice-beast1"]
    action, output = session(game, "activate", ["bob", "options", "confirm"])
    assert beast.get_zone() == ZoneType.BATTLEFIELD
    assert any("Sacrifice" in line for line in output)
    beast.set_controller(game.state.players[1])
    assert not game.loop.processor.process(game.state, action)[0].success
    assert game.state.stack.is_empty()
    beast.set_controller(player)
    assert game.loop.processor.process(game.state, action)[0].success
    assert beast.get_zone() == ZoneType.GRAVEYARD
    assert game.state.players[1].health == 10
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert game.state.players[1].health == 8
