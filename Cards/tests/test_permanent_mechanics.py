"""CR 111, 301, 303, 306, 606, 704: regressions through the real pipeline."""
from dataclasses import replace
from itertools import product

import pytest
from immutabledict import immutabledict

from game.enums import CardType as T, CardSubtype as S, CounterType as C, ZoneType as Z, TurnPhase as P
from game.game_state import Card, CardDefinition, Player, State
from game.game_state.player import DecisionMaker
from game.game_state.modifier import AddIntModifier
from game.stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_TYPES
from game.game_actions.data_structs.ability import AbilityDefinition, SubAbilityDefinition, TriggerAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.data_structs.game_action import ResolutionContext, ScheduledResolution, FixedExecutionPlan, AbilityAction
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.triggers.trigger_condition import DiesCondition, EntersBattlefieldCondition
from game.game_actions.permanent_effects import attachment_ability, CreateTokenEffect
from game.operations.card_operations import MoveCardOperation, DamageCreatureOperation, DamagePlayerOperation
from game.rules.permanents import CreateTokenOperation, AttachOperation
from game.console.demo_game import build_action
from game.console.combat_commands import parse_attackers
from game.console.console_commands import CommandError


class Controller(DecisionMaker):
    def get_action(self, state, player):
        return None

    def choose_legend(self, state, player, cards):
        return cards[-1]


@pytest.fixture
def game():
    state = State([Player([], Controller(), name="Alice"), Player([], Controller(), name="Bob")])
    state.turn.phase = P.PRECOMBAT_MAIN
    bus = EventBus()
    engine = ResolutionEngine(OperationExecutor(), bus)
    return state, engine, ActionProcessor(engine), bus


UNIT = CardDefinition("Unit", types=frozenset({T.CREATURE}), power=2, toughness=2)
WALKER = CardDefinition("Walker", types=frozenset({T.PLANESWALKER}), loyalty=4, legendary=True)
BONUS = immutabledict({STAT_POWER: (AddIntModifier(2),), STAT_TOUGHNESS: (AddIntModifier(2),)})


def add(game, definition=UNIT, player=0, zone=Z.BATTLEFIELD, key=None):
    owner = game[0].players[player]
    card = Card(definition, owner, key)
    owner.add_card(card, zone)
    card.controlled_since = 0
    return card


def context(game, source=None):
    return ResolutionContext(source=source, controller=game[0].active_player)


def resolve(game, *operations):
    return game[1].resolve(game[0], ScheduledResolution(FixedExecutionPlan(list(operations)), context(game)))


def activate(game, card, cost, *, effects=(), costs=()):
    definition = AbilityDefinition(key="loyalty", loyalty_cost=cost)
    action = AbilityAction("loyalty", card, FixedExecutionPlan(list(effects)), FixedExecutionPlan(list(costs)),
                           controller=card.get_controller(game[0]), ability=definition)
    return game[2].process(game[0], action)


@pytest.mark.parametrize("count", [0, 1, 2, 8])
def test_token_group_creation_and_summoning_sickness(game, count):
    operation = CreateTokenOperation(context(game), UNIT, count)
    assert resolve(game, operation).success
    assert len(operation.created) == count
    assert len({c.key for c in operation.created}) == count
    assert all(c.is_token and c.get_zone() == Z.BATTLEFIELD and c.is_summoning_sick(game[0]) for c in operation.created)
    assert len([e for e in game[3].emitted_events if e.key == "card_moved"]) == count


@pytest.mark.parametrize("destination", [Z.GRAVEYARD, Z.EXILE, Z.HAND, Z.DECK])
def test_token_cannot_return_even_before_sba(game, destination):
    operation = CreateTokenOperation(context(game), UNIT)
    resolve(game, operation)
    token, = operation.created
    resolve(game, MoveCardOperation(context(game), token, destination),
            MoveCardOperation(context(game), token, Z.BATTLEFIELD))
    assert token.get_zone() == destination
    assert token._game_state is None
    assert token not in game[0].get_cards()
    token.owner.add_card(token, Z.BATTLEFIELD)
    assert token not in game[0].get_cards()


