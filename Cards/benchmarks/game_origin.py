"""One live MCTS decision after ordinary 60-card setup; explicit deck knowledge."""
import argparse
from functools import partial
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from benchmarks.search_tactics import tactical_policy
from game.ai.game_origin import GameOrigin, GameOriginWorlds
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.setup_deal_evidence import SetupDealEvidence
from game.cards.catalog import game_catalog
from game.cards.decks import load_arena_starter


def keep_pass(information):
    """Controlled history model: both players keep, then pass before main."""
    for action in information.actions:
        if action.kind == "MulliganOption" and not action.describe()["take_mulligan"]:
            return action
        if action.kind == "PassPriorityAction":
            return action
    return ObservationOpponentPolicy()(information)


def history_weights(information):
    preferred = keep_pass(information)
    return tuple(float(action == preferred) for action in information.actions)


def run(*, match_seed=42, search_seed=7001, iterations=8, population_size=4,
        mulligans=0, conditioning="draws", starting_player=0):
    if type(mulligans) is not int or not 0 <= mulligans <= 7:
        raise ValueError("mulligans must be in 0..7")
    if conditioning not in {"draws", "setup", "initial"}:
        raise ValueError("Unknown conditioning mode")
    # Deck files are read once, before any simulation. Knowledge of BOTH lists
    # is an explicit experiment assumption, never inferred from the live State.
    origin = GameOrigin((load_arena_starter("red"), load_arena_starter("white")),
                        game_catalog, starting_player_idx=starting_player)
    worlds = GameOriginWorlds(origin, policy_for=tactical_policy, condition_draws=conditioning == "draws")
    class InitialDealOnly:
        """Previous baseline: redraw evidence must match by random rejection."""
        def __call__(self, seed):
            return worlds(seed)

        def condition(self, information):
            evidence = SetupDealEvidence.from_information(information, hand_size=7)
            if evidence is None:
                return self
            return partial(worlds, opening_hand_ids=evidence.constraints()["opening_hand_ids"])

    prior = InitialDealOnly() if conditioning == "initial" else worlds
    population = IncrementalWorldProvider(prior, size=population_size, seed=search_seed,
                                         opponent_weights=history_weights, max_attempts=4)

    class FirstMainProvider:
        def reset(self):
            population.reset()
            self.used = False

        def __call__(self, information):
            view = information.current.observation()
            if (not self.used and information.current.request_kind == "PriorityDecisionRequest"
                    and view["phase"] == "PRECOMBAT_MAIN" and view["active"] == view["self"]):
                self.used = True
                return population(information)
            return None

    policy = ObservationOpponentPolicy()
    search = InformationSetSearch(opponent_policy=policy, budget=InformationSearchBudget(
        worlds=2, iterations=iterations, max_depth=1, max_extensions=8, max_advances=iterations * 16))
    trace = []

    class RecordedAgent(MCTSAgent):
        def _decide(self, request):
            result = super()._decide(request)
            trace.append({"player": request.player.idx, "request": type(request).__name__,
                          "phase": request.state.turn.phase.name if request.state is not None else None,
                          "action": self.recall.entries[-1].describe(), **result.info["mcts"]})
            return result

    def own_setup_policy(information):
        if information.current.request_kind == "MulliganRequest":
            take = information.current.observation()["decision_context"]["mulligans_taken"] < mulligans
            return next(a for a in information.actions if a.describe()["take_mulligan"] == take)
        return keep_pass(information)

    own = RecordedAgent(world_provider=FirstMainProvider(), search=search, fallback=own_setup_policy,
                        policy_for=tactical_policy, seed=search_seed)
    opponent = RecordedAgent(world_provider=lambda info: None, fallback=keep_pass, policy_for=tactical_policy)
    started = perf_counter()
    game = origin.scenario(match_seed)((own, opponent))
    while not (game.state.active_player.idx == 0 and game.state.turn.phase.name == "BEGIN_COMBAT"):
        game.loop.step(game.state)
    attempted = [row for row in trace if row["used_search"]]
    passed = len(attempted) == 1 and attempted[0]["reason"] == "searched"
    return {
        "passed": passed,
        "scope": "Ordinary setup through first main, both declared decklists known; prescribed own mulligans and controlled keep/pass past opponent. One searched decision, not a complete match or strength benchmark.",
        "match_seed": match_seed, "search_seed": search_seed,
        "iterations": iterations, "population_size": population_size,
        "mulligans": mulligans, "conditioning": conditioning,
        "starting_player": starting_player,
        "decks": [{"name": d.name, "size": d.size} for d in origin.decks],
        "elapsed_ms": (perf_counter() - started) * 1000,
        "search_attempts": len(attempted), "searched_decisions": sum(r["reason"] == "searched" for r in trace),
        "trace": trace,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-seed", type=int, default=42)
    parser.add_argument("--search-seed", type=int, default=7001)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--population-size", type=int, default=4)
    parser.add_argument("--mulligans", type=int, default=0)
    parser.add_argument("--conditioning", choices=("draws", "setup", "initial"), default="draws")
    parser.add_argument("--starting-player", type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    result = run(**vars(args))
    folder = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("game-origin-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    (folder / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{'PASS' if result['passed'] else 'MISS'}: {result['searched_decisions']} searched decision, {result['elapsed_ms']:.1f} ms\n{folder}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
