"""Live controller must match replay information and return live legal objects."""
import pytest

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.decision_maker import PriorityDecisionRequest
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.knowledge import PlayerEvent
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_actions import PlayerAction
from game.ai.search_simulator import SearchSimulator, ReplayDiverged, SimulationBudgetExceeded


def search():
    return InformationSetSearch(opponent_policy=ObservationOpponentPolicy(),
        budget=InformationSearchBudget(worlds=1, iterations=8, max_advances=128, max_depth=1, max_extensions=8))


def agent(provider=lambda info: None, **kwargs):
    return MCTSAgent(world_provider=provider, search=search(), policy_for=tactical_policy, **kwargs)


def request(game):
    # PriorityWindow sets these before calling the controller.
    game.state.priority.current_player = game.state.players[0]
    return PriorityDecisionRequest(game.state, game.state.players[0])


def test_live_root_matches_simulator_and_search_returns_live_card_without_playing_it():
    recipe = tactical_scenario("lethal_shock")
    expected = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True).start().player_information()
    inputs = []
    def provider(info):
        inputs.append(info)
        assert info == expected
        return lambda seed: SearchSimulator(recipe, policy_for=tactical_policy, track_information=True)
    own = agent(provider)
    game = recipe((own, agent()))
    req = request(game)
    rng = game.state.rng.getstate()
    hand = tuple(game.state.players[0].hand.values())
    result = own.decide(req)
    assert result.info["mcts"]["reason"] == "searched"
    assert result.value.source is hand[0]
    assert tuple(game.state.players[0].hand.values()) == hand
    assert not any(c.state.tapped for c in game.state.players[0].battlefield.values())
    assert game.state.rng.getstate() == rng
    assert inputs == [own.last_information]
    assert own.recall.entries[-1].kind == "action"


def test_incompatible_world_uses_visible_fallback_and_retains_reason():
    wrong = tactical_scenario("answered_shock")
    own = agent(lambda info: lambda seed: SearchSimulator(wrong, policy_for=tactical_policy, track_information=True))
    game = tactical_scenario("lethal_shock")((own, agent()))
    result = own.decide(request(game))
    assert result.info["mcts"]["reason"] == "sampled_root_mismatch"
    assert result.info["mcts"]["used_search"]
    assert result.info["mcts"]["used_fallback"]
    assert own.last_search is None
    assert result.value.source in game.state.players[0].hand.values()


def test_real_replay_failure_is_not_hidden_by_fallback():
    def broken(seed):
        raise ReplayDiverged("broken rule replay")
    own = agent(lambda info: broken)
    game = tactical_scenario("lethal_shock")((own, agent()))
    with pytest.raises(ReplayDiverged, match="broken rule"):
        own.decide(request(game))
    assert not own.recall.entries  # No returned decision was committed.


def test_unoffered_policy_action_is_rejected_without_recording_a_choice():
    own = agent(fallback=lambda info: PlayerAction("forged", "{}"))
    game = tactical_scenario("lethal_shock")((own, agent()))
    with pytest.raises(ValueError, match="not offered"):
        own.decide(request(game))
    assert not own.recall.entries


def test_menu_cap_is_not_silent_heuristic_pruning():
    own = agent(max_options=1)
    game = tactical_scenario("lethal_shock")((own, agent()))
    with pytest.raises(SimulationBudgetExceeded, match="max_options=1"):
        own.decide(request(game))
    assert not own.recall.entries


def test_incomplete_search_uses_observation_policy_with_explicit_metrics():
    own = agent(lambda info: lambda seed: SearchSimulator(tactical_scenario("lethal_shock"),
                                                         policy_for=tactical_policy, track_information=True))
    own.search = InformationSetSearch(opponent_policy=ObservationOpponentPolicy(),
        budget=InformationSearchBudget(worlds=1, iterations=1, max_advances=1))
    game = tactical_scenario("lethal_shock")((own, agent()))
    result = own.decide(request(game))
    assert result.info["mcts"]["used_search"]
    assert result.info["mcts"]["used_fallback"]
    assert own.last_search.used_fallback
    assert result.value.source in game.state.players[0].hand.values()


