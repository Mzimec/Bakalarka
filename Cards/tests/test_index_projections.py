"""Updating runtime flags must not rebuild unrelated card characteristics."""
import pytest
from game.ai.decision_maker import ModularDecisionMaker
from game.enums import ZoneType, CardType, ActivatableAbilityType
from game.game_state import State, Player, Card, CardDefinition
from game.game_state.registers.card_register import (
    CARD_INDEX_KEYS, IK_TAPPED, IK_HAS_DAMAGE, IK_IS_TOKEN, IK_POWER,
    IK_CONTROLLER, IK_ABILITY_KIND, IK_CMC,
)
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.stat_type import STAT_POWER
from helper.query_system.query import EqQuery


@pytest.fixture
def board():
    state = State([Player([], ModularDecisionMaker(), idx=i) for i in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    state.card_register.index_values(card)
    return state, card


@pytest.mark.parametrize("flag", ["tap", "damage", "token"])
def test_runtime_flag_updates_skip_unrelated_characteristics_and_lki(board, monkeypatch, flag):
    state, card = board
    def unexpected(*args):
        pytest.fail("Runtime flag update rebuilt unrelated card characteristics")
    monkeypatch.setattr(card, "get_power", unexpected)
    monkeypatch.setattr(card, "get_toughness", unexpected)
    monkeypatch.setattr(card, "get_activatable_ability_defs", unexpected)
    monkeypatch.setattr(state._card_snapshots, "capture", unexpected)
    if flag == "tap":
        card.is_tapped = True
        key = IK_TAPPED
    elif flag == "damage":
        card.state.damage_marked = 1
        key = IK_HAS_DAMAGE
    else:
        card.is_token = True
        key = IK_IS_TOKEN
    assert state.query_cards(EqQuery(key, True)) == (card,)
    assert set(state.card_register.index_values(card)) == set(CARD_INDEX_KEYS)


def test_base_characteristics_and_controller_invalidate_projection(board):
    state, card = board
    card.set_base_stat(STAT_POWER, 8)
    assert state.query_cards(EqQuery(IK_POWER, 8)) == (card,)
    card.set_controller(state.players[1])
    assert state.query_cards(EqQuery(IK_CONTROLLER, state.players[1])) == (card,)


def test_zone_change_recomputes_ability_usability(board):
    state, _ = board
    card = Card(CardDefinition("Ability", abilities=frozenset({ActivatedAbilityDefinition()})), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    assert card in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.NON_MANA))
    card.owner.move_card(card, ZoneType.HAND, state)
    assert card not in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.NON_MANA))


def test_stack_x_remains_live_when_characteristics_are_reused(board):
    state, _ = board
    card = Card(CardDefinition("X", mana_cost="{X}{U}"), state.players[0])
    card.owner.add_card(card, ZoneType.STACK)
    for value in (3, 5, 0):
        card.state.mana_x = value
        state.notify_card_changed(card)
        assert card in state.query_cards(EqQuery(IK_CMC, value + 1))


def test_mutable_zone_permissions_are_not_cached(board):
    state, _ = board
    zones = {ZoneType.BATTLEFIELD}
    definition = ActivatedAbilityDefinition(allowed_zones=zones)
    from helper.runtime_object import ImmutableKeyedCollection
    card = Card(CardDefinition("Ability", abilities=ImmutableKeyedCollection({definition.key: definition})), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    query = EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.NON_MANA)
    assert card in state.query_cards(query)
    zones.clear()
    state.notify_card_changed(card)
    assert card not in state.query_cards(query)


def test_projection_cache_survives_rollback_and_unregister(board):
    state, card = board
    checkpoint = RuntimeCheckpoint(state)
    card.set_base_stat(STAT_POWER, 9)
    assert card in state.query_cards(EqQuery(IK_POWER, 9))
    checkpoint.rollback()
    assert card in state.query_cards(EqQuery(IK_POWER, 2))
    assert card not in state.query_cards(EqQuery(IK_POWER, 9))
    assert id(card) in state._card_snapshots._index_entries
    state.card_register.unregister(card)
    assert id(card) not in state._card_snapshots._index_entries


def test_custom_ability_zone_logic_is_evaluated_again(board):
    state, _ = board
    class DynamicAbility(ActivatedAbilityDefinition):
        enabled = True
        def is_usable_in_zone(self, zone):
            return type(self).enabled
    card = Card(CardDefinition("Dynamic", abilities=frozenset({DynamicAbility()})), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    query = EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.NON_MANA)
    assert card in state.query_cards(query)
    DynamicAbility.enabled = False
    state.notify_card_changed(card)
    assert card not in state.query_cards(query)


