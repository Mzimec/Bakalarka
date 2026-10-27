"""Own mulligan conditioning must preserve rules, unknown futures and likelihoods."""
from dataclasses import replace

import pytest

from game.ai.game_origin import GameOriginWorlds
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.search_recall import PlayerRecall
from game.ai.setup_deal_evidence import SetupDealEvidence
from game.cards.decks import DeckList
from game.game_loop.setup import SetupConfig
from test_game_origin import origin, keep_pass, weights
from test_game_setup import Controller


def choose_setup(info, observer, rounds):
    if info.current.player == observer and info.current.request_kind == "MulliganRequest":
        take = info.current.observation()["decision_context"]["mulligans_taken"] < rounds
        return next(a for a in info.actions if a.describe()["take_mulligan"] == take)
    return keep_pass(info)


def after_setup(sim, observer=0, rounds=1):
    def choose(node):
        info = node.player_information()
        if info.current.player == observer and info.current.request_kind == "PriorityDecisionRequest":
            return None
        return choose_setup(info, observer, rounds)
    return sim.follow_actions(choose)


@pytest.mark.parametrize("observer", [0, 1])
@pytest.mark.parametrize("rounds", [1, 2, 3])
def test_all_observed_deals_reconstruct_in_one_attempt_including_forced_keep(observer, rounds):
    worlds = GameOriginWorlds(origin(starting_player_idx=observer,
                                   config=SetupConfig(opening_hand_size=3, minimum_deck_size=6)))
    target = after_setup(worlds(19), observer, rounds).player_information()
    evidence = SetupDealEvidence.from_information(target, hand_size=3)
    assert len(evidence.hands) == rounds + 1
    assert all(len(hand) == 3 for hand in evidence.hands)
    assert len(target.current.observation()["hand"]) == 3 - rounds
    for seed in (100, 200, 300):
        sample = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)(target)(seed)
        root = sample.start()
        assert root.player_information() == target
        assert sample.initialization_metrics.sampling_attempts == 1
        assert sample.initialization_metrics.rejected_samples == 0
        # Resuming the strict branch also checks setup addresses and bottoming.
        sample.advance_action(root, keep_pass(target))


def test_current_bottom_request_pins_full_redraw_but_next_unseen_redraw_varies():
    worlds = GameOriginWorlds(origin())
    def stop_bottom(node):
        info = node.player_information()
        return (None if info.current.player == 0 and info.current.request_kind == "MulliganBottomRequest"
                else choose_setup(info, 0, 1))
    target = worlds(7).follow_actions(stop_bottom).player_information()
    conditioned = worlds.condition(target)
    next_hands = set()
    for seed in range(12):
        sim = conditioned(seed)
        root = sim.follow_actions(stop_bottom)
        assert root.player_information() == target
        keep_or_redraw = sim.advance_action(root, keep_pass(target))
        redraw = next(a for a in keep_or_redraw.player_information().actions if a.describe()["take_mulligan"])
        next_bottom = sim.advance_action(keep_or_redraw, redraw)
        hand = next_bottom.player_information().current.observation()["hand"]
        next_hands.add(tuple(c["id"] for c in hand))
    assert len(next_hands) > 5


def test_bottom_order_and_remaining_library_survive_conditioning():
    recipe = origin(config=SetupConfig(opening_hand_size=3, minimum_deck_size=6))
    initial = (("c1", "c2", "c3"), None)
    redraws = ((("c4", "c5", "c6"), ("c2", "c4", "c6")), ())
    libraries = set()
    for seed in range(12):
        own = Controller(2)
        game = recipe.scenario(seed, opening_hand_ids=initial, mulligan_hand_ids=redraws)((own, Controller()))
        player = game.state.players[0]
        library = tuple(reversed(tuple(player.deck.values())))
        assert tuple(c.command_id for c in player.hand.values()) == ("c6",)
        assert library[-2:] == own.bottoms[-1]
        assert tuple(c.command_id for c in library[-2:]) == ("c2", "c4")
        libraries.add(tuple(c.command_id for c in library[:-2]))
    assert len(libraries) > 1


def test_incremental_depletion_refreshes_from_all_new_deals_without_mutating_old_world():
    worlds = GameOriginWorlds(origin())
    provider = IncrementalWorldProvider(worlds, size=1, opponent_weights=weights, max_attempts=1, seed=12)
    reference = worlds(29)
    initial = reference.start().player_information()
    old = provider(initial)(33)
    old_node = old.start()
    target = after_setup(reference, rounds=2).player_information()
    sample = provider(target)(34)
    assert sample.start().player_information() == target
    assert sample.initialization_metrics.population_survivors == 0
    assert sample.initialization_metrics.population_refreshes == 1
    assert sample.initialization_metrics.sampling_attempts == 1
    assert old.start() == old_node
    old.advance_action(old_node, keep_pass(initial))


def test_opponent_hand_posterior_still_depends_on_probability_of_observed_keep():
    # Prior P(Shock in opponent hand)=1/3. Keep probabilities are 1/5 if
    # holding Shock, 4/5 otherwise. Observing keep gives posterior 1/9.
    deck = DeckList("Tiny", (("Mountain", 2), ("Shock", 1)))
    worlds = GameOriginWorlds(origin(decks=(deck, deck),
        config=SetupConfig(opening_hand_size=1, minimum_deck_size=3)))
    target = after_setup(worlds(8)).player_information()
    def model(info):
        if info.current.request_kind == "MulliganRequest":
            shock = any(c["name"] == "Shock" for c in info.current.observation()["hand"])
            keep_probability = 0.2 if shock else 0.8
            return tuple(1 - keep_probability if a.describe()["take_mulligan"] else keep_probability
                         for a in info.actions)
        return weights(info)
    provider = HistoryWorldProvider(worlds, opponent_weights=model, max_attempts=32)
    held = 0
    for seed in range(180):
        node = provider(target)(seed).start()
        assert node.player_information() == target
        # Privileged diagnostic assertion only; policies never get both views.
        held += any(c["name"] == "Shock" for c in node.observation(1)["hand"])
    assert 0.055 < held / 180 < 0.18  # Includes 1/9, excludes unweighted prior 1/3.


def test_missing_initial_evidence_is_not_replaced_with_a_post_bottom_hand():
    target = after_setup(GameOriginWorlds(origin())(4)).player_information()
    entries = list(target.recall.entries)
    first = next(i for i, e in enumerate(entries) if e.kind == "observation"
                 and e.describe()["request_kind"] == "MulliganBottomRequest")
    recall = PlayerRecall()
    for entry in entries[first:]:
        recall = recall.append(entry.kind, entry.describe())
    assert SetupDealEvidence.from_information(replace(target, recall=recall), hand_size=2) is None


@pytest.mark.parametrize("rounds", [((('c1',),), ()), ((('c1', 'c7'),), ()),
                                  ((('c1', 'c2'),) * 3, ()), ((),)])
def test_invalid_redraw_constraints_fail_explicitly(rounds):
    with pytest.raises(ValueError):
        origin().scenario(1, mulligan_hand_ids=rounds)((Controller(), Controller()))
