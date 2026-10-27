from __future__ import annotations
import argparse
from pathlib import Path

from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.console.demo_game import ConsoleDecisionMaker
from game.simulation.config import make_controller
from game.simulation.match_runner import run_match


AI_CONFIG = {
    "type": "modular",
    "candidates": {
        "target_limit": 24,
        "cost_limit": 4,
        "keep_per_ability": 4,
    },
    "combat": {
        "max_assignments": 512,
    },
}


def main() -> int:
    """!
    @brief Run one interactive human-versus-AI match.
    """
    parser = argparse.ArgumentParser(
        description="Play one match against the current primary AI."
    )

    parser.add_argument(
        "--human-deck",
        choices=tuple(ARENA_STARTERS),
        default="white",
    )
    parser.add_argument(
        "--ai-deck",
        choices=tuple(ARENA_STARTERS),
        default="white",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--starting-player",
        choices=("human", "ai"),
        default="human",
    )

    parser.add_argument(
        "--max-turns",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--max-decisions",
        type=int,
        default=10000,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    parser.add_argument("--ai-agent", choices=("modular", "mcts"), default="modular")
    parser.add_argument("--mcts-known-decks", action="store_true",
                        help="Explicitly let experimental MCTS know both starting deck lists (not hands/order).")
    parser.add_argument("--mcts-settings", type=Path,
                        help="JSON object of MCTS settings; see docs/AI_SEARCH.md.")
    parser.add_argument('--mcts-workers', type=int,
                        help='Override parallel_workers from the MCTS JSON (1 = serial).')
    parser.add_argument('--mcts-parallel-backend', choices=('process', 'thread'),
                        help='Override parallel_backend; processes can use multiple CPU cores.')
    parser.add_argument('--mcts-checkpoint', type=Path,
                        help='Load trained policy/value checkpoint (UCT uses value only).')

    args = parser.parse_args()
    if args.ai_agent == "mcts" and not args.mcts_known_decks:
        parser.error("--ai-agent mcts requires --mcts-known-decks: both deck lists are an explicit model assumption")
    if args.ai_agent != "mcts" and (args.mcts_known_decks or args.mcts_settings
                                  or args.mcts_workers is not None or args.mcts_parallel_backend or args.mcts_checkpoint):
        parser.error("MCTS flags require --ai-agent mcts")
    if args.mcts_workers is not None and not 1 <= args.mcts_workers <= 61:
        parser.error('--mcts-workers must be in [1, 61]')

    human_deck = load_arena_starter(args.human_deck)
    ai_deck = load_arena_starter(args.ai_deck)

    # Adjust this line only if ConsoleDecisionMaker requires constructor arguments.
    human = ConsoleDecisionMaker()

    starting_player = (
        0
        if args.starting_player == "human"
        else 1
    )
    ai_name = "MCTS" if args.ai_agent == "mcts" else "Modular"
    ai_config = AI_CONFIG
    origin = None
    if args.ai_agent == "mcts":
        import json
        from game.ai.game_origin import GameOrigin
        from game.cards.catalog import game_catalog
        settings = json.loads(args.mcts_settings.read_text(encoding="utf-8")) if args.mcts_settings else {}
        if not isinstance(settings, dict):
            parser.error("--mcts-settings must contain a JSON object")
        if args.mcts_workers is not None:
            settings['parallel_workers'] = args.mcts_workers
        if args.mcts_parallel_backend:
            settings['parallel_backend'] = args.mcts_parallel_backend
        if args.mcts_checkpoint:
            settings['model_checkpoint'] = str(args.mcts_checkpoint.resolve())
            settings['value_model'] = 'neural'
            settings['prior'] = 'neural' if settings.get('selection','uct') != 'uct' else 'uniform'
        ai_config = {"type": "mcts", "mcts": {"deck_knowledge": "both_lists", **settings}}
        origin = GameOrigin((human_deck, ai_deck), game_catalog,
                            names=("Human", ai_name), starting_player_idx=starting_player)
    ai = make_controller(ai_config, seed=None if args.seed is None else args.seed + 1,
                         max_decisions=args.max_decisions, search_origin=origin)
    if args.ai_agent == "mcts":
        print("Experimental MCTS: knows both starting deck lists; hands and library order remain hidden.")
        def show_search(request, detail):
            if detail["reason"] != "forced_choice":
                metrics = (detail.get("search") or {}).get("metrics", {})
                print(f"MCTS {type(request).__name__}: {detail['reason']}, {detail['elapsed_ms']:.0f} ms; "
                      f"root coverage={metrics.get('root_actions_visited', 0)}/{detail['options']}, "
                      f"completed simulations={metrics.get('iterations_completed', 0)}, "
                      f"engine calls={metrics.get('simulator_advance_calls', 0)}, "
                      f"cached transitions={metrics.get('transition_cache_hits', 0)}, "
                      f"workers={metrics.get('parallel_workers', 1)} {metrics.get('parallel_backend', 'serial')}")
                path = (detail.get("unsupported_detail") or {}).get("reference_path")
                if path:
                    print("  Snapshot reference path: " + " -> ".join(path))
        ai.on_search_decision = show_search

    result = run_match(
        (human_deck.name, ai_deck.name),
        args.output,
        seed=args.seed,
        starting_player=starting_player,
        max_turns=args.max_turns,
        max_decisions=args.max_decisions,
        controllers=(human, ai),
        names=("Human", ai_name),
        decklists=(human_deck, ai_deck),
        metadata={
            "mode": "human_vs_ai",
            "players": [
                {
                    "name": "Human",
                    "deck": human_deck.name,
                    "controller": "console",
                },
                {
                    "name": ai_name,
                    "deck": ai_deck.name,
                    "controller": ai_config,
                },
            ],
        },
    )

    print(
        f"{result.status}, "
        f"winner={result.winner}, "
        f"turns={result.turns}, "
        f"seed={result.seed}"
    )

    if result.error:
        print(result.error)
    for row in result.search_diagnostics:
        print(f"MCTS: {row['searched_decisions']}/{row['search_attempts']} searches usable, "
              f"{row['fallback_decisions']} fallbacks; decision p95={row['decision_ms_p95']:.0f} ms; "
              f"reasons={row['reasons']}")
        print(f"  Search decisions only: median={row['search_decision_ms_median']:.0f} ms, "
              f"p95={row['search_decision_ms_p95']:.0f} ms")

    return int(result.status in {"error", "unsupported"})


if __name__ == "__main__":
    raise SystemExit(main())
