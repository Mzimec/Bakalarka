"""Paired whole-game checkpoint A/B with identical seeds and search budgets."""
import argparse
from datetime import datetime
import json
from pathlib import Path

from benchmarks.play_search import run


def trace(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "used_search" not in row:
            continue
        search = row.get("search") or {}
        metrics = search.get("metrics") or {}
        rows.append({**{key: row.get(key) for key in (
            "request_kind", "turn", "phase", "reason", "options", "information_key")},
            **{key: search.get(key) for key in ("choice", "estimates", "stop_reason", "used_fallback")},
            **{key: metrics.get(key) for key in ("iterations_completed", "advance_attempts", "interruption_reasons")}})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--worlds", type=int, default=1)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-advances", type=int, default=16)
    parser.add_argument("--checkpoint-limit", type=int, default=8)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-checkpoints-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    print(folder, flush=True)
    results = []
    for name, limit in (("replay", 0), ("checkpoints", args.checkpoint_limit)):
        target = folder / name
        target.mkdir()
        result = run(target, seeds=(args.seed,), checkpoint_limit=limit, iterations=args.iterations,
                     worlds=args.worlds, max_depth=args.max_depth, max_advances=args.max_advances)
        (target / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append(result)
    traces = [trace(folder / name / "match-0.jsonl") for name in ("replay", "checkpoints")]
    matches = [r["matches"][0] for r in results]
    same = (bool(traces[0]) and traces[0] == traces[1] and
            all(matches[0][key] == matches[1][key] for key in ("status", "winner", "turns", "decisions")))
    summary = {"scope": "Single sequential pair, replay first; not a strength comparison or confidence interval.",
               "passed": same and all(r["passed"] for r in results), "trace_equal": same,
               "decisions_compared": len(traces[0]),
               "elapsed_seconds": {name: r["elapsed_seconds"] for name, r in zip(("replay", "checkpoints"), matches)}}
    (folder / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
