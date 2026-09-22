"""Combat rules exercised against real state, operations, replacements, and SBA."""
from game.ai.decision_maker import CombatDamageOption, DecisionResult, PriorityDecisionRequest
from game.game_actions import PassPriorityAction
from itertools import product

import pytest

from game.rules.combat import CombatError, CombatState
from game.console.combat_commands import parse_attackers, parse_blockers
from game.console.console_commands import CommandError
from game.enums import CardType, TurnPhase, ZoneType
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.resolution.sba_resolver import LethalCreaturesRule
from game.game_state import Card, CardDefinition, Player, State
from game.ai.decision_maker import ModularDecisionMaker as DecisionMaker
from game.stat_type import STAT_TYPES


class PassiveController(DecisionMaker):
    def decide_priority(self, request):
        state = request.state; player = request.player
        return DecisionResult(PassPriorityAction(player))


@pytest.fixture
def game():
    state = State([Player([], PassiveController(), name="Alice"), Player([], PassiveController(), name="Bob")])
    state.combat = CombatState(state)
    for player in state.players:
        player.last_turn_started = 1
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    bus = EventBus()
    engine = ResolutionEngine(OperationExecutor(), bus, [LethalCreaturesRule()])
    return state, engine, bus


def creature(game, player=0, power=2, toughness=2, keywords=(), mature=True):
    state = game[0]
    owner = state.players[player]
    card = Card(CardDefinition("Creature", types=frozenset({CardType.CREATURE}),
                               power=power, toughness=toughness, keywords=frozenset(keywords)), owner)
    owner.add_card(card, ZoneType.BATTLEFIELD, state)
    if mature:
        card.controlled_since = 0
    return card


def declare(game, attackers, blockers=()):
    state = game[0]
    state.combat.declare_attackers(state.players[0], attackers)
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], blockers)


def damage(game, first=False, assignments=None):
    state, engine, bus = game
    state.turn.phase = TurnPhase.FIRST_COMBAT_DAMAGE if first else TurnPhase.SECOND_COMBAT_DAMAGE
    operations = state.combat.damage_operations(first_strike=first, assignments=assignments)
    result = engine.resolve_simultaneous(state, operations)
    assert result.success
    return result


@pytest.mark.parametrize("power", range(-2, 9))
def test_unblocked_damage_uses_nonnegative_power(game, power):
    attacker = creature(game, power=power)
    declare(game, [attacker])
    damage(game)
    assert game[0].players[1].health == 20 - max(0, power)
    assert attacker.is_tapped
    assert attacker.state.damage_marked == 0


@pytest.mark.parametrize("ap,at,bp,bt", list(product(range(1, 5), repeat=4)))
def test_damage_exchange_is_simultaneous_for_creature_stat_matrix(game, ap, at, bp, bt):
    attacker = creature(game, power=ap, toughness=at)
    blocker = creature(game, 1, bp, bt)
    declare(game, [attacker], {blocker: attacker})
    damage(game)
    assert attacker.get_zone() == (ZoneType.GRAVEYARD if bp >= at else ZoneType.BATTLEFIELD)
    assert blocker.get_zone() == (ZoneType.GRAVEYARD if ap >= bt else ZoneType.BATTLEFIELD)
    assert game[0].players[1].health == 20
    assert not blocker.is_tapped
    if bp < at:
        assert attacker.state.damage_marked == bp
    if ap < bt:
        assert blocker.state.damage_marked == ap
    dealt = [event for event in game[2].emitted_events if event.key == "damage_dealt"]
    assert len(dealt) == 2
    assert all(event.payload["combat"] for event in dealt)


@pytest.mark.parametrize("problem", ["tapped", "sick", "defender", "foreign", "not_creature", "hand"])
def test_invalid_attacker_keeps_whole_declaration_unchanged(game, problem):
    state = game[0]
    legal = creature(game)
    bad = creature(game, keywords=("defender",) if problem == "defender" else ())
    if problem == "tapped":
        bad.is_tapped = True
    elif problem == "sick":
        bad.controlled_since = 1
    elif problem == "foreign":
        bad.set_controller(state.players[1])
    elif problem == "not_creature":
        bad.set_base_stat(STAT_TYPES, {CardType.ARTIFACT})
    elif problem == "hand":
        bad.owner.move_card(bad, ZoneType.HAND, state)
    with pytest.raises(CombatError):
        state.combat.declare_attackers(state.players[0], [legal, bad])
    assert not legal.is_tapped
    assert not state.combat.attackers