def test_sampled_scenario_evidence_has_identical_live_and_replay_projection():
    from benchmarks.search_hidden_worlds import public_position, catalog, beliefs
    from game.ai.sampled_scenario import SampledScenario
    recipe = SampledScenario(public_position, catalog, beliefs(), observer=0, seed=9)
    expected = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True).start().player_information()
    own = agent()
    game = recipe((own, agent()))
    own.decide(request(game))
    assert own.last_information == expected
    assert "initial_search_evidence" in own.last_information.current.observation()


def test_starting_player_setup_is_traced_without_calling_world_provider():
    from game.game_state import Player
    from game.game_actions.generation.decision_abstraction.auxiliary import StartingPlayerRequest
    def forbidden(info):
        raise AssertionError("Setup has no in-game world")
    own = agent(forbidden)
    players = (Player([], own, idx=0), Player([], agent(), idx=1))
    result = own.decide(StartingPlayerRequest(state=None, player=players[0], candidates=players))
    assert result.info["mcts"]["reason"] == "setup"
    assert len(own.recall.entries) == 2


def test_controller_reuse_resets_recall_knowledge_and_seed_stream():
    own = agent()
    recipe = tactical_scenario("lethal_shock")
    first = recipe((own, agent()))
    own.decide(request(first))
    old_input = own.last_information
    own.receive_player_event(PlayerEvent("card_drawn", 5, "PRECOMBAT_MAIN", owner=1),
                             game_token=first.state.knowledge_token, player=0)
    assert len(own.recall.entries) == 3
    second = recipe((own, agent()))
    own.decide(request(second))
    assert own.last_information == old_input
    assert len(own.recall.entries) == 2
    assert not own.knowledge.events


def test_projected_events_are_copied_and_raw_payloads_rejected():
    own = agent()
    token = object()
    event = PlayerEvent("card_drawn", 5, "PRECOMBAT_MAIN", owner=1)
    own.receive_player_event(event, game_token=token, player=0)
    assert own.recall.entries[0].describe()["card_id"] is None
    with pytest.raises(TypeError, match="projected"):
        own.receive_player_event({"secret": "Counterspell"}, game_token=token, player=0)
    assert len(own.recall.entries) == 1


def test_hidden_hand_changes_do_not_change_live_provider_input():
    from game.game_state import Card
    from game.enums import ZoneType
    from benchmarks.search_hidden_worlds import catalog
    inputs = []
    def provider(info):
        inputs.append(info)
        return None
    for name in ("Counterspell", "Plains"):
        own = agent(provider)
        game = tactical_scenario("lethal_shock")((own, agent()))
        opponent = game.state.players[1]
        opponent.add_card(Card(catalog()[name], opponent), ZoneType.HAND)
        own.decide(request(game))
    assert inputs[0] == inputs[1]
    assert "Counterspell" not in str(inputs[0])


def test_all_live_decision_observations_events_and_choices_match_replay():
    """Run the actual loop, checking each request before allowing it to execute."""
    recipe = tactical_scenario("lethal_shock")
    sim = SearchSimulator(recipe, policy_for=tactical_policy, track_information=True)
    expected = sim.start()
    checked = []
    policy = ObservationOpponentPolicy()
    def checked_choice(info):
        nonlocal expected
        assert info == expected.player_information()
        chosen = policy(info)
        checked.append((info.current.player, info.current.request_kind))
        expected = sim.advance_action(expected, chosen)
        return chosen
    controllers = (agent(fallback=checked_choice), agent(fallback=checked_choice))
    game = recipe(controllers)
    game.loop.step(game.state)
    assert game.state.is_game_over and expected.terminal
    assert game.state.players[1].has_lost
    assert len(checked) >= 3
    assert any(p == 1 for p, _ in checked)
    for p in (0, 1):
        assert controllers[p].recall == expected.recall(p)