def test_layer_boundary_skips_clean_base_cards_but_keeps_queued_changes(board):
    state, card = board
    register = state.card_register
    register.mark_layer_changed(card)
    assert card.key not in register._dirty
    card.is_tapped = True
    register.mark_layer_changed(card)
    assert card.key in register._dirty
    assert state.query_cards(EqQuery(IK_TAPPED, True)) == (card,)


def test_effect_refresh_does_not_reindex_unaffected_library_cards(board, monkeypatch):
    from game.game_state.continuous_rules import StaticContinuousRule
    from game.game_state.modifier import AddIntModifier
    from game.target.target_spec import QueryTargetSpec
    from game.game_state.registers.card_register import IK_KEY
    state, card = board
    library = Card(CardDefinition("Library", types=frozenset({CardType.CREATURE}), power=1, toughness=1), card.owner)
    library.owner.add_card(library, ZoneType.DECK)
    rule = StaticContinuousRule("bonus", QueryTargetSpec(EqQuery(IK_KEY, card.key)), {STAT_POWER: (AddIntModifier(4),)})
    source = Card(CardDefinition("Anthem", types=frozenset({CardType.ENCHANTMENT}), continuous_effects=(rule,)), card.owner)
    source.owner.add_card(source, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    state.card_register.index_values(library)
    calls = []
    original = state.card_register.index_values
    def count(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(state.card_register, "index_values", count)
    source.is_tapped = True
    assert card in state.query_cards(EqQuery(IK_POWER, 6))
    assert library not in calls and card in calls


def test_entry_projection_has_its_own_bounded_cache(board):
    from game.operations.card_operations import MoveCardOperation
    from game.game_actions.data_structs.game_action import ResolutionContext
    state, card = board
    card.owner.move_card(card, ZoneType.HAND, state)
    state.synchronise_registers()
    state.card_register.index_values(card)
    keys = set(state._card_snapshots._index_entries)
    operation = MoveCardOperation(ResolutionContext(source=card, controller=card.owner), card, ZoneType.BATTLEFIELD)
    for _ in range(3):
        incoming, projected = operation.entry_characteristics(state)
        assert projected._card_snapshots is not state._card_snapshots
        projected.card_register.index_values(incoming)
        assert set(state._card_snapshots._index_entries) == keys
        assert all(entry.card._game_state is projected for entry in projected._card_snapshots._index_entries.values())


def test_layer_refresh_keeps_custom_dynamic_mana_values_live(board):
    from types import SimpleNamespace
    from immutabledict import immutabledict
    from game.mana.mana_value import ManaValue
    from game.stat_type import STAT_MANA_COST
    state, card = board
    cost = SimpleNamespace(base_value=ManaValue.parse("{1}"))
    card._stats = immutabledict({**card.stats, STAT_MANA_COST: cost})
    state.notify_card_changed(card)
    assert card in state.query_cards(EqQuery(IK_CMC, 1))
    cost.base_value = ManaValue.parse("{3}")
    state.card_register.mark_layer_changed(card)
    assert card in state.query_cards(EqQuery(IK_CMC, 3))


def test_layer_refresh_keeps_stack_x_live_without_a_stat_table_change(board):
    state, _ = board
    card = Card(CardDefinition("X", mana_cost="{X}{U}"), state.players[0])
    card.owner.add_card(card, ZoneType.STACK)
    assert card in state.query_cards(EqQuery(IK_CMC, 1))
    state.card_register.index_values(card)
    card.state.mana_x = 5
    state.card_register.mark_layer_changed(card)
    assert card in state.query_cards(EqQuery(IK_CMC, 6))


def test_partial_index_optimization_preserves_gameplay_log(tmp_path, monkeypatch):
    import json
    from game.ai.simple_agent import SimpleAgent
    from game.ai.modular_agent import ModularAgent
    from game.game_state.card_snapshot_cache import CardSnapshotCache
    from game.game_state.registers.card_register import CardRegister
    from game.simulation.match_runner import run_match
    events = []
    for enabled in (False, True):
        path = tmp_path / f"index-{enabled}.jsonl"
        with monkeypatch.context() as patch:
            if not enabled:
                patch.setattr(CardSnapshotCache, "index_characteristics", lambda self, state, card, reader: reader(state, card))
                patch.setattr(CardRegister, "mark_layer_changed", CardRegister.mark_changed)
            result = run_match(("white", "white"), path, seed=2, max_turns=6,
                               controllers=(ModularAgent(), SimpleAgent()))
        assert result.status == "turn_limit", result.error
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events.append([row for row in rows if row["kind"] not in {"decision_timing", "result"}])
    assert events[0] == events[1]
