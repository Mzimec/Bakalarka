"""Console/AI integration and human-readable report regression tests."""

import json

import pytest

from game.simulation.match_runner import run_match
from game.reporting.readable_log import describe, render_match
from game.ai.simple_agent import SimpleAgent
from game.console.demo_game import ConsoleDecisionMaker


def test_event_payload_keeps_nested_player_and_amount():
    assert describe({"player": {"player": "AI"}, "amount": 3}) == "player: AI; amount: 3"


def test_console_can_concede_against_ai(tmp_path):
    answers = iter(["n", "concede"])
    console = ConsoleDecisionMaker(read=lambda _: next(answers), write=lambda _: None)
    result = run_match(
        ("white", "red"),
        tmp_path / "human.jsonl",
        controllers=(console, SimpleAgent()),
        names=("You", "AI"),
    )
    assert result.status == "win" and result.winner == "AI"
    assert result.elapsed_seconds > 0
    report = (tmp_path / "human.txt").read_text(encoding="utf-8")
    assert "Keep the Peace (white)" in report and "Winner: AI" in report


def test_console_quit_preserves_report(tmp_path):
    def stop(_):
        raise EOFError

    result = run_match(
        ("blue", "green"),
        tmp_path / "quit.jsonl",
        controllers=(ConsoleDecisionMaker(read=stop), SimpleAgent()),
    )
    assert result.status == "aborted"
    assert (tmp_path / "quit.txt").exists()


def test_historical_report_has_actions_and_unknown_time(tmp_path):
    # Minimal historical schema, independent of untracked experiment artifacts.
    source = tmp_path / "historical.jsonl"
    rows = [
        {"kind": "match", "colors": ["black", "green"], "seed": 2026, "starting_player": 0},
        {"kind": "event", "event": "spell_cast", "controller": "black-0", "source": "Murder"},
        {"kind": "event", "event": "attacker_declared", "controller": "green-1", "source": "Baloth"},
        {"kind": "event", "event": "blocker_declared", "controller": "black-0", "source": "Skeleton"},
        {"kind": "decision", "player": "black-0", "decision": "trigger", "details": {}},
        {"kind": "result", "status": "win", "winner": "green-1", "turns": 12},
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    output = tmp_path / "history.txt"
    render_match(source, output)
    report = output.read_text(encoding="utf-8")
    for expected in (
        "Cold-Blooded Killers",
        "unavailable",
        "casts Murder",
        "attacks with",
        "blocks with",
        "chooses trigger",
        "Winner: green-1",
    ):
        assert expected in report
    with pytest.raises(FileExistsError):
        render_match(source, output)


def test_new_log_records_resolution_and_trigger(tmp_path):
    result = run_match(("black", "green"), tmp_path / "new.jsonl", seed=2026, max_turns=8)
    assert result.status == "turn_limit"
    rows = [json.loads(line) for line in (tmp_path / "new.jsonl").read_text().splitlines()]
    assert any(row["kind"] == "resolution" for row in rows)
    assert any(row["kind"] == "trigger_detected" for row in rows)
    assert "Elapsed wall time: unavailable" not in (tmp_path / "new.txt").read_text()