def test_haste_allows_new_creature_to_attack_and_vigilance_preserves_untapped(game):
    attacker = creature(game, keywords=("haste", "vigilance"), mature=False)
    declare(game, [attacker])
    assert not attacker.is_tapped
    damage(game)
    assert game[0].players[1].health == 18


def test_attackers_cannot_be_declared_twice_or_by_defending_player(game):
    state = game[0]
    attacker = creature(game)
    with pytest.raises(CombatError, match="active player"):
        state.combat.declare_attackers(state.players[1], [])
    state.combat.declare_attackers(state.players[0], [attacker])
    with pytest.raises(CombatError, match="already"):
        state.combat.declare_attackers(state.players[0], [])


def test_duplicate_attacker_and_self_attack_are_rejected(game):
    state = game[0]
    attacker = creature(game)
    with pytest.raises(CombatError, match="twice"):
        state.combat.declare_attackers(state.players[0], [attacker, attacker])
    with pytest.raises(CombatError, match="opposing player"):
        state.combat.declare_attackers(state.players[0], {attacker: state.players[0]})


@pytest.mark.parametrize("phase", [TurnPhase.PRECOMBAT_MAIN, TurnPhase.BEGIN_COMBAT, TurnPhase.AFTER_ATTACKERS,
                                 TurnPhase.DECLARE_BLOCKERS, TurnPhase.END_COMBAT])
def test_attack_declaration_obeys_step_timing(game, phase):
    state = game[0]
    state.turn.phase = phase
    with pytest.raises(CombatError, match="declare attackers step"):
        state.combat.declare_attackers(state.players[0], [])


@pytest.mark.parametrize("attacker_kw,blocker_kw,legal", [
    ((), (), True), (("flying",), (), False), (("flying",), ("reach",), True),
    (("flying",), ("flying",), True), ((), ("flying",), True), ((), ("reach",), True),
    (("unblockable",), ("flying", "reach"), False), (("can't be blocked",), (), False),
    ((), ("can't block",), False), ((), ("defender",), True),
])
def test_blocking_keyword_legality(game, attacker_kw, blocker_kw, legal):
    state = game[0]
    attacker = creature(game, keywords=attacker_kw)
    blocker = creature(game, 1, keywords=blocker_kw, mature=False)
    state.combat.declare_attackers(state.players[0], [attacker])
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    if legal:
        state.combat.declare_blockers(state.players[1], {blocker: attacker})
        assert state.combat.blockers == {blocker: attacker}
        assert not blocker.is_tapped
    else:
        with pytest.raises(CombatError):
            state.combat.declare_blockers(state.players[1], {blocker: attacker})
        assert not state.combat.blockers


def test_tapped_blocker_and_duplicate_blocker_fail_atomically(game):
    state = game[0]
    first, second = creature(game), creature(game)
    blocker = creature(game, 1)
    state.combat.declare_attackers(state.players[0], [first, second])
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    with pytest.raises(CombatError, match="multiple attackers"):
        state.combat.declare_blockers(state.players[1], [(blocker, first), (blocker, second)])
    blocker.is_tapped = True
    with pytest.raises(CombatError):
        state.combat.declare_blockers(state.players[1], {blocker: first})
    assert not state.combat.blockers
    assert not state.combat.blocked


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_menace_requires_zero_or_at_least_two_blockers(game, count):
    state = game[0]
    attacker = creature(game, keywords=("menace",))
    blockers = {creature(game, 1): attacker for _ in range(count)}
    state.combat.declare_attackers(state.players[0], [attacker])
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    if count == 1:
        with pytest.raises(CombatError, match="Menace"):
            state.combat.declare_blockers(state.players[1], blockers)
        assert not state.combat.blockers
    else:
        state.combat.declare_blockers(state.players[1], blockers)
        assert len(state.combat.blockers) == count


