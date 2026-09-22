import argparse
from datetime import datetime
from pathlib import Path

from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.simulation.match_runner import TournamentPlayer, run_tournament


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
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--agent1",
        choices=AGENT_CONFIGS,
        default="modular"
    )

    parser.add_argument(
        "--deck1",
        choices=tuple(ARENA_STARTERS),
        default="white"
    )

    parser.add_argument(
        "--agent2",
        choices=AGENT_CONFIGS,
        default="simple"
    )

    parser.add_argument(
        "--deck2",
        choices=tuple(ARENA_STARTERS),
        default="white"
    )

    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--max-decisions", type=int, default=10000)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs"),
        help="Parent directory for a new tournament-<timestamp> folder.",
    )

    parser.add_argument(
        "--log-matches",
        action="store_true",
    )

    args = parser.parse_args()

    players = (
        TournamentPlayer(
            name=args.agent1,
            deck=load_arena_starter(args.deck1),
            controller=AGENT_CONFIGS[args.agent1],
        ),
        TournamentPlayer(
            name=args.agent2,
            deck=load_arena_starter(args.deck2),
            controller=AGENT_CONFIGS[args.agent2],
        ),
    )

    output = args.output / f"tournament-{datetime.now():%Y%m%d-%H%M%S-%f}"
    results = run_tournament(
        output,
        players,
        games=args.rounds,
        seed=args.seed,
        max_turns=args.max_turns,
        max_decisions=args.max_decisions,
        log_matches=args.log_matches,
        log_batch=True,
    )
    print(f"Tournament results: {output.resolve()}")

    return int(
        any(
            result.status in {"error", "unsupported"}
            for result in results
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
