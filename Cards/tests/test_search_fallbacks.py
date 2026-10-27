from dataclasses import replace
from types import SimpleNamespace

import pytest

from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.current_position_worlds import CurrentPositionWorlds
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.simulation.search_config import search_generation_policy, SearchAgentConfig
from test_information_set_search import Tree, Score, first
from test_current_position_worlds import position, controllers


def chain():
    rows = {"root": {"children": {"good": "g0", "bad": "b0"}}}
    for prefix, score in (("g", .7), ("b", -.7)):
        for index in range(6):
            rows[f"{prefix}{index}"] = {"pending": True, "children": {"wait": f"{prefix}{index+1}"}}
        rows[f"{prefix}6"] = {"score": score, "children": {"wait": f"{prefix}6"}}
    return Tree(rows)


def test_forced_chain_preserves_rules_steps_without_spending_extensions():
    budget = InformationSearchBudget(worlds=1, iterations=8, max_depth=1, max_extensions=0, max_advances=128)
    baseline = InformationSetSearch(opponent_policy=first, evaluator=Score(), budget=budget).search(
        lambda seed: chain(), chain().start().player_information())
    assert baseline.used_fallback and baseline.fallback_reason == "extension_limit"
    result = InformationSetSearch(opponent_policy=first, evaluator=Score(), budget=budget,
        compress_forced=True).search(lambda seed: chain(), chain().start().player_information())
    assert result.choice.kind == "good" and not result.used_fallback
    assert result.metrics.iterations_completed == 8 and result.metrics.forced_transitions == 48
    assert result.metrics.advance_attempts == 56


def test_forced_chain_still_obeys_the_hard_work_budget():
    budget = InformationSearchBudget(worlds=1, iterations=8, max_depth=1, max_extensions=0, max_advances=3)
    result = InformationSetSearch(opponent_policy=first, evaluator=Score(), budget=budget,
        compress_forced=True).search(lambda seed: chain(), chain().start().player_information())
    assert result.used_fallback and result.stop_reason == result.fallback_reason == "advance_budget"
    assert result.metrics.advance_attempts == 3 and result.metrics.iterations_completed == 0


def test_a_forced_root_still_publishes_its_completed_value():
    factory = lambda seed: Tree({"root": {"children": {"win": "win"}}, "win": {"winner": 0}})
    result = InformationSetSearch(opponent_policy=first, compress_forced=True,
        budget=InformationSearchBudget(worlds=1, iterations=4)).search(factory, factory(0).start().player_information())
    assert not result.used_fallback and result.value == 1 and result.estimates[0].visits == 4


def test_forced_compression_does_not_silently_increase_the_configured_depth():
    factory = lambda seed: Tree({"root": {"children": {"wait": "leaf"}},
        "leaf": {"score": .25, "children": {"win": "win", "lose": "lose"}},
        "win": {"winner": 0}, "lose": {"winner": 1}})
    result = InformationSetSearch(opponent_policy=first, evaluator=Score(), compress_forced=True,
        budget=InformationSearchBudget(worlds=1, iterations=4, max_depth=1)).search(
            factory, factory(0).start().player_information())
    assert result.value == .25 and result.metrics.terminal_leaves == 0
    assert result.metrics.advance_attempts == 4


def test_partial_coverage_is_an_explicit_bounded_mcts_recommendation_not_a_truncated_menu():
    factory = lambda seed: Tree({"root": {"children": {str(i): "win" for i in range(100)}}, "win": {"winner": 0}})
    root = factory(0).start().player_information()
    budget = InformationSearchBudget(worlds=1, iterations=2)
    strict = InformationSetSearch(opponent_policy=first, budget=budget).search(factory, root)
    bounded = InformationSetSearch(opponent_policy=first, budget=budget, require_root_coverage=False).search(factory, root)
    assert strict.used_fallback and strict.fallback_reason == "root_coverage"
    assert not bounded.used_fallback and not bounded.root_coverage_complete
    assert not bounded.metrics.root_coverage_required and bounded.metrics.root_actions == 100
    assert len(bounded.estimates) == 100 and sum(e.visits > 0 for e in bounded.estimates) == 2
    assert bounded.choice in root.actions


def test_allowing_partial_coverage_does_not_accept_censored_trajectory_values():
    result = InformationSetSearch(opponent_policy=first, evaluator=Score(), require_root_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=2, max_depth=1, max_extensions=0)).search(
            lambda seed: chain(), chain().start().player_information())
    assert result.used_fallback and result.fallback_reason == "extension_limit"
    assert all(e.mean_value is None for e in result.estimates)


