"""Runtime queries retain membership through mutation and effect expiration."""
from helper.query_system.query import EqQuery, HasQuery
from game.game_state import State, Player, Card, CardDefinition
from game.game_loop.minimal_game import ScriptedController
from game.enums import ZoneType, CardType, TurnPhase
from game.game_state.registers.card_register import IK_KEY, IK_STATIC_CONTINUOUS
from game.game_state.registers.effect_register import (
    IK_EFFECT_KEY, IK_EFFECT_SOURCE, IK_EFFECT_LAYER, IK_EFFECT_DURATION,
)
from game.game_state.modifier import (
    ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState,
    PermanentDuration, TimeStampDuration, TimeStamp, DynamicTargetingStrategy, AddIntModifier,
)
from game.game_state.layers import modifier_layer
from game.stat_type import STAT_POWER
from game.target.target_spec import QueryTargetSpec
from game.cards.starter_cards import starter_catalog


def make_state():
    return State([Player([], ScriptedController(), name="A"),
                  Player([], ScriptedController(), name="B")])


def add(state, definition, zone=ZoneType.BATTLEFIELD):
    card = Card(definition, state.active_player)
    card.owner.add_card(card, zone)
    return card


def test_card_lookup_does_not_traverse_player_zones(monkeypatch):
    state = make_state()
    card = add(state, CardDefinition("Unit"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Query must not scan player zones")
    for player in state.players:
        monkeypatch.setattr(player, "get_cards", forbidden)
    assert state.get_cards(from_zones=[ZoneType.BATTLEFIELD]) == [card]
    assert state.get_cards(from_players=[]) == []
    card.owner.move_card(card, ZoneType.EXILE)
    assert state.get_cards(from_zones=[ZoneType.BATTLEFIELD]) == []
    assert state.get_cards(from_zones=[ZoneType.EXILE]) == [card]


def test_player_loss_invalidates_alive_membership():
    state = make_state()
    player = state.active_player
    assert player in state.active_players
    player.has_lost = True
    assert player not in state.active_players
    assert state.is_game_over
    player.has_lost = False
    assert player in state.active_players
    assert not state.is_game_over


def test_formula_query_and_index_agree_after_zone_and_control_changes():
    state = make_state()
    nightmare = add(state, starter_catalog()["Nightmare"], ZoneType.HAND)
    swamp = add(state, starter_catalog()["Swamp"])
    from game.game_state.registers.card_register import IK_POWER
    for owner, zone, expected in (
        (state.players[0], ZoneType.BATTLEFIELD, 1),
        (state.players[1], ZoneType.BATTLEFIELD, 0),
        (state.players[0], ZoneType.BATTLEFIELD, 1),
        (state.players[0], ZoneType.GRAVEYARD, 0),
    ):
        swamp.set_controller(owner)
        swamp.owner.move_card(swamp, zone)
        assert nightmare.get_power(state) == expected
        assert nightmare in state.query_cards(EqQuery(IK_POWER, expected))
    assert nightmare in state.query_cards(EqQuery(IK_STATIC_CONTINUOUS, True))


def test_effect_indexes_are_removed_on_expiration_and_key_reuse():
    state = make_state()
    card = add(state, CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=2))
    mod = AddIntModifier(1)
    layer = modifier_layer(STAT_POWER, mod)
    def effect(duration):
        return ContinuousEffect("bonus", ContinuousEffectDefinition(
            duration, card, state.time_stamp,
            DynamicTargetingStrategy(QueryTargetSpec(EqQuery(IK_KEY, card.key))),
            {STAT_POWER: [mod]},
        ), ContinuousEffectState(set()))
    timed = effect(TimeStampDuration(TimeStamp(1, TurnPhase.CLEANUP)))
    state.add_continuous_effect(timed)
    assert state.query_effects(EqQuery(IK_EFFECT_SOURCE, card.key)
        & EqQuery(IK_EFFECT_LAYER, layer)) == (timed,)
    state.turn.number = 1
    state.turn.phase = TurnPhase.CLEANUP
    assert state.query_effects(HasQuery(IK_EFFECT_KEY)) == ()
    replacement = effect(PermanentDuration())
    state.add_continuous_effect(replacement)
    assert state.query_effects(EqQuery(IK_EFFECT_DURATION, PermanentDuration)) == (replacement,)
    assert state.query_effects(EqQuery(IK_EFFECT_DURATION, TimeStampDuration)) == ()
    assert card.get_power(state) == 3


def test_sba_queries_skip_unrelated_zones_and_remove_departed_tokens(monkeypatch):
    from game.game_actions.resolution.sba_resolver import LethalCreaturesRule
    from game.rules.permanents import PermanentStateRule, CeaseTokenOperation
    from game.game_state.registers.card_register import IK_IS_TOKEN
    state = make_state()
    dead = add(state, CardDefinition("Dead", types=frozenset({CardType.CREATURE}), power=1, toughness=0))
    hand = add(state, dead.definition, ZoneType.HAND)
    token = Card(CardDefinition("Token"), state.active_player, is_token=True)
    token.owner.add_card(token, ZoneType.BATTLEFIELD)
    token.owner.move_card(token, ZoneType.EXILE)
    def forbidden(*args, **kwargs):
        raise AssertionError("SBA must select indexed candidates")
    monkeypatch.setattr(state, "get_cards", forbidden)
    lethal = LethalCreaturesRule().collect(state)
    assert [v.opertaions[0].card for v in lethal] == [dead]
    violations = PermanentStateRule().collect(state)
    operations = [op for v in violations for op in v.opertaions]
    assert len(operations) == 1
    assert isinstance(operations[0], CeaseTokenOperation)
    operations[0].execute(state)
    assert state.query_cards(EqQuery(IK_IS_TOKEN, True)) == ()
    assert state.card_register.get_by_key(hand.key) is hand


def test_token_index_tracks_runtime_flag():
    from game.game_state.registers.card_register import IK_IS_TOKEN
    state = make_state()
    card = add(state, CardDefinition("Card"))
    assert state.query_cards(EqQuery(IK_IS_TOKEN, True)) == ()
    card.is_token = True
    assert state.query_cards(EqQuery(IK_IS_TOKEN, True)) == (card,)
    card.is_token = False
    assert state.query_cards(EqQuery(IK_IS_TOKEN, True)) == ()


def test_cleanup_clears_damage_after_creature_type_is_lost(monkeypatch):
    from game.game_loop.game_loop import CleanupPhaseController
    from game.stat_type import STAT_TYPES
    state = make_state()
    card = add(state, CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=2, toughness=2))
    card.state.damage_marked = 1
    card.state.damage_by_deathtouch = True
    card.set_base_stat(STAT_TYPES, {CardType.ARTIFACT})
    def forbidden(*args, **kwargs):
        raise AssertionError("Cleanup must use battlefield query")
    monkeypatch.setattr(state, "get_cards", forbidden)
    CleanupPhaseController().execute_turn_based_actions(state)
    assert card.state.damage_marked == 0
    assert not card.state.damage_by_deathtouch


