"""Sequential real-game MCTS coverage audit, with every fallback linked to JSONL.

This is defect discovery, not a tournament or equal-work performance comparison.
All ten unordered starter color pairs are exercised, with both agent seats and
starting positions. Extra UCT/hierarchical runs use the same latency budget.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter, process_time
import traceback


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def worker(config, output):
    from benchmarks.engine_inner import fingerprint
    from game.simulation.config import run_config
    import game
    # Failure-only instrumentation: preserve exception types/messages/control
    # flow and avoid tracing successful engine calls.
    from game.ai.search_simulator import SimulationBudgetExceeded
    import inspect
    original_init = SimulationBudgetExceeded.__init__
    limits = []
    def record_limit(exc, *args):
        original_init(exc, *args)
        detail = dict(type=type(exc).__name__, message=str(exc))
        frame = inspect.currentframe().f_back
        try:
            while frame is not None:
                if frame.f_code.co_name == "_decide" and frame.f_code.co_filename.replace("\\", "/").endswith("/ai/mcts_agent.py"):
                    request = frame.f_locals["request"]
                    detail.update(turn=request.state.turn.number, phase=request.state.turn.phase.name,
                                  request=type(request).__name__)
                    break
                frame = frame.f_back
        finally:
            del frame
        limits.append(detail)
    SimulationBudgetExceeded.__init__ = record_limit
    source = Path(game.__file__).resolve().parents[1]
    stamp = fingerprint(source)
    wall, cpu = perf_counter(), process_time()
    try:
        result = dict(result=asdict(run_config(config, output=output/"match.jsonl")))
    except Exception:
        result = dict(worker_exception=traceback.format_exc())
    result.update(wall_seconds=perf_counter()-wall, cpu_seconds=process_time()-cpu,
                  fingerprint=stamp, source_unchanged=stamp == fingerprint(source),
                  simulation_limit_exceptions=limits)
    save(output/"results.json", result)


def classify(reason):
    if reason in {"setup_policy", "setup"}:
        return "setup"
    if reason in {"time_budget", "advance_budget", "simulator_budget", "option_budget", "incomplete_menu_history"}:
        return "budget"
    if reason in {"extension_limit", "root_coverage", "no_completed_trajectory"}:
        return "horizon_or_coverage"
    return "support_or_history"


def summarize(directory, case):
    result_path = directory/"results.json"
    data = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result = data.get("result", {})
    reasons, stops, interruptions, requests = Counter(), Counter(), Counter(), Counter()
    fallbacks, exceptions = [], []
    started = usable = completed = 0
    log = directory/"match.jsonl"
    if log.exists():
        with log.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    exceptions.append(dict(line=line_number, error="Incomplete JSONL record"))
                    continue
                if row.get("kind") == "exception":
                    exceptions.append(dict(line=line_number, traceback=row.get("traceback")))
                if row.get("kind") != "search_decision":
                    continue
                reason = row["reason"]
                reasons[reason] += 1
                requests[row["request_kind"]] += 1
                started += bool(row["used_search"])
                usable += reason == "searched"
                search = row.get("search")
                if search:
                    stops[search["stop_reason"]] += 1
                    metrics = search["metrics"]
                    interruptions.update(dict(metrics["interruption_reasons"]))
                    completed += metrics["iterations_completed"]
                if row["used_fallback"]:
                    fallbacks.append(dict(category=classify(reason), line=line_number,
                        **{k: row.get(k) for k in ("turn", "phase", "request_kind", "reason",
                            "options", "elapsed_ms", "information_key", "world_preparation", "unsupported_detail")},
                        search=search))
    row = dict(case=case, status=result.get("status", "worker_incomplete"), error=result.get("error"),
        turns=result.get("turns"), elapsed_seconds=result.get("elapsed_seconds"),
        cpu_seconds=data.get("cpu_seconds"), source_unchanged=data.get("source_unchanged"),
        fingerprint=data.get("fingerprint"), search_attempts=started, usable_searches=usable,
        completed_trajectories=completed, reasons=dict(reasons), stops=dict(stops),
        trajectory_interruptions=dict(interruptions), requests=dict(requests),
        fallbacks=fallbacks, exceptions=exceptions, worker_exception=data.get("worker_exception"))
    row["simulation_limit_exceptions"] = data.get("simulation_limit_exceptions", [])
    save(directory/"fallbacks.json", row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settings", type=Path, default=Path(__file__).resolve().parents[1]/"configs/mcts_flat_puct_fast.json")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.output)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    pairs = [("white", "blue"), ("white", "black"), ("blue", "black"),
             ("blue", "red"), ("black", "red"), ("black", "green"),
             ("red", "green"), ("red", "white"), ("green", "white"), ("green", "blue")]
    cases = [dict(name=f"{i+1:02}-{own}-{other}", own=own, other=other,
                  seed=101+i, search_seed=7001+i, seat=i % 2, starting=(i//2) % 2, selection="puct")
             for i, (own, other) in enumerate(pairs)]
    cases += [dict(name="11-uct-white-green", own="white", other="green", seed=201,
                   search_seed=7101, seat=1, starting=1, selection="uct"),
              dict(name="12-hierarchical-blue-black", own="blue", other="black", seed=202,
                   search_seed=7102, seat=1, starting=0, selection="hierarchical_puct")]
    save(args.output/"manifest.json", dict(scope=__doc__, settings=settings, cases=cases,
        python=sys.version, executable=sys.executable, timeout_seconds=args.timeout,
        hash_seed="0", execution="sequential subprocess per game"))
    rows = []
    for case in cases:
        directory = args.output/case["name"]
        directory.mkdir()
        configured = {**settings, "deck_knowledge": "both_lists", "selection": case["selection"],
                      "prior": "uniform" if case["selection"] == "uct" else "heuristic",
                      "search_seed": case["search_seed"]}
        players = [dict(name="Simple", deck=case["other"], agent=dict(type="simple")) for _ in range(2)]
        players[case["seat"]] = dict(name="Search", deck=case["own"], agent=dict(type="mcts", mcts=configured))
        config = directory/"config.json"
        save(config, dict(seed=case["seed"], starting_player=case["starting"],
                          max_turns=80, max_decisions=3000, players=players))
        env = dict(os.environ, PYTHONHASHSEED="0")
        print("Starting", case["name"], flush=True)
        with (directory/"worker.txt").open("w", encoding="utf-8") as stream:
            try:
                process = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                    "--worker", str(config.resolve()), "--output", str(directory.resolve())],
                    stdout=stream, stderr=subprocess.STDOUT, env=env, timeout=args.timeout)
                execution = dict(returncode=process.returncode, timed_out=False)
            except subprocess.TimeoutExpired:
                execution = dict(returncode=None, timed_out=True)
        row = summarize(directory, case)
        row["execution"] = execution
        rows.append(row)
        save(args.output/"inventory.json", dict(scope=__doc__, cases=rows))
        problems = Counter(f["reason"] for f in row["fallbacks"] if f["category"] != "setup")
        print(case["name"], row["status"], f"{row['usable_searches']}/{row['search_attempts']}",
              dict(problems), row["error"], flush=True)
    totals = dict(games=len(rows), statuses=dict(Counter(r["status"] for r in rows)),
        search_attempts=sum(r["search_attempts"] for r in rows),
        usable_searches=sum(r["usable_searches"] for r in rows),
        completed_trajectories=sum(r["completed_trajectories"] for r in rows),
        fallback_reasons=dict(Counter(f["reason"] for r in rows for f in r["fallbacks"])),
        trajectory_interruptions=dict(sum((Counter(r["trajectory_interruptions"]) for r in rows), Counter())),
        source_unchanged=all(r["source_unchanged"] for r in rows),
        fingerprints=sorted({r["fingerprint"] for r in rows if r["fingerprint"]}),
        timed_out=sum(r["execution"]["timed_out"] for r in rows))
    save(args.output/"summary.json", totals)
    print(json.dumps(totals, indent=2), flush=True)


if __name__ == "__main__":
    main()
