"""Ordinary setup and observation-only opening-hand conditioning."""
from collections import Counter
from random import Random

import pytest

from game.ai.game_origin import GameOrigin, GameOriginWorlds
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator, HistorySamplingExhausted
from game.cards.decks import DeckList
from game.game_loop.setup import SetupConfig, create_game, shuffle_library
from test_game_setup import Controller


def catalog():
    from game.cards.starter_cards import starter_catalog
    return starter_catalog()


def origin(**kwargs):
    deck = DeckList("Declared", (("Mountain", 4), ("Shock", 2)))
    defaults = dict(decks=(deck, deck), catalog_factory=catalog,
                    config=SetupConfig(opening_hand_size=2, minimum_deck_size=6), starting_player_idx=0)
    return GameOrigin(**(defaults | kwargs))


def keep_pass(info):
    for action in info.actions:
        if action.kind == "MulliganOption" and not action.describe()["take_mulligan"]:
            return action
        if action.kind == "PassPriorityAction":
            return action
    return ObservationOpponentPolicy()(info)


def weights(info):
    preferred = keep_pass(info)
    return tuple(float(a == preferred) for a in info.actions)


def main_node(sim, observer=0):
    def choose(node):
        info = node.player_information()
        if (info.current.player == observer and info.current.request_kind == "PriorityDecisionRequest"
                and info.current.observation()["phase"] == "PRECOMBAT_MAIN"
                and info.current.observation()["active"] == observer):
            return None
        return keep_pass(info)
    return sim.follow_actions(choose)


def order(state):
    return tuple((tuple(p.hand), tuple(p.deck)) for p in state.players)


@pytest.mark.parametrize("starter", [None, 0, 1])
def test_origin_reuses_normal_setup_with_identical_seeded_result(starter):
    recipe = origin(starting_player_idx=starter)
    game = recipe.scenario(91)((Controller(1), Controller()))
    direct = create_game(recipe.decks, (Controller(1), Controller()), catalog(), seed=91,
                         config=recipe.config, names=recipe.names, starting_player_idx=starter)
    assert order(game.state) == order(direct)
    assert game.state.mulligans_taken == direct.mulligans_taken == (1, 0)
    assert game.state.active_player_idx == direct.active_player_idx
    assert game.state.rng.getstate() == direct.rng.getstate()


def test_fresh_runtime_definitions_rng_and_branches_are_isolated():
    recipe = origin()
    a, b = (recipe.scenario(7)((Controller(), Controller())) for _ in range(2))
    ca, cb = (next(iter(g.state.players[0].hand.values())) for g in (a, b))
    assert ca is not cb and ca.definition is not cb.definition
    before = b.state.rng.getstate()
    a.state.rng.random()
    a.state.players[0].health = 1
    assert b.state.rng.getstate() == before and b.state.players[0].health == 20
    sim = GameOriginWorlds(recipe)(7)
    root = sim.start()
    assert root.decision.kind == "MulliganRequest"
    keep = keep_pass(root.player_information())
    child = sim.advance_action(root, keep)
    assert sim.fork(root) == root and sim.fork(child) == child


def test_conditioned_hand_matches_while_both_hidden_permutations_vary():
    worlds = GameOriginWorlds(origin())
    original = worlds(42).start().player_information()
    conditioned = worlds.condition(original)
    other_hands, own_libraries = set(), set()
    for seed in range(12):
        sim = conditioned(seed)
        root = sim.start()
        assert root.player_information() == original
        reply = sim.advance_action(root, keep_pass(original))
        other_hands.add(tuple(c["id"] for c in reply.player_information().current.observation()["hand"]))
        game = sim.factory((Controller(), Controller()))
        own_libraries.add(tuple(game.state.players[0].deck))
        assert Counter(c.name for c in game.state.players[0].get_cards()) == {"Mountain": 4, "Shock": 2}
    assert len(other_hands) > 5 and len(own_libraries) > 5


def test_conditioned_shuffle_is_uniform_over_remaining_physical_cards():
    state = origin().scenario(1)((Controller(), Controller())).state
    player = state.players[0]
    cards = tuple(player.deck.values())
    assert len(cards) == 4
    counts = Counter()
    for seed in range(1200):
        # Reset the input order: each draw represents an independent shuffle.
        player.deck.clear()
        player.deck.update((c.key, c) for c in cards)
        shuffle_library(player, Random(seed), top_cards=cards[:2])
        result = tuple(reversed(tuple(player.deck.values())))
        assert result[:2] == cards[:2]
        counts[tuple(c.key for c in result[2:])] += 1
    assert len(counts) == 2 and all(500 < n < 700 for n in counts.values())


@pytest.mark.parametrize("ids", [((),), (("c1", "c1"), None), (("missing", "c1"), None),
                                (("c1", "c7"), None), (("c1",), None)])
