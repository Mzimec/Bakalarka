"""Production wiring, explicit deck assumptions, coverage and hard limits."""
import json
import sys

import pytest

from game.ai.game_origin import GameOrigin
from game.ai.simple_agent import DecisionLimitReached
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter
from game.simulation.config import make_controller, run_config
from test_mcts_agent import agent, request
from benchmarks.search_tactics import tactical_scenario


def explicit_config(**overrides):
    return {"type": "mcts", "mcts": {"deck_knowledge": "both_lists", "worlds": 1,
        "population_size": 1, "iterations": 4, "max_depth": 1, "max_advances": 16,
        "max_attempts": 4, **overrides}}


@pytest.mark.parametrize("config", [
    {"type": "mcts"}, {"type": "mcts", "mcts": {"deck_knowledge": "infer"}},
    {"type": "simple", "mcts": {}}, {**explicit_config(), "auto_pass": True},
    explicit_config(max_attempts=0), explicit_config(worlds=True), explicit_config(history_exploration=float("nan")),
    explicit_config(typo=1), explicit_config(condition_draws="false"), explicit_config(condition_opening_reveal="false"),
    explicit_config(world_backend="unknown"),
    explicit_config(checkpoint_limit=-1), explicit_config(checkpoint_limit=True),
    explicit_config(world_backend="history", checkpoint_limit=8),
])
def test_invalid_or_implicit_search_settings_rejected(config):
    with pytest.raises((ValueError, TypeError)):
        make_controller(config, seed=1, max_decisions=100)


def test_explicit_deck_context_required_and_match_seed_not_used_for_search():
    with pytest.raises(ValueError, match="GameOrigin"):
        make_controller(explicit_config(), seed=42, max_decisions=100)
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")), game_catalog)
    a = make_controller(explicit_config(), seed=42, max_decisions=100, search_origin=origin)
    b = make_controller(explicit_config(), seed=99, max_decisions=100, search_origin=origin)
    assert a.seed == b.seed == 7001
    assert a.world_provider is not b.world_provider
    assert a.max_decisions == 100 and not a.search_setup


def test_full_history_backend_remains_an_explicit_reference():
    from game.ai.incremental_world_provider import IncrementalWorldProvider
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")), game_catalog)
    own = make_controller(explicit_config(world_backend="history"), seed=42, max_decisions=100, search_origin=origin)
    assert isinstance(own.world_provider, IncrementalWorldProvider)


def test_live_budget_counts_forced_decisions_and_resets_between_games():
    own = agent(max_decisions=1)
    game = tactical_scenario("lethal_shock")((own, agent()))
    own.decide(request(game))
    assert own.decisions == 1
    assert own.search_summary()["decisions"] == 1
    with pytest.raises(DecisionLimitReached):
        own.decide(request(game))
    another = tactical_scenario("lethal_shock")((own, agent()))
    own.decide(request(another))
    assert own.decisions == own.search_summary()["decisions"] == 1


def test_emergency_menu_fallback_never_searches_a_truncated_menu_and_resets():
    calls = []
    own = agent(provider=lambda info: calls.append(info), max_options=1, allow_incomplete_menu_fallback=True)
    game = tactical_scenario("lethal_shock")((own, agent()))
    result = own.decide(request(game))
    assert result.info["mcts"]["reason"] == "option_budget"
    assert not result.info["mcts"]["menu_complete"] and not calls
    own.max_options = 4096
    result = own.decide(request(game))
    assert result.info["mcts"]["reason"] == "incomplete_menu_history" and not calls
    other = tactical_scenario("lethal_shock")((own, agent()))
    own.decide(request(other))
    assert len(calls) == 1 and own._search_disabled_reason is None


@pytest.mark.parametrize("collect", [True, False])
def test_production_config_logs_search_and_summary_even_without_decision_measurements(tmp_path, collect):
    cfg = {"seed": 43, "max_turns": 1, "collect_decision_stats": collect,
           "players": [{"name": "Search", "deck": "red", "agent": explicit_config()},
                       {"name": "Baseline", "deck": "white", "agent": {"type": "simple"}}]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    result = run_config(path, output=tmp_path / "match.jsonl")
    assert result.status == "turn_limit", result.error
    summary = result.search_diagnostics[0]
    assert summary["search_attempts"] > 0 and summary["searched_decisions"] > 0
    assert summary["decisions"] == summary["searched_decisions"] + summary["forced_decisions"] + summary["fallback_decisions"]
    assert summary["decision_ms_max"] >= summary["decision_ms_p95"] >= 0
    assert bool(result.decision_timing) == collect
    rows = [json.loads(line) for line in (tmp_path / "match.jsonl").read_text(encoding="utf-8").splitlines()]
    decisions = [row for row in rows if row["kind"] == "search_decision"]
    assert all("turn" in row and "phase" in row for row in decisions)
    assert any(row["phase"] == "PRECOMBAT_MAIN" for row in decisions)
    assert len(decisions) == summary["decisions"]
    assert sum(row["reason"] == "searched" for row in decisions) == summary["searched_decisions"]
    assert rows[-1]["search_diagnostics"] == result.search_diagnostics
    assert result.decisions >= summary["decisions"]


def test_match_reports_mcts_decision_limit_instead_of_an_engine_error(tmp_path):
    cfg = {"seed": 43, "max_turns": 3, "max_decisions": 1,
           "players": [{"name": "Search", "deck": "red", "agent": explicit_config()},
                       {"name": "Baseline", "deck": "white", "agent": {"type": "simple"}}]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    result = run_config(path, output=tmp_path / "match.jsonl")
    assert result.status == "decision_limit"
    assert result.search_diagnostics[0]["decisions"] == 1


def test_play_requires_explicit_deck_knowledge_before_interactive_io(monkeypatch):
    from game.bin import play
    monkeypatch.setattr(sys, "argv", ["play", "--ai-agent", "mcts"])
    with pytest.raises(SystemExit) as exc:
        play.main()
    assert exc.value.code == 2


def test_play_wires_correct_names_starting_player_and_telemetry(monkeypatch):
    from game.bin import play
    from game.simulation.match_runner import MatchResult
    captured = {}
    def run(colors, output, **kwargs):
        captured.update(kwargs)
        return MatchResult(colors, 42, 1, "aborted", None, None, 0, 0, None)
    monkeypatch.setattr(play, "run_match", run)
    monkeypatch.setattr(sys, "argv", ["play", "--ai-agent", "mcts", "--mcts-known-decks", "--starting-player", "ai"])
    assert play.main() == 0
    own = captured["controllers"][1]
    origin = own.world_provider.origin
    assert origin.names == captured["names"] == ("Human", "MCTS")
    assert origin.starting_player_idx == captured["starting_player"] == 1
    assert callable(own.on_search_decision)
    assert captured["metadata"]["players"][1]["controller"]["mcts"]["deck_knowledge"] == "both_lists"
