"""Real-engine information-set search diagnostic against an explicit responder."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path

from benchmarks.search_hidden_worlds import beliefs, catalog, public_position
from benchmarks.search_tactics import tactical_policy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.sampled_scenario import HiddenZoneBelief, SampledScenario
from game.ai.search_simulator import SearchSimulator
from game.ai.observation_policy import ObservationOpponentPolicy


CASES = ("no_answer", "known_answer", "unknown_answer")


def counter_or_pass(info):
    """Fixture opponent: counter the other player's Shock aimed at us.

Uses only its own visible hand/menu and public stack. This deliberately small
policy is a diagnostic adversary, not a general heuristic for all card effects.
"""
    view = info.current.observation()
    pending = view["pending_abilities"]
    def targets_us(value):
        if isinstance(value, dict):
            return value.get("player") == info.current.player or any(targets_us(v) for v in value.values())
        return isinstance(value, list) and any(targets_us(v) for v in value)
    if pending and pending[-1]["controller"] != info.current.player and targets_us(pending[-1]["targets"]):
        for action in info.actions:
            if action.kind == "AbilityAction" and action.describe()["source"]["name"] == "Counterspell":
                return action
    return next(a for a in info.actions if a.kind == "PassPriorityAction")


def world_factory(case):
    if case not in CASES:
        raise ValueError(f"Unknown diagnostic case: {case}")
    own, other = beliefs()
    if case == "no_answer":
        other = HiddenZoneBelief.from_deck({"Island": 6}, hand_size=1, known_outside=("Island", "Island"))
    elif case == "known_answer":
        other = replace(other, known_hand=("Counterspell",))
    def build(seed):
        scenario = SampledScenario(public_position, catalog, (own, other), observer=0, seed=seed)
        return SearchSimulator(scenario, policy_for=tactical_policy, track_information=True)
    return build


def run_case(case, *, iterations=96, worlds=32, seed=42, opponent="heuristic", rollout="uniform"):
    if opponent not in {"heuristic", "fixture"}:
        raise ValueError("Choose heuristic or fixture opponent")
    if rollout not in {"uniform", "guided"}:
        raise ValueError("Choose uniform or guided rollout")
    factory = world_factory(case)
    root = factory(0).start().player_information()
    lethal = next(a for a in root.actions if a.kind == "AbilityAction"
                  and '"player": 1' in json.dumps(a.describe()["effect"]["targets"], sort_keys=True))
    budget = InformationSearchBudget(worlds=worlds, iterations=iterations, max_advances=iterations * 12,
                                     max_depth=1, max_extensions=8)
    pool_answers = []
    def measured_factory(sample_seed):
        sim = factory(sample_seed)
        # Privileged diagnostic only; never passed to the search policy input.
        pool_answers.append("Counterspell" in sim.factory.samples[1].hand)
        return sim
    policy = ObservationOpponentPolicy() if opponent == "heuristic" else counter_or_pass
    result = InformationSetSearch(opponent_policy=policy, budget=budget,
        rollout_policy=ObservationOpponentPolicy() if rollout == "guided" else None).search(measured_factory, root, seed=seed)
    cast = next(e for e in result.estimates if e.action == lethal)
    if case == "no_answer":
        passed = result.choice == lethal and cast.mean_value == 1
    elif case == "known_answer":
        passed = cast.visits > 0 and cast.mean_value < 1 and result.metrics.terminal_leaves == 0
    else:
        passed = result.choice == lethal and cast.mean_value is not None and 0 < cast.mean_value < 1
    return {"case": case, "passed": passed and not result.used_fallback,
            "seed": seed, "opponent_policy": opponent, "rollout": rollout, "budget": asdict(budget), "search": asdict(result),
            "sampled_worlds_with_answer": sum(pool_answers),
            "sampled_answer_fraction": sum(pool_answers) / len(pool_answers),
            "lethal_cast_estimate": asdict(cast),
            "scope": "Root information-set UCT vs fixed observation-only policy. Controlled scenario and finite belief pool; not equilibrium ISMCTS or a strength benchmark."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    parser.add_argument("--iterations", type=int, default=96)
    parser.add_argument("--worlds", type=int, default=32)
    parser.add_argument("--opponent", choices=("heuristic", "fixture"), default="heuristic")
    parser.add_argument("--rollout", choices=("uniform", "guided"), default="uniform")
    args = parser.parse_args()
    rows = [run_case(case, iterations=args.iterations, worlds=args.worlds, opponent=args.opponent, rollout=args.rollout)
            for case in (CASES if args.case == "all" else (args.case,))]
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("information-set-search-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for row in rows:
        r = row["search"]
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['case']}: "
              f"cast={row['lethal_cast_estimate']['mean_value']}, "
              f"completed={r['metrics']['iterations_completed']}, elapsed={r['metrics']['elapsed_ms']:.1f} ms")
    print(path)
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
