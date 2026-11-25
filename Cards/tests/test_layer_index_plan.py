"""Layer write sets preserve live queries while skipping unrelated readers."""
import pytest

from game.enums import CardType, CardSubtype, ZoneType, ActivatableAbilityType
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.game_state.modifier import (
    ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
    PermanentDuration, DynamicTargetingStrategy, AddIntModifier, SetModifier,
)
from game.game_state.layers import SwitchPowerToughness
from game.game_state.characteristic_scope import characteristic_scope, pipeline_cache
from game.game_state.registers.card_register import (
    IK_KEY, IK_POWER, IK_TOUGHNESS, IK_ABILITY_KIND, IK_CONTROLLER, IK_TYPE,
)
from game.stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_TYPES, STAT_SUBTYPES, STAT_ATTACH_MODS
from game.target.target_spec import QueryTargetSpec
from helper.query_system.query import EqQuery


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    card = Card(CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=3), state.players[0])
    card.owner.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    return state, card


def add_effect(state, card, key, modifiers):
    definition = ContinuousEffectDefinition(
        PermanentDuration(), card, state.time_stamp,
        DynamicTargetingStrategy(QueryTargetSpec(EqQuery(IK_KEY, card.key))), modifiers,
    )
    effect = ContinuousEffect(key, definition, ContinuousEffectState(set()))
    state.add_continuous_effect(effect)
    return effect


def test_power_projection_does_not_read_abilities_or_controller(board, monkeypatch):
    state, card = board
    add_effect(state, card, "bonus", {STAT_POWER: (AddIntModifier(4),)})
    indexes = state.card_register.layer_index_keys(card)
    assert IK_POWER in indexes and IK_TOUGHNESS in indexes
    assert IK_ABILITY_KIND not in indexes and IK_CONTROLLER not in indexes
    monkeypatch.setattr(card, "get_activatable_ability_defs", lambda *_: pytest.fail("Unrelated ability read"))
    monkeypatch.setattr(card, "get_controller", lambda *_: pytest.fail("Unrelated controller read"))
    assert state.card_register.partial_index_values(card, indexes)[IK_POWER] == frozenset({6})


def test_ordinary_cards_receive_initial_projection_then_leave_layer_candidates(board):
    state, card = board
    state._card_snapshots.discard(card)
    indexes = state.card_register.layer_index_keys(card)
    state.card_register.mark_layer_changed(card, indexes)
    state.synchronise_registers()
    assert not state.card_register.needs_layer_refresh(card)


def test_type_and_subtype_changes_update_intrinsic_mana_membership(board):
    state, card = board
    add_effect(state, card, "land", {
        STAT_TYPES: (SetModifier(frozenset({CardType.LAND})),),
        STAT_SUBTYPES: (SetModifier(frozenset({CardSubtype.ISLAND})),),
    })
    assert card in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA))
    assert card in state.query_cards(EqQuery(IK_TYPE, CardType.LAND))
    effect = state._cont_effect_manager.get("land")
    effect.detach(state)
    state._cont_effect_manager.pop("land")
    state.synchronise_registers()
    assert card not in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA))
    assert card in state.query_cards(EqQuery(IK_TYPE, CardType.CREATURE))


def test_switch_and_bonus_indexes_match_live_values(board):
    state, card = board
    add_effect(state, card, "switch", {
        STAT_POWER: (SwitchPowerToughness(),),
        STAT_TOUGHNESS: (SwitchPowerToughness(),),
    })
    add_effect(state, card, "bonus", {STAT_POWER: (AddIntModifier(4),)})
    assert card in state.query_cards(EqQuery(IK_POWER, card.get_power(state)))
    assert card in state.query_cards(EqQuery(IK_TOUGHNESS, card.get_toughness(state)))


def test_dynamic_modifier_retains_general_projection(board):
    state, card = board
    class DynamicBonus(AddIntModifier):
        def modify(self, value):
            return value + card.owner.health
    add_effect(state, card, "dynamic", {STAT_POWER: (DynamicBonus(0),)})
    assert IK_ABILITY_KIND in state.card_register.layer_index_keys(card)
    card.owner.health = 7
    state.synchronise_registers()
    assert card in state.query_cards(EqQuery(IK_POWER, 9))


