"""Live replan after a hidden opposing Counterspell becomes public."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from benchmarks.search_hidden_worlds import public_position, catalog
from benchmarks.search_tactics import tactical_policy
from game.ai.history_world_provider import HistoryWorldProvider
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.sampled_scenario import HiddenZoneBelief, SampledScenario
from game.ai.scenario_world_provider import ScenarioWorldProvider
from game.ai.search_simulator import SearchSimulator


def public_recipe(controllers):
    from game.game_state import Card
    from game.enums import ZoneType, TurnPhase
    game = public_position(controllers)
    player = game.state.players[0]
    player.add_card(Card(catalog()["Mountain"], player), ZoneType.BATTLEFIELD)
    game.state.turn.phase = TurnPhase.END_STEP
    return game


def scenario(seed):
    beliefs = (
        HiddenZoneBelief.from_deck({"Shock": 2, "Island": 2}, hand_size=2, known_hand=("Shock", "Shock")),
        HiddenZoneBelief.from_deck({"Counterspell": 1, "Island": 3}, hand_size=1),
    )
    return SampledScenario(public_recipe, catalog, beliefs, observer=0, seed=seed)


def simulator(seed):
    return SearchSimulator(scenario(seed), policy_for=tactical_policy, track_information=True)


def passing(info):
    return next(a for a in info.actions if a.kind == "PassPriorityAction")


def run(*, seed=42, iterations=12, max_attempts=128, provider_kind="history"):
    # Select this diagnostic's real hidden hand deliberately. The agent gets
    # only the declared 1-in-4 prior, not this fixture seed or actual sample.
    live_recipe = next(recipe for n in range(100) if "Counterspell" in (recipe := scenario(n)).samples[1].hand)
    policy = ObservationOpponentPolicy()
    if provider_kind == "history":
        provider = HistoryWorldProvider(simulator, max_attempts=max_attempts)
    elif provider_kind == "fixed_pass":
        provider = ScenarioWorldProvider(simulator, opponent_policy=passing)
    else:
        raise ValueError("Unknown provider")
    captured = []
    def world_provider(info):
        # The first cast and own pass prepare a reproducible live interaction.
        # Search is tested only after the real opponent casts and passes.
        if len(info.current.observation().get("pending_abilities", ())) >= 2:
            captured.append(info)
            return provider(info)
        return None
    def setup_choice(info):
        if len(info.current.observation().get("pending_abilities", ())) == 1:
            return passing(info)
        return policy(info)
    search = InformationSetSearch(opponent_policy=policy, budget=InformationSearchBudget(
        worlds=2, iterations=iterations, max_depth=2, max_extensions=16, max_advances=iterations * 20))
    trace = []
    class RecordingAgent(MCTSAgent):
        def _decide(self, request):
            result = super()._decide(request)
            trace.append({"player": request.player.idx, "request": type(request).__name__,
                          "action": self.recall.entries[-1].describe(), **result.info["mcts"]})
            return result
    own = RecordingAgent(world_provider=world_provider, search=search, fallback=setup_choice,
                         policy_for=tactical_policy, seed=seed)
    opponent = RecordingAgent(world_provider=lambda info: None, policy_for=tactical_policy)
    game = live_recipe((own, opponent))
    started = perf_counter()
    game.loop.step(game.state)
    elapsed = (perf_counter() - started) * 1000
    rows = [r for r in trace if r["player"] == 0 and r["used_search"]]
    return {"scope": "Scripted own opening; live opponent counter; MCTS response under an explicit hidden-world prior.",
            "provider": provider_kind, "seed": seed, "iterations": iterations,
            "prior_counter_in_hand": .25, "elapsed_ms": elapsed,
            "terminal": game.state.is_game_over, "opponent_lost": game.state.players[1].has_lost,
            "passed": bool(captured) and bool(rows) and all(r["reason"] == "searched" for r in rows)
                      and game.state.is_game_over and game.state.players[1].has_lost,
            "response_searches": len(rows), "response_fallbacks": sum(r["used_fallback"] for r in rows),
            "response_input": captured[0].current.observation() if captured else None, "trace": trace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--max-attempts", type=int, default=128)
    parser.add_argument("--provider", choices=("history", "fixed_pass"), default="history")
    args = parser.parse_args()
    result = run(seed=args.seed, iterations=args.iterations, max_attempts=args.max_attempts, provider_kind=args.provider)
    path = Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("live-history-search-%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True)
    (path / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{'PASS' if result['passed'] else 'MISS'} {args.provider}: {result['response_searches']} response searches, "
          f"{result['response_fallbacks']} response fallbacks, {result['elapsed_ms']:.1f} ms\n{path}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