def test_blocker_can_tap_after_declaration_and_still_deal_damage(game):
    attacker, blocker = creature(game, toughness=5), creature(game, 1, toughness=5)
    declare(game, [attacker], {blocker: attacker})
    blocker.is_tapped = True
    damage(game)
    assert attacker.state.damage_marked == 2


@pytest.mark.parametrize("trample", [False, True])
@pytest.mark.parametrize("change", ["exile", "blink", "controller", "type"])
def test_removed_blocker_leaves_attacker_blocked(game, trample, change):
    state = game[0]
    attacker = creature(game, power=5, keywords=("trample",) if trample else ())
    blocker = creature(game, 1)
    declare(game, [attacker], {blocker: attacker})
    if change in {"exile", "blink"}:
        blocker.owner.move_card(blocker, ZoneType.EXILE, state)
        if change == "blink":
            blocker.owner.move_card(blocker, ZoneType.BATTLEFIELD, state)
    elif change == "controller":
        blocker.set_controller(state.players[0])
    else:
        blocker.set_base_stat(STAT_TYPES, {CardType.ARTIFACT})
    damage(game)
    assert state.players[1].health == (15 if trample else 20)
    assert attacker.state.damage_marked == 0


@pytest.mark.parametrize("change", ["exile", "blink", "controller", "type"])
def test_removed_attacker_and_its_blocker_deal_no_damage(game, change):
    state = game[0]
    attacker, blocker = creature(game), creature(game, 1)
    declare(game, [attacker], {blocker: attacker})
    if change in {"exile", "blink"}:
        attacker.owner.move_card(attacker, ZoneType.EXILE, state)
        if change == "blink":
            attacker.owner.move_card(attacker, ZoneType.BATTLEFIELD, state)
    elif change == "controller":
        attacker.set_controller(state.players[1])
    else:
        attacker.set_base_stat(STAT_TYPES, {CardType.ARTIFACT})
    damage(game)
    assert blocker.state.damage_marked == 0
    assert attacker.state.damage_marked == 0


@pytest.mark.parametrize("power,toughness", list(product(range(1, 7), range(1, 5))))
def test_trample_deals_only_excess_after_lethal_assignment(game, power, toughness):
    attacker = creature(game, power=power, toughness=10, keywords=("trample",))
    blocker = creature(game, 1, 1, toughness)
    declare(game, [attacker], {blocker: attacker})
    damage(game)
    assert game[0].players[1].health == 20 - max(0, power - toughness)


def test_deathtouch_trample_requires_only_one_damage_for_each_blocker(game):
    attacker = creature(game, power=5, toughness=20, keywords=("deathtouch", "trample"))
    blockers = [creature(game, 1, 1, 10) for _ in range(2)]
    declare(game, [attacker], {b: attacker for b in blockers})
    damage(game)
    assert all(b.get_zone() == ZoneType.GRAVEYARD for b in blockers)
    assert game[0].players[1].health == 17


def test_trample_counts_damage_marked_but_still_assigns_lethal_to_indestructible(game):
    attacker = creature(game, power=5, toughness=20, keywords=("trample",))
    blocker = creature(game, 1, 1, 5, ("indestructible",))
    blocker.state.damage_marked = 3
    declare(game, [attacker], {blocker: attacker})
    damage(game)
    assert game[0].players[1].health == 17
    assert blocker.get_zone() == ZoneType.BATTLEFIELD
    assert blocker.state.damage_marked == 5


def test_modern_rules_allow_splitting_damage_without_lethal_assignment_order(game):
    attacker = creature(game, power=3, toughness=10)
    first, second = creature(game, 1, 1, 3), creature(game, 1, 1, 3)
    declare(game, [attacker], {first: attacker, second: attacker})
    damage(game, assignments={attacker: {first: 1, second: 2}})
    assert (first.state.damage_marked, second.state.damage_marked) == (1, 2)
    assert all(c.get_zone() == ZoneType.BATTLEFIELD for c in (first, second))