def test_token_dies_trigger_survives_token_ceasing_to_exist(game):
    definition = replace(UNIT, triggers=frozenset({TriggerAbilityDefinition(key="dies", condition=DiesCondition(source_only=True))}))
    operation = CreateTokenOperation(context(game), definition)
    resolve(game, operation)
    token, = operation.created
    resolve(game, DamageCreatureOperation(context(game), token, 2))
    assert token._game_state is None
    assert len(game[0].stack.items) == 1
    assert game[1].resolve(game[0], game[0].stack.pop()).success


def test_simultaneous_tokens_see_each_others_entry(game):
    trigger = TriggerAbilityDefinition(key="etb", condition=EntersBattlefieldCondition())
    operation = CreateTokenOperation(context(game), replace(UNIT, triggers=frozenset({trigger})), 3)
    resolve(game, operation)
    assert len(game[0].stack.items) == 9


@pytest.mark.parametrize("same_controller,same_name,legendary", tuple(product([False, True], repeat=3)))
def test_legend_rule_groups_by_controller_and_name(game, same_controller, same_name, legendary):
    definition = replace(UNIT, legendary=legendary, keywords=frozenset({"indestructible"}))
    first = add(game, definition)
    second = add(game, replace(definition, name=definition.name if same_name else "Other"), player=0 if same_controller else 1)
    game[1].settle(game[0])
    assert first.get_zone() == (Z.GRAVEYARD if same_controller and same_name and legendary else Z.BATTLEFIELD)
    assert second.get_zone() == Z.BATTLEFIELD


def test_legend_rule_uses_effective_controller_not_owner(game):
    first = add(game, WALKER)
    second = add(game, WALKER, player=1)
    second.set_controller(first.owner)
    game[1].settle(game[0])
    assert first.get_zone() == Z.GRAVEYARD and second.get_zone() == Z.BATTLEFIELD


@pytest.mark.parametrize("damage", range(7))
def test_planeswalker_damage_removes_loyalty_and_zero_dies(game, damage):
    walker = add(game, replace(WALKER, keywords=frozenset({"indestructible"})))
    resolve(game, DamageCreatureOperation(context(game), walker, damage))
    assert walker.get_zone() == (Z.GRAVEYARD if damage >= 4 else Z.BATTLEFIELD)
    if damage < 4:
        assert walker.state.counters[C.LOYALTY] == 4 - damage
        assert walker.state.damage_marked == 0


def test_animated_planeswalker_receives_both_damage_results(game):
    walker = add(game, replace(WALKER, types=frozenset({T.CREATURE, T.PLANESWALKER}), power=4, toughness=5))
    resolve(game, DamageCreatureOperation(context(game), walker, 2))
    assert walker.state.counters[C.LOYALTY] == 2 and walker.state.damage_marked == 2


@pytest.mark.parametrize("cost", [-5, -4, -2, 0, 1, 3])
def test_loyalty_payment_and_once_per_permanent(game, cost):
    walker = add(game, WALKER)
    result = activate(game, walker, cost)
    if cost < -4:
        assert not result[0].success
        assert walker.state.counters[C.LOYALTY] == 4 and not game[0].stack.items
        return
    assert result[0].success and len(game[0].stack.items) == 1
    assert walker.get_zone() == (Z.GRAVEYARD if cost == -4 else Z.BATTLEFIELD)
    assert game[1].resolve(game[0], game[0].stack.pop()).success
    if cost != -4:
        assert walker.state.counters[C.LOYALTY] == 4 + cost
        assert not activate(game, walker, 1)[0].success


@pytest.mark.parametrize("phase", list(P))
def test_loyalty_timing(game, phase):
    walker = add(game, WALKER)
    game[0].turn.phase = phase
    assert activate(game, walker, 1)[0].success == (phase in {P.PRECOMBAT_MAIN, P.POSTCOMBAT_MAIN})


