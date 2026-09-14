"""Real State -> incremental indexes -> target validation -> query spell."""
import random

import pytest

from helper.query_system.query import EqQuery, RangeQuery, DifferenceQuery
from game.console.demo_game import create_demo_game, build_action, TAPPED_CREATURES, RECALL_EFFECT
from game.console.console_commands import CommandError
from game.enums import CardType, CardSubtype, CounterType, TurnPhase, ZoneType
from game.game_state import Card, CardDefinition, Player, State
from game.game_state.registers.card_register import (
    IK_KEY, IK_ZONE, IK_TYPE, IK_SUBTYPE, IK_CONTROLLER, IK_OWNER, IK_POWER, IK_TAPPED,
)
from game.game_state.registers.player_register import IK_HEALTH, IK_ALIVE
from game.game_loop.game_loop import UntapPhaseController
from game.game_loop.minimal_game import ScriptedController
from game.stat_type import STAT_POWER, STAT_TYPES, STAT_SUBTYPES
from game.target.target_spec import QueryTargetSpec


def make_game():
    game = create_demo_game((ScriptedController(), ScriptedController()))
    game.state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return game


def creature(player, state, key, zone=ZoneType.BATTLEFIELD):
    card = Card(CardDefinition("Test creature", types=frozenset({CardType.CREATURE}), power=2, toughness=2), player, key)
    player.add_card(card, zone)  # State is inferred from the owning player.
    return card


def test_existing_and_new_cards_register_and_keep_identity_through_zone_moves():
    player = Player([], ScriptedController())
    card = Card(CardDefinition("Card"), player)
    player.add_card(card)
    state = State([player])
    identity = card.runtime_id
    assert identity is not None
    assert state.query_cards(EqQuery(IK_ZONE, ZoneType.DECK)) == (card,)
    player.draw()  # No explicit state argument needed to update the registry.
    assert state.query_cards(EqQuery(IK_ZONE, ZoneType.DECK)) == ()
    assert state.query_cards(EqQuery(IK_ZONE, ZoneType.HAND)) == (card,)
    for zone in (ZoneType.STACK, ZoneType.BATTLEFIELD, ZoneType.GRAVEYARD, ZoneType.EXILE):
        player.move_card(card, zone)
        assert state.query_cards(EqQuery(IK_ZONE, zone)) == (card,)
        assert card.runtime_id == identity
    assert state.card_register.get_by_key(card.key) is card


def test_tap_untap_and_control_changes_invalidate_only_changed_objects(monkeypatch):
    game = make_game()
    state = game.state
    alice, bob = state.players
    first = creature(alice, state, "first")
    second = creature(bob, state, "second")
    state.synchronise_registers()
    reads = []
    original = state.card_register.index_values
    def recording(card):
        reads.append(card.key)
        return original(card)
    monkeypatch.setattr(state.card_register, "index_values", recording)
    first.state.tapped = True  # The runtime-state property also notifies.
    assert state.query_cards(TAPPED_CREATURES) == (first,)
    assert reads == ["first"]
    state.query_cards(TAPPED_CREATURES)
    assert reads == ["first"]  # Repeated queries do not scan unchanged cards.
    first.set_controller(bob)
    assert state.query_cards(EqQuery(IK_CONTROLLER, bob) & EqQuery(IK_KEY, "first")) == (first,)
    assert state.query_cards(EqQuery(IK_OWNER, alice) & EqQuery(IK_KEY, "first")) == (first,)
    UntapPhaseController().execute_turn_based_actions(state)
    assert first.is_tapped  # Alice owns it, but Bob now controls it.
    state.switch_active_player()
    UntapPhaseController().execute_turn_based_actions(state)
    assert not first.is_tapped
    assert state.query_cards(TAPPED_CREATURES) == ()


def test_derived_stats_counters_types_and_subtypes_update_indexes():
    game = make_game()
    state = game.state
    card = creature(state.active_player, state, "changing")
    card.state.counters[CounterType.PLUS_ONE] = 3
    assert card in state.query_cards(RangeQuery(IK_POWER, 5, 5))
    assert card not in state.query_cards(EqQuery(IK_POWER, 2))
    card.state.counters.pop(CounterType.PLUS_ONE)
    card.set_base_stat(STAT_POWER, 7)
    card.set_base_stat(STAT_TYPES, {CardType.ARTIFACT})
    card.set_base_stat(STAT_SUBTYPES, {CardSubtype.ELF})
    assert card in state.query_cards(EqQuery(IK_POWER, 7))
    assert card not in state.query_cards(EqQuery(IK_TYPE, CardType.CREATURE))
    assert card in state.query_cards(EqQuery(IK_SUBTYPE, CardSubtype.ELF))