@pytest.mark.parametrize("selection", ["puct", "hierarchical_puct"])
def test_standard_puct_does_not_spend_all_iterations_on_mandatory_menu_coverage(selection):
    factory = lambda seed: Tree({"root": {"children": {str(i): "win" for i in range(100)}}, "win": {"winner": 0}})
    info = factory(0).start().player_information()
    prior = lambda current: {a: (1 if a == current.actions[0] else .00001) for a in current.actions}
    result = InformationSetSearch(opponent_policy=first, selection=selection, action_prior=prior,
        force_initial_coverage=False, require_root_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=16)).search(factory, info)
    assert not result.used_fallback and result.metrics.iterations_completed == 16
    assert result.estimates[0].visits == 16 and result.metrics.root_actions_visited == 1
    assert not result.root_coverage_complete and not result.metrics.forced_initial_coverage


def test_time_budget_retains_completed_samples_and_exposes_partial_coverage(monkeypatch):
    from game.ai.information_set_search import _Run
    # Deterministic clock boundary after three completed root simulations.
    monkeypatch.setattr(_Run, "time_exhausted", lambda run: run.completed >= 3)
    factory = lambda seed: Tree({"root": {"children": {str(i): "win" for i in range(20)}}, "win": {"winner": 0}})
    result = InformationSetSearch(opponent_policy=first, require_root_coverage=False,
        budget=InformationSearchBudget(worlds=1, iterations=100), max_time_ms=100).search(
            factory, factory(0).start().player_information())
    assert result.stop_reason == "time_budget" and not result.used_fallback
    assert result.metrics.iterations_completed == 3 and result.metrics.time_limit_ms == 100
    assert result.metrics.root_actions_visited == 3 and not result.root_coverage_complete


def test_time_budget_before_world_initialization_cannot_invent_a_value(monkeypatch):
    from game.ai.information_set_search import _Run
    monkeypatch.setattr(_Run, "time_exhausted", lambda run: True)
    result = InformationSetSearch(opponent_policy=first, require_root_coverage=False).search(
        lambda seed: chain(), chain().start().player_information())
    assert result.used_fallback and result.fallback_reason == "time_budget"
    assert result.value is None and result.metrics.worlds_built == 0


def test_soft_deadline_allows_one_complete_attempt_before_honoring_the_clock(monkeypatch):
    import game.ai.information_set_search as module
    ticks = iter([0.0])
    monkeypatch.setattr(module, "perf_counter", lambda: next(ticks, 1.0))
    factory = lambda seed: Tree({"root": {"children": {"a": "win", "b": "win"}}, "win": {"winner": 0}})
    result = InformationSetSearch(opponent_policy=first, require_root_coverage=False,
        max_time_ms=100, min_trajectories=1, budget=InformationSearchBudget(worlds=1, iterations=8)).search(
            factory, factory(0).start().player_information())
    assert result.stop_reason == "time_budget" and not result.used_fallback
    assert result.metrics.iterations_completed == 1 and result.value == 1


def test_continuation_uses_only_the_safe_anchor_and_exact_own_recall():
    from game.enums import ZoneType
    origin, game = position()
    player = game.state.players[0]
    mountain = next(c for c in player.get_cards() if c.name == "Mountain")
    player.move_card(mountain, ZoneType.HAND)
    game.state.priority.current_player = player
    request = PriorityDecisionRequest(game.state, player)
    prior = PlayerRecall()
    info = DecisionProjector(request, tuple(request.option_space(search_generation_policy(request))), prior).build().information
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    factory = provider.prepare(request, info, prior, player.controller.knowledge)
    sim = factory(10)
    node = sim.start()
    land = next(a for a in node.player_information().actions if a.kind == "LandPlayAction")
    target = sim.advance_action(node, land).player_information()
    # The unsupported native prompt does not donate a runtime continuation.
    # A strict simulator reconstructs exactly the exposed suffix from the anchor.
    nested = SimpleNamespace(state=game.state, player=player)
    conditioned = provider.prepare(nested, target, target.recall, player.controller.knowledge)
    assert conditioned is not None
    sample = conditioned(99)
    assert sample.start().player_information() == target
    assert sample.simulation_backend is not None
    assert sample.initialization_metrics.sampling_attempts == 1
    assert sample.initialization_metrics.prefix_advance_attempts == 1
    provider.reset()
    assert provider.prepare(nested, target, target.recall, player.controller.knowledge) is None
    assert provider.unsupported_reason == "unsupported_root_continuation"


