"""Pin only the observable untouched own library prefix, not later outcomes."""
from dataclasses import asdict, replace
from collections import Counter

import pytest

from game.ai.draw_prefix_evidence import DrawPrefixEvidence
from game.ai.game_origin import GameOriginWorlds
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.knowledge import PlayerEvent
from game.ai.search_recall import PlayerRecall
from game.ai.setup_deal_evidence import SetupDealEvidence
from game.cards.decks import DeckList
from game.game_loop.setup import SetupConfig
from test_game_origin import origin, main_node, weights, keep_pass
from test_setup_deal_evidence import choose_setup
from test_game_setup import Controller


def own_main(sim, *, turn=3, mulligans=0):
    def choose(node):
        info = node.player_information()
        view = info.current.observation()
        if (info.current.player == 0 and info.current.request_kind == "PriorityDecisionRequest"
                and view["active"] == 0 and view["turn"] == turn and view["phase"] == "PRECOMBAT_MAIN"):
            return None
        return choose_setup(info, 0, mulligans)
    return sim.follow_actions(choose)


@pytest.mark.parametrize("mulligans", [0, 1, 2])
def test_two_own_draws_after_setup_reconstruct_without_rejection(mulligans):
    worlds = GameOriginWorlds(origin())
    target = own_main(worlds(4), turn=5, mulligans=mulligans).player_information()
    setup = SetupDealEvidence.from_information(target, hand_size=2)
    prefix = DrawPrefixEvidence.from_information(target, setup)
    assert len(prefix.card_ids) == 2
    for seed in range(5):
        sample = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)(target)(seed)
        assert sample.start().player_information() == target
        assert sample.initialization_metrics.rejected_samples == 0


def test_refresh_after_observed_draw_does_not_change_old_particle():
    worlds = GameOriginWorlds(origin())
    sim = worlds(7)
    first = main_node(sim).player_information()
    provider = IncrementalWorldProvider(worlds, size=1, seed=93, opponent_weights=weights, max_attempts=1)
    old = provider(first)(1)
    previous = old.start()
    target = own_main(sim, turn=5).player_information()
    updated = provider(target)(2)
    assert updated.start().player_information() == target
    assert updated.initialization_metrics.population_refreshes == 1
    assert updated.initialization_metrics.rejected_samples == 0
    assert old.start() == previous
    old.advance_action(previous, keep_pass(first))


def test_future_unobserved_order_is_uniform_and_bottoms_stay_fixed():
    recipe = origin(config=SetupConfig(opening_hand_size=2, minimum_deck_size=6))
    prefix = DrawPrefixEvidence(0, ("c4",), "end_of_recall")
    remainder = Counter()
    for seed in range(180):
        game = recipe.scenario(seed, opening_hand_ids=(("c1", "c2"), None),
            mulligan_hand_ids=((("c2", "c3"),), ()), draw_prefix=prefix)((Controller(1), Controller()))
        library = tuple(c.command_id for c in reversed(tuple(game.state.players[0].deck.values())))
        assert library[0] == "c4" and library[-1] == "c2"
        remainder[library[1:-1]] += 1
        assert set(library) == {"c1", "c2", "c4", "c5", "c6"}
    assert len(remainder) == 6
    assert all(15 <= count <= 45 for count in remainder.values())


def evidence_with(entries):
    info = GameOriginWorlds(origin())(9).start().player_information()
    setup = SetupDealEvidence.from_information(info, hand_size=2)
    # Use cards outside that fixed known initial hand.
    available = [f"c{i}" for i in range(1, 7) if f"c{i}" not in setup.hands[-1]]
    recall = info.recall
    for kind, payload in entries(available):
        recall = recall.append(kind, payload)
    return DrawPrefixEvidence.from_information(replace(info, recall=recall), setup), available


def pair(identity, *, owner=0):
    return [("event", asdict(PlayerEvent(kind, 3, "DRAW", owner, owner, identity,
                                         "Mountain", "DECK", "HAND")))
            for kind in ("card_moved", "card_drawn")]


@pytest.mark.parametrize("barrier", [
    ("observation", {"request_kind": "ScryRequest"}),
    ("observation", {"request_kind": "UnknownInspectionRequest"}),
    ("event", asdict(PlayerEvent("library_shuffled", 3, "DRAW", 0, 0))),
    ("event", asdict(PlayerEvent("card_moved", 3, "DRAW", 0, 0, "c1", "Mountain", "HAND", "DECK"))),
    ("event", asdict(PlayerEvent("card_moved", 3, "DRAW", 0, 0, "c1", "Mountain", "DECK", "GRAVEYARD"))),
])
def test_library_change_or_inspection_stops_prefix_and_preserves_earlier_evidence(barrier):
    prefix, available = evidence_with(lambda ids: [*pair(ids[0]), barrier, *pair(ids[1])])
    assert prefix.card_ids == (available[0],)
    assert prefix.stop_reason != "end_of_recall"


def test_move_without_draw_is_not_inferred_as_top_draw():
    prefix, _ = evidence_with(lambda ids: [pair(ids[0])[0], *pair(ids[1])])
    assert prefix.card_ids == () and prefix.stop_reason == "unpaired_library_move"


@pytest.mark.parametrize("kind", ["AbilityDecisionRequest", "ManaGenerationRequest", "TriggerOrderRequest",
    "LegendRequest", "ReplacementRequest", "ReplacementOrderRequest", "ReplacementAcceptanceRequest"])
def test_action_preparation_does_not_discard_later_draw_evidence(kind):
    prefix, available = evidence_with(lambda ids: [*pair(ids[0]),
        ("observation", {"request_kind": kind}), *pair(ids[1])])
    assert prefix.card_ids == tuple(available[:2]) and prefix.stop_reason == "end_of_recall"


def test_ability_preparation_still_stops_at_actual_inspection_before_draw():
    prefix, available = evidence_with(lambda ids: [*pair(ids[0]),
        ("observation", {"request_kind": "AbilityDecisionRequest"}),
        ("observation", {"request_kind": "ScryRequest"}), *pair(ids[1])])
    assert prefix.card_ids == (available[0],)
    assert prefix.stop_reason == "inspection_or_unreviewed_request"


def test_opponent_hidden_draws_never_constrain_own_or_opponent_library():
    prefix, available = evidence_with(lambda ids: [*pair(None, owner=1), *pair(ids[0])])
    assert prefix.card_ids == (available[0],)


def test_repeated_card_stops_before_a_second_incarnation():
    prefix, available = evidence_with(lambda ids: [*pair(ids[0]), *pair(ids[0])])
    assert prefix.card_ids == (available[0],)
    assert prefix.stop_reason == "known_or_repeated_card"


def test_mulligan_bottom_is_checked_by_replay_not_pinned_into_random_prefix():
    recipe = origin(config=SetupConfig(opening_hand_size=3, minimum_deck_size=6))
    worlds = GameOriginWorlds(replace(recipe, decks=(recipe.decks[0], DeckList("Larger", (("Mountain", 12),)))))
    # Three random residual cards, then the first known bottom on own turn 9.
    target = own_main(worlds(10), turn=9, mulligans=2).player_information()
    setup = SetupDealEvidence.from_information(target, hand_size=3)
    prefix = DrawPrefixEvidence.from_information(target, setup)
    assert len(prefix.card_ids) == 3 and prefix.stop_reason == "known_or_repeated_card"
    sample = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)(target)(99)
    assert sample.start().player_information() == target
