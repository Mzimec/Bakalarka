"""Exercise reference replay on a seeded real game; not a playing-strength test."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from game.ai.search_simulator import SearchSimulator, starter_scenario


def diagnostic_choice(node):
    if node.decision.kind == "MulliganRequest":
        return next(c for c in node.choices if ("take_mulligan", ("bool", False)) in c.data[1])
    for kind in ("LandPlayAction", "AbilityAction", "PassPriorityAction"):
        choice = next((c for c in node.choices if c.kind == kind), None)
        if choice is not None:
            return choice
    return node.choices[0]


def run_diagnostic(*, seed=3, decisions=32):
    if type(decisions) is not int or decisions < 1:
        raise ValueError("decisions must be a positive integer")
    simulator = SearchSimulator(starter_scenario(seed=seed))
    start = perf_counter()
    node = simulator.start()
    setup_ms = (perf_counter() - start) * 1000
    rows = []
    for _ in range(decisions):
        if node.terminal or node.cutoff:
            break
        choice = diagnostic_choice(node)
        address = node.decision
        count = len(node.choices)
        started = perf_counter()
        node = simulator.advance(node, choice)
        rows.append({"depth": len(node.history), "request": address.kind, "player": address.player,
                     "turn": address.turn, "phase": address.phase, "options": count,
                     "selected": choice.kind, "advance_ms": (perf_counter() - started) * 1000})
    started = perf_counter()
    branch = simulator.fork(node)
    fork_ms = (perf_counter() - started) * 1000
    assert branch == node
    return {"backend": "deterministic replay", "seed": seed,
            "description": "Fixed diagnostic choices; no win-rate or search-speed claim.",
            "start_ms": setup_ms, "fork_ms": fork_ms,
            "total_ms": (perf_counter() - start) * 1000, "decisions": len(rows),
            "terminal": node.terminal, "cutoff": node.cutoff,
            "fork_equal": True, "steps": rows, "final_player_0_view": node.observation(0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--decisions", type=int, default=32)
    args = parser.parse_args()
    result = run_diagnostic(seed=args.seed, decisions=args.decisions)
    output = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-simulator-%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{result['decisions']} advances: {result['total_ms']:.1f} ms; fork: {result['fork_ms']:.1f} ms; equal: {result['fork_equal']}")
    print(output)


if __name__ == "__main__":
    main()