def test_removal_id_reuse_and_failed_duplicate_registration_preserve_indexes():
    game = make_game()
    state = game.state
    alice, bob = state.players
    old = creature(alice, state, "old")
    old.is_tapped = True
    old_id = old.runtime_id
    alice.remove_card(old)  # Removes pending updates as well as old index bits.
    new = creature(alice, state, "new")
    assert old.runtime_id is None
    assert new.runtime_id == old_id
    assert state.query_cards(EqQuery(IK_KEY, "old")) == ()
    assert state.query_cards(TAPPED_CREATURES) == ()
    duplicate = Card(new.definition, bob, key="new")
    with pytest.raises(ValueError, match="Duplicate entity key"):
        bob.add_card(duplicate)
    assert "new" not in bob.deck
    assert duplicate.runtime_id is None
    assert state.card_register.get_by_key("new") is new


def test_player_indexes_follow_damage_and_concession():
    game = make_game()
    alice, bob = game.state.players
    action = build_action(game.state, alice, "play bolt1 bob")
    game.loop.processor.process(game.state, action)
    game.loop.processor.executor.resolve(game.state, game.state.stack.pop())
    assert game.state.query_players(EqQuery(IK_HEALTH, 7)) == (bob,)
    bob.health = 0
    assert game.state.query_players(EqQuery(IK_ALIVE, False)) == (bob,)


def test_query_target_spec_binds_controller_and_validates_without_candidates():
    game = make_game()
    state = game.state
    alice, bob = state.players
    ally = creature(alice, state, "ally")
    enemy = creature(bob, state, "enemy")
    spec = QueryTargetSpec(lambda source, controller, state:
                           EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_CONTROLLER, controller)
                           & EqQuery(IK_TYPE, CardType.CREATURE))
    assert list(spec.generate_candidates(ally, alice, state)) == [ally]
    assert list(spec.generate_candidates(ally, alice, state, {ally})) == []
    assert spec.is_valid_target(ally, ally, alice, state)
    assert not spec.is_valid_target(enemy, ally, alice, state)
    ally.set_controller(bob)
    assert not spec.is_valid_target(ally, ally, alice, state)


def test_tidal_recall_uses_resolution_state_and_returns_to_owners_hands():
    game = make_game()
    state = game.state
    alice, bob = state.players
    initially_tapped = creature(alice, state, "initial")
    response_tapped = creature(bob, state, "response")
    noncreature = Card(CardDefinition("Rock", types=frozenset({CardType.ARTIFACT})), bob, "rock")
    bob.add_card(noncreature, ZoneType.BATTLEFIELD)
    outside = creature(bob, state, "outside", ZoneType.GRAVEYARD)
    initially_tapped.is_tapped = noncreature.is_tapped = outside.is_tapped = True
    recall = build_action(state, alice, "play recall1")
    game.loop.processor.process(state, recall)
    assert recall.source.get_zone() == ZoneType.STACK
    # Simulate changes made by responses after casting but before resolution.
    initially_tapped.is_tapped = False
    response_tapped.is_tapped = True
    response_tapped.set_controller(alice)
    game.loop.processor.executor.resolve(state, state.stack.pop())
    assert response_tapped.get_zone() == ZoneType.HAND
    assert response_tapped.key in bob.hand  # Owner, not its current controller.
    assert not response_tapped.is_tapped
    assert initially_tapped.get_zone() == noncreature.get_zone() == ZoneType.BATTLEFIELD
    assert outside.get_zone() == ZoneType.GRAVEYARD
    assert recall.source.get_zone() == ZoneType.GRAVEYARD
    assert state.query_cards(TAPPED_CREATURES) == ()
    assert state.query_cards(EqQuery(IK_ZONE, ZoneType.STACK)) == ()


