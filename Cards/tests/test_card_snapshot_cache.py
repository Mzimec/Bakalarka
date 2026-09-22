"""Snapshot reuse must preserve LKI, custom modifiers and rollback semantics."""
from dataclasses import replace
import pytest
from game.ai.decision_maker import ModularDecisionMaker
from game.game_state import State, Player, Card, CardDefinition
from game.enums import CardType, ZoneType, CounterType
from game.stat_type import STAT_POWER, STAT_KEYWORDS
from game.game_actions.resolution import event_bus
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_state.modifier import CounterModifierSource, TimeStampedModifier, AddIntModifier


@pytest.fixture
def board():
    state = State([Player([], ModularDecisionMaker(), idx=i) for i in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    return state, card


def capture(board):
    return event_bus.capture_single_card(*board)


def test_unchanged_cards_reuse_snapshot_without_evaluating_again(board, monkeypatch):
    first = capture(board)
    monkeypatch.setattr(event_bus, "_capture_single_card", lambda *args: pytest.fail("Unchanged LKI rebuilt"))
    assert capture(board) is first
    board[1].is_tapped = True
    assert capture(board) is first  # tapped is not part of LKI


def test_new_base_stats_do_not_rewrite_old_snapshot(board):
    first = capture(board)
    board[1].set_base_stat(STAT_POWER, 7)
    second = capture(board)
    assert first.power == 2 and second.power == 7 and first is not second


def test_zone_reentry_changes_incarnation(board):
    state, card = board
    first = capture(board)
    card.owner.move_card(card, ZoneType.GRAVEYARD, state)
    departed = capture(board)
    card.owner.move_card(card, ZoneType.BATTLEFIELD, state)
    returned = capture(board)
    assert first.zone == returned.zone == ZoneType.BATTLEFIELD
    assert departed.zone == ZoneType.GRAVEYARD
    assert returned.zone_revision != first.zone_revision


def test_controller_and_copy_definition_changes(board):
    state, card = board
    old = capture(board)
    card.set_controller(state.players[1])
    changed = capture(board)
    assert old.controller is state.players[0] and changed.controller is state.players[1]
    card._definition = replace(card.definition, name="Copy")
    assert capture(board).name == "Copy" and old.name == "Unit"


def test_counters_are_not_reused_or_retroactively_mutated(board):
    state, card = board
    old = capture(board)
    card.state.counters[CounterType.LOYALTY] = 3
    current = capture(board)
    card.state.counters[CounterType.LOYALTY] = 4
    assert current.counters[CounterType.LOYALTY] == 3
    assert capture(board).counters[CounterType.LOYALTY] == 4 and not old.counters


def test_custom_source_subclass_is_never_treated_as_empty(board):
    state, card = board
    old = capture(board)
    class DynamicSource(CounterModifierSource):
        bonus = 5
        def get_modifiers(self, stat, state):
            return [TimeStampedModifier(AddIntModifier(self.bonus), state.time_stamp)] if stat == STAT_POWER else []
    source = DynamicSource({})
    card._modifier_sources = (card.modifier_sources[0], source, card.modifier_sources[2])
    assert capture(board).power == 7
    source.bonus = 8
    assert capture(board).power == 10 and old.power == 2


def test_attachments_and_removal_use_live_characteristics(board):
    state, card = board
    old = capture(board)
    aura = Card(CardDefinition("Aura", types=frozenset({CardType.ENCHANTMENT}),
                               attach_mods={STAT_POWER: (AddIntModifier(4),)}), card.owner)
    card.owner.add_card(aura, ZoneType.BATTLEFIELD)
    aura.attach(card, state)
    assert capture(board).power == 6
    aura.detach()
    assert capture(board).power == old.power == 2


def test_continuous_effects_and_expiration_do_not_use_stale_snapshot(board):
    from game.game_state.continuous_rules import StaticContinuousRule
    from game.target.target_spec import QueryTargetSpec
    from helper.query_system.query import EqQuery
    from game.game_state.registers.card_register import IK_KEY
    state, card = board
    old = capture(board)
    rule = StaticContinuousRule("bonus", QueryTargetSpec(EqQuery(IK_KEY, card.key)), {STAT_POWER: (AddIntModifier(4),)})
    source = Card(CardDefinition("Anthem", types=frozenset({CardType.ENCHANTMENT}), continuous_effects=(rule,)), card.owner)
    card.owner.add_card(source, ZoneType.BATTLEFIELD)
    assert capture(board).power == 6
    source.owner.move_card(source, ZoneType.GRAVEYARD, state)
    assert capture(board).power == old.power == 2


def test_rollback_restores_the_snapshot_inputs(board):
    state, card = board
    old = capture(board)
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_POWER, 8)
    assert capture(board).power == 8
    checkpoint.rollback()
    assert capture(board).power == old.power == 2
    card.set_base_stat(STAT_POWER, 9)
    assert capture(board).power == 9


def test_mutable_base_inputs_fall_back(board):
    state, card = board
    from immutabledict import immutabledict
    keywords = set()
    card._stats = immutabledict({**card.stats, STAT_KEYWORDS: replace(card.stats[STAT_KEYWORDS], base_value=keywords)})
    old = capture(board)
    keywords.add("flying")
    assert capture(board).keywords == frozenset({"flying"}) and not old.keywords


def test_unregister_discards_cached_identity(board):
    state, card = board
    capture(board)
    assert id(card) in state._card_snapshots._entries
    state.card_register.unregister(card)
    assert id(card) not in state._card_snapshots._entries
