import json
from pathlib import Path
import sys

import pytest

from benchmarks import function_profile
from benchmarks.function_profile import FunctionProfile


def test_nested_average_maximum_and_self_time(monkeypatch, tmp_path):
    clock = [0]
    monkeypatch.setattr(function_profile, "perf_counter_ns", lambda: clock[0])

    def leaf(duration):
        clock[0] += duration

    def parent():
        clock[0] += 100
        leaf(200)
        leaf(600)
        clock[0] += 100

    profile = FunctionProfile(Path(__file__).parents[1])
    with profile:
        parent()
    rows = {r["function"].split(".")[-1]: r for r in profile.rows()}
    assert rows["leaf"]["calls"] == 2
    assert rows["leaf"]["average_us"] == .4
    assert rows["leaf"]["max_ms"] == .0006
    assert rows["parent"]["total_ms"] == .001
    assert rows["parent"]["self_ms"] == .0002
    assert not profile.stack and profile.unmatched_returns == 0
    profile.write(tmp_path / "profile")
    data = json.loads((tmp_path / "profile/functions.json").read_text())
    assert data["unfinished_activations"] == 0
    assert (tmp_path / "profile/functions.csv").exists()


def test_recursion_generator_suspension_and_exception(monkeypatch):
    clock = [0]
    monkeypatch.setattr(function_profile, "perf_counter_ns", lambda: clock[0])

    def recursive(depth):
        clock[0] += 100
        if depth:
            recursive(depth - 1)

    def stream():
        clock[0] += 100
        yield 1
        clock[0] += 200

    def failure():
        {}.pop("missing")

    profile = FunctionProfile(Path(__file__).parents[1])
    with pytest.raises(KeyError):
        with profile:
            recursive(2)
            iterator = stream()
            next(iterator)
            clock[0] += 10000  # Waiting between resumes must not enter generator time.
            next(iterator, None)
            failure()
    assert sys.getprofile() is None
    rows = {r["function"].split(".")[-1]: r for r in profile.rows()}
    assert rows["recursive"]["calls"] == 3
    assert rows["recursive"]["max_ms"] == .0003
    assert rows["stream"]["calls"] == 2
    assert rows["stream"]["generator_segments"]
    assert rows["stream"]["total_ms"] == .0003
    assert rows["stream"]["max_ms"] == .0002
    assert rows["pop"]["calls"] == 1
    assert not profile.stack and profile.unmatched_returns == 0


def test_existing_profiler_is_not_replaced():
    hook = lambda *args: None
    sys.setprofile(hook)
    try:
        with pytest.raises(RuntimeError, match="already active"):
            with FunctionProfile(Path.cwd()):
                pass
        assert sys.getprofile() is hook
    finally:
        sys.setprofile(None)