@pytest.mark.parametrize("bad", ["too_little", "too_much", "negative", "fraction", "boolean", "wrong_target", "trample"])
def test_illegal_damage_assignment_can_be_corrected_without_partial_damage(game, bad):
    state = game[0]
    attacker = creature(game, power=4, toughness=10, keywords=("trample",))
    blocker = creature(game, 1, 1, 3)
    declare(game, [attacker], {blocker: attacker})
    proposed = {
        "too_little": {blocker: 2}, "too_much": {blocker: 5},
        "negative": {blocker: -1, state.players[1]: 5}, "fraction": {blocker: 4.0},
        "boolean": {blocker: True, state.players[1]: 3},
        "wrong_target": {state.players[0]: 4}, "trample": {blocker: 2, state.players[1]: 2},
    }[bad]
    with pytest.raises(CombatError):
        damage(game, assignments={attacker: proposed})
    assert blocker.state.damage_marked == attacker.state.damage_marked == 0
    assert state.players[1].health == 20
    damage(game)
    assert state.players[1].health == 19


@pytest.mark.parametrize("keyword", ["first strike", "double strike"])
def test_first_strike_kills_blocker_before_it_can_deal_damage(game, keyword):
    attacker = creature(game, power=2, toughness=2, keywords=(keyword,))
    blocker = creature(game, 1, 2, 2)
    declare(game, [attacker], {blocker: attacker})
    damage(game, first=True)
    assert blocker.get_zone() == ZoneType.GRAVEYARD
    assert attacker.get_zone() == ZoneType.BATTLEFIELD
    damage(game)
    assert attacker.state.damage_marked == 0
    assert game[0].players[1].health == 20


@pytest.mark.parametrize("keyword,expected", [("first strike", 18), ("double strike", 16)])
def test_unblocked_first_and_double_strike_damage_steps(game, keyword, expected):
    attacker = creature(game, keywords=(keyword,))
    declare(game, [attacker])
    damage(game, first=True)
    damage(game)
    assert game[0].players[1].health == expected


def test_double_strike_trample_recomputes_remaining_blockers_and_power(game):
    attacker = creature(game, power=3, toughness=10, keywords=("double strike", "trample"))
    blocker = creature(game, 1, 1, 2)
    declare(game, [attacker], {blocker: attacker})
    damage(game, first=True)
    assert game[0].players[1].health == 19
    damage(game)
    assert game[0].players[1].health == 16
    assert attacker.state.damage_marked == 0


def test_first_strike_on_blocker_is_respected(game):
    attacker = creature(game)
    blocker = creature(game, 1, keywords=("first strike",))
    declare(game, [attacker], {blocker: attacker})
    damage(game, first=True)
    damage(game)
    assert attacker.get_zone() == ZoneType.GRAVEYARD
    assert blocker.get_zone() == ZoneType.BATTLEFIELD
    assert blocker.state.damage_marked == 0


def test_strike_damage_cannot_be_repeated_or_regular_damage_used_first(game):
    attacker = creature(game, keywords=("first strike",))
    declare(game, [attacker])
    with pytest.raises(CombatError, match="first strike"):
        damage(game)
    damage(game, first=True)
    with pytest.raises(CombatError, match="already"):
        damage(game, first=True)
    damage(game)
    with pytest.raises(CombatError, match="already"):
        damage(game)


@pytest.mark.parametrize("initial,gain,expected", [
    ("first strike", "", 18), ("first strike", "double strike", 16),
    ("double strike", "", 18), ("", "first strike", 18),
])
def test_keyword_changes_between_damage_steps_follow_510_4(game, initial, gain, expected):
    attacker = creature(game, keywords=(initial,) if initial else ())
    # Ensure there is an early step even when our attacker initially has neither.
    other = creature(game, power=0, keywords=("first strike",))
    declare(game, [attacker, other])
    damage(game, first=True)
    from game.stat_type import STAT_KEYWORDS
    attacker.set_base_stat(STAT_KEYWORDS, frozenset({gain}) if gain else frozenset())
    damage(game)
    assert game[0].players[1].health == expected


