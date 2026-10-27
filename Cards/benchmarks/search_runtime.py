"""Paired deterministic search-cache comparison on actual engine scenarios."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import platform

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator


def run(*, iterations=32):
    rows = []
    for case in ("lethal_shock", "answered_shock", "attack_pacified"):
        def factory(seed):
            return SearchSimulator(tactical_scenario(case), policy_for=tactical_policy, track_information=True)
        info = factory(0).start().player_information()
        results = []
        for cache in (0, 2048):
            policy = ObservationOpponentPolicy()
            budget = InformationSearchBudget(worlds=2, iterations=iterations, max_depth=2,
                                             max_advances=iterations * 16)
            result = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
                transition_cache_size=cache, budget=budget).search(factory, info, seed=42)
            results.append(result)
        a, b = results
        same = ((a.choice, a.estimates, a.stop_reason, a.used_fallback, a.metrics.iterations_completed,
                 a.metrics.advance_attempts, a.metrics.interruption_reasons) ==
                (b.choice, b.estimates, b.stop_reason, b.used_fallback, b.metrics.iterations_completed,
                 b.metrics.advance_attempts, b.metrics.interruption_reasons))
        rows.append({"case": case, "equivalent": same, "budget": asdict(budget),
                     "uncached": asdict(a), "cached": asdict(b)})
        print(f"{case}: same={same}, {a.metrics.elapsed_ms:.0f} -> {b.metrics.elapsed_ms:.0f} ms, "
              f"engine calls {a.metrics.simulator_advance_calls} -> {b.metrics.simulator_advance_calls}", flush=True)
    return {"scope": "Fixed known-world tactical fixtures, not strength or hidden-belief validation. "
                     "Cache-off runs first; single-run timings are indicative, deterministic work counts are primary.",
            "python": platform.python_version(), "hash_seed": os.environ.get("PYTHONHASHSEED", "random"),
            "passed": all(r["equivalent"] for r in rows), "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=32)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-runtime-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    print(folder, flush=True)
    result = run(iterations=args.iterations)
    (folder / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
