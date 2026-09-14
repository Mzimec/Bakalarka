"""Mana reference enumeration and real casting/land integration regressions."""
from collections import Counter
from dataclasses import replace
from itertools import product
import pytest
from game.enums import ManaType as M, TurnPhase, ZoneType, CardType, SAVariableType
from game.mana.mana_value import ManaValue, ManaPool, ImmutableManaRequirement, generate_mana_options_from_req_pool
from game.console.demo_game import create_demo_game, build_action
from game.cards.demo_cards import LIGHTNING_BOLT_CAST, EMBER_ADEPT_TAP
from game.game_state import Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.rules.lands import basic_land, LandPlayAction
from game.console.command_session import CommandSession
from game.game_actions.data_structs.game_action import AbilityAction, FixedExecutionPlan, ResolutionContext
from game.game_actions.mana_effects import SpendManaOperation, PayLifeOperation
from game.operations.card_operations import TapCardOperation, MoveCardOperation


@pytest.mark.parametrize("cost,value", [("{0}", 0), ("", 0), ("2WU", 4), ("{C}{C}", 2),
    ("{X}{X}{R}", 1), ("{W/U}", 1), ("{2/R}", 2), ("{B/P}", 1), ("{W/U/P}", 1), ("12G", 13)])
def test_parse_and_mana_value(cost, value):
    parsed = ManaValue.parse(cost)
    assert parsed.cmc() == value
    assert parsed.to_mutable().to_immutable() == parsed


@pytest.mark.parametrize("cost", ["{S}", "{Y}", "{C/P}", "{W/W}", "{2/C}", "{-1}", "{R", "R}", "{R}oops", "{W/R/B}"])
def test_invalid_or_unsupported_symbols_are_rejected(cost):
    with pytest.raises(ValueError):
        ManaValue.parse(cost)


COLORS = (M.RED, M.BLUE, M.COLORLESS)
REQUIREMENTS = [(), (frozenset({M.RED}),), (frozenset({M.COLORLESS}),),
                (frozenset({M.RED, M.BLUE}), frozenset(COLORS)),
                (frozenset({M.RED}), frozenset(COLORS), frozenset(COLORS))]


@pytest.mark.parametrize("counts", list(product(range(3), repeat=3)))
@pytest.mark.parametrize("symbols", REQUIREMENTS)
def test_payment_enumerator_matches_independent_brute_force(counts, symbols):
    pool = ManaPool(dict(zip(COLORS, counts)))
    requirement = ImmutableManaRequirement(Counter(symbols))
    expected = set()
    # Independent reference: assign one mana color to each printed requirement.
    for assignment in product(COLORS, repeat=len(symbols)):
        paid = Counter(assignment)
        if all(color in allowed for color, allowed in zip(assignment, symbols)) and all(paid[m] <= pool.get(m, 0) for m in COLORS):
            expected.add(tuple(paid[m] for m in COLORS))
    actual = list(generate_mana_options_from_req_pool(requirement, pool))
    assert {tuple(payment.get(m, 0) for m in COLORS) for payment in actual} == expected
    assert len(actual) == len(expected)
    assert tuple(pool.get(m, 0) for m in COLORS) == counts


def test_repeated_phyrexian_symbols_have_only_distinct_payment_counts():
    # 31 alternatives, not enumeration of 2**30 interchangeable choices.
    options = list(ManaValue.parse("{R/P}" * 30).payment_options())
    assert len(options) == 31
    assert {life for _, life in options} == set(range(0, 61, 2))


@pytest.mark.parametrize("amount", [-1, 1.5, True])
def test_invalid_pool_addition_and_subtraction_are_atomic(amount):
    pool = ManaPool({M.RED: 2})
    for mutate in (pool.add, pool.substract):
        with pytest.raises(ValueError):
            mutate({M.BLUE: 1, M.RED: amount})
        assert dict(pool) == {M.RED: 2}


def game():
    result = create_demo_game((ScriptedController(), ScriptedController()), full_rules=True)
    result.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return result


def add(game, definition, key, zone=ZoneType.HAND):
    player = game.state.active_player
    card = Card(definition, player, key=key)
    player.add_card(card, zone)
    return card


def paid_spell(game, cost):
    return add(game, CardDefinition("Paid spell", mana_cost=cost, types=frozenset({CardType.INSTANT}),
                                   abilities=frozenset({LIGHTNING_BOLT_CAST})), "paid")


@pytest.mark.parametrize("name,mana", [("Plains", M.WHITE), ("Island", M.BLUE), ("Swamp", M.BLACK),
    ("Mountain", M.RED), ("Forest", M.GREEN), ("Wastes", M.COLORLESS)])
def test_land_play_and_intrinsic_mana_are_immediate(name, mana):
    g = game()
    card = add(g, basic_land(name), "land")
    player = g.state.active_player
    assert g.loop.processor.process(g.state, build_action(g.state, player, "play land"))[0].success
    assert card.get_zone() == ZoneType.BATTLEFIELD and g.state.stack.is_empty()
    assert player.lands_played_this_turn == 1
    results = g.loop.processor.process(g.state, build_action(g.state, player, "activate land"))
    assert all(result.success for result in results)
    assert g.state.stack.is_empty() and card.is_tapped
    assert dict(player.mana_pool) == {mana: 1}
    assert not any(event.key == "spell_cast" for event in g.event_bus.emitted_events)


