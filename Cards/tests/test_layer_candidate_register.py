"""Incremental candidate selection preserves invalidation and rollback."""
from dataclasses import replace

import pytest
from immutabledict import immutabledict

from game.enums import CardType, ZoneType, ActivatableAbilityType
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_state.modifier import (
    ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
    PermanentDuration, DynamicTargetingStrategy, AddIntModifier,
)
from game.game_state.registers.card_register import IK_KEY, IK_POWER, IK_ABILITY_KIND
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.stat_type import STAT_POWER
from game.target.target_spec import QueryTargetSpec
from helper.query_system.query import EqQuery


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    unit = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    unit.owner.add_card(unit, ZoneType.BATTLEFIELD)
    definition = ContinuousEffectDefinition(
        PermanentDuration(), unit, state.time_stamp,
        DynamicTargetingStrategy(QueryTargetSpec(EqQuery(IK_KEY, unit.key))),
        {STAT_POWER: (AddIntModifier(1),)},
    )
    state.add_continuous_effect(ContinuousEffect("bonus", definition, ContinuousEffectState(set())))
    return state, unit


def warm(state):
    state.synchronise_registers()
    state._effects_dirty = True
    state.refresh_continuous_effects()


def test_refresh_does_not_scan_or_reclassify_unchanged_library(board, monkeypatch):
    state, unit = board
    library = [Card(CardDefinition("Library", power=1, toughness=1), unit.owner) for _ in range(60)]
    for card in library:
        card.owner.add_card(card, ZoneType.DECK)
    warm(state)
    checked = []
    original = state.card_register._can_preserve_characteristics
    def observe(card, *args):
        checked.append(card)
        return original(card, *args)
    monkeypatch.setattr(state.card_register, "_can_preserve_characteristics", observe)
    monkeypatch.setattr(state, "get_cards", lambda *_: pytest.fail("Full card scan"))
    state._effects_dirty = True
    state.refresh_continuous_effects()
    assert not set(checked).intersection(library)
    assert unit in state.query_cards(EqQuery(IK_POWER, 3))


def test_shared_ability_metadata_change_revisits_all_dependent_cards(board):
    state, unit = board
    class SharedAbility(ActivatedAbilityDefinition):
        is_mana_ability = False
    ability = SharedAbility()
    definition = CardDefinition("Shared", abilities=frozenset({ability}))
    cards = [Card(definition, unit.owner) for _ in range(12)]
    for card in cards:
        card.owner.add_card(card, ZoneType.BATTLEFIELD)
    warm(state)
    tracker = state.card_register.layer_candidates
    assert len(tracker._definitions[id(ability)].cards) == 12
    SharedAbility.is_mana_ability = True
    state._effects_dirty = True
    found = state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA))
    assert set(cards) <= set(found)


def test_replacing_immutable_inputs_requeues_an_invariant_card(board):
    state, unit = board
    card = Card(CardDefinition("Library", power=1, toughness=1), unit.owner)
    card.owner.add_card(card, ZoneType.DECK)
    warm(state)
    card._stats = immutabledict({**card.stats, STAT_POWER: replace(card.stats[STAT_POWER], base_value=8)})
    assert card in state.query_cards(EqQuery(IK_POWER, 8))
    assert card not in state.query_cards(EqQuery(IK_POWER, 1))


def test_custom_state_dependent_card_is_always_a_candidate(board):
    state, unit = board
    class DynamicCard(Card):
        def get_power(self, state):
            return self.owner.health
    card = DynamicCard(unit.definition, unit.owner)
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    warm(state)
    card.owner.health = 7
    assert card in state.query_cards(EqQuery(IK_POWER, 7))
    assert card.key in state.card_register.layer_candidates._dynamic


def test_attachment_notifications_add_and_remove_candidates(board):
    state, unit = board
    host = Card(unit.definition, unit.owner)
    host.owner.add_card(host, ZoneType.BATTLEFIELD)
    equipment = Card(CardDefinition("Equipment", attach_mods={STAT_POWER: (AddIntModifier(4),)}), unit.owner)
    equipment.owner.add_card(equipment, ZoneType.BATTLEFIELD)
    warm(state)
    equipment.attach(host, state)
    assert host in state.query_cards(EqQuery(IK_POWER, 6))
    equipment.detach()
    assert host in state.query_cards(EqQuery(IK_POWER, 2))
    warm(state)
    assert host.key not in state.card_register.layer_candidates._dynamic


def test_unregister_and_reregister_remove_definition_watches(board):
    state, unit = board
    ability = ActivatedAbilityDefinition()
    card = Card(CardDefinition("Watcher", abilities=frozenset({ability})), unit.owner)
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    warm(state)
    register = state.card_register
    register.unregister(card)
    assert card.key not in register.layer_candidates._card_definitions
    assert id(ability) not in register.layer_candidates._definitions
    register.register(card)
    warm(state)
    assert card.key in register.layer_candidates._card_definitions


def test_checkpoint_restores_candidate_and_watch_memberships(board):
    state, unit = board
    ability = ActivatedAbilityDefinition()
    card = Card(CardDefinition("Watcher", abilities=frozenset({ability}), power=1), unit.owner)
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    warm(state)
    register = state.card_register
    checkpoint = RuntimeCheckpoint(state)
    register.unregister(card)
    checkpoint.rollback()
    assert register.get_by_key(card.key) is card
    assert register.layer_candidates._definitions[id(ability)].cards[card.key] is card
    card.set_base_stat(STAT_POWER, 9)
    assert card in state.query_cards(EqQuery(IK_POWER, 9))
    assert card not in state.query_cards(EqQuery(IK_POWER, 1))


def test_clear_removes_candidate_dependencies(board):
    state, unit = board
    warm(state)
    register = state.card_register
    register.clear()
    assert register.layer_candidates.select() == ()
    assert not register.layer_candidates._definitions


def test_entry_projection_owns_candidate_registry(board):
    from game.operations.card_operations import MoveCardOperation
    from game.game_actions.data_structs.game_action import ResolutionContext
    state, unit = board
    entrant = Card(unit.definition, unit.owner)
    entrant.owner.add_card(entrant, ZoneType.HAND)
    warm(state)
    operation = MoveCardOperation(ResolutionContext(source=entrant, controller=entrant.owner), entrant, ZoneType.BATTLEFIELD)
    incoming, projected = operation.entry_characteristics(state)
    assert projected.card_register.layer_candidates is not state.card_register.layer_candidates
    incoming.set_base_stat(STAT_POWER, 11)
    assert incoming in projected.query_cards(EqQuery(IK_POWER, 11))
    assert entrant.get_power(state) == 2
    assert all(card._game_state is projected for card in projected.card_register.layer_candidates._dynamic.values())
