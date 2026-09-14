import argparse
from pathlib import Path

from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.simulation.config import make_controller
from game.simulation.match_runner import run_match


AGENT_CONFIGS = {
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


def main() -> int:
    """!
    @brief Run one configurable AI-versus-AI match.
    """
    parser = argparse.ArgumentParser(
        description="Run one configurable AI-versus-AI match."
    )

    parser.add_argument(
        "--agent1",
        choices=AGENT_CONFIGS,
        default="modular",
    )
    parser.add_argument(
        "--deck1",
        choices=tuple(ARENA_STARTERS),
        default="white",
    )

    parser.add_argument(
        "--agent2",
        choices=AGENT_CONFIGS,
        default="simple",
    )
    parser.add_argument(
        "--deck2",
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
        type=int,
        choices=(0, 1),
        default=0,
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
        default=Path("runs/match.jsonl"),
        help="Optional JSONL match log.",
    )

    args = parser.parse_args()

    deck1 = load_arena_starter(args.deck1)
    deck2 = load_arena_starter(args.deck2)

    controllers = (
        make_controller(
            AGENT_CONFIGS[args.agent1],
            seed=args.seed,
            max_decisions=args.max_decisions,
        ),
        make_controller(
            AGENT_CONFIGS[args.agent2],
            seed=None if args.seed is None else args.seed + 1,
            max_decisions=args.max_decisions,
        ),
    )

    result = run_match(
        (deck1.name, deck2.name),
        args.output,
        seed=args.seed,
        starting_player=args.starting_player,
        max_turns=args.max_turns,
        max_decisions=args.max_decisions,
        controllers=controllers,
        names=(args.agent1, args.agent2),
        decklists=(deck1, deck2),
        metadata={
            "players": [
                {
                    "name": args.agent1,
                    "deck": deck1.name,
                    "controller": AGENT_CONFIGS[args.agent1],
                },
                {
                    "name": args.agent2,
                    "deck": deck2.name,
                    "controller": AGENT_CONFIGS[args.agent2],
                },
            ]
        },
    )

    print(
        f"{result.colors} "
        f"start={result.starting_player} "
        f"seed={result.seed}: "
        f"{result.status}, "
        f"winner={result.winner}, "
        f"turns={result.turns}"
    )

    if result.error:
        print(result.error)

    return int(result.status in {"error", "unsupported"})


if __name__ == "__main__":
    raise SystemExit(main())