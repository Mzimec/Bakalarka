import json
import subprocess
import pytest
from types import SimpleNamespace

from benchmarks import environment
from benchmarks import component_benchmark


def test_source_cache_switch_restores_methods_after_failure():
    from benchmarks.cache_controls import source_cache_mode
    from game.mana.source_cache import StaticManaSourceCache
    original = (StaticManaSourceCache.lookup, StaticManaSourceCache.remember, StaticManaSourceCache.supported)
    with pytest.raises(RuntimeError, match="benchmark failed"):
        with source_cache_mode(False):
            assert StaticManaSourceCache().lookup(None, None) is None
            assert StaticManaSourceCache.supported(None) is False
            raise RuntimeError("benchmark failed")
    assert (StaticManaSourceCache.lookup, StaticManaSourceCache.remember, StaticManaSourceCache.supported) == original


def test_source_fingerprint_tracks_local_sources_not_results(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    source = game / "card.py"
    source.write_text("value = 1\n", encoding="utf-8")
    before = environment.source_fingerprint(tmp_path)
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    (results / "run.json").write_text('{"time": 12}')
    assert environment.source_fingerprint(tmp_path) == before
    source.write_text("value = 2\n", encoding="utf-8")
    assert environment.source_fingerprint(tmp_path)["sha256"] != before["sha256"]
    (game / "untracked.py").write_text("pass\n")
    assert environment.source_fingerprint(tmp_path)["files"] == 2


def test_environment_without_git_preserves_runtime_and_source_metadata(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)
    result = environment.collect_environment(tmp_path)
    assert result["code"]["git"]["available"] is False
    assert result["python"]["executable"]
    assert result["python"]["version"]
    assert result["code"]["sources"]["sha256"]
    json.dumps(result)


def test_benchmark_persists_metadata_and_cpu_times_in_unique_reports(tmp_path, monkeypatch):
    from game.mana.source_cache import StaticManaSourceCache
    original_lookup = StaticManaSourceCache.lookup
    monkeypatch.setattr(component_benchmark, "__file__", str(tmp_path / "benchmarks" / "component_benchmark.py"))
    monkeypatch.setattr(component_benchmark, "configure_profiler", lambda profiler: None)
    monkeypatch.setattr(environment, "git_metadata", lambda root: {"available": False})
    monkeypatch.setattr(component_benchmark, "run_match", lambda *args, **kwargs: SimpleNamespace(
        elapsed_seconds=0.1, status="win", turns=2, decisions=3, winner_index=0))
    results = tmp_path / "benchmarks" / "results"
    results.mkdir(parents=True)
    legacy = results / "component_benchmark.json"
    legacy.write_text("legacy")
    component_benchmark.main([])
    component_benchmark.main(["--no-decision-stats", "--no-source-cache"])
    assert StaticManaSourceCache.lookup is original_lookup
    paths = list(results.glob("component_benchmark-*.json"))
    assert len(paths) == 2
    assert legacy.read_text() == "legacy"
    for path in paths:
        report = json.loads(path.read_text())
        assert report["schema_version"] == 2
        assert report["environment"]["python"]["executable"]
        assert report["cpu_seconds"] >= 0
        assert report["elapsed_seconds"] > 0
        assert report["cpu_to_wall_ratio"] >= 0
        assert report["games"] == len(report["matches"]) == 20
        assert report["configuration"]["collect_decision_stats"] == report["environment"]["instrumentation"]["decision_stats_enabled"]
        assert report["configuration"]["persistent_mana_source_cache"] == report["environment"]["instrumentation"]["persistent_mana_source_cache_enabled"]
        assert report["total_turns"] == 40
        assert report["total_decisions"] == 60
        for match in report["matches"]:
            assert match["cpu_seconds"] >= 0
            assert match["wall_seconds"] > 0
            assert match["elapsed_seconds"] == 0.1
    assert {json.loads(path.read_text())["configuration"]["collect_decision_stats"] for path in paths} == {True, False}
