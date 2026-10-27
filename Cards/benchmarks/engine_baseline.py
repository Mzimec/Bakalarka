"""Compare ordinary games with a minimal-policy workload, sequentially."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time

from benchmarks.environment import collect_environment
from game.bin.tournament import AGENT_CONFIGS
from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.paths import RUNS_DIR
from game.simulation.config import make_controller
from game.simulation.match_runner import TournamentPlayer, run_match, run_tournament


MATCHUPS = {"modular-simple": ("modular", "simple"), "trivial-trivial": ("trivial", "trivial")}


def summarize(results):
    """Keep workload and measured decision intervals next to elapsed time."""
    games = len(results)
    elapsed = sum(r.elapsed_seconds for r in results)
    turns = sum(r.turns for r in results)
    decisions = sum(r.decisions for r in results)
    timed = sum(e["overall"]["count"] for r in results for e in r.decision_timing)
    decision_ns = sum(e["overall"]["total_elapsed_ns"] for r in results for e in r.decision_timing)
    generation_ns = sum(e["overall"]["total_generation_ns"] for r in results for e in r.decision_timing)
    other_ns = sum(e["overall"]["total_other_decision_ns"] for r in results for e in r.decision_timing)
    return {
        "games": games, "completed": sum(r.status == "win" or r.status == "draw" for r in results),
        "match_elapsed_seconds": elapsed, "seconds_per_game": elapsed / games,
        "turns": turns, "milliseconds_per_turn": elapsed * 1000 / turns if turns else None,
        "controller_decisions": decisions, "timed_decisions": timed,
        "decision_seconds": decision_ns / 1e9 if timed else None,
        "active_option_generation_seconds": generation_ns / 1e9 if timed else None,
        "other_decision_seconds": other_ns / 1e9 if timed else None,
        "outside_decisions_seconds": elapsed - decision_ns / 1e9 if timed else None,
        "outcomes": [{key: getattr(r, key) for key in
                     ("seed", "starting_player", "status", "winner_index", "turns", "decisions", "error")}
                     for r in results],
    }


def run(output, *, rounds=10, repeats=2, seed=1, decks=("white", "white"), diagnostics=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    environment = collect_environment(root)
    environment["instrumentation"]["component_profiler_enabled"] = False
    decklists = tuple(load_arena_starter(name) for name in decks)
    report = {"environment": environment, "settings": {
        "rounds": rounds, "games_per_batch": rounds * 2, "repeats": repeats,
        "seed": seed, "decks": decks, "max_turns": 100, "max_decisions": 10000,
        "matchups": {label: [AGENT_CONFIGS[kind] for kind in kinds] for label, kinds in MATCHUPS.items()},
        "warmup": "One unrecorded game per matchup before timing batches",
    }, "timing_batches": [], "diagnostic_batches": []}

    for kinds in MATCHUPS.values():
        run_match(decks, None, seed=seed, max_turns=100, collect_decision_stats=False,
                  controllers=tuple(make_controller(AGENT_CONFIGS[kind], seed=seed + i,
                                    max_decisions=10000) for i, kind in enumerate(kinds)))

    def batch(label, folder, *, collect):
        kinds = MATCHUPS[label]
        players = tuple(TournamentPlayer(f"{kind}-{i + 1}", decklists[i], AGENT_CONFIGS[kind])
                        for i, kind in enumerate(kinds))
        wall, cpu = perf_counter(), process_time()
        results = run_tournament(output / folder, players, games=rounds, seed=seed,
                                 log_matches=False, log_batch=True, collect_decision_stats=collect)
        result = {"matchup": label, "folder": folder, "decision_stats_enabled": collect,
                  "batch_wall_seconds": perf_counter() - wall,
                  "batch_cpu_seconds": process_time() - cpu, **summarize(results)}
        print(f"{folder}: {result['match_elapsed_seconds']:.3f} s, {result['completed']}/{len(results)} completed; "
              f"{result['turns']} turns; {result['controller_decisions']} decisions", flush=True)
        return result

    labels = tuple(MATCHUPS)
    for repeat in range(repeats):
        # Alternate order to reduce warmup/frequency/other-load bias.
        for label in labels if repeat % 2 == 0 else reversed(labels):
            report["timing_batches"].append(batch(label, f"timing-{repeat + 1}-{label}", collect=False))
    if diagnostics:
        for label in labels:
            report["diagnostic_batches"].append(batch(label, f"diagnostic-{label}", collect=True))
    report["validation"] = {}
    for label in labels:
        batches = [b for b in (*report["timing_batches"], *report["diagnostic_batches"])
                   if b["matchup"] == label]
        report["validation"][label] = {
            "same_outcomes_with_repetition_and_diagnostics": all(
                b["outcomes"] == batches[0]["outcomes"] for b in batches),
            "all_games_completed": all(b["completed"] == b["games"] for b in batches),
        }
    report["medians"] = {
        label: {"match_elapsed_seconds": median(b["match_elapsed_seconds"] for b in report["timing_batches"]
                                                if b["matchup"] == label),
                "seconds_per_game": median(b["seconds_per_game"] for b in report["timing_batches"]
                                           if b["matchup"] == label)}
        for label in labels}
    report["limitations"] = [
        "Trivial chooses the first non-pass non-mana priority action; attacks using AllAttackersStrategy; "
        "takes the first option elsewhere. No scoring, observation, LLM or search.",
        "It keeps its opening hand, normally never blocks, and tries each priority ability once per turn/phase.",
        "Different agents produce different trajectories and consume different amounts of lazy option generation. "
        "A whole-game difference is not a direct measurement of scoring overhead.",
        "Timing batches disable decision telemetry and match logs; diagnostic batches are separate instrumented runs.",
        "Active generation times top-level next() intervals, including nested expansion once, excluding consumer time. "
        "Other decision time includes discovery outside streams, policy, validation and instrumentation. "
        "Precomputed choices are generated before decide() and are not part of its generation interval.",
        "Outside-decisions time includes engine execution, setup and observer/orchestration overhead. "
        "This is a minimal-policy workload, not a policy-free replay of an identical game.",
        "Controller decision counts differ in coverage: Trivial counts every request, including setup; older agents "
        "count their recorded decisions. Use diagnostic timed_decisions for complete request counts.",
    ]
    (output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Engine workload with minimal policy", "", "Timings without decision telemetry or match logs:", ""]
    for label, values in report["medians"].items():
        lines.append(f"- {label}: median {values['match_elapsed_seconds']:.3f} s per {2 * rounds} games; "
                     f"{values['seconds_per_game'] * 1000:.1f} ms/game.")
    for batch_result in report["diagnostic_batches"]:
        b = batch_result
        lines.extend(["", f"Diagnostic {b['matchup']}: {b['match_elapsed_seconds']:.3f} s; "
                      f"{b['timed_decisions']} timed requests; {b['turns']} turns.",
                      f"Active option generation {b['active_option_generation_seconds']:.3f} s; "
                      f"other decision work {b['other_decision_seconds']:.3f} s; "
                      f"outside decisions {b['outside_decisions_seconds']:.3f} s."])
    lines.extend(["", *[f"- {note}" for note in report["limitations"]]])
    lines.extend(["", "Validation:", "", *[f"- {label}: {result}"
                  for label, result in report["validation"].items()]])
    (output / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Results: {output.resolve()}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10, help="Seeds, each played with both starting players")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--deck1", choices=tuple(ARENA_STARTERS), default="white")
    parser.add_argument("--deck2", choices=tuple(ARENA_STARTERS), default="white")
    parser.add_argument("--diagnostics", action="store_true", help="Run separate batches with generation/decision timing")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.rounds, args.repeats) < 1:
        parser.error("rounds and repeats must be positive")
    output = args.output or RUNS_DIR / f"engine-baseline-{datetime.now():%Y%m%d-%H%M%S-%f}"
    report = run(output, rounds=args.rounds, repeats=args.repeats, seed=args.seed,
                 decks=(args.deck1, args.deck2), diagnostics=args.diagnostics)
    return int(any(r["status"] in {"error", "unsupported"} for batch in
                   (*report["timing_batches"], *report["diagnostic_batches"]) for r in batch["outcomes"]) or
               any(not v["same_outcomes_with_repetition_and_diagnostics"] for v in report["validation"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
