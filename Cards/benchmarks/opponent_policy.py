"""Small tactical checks and selection latency for the observation-only prior."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from benchmarks.search_tactics import tactical_scenario, tactical_policy
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator


CASES = ("lethal_shock", "useful_pacifism", "redundant_pacifism", "attack_pacified", "counter_response")


def run_case(case, *, repetitions=100):
    if case not in CASES or type(repetitions) is not int or repetitions < 1:
        raise ValueError("Choose a supported case and a positive repetition count")
    sim = SearchSimulator(tactical_scenario("answered_shock" if case == "counter_response" else case),
                          policy_for=tactical_policy, track_information=True)
    node = sim.start()
    policy = ObservationOpponentPolicy()
    if case == "counter_response":
        node = sim.advance_action(node, policy(node.player_information()))
        node = sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))
    info = node.player_information()
    ratings = policy.rank(info)
    chosen = ratings[0].action
    start = perf_counter()
    for _ in range(repetitions):
        assert policy(info) == chosen
    elapsed_ms = (perf_counter() - start) * 1000
    child = sim.advance_action(node, chosen)
    assert sim.fork(child) == child
    if case == "redundant_pacifism":
        passed = chosen.kind == "PassPriorityAction"
    elif case == "attack_pacified":
        passed = bool(chosen.describe()["declarations"]["mapping"])
    elif case == "counter_response":
        passed = chosen.kind == "AbilityAction" and chosen.describe()["source"]["name"] == "Counterspell"
    else:
        passed = chosen.kind == "AbilityAction" and ratings[0].score > 0
    return {"case": case, "passed": passed, "options": len(info.actions),
            "repetitions": repetitions, "mean_policy_ms": elapsed_ms / repetitions,
            "selected": chosen.describe(), "ratings": [
                {"action": r.action.describe(), "score": r.score, "supported": r.supported, "reasons": r.reasons}
                for r in ratings],
            "scope": "Fixed tactical checks, not a playing-strength benchmark. Timing includes input decoding and ranking; excludes engine generation/replay."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=100)
    args = parser.parse_args()
    rows = [run_case(case, repetitions=args.repetitions) for case in CASES]
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("opponent-policy-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for row in rows:
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['case']}: {row['mean_policy_ms']:.3f} ms, {row['options']} options")
    print(path)
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