def test_loyalty_failed_other_cost_leaves_counters_and_activation_available(game):
    from game.game_actions.mana_effects import PayLifeOperation
    walker = add(game, WALKER)
    assert not activate(game, walker, 1, costs=[PayLifeOperation(context(game, walker), 100)])[0].success
    assert walker.state.counters[C.LOYALTY] == 4 and walker.loyalty_activated_turn is None
    assert activate(game, walker, 1)[0].success


def test_loyalty_reset_after_blink_but_not_control_change(game):
    walker = add(game, WALKER)
    assert activate(game, walker, 0)[0].success
    game[1].resolve(game[0], game[0].stack.pop())
    walker.set_controller(game[0].players[1])
    walker.set_controller(game[0].players[0])
    assert not activate(game, walker, 1)[0].success
    resolve(game, MoveCardOperation(context(game), walker, Z.EXILE), MoveCardOperation(context(game), walker, Z.BATTLEFIELD))
    assert walker.state.counters[C.LOYALTY] == 4
    assert activate(game, walker, 1)[0].success


def test_loyalty_ability_works_via_command_builder(game):
    effect = CreateTokenEffect("soldier", UNIT)
    ability = AbilityDefinition(key="recruit", loyalty_cost=-1, action_subdefs=(SubAbilityDefinition(
        action_node=EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()})), effects=frozenset({effect})),))
    walker = add(game, replace(WALKER, abilities=frozenset({ability})), key="walker")
    action = build_action(game[0], walker.owner, "activate walker recruit")
    assert game[2].process(game[0], action)[0].success
    assert walker.state.counters[C.LOYALTY] == 3
    assert not any(c.is_token for c in game[0].get_cards())
    assert game[1].resolve(game[0], game[0].stack.pop()).success
    assert len([c for c in game[0].get_cards() if c.is_token]) == 1


def attachment(game, *, aura=False, zone=Z.BATTLEFIELD, cost=None):
    ability = attachment_ability(equip=not aura, mana_cost=cost)
    definition = CardDefinition("Aura" if aura else "Equipment", types=frozenset({T.ENCHANTMENT if aura else T.ARTIFACT}),
                                subtypes=frozenset({S.AURA if aura else S.EQUIPMENT}),
                                abilities=frozenset({ability}), attach_mods=BONUS)
    return add(game, definition, zone=zone, key="aura" if aura else "equipment")


@pytest.mark.parametrize("aura", [False, True])
def test_attachment_command_resolves_bonus_and_host_departure(game, aura):
    host = add(game, key="host")
    item = attachment(game, aura=aura, zone=Z.HAND if aura else Z.BATTLEFIELD)
    command = "play aura host" if aura else "activate equipment equip host"
    assert game[2].process(game[0], build_action(game[0], host.owner, command))[0].success
    assert item.attached_to is None and host.get_power(game[0]) == 2
    assert game[1].resolve(game[0], game[0].stack.pop()).success
    assert item.attached_to is host and host.get_power(game[0]) == 4
    resolve(game, MoveCardOperation(context(game), host, Z.HAND))
    assert item.attached_to is None and not host.state.attached
    assert item.get_zone() == (Z.GRAVEYARD if aura else Z.BATTLEFIELD)
    assert host.get_power(game[0]) == 2


@pytest.mark.parametrize("aura", [False, True])
def test_attachment_target_blinks_before_resolution(game, aura):
    host = add(game, key="host")
    item = attachment(game, aura=aura, zone=Z.HAND if aura else Z.BATTLEFIELD)
    command = "play aura host" if aura else "activate equipment equip host"
    game[2].process(game[0], build_action(game[0], host.owner, command))
    resolve(game, MoveCardOperation(context(game), host, Z.EXILE), MoveCardOperation(context(game), host, Z.BATTLEFIELD))
    assert not game[1].resolve(game[0], game[0].stack.pop()).success
    assert item.attached_to is None and host.get_power(game[0]) == 2
    assert item.get_zone() == (Z.GRAVEYARD if aura else Z.BATTLEFIELD)