def test_invalid_initial_hand_constraints_fail(ids):
    with pytest.raises(ValueError):
        origin().scenario(1, opening_hand_ids=ids)((Controller(), Controller()))


def test_mulligan_redraw_is_not_forced_to_initial_hand():
    recipe = origin()
    ids = (("c1", "c2"), None)
    hands = set()
    for seed in range(10):
        game = recipe.scenario(seed, opening_hand_ids=ids)((Controller(1), Controller()))
        assert game.state.mulligans_taken == (1, 0)
        hands.add(tuple(c.command_id for c in game.state.players[0].hand.values()))
    assert len(hands) > 1


@pytest.mark.parametrize("observer", [0, 1])
def test_full_history_provider_reaches_ordinary_main_phase(observer):
    worlds = GameOriginWorlds(origin(starting_player_idx=observer))
    target = main_node(worlds(4), observer).player_information()
    provider = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)
    for seed in (100, 200):
        sample = provider(target)(seed)
        root = sample.start()
        assert root.player_information() == target
        assert sample.initialization_metrics.sampling_attempts == 1
        assert sample.initialization_metrics.rejected_samples == 0
        child = sample.advance_action(root, keep_pass(target))
        assert child.history[:len(root.history)] == root.history


def test_incremental_provider_accepts_conditioned_origin_and_reuses_population():
    worlds = GameOriginWorlds(origin())
    reference = worlds(9)
    root = reference.start()
    provider = IncrementalWorldProvider(worlds, size=2, opponent_weights=weights, max_attempts=1)
    first = provider(root.player_information())(6)
    assert first.start().player_information() == root.player_information()
    target = main_node(reference).player_information()
    second = provider(target)(8)
    assert second.start().player_information() == target
    assert second.initialization_metrics.population_survivors == 2
    assert second.initialization_metrics.sampling_attempts == 0


def test_starting_player_choice_and_mulligan_remain_replayable_setup_boundaries():
    worlds = GameOriginWorlds(origin(starting_player_idx=None))
    sim = worlds(3)
    root = sim.start()
    assert root.decision.kind == "StartingPlayerRequest"
    assert worlds.condition(root.player_information()) is worlds
    hand = sim.advance_action(root, root.player_information().actions[0])
    assert hand.decision.kind == "MulliganRequest"
    redraw = next(a for a in hand.player_information().actions if a.describe()["take_mulligan"])
    node = sim.advance_action(hand, redraw)
    node = sim.advance_action(node, keep_pass(node.player_information()))
    assert node.decision.kind == "MulliganBottomRequest"
    assert sim.fork(node) == node
    bottomed = sim.advance_action(node, node.player_information().actions[0])
    assert bottomed.decision.kind == "MulliganRequest"
    assert len(bottomed.player_information().current.observation()["hand"]) == 1


def test_conditioned_factories_are_independent_snapshots_of_evidence():
    worlds = GameOriginWorlds(origin())
    a, b = (worlds(seed).start().player_information() for seed in (5, 6))
    assert a != b
    pa, pb = worlds.condition(a), worlds.condition(b)
    assert pa(1).start().player_information() == a
    assert pb(1).start().player_information() == b
    assert pa(2).start().player_information() == a


def test_later_draw_is_checked_not_overwritten_by_opening_hand_conditioning():
    worlds = GameOriginWorlds(origin(), condition_draws=False)
    # Player 1 must draw before their first main phase. Conditioning only their
    # opening hand must NOT make every independently sampled future draw match.
    target = main_node(worlds(7), observer=1).player_information()
    provider = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)
    accepted = rejected = 0
    for seed in range(16):
        try:
            assert provider(target)(seed).start().player_information() == target
            accepted += 1
        except HistorySamplingExhausted:
            rejected += 1
    assert accepted and rejected


def test_live_agent_setup_recall_matches_sample_without_reading_live_state():
    recipe = origin()
    worlds = GameOriginWorlds(recipe)
    captured = []
    def provider(info):
        captured.append(info)
        return None
    own = MCTSAgent(world_provider=provider, fallback=keep_pass)
    other = MCTSAgent(world_provider=lambda info: None, fallback=keep_pass)
    game = recipe.scenario(15)((own, other))
    # Complete the first main phase using the live loop; retain its first own
    # boundary rather than manufacturing a request on a post-setup state.
    while game.state.turn.phase.name != "BEGIN_COMBAT":
        game.loop.step(game.state)
    target = next(info for info in captured if info.current.observation().get("phase") == "PRECOMBAT_MAIN")
    before = order(game.state), game.state.rng.getstate(), own.recall
    sample = HistoryWorldProvider(worlds, opponent_weights=weights, max_attempts=1)(target)(998)
    assert sample.start().player_information() == target
    assert (order(game.state), game.state.rng.getstate(), own.recall) == before
