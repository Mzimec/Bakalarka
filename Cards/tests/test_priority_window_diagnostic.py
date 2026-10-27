"""A diagnostic must stop before execution and restore every profiling hook."""
import pytest

from benchmarks.priority_window import WindowAgent, diagnose, render
from game.ai.modular_agent import CandidateGenerator, observation
from game.ai.simple_agent import SimpleAgent
from game.enums import TurnPhase
from game.simulation.match_runner import run_match
from game.game_actions.generation.exec_plan_gen_pipeline import ExecutionPlanPipeline


@pytest.fixture
def captured():
    agent = WindowAgent(9, TurnPhase.PRECOMBAT_MAIN, 1)
    result = run_match(("white", "white"), None, seed=3, starting_player=0,
                       controllers=(agent, SimpleAgent()), collect_decision_stats=False)
    assert result.status == "aborted" and result.error is None
    request = agent.captured
    assert request is not None and request.state.turn.number >= 9
    assert request.state.turn.phase == TurnPhase.PRECOMBAT_MAIN
    assert request.player is request.state.active_player
    assert not request.state.stack.items
    return agent, request


def test_diagnostic_preserves_position_and_counts_actual_lazy_outputs(captured):
    agent, request = captured
    before = observation(request.state, request.player)
    original = ExecutionPlanPipeline.generate
    report = diagnose(request, agent)
    assert ExecutionPlanPipeline.generate is original
    assert observation(request.state, request.player) == before
    assert report["same_retained_candidates"]
    assert report["generated_actions"] == len(report["actions"])
    assert report["retained_candidates"] == sum(row["retained"] for row in report["actions"])
    assert report["generated_actions"] == report["work"]["generation.PriorityDecisionRequest.yields"]
    assert sum(row["actions"] for row in report["abilities"]) == report["generated_actions"] - 1
    assert report["components"]["stage.cost_plans"]["calls"] > 0
    assert report["components"].get("operations.execute_batch") is None
    assert report["actions"][0]["description"]["kind"] == "pass"
    assert "All generated actions" in render(report)


def test_diagnostic_restores_hooks_when_profiled_generation_raises(captured, monkeypatch):
    agent, request = captured
    original_generate = agent.candidates.generate
    original_plan = ExecutionPlanPipeline.generate
    original_score = CandidateGenerator._ability_candidate
    calls = 0

    def failing(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("probe failure")
        return original_generate(*args, **kwargs)

    monkeypatch.setattr(agent.candidates, "generate", failing)
    with pytest.raises(RuntimeError, match="probe failure"):
        diagnose(request, agent)
    assert ExecutionPlanPipeline.generate is original_plan
    assert CandidateGenerator._ability_candidate is original_score
