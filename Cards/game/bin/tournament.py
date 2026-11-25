from __future__ import annotations
import argparse
import json
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.simulation.match_runner import TournamentPlayer, run_tournament


OLLAMA_SETTINGS = Path(__file__).resolve().parents[2] / "configs" / "ollama_play.json"
MCTS_SETTINGS = Path(__file__).resolve().parents[2] / "configs" / "mcts_play_128_native.json"
AGENT_CONFIGS = {
    "trivial": {"type": "trivial"},
    "simple": {
        "type": "simple",
    },
    "modular": {
        "type": "modular",
        "candidates": {
            "target_limit": 24,
            "cost_limit": 4,
            "keep_per_ability": 4,
        },
        "combat": {
            "max_assignments": 512,
        },
    },
}


def main(argv=None) -> int:
    """!
    @brief Run this command-line entry point.
    """
    parser = argparse.ArgumentParser()
    agent_choices = (*AGENT_CONFIGS, "ollama", "mcts")

    parser.add_argument(
        "--agent1",
        choices=agent_choices,
        default="modular"
    )

    parser.add_argument(
        "--deck1",
        choices=tuple(ARENA_STARTERS),
        default="white"
    )

    parser.add_argument(
        "--agent2",
        choices=agent_choices,
        default="simple"
    )

    parser.add_argument(
        "--deck2",
        choices=tuple(ARENA_STARTERS),
        default="white"
    )

    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--llm-settings", type=Path,
                        help="Ollama settings JSON. Defaults to configs/ollama_play.json.")
    parser.add_argument("--mcts-settings", type=Path,
                        help="MCTS settings JSON. Defaults to configs/mcts_play_128_native.json.")
    parser.add_argument("--mcts-known-decks", action="store_true",
                        help="Let MCTS know both starting deck lists. Hands and deck order stay hidden.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--max-decisions", type=int, default=10000)
    parser.add_argument("--no-decision-stats", action="store_true",
                        help="Disable decision timing and option statistics; keep decision limits.")
    parser.add_argument("--profile-functions", action="store_true",
                        help="Save all-function call counts, averages and maxima in function-profile/ (adds substantial timing overhead).")

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional parent directory for tournament output logs; default: memory only.",
    )

    parser.add_argument(
        "--log-matches",
        action="store_true",
    )

    args = parser.parse_args(argv)
    if (args.profile_functions or args.log_matches) and args.output is None:
        parser.error('--profile-functions/--log-matches require an explicit --output directory')

    selected_agents = (args.agent1, args.agent2)
    uses_ollama = "ollama" in selected_agents
    if args.llm_settings is not None and not uses_ollama:
        parser.error("--llm-settings requires an Ollama participant")
    uses_mcts = "mcts" in selected_agents
    if uses_mcts and not args.mcts_known_decks:
        parser.error("MCTS requires --mcts-known-decks")
    if not uses_mcts and (args.mcts_settings is not None or args.mcts_known_decks):
        parser.error("MCTS flags require an MCTS participant")

    agent_configs = AGENT_CONFIGS.copy()
    if uses_mcts:
        from game.simulation.search_config import SearchAgentConfig

        try:
            path = args.mcts_settings or MCTS_SETTINGS
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
            SearchAgentConfig.parse(settings)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        agent_configs["mcts"] = {"type": "mcts", "mcts": settings}

    if uses_ollama:
        from game.ai.agents.llm_policy import make_llm_selector

        try:
            path = args.llm_settings or OLLAMA_SETTINGS
            settings = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(settings, dict) or settings.get("provider", "ollama") != "ollama":
                raise ValueError("Tournament Ollama mode requires an Ollama settings object")
            make_llm_selector(settings).check_model()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(str(exc))
        agent_configs["ollama"] = {**AGENT_CONFIGS["modular"], "type": "llm", "llm": settings}

    players = (
        TournamentPlayer(
            name=args.agent1,
            deck=load_arena_starter(args.deck1),
            controller=agent_configs[args.agent1],
        ),
        TournamentPlayer(
            name=args.agent2,
            deck=load_arena_starter(args.deck2),
            controller=agent_configs[args.agent2],
        ),
    )

    output = args.output / f"tournament-{datetime.now():%Y%m%d-%H%M%S-%f}" if args.output is not None else None
    profiler = None
    if args.profile_functions:
        from benchmarks.function_profile import FunctionProfile
        from benchmarks.environment import collect_environment
        root = Path(__file__).resolve().parents[2]
        profiler = FunctionProfile(root)
        environment = collect_environment(root)
        environment["instrumentation"]["component_profiler_enabled"] = False
        environment["instrumentation"]["function_profiler_enabled"] = True
    with profiler if profiler is not None else nullcontext():
        results = run_tournament(
            output,
            players,
            games=args.rounds,
            seed=args.seed,
            max_turns=args.max_turns,
            max_decisions=args.max_decisions,
            log_matches=args.log_matches,
            log_batch=output is not None,
            collect_decision_stats=not args.no_decision_stats,
        )
    if profiler is not None:
        profiler.write(output / "function-profile", dict(environment=environment,
                       arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}))
        print("Function profiling was enabled: tournament timings include profiler overhead.")
    print(f"Tournament results: {output.resolve()}" if output is not None else f"Completed {len(results)} games (memory only)")

    return int(
        any(
            result.status in {"error", "unsupported"}
            for result in results
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