@pytest.mark.parametrize("target_toughness", [1, 3, 10])
def test_lifelink_gains_damage_dealt_even_when_exceeding_creature_toughness(game, target_toughness):
    attacker = creature(game, power=5, toughness=10, keywords=("lifelink",))
    blocker = creature(game, 1, 1, target_toughness)
    declare(game, [attacker], {blocker: attacker})
    damage(game)
    assert game[0].players[0].health == 25


def test_lifelink_from_fatally_damaged_creature_prevents_player_loss(game):
    state = game[0]
    state.players[1].health = 1
    attacker = creature(game, power=5)
    unblocked = creature(game, power=2)
    blocker = creature(game, 1, 3, 1, ("lifelink",))
    declare(game, [attacker, unblocked], {blocker: attacker})
    damage(game)
    assert state.players[1].health == 2
    assert blocker.get_zone() == ZoneType.GRAVEYARD


def test_prevention_replacement_applies_before_lifelink(game):
    from game.operations.card_operations import DamagePlayerOperation

    class PreventDamage:
        def replace(self, state, operation):
            if isinstance(operation, DamagePlayerOperation):
                return []
            return None

    state = game[0]
    state.replacement_rules.append(PreventDamage())
    attacker = creature(game, power=5, keywords=("lifelink",))
    declare(game, [attacker])
    damage(game)
    assert [p.health for p in state.players] == [20, 20]
    assert not [event for event in game[2].emitted_events if event.key == "damage_dealt"]


def test_damage_assignment_controller_hook_is_used(game):
    calls = []
    state = game[0]
    attacker = creature(game, power=4, toughness=10)
    first, second = creature(game, 1, 1, 5), creature(game, 1, 1, 5)

    def choose(state, player, creature, recipients, amount):
        calls.append((creature, recipients, amount))
        return {second: 4}

    state.players[0].controller.decide_combat_damage = lambda request: DecisionResult(CombatDamageOption(
        choose(request.state, request.player, request.creature, request.recipients, request.amount)
    ))
    declare(game, [attacker], {first: attacker, second: attacker})
    damage(game)
    assert calls == [(attacker, (first, second), 4)]
    assert (first.state.damage_marked, second.state.damage_marked) == (0, 4)


def test_declaration_events_identify_attackers_defenders_and_blockers(game):
    state = game[0]
    attacker, blocker = creature(game), creature(game, 1)
    attacks = state.combat.declare_attackers(state.players[0], [attacker])
    assert [e.key for e in attacks] == ["card_tapped", "attacker_declared", "attackers_declared"]
    assert attacks[1].payload["defender"] is state.players[1]
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    blocks = state.combat.declare_blockers(state.players[1], {blocker: attacker})
    assert [e.key for e in blocks] == ["blocker_declared", "attacker_blocked", "blockers_declared"]
    assert blocks[0].payload["attacker"] is attacker


def test_combat_end_clears_participants_and_new_combat_can_start(game):
    state = game[0]
    attacker = creature(game, keywords=("vigilance",))
    declare(game, [attacker])
    damage(game)
    state.combat.end()
    assert not state.combat.active
    assert not state.combat.attackers
    with pytest.raises(CombatError, match="no active"):
        damage(game)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    declare(game, [attacker])
    damage(game)
    assert state.players[1].health == 16


def test_attack_and_block_commands_resolve_ids_and_validate_without_mutation(game):
    state = game[0]
    attacker, blocker = creature(game), creature(game, 1)
    attackers = parse_attackers(f"attack {attacker.command_id} bob", state, state.players[0])
    assert attackers == {attacker: state.players[1]}
    assert not attacker.is_tapped
    assert not state.combat.attackers
    state.combat.declare_attackers(state.players[0], attackers)
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    blockers = parse_blockers(f"block {blocker.command_id}:{attacker.command_id}", state, state.players[1])
    assert blockers == {blocker: attacker}
    assert not state.combat.blockers


@pytest.mark.parametrize("command", ["attack", "block c1:c2", "attack missing", "attack c1:c2:c3", "pass x"])
def test_bad_attack_commands_report_command_errors(game, command):
    with pytest.raises(CommandError):
        parse_attackers(command, game[0], game[0].players[0])


