import argparse
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


def main() -> None:
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

    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--max-decisions", type=int, default=10000)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/tournament"),
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

    results = run_tournament(
        args.output,
        players,
        games=args.rounds,
        seed=args.seed,
        max_turns=args.max_turns,
        max_decisions=args.max_decisions,
        log_matches=args.log_matches,
        log_batch=True,
    )

    return int(
        any(
            result.status in {"error", "unsupported"}
            for result in results
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())