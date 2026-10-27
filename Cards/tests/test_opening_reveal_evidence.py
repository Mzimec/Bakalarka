from collections import Counter, namedtuple
from dataclasses import asdict
from random import Random
from types import SimpleNamespace

import pytest

from game.ai.game_origin import GameOriginWorlds
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.knowledge import PlayerEvent
from game.ai.opening_reveal_evidence import OpeningRevealEvidence
from game.ai.opening_reveal_evidence import observed_mulligans
from game.ai.scenario_world_provider import resume_to_information
from game.ai.history_world_provider import UniformHistoryPolicy, sample_history_action
from game.ai.information_set_search import SampledRootMismatch
from game.ai.search_recall import PlayerRecall
from game.game_loop.setup import shuffle_library
from test_game_origin import origin, keep_pass


def test_deadline_shuffle_is_uniform_over_feasible_permutations():
    Ref = namedtuple("Ref", "key")
    cards = tuple(Ref(str(i)) for i in range(4))
    counts = Counter()
    for seed in range(1600):
        player = SimpleNamespace(deck={card.key: card for card in cards})
        shuffle_library(player, Random(seed), draw_deadlines=((cards[0], 2), (cards[1], 3)))
        order = tuple(reversed(tuple(player.deck)))
        assert order.index("0") < 2 and order.index("1") < 3
        counts[order] += 1
    assert len(counts) == 8
    assert all(150 < count < 250 for count in counts.values())


@pytest.mark.parametrize("limits", [(("a", 0),), (("a", 4),), (("a", True),),
    (("a", 1), ("a", 2)), (("a", 1), ("b", 1))])
def test_invalid_deadlines_do_not_mutate_library(limits):
    Ref = namedtuple("Ref", "key")
    cards = {key: Ref(key) for key in ("a", "b", "c")}
    player = SimpleNamespace(deck=dict(cards))
    with pytest.raises(ValueError):
        shuffle_library(player, Random(1), draw_deadlines=tuple((cards[key], n) for key, n in limits))
    assert player.deck == cards


def first_play(info):
    view = info.current.observation()
    if info.current.player == 1 and view.get("turn") == 1 and view.get("phase") == "PRECOMBAT_MAIN":
        land = next((a for a in info.actions if a.kind == "LandPlayAction"), None)
        if land is not None:
            return land
    return keep_pass(info)


def play_target():
    worlds = GameOriginWorlds(origin(starting_player_idx=1))
    def choose(node):
        info = node.player_information()
        if info.current.player == 0 and any(
            e.kind == "event" and e.describe().get("owner") == 1
            and e.describe().get("from_zone") == "HAND" for e in info.recall.entries):
            return None
        return first_play(info)
    return worlds, worlds(42).follow_actions(choose).player_information()


def test_native_first_opponent_play_conditions_public_card_without_forcing_their_policy():
    worlds, target = play_target()
    evidence = OpeningRevealEvidence.from_information(target, hand_size=2)
    assert evidence is not None and evidence.opponent == 1 and evidence.deadline == 2
    model = lambda info: tuple(float(a == first_play(info)) for a in info.actions)
    provider = HistoryWorldProvider(worlds, opponent_weights=model, max_attempts=64)
    hands = set()
    for seed in range(8):
        sample = provider(target)(seed)
        node = sample.start()
        assert node.player_information() == target
        hands.add(tuple(c["id"] for c in node.observation(1)["hand"]))
    assert len(hands) > 1  # Only the public revealed card was constrained.

    # The same hand constraint cannot make an always-pass opponent play a land.
    from game.ai.search_simulator import HistorySamplingExhausted
    never_play = lambda info: tuple(float(a == keep_pass(info)) for a in info.actions)
    with pytest.raises(HistorySamplingExhausted):
        HistoryWorldProvider(worlds, opponent_weights=never_play, max_attempts=2)(target)(9).start()


