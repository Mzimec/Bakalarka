"""Shared discovery must not survive mutation, rollback, dynamic rules or yields."""
from dataclasses import replace
from contextlib import nullcontext
import pytest

from game.enums import ZoneType, ManaType
from game.game_loop.minimal_game import ScriptedController
from game.game_state import State, Player, Card, CardDefinition
from game.rules.lands import basic_land, mana_ability
from game.game_actions.mana_effects import AddManaEffect
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from game.mana.discovery_context import mana_discovery_scope, current_discovery_context, share_mana_discovery
from game.mana.mana_generator import ManaGenerator
from game.mana.mana_value import ManaValue


@pytest.fixture
def board():
    state = State([Player([], ScriptedController()) for _ in range(2)])
    player = state.players[0]
    lands = [Card(basic_land("Plains"), player) for _ in range(2)]
    for card in lands:
        player.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    for card in lands:
        state.card_register.index_values(card)
    return state, player, lands


def test_static_sources_are_reused_without_querying_candidates(board, monkeypatch):
    state, player, _ = board
    with mana_discovery_scope():
        first = state.get_mana_sources(player)
        monkeypatch.setattr(state, "query_cards", lambda *_: pytest.fail("Candidates queried again"))
        assert state.get_mana_sources(player) is first


@pytest.mark.parametrize("change", ["tap", "control", "zone", "abilities"])
@pytest.mark.parametrize("scoped", [True, False])
def test_changed_sources_are_rediscovered_and_revalidated(board, change, scoped):
    from game.stat_type import STAT_INTRINSIC_MANA
    state, player, lands = board
    requirement = next(ManaValue("{W}{W}").payment_options())[0]
    generator = ManaGenerator()
    with mana_discovery_scope() if scoped else nullcontext():
        assert generator.generate(requirement, state, player) is not None
        if change == "tap":
            lands[0].is_tapped = True
        elif change == "control":
            lands[0].set_controller(state.players[1])
        elif change == "zone":
            player.move_card(lands[0], ZoneType.GRAVEYARD, state)
        else:
            lands[0].set_base_stat(STAT_INTRINSIC_MANA, False)
        assert generator.generate(requirement, state, player) is None


def test_reserved_sources_and_mana_pool_remain_per_search(board):
    state, player, lands = board
    requirement = next(ManaValue("{W}").payment_options())[0]
    with mana_discovery_scope():
        for reserved in lands:
            plan = ManaGenerator().generate(requirement, state, player, reserved=frozenset({reserved}))
            assert len(plan.steps) == 1 and plan.steps[0].source is not reserved
        plan = ManaGenerator().generate(requirement, state, player, mana_pool={ManaType.WHITE: 1})
        assert all(step.source is None for step in plan.steps)


@pytest.mark.parametrize("scoped", [True, False])
def test_discovery_after_rollback_and_new_mutation_has_no_reused_version(board, scoped):
    state, player, lands = board
    with mana_discovery_scope() if scoped else nullcontext():
        original = state.get_mana_sources(player)
        checkpoint = RuntimeCheckpoint(state)
        player.move_card(lands[0], ZoneType.GRAVEYARD, state)
        assert len(state.get_mana_sources(player)) == 1
        checkpoint.rollback()
        assert len(state.get_mana_sources(player)) == len(original) == 2
        player.move_card(lands[1], ZoneType.GRAVEYARD, state)
        assert {source.source for source in state.get_mana_sources(player)} == {lands[0]}


@pytest.mark.parametrize("dynamic", [False, True])
@pytest.mark.parametrize("scoped", [True, False])
def test_live_effect_amount_is_not_reused(board, dynamic, scoped):
    state, player, _ = board
    class DynamicMana(AddManaEffect):
        def get_amount(self, state, context):
            return context.controller.maximum_hand_size
    effect = (DynamicMana if dynamic else AddManaEffect)("amount", ManaType.BLUE, 1)
    base = mana_ability(ManaType.BLUE)
    from game.game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap
    part = replace(base.action_subdefs[0], effects=frozenset({effect}),
                   action_node=EffectActionNode(ImmutableEffectToSlotMap({effect.key: frozenset()})))
    definition = replace(base, action_subdefs=(part,))
    card = Card(CardDefinition("Mana artifact", abilities=frozenset({definition})), player)
    player.add_card(card, ZoneType.BATTLEFIELD)
    state.synchronise_registers()
    state.card_register.index_values(card)
    with mana_discovery_scope() if scoped else nullcontext():
        old = next(source for source in state.get_mana_sources(player) if source.source is card)
        # Neither of these mutations emits a register notification.
        if dynamic:
            player.maximum_hand_size = 10
        else:
            effect.amount = 10
        new = next(source for source in state.get_mana_sources(player) if source.source is card)
        assert sum(new.produces.values()) == 10
        assert sum(old.produces.values()) != 10


def test_discovery_context_is_shared_when_nested_but_never_leaks_across_yield():
    contexts = []
    def generate():
        for _ in range(2):
            with mana_discovery_scope() as context:
                assert context is current_discovery_context()
                contexts.append(context)
            yield 1
    iterator = share_mana_discovery(generate())
    assert next(iterator) == 1 and current_discovery_context() is None
    assert next(iterator) == 1 and current_discovery_context() is None
    assert contexts[0] is not contexts[1]
    iterator.close()
    assert current_discovery_context() is None


def test_failed_generation_releases_context():
    def generate():
        assert current_discovery_context() is not None
        raise ValueError("generation failed")
        yield
    with pytest.raises(ValueError):
        next(share_mana_discovery(generate()))
    assert current_discovery_context() is None


def test_persistent_descriptions_survive_tapping_but_availability_is_live(board):
    state, player, lands = board
    first = state.get_mana_sources(player)
    assert state.get_mana_sources(player) is first
    lands[0].is_tapped = True
    assert state.get_mana_sources(player) is first
    requirement = next(ManaValue("{W}{W}").payment_options())[0]
    assert ManaGenerator().generate(requirement, state, player) is None
    lands[0].is_tapped = False
    assert state.get_mana_sources(player) is first
    assert ManaGenerator().generate(requirement, state, player) is not None


def test_persistent_cache_invalidates_same_membership_output_change(board):
    from game.stat_type import STAT_ABILITIES
    state, player, lands = board
    before = state.get_mana_sources(player)
    token = state._mana_source_cache.token(state)
    # Still a battlefield mana source controlled by the same player.
    new = mana_ability(ManaType.BLUE)
    lands[0].set_base_stat(STAT_ABILITIES, {new.key: new})
    after = state.get_mana_sources(player)
    assert state._mana_source_cache.token(state) == token
    assert after is not before
    assert any(ManaType.BLUE in symbol.mana
               for source in after if source.source is lands[0] for symbol in source.produces)


def test_persistent_cache_rechecks_unnotified_template_overrides(board, monkeypatch):
    state, player, _ = board
    before = state.get_mana_sources(player)
    original = AddManaEffect.get_amount
    monkeypatch.setattr(AddManaEffect, "get_amount", lambda self, state, context: original(self, state, context) + 2)
    after = state.get_mana_sources(player)
    assert after is not before
    assert all(sum(source.produces.values()) == 3 for source in after)
    assert all(sum(source.produces.values()) == 1 for source in before)


def test_empty_source_cache_not_reused_after_source_enters(board):
    state, _, _ = board
    player = state.players[1]
    assert state.get_mana_sources(player) == ()
    card = Card(basic_land("Island"), player)
    player.add_card(card, ZoneType.BATTLEFIELD)
    assert [source.source for source in state.get_mana_sources(player)] == [card]
