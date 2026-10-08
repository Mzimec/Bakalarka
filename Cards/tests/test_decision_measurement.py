"""Latency instrumentation must preserve lazy generation and decision semantics."""
import json

import pytest

from game.ai.decision_maker import ModularDecisionMaker, DecisionResult
from game.ai.simple_agent import SimpleAgent
from game.enums import CardType, TurnPhase, ZoneType
from game.game_state import State, Player, Card, CardDefinition
from game.game_actions.data_structs.ability import ActivatedAbilityDefinition
from game.game_actions.generation.decision_abstraction.requests import MulliganRequest, PriorityDecisionRequest
from game.game_actions.generation.decision_abstraction.options import MulliganOption
from game.game_actions.generation.decision_abstraction.policies import MulliganPolicy
from game.game_actions.generation.pruning.pruning_strategy import LimitPruning
from game.game_actions.generation.decision_abstraction.measurement import observe_decisions
from game.reporting.decision_statistics import DecisionStatistics
from game.simulation.match_runner import run_match


@pytest.fixture
def state():
    state = State([Player([], ModularDecisionMaker(), idx=i) for i in range(2)])
    state.turn.phase = TurnPhase.PRECOMBAT_MAIN
    return state


def test_partial_iteration_is_not_exhausted_to_measure_size(state, monkeypatch):
    from game.game_actions.generation.decision_abstraction import measurement
    ticks = iter((100, 300))
    monkeypatch.setattr(measurement, "perf_counter_ns", lambda: next(ticks))

    class Lazy:
        def generate(self, request):
            yield MulliganOption(False)
            pytest.fail("Measurement must not ask for another option")

    class First(ModularDecisionMaker):
        def decide_mulligan(self, request):
            return DecisionResult(next(iter(request.option_space(MulliganPolicy(strategy=Lazy())))))

    result = First().decide(MulliganRequest(state, state.active_player))
    metrics = result.info["decision_metrics"]
    assert metrics["elapsed_ns"] == result.info["elapsed_time"] == 200
    assert metrics["options_observed"] == 1 and metrics["option_space_size"] is None
    assert metrics["option_spaces"][0]["exhausted"] is False


def test_nested_generation_is_not_counted_as_extra_priority_options(state):
    player = state.active_player
    card = Card(CardDefinition("Source", types=frozenset({CardType.ARTIFACT}),
                               abilities=frozenset({ActivatedAbilityDefinition()})), player)
    player.add_card(card, ZoneType.BATTLEFIELD)

    class All(ModularDecisionMaker):
        def decide_priority(self, request):
            return DecisionResult(list(request.options)[0])

    metrics = All().decide(PriorityDecisionRequest(state, player)).info["decision_metrics"]
    assert metrics["options_observed"] == metrics["option_space_size"] == 3
    assert len(metrics["option_spaces"]) == 1
    assert not card.is_tapped and state.stack.is_empty()


@pytest.mark.parametrize("repeats,limit,count,size", [(1, None, 2, 2), (2, None, 4, None), (1, 1, 1, 1)])
def test_reiteration_and_pruning_are_reported_without_claiming_unique_global_size(state, repeats, limit, count, size):
    class Enumerate(ModularDecisionMaker):
        def decide_mulligan(self, request):
            space = request.option_space(MulliganPolicy(pruning=LimitPruning(limit) if limit else None))
            for _ in range(repeats):
                options = list(space)
            return DecisionResult(options[0])

    metrics = Enumerate().decide(MulliganRequest(state, state.active_player)).info["decision_metrics"]
    assert metrics["options_observed"] == count
    assert metrics["option_space_size"] == size
    assert len(metrics["option_spaces"]) == repeats


def test_failure_is_timed_and_observer_scope_is_restored(state):
    rows = []

    class Fail(ModularDecisionMaker):
        def decide_mulligan(self, request):
            raise EOFError

    request = MulliganRequest(state, state.active_player)
    with observe_decisions(lambda agent, request, metrics: rows.append(metrics)):
        with pytest.raises(EOFError):
            Fail().decide(request)
    ModularDecisionMaker().decide(request)
    assert len(rows) == 1
    assert rows[0]["error_type"] == "EOFError" and rows[0]["status"] == "error"
    assert rows[0]["elapsed_ns"] >= 0 and rows[0]["option_space_size"] is None


def test_merging_match_statistics_weights_individual_decisions():
    first, second, merged = DecisionStatistics(), DecisionStatistics(), DecisionStatistics()
    def record(stats, elapsed, size):
        stats.record(0, "A", "Agent", "agent", {
            "status": "success", "elapsed_ns": elapsed, "options_observed": 2,
            "option_space_size": size, "request_type": "PriorityDecisionRequest",
        })
    record(first, 1_000_000, 2)
    record(second, 9_000_000, None)
    record(second, 9_000_000, None)
    merged.merge(first.summary())
    merged.merge(second.summary())
    summary = merged.summary()[0]
    assert summary["overall"]["average_elapsed_ms"] == pytest.approx(19 / 3)
    assert summary["overall"]["known_size_count"] == 1
    assert summary["overall"]["average_known_size"] == 2
    assert summary["by_options_observed"]["PriorityDecisionRequest"]["2-10"]["count"] == 3