@pytest.mark.parametrize("aura", [False, True])
def test_attachment_detaches_when_host_loses_creature_type(game, aura):
    host = add(game)
    item = attachment(game, aura=aura)
    resolve(game, AttachOperation(context(game), item, host))
    host.set_base_stat(STAT_TYPES, frozenset({T.ARTIFACT}))
    game[1].settle(game[0])
    assert item.attached_to is None
    assert item.get_zone() == (Z.GRAVEYARD if aura else Z.BATTLEFIELD)


def test_equipment_moves_between_hosts_and_stays_attached_after_control_change(game):
    first, second = add(game), add(game)
    item = attachment(game)
    resolve(game, AttachOperation(context(game), item, first), AttachOperation(context(game), item, second))
    assert first.get_power(game[0]) == 2 and second.get_power(game[0]) == 4
    second.set_controller(game[0].players[1])
    game[1].settle(game[0])
    assert item.attached_to is second and item.get_controller(game[0]) is first.owner


@pytest.mark.parametrize("keyword", ["shroud", "hexproof"])
def test_aura_non_targeted_entry_ignores_targeting_restrictions(game, keyword):
    host = add(game, replace(UNIT, keywords=frozenset({keyword})), player=1, key="host")
    aura = attachment(game, aura=True, zone=Z.HAND)
    with pytest.raises(CommandError):
        build_action(game[0], aura.owner, "play aura host")
    resolve(game, AttachOperation(context(game), aura, host, entering=True))
    assert aura.attached_to is host


@pytest.mark.parametrize("blocked,trample", tuple(product([False, True], repeat=2)))
def test_planeswalker_combat_and_no_trample_redirect_to_player(game, blocked, trample):
    state = game[0]
    attacker = add(game, replace(UNIT, power=6, toughness=6, keywords=frozenset({"trample"}) if trample else frozenset()), key="attacker")
    walker = add(game, WALKER, player=1, key="walker")
    blocker = add(game, player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    declarations = parse_attackers("attack attacker:walker", state, state.active_player)
    state.combat.declare_attackers(state.active_player, declarations)
    state.turn.phase = P.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], {blocker: attacker} if blocked else {})
    state.turn.phase = P.SECOND_COMBAT_DAMAGE
    health = walker.owner.health
    assert game[1].resolve_simultaneous(state, state.combat.damage_operations()).success
    assert walker.owner.health == health
    assert walker.get_zone() == (Z.BATTLEFIELD if blocked and not trample else Z.GRAVEYARD)


@pytest.mark.parametrize("blink", [False, True])
def test_vanished_defender_gets_no_damage_and_no_redirection(game, blink):
    state = game[0]
    attacker = add(game)
    walker = add(game, WALKER, player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.active_player, {attacker: walker})
    resolve(game, MoveCardOperation(context(game), walker, Z.EXILE))
    if blink:
        resolve(game, MoveCardOperation(context(game), walker, Z.BATTLEFIELD))
    state.turn.phase = P.SECOND_COMBAT_DAMAGE
    assert state.combat.damage_operations() == ()
    assert attacker in state.combat.attackers


@pytest.mark.parametrize("infect,wither,lifelink", tuple(product([False, True], repeat=3)))
def test_damage_keyword_results(game, infect, wither, lifelink):
    keywords = frozenset(k for k, enabled in [("infect", infect), ("wither", wither), ("lifelink", lifelink)] if enabled)
    source = add(game, replace(UNIT, keywords=keywords))
    target = add(game, replace(UNIT, toughness=6), player=1)
    player = target.owner
    own_health, enemy_health = source.owner.health, player.health
    resolve(game, DamageCreatureOperation(context(game, source), target, 2), DamagePlayerOperation(context(game, source), player, 2))
    assert target.state.counters.get(C.MINUS_ONE, 0) == (2 if infect or wither else 0)
    assert target.state.damage_marked == (0 if infect or wither else 2)
    assert player.poison_counters == (2 if infect else 0)
    assert player.health == enemy_health - (0 if infect else 2)
    assert source.owner.health == own_health + (4 if lifelink else 0)


