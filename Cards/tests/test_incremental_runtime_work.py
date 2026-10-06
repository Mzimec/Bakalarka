"""Partial notifications and SBA candidate queries preserve game semantics."""
from types import SimpleNamespace

import pytest
from immutabledict import immutabledict

from game.enums import CardType, CounterType, ZoneType
from game.game_loop.minimal_game import ScriptedController
from game.game_state import State, Player, Card, CardDefinition
from game.game_state.registers.card_register import (
    IK_TAPPED, IK_HAS_DAMAGE, IK_POWER, IK_CMC, IK_NAME, IK_ATTACHED, IK_HAS_COUNTERS,
)
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.game_actions.resolution.sba_resolver import LethalCreaturesRule
from game.rules.permanents import PermanentStateRule
from game.stat_type import STAT_POWER, STAT_MANA_COST
from game.mana.mana_value import ManaValue
from helper.query_system.query import EqQuery


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    state.card_register.index_values(card)
    return state, card


def test_runtime_updates_merge_without_reading_other_characteristics(board, monkeypatch):
    state, card = board
    register = state.card_register
    before = register._snapshots[card.key]
    monkeypatch.setattr(register, "index_values", lambda *_: pytest.fail("Full card index rebuilt"))
    monkeypatch.setattr(card, "get_mana_value", lambda *_: pytest.fail("Mana read for a runtime flag"))
    card.is_tapped = True
    card.state.damage_by_deathtouch = True
    assert state.query_cards(EqQuery(IK_TAPPED, True) & EqQuery(IK_HAS_DAMAGE, True)) == (card,)
    assert register._snapshots[card.key][IK_NAME] is before[IK_NAME]


@pytest.mark.parametrize("full_first", [False, True])
def test_full_notification_dominates_partial_in_either_order(board, full_first):
    state, card = board
    changes = (lambda: card.set_base_stat(STAT_POWER, 8), lambda: setattr(card, "is_tapped", True))
    for change in changes if full_first else reversed(changes):
        change()
    assert state.query_cards(EqQuery(IK_POWER, 8) & EqQuery(IK_TAPPED, True)) == (card,)


def test_layer_updates_preserve_unrelated_index_values(board):
    state, card = board
    register = state.card_register
    name = register._snapshots[card.key][IK_NAME]
    # A custom mutable mana stat must be read live at a layer boundary.
    cost = SimpleNamespace(base_value=ManaValue("{1}"))
    card._stats = immutabledict({**card.stats, STAT_MANA_COST: cost})
    register.mark_layer_changed(card)
    assert state.query_cards(EqQuery(IK_CMC, 1)) == (card,)
    assert register._snapshots[card.key][IK_NAME] is name
    cost.base_value = ManaValue("{3}")
    card.is_tapped = True
    assert state.query_cards(EqQuery(IK_CMC, 3)) == (card,)


def test_partial_updates_rollback_and_unregister_without_old_dirty_fields(board):
    state, card = board
    checkpoint = RuntimeCheckpoint(state)
    card.is_tapped = True
    state.synchronise_registers()
    checkpoint.rollback()
    assert state.query_cards(EqQuery(IK_TAPPED, True)) == ()
    card.state.damage_marked = 1
    state.card_register.unregister(card)
    assert card.key not in state.card_register._dirty_indexes
    state.card_register.register(card)
    assert state.query_cards(EqQuery(IK_HAS_DAMAGE, True)) == (card,)


def test_permanent_candidate_flags_follow_mutations(board):
    state, card = board
    other = Card(CardDefinition("Attachment"), card.owner)
    card.owner.add_card(other, ZoneType.BATTLEFIELD)
    other.attached_to = card
    assert state.query_cards(EqQuery(IK_ATTACHED, True)) == (other,)
    other.attached_to = None
    assert not state.query_cards(EqQuery(IK_ATTACHED, True))
    card.state.counters[CounterType.PLUS_ONE] = 1
    assert state.query_cards(EqQuery(IK_HAS_COUNTERS, True)) == (card,)
    card.state.counters.clear()
    assert not state.query_cards(EqQuery(IK_HAS_COUNTERS, True))


def test_sba_does_not_evaluate_healthy_ordinary_permanents(board, monkeypatch):
    state, card = board
    def unexpected(*_):
        pytest.fail("Unrelated permanent was evaluated by SBA")
    for name in ("get_toughness", "get_controller", "get_types", "get_subtypes"):
        monkeypatch.setattr(card, name, unexpected)
    assert LethalCreaturesRule().collect(state) == []
    assert PermanentStateRule().collect(state) == []


def test_sba_keeps_state_dependent_cards_even_when_last_index_was_healthy(board):
    state, ordinary = board
    class LifeDependentCreature(Card):
        def get_toughness(self, state):
            return self.owner.health - 1
    card = LifeDependentCreature(ordinary.definition, ordinary.owner)
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    # Changing a player's life does not notify this card's registry entry.
    card.owner.health = 1
    violations = LethalCreaturesRule().collect(state)
    assert len(violations) == 1 and violations[0].opertaions[0].card is card


def test_runtime_notifications_preserve_custom_card_hooks(board):
    state, ordinary = board
    class ObservedCard(Card):
        observed = False
        def _notify_changed(self):
            self.observed = True
            super()._notify_changed()
    card = ObservedCard(ordinary.definition, ordinary.owner)
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    card.observed = False
    card.is_tapped = True
    assert card.observed
    assert state.query_cards(EqQuery(IK_TAPPED, True)) == (card,)


@pytest.mark.parametrize("toughness,damage,deathtouch,indestructible,dies", [
    (0, 0, False, True, True), (3, 2, False, False, False),
    (3, 3, False, False, True), (3, 3, False, True, False),
    (3, 1, True, False, True), (3, 1, True, True, False),
])
def test_sba_candidate_filter_preserves_lethal_rules(board, toughness, damage, deathtouch, indestructible, dies):
    state, card = board
    from game.stat_type import STAT_TOUGHNESS, STAT_KEYWORDS
    card.set_base_stat(STAT_TOUGHNESS, toughness)
    card.set_base_stat(STAT_KEYWORDS, frozenset({"indestructible"}) if indestructible else frozenset())
    card.state.damage_marked = damage
    card.state.damage_by_deathtouch = deathtouch
    assert bool(LethalCreaturesRule().collect(state)) is dies
