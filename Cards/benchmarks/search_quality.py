"""Paired real-engine tactical checks; failures are retained, not filtered out."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from random import Random
from time import perf_counter

from benchmarks.search_hidden_worlds import catalog
from benchmarks.search_tactics import tactical_policy, tactical_scenario
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.search_simulator import SearchSimulator


CASES = {"remove_before_attack": 12, "two_spells_mana": 6, "double_block": 1, "counter_lethal": 1}


def position(case):
    if case not in CASES:
        raise ValueError(f"Unknown case {case}")
    def build(controllers):
        from game.console.demo_game import DemoGame
        from game.enums import ZoneType as Z, TurnPhase as P, ManaType
        from game.game_state import Card, Player, State
        from game.game_loop.game_loop import GameLoop
        from game.game_actions.resolution.action_processor import ActionProcessor
        from game.game_actions.resolution.operation_executor import OperationExecutor
        from game.game_actions.resolution.resolution_engine import ResolutionEngine
        from game.game_actions.resolution.event_bus import EventBus
        state = State([Player([], c, idx=i) for i, c in enumerate(controllers)])
        state.rng = Random(11)
        state.turn.number, state.turn.phase = 5, P.PRECOMBAT_MAIN
        definitions = catalog()
        def add(name, zone=Z.BATTLEFIELD, owner=0):
            card = Card(definitions[name], state.players[owner])
            card.owner.add_card(card, zone)
            card.controlled_since = 0
            return card
        if case == "remove_before_attack":
            add("Mountain")
            add("Shock", Z.HAND)
            add("Rumbling Baloth")
            add("Moorland Inquisitor", owner=1)
            state.players[1].health = 4
        elif case == "two_spells_mana":
            # Finite opportunity: the pool empties when this end step ends.
            state.turn.phase = P.END_STEP
            state.players[0].mana_pool.add_pair(ManaType.RED, 2)
            add("Shock", Z.HAND)
            add("Shock", Z.HAND)
            state.players[1].health = 4
        elif case == "double_block":
            add("Moorland Inquisitor")
            add("Moorland Inquisitor")
            attacker = add("Rumbling Baloth", owner=1)
            state.players[0].health = 2
            state.switch_active_player()
            state.begin_turn()
            state.combat.begin()
            state.turn.phase = P.DECLARE_ATTACKERS
            state.combat.declare_attackers(state.players[1], {attacker: state.players[0]})
            state.turn.phase = P.DECLARE_BLOCKERS
        for owner in (0, 1):
            for _ in range(4):
                add("Island", Z.DECK, owner)
        bus = EventBus()
        return DemoGame(state, bus, GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), bus))))
    return build


class ResponseScenario:
    """A fixed public cast/pass prefix; subsequent choices use the normal API."""
    def __init__(self):
        self.sim = SearchSimulator(tactical_scenario("answered_shock"), policy_for=tactical_policy, track_information=True)

    def start(self):
        node = self.sim.start()
        node = self.sim.advance_action(node, ObservationOpponentPolicy()(node.player_information()))
        return self.sim.advance_action(node, next(a for a in node.player_information().actions if a.kind == "PassPriorityAction"))

    def advance_action(self, node, action):
        return self.sim.advance_action(node, action)


def world_factory(case):
    if case == "counter_lethal":
        return lambda seed: ResponseScenario()
    return lambda seed: SearchSimulator(position(case), policy_for=tactical_policy, track_information=True)


def verify(case, sim, root, action, *, limit=40):
    """Execute the recommendation then the same fixed continuation for all modes.

This measures the selected first move under a stated continuation policy, not
the optimal value of every action or an entire repeatedly replanned match.
"""
    policy = ObservationOpponentPolicy()
    node, trace = root, []
    for i in range(limit):
        view = node.observation(root.decision.player)
        combat = view.get("combat", {})
        if node.terminal or (i and node.decision is not None and node.decision.kind == "PriorityDecisionRequest" and (
            (case == "two_spells_mana" and (view["turn"] != root.decision.turn or view["phase"] != "END_STEP"))
            or (case in {"remove_before_attack", "double_block"} and combat.get("regular_damage_done"))
            or (case == "counter_lethal" and not view["pending_abilities"]))):
            break
        if node.cutoff:
            break
        rating = None if i == 0 else policy.rank(node.player_information())[0]
        selected = action if rating is None else rating.action
        trace.append({"player": node.decision.player, "request": node.decision.kind,
                      "phase": node.decision.phase, "action": selected.describe(),
                      "continuation_fallback": rating is not None and not rating.supported})
        node = sim.advance_action(node, selected)
    else:
        return {"passed": False, "unresolved": True, "trace": trace}
    view = node.observation(root.decision.player)
    if case in {"remove_before_attack", "two_spells_mana"}:
        passed = node.terminal and view["players"][1]["has_lost"] and not view["players"][0]["has_lost"]
    elif case == "double_block":
        passed = (not view["players"][0]["has_lost"] and not any(c["name"] == "Rumbling Baloth" for c in view["battlefield"]))
    else:
        passed = not view["players"][1]["has_lost"] and not view["pending_abilities"]
    return {"passed": passed and not node.cutoff, "unresolved": bool(node.cutoff), "terminal": node.terminal,
            "trace": trace, "endpoint": view}


def run_case(case, mode, *, iterations=32, seed=42):
    factory = world_factory(case)
    sim = factory(seed)
    root = sim.start()
    info = root.player_information()
    policy = ObservationOpponentPolicy()
    budget = InformationSearchBudget(worlds=1, iterations=iterations, max_depth=CASES[case],
                                     max_extensions=16, max_advances=iterations * (CASES[case] + 18))
    started = perf_counter()
    if mode == "heuristic":
        choice, result = policy(info), None
    elif mode in {"uniform", "guided"}:
        result = InformationSetSearch(opponent_policy=policy, budget=budget,
            rollout_policy=policy if mode == "guided" else None).search(factory, info, seed=seed)
        choice = result.choice
    else:
        raise ValueError(f"Unknown mode {mode}")
    elapsed = (perf_counter() - started) * 1000
    validation = verify(case, sim, root, choice)
    return {"case": case, "mode": mode, "seed": seed, "budget": asdict(budget) if result else None,
            "selected": choice.describe(), "elapsed_ms": elapsed,
            "recommendation_usable": result is None or not result.used_fallback,
            "search": asdict(result) if result else None, "verification": validation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    parser.add_argument("--modes", nargs="+", choices=("heuristic", "uniform", "guided"), default=["heuristic", "uniform", "guided"])
    parser.add_argument("--iterations", type=int, default=32)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42])
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("search-quality-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    rows = []
    for case in (CASES if args.case == "all" else (args.case,)):
        for seed in args.seeds:
            for mode in args.modes:
                row = run_case(case, mode, iterations=args.iterations, seed=seed)
                rows.append(row)
                (path / "results.json").write_text(json.dumps({"scope": "Controlled first-action comparison against fixed continuation; no win-rate claim.", "results": rows}, indent=2), encoding="utf-8")
                passed = row['verification']['passed'] and row['recommendation_usable']
                print(f"{'PASS' if passed else 'MISS'} {case} {mode} seed={seed}: {row['elapsed_ms']:.1f} ms", flush=True)
    print(path)


if __name__ == "__main__":
    main()
