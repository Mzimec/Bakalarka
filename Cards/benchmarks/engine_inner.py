"""Compare archived engine sources in separate, warmed worker processes."""
import argparse
import __future__
import gc
import hashlib
import json
import os
import platform
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter, process_time


def fingerprint(root):
    digest = hashlib.sha256()
    for package in ("game", "helper"):
        for path in sorted((root / package).rglob("*.py")):
            digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def worker(args):
    if args.disable:
        from game.game_state import card, modifier
        from game.game_actions.triggers import trigger_roster
        from game.game_actions.resolution import cost_transaction
        reference = args.reference.resolve()

        def load(relative, namespace):
            exec(compile((reference / relative).read_text(encoding="utf-8-sig"), str(reference / relative),
                         "exec", flags=__future__.annotations.compiler_flag), namespace)
            return namespace

        if "plans" in args.disable:
            old = load("game/game_state/card.py", dict(card.__dict__))
            reader = old["Card"].get_stat
            old["Card"] = card.Card
            card.Card.get_stat = reader
        if "registry" in args.disable:
            old = load("game/game_actions/triggers/trigger_roster.py", dict(trigger_roster.__dict__))
            trigger_roster.capture_native_roster = old["capture_native_roster"]
        if "journal" in args.disable:
            old = load("game/game_actions/resolution/cost_transaction.py", dict(cost_transaction.__dict__))
            class EagerCheckpoint(old["RuntimeCheckpoint"]):
                def __init__(self, *args, native_payment=False, **kwargs):
                    super().__init__(*args, **kwargs)
            cost_transaction.RuntimeCheckpoint = EagerCheckpoint
    from benchmarks.trigger_dispatch import play, outcome, trace
    warm = outcome(play(1))
    if warm["status"] != "win":
        raise RuntimeError(f"Warmup failed: {warm}")
    gc.collect()
    wall, cpu = perf_counter(), process_time()
    matches = [outcome(play(seed, first)) for seed in range(1, args.seeds + 1) for first in (0, 1)]
    row = dict(wall_seconds=perf_counter()-wall, cpu_seconds=process_time()-cpu, matches=matches)
    if args.traces:
        checks = []
        for index, colors in enumerate((("white", "white"), ("red", "white"), ("blue", "black"), ("green", "red"))):
            path = args.traces / f"trace-{index}.jsonl"
            result = play(42 + index, colors=colors, output=path)
            records = trace(path)
            # Modular's diagnostic candidate summary flattens target groups
            # through sets. Their presentation order can differ between worker
            # processes. Keep all actual decision/action/event fields intact.
            for record in records:
                if record.get("decision") == "policy_choice":
                    choice = record.get("details", {}).get("choice", {})
                    for key in ("targets", "cost_targets"):
                        if type(choice.get(key)) is list:
                            choice[key] = sorted(choice[key])
            checks.append(dict(colors=colors, outcome=outcome(result), records=len(records),
                               sha256=hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()))
        row["traces"] = checks
    print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--disable", nargs="*", choices=("plans", "registry", "journal"))
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("seeds must be positive")
    if args.worker:
        worker(args)
        return
    if args.reference is None or args.output is None:
        parser.error("reference and output are required")
    args.output.mkdir(parents=True, exist_ok=False)
    current = Path(__file__).resolve().parents[1]
    roots = dict(reference=args.reference.resolve(), optimized=current)
    sources = {name: fingerprint(root) for name, root in roots.items()}
    trials = []
    for index, name in enumerate(("reference", "optimized", "optimized", "reference")):
        env = dict(os.environ, PYTHONPATH=str(roots[name]), PYTHONHASHSEED="0")
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--seeds", str(args.seeds)]
        if name == "optimized" and args.disable:
            command.extend(("--reference", str(args.reference.resolve()), "--disable", *args.disable))
        if index < 2:
            folder = args.output / name
            folder.mkdir()
            command.extend(("--traces", str(folder.resolve())))
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        row = dict(mode=name, **json.loads(result.stdout.splitlines()[-1]))
        trials.append(row)
        (args.output / "trials.json").write_text(json.dumps(trials, indent=2), encoding="utf-8")
        print({key: row[key] for key in ("mode", "wall_seconds", "cpu_seconds")}, flush=True)
    medians = {name: median(row["wall_seconds"] for row in trials if row["mode"] == name) for name in roots}
    passed = (all(row["matches"] == trials[0]["matches"] for row in trials)
              and all(match["status"] == "win" for row in trials for match in row["matches"])
              and trials[0]["traces"] == trials[1]["traces"])
    valid = (all(row["cpu_seconds"] / row["wall_seconds"] >= .9 for row in trials)
             and sources == {name: fingerprint(root) for name, root in roots.items()})
    report = dict(trials=trials, source_fingerprints=sources, passed=passed, timing_valid=valid,
                  runtime=dict(python=sys.version, platform=platform.platform(), hash_seed="0", seeds=args.seeds),
                  disabled=args.disable or [],
                  median_seconds=medians if valid else None,
                  reduction=1-medians["optimized"]/medians["reference"] if valid else None,
                  scope="Separate warmed workers, ABBA order, natural GC; timed games have no logging/profiler/decision telemetry. Four deck trace pairs outside timings.")
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print({key: report[key] for key in ("passed", "timing_valid", "median_seconds", "reduction")}, flush=True)
    return 0 if passed and valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