@pytest.mark.parametrize("plus,minus", tuple(product(range(4), repeat=2)))
def test_opposite_counters_cancel_in_sba(game, plus, minus):
    unit = add(game, replace(UNIT, toughness=10))
    unit.state.counters[C.PLUS_ONE] = plus
    unit.state.counters[C.MINUS_ONE] = minus
    game[1].settle(game[0])
    assert unit.state.counters.get(C.PLUS_ONE, 0) == max(0, plus - minus)
    assert unit.state.counters.get(C.MINUS_ONE, 0) == max(0, minus - plus)
    assert unit.get_power(game[0]) == 2 + plus - minus


def test_infect_poison_loss(game):
    source = add(game, replace(UNIT, keywords=frozenset({"infect"})))
    resolve(game, DamagePlayerOperation(context(game, source), game[0].players[1], 10))
    assert game[0].players[1].loss_reason == "poison"


def test_two_loyalty_payments_are_rejected_before_either_is_paid(game):
    from game.rules.permanents import LoyaltyCostOperation
    walker = add(game, WALKER)
    assert not activate(game, walker, 1, costs=[LoyaltyCostOperation(context(game, walker), 1)])[0].success
    assert walker.state.counters[C.LOYALTY] == 4 and walker.loyalty_activated_turn is None


def test_loyalty_on_nonwalker_and_new_turn(game):
    unit = add(game)
    assert activate(game, unit, 1)[0].success
    game[1].resolve(game[0], game[0].stack.pop())
    assert unit.state.counters[C.LOYALTY] == 1
    game[0].turn.number += 1
    assert activate(game, unit, -1)[0].success
    assert unit.get_zone() == Z.BATTLEFIELD


def test_loyalty_rejected_during_opponents_turn_even_with_priority(game):
    walker = add(game, WALKER, player=1)
    game[0].priority.current_player = walker.owner
    assert AbilityDefinition(loyalty_cost=1).validation_error(walker, walker.owner, game[0])
    assert walker.state.counters[C.LOYALTY] == 4


def test_equip_requires_mana_and_own_target_at_activation_and_resolution(game):
    from game.enums import ManaType
    host = add(game, key="host")
    item = attachment(game, cost="{1}")
    with pytest.raises(CommandError):
        build_action(game[0], item.owner, "activate equipment equip host")
    item.owner.mana_pool.add({ManaType.COLORLESS: 1})
    assert game[2].process(game[0], build_action(game[0], item.owner, "activate equipment equip host"))[0].success
    assert not item.owner.mana_pool.get(ManaType.COLORLESS, 0)
    host.set_controller(game[0].players[1])
    assert not game[1].resolve(game[0], game[0].stack.pop()).success
    assert item.attached_to is None


def test_attachment_removal_can_kill_former_host(game):
    host = add(game)
    item = attachment(game)
    resolve(game, AttachOperation(context(game), item, host), DamageCreatureOperation(context(game), host, 3))
    assert host.get_zone() == Z.BATTLEFIELD
    resolve(game, MoveCardOperation(context(game), item, Z.HAND))
    assert host.get_zone() == Z.GRAVEYARD


def test_custom_enchant_restriction_is_enforced_after_control_change(game):
    host = add(game)
    aura = attachment(game, aura=True)
    restricted = replace(aura.definition, enchant=lambda state, source, target: target.get_controller(state) is source.get_controller(state))
    # Create the restricted definition directly, without editing shared card data.
    aura.owner.remove_card(aura)
    aura = add(game, restricted)
    resolve(game, AttachOperation(context(game), aura, host))
    assert aura.attached_to is host
    host.set_controller(game[0].players[1])
    game[1].settle(game[0])
    assert aura.get_zone() == Z.GRAVEYARD


@pytest.mark.parametrize("change", ["control", "type"])
def test_defender_removed_from_combat_does_not_return_when_change_is_reversed(game, change):
    state = game[0]
    attacker = add(game)
    walker = add(game, WALKER, player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.active_player, {attacker: walker})
    if change == "control":
        walker.set_controller(state.active_player)
        walker.set_controller(state.players[1])
    else:
        walker.set_base_stat(STAT_TYPES, frozenset({T.ARTIFACT}))
        state.combat.prune()
        walker.set_base_stat(STAT_TYPES, frozenset({T.PLANESWALKER}))
    state.turn.phase = P.SECOND_COMBAT_DAMAGE
    assert state.combat.damage_operations() == ()


