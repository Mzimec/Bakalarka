"""Alternating benchmark for reuse of a normalized replay observation."""
from datetime import datetime
import gc
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from benchmarks.world_root_copy import capture_snapshot
from game.ai.observation import decision_observation
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.simulation.search_config import search_generation_policy


def main():
    request, _ = capture_snapshot()
    options = tuple(request.option_space(search_generation_policy(request)))
    view, recall = decision_observation(request), PlayerRecall()
    rows, expected = [], None
    for repetition in range(4):
        modes = (False, True) if repetition % 2 == 0 else (True, False)
        for retained in modes:
            gc.collect()
            start = perf_counter()
            for _ in range(100):
                projected = DecisionProjector(request, options, recall, view=view, retain_view=retained).build()
                actor = projected._view if retained else projected.information.current.observation()
            elapsed = perf_counter() - start
            actual = projected.information, actor
            if expected is None:
                expected = actual
            assert actual == expected
            rows.append({"retained": retained, "repetition": repetition, "ms": elapsed * 1000})
    medians = {str(mode): median(r["ms"] for r in rows if r["retained"] == mode) for mode in (False, True)}
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("replay-projection-%Y%m%d-%H%M%S-%f")
    folder.mkdir()
    report = {"scope": "One captured midgame request, 100 projections per trial, four alternating trials per mode, existing view and options reused. Excludes world copying, observation building and generation; not a game speedup.",
              "equal": True, "samples": rows, "median_ms": medians,
              "actor_view_decodes_per_projection": {"reference": 2, "retained": 1}}
    (folder / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(folder)
    print(medians)


if __name__ == "__main__":
    main()