def synthetic_information(*extra, hand_size=2):
    view = {"turn": 1, "phase": "UPKEEP", "players": [{"id": 0, "hand_size": 2},
             {"id": 1, "hand_size": hand_size}]}
    recall = PlayerRecall().append("observation", {"request_kind": "PriorityDecisionRequest", "view": view})
    for kind, payload in extra:
        recall = recall.append(kind, payload)
    return SimpleNamespace(current=SimpleNamespace(player=0), recall=recall)


def reveal():
    return "event", asdict(PlayerEvent("card_moved", 2, "PRECOMBAT_MAIN", 1, 1,
                                       "c7", "Mountain", "HAND", "BATTLEFIELD"))


def test_hidden_draw_gives_a_deadline_not_a_known_hand_or_top_card():
    entries = [("event", asdict(PlayerEvent(kind, 2, "DRAW", 1, 1, None, None, "DECK", "HAND")))
               for kind in ("card_moved", "card_drawn")]
    evidence = OpeningRevealEvidence.from_information(synthetic_information(*entries, reveal()), hand_size=2)
    assert evidence == OpeningRevealEvidence(1, "c7", 3)
    assert evidence.constraints() == ((), (("c7", 3),))


@pytest.mark.parametrize("barrier", [
    ("action", {"kind": "LandPlayAction"}),
    ("observation", {"request_kind": "ScryRequest"}),
    ("event", asdict(PlayerEvent("library_shuffled", 1, "UPKEEP", 1, 1))),
    ("event", asdict(PlayerEvent("card_moved", 1, "UPKEEP", 1, 1, None, None, "DECK", "GRAVEYARD"))),
    ("event", asdict(PlayerEvent("card_drawn", 1, "DRAW", 1, 1, None, None, "DECK", "HAND"))),
])
def test_ambiguous_library_or_earlier_action_disables_opening_inference(barrier):
    assert OpeningRevealEvidence.from_information(synthetic_information(barrier, reveal()), hand_size=2) is None


def test_opponent_mulligan_and_missing_early_history_are_not_inferred():
    assert OpeningRevealEvidence.from_information(synthetic_information(reveal(), hand_size=1), hand_size=2) is None
    info = synthetic_information(reveal())
    info.recall = PlayerRecall(info.recall.entries[1:])
    assert OpeningRevealEvidence.from_information(info, hand_size=2) is None


@pytest.mark.parametrize("starter", [0, 1])
def test_early_mulligan_rejection_preserves_accepted_worlds_and_reduces_work(starter):
    from test_setup_deal_evidence import choose_setup
    worlds = GameOriginWorlds(origin(starting_player_idx=starter))
    def live(node):
        info = node.player_information()
        if info.current.player == 0 and info.current.request_kind == "PriorityDecisionRequest":
            return None
        return choose_setup(info, 1, 1)
    target = worlds(8).follow_actions(live).player_information()
    assert observed_mulligans(target) == {0: 0, 1: 1}
    outcomes, work = [], []
    for early in (False, True):
        counts, accepted = [], []
        for seed in range(24):
            rng = Random(seed + 900)
            policy = lambda info: sample_history_action(UniformHistoryPolicy(), info, rng)
            try:
                node = resume_to_information(worlds.condition(target)(seed), target, policy, 256,
                                             on_advance=lambda: counts.append(1), early_setup_check=early)
            except SampledRootMismatch:
                accepted.append(None)
            else:
                assert node.player_information() == target
                accepted.append(node.observation(1))
        outcomes.append(accepted)
        work.append(len(counts))
    assert outcomes[0] == outcomes[1] and any(x is not None for x in outcomes[0])
    assert work[1] < work[0]


def test_setup_pruning_requires_complete_early_public_evidence():
    assert observed_mulligans(synthetic_information(reveal())) == {}
    worlds, target = play_target()
    assert observed_mulligans(target) == {0: 0, 1: 0}
    first = worlds(1).start().player_information()
    assert observed_mulligans(first) == {}  # During setup final counts are unknown.
