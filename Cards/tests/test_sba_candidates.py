"""Empty candidate proofs must survive unrelated edits, never hide live SBAs."""
import pytest

from game.enums import CardType, ZoneType, CounterType
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_actions.resolution.sba_resolver import SBAResolver, LethalCreaturesRule, PlayerLossRule
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.rules.permanents import PermanentStateRule
from game.stat_type import STAT_TOUGHNESS


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    return state, card


def resolver(*rules):
    return SBAResolver(OperationExecutor(), list(rules))


def test_empty_query_reused_after_tapping_without_evaluation(board, monkeypatch):
    from helper.query_system.query import AndQuery
    state, card = board
    selection = LethalCreaturesRule().candidate_filter
    assert selection.empty(state)
    original = AndQuery.eval
    def evaluate(query, ctx):
        if query is selection.query:
            pytest.fail("Unchanged candidate query evaluated again")
        return original(query, ctx)
    monkeypatch.setattr(AndQuery, "eval", evaluate)
    card.is_tapped = True
    assert selection.empty(state)


def test_damage_changes_from_nonlethal_to_lethal_with_same_membership(board):
    state, card = board
    rule = LethalCreaturesRule()
    run = resolver(rule)
    run.resolve(state)
    card.state.damage_marked = 1
    run.resolve(state)
    assert card.get_zone() == ZoneType.BATTLEFIELD
    token = state.card_register.membership_token(rule.candidate_filter.indexes)
    card.state.damage_marked = 3
    state.synchronise_registers()
    assert token == state.card_register.membership_token(rule.candidate_filter.indexes)
    run.resolve(state)
    assert card.get_zone() == ZoneType.GRAVEYARD


def test_continuous_toughness_change_invalidates_empty_candidates(board):
    from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
        PermanentDuration, DynamicTargetingStrategy, AddIntModifier)
    from game.target.continuous_targets import CardIncarnationsSpec
    state, card = board
    rule = LethalCreaturesRule()
    assert rule.candidate_filter.empty(state)
    definition = ContinuousEffectDefinition(PermanentDuration(), card, state.time_stamp,
        DynamicTargetingStrategy(CardIncarnationsSpec(((card, card.zone_revision),))),
        {STAT_TOUGHNESS: [AddIntModifier(-3)]})
    state.add_continuous_effect(ContinuousEffect("shrink", definition, ContinuousEffectState(set())))
    resolver(rule).resolve(state)
    assert card.get_zone() == ZoneType.GRAVEYARD


def test_player_change_refreshes_card_dependencies_before_skipping(board):
    from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
        PermanentDuration, DynamicTargetingStrategy, AddIntModifier)
    from game.target.continuous_targets import SourceControllerLifeSpec
    state, card = board
    definition = ContinuousEffectDefinition(PermanentDuration(), card, state.time_stamp,
        DynamicTargetingStrategy(SourceControllerLifeSpec(25)),
        {STAT_TOUGHNESS: [AddIntModifier(-3)]})
    state.add_continuous_effect(ContinuousEffect("life-shrink", definition, ContinuousEffectState(set())))
    rule = LethalCreaturesRule()
    run = resolver(rule)
    run.resolve(state)
    assert card.get_zone() == ZoneType.BATTLEFIELD
    card.owner.health = 25
    run.resolve(state)
    assert card.get_zone() == ZoneType.GRAVEYARD


@pytest.mark.parametrize("attribute,value", [("health", 0), ("failed_draw", True), ("poison_counters", 10)])
def test_player_loss_detected_after_empty_check(board, attribute, value):
    state, _ = board
    rule = PlayerLossRule()
    run = resolver(rule)
    run.resolve(state)
    setattr(state.players[0], attribute, value)
    run.resolve(state)
    assert state.players[0].has_lost


def test_token_registration_and_zone_departure_invalidates_candidates(board):
    state, _ = board
    rule = PermanentStateRule()
    run = resolver(rule)
    run.resolve(state)
    token = Card(CardDefinition("Token"), state.players[0], is_token=True)
    token.owner.add_card(token, ZoneType.BATTLEFIELD)
    run.resolve(state)
    assert token._game_state is state
    token.owner.move_card(token, ZoneType.GRAVEYARD, state)
    run.resolve(state)
    assert token._game_state is None


def test_loyalty_amount_is_rechecked_while_counter_membership_stays_true(board):
    state, _ = board
    walker = Card(CardDefinition("Walker", types=frozenset({CardType.PLANESWALKER})), state.players[0])
    walker.owner.add_card(walker, ZoneType.BATTLEFIELD)
    walker.state.counters[CounterType.LOYALTY] = 1
    walker.state.counters[CounterType.PLUS_ONE] = 1
    rule = PermanentStateRule()
    run = resolver(rule)
    run.resolve(state)
    walker.state.counters[CounterType.LOYALTY] = 0
    run.resolve(state)
    assert walker.get_zone() == ZoneType.GRAVEYARD


def test_empty_proof_does_not_survive_rollback_to_nonempty_state(board):
    state, card = board
    card.state.damage_marked = 3
    state.synchronise_registers()
    checkpoint = RuntimeCheckpoint(state)
    rule = LethalCreaturesRule()
    card.state.damage_marked = 0
    assert rule.candidate_filter.empty(state)
    checkpoint.rollback()
    resolver(rule).resolve(state)
    assert card.get_zone() == ZoneType.GRAVEYARD


def test_custom_subclass_and_instance_collect_keep_dispatch(board):
    state, _ = board
    calls = []
    class Custom(LethalCreaturesRule):
        def collect(self, state):
            calls.append("subclass")
            return []
    instance = LethalCreaturesRule()
    instance.collect = lambda state: calls.append("instance") or []
    run = resolver(Custom(), instance)
    run.resolve(state)
    run.resolve(state)
    assert calls == ["subclass", "instance"] * 2


def test_filter_isolated_between_states_and_after_new_write_following_rollback(board):
    state, card = board
    selection = LethalCreaturesRule().candidate_filter
    assert selection.empty(state)
    checkpoint = RuntimeCheckpoint(state)
    card.state.damage_marked = 3
    assert not selection.empty(state)
    checkpoint.rollback()
    assert selection.empty(state)
    card.set_base_stat(STAT_TOUGHNESS, 0)
    assert not selection.empty(state)
    other = State([Player([], ScriptedController()) for _ in range(2)])
    assert selection.empty(other)
    assert not selection.empty(state)