def test_native_scry_continuation_conditions_only_the_players_observed_untouched_prefix():
    from game.ai.game_origin import GameOrigin
    from game.cards.decks import DeckList
    from game.cards.catalog import game_catalog
    from game.game_loop.setup import SetupConfig
    from game.enums import ZoneType, TurnPhase
    deck = DeckList("Scry", (("Island", 6), ("Opt", 2), ("Wall of Runes", 2)))
    origin = GameOrigin((deck, deck), game_catalog, starting_player_idx=0,
        config=SetupConfig(opening_hand_size=2, minimum_deck_size=10))
    game = origin.scenario(42)(controllers())
    state = game.state
    player = state.players[0]
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    state.turn.number = 9
    state.priority.current_player = player
    player.move_card(next(c for c in player.get_cards() if c.name == "Island"), ZoneType.BATTLEFIELD)
    player.move_card(next(c for c in player.get_cards() if c.name == "Opt"), ZoneType.HAND)
    request = PriorityDecisionRequest(state, player)
    prior = PlayerRecall()
    info = DecisionProjector(request, tuple(request.option_space(search_generation_policy(request))), prior).build().information
    provider = CurrentPositionWorlds(origin, policy_for=search_generation_policy)
    factory = provider.prepare(request, info, prior, player.controller.knowledge)
    sim = factory(7)
    node = sim.start()
    cast = next(a for a in node.player_information().actions if a.kind == "AbilityAction" and a.describe()["source"]["name"] == "Opt")
    node = sim.advance_action(node, cast)
    for _ in range(8):
        if node.decision.kind == "ScryRequest":
            break
        node = sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))
    assert node.decision.kind == "ScryRequest"
    target = node.player_information()
    nested = SimpleNamespace(state=state, player=player)
    conditioned = provider.prepare(nested, target, target.recall, player.controller.knowledge)
    for seed in range(5):
        sample = conditioned(seed)
        assert sample.start().player_information() == target
        assert sample.initialization_metrics.sampling_attempts == 1
    snapshot = provider._anchor[4]
    blocked = replace(target, recall=PlayerRecall((*target.recall.entries[:-1],
        PlayerRecall().append("event", {"kind": "library_shuffled", "owner": 0}).entries[0],
        target.recall.entries[-1])))
    assert provider._condition_inspection(snapshot, blocked) is snapshot


def test_native_player_loss_operations_have_visible_ordering_and_preserve_terminal_rules():
    from game.ai.decision_maker import ReplacementOrderRequest
    from game.operations.player_operations import LosePlayerOperation
    origin, game = position()
    player, other = game.state.players
    operations = (LosePlayerOperation(player, "life"), LosePlayerOperation(other, "poison"))
    request = ReplacementOrderRequest(game.state, player, operations)
    info = DecisionProjector(request, tuple(request.options), PlayerRecall()).build().information
    assert len(info.actions) == 2
    for action in info.actions:
        assert {item["reason"] for item in action.describe()["items"]} == {"life", "poison"}
    assert operations[0].lki_cards(game.state) == ()
    events = operations[0].execute(game.state)
    assert player.has_lost and player.loss_reason == "life"
    assert events[0].key == "player_lost" and events[0].payload["reason"] == "life"


@pytest.mark.parametrize("name", ["compress_forced", "require_root_coverage", "force_initial_coverage"])
def test_boolean_search_controls_are_strict(name):
    with pytest.raises(ValueError):
        SearchAgentConfig.parse({"deck_knowledge": "both_lists", name: "false"})


@pytest.mark.parametrize("iterations", ["16", False, None, -1])
def test_minimum_trajectory_validation_rejects_invalid_iteration_types(iterations):
    with pytest.raises(ValueError):
        SearchAgentConfig.parse({"deck_knowledge": "both_lists", "iterations": iterations})


def test_current_provider_honors_configured_history_exploration_and_resets_diagnostics():
    origin, _ = position()
    provider = CurrentPositionWorlds(origin, history_exploration=.4)
    assert provider.history_exploration == .4
    provider.preparation = {"route": "current"}
    provider.reset()
    assert provider.preparation is None
    with pytest.raises(ValueError):
        CurrentPositionWorlds(origin, history_exploration=float("nan"))
