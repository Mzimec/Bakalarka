"""Cross-system regressions: complete turns, triggers, targeting and SBA boundaries."""
from game.ai.decision_maker import DecisionResult, DeclareAttackersOption, DeclareBlockersOption
from game.game_actions import PassPriorityAction
from dataclasses import replace
import pytest
from game.enums import CardType, TurnPhase as P, ZoneType as Z, ManaType
from game.game_state import Card, CardDefinition, Player, State
from game.ai.decision_maker import ModularDecisionMaker as DecisionMaker
from game.console.demo_game import create_demo_game, build_action
from game.cards.demo_cards import BEAST, WATCHER
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.data_structs.ability import TriggerAbilityDefinition
from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
from game.game_actions.triggers.trigger_condition import SpellCastCondition, StepCondition, DiesCondition
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_actions.data_structs.game_action import ResolutionContext, ScheduledResolution, FixedExecutionPlan
from game.operations.card_operations import DamagePlayerOperation, DrawCardOperation


class RecordingController(DecisionMaker):
    def __init__(self):
        self.calls = []
        self.attacks = {}
        self.blocks = {}

    def decide_priority(self, request):
        state = request.state; player = request.player
        self.calls.append((state.turn.number, state.turn.phase, player, len(state.stack.items)))
        return DecisionResult(PassPriorityAction(player))

    def decide_attackers(self, request):
        state = request.state; player = request.player
        return DecisionResult(DeclareAttackersOption(self.attacks))

    def decide_blockers(self, request):
        state = request.state; player = request.player
        return DecisionResult(DeclareBlockersOption(self.blocks))


def game(expanded=False):
    a, b = RecordingController(), RecordingController()
    result = create_demo_game((a, b), expanded=expanded, full_rules=True)
    return result, a, b


def permanent(g, definition=BEAST, player=None, key=None):
    player = player or g.state.active_player
    card = Card(definition, player, key=key)
    player.add_card(card, Z.BATTLEFIELD)
    card.controlled_since = 0
    return card


def test_first_player_skips_entire_draw_step_and_untap_has_no_priority():
    g, a, b = game()
    hand = len(g.state.active_player.hand)
    g.loop.step(g.state)
    assert g.state.turn.phase == P.UPKEEP and not a.calls and not b.calls
    g.loop.step(g.state)
    assert g.state.turn.phase == P.PRECOMBAT_MAIN
    assert len(g.state.active_player.hand) == hand
    assert {phase for _, phase, _, _ in a.calls + b.calls} == {P.UPKEEP}


def test_complete_turn_resets_lands_untaps_and_next_player_draws():
    g, a, b = game()
    alice, bob = g.state.players
    land_counter = alice.lands_played_this_turn = 1
    bob_hand = len(bob.hand)
    creature = permanent(g, player=bob)
    creature.is_tapped = True
    for _ in range(20):
        if g.state.turn.number == 2 and g.state.turn.phase == P.PRECOMBAT_MAIN:
            break
        g.loop.step(g.state)
    assert g.state.active_player is bob and g.state.turn.number == 2
    assert not creature.is_tapped and len(bob.hand) == bob_hand + 1
    assert alice.lands_played_this_turn == 0
    assert not any(phase in {P.UNTAP, P.CLEANUP, P.AFTER_ATTACKERS, P.AFTER_BLOCKERS} for _, phase, _, _ in a.calls + b.calls)


@pytest.mark.parametrize("first_strike", [False, True])
def test_combat_loop_declarations_damage_and_priority_windows(first_strike):
    g, a, b = game()
    definition = replace(BEAST, keywords=frozenset({"first strike"}) if first_strike else frozenset())
    attacker = permanent(g, definition)
    blocker = permanent(g, player=g.state.players[1])
    a.attacks = {attacker: g.state.players[1]}
    b.blocks = {blocker: attacker}
    g.state.turn.phase = P.BEGIN_COMBAT
    while g.state.turn.phase != P.POSTCOMBAT_MAIN:
        g.loop.step(g.state)
    assert blocker.get_zone() == Z.GRAVEYARD
    assert attacker.get_zone() == (Z.BATTLEFIELD if first_strike else Z.GRAVEYARD)
    phases = {phase for _, phase, _, _ in a.calls}
    assert (P.FIRST_COMBAT_DAMAGE in phases) is first_strike
    assert {P.BEGIN_COMBAT, P.DECLARE_ATTACKERS, P.DECLARE_BLOCKERS, P.SECOND_COMBAT_DAMAGE, P.END_COMBAT} <= phases
    assert not g.state.combat.active


def test_no_attackers_skip_blockers_and_damage_steps():
    g, a, b = game()
    g.state.turn.phase = P.BEGIN_COMBAT
    g.loop.step(g.state)
    g.loop.step(g.state)
    assert g.state.turn.phase == P.END_COMBAT
    assert not any(phase == P.DECLARE_BLOCKERS for _, phase, _, _ in a.calls + b.calls)


def test_sickness_is_continuous_control_since_own_turn_not_global_turn_number():
    g, _, _ = game()
    card = permanent(g)
    alice, bob = g.state.players
    card.controlled_since = 1
    g.state.turn.number = 2
    g.state.switch_active_player()
    g.state.begin_turn()
    assert card.is_summoning_sick(g.state)
    g.state.turn.number = 3
    g.state.switch_active_player()
    g.state.begin_turn()
    assert not card.is_summoning_sick(g.state)
    card.set_controller(bob)
    card.set_controller(alice)
    assert card.is_summoning_sick(g.state)