def test_skip_untap_index_consumes_only_due_player_and_checks_incarnation():
    from game.game_loop.game_loop import UntapPhaseController
    from game.game_state.registers.card_register import IK_SKIP_UNTAP_PLAYER
    state = make_state()
    card = add(state, CardDefinition("Unit"))
    card.state.tapped = True
    card.state.skip_untap = (card.zone_revision, state.players[1])
    assert state.query_cards(EqQuery(IK_SKIP_UNTAP_PLAYER, state.players[1])) == (card,)
    UntapPhaseController().execute_turn_based_actions(state)
    assert card.state.skip_untap is not None
    card.owner.move_card(card, ZoneType.HAND)
    card.owner.move_card(card, ZoneType.BATTLEFIELD)
    card.set_controller(state.players[1])
    card.state.tapped = True
    state.switch_active_player()
    UntapPhaseController().execute_turn_based_actions(state)
    assert not card.is_tapped  # Old-incarnation marker is consumed, not applied.
    assert state.query_cards(EqQuery(IK_SKIP_UNTAP_PLAYER, state.players[1])) == ()


def test_player_loss_query_tracks_poison_failed_draw_and_loss():
    from game.game_actions.resolution.sba_resolver import PlayerLossRule
    state = make_state()
    player = state.active_player
    rule = PlayerLossRule()
    assert rule.collect(state) == []
    player.poison_counters = 10
    assert rule.collect(state)[0].opertaions[0].reason == "poison"
    player.poison_counters = 0
    assert rule.collect(state) == []
    player.failed_draw = True
    operation = rule.collect(state)[0].opertaions[0]
    assert operation.reason == "empty_library"
    operation.execute(state)
    assert rule.collect(state) == []


