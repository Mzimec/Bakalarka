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


@pytest.mark.parametrize("counter_amount", (0, 2))
def test_continuous_effects_and_expiration_do_not_use_stale_snapshot(board, counter_amount):
    from game.game_state.continuous_rules import StaticContinuousRule
    from game.target.target_spec import QueryTargetSpec
    from helper.query_system.query import EqQuery
    from game.game_state.registers.card_register import IK_KEY
    state, card = board
    if counter_amount:
        card.state.counters[CounterType.PLUS_ONE] = counter_amount
    old = capture(board)
    rule = StaticContinuousRule("bonus", QueryTargetSpec(EqQuery(IK_KEY, card.key)), {STAT_POWER: (AddIntModifier(4),)})
    source = Card(CardDefinition("Anthem", types=frozenset({CardType.ENCHANTMENT}), continuous_effects=(rule,)), card.owner)
    card.owner.add_card(source, ZoneType.BATTLEFIELD)
    assert capture(board).power == 6 + counter_amount
    source.owner.move_card(source, ZoneType.GRAVEYARD, state)
    assert capture(board).power == old.power == 2 + counter_amount


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


def test_constant_counters_reuse_snapshot_and_removal_invalidates(board, monkeypatch):
    state, card = board
    card.state.counters[CounterType.PLUS_ONE] = 2
    old = capture(board)
    assert (old.power, old.toughness) == (4, 5)
    with monkeypatch.context() as patch:
        patch.setattr(event_bus, "_capture_single_card", lambda *args: pytest.fail("Counter LKI rebuilt"))
        card.is_tapped = True
        assert capture(board) is old
    card.state.counters[CounterType.PLUS_ONE] = 3
    assert capture(board).power == 5
    card.state.counters.clear()
    assert capture(board).power == 2
    assert old.counters[CounterType.PLUS_ONE] == 2
    assert old.power == 4


def test_counter_registry_changes_invalidate_native_snapshot(board, monkeypatch):
    from game.game_state import modifier
    state, card = board
    card.state.counters[CounterType.PLUS_ONE] = 2
    assert capture(board).power == 4
    monkeypatch.setitem(modifier.TYPE_TO_COUNTER, CounterType.PLUS_ONE, modifier.MinusCounter())
    assert capture(board).power == 0
    monkeypatch.setitem(modifier.STAT_TO_COUNTERS, STAT_POWER, set())
    assert capture(board).power == 2


def test_custom_counter_behavior_stays_live(board, monkeypatch):
    from game.game_state import modifier
    class CustomCounter:
        bonus = 3
        def get_modifiers(self, amount):
            return {STAT_POWER: [AddIntModifier(self.bonus)]}
    counter = CustomCounter()
    monkeypatch.setitem(modifier.TYPE_TO_COUNTER, CounterType.PLUS_ONE, counter)
    board[1].state.counters[CounterType.PLUS_ONE] = 1
    assert capture(board).power == 5
    counter.bonus = 6
    assert capture(board).power == 8


def test_constant_attachment_with_counters_reuses_and_tracks_in_place_grants(board, monkeypatch):
    from game.game_state.modifier import AddSetModifier
    state, card = board
    # Definition containers can retain mutable modifier lists.
    bonuses = [AddIntModifier(4)]
    aura = Card(CardDefinition("Aura", types=frozenset({CardType.ENCHANTMENT}),
                               attach_mods={STAT_POWER: bonuses,
                                            STAT_KEYWORDS: (AddSetModifier(frozenset({"flying"})),)}), card.owner)
    card.owner.add_card(aura, ZoneType.BATTLEFIELD)
    aura.attach(card, state)
    card.state.counters[CounterType.PLUS_ONE] = 1
    old = capture(board)
    assert old.power == 7 and "flying" in old.keywords
    with monkeypatch.context() as patch:
        patch.setattr(event_bus, "_capture_single_card", lambda *args: pytest.fail("Attachment LKI rebuilt"))
        assert capture(board) is old
    bonuses[0] = AddIntModifier(8)
    assert capture(board).power == 11
    aura.detach()
    assert capture(board).power == 3 and "flying" not in capture(board).keywords
    card.state.counters.clear()
    assert capture(board).power == 2
    assert old.power == 7 and "flying" in old.keywords


def test_dynamic_attachment_modifiers_stay_live(board):
    from game.game_state.modifier import Modifier
    from game.enums import ModifierType, Layer
    class DynamicBonus(Modifier):
        behavior = ModifierType.ADD
        layer = Layer.ADD
        bonus = 4
        def modify(self, original):
            return original + self.bonus
    state, card = board
    bonus = DynamicBonus()
    aura = Card(CardDefinition("Aura", types=frozenset({CardType.ENCHANTMENT}),
                               attach_mods={STAT_POWER: (bonus,)}), card.owner)
    card.owner.add_card(aura, ZoneType.BATTLEFIELD)
    aura.attach(card, state)
    old = capture(board)
    bonus.bonus = 8
    assert old.power == 6 and capture(board).power == 10


def test_counter_snapshot_does_not_escape_partial_layer_evaluation(board):
    from game.enums import Layer
    state, card = board
    card.state.counters[CounterType.PLUS_ONE] = 2
    full = capture(board)
    state._refreshing_effects = True
    state._layer_ceiling = Layer.SET
    try:
        partial = capture(board)
        assert partial.power == 2 and partial is not full
    finally:
        state._layer_ceiling = None
        state._refreshing_effects = False
    assert capture(board) is full and full.power == 4


def test_counter_rollback_and_departed_incarnation_keep_old_information(board):
    state, card = board
    card.state.counters[CounterType.PLUS_ONE] = 2
    original = capture(board)
    checkpoint = RuntimeCheckpoint(state)
    card.state.counters[CounterType.PLUS_ONE] = 5
    assert capture(board).power == 7
    checkpoint.rollback()
    assert capture(board).power == original.power == 4
    revision = card.zone_revision
    card.owner.move_card(card, ZoneType.GRAVEYARD, state)
    assert card._incarnation_history[revision].power == 4
    assert capture(board).power == 2
    card.owner.move_card(card, ZoneType.BATTLEFIELD, state)
    card.state.counters[CounterType.PLUS_ONE] = 2
    assert capture(board).zone_revision != original.zone_revision