@pytest.mark.parametrize("phase", [P.UPKEEP, P.PRECOMBAT_MAIN, P.POSTCOMBAT_MAIN, P.END_STEP])
def test_mana_pools_empty_at_every_step_boundary(phase):
    g, _, _ = game()
    g.state.turn.phase = phase
    for player in g.state.players:
        player.mana_pool.add({ManaType.RED: 3})
    g.loop.step(g.state)
    assert all(not player.mana_pool for player in g.state.players)


def test_cleanup_discards_to_limit_and_removes_damage_before_expired_buff_sba():
    g, _, _ = game(expanded=True)
    g.state.turn.phase = P.PRECOMBAT_MAIN
    card = permanent(g, key="cleanup-creature")
    action = build_action(g.state, g.state.active_player, "play growth1 cleanup-creature")
    g.loop.processor.process(g.state, action)
    g.loop.processor.executor.resolve(g.state, g.state.stack.pop())
    assert card.get_toughness(g.state) == 4
    card.state.damage_marked = 3
    g.state.turn.phase = P.CLEANUP
    g.loop.step(g.state)
    assert card.get_zone() == Z.BATTLEFIELD and card.state.damage_marked == 0
    assert card.get_toughness(g.state) == 2
    assert len(g.state.players[0].hand) == 7


def test_spell_cast_trigger_is_stacked_above_spell_before_priority():
    g, _, _ = game()
    trigger = TriggerAbilityDefinition(key="cast-watcher", condition=SpellCastCondition())
    permanent(g, CardDefinition("Watcher", types=frozenset({CardType.ARTIFACT}), triggers=frozenset({trigger})))
    g.state.turn.phase = P.PRECOMBAT_MAIN
    action = build_action(g.state, g.state.active_player, "play bolt1 bob")
    assert g.loop.processor.process(g.state, action)[0].success
    assert [item.key for item in g.state.stack.items] == [action.action_key, "cast-watcher"]


def test_step_trigger_is_resolved_in_upkeep_and_not_in_untap():
    g, a, b = game()
    trigger = TriggerAbilityDefinition(key="untap-trigger", condition=StepCondition(P.UNTAP))
    permanent(g, CardDefinition("Watcher", types=frozenset({CardType.ARTIFACT}), triggers=frozenset({trigger})))
    g.loop.step(g.state)
    assert g.state.stack.is_empty()
    g.loop.step(g.state)
    assert a.calls[0][1] == P.UPKEEP and a.calls[0][3] == 1
    assert g.state.stack.is_empty()


@pytest.mark.parametrize("returned", [False, True])
def test_partial_illegal_targets_resolve_on_remaining_original_objects(returned):
    g, _, _ = game(expanded=True)
    g.state.turn.phase = P.PRECOMBAT_MAIN
    first = permanent(g, key="first")
    second = permanent(g, key="second")
    action = build_action(g.state, g.state.active_player, "play flame1 first,second")
    assert g.loop.processor.process(g.state, action)[0].success
    first.owner.move_card(first, Z.HAND)
    if returned:
        first.owner.move_card(first, Z.BATTLEFIELD)
    assert g.loop.processor.executor.resolve(g.state, g.state.stack.pop()).success
    assert second.get_zone() == Z.GRAVEYARD
    assert first.get_zone() == (Z.BATTLEFIELD if returned else Z.HAND)
    assert first.state.damage_marked == 0


@pytest.mark.parametrize("keyword,own,legal", [("shroud", True, False), ("shroud", False, False),
    ("hexproof", True, True), ("hexproof", False, False)])
def test_targeting_keywords_do_not_affect_nontargeting_queries(keyword, own, legal):
    g, _, _ = game(expanded=True)
    g.state.turn.phase = P.PRECOMBAT_MAIN
    card = permanent(g, replace(BEAST, keywords=frozenset({keyword})),
                     g.state.players[0 if own else 1], key="protected")
    if legal:
        build_action(g.state, g.state.active_player, "play flame1 protected")
    else:
        with pytest.raises(ValueError):
            build_action(g.state, g.state.active_player, "play flame1 protected")
    card.is_tapped = True
    action = build_action(g.state, g.state.active_player, "play recall1")
    g.loop.processor.process(g.state, action)
    g.loop.processor.executor.resolve(g.state, g.state.stack.pop())
    assert card.get_zone() == Z.HAND


def test_draw_failure_is_checked_after_whole_resolution():
    g, _, _ = game()
    p = g.state.active_player
    for card in tuple(p.deck.values()):
        p.move_card(card, Z.EXILE)
    ctx = ResolutionContext(controller=p)
    result = g.loop.processor.executor.resolve(g.state, ScheduledResolution(
        FixedExecutionPlan([DrawCardOperation(ctx)]), ctx))
    assert result.success and p.has_lost and p.health == 10
    assert p.loss_reason == "empty_library"


def test_lifelink_is_part_of_damage_before_loss_sba():
    g, _, _ = game()
    source = permanent(g, replace(BEAST, keywords=frozenset({"lifelink"})))
    p = g.state.active_player
    p.health = 2
    context = ResolutionContext(controller=p, source=source)
    g.loop.processor.executor.resolve_simultaneous(g.state, [DamagePlayerOperation(context, p, 3)])
    assert p.health == 2 and not p.has_lost