def test_registration_cursor_distinguishes_recycled_runtime_ids_and_reregistration():
    state = make_state()
    card = add(state, CardDefinition("Old"))
    register = state.card_register
    old_id = card.runtime_id
    cursor = register.registration_cursor
    register.unregister(card)
    other = add(state, CardDefinition("New"))
    assert other.runtime_id == old_id
    register.register(card)
    assert register.introduced_after(cursor) == (other,)


def test_cost_rollback_queries_new_cards_and_restores_damage_indexes(monkeypatch):
    from game.game_actions.resolution.cost_transaction import CostTransaction
    from game.game_state.registers.card_register import IK_HAS_DAMAGE
    state = make_state()
    card = add(state, CardDefinition("Old"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Transaction identity lookup must use registration cursor")
    monkeypatch.setattr(state, "get_cards", forbidden)
    transaction = CostTransaction(state)
    card.state.damage_marked = 2
    new = add(state, CardDefinition("New"))
    assert state.query_cards(EqQuery(IK_HAS_DAMAGE, True)) == (card,)
    transaction.rollback()
    assert new.runtime_id is None and new._game_state is None
    assert card.state.damage_marked == 0
    assert state.query_cards(EqQuery(IK_HAS_DAMAGE, True)) == ()


def test_expiration_query_skips_permanent_and_future_effects():
    from game.game_state.registers.effect_register import EffectRegister
    from game.game_state.modifier import GameMoment, GameMomentDuration, PlayerMoment
    state = make_state()
    source = add(state, CardDefinition("Source"))
    register = EffectRegister()
    def effect(key, duration):
        result = ContinuousEffect(key, ContinuousEffectDefinition(
            duration, source, state.time_stamp,
            DynamicTargetingStrategy(QueryTargetSpec(EqQuery(IK_KEY, source.key))), {}
        ), ContinuousEffectState(set()))
        register.add(result)
        return result
    effect("permanent", PermanentDuration())
    effect("future", TimeStampDuration(TimeStamp(99, TurnPhase.CLEANUP)))
    due = effect("due", TimeStampDuration(state.time_stamp))
    state.players[1].moment = PlayerMoment(0, TurnPhase.UNTAP)
    player_effect = effect("player", GameMomentDuration(GameMoment(1, PlayerMoment(99, TurnPhase.CLEANUP))))
    assert register.expiration_candidates(state) == (due,)
    state.players[1].has_lost = True
    assert register.expiration_candidates(state) == (due, player_effect)


def test_predicate_prefilter_avoids_reading_unrelated_battlefield_cards():
    from game.cards.starter_cards import PredicateTargetSpec
    from game.game_state.registers.card_register import IK_TYPE
    state = make_state()
    unit = add(state, CardDefinition("Unit", types=frozenset({CardType.CREATURE}), power=1, toughness=1))
    add(state, CardDefinition("Rock", types=frozenset({CardType.ARTIFACT})))
    visited = []
    spec = PredicateTargetSpec(lambda card, *args: visited.append(card) or True,
                               query=EqQuery(IK_TYPE, CardType.CREATURE))
    assert tuple(spec.generate_candidates(unit, state.active_player, state)) == (unit,)
    assert visited == [unit]