def test_match_records_each_players_timing_and_works_without_log_files(tmp_path):
    agent = SimpleAgent()
    previous_log = agent.log
    result = run_match(("white", "red"), tmp_path / "timed.jsonl", seed=1, max_turns=1,
                       controllers=(agent, SimpleAgent()))
    assert result.status == "turn_limit", result.error
    assert agent.log is previous_log
    rows = [json.loads(line) for line in (tmp_path / "timed.jsonl").read_text().splitlines()]
    timings = [row for row in rows if row["kind"] == "decision_timing"]
    assert sum(entry["overall"]["count"] for entry in result.decision_timing) == len(timings)
    assert {entry["player_index"] for entry in result.decision_timing} == {0, 1}
    assert any(row["request_type"] == "MulliganRequest" for row in timings)
    assert any(row["request_type"] == "PriorityDecisionRequest" for row in timings)
    assert rows[-1]["decision_timing"] == result.decision_timing
    report = (tmp_path / "timed.txt").read_text()
    assert "Decision timing" in report and "observed options" in report
    silent = run_match(("white", "red"), None, seed=1, max_turns=1)
    assert silent.status == "turn_limit" and silent.decision_timing


def test_aborted_human_decision_is_logged_separately_from_agent_time(tmp_path):
    from game.console.demo_game import ConsoleDecisionMaker
    def abort(prompt):
        raise EOFError
    result = run_match(("white", "red"), tmp_path / "abort.jsonl",
                       controllers=(ConsoleDecisionMaker(read=abort, write=lambda _: None), SimpleAgent()))
    assert result.status == "aborted"
    timing = result.decision_timing[0]
    assert timing["mode"] == "human" and timing["overall"]["errors"] == 1


def test_disabled_diagnostics_skip_measurement_and_observer_but_validate_results(state, monkeypatch):
    import game.ai.decision_maker as decisions
    request = MulliganRequest(state, state.active_player)
    rows = []
    with observe_decisions(lambda *args: rows.append(args)):
        with observe_decisions(None, enabled=False), monkeypatch.context() as patch:
            patch.setattr(decisions, "DecisionMeasurement", lambda *args: pytest.fail("Measurement allocated"))
            assert ModularDecisionMaker().decide(request).info == {}

            class Wrong(ModularDecisionMaker):
                def decide_mulligan(self, request):
                    from game.game_actions.data_structs.game_action import PassPriorityAction
                    return DecisionResult(PassPriorityAction(request.player))

            with pytest.raises(TypeError, match="MulliganOption"):
                Wrong().decide(request)
        assert rows == []
        assert "decision_metrics" in ModularDecisionMaker().decide(request).info
    assert len(rows) == 1


def test_disabled_scope_isolated_from_active_measurement_and_restored_after_failure(state):
    from game.game_actions.generation.decision_abstraction.measurement import DecisionMeasurement
    request = MulliganRequest(state, state.active_player)
    with DecisionMeasurement(request) as measurement:
        with pytest.raises(RuntimeError), observe_decisions(None, enabled=False):
            assert len(list(request.options)) == 2
            raise RuntimeError("nested failure")
        assert measurement.streams == []
        assert len(list(request.options)) == 2
    assert measurement.metrics["options_observed"] == 2


def test_match_without_diagnostics_preserves_events_and_decision_budget(tmp_path, monkeypatch):
    import game.simulation.match_runner as runner
    from game.ai.modular_agent import ModularAgent
    games, events = [], []
    for enabled in (True, False):
        path = tmp_path / f"stats-{enabled}.jsonl"
        if not enabled:
            monkeypatch.setattr(runner, "DecisionStatistics", lambda: pytest.fail("Statistics allocated"))
        agent = ModularAgent(max_decisions=30)
        previous_log = agent.log
        result = run_match(("white", "white"), path, seed=123, max_turns=6,
                           controllers=(agent, SimpleAgent(max_decisions=30)),
                           collect_decision_stats=enabled)
        assert result.status == "decision_limit", result.error
        assert agent.log is previous_log
        assert bool(result.decision_timing) is enabled
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert any(row["kind"] == "decision_timing" for row in rows) is enabled
        events.append([{key: value for key, value in row.items() if key != "seq"}
                       for row in rows if row["kind"] not in {"decision_timing", "result"}])
        games.append((result.status, result.turns, result.decisions, result.winner_index))
    assert games[0] == games[1]
    assert events[0] == events[1]


def test_match_rejects_non_boolean_diagnostics():
    with pytest.raises(ValueError, match="boolean"):
        run_match(("white", "white"), None, collect_decision_stats="false")
