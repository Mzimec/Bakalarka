"""Run MCTS as a live controller in a controlled two-spell tactical position."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from benchmarks.search_quality import position
from benchmarks.search_tactics import tactical_policy
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.scenario_world_provider import ScenarioWorldProvider
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.search_simulator import SearchSimulator


def run(*, seed=42, iterations=16, provider_kind="fixed", population_size=4):
    recipe = position("two_spells_mana")
    policy = ObservationOpponentPolicy()
    world_factory = lambda seed: SearchSimulator(recipe, policy_for=tactical_policy, track_information=True)
    if provider_kind == "fixed":
        provider = ScenarioWorldProvider(world_factory, opponent_policy=policy)
    elif provider_kind == "history":
        provider = HistoryWorldProvider(world_factory)
    elif provider_kind == "incremental":
        provider = IncrementalWorldProvider(world_factory, size=population_size, seed=seed)
    else:
        raise ValueError("Unknown world provider")
    search = InformationSetSearch(opponent_policy=policy, budget=InformationSearchBudget(
        worlds=1, iterations=iterations, max_depth=6, max_extensions=16, max_advances=iterations * 24))
    trace = []
    class RecordedAgent(MCTSAgent):
        def _decide(self, request):
            result = super()._decide(request)
            trace.append({"player": request.player.idx, "request": type(request).__name__,
                          "action": self.recall.entries[-1].describe(), **result.info["mcts"]})
            return result
    own = RecordedAgent(world_provider=provider, search=search, policy_for=tactical_policy, seed=seed)
    opponent = RecordedAgent(world_provider=lambda info: None, policy_for=tactical_policy)
    game = recipe((own, opponent))
    started = perf_counter()
    game.loop.step(game.state)
    elapsed = (perf_counter() - started) * 1000
    searched = [row for row in trace if row["reason"] == "searched"]
    fallback = [row for row in trace if row["used_fallback"]]
    casts = [row for row in searched if row["action"]["kind"] == "AbilityAction"]
    return {"scope": "Live engine, fixed public recipe and fixed opponent; not general play or a strength benchmark.",
            "seed": seed, "iterations": iterations, "provider": provider_kind,
            "population_size": population_size if provider_kind == "incremental" else None, "elapsed_ms": elapsed,
            "terminal": game.state.is_game_over, "opponent_lost": game.state.players[1].has_lost,
            "searched_decisions": len(searched), "fallback_decisions": len(fallback),
            "passed": game.state.is_game_over and game.state.players[1].has_lost and len(casts) == 2 and not fallback,
            "trace": trace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=16)
    parser.add_argument("--provider", choices=("fixed", "history", "incremental"), default="fixed")
    parser.add_argument("--population-size", type=int, default=4)
    args = parser.parse_args()
    result = run(seed=args.seed, iterations=args.iterations, provider_kind=args.provider, population_size=args.population_size)
    directory = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("live-search-agent-%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    (directory / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{'PASS' if result['passed'] else 'MISS'}: {result['searched_decisions']} searched decisions, "
          f"{result['fallback_decisions']} fallbacks, {result['elapsed_ms']:.1f} ms\n{directory}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
