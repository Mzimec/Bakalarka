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

    args = parser.parse_args()

    human_deck = load_arena_starter(args.human_deck)
    ai_deck = load_arena_starter(args.ai_deck)

    # Adjust this line only if ConsoleDecisionMaker requires constructor arguments.
    human = ConsoleDecisionMaker()

    ai = make_controller(
        AI_CONFIG,
        seed=None if args.seed is None else args.seed + 1,
        max_decisions=args.max_decisions,
    )

    starting_player = (
        0
        if args.starting_player == "human"
        else 1
    )

    result = run_match(
        (human_deck.name, ai_deck.name),
        args.output,
        seed=args.seed,
        starting_player=starting_player,
        max_turns=args.max_turns,
        max_decisions=args.max_decisions,
        controllers=(human, ai),
        names=("Human", "Modular"),
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
                    "name": "Modular",
                    "deck": ai_deck.name,
                    "controller": AI_CONFIG,
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

    return int(result.status in {"error", "unsupported"})


if __name__ == "__main__":
    raise SystemExit(main())