def test_changing_attachment_grants_updates_host_indexes(board):
    state, card = board
    attachment = Card(CardDefinition("Equipment", attach_mods={STAT_POWER: (AddIntModifier(1),)}), card.owner)
    card.owner.add_card(attachment, ZoneType.BATTLEFIELD)
    attachment.attach(card, state)
    state.synchronise_registers()
    assert card in state.query_cards(EqQuery(IK_POWER, 3))
    add_effect(state, attachment, "new grant", {
        STAT_ATTACH_MODS: (SetModifier({STAT_TOUGHNESS: (AddIntModifier(5),)}),),
    })
    assert card in state.query_cards(EqQuery(IK_POWER, 2))
    assert card in state.query_cards(EqQuery(IK_TOUGHNESS, 8))


def test_scoped_pipeline_reuse_ends_before_next_read_and_after_mutation(board, monkeypatch):
    state, card = board
    add_effect(state, card, "bonus", {STAT_POWER: (AddIntModifier(1),)})
    calls = []
    source = card.modifier_sources[0]
    original = source.get_modifiers
    def observed(stat, state):
        calls.append(stat)
        return original(stat, state)
    monkeypatch.setattr(source, "get_modifiers", observed)
    with characteristic_scope(state, card):
        card.get_types(state)
        card.get_types(state)
        assert calls == [STAT_TYPES]
        card.is_tapped = True
        card.get_types(state)
        assert calls == [STAT_TYPES, STAT_TYPES]
    assert pipeline_cache(state, card) is None
    card.get_types(state)
    assert len(calls) == 3


def test_scope_unwinds_on_exception_and_nested_reads(board):
    state, card = board
    with characteristic_scope(state, card):
        outer = pipeline_cache(state, card)
        with pytest.raises(ValueError):
            with characteristic_scope(state, card):
                assert pipeline_cache(state, card) is not outer
                raise ValueError("reader failed")
        assert pipeline_cache(state, card) is outer
    assert pipeline_cache(state, card) is None


def test_index_projection_shares_repeated_type_pipeline(board, monkeypatch):
    state, card = board
    add_effect(state, card, "land", {STAT_TYPES: (SetModifier(frozenset({CardType.LAND})),)})
    indexes = state.card_register.layer_index_keys(card)
    calls = []
    source = card.modifier_sources[0]
    original = source.get_modifiers
    def observed(stat, state):
        calls.append(stat)
        return original(stat, state)
    monkeypatch.setattr(source, "get_modifiers", observed)
    state.card_register.partial_index_values(card, indexes)
    assert calls.count(STAT_TYPES) == 1
    assert pipeline_cache(state, card) is None


def test_definition_validation_is_not_reused_across_refreshes(board):
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
    state, card = board
    class CustomAbility(ActivatedAbilityDefinition):
        is_mana_ability = False
    definition = CustomAbility()
    source = Card(CardDefinition("Ability", abilities=frozenset({definition})), card.owner)
    card.owner.add_card(source, ZoneType.BATTLEFIELD)
    add_effect(state, card, "bonus", {STAT_POWER: (AddIntModifier(1),)})
    assert source not in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA))
    CustomAbility.is_mana_ability = True
    state._effects_dirty = True
    assert source in state.query_cards(EqQuery(IK_ABILITY_KIND, ActivatableAbilityType.MANA))


def test_custom_attachment_reader_stays_live(board):
    state, card = board
    class DynamicAttachment(Card):
        def get_stat(self, stat, state):
            if stat == STAT_ATTACH_MODS:
                return {STAT_POWER: (AddIntModifier(self.owner.health),)}
            return super().get_stat(stat, state)
    attachment = DynamicAttachment(CardDefinition("Dynamic equipment"), card.owner)
    card.owner.add_card(attachment, ZoneType.BATTLEFIELD)
    attachment.attach(card, state)
    assert card.get_power(state) == 22
    card.owner.health = 3
    assert card.get_power(state) == 5
    assert IK_ABILITY_KIND in state.card_register.layer_index_keys(card)