def test_defending_player_can_still_block_after_walker_leaves(game):
    state = game[0]
    attacker, blocker = add(game), add(game, player=1)
    walker = add(game, WALKER, player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.active_player, {attacker: walker})
    resolve(game, MoveCardOperation(context(game), walker, Z.HAND))
    state.turn.phase = P.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], {blocker: attacker})
    state.turn.phase = P.SECOND_COMBAT_DAMAGE
    assert game[1].resolve_simultaneous(state, state.combat.damage_operations()).success
    assert attacker.get_zone() == blocker.get_zone() == Z.GRAVEYARD


def test_double_strike_does_not_hit_player_after_walker_dies(game):
    state = game[0]
    attacker = add(game, replace(UNIT, power=6, keywords=frozenset({"double strike", "trample"})))
    walker = add(game, WALKER, player=1)
    state.combat.begin()
    state.turn.phase = P.DECLARE_ATTACKERS
    state.combat.declare_attackers(state.active_player, {attacker: walker})
    state.turn.phase = P.FIRST_COMBAT_DAMAGE
    game[1].resolve_simultaneous(state, state.combat.damage_operations(first_strike=True))
    assert walker.get_zone() == Z.GRAVEYARD
    state.turn.phase = P.SECOND_COMBAT_DAMAGE
    assert state.combat.damage_operations() == ()


def test_opposite_counters_do_not_save_zero_toughness_creature(game):
    unit = add(game, replace(UNIT, triggers=frozenset({TriggerAbilityDefinition(key="dies", condition=DiesCondition(source_only=True))})))
    unit.state.counters[C.PLUS_ONE] = 1
    unit.state.counters[C.MINUS_ONE] = 3
    game[1].settle(game[0])
    assert unit.get_zone() == Z.GRAVEYARD and len(game[0].stack.items) == 1


def test_new_demo_cards_are_playable_and_displayed(game):
    from game.console.demo_game import create_demo_game, format_state
    from game.game_loop.minimal_game import ScriptedController
    demo = create_demo_game((ScriptedController([]), ScriptedController([])), expanded=True)
    state = demo.state
    state.turn.phase = P.PRECOMBAT_MAIN
    player = state.active_player
    processor = ActionProcessor(ResolutionEngine(OperationExecutor(), demo.event_bus))
    assert processor.process(state, build_action(state, player, "play marshal1"))[0].success
    assert processor.executor.resolve(state, state.stack.pop()).success
    assert "loyalty 3" in format_state(state)
    assert processor.process(state, build_action(state, player, "activate marshal1 recruit"))[0].success
    assert processor.executor.resolve(state, state.stack.pop()).success
    token = next(card for card in state.get_cards() if card.is_token)
    assert processor.process(state, build_action(state, player, f"play aura1 {token.key}"))[0].success
    assert processor.executor.resolve(state, state.stack.pop()).success
    assert token.get_power(state) == 2
    assert "attached to" in format_state(state) and "token" in format_state(state)


def test_aura_printed_cost_is_paid_exactly_once(game):
    from game.enums import ManaType
    host = add(game, key="host")
    definition = CardDefinition("Paid Aura", mana_cost="{W}", types=frozenset({T.ENCHANTMENT}),
                                subtypes=frozenset({S.AURA}), abilities=frozenset({attachment_ability()}))
    aura = add(game, definition, zone=Z.HAND, key="paid-aura")
    with pytest.raises(CommandError):
        build_action(game[0], aura.owner, "play paid-aura host")
    aura.owner.mana_pool.add({ManaType.WHITE: 1})
    action = build_action(game[0], aura.owner, "play paid-aura host")
    assert game[2].process(game[0], action)[0].success
    assert not aura.owner.mana_pool.get(ManaType.WHITE, 0)
    assert game[1].resolve(game[0], game[0].stack.pop()).success
    assert aura.attached_to is host