def test_empty_recall_resolves_normally_and_target_argument_is_rejected():
    game = make_game()
    state = game.state
    with pytest.raises(CommandError, match="without a target"):
        build_action(state, state.active_player, "play recall1 bob")
    action = build_action(state, state.active_player, "play recall1")
    game.loop.processor.process(state, action)
    result = game.loop.processor.executor.resolve(state, state.stack.pop())
    assert result.success
    assert action.source.get_zone() == ZoneType.GRAVEYARD


def test_query_result_is_stable_while_moving_matching_cards():
    game = make_game()
    state = game.state
    cards = [creature(state.active_player, state, f"unit{i}") for i in range(140)]
    for card in cards:
        card.is_tapped = True
    result = state.card_register.query(TAPPED_CREATURES)
    for card in result:
        card.owner.move_card(card, ZoneType.HAND)
    assert all(card.get_zone() == ZoneType.HAND for card in cards)
    assert state.query_cards(TAPPED_CREATURES) == ()


def test_randomized_index_results_match_live_state_after_changes():
    game = make_game()
    state = game.state
    rng = random.Random(19)
    cards = [creature(state.players[i % 2], state, f"unit{i}") for i in range(20)]
    for _ in range(100):
        card = rng.choice(cards)
        card.owner.move_card(card, rng.choice(list(ZoneType)))
        card.is_tapped = bool(rng.randrange(2))
        card.set_controller(rng.choice(state.players))
        expected = {c for c in cards if c.get_zone() == ZoneType.BATTLEFIELD and c.is_tapped}
        assert set(state.query_cards(TAPPED_CREATURES)) == expected
        for player in state.players:
            query = TAPPED_CREATURES & EqQuery(IK_CONTROLLER, player)
            assert set(state.query_cards(query)) == {c for c in expected if c.get_controller(state) is player}


def test_selected_target_is_revalidated_with_query_during_resolution():
    from game.game_actions import FixedExecutionPlan, ResolutionContext, ScheduledResolution
    from game.game_actions.data_structs.ability import EffectBinding, EffectSequence
    from game.operations.card_operations import MoveCardOperation
    from game.target.target_resolver import TargetBinding, TargetOption, TargetSlot, TargetResolver, RepetitionTargetSlotWrapper
    from game.target.target_selector import SingleTargetSelector

    game = make_game()
    state = game.state
    card = creature(state.active_player, state, "selected")
    card.is_tapped = True
    spec = QueryTargetSpec(TAPPED_CREATURES)
    slot = TargetSlot("chosen", TargetResolver(spec, SingleTargetSelector()), frozenset())
    wrapper = RepetitionTargetSlotWrapper("chosen_0", slot)
    context = ResolutionContext(
        controller=state.active_player, source=card,
        targets=TargetBinding({"chosen": {"chosen_0": TargetOption({card: 1})}}).to_immutable(),
        effects=EffectSequence((EffectBinding(RECALL_EFFECT, frozenset({wrapper})),)),
    )
    resolution = ScheduledResolution(FixedExecutionPlan([MoveCardOperation(context, card, ZoneType.HAND)]), context)
    assert spec.is_valid_target(card, card, state.active_player, state)
    card.is_tapped = False  # A response makes the chosen target illegal.
    result = game.loop.processor.executor.resolve(state, resolution)
    assert not result.success
    assert card.get_zone() == ZoneType.BATTLEFIELD


def test_query_masks_are_copies_and_difference_does_not_corrupt_indexes():
    from helper.query_system.query import QueryContext
    game = make_game()
    state = game.state
    card = creature(state.active_player, state, "mask")
    query = EqQuery(IK_KEY, "mask")
    mask = query.eval(QueryContext(state.card_register.storage, state.card_register.idx_provider))
    mask.clear()
    assert state.query_cards(query) == (card,)
    assert state.query_cards(DifferenceQuery(query, (EqQuery(IK_ZONE, ZoneType.HAND),))) == (card,)


def test_controlled_opponents_card_can_be_activated_by_current_controller():
    game = make_game()
    state = game.state
    alice, bob = state.players
    card = alice.hand["alice-adept1"]
    alice.move_card(card, ZoneType.BATTLEFIELD)
    card.set_controller(bob)
    state.switch_active_player()
    action = build_action(state, bob, "activate alice-adept1 tap_damage alice")
    assert all(intent.context.controller is bob for intent in action.get_intents())
    game.loop.processor.process(state, action)
    game.loop.processor.executor.resolve(state, state.stack.pop())
    assert alice.health == 8
    assert card.is_tapped
