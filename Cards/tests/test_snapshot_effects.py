import pytest

from game.ai.current_position_worlds import CurrentPositionWorlds
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.game_origin import GameOrigin
from game.cards.decks import DeckList
from game.cards.catalog import game_catalog
from game.game_loop.setup import SetupConfig
from game.enums import ZoneType, TurnPhase
from game.simulation.search_config import search_generation_policy
from test_current_position_worlds import controllers


def position():
    deck = DeckList("Hidden statics", (("Island", 4), ("Nightmare", 2), ("Winged Words", 2), ("Shock", 2)))
    origin = GameOrigin((deck, deck), game_catalog, starting_player_idx=0,
        config=SetupConfig(opening_hand_size=2, minimum_deck_size=10))
    game = origin.scenario(42)(controllers())
    state = game.state
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    state.priority.current_player = state.players[0]
    # A private hand-only static is a second case besides all-zone CDA.
    other = state.players[1]
    other.move_card(next(c for c in other.get_cards() if c.name == "Winged Words"), ZoneType.HAND)
    return origin, game


def prepare(origin, game):
    state = game.state
    request = PriorityDecisionRequest(state, state.players[0])
    prior = PlayerRecall()
    info = DecisionProjector(request, tuple(request.option_space(search_generation_policy(request))), prior).build().information
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    factory = provider.prepare(request, info, prior, state.players[0].controller.knowledge)
    return provider, factory, info


def test_hidden_self_only_static_effects_rebuild_from_sampled_cards_without_live_mutation():
    origin, game = position()
    provider, factory, info = prepare(origin, game)
    assert factory is not None, provider.unsupported_detail
    original = [(e, e.state.timestamp_order, set(e.state.currently_affected)) for e in game.state._cont_effect_manager.query()]
    for seed in range(4):
        sim = factory(seed)
        assert sim.start().player_information() == info
    assert [(e, e.state.timestamp_order, set(e.state.currently_affected)) for e in game.state._cont_effect_manager.query()] == original


def test_real_hidden_membership_cannot_change_a_sampled_static_graph():
    origin, game = position()
    first, factory, info = prepare(origin, game)
    other = game.state.players[1]
    winged = next(c for c in other.hand.values() if c.name == "Winged Words")
    island = next(c for c in other.deck.values() if c.name == "Island")
    # Counterfactual private membership; no public event or size change.
    other.hand.pop(winged.key)
    other.deck.pop(island.key)
    winged.state.zone_info.zone = ZoneType.DECK
    island.state.zone_info.zone = ZoneType.HAND
    other.deck.append(winged)
    other.hand.append(island)
    game.state.card_register.mark_changed(winged)
    game.state.card_register.mark_changed(island)
    second, other_factory, other_info = prepare(origin, game)
    assert other_info == info
    assert other_factory is not None, second.unsupported_detail
    assert factory(99).start()._views == other_factory(99).start()._views


def test_unreviewed_hidden_resolved_effect_remains_an_explicit_snapshot_failure():
    from game.game_state.modifier import (ContinuousEffect, ContinuousEffectDefinition,
        ContinuousEffectState, AnchoredDuration, DynamicTargetingStrategy)
    from game.target.target_spec import SelfSpec
    origin, game = position()
    hidden = next(iter(game.state.players[1].hand.values()))
    effect = ContinuousEffect("not-derived", ContinuousEffectDefinition(
        AnchoredDuration(frozenset({ZoneType.HAND}), hidden), hidden, game.state.time_stamp,
        DynamicTargetingStrategy(SelfSpec()), {}), ContinuousEffectState(set()))
    game.state._cont_effect_manager.add(effect)
    provider, factory, _ = prepare(origin, game)
    assert factory is None
    assert provider.unsupported_reason.startswith("unsupported_snapshot")


def test_hidden_native_history_source_fails_before_graph_copy_without_reading_identity(monkeypatch):
    from game.ai.runtime_clone import reject_unknown_history_sources, InaccessibleHistorySource, snapshot_failure_trace
    from game.game_actions.resolution.event_bus import GameEvent
    from game.game_state.card import Card
    origin, game = position()
    state = game.state
    hidden = next(iter(state.players[1].hand.values()))
    state.turn_events = [GameEvent("card_drawn", hidden, state.players[1])]
    # A preflight must not inspect even the hidden printed definition.
    original = Card.definition
    def forbid(card):
        if card is hidden:
            raise AssertionError("Read hidden definition")
        return original.__get__(card, Card)
    monkeypatch.setattr(Card, "definition", property(forbid))
    with pytest.raises(InaccessibleHistorySource) as caught:
        reject_unknown_history_sources(state, (), state.players[0])
    assert snapshot_failure_trace(caught.value) == ["State", "turn_events", "GameEvent", "source", "Card"]
    reject_unknown_history_sources(state, (hidden,), state.players[0])
    reject_unknown_history_sources(state, (), state.players[1])


def test_history_preflight_preserves_extension_dispatch():
    from game.ai.runtime_clone import reject_unknown_history_sources
    from game.game_actions.resolution.event_bus import GameEvent
    class CustomEvent(GameEvent):
        pass
    _, game = position()
    hidden = next(iter(game.state.players[1].hand.values()))
    game.state.turn_events = [CustomEvent("card_drawn", hidden, hidden.owner)]
    reject_unknown_history_sources(game.state, (), game.state.players[0])