@pytest.mark.parametrize("phase", list(TurnPhase))
def test_land_timing_across_all_steps(phase):
    g = game()
    card = add(g, basic_land("Mountain"), "land")
    g.state.turn.phase = phase
    result = g.loop.processor.process(g.state, LandPlayAction(card, g.state.active_player))
    assert result[0].success == (phase in {TurnPhase.PRECOMBAT_MAIN, TurnPhase.POSTCOMBAT_MAIN})
    if not result[0].success:
        assert card.get_zone() == ZoneType.HAND


def test_land_limit_rejects_prebuilt_second_action_and_resets_next_turn():
    g = game()
    a = add(g, basic_land("Mountain"), "land-a")
    b = add(g, basic_land("Island"), "land-b")
    p = g.state.active_player
    second = LandPlayAction(b, p)
    assert g.loop.processor.process(g.state, LandPlayAction(a, p))[0].success
    assert not g.loop.processor.process(g.state, second)[0].success
    assert b.get_zone() == ZoneType.HAND
    p.land_plays_per_turn = 2
    assert g.loop.processor.process(g.state, second)[0].success
    g.state.turn.number += 2
    g.state.begin_turn()
    assert p.lands_played_this_turn == 0


def test_land_builder_confirms_special_action_without_mutation():
    g = game()
    card = add(g, basic_land("Mountain"), "land")
    inputs = iter(["land", "confirm"])
    action = CommandSession(g.state, g.state.active_player, "play", lambda _: next(inputs), lambda _: None).run()
    assert isinstance(action, LandPlayAction) and card.get_zone() == ZoneType.HAND
    assert g.loop.processor.process(g.state, action)[0].success


@pytest.mark.parametrize("cost,pool,life", [("{1}{R}", {M.RED: 1, M.BLUE: 1}, 0),
    ("{C}", {M.COLORLESS: 1}, 0), ("{W/U}", {M.BLUE: 1}, 0),
    ("{2/R}", {M.BLUE: 2}, 0), ("{B/P}", {}, 2), ("{0}", {}, 0)])
def test_card_definition_cost_is_paid_before_spell_hits_stack(cost, pool, life):
    g = game()
    spell = paid_spell(g, cost)
    p = g.state.active_player
    p.mana_pool.add(pool)
    action = build_action(g.state, p, "play paid bob")
    assert spell.get_zone() == ZoneType.HAND and p.health == 10
    assert g.loop.processor.process(g.state, action)[0].success
    assert not p.mana_pool and p.health == 10 - life
    assert spell.get_zone() == ZoneType.STACK and len(g.state.stack.items) == 1
    assert g.loop.processor.executor.resolve(g.state, g.state.stack.pop()).success
    assert g.state.players[1].health == 7


def test_colored_mana_cannot_pay_explicit_colorless():
    g = game()
    paid_spell(g, "{C}")
    g.state.active_player.mana_pool.add({M.RED: 5})
    with pytest.raises(ValueError):
        build_action(g.state, g.state.active_player, "play paid bob")


def test_no_mana_cost_is_not_the_same_as_zero_cost():
    g = game()
    spell = paid_spell(g, None)
    with pytest.raises(ValueError, match="without a mana cost"):
        build_action(g.state, g.state.active_player, "play paid bob")
    assert spell.get_zone() == ZoneType.HAND


def test_x_cost_and_stack_mana_value():
    g = game()
    spell = paid_spell(g, "{X}{X}{R}")
    p = g.state.active_player
    p.mana_pool.add({M.RED: 5})
    action = build_action(g.state, p, "play paid x=2 bob")
    assert action.action_generator.param_context.x_variables[SAVariableType.X] == 2
    assert spell.get_mana_value(g.state) == 1
    assert g.loop.processor.process(g.state, action)[0].success
    assert not p.mana_pool and spell.get_mana_value(g.state) == 5
    g.loop.processor.executor.resolve(g.state, g.state.stack.pop())
    assert spell.get_mana_value(g.state) == 1


@pytest.mark.parametrize("duplicate", ["mana", "tap", "life", "sacrifice"])
def test_combined_cost_preflight_never_partially_pays(duplicate):
    g = game()
    source = add(g, CardDefinition("Source", types=frozenset({CardType.ARTIFACT})), "source", ZoneType.BATTLEFIELD)
    p = g.state.active_player
    p.mana_pool.add({M.RED: 1})
    ctx = ResolutionContext(source=source, controller=p, is_cost=True)
    op = {"mana": lambda: SpendManaOperation(ctx, {M.RED: 1}), "tap": lambda: TapCardOperation(ctx, source),
          "life": lambda: PayLifeOperation(ctx, 6), "sacrifice": lambda: MoveCardOperation(ctx, source, ZoneType.GRAVEYARD)}[duplicate]
    costs = [op(), op()]
    action = AbilityAction("cost_test", source, FixedExecutionPlan([]), FixedExecutionPlan(costs), controller=p)
    assert not g.loop.processor.process(g.state, action)[0].success
    assert p.health == 10 and dict(p.mana_pool) == {M.RED: 1}
    assert not source.is_tapped and source.get_zone() == ZoneType.BATTLEFIELD
    assert g.state.stack.is_empty() and not g.event_bus.emitted_events