def test_multiplayer_attackers_choose_defenders_and_cannot_cross_block():
    players = [Player([], PassiveController(), name=f"P{i}") for i in range(3)]
    state = State(players)
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    for p in players:
        p.last_turn_started = 1
    first, second = creature((state,), 0), creature((state,), 0)
    blocker = creature((state,), 2)
    with pytest.raises(CombatError, match="defending player"):
        state.combat.declare_attackers(players[0], [first])
    state.combat.declare_attackers(players[0], {first: players[1], second: players[2]})
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    with pytest.raises(CombatError, match="attacking its controller"):
        state.combat.declare_blockers(players[2], {blocker: first})
    state.combat.declare_blockers(players[1], {})
    state.combat.declare_blockers(players[2], {blocker: second})
    assert state.combat.blockers == {blocker: second}


@pytest.mark.parametrize("blocker_count", [0, 1, 2])
def test_console_displays_declared_block_pairs_once(game, blocker_count):
    from game.console.demo_game import ConsoleDecisionMaker, card_reference

    state, _, bus = game
    attacker = creature(game)
    blockers = [creature(game, player=1) for _ in range(blocker_count)]
    for event in state.combat.declare_attackers(state.players[0], {attacker: state.players[1]}):
        bus.emit(event)
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    for event in state.combat.declare_blockers(
        state.players[1], {blocker: attacker for blocker in blockers}
    ):
        bus.emit(event)
    output = []
    console = ConsoleDecisionMaker(bus, write=output.append, auto_pass=True)
    console.show_events()
    assert any("attacks Bob." in line for line in output)
    pairs = [line for line in output if " blocks " in line]
    assert len(pairs) == blocker_count
    for blocker in blockers:
        assert (
            f"Bob: {card_reference(blocker)} ({blocker.name}) blocks "
            f"{card_reference(attacker)} ({attacker.name})."
        ) in pairs
    if not blockers:
        assert "Bob declares no blockers." in output
    previous = list(output)
    console.show_events()
    assert output == previous


def test_priority_prompt_shows_current_combat_before_reading_spell_command(game):
    from game.console.demo_game import ConsoleDecisionMaker, card_reference

    state = game[0]
    attacker = creature(game)
    blockers = [creature(game, player=1) for _ in range(2)]
    declare(game, {attacker: state.players[1]}, {b: attacker for b in blockers})
    output = []

    def read(prompt):
        text = "\n".join(output)
        assert "Combat:" in text
        assert f"{card_reference(attacker)} (Creature, 2/2, damage 0) attacks Bob" in text
        for blocker in blockers:
            assert f"Blocked by: {card_reference(blocker)}" in text
        return "pass"

    ConsoleDecisionMaker(read=read, write=output.append).decide(PriorityDecisionRequest(state, state.players[0])).value


def test_combat_view_distinguishes_pending_unblocked_and_removed_blockers(game):
    from game.console.demo_game import format_state

    state = game[0]
    attacker = creature(game)
    blocker = creature(game, player=1)
    state.combat.declare_attackers(state.players[0], {attacker: state.players[1]})
    assert "Blockers not declared yet." in format_state(state)
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    state.combat.declare_blockers(state.players[1], {blocker: attacker})
    state.combat.blockers.clear()
    assert "Blocked; no blockers remain in combat." in format_state(state)
    state.combat.blocked.clear()
    assert "Unblocked." in format_state(state)
    state.combat.end()
    assert "Combat:" not in format_state(state)


def test_console_announces_spells_and_life_events(game):
    from game.console.demo_game import ConsoleDecisionMaker, card_reference
    from game.game_actions.resolution.event_bus import GameEvent

    state, _, bus = game
    card = creature(game)
    bus.emit(GameEvent("spell_cast", card, state.players[0]))
    bus.emit(GameEvent("life_gained", card, state.players[0],
                       {"player": state.players[0], "amount": 3}))
    output = []
    ConsoleDecisionMaker(bus, write=output.append).show_events()
    assert any("Spell cast" in line and card_reference(card) in line for line in output)
    assert any("Life gained" in line and "amount: 3" in line for line in output)
