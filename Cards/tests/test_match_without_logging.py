"""No-file matches use the same rules without constructing reporting wrappers."""
import pytest

from game.simulation import match_runner as runner


def test_no_output_constructs_no_logging_wrappers_or_log_records(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Logging work performed without an output destination")
    for name in ("LoggedEventBus", "LoggedResolutionEngine", "LoggedProcessor"):
        monkeypatch.setattr(runner, name, unexpected)
    monkeypatch.setattr(runner.NullMatchLog, "write", unexpected)
    monkeypatch.setattr(runner, "asdict", unexpected)
    result = runner.run_match(("white", "red"), None, seed=12, max_turns=2)
    assert result.status == "turn_limit", result.error
    assert result.decision_timing


def test_no_output_preserves_events_and_decisions(tmp_path, monkeypatch):
    recorded, runs = [], []
    emit = runner.EventBus.emit
    record = runner.SimpleAgent._record

    def captured_emit(self, event, state=None):
        result = emit(self, event, state)
        recorded.append(runner.public_value(dict(kind="event", event=result.key,
                         source=result.source, controller=result.controller, payload=result.payload)))
        return result

    def captured_decision(self, state, player, kind, **details):
        recorded.append(runner.public_value(dict(kind=kind, player=player, details=details)))
        return record(self, state, player, kind, **details)

    monkeypatch.setattr(runner.EventBus, "emit", captured_emit)
    monkeypatch.setattr(runner.SimpleAgent, "_record", captured_decision)
    for output in (tmp_path / "logged.jsonl", None):
        recorded.clear()
        result = runner.run_match(("blue", "green"), output, seed=12, max_turns=8,
                                  collect_decision_stats=False)
        assert result.status == "turn_limit", result.error
        runs.append((result.status, result.turns, result.decisions, result.winner_index, list(recorded)))
    assert runs[0] == runs[1]
