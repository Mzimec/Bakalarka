"""The benchmark controller stays lazy, legal and independent of scoring."""
import json

import pytest

from game.ai.simple_agent import DecisionLimitReached, SimpleAgent
from game.ai.trivial_agent import TrivialAgent
from game.cards.starter_cards import starter_catalog
from game.enums import TurnPhase, ZoneType
from game.game_actions.data_structs.game_action import PassPriorityAction
from game.game_actions.generation.decision_abstraction.requests import (
    DeclareAttackersRequest, DeclareBlockersRequest, MulliganRequest, PriorityDecisionRequest,
)
from game.game_actions.generation.decision_abstraction.options import MulliganOption
from game.game_actions.generation.decision_abstraction.option_space import DecisionOptionSpace
from game.game_state import Card, Player, State
from game.simulation.config import make_controller
from game.simulation.match_runner import run_match


def board(agent=None):
    state = State([Player([], agent or TrivialAgent(), idx=i) for i in range(2)])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return state, state.active_player


def add(state, player, name, zone=ZoneType.BATTLEFIELD):
    card = Card(starter_catalog()[name], player)
    player.add_card(card, zone)
    return card


def test_generic_request_does_not_look_ahead(monkeypatch):
    state, player = board()

    def choices(self):
        yield MulliganOption(False)
        pytest.fail("The trivial policy must stop at its first candidate")

    monkeypatch.setattr(DecisionOptionSpace, "__iter__", choices)
    result = TrivialAgent().decide(MulliganRequest(state, player))
    assert not result.value.take_mulligan


def test_priority_consumes_only_pass_and_first_action_and_closes_stream(monkeypatch):
    from game.rules.lands import LandPlayAction
    state, player = board()
    land = add(state, player, "Island", ZoneType.HAND)
    closed = []

    def choices(self):
        try:
            yield PassPriorityAction(player)
            yield LandPlayAction(land, player)
            pytest.fail("A second playable action was requested")
        finally:
            closed.append(True)

    monkeypatch.setattr(DecisionOptionSpace, "__iter__", choices)
    result = TrivialAgent(auto_pass=False).decide(PriorityDecisionRequest(state, player))
    assert result.value.card is land and closed == [True]


def test_real_generation_uses_solver_without_scoring_or_observation(monkeypatch):
    state, player = board()
    for _ in range(3):
        add(state, player, "Island")
    spell = add(state, player, "Cloudkin Seer", ZoneType.HAND)
    monkeypatch.setattr(SimpleAgent, "score", lambda *args: pytest.fail("Scoring invoked"))
    monkeypatch.setattr("game.ai.modular_agent.observation", lambda *args: pytest.fail("Observation built"))
    result = TrivialAgent().decide(PriorityDecisionRequest(state, player))
    assert result.value.source is spell
    assert result.value.validation_error(state) is None
    assert result.info["decision_metrics"]["options_observed"] == 2
    assert state.stack.is_empty() and not any(c.is_tapped for c in player.battlefield.values())


def test_quiet_autopass_and_disabled_autopass_have_same_choice():
    state, player = board()
    state.turn.phase = TurnPhase.UPKEEP
    for enabled in (True, False):
        agent = TrivialAgent(auto_pass=enabled)
        result = agent.decide(PriorityDecisionRequest(state, player))
        assert isinstance(result.value, PassPriorityAction)
        metrics = result.info["decision_metrics"]
        assert metrics["auto_pass"] is enabled
        assert metrics["options_observed"] == (0 if enabled else 1)


def test_repeatable_priority_ability_is_not_selected_again_in_same_window():
    from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
    from game.game_state import CardDefinition
    from game.enums import CardType
    state, player = board()
    card = Card(CardDefinition("Free ability", types=frozenset({CardType.ARTIFACT}),
                              abilities=frozenset({ActivatedAbilityDefinition()})), player)
    player.add_card(card, ZoneType.BATTLEFIELD)
    agent = TrivialAgent()
    first = agent.decide(PriorityDecisionRequest(state, player)).value
    assert first.source is card
    assert isinstance(agent.decide(PriorityDecisionRequest(state, player)).value, PassPriorityAction)
    state.turn.phase = TurnPhase.POSTCOMBAT_MAIN
    assert agent.decide(PriorityDecisionRequest(state, player)).value.source is card


def test_attack_proposal_uses_pipeline_and_blocks_take_first_option():
    state, player = board()
    creature = add(state, player, "Cloudkin Seer")
    creature.controlled_since = 0
    player.last_turn_started = 1
    state.turn.number = 2
    state.turn.phase = TurnPhase.BEGIN_COMBAT
    state.combat.begin()
    state.turn.phase = TurnPhase.DECLARE_ATTACKERS
    request = DeclareAttackersRequest(state, player)
    option = TrivialAgent().decide(request).value
    assert creature in option.declarations
    state.combat.validate_attackers(player, option.declarations)
    state.combat.declare_attackers(player, option.declarations)
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    opponent = state.players[1]
    result = TrivialAgent().decide(DeclareBlockersRequest(state, opponent))
    assert not result.value.declarations


def test_agent_budget_and_configuration():
    agent = make_controller({"type": "trivial", "auto_pass": False}, seed=0, max_decisions=1)
    assert isinstance(agent, TrivialAgent) and not agent.auto_pass
    state, player = board(agent)
    agent.decide(MulliganRequest(state, player))
    with pytest.raises(DecisionLimitReached):
        agent.decide(MulliganRequest(state, player))
    with pytest.raises(ValueError, match="Auxiliary policies"):
        make_controller({"type": "trivial", "candidates": {}}, seed=0, max_decisions=10)


def test_trivial_match_logging_and_diagnostics_preserve_outcome(tmp_path):
    results = []
    for stats in (True, False):
        agent = TrivialAgent()
        previous_log = agent.log
        path = tmp_path / f"trivial-{stats}.jsonl"
        result = run_match(("white", "white"), path, seed=1, max_turns=100,
                           controllers=(agent, TrivialAgent()), collect_decision_stats=stats)
        assert result.status == "win", result.error
        assert agent.log is previous_log
        assert bool(result.decision_timing) is stats
        rows = [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()]
        assert any(row["kind"] == "decision" and row["decision"] == "trivial_choice" for row in rows)
        results.append((result.status, result.winner_index, result.turns, result.decisions))
    assert results[0] == results[1]
