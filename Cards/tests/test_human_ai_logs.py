"""Console/AI integration and human-readable report regression tests."""

import json
from pathlib import Path
import subprocess
import sys

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
    source = (
        Path(__file__).parents[1]
        / "artifacts/test-results/ai-five-verified/black-green-seed2026-start0.jsonl"
    )
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


def test_play_ai_entry_point(tmp_path):
    run = subprocess.run(
        cwd=Path(__file__).resolve().parents[1],
        args=[sys.executable, "-B", "-m", "game.bin.play_ai", "--output", str(tmp_path / "cli.jsonl")],
        input="n\nconcede\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "winner: AI" in run.stdout
