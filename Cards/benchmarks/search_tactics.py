"""Small known-world search diagnostics; no hidden-information strength claim."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from random import Random
from time import perf_counter

from game.ai.perfect_information_search import PerfectInformationSearch, SearchBudget
from game.ai.search_simulator import SearchSimulator
from game.game_actions.generation.decision_abstraction.requests import PriorityDecisionRequest
from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy


CASES = ("lethal_shock", "answered_shock", "useful_pacifism", "redundant_pacifism", "attack_pacified")


def tactical_scenario(case):
    if case not in CASES:
        raise ValueError(f"Unknown tactical case: {case}")
    def build(controllers):
        from game.console.demo_game import DemoGame
        from game.cards.starter_cards import starter_catalog
        from game.cards.control_cards import control_catalog
        from game.enums import ZoneType as Z, TurnPhase as P
        from game.game_state import Card, Player, State
        from game.game_loop.game_loop import GameLoop
        from game.game_actions.resolution.action_processor import ActionProcessor
        from game.game_actions.resolution.operation_executor import OperationExecutor
        from game.game_actions.resolution.resolution_engine import ResolutionEngine
        from game.game_actions.resolution.event_bus import EventBus

        state = State([Player([], controller, idx=i) for i, controller in enumerate(controllers)])
        state.rng = Random(11)
        state.turn.number = 5
        state.turn.phase = P.PRECOMBAT_MAIN
        catalog = {**starter_catalog(), **control_catalog()}
        def add(name, zone=Z.BATTLEFIELD, owner=0):
            card = Card(catalog[name], state.players[owner])
            card.owner.add_card(card, zone)
            card.controlled_since = 0
            return card
        if case in {"lethal_shock", "answered_shock"}:
            add("Mountain")
            add("Shock", Z.HAND)
            state.players[1].health = 2
            if case == "answered_shock":
                add("Island", owner=1)
                add("Island", owner=1)
                add("Counterspell", Z.HAND, owner=1)
        else:
            angel = add("Serra Angel", owner=1)
            if case != "useful_pacifism":
                add("Pacifism").attach(angel, state)
            if case == "attack_pacified":
                add("Impassioned Orator")
                state.players[1].health = 2
                state.combat.begin()
                state.turn.phase = P.DECLARE_ATTACKERS
            else:
                add("Plains")
                add("Plains")
                add("Pacifism", Z.HAND)
        # This diagnostic explicitly knows the entire fixed world. Library
        # contents are identical, so no unknown chance outcome affects a choice.
        for owner in range(2):
            for _ in range(3):
                add("Island", Z.DECK, owner)
        bus = EventBus()
        loop = GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus)))
        return DemoGame(state, bus, loop)
    return build


def tactical_policy(request):
    # Explicit experimental menu: source mana payment remains in ability plans.
    # Separate mana activations and concession are excluded in these fixtures.
    if isinstance(request, PriorityDecisionRequest):
        return PriorityGenerationPolicy(include_mana=False, include_concede=False)
    return None


def run_case(case, *, depth=None, max_advances=512):
    simulator = SearchSimulator(tactical_scenario(case), policy_for=tactical_policy)
    root = simulator.start()
    budget = SearchBudget(max_depth=depth if depth is not None else (7 if case == "attack_pacified" else 1),
                          max_advances=max_advances)
    started = perf_counter()
    result = PerfectInformationSearch(budget=budget).search(simulator, root)
    endpoint = root
    for step in result.variation:
        assert step.address == endpoint.decision
        endpoint = simulator.advance(endpoint, step.choice)
    chosen_kind = result.choice.kind if result.choice is not None else None
    if case in {"lethal_shock", "attack_pacified"}:
        passed = result.value == 1 and endpoint.terminal
    elif case == "answered_shock":
        passed = result.value is not None and result.value < 1 and any(
            row.value < 1 and any(step.address.player == 1 and step.choice.kind == "AbilityAction"
                                 for step in row.variation)
            for row in result.root_evaluations)
    elif case == "redundant_pacifism":
        passed = chosen_kind == "PassPriorityAction" and not result.used_fallback
    else:
        passed = chosen_kind == "AbilityAction" and any(
            c["name"] == "Serra Angel" and "can't block" in c["keywords"]
            for c in endpoint.observation(0)["battlefield"])
    return {"case": case, "passed": passed, "budget": asdict(budget), "search": asdict(result),
            "diagnostic_total_ms": (perf_counter() - started) * 1000,
            "root_player_0_view": root.observation(0), "variation_endpoint": endpoint.observation(0),
            "endpoint_terminal": endpoint.terminal,
            "scope": "Fixed known world; explicit generation policy; this is not ISMCTS or a playing-strength comparison."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    parser.add_argument("--depth", type=int)
    parser.add_argument("--max-advances", type=int, default=512)
    args = parser.parse_args()
    cases = CASES if args.case == "all" else (args.case,)
    results = [run_case(case, depth=args.depth, max_advances=args.max_advances) for case in cases]
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-tactics-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for row in results:
        result = row["search"]
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['case']}: depth={result['completed_depth']}, "
              f"advances={result['metrics']['advance_attempts']}, elapsed={result['metrics']['elapsed_ms']:.1f} ms, "
              f"stop={result['stop_reason']}")
    print(path)
    if not all(row["passed"] for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
