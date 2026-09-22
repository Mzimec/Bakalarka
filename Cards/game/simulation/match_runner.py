"""Reproducible starter match experiments with structured event-time logs."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
from itertools import combinations
import json
import traceback
from time import perf_counter
from collections import Counter
import secrets

from game.reporting.readable_log import render_match
from game.reporting.decision_statistics import DecisionStatistics, format_decision_statistics
from game.game_actions.generation.decision_abstraction.measurement import observe_decisions
from pathlib import Path
from typing import Any

from game.ai.simple_agent import SimpleAgent, DecisionLimitReached
from game.cards.decks import ARENA_STARTERS, load_arena_starter
from game.game_loop.setup import create_game
from game.cards.catalog import game_catalog
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_loop.game_loop import GameLoop


def public_value(value: Any) -> Any:
    """!
    @brief Convert an engine value to a stable JSON-compatible snapshot.

    Runtime objects are represented by public identifiers instead of retaining
    mutable references or process-specific object addresses.

    @param value Value observed at log time.
    @return JSON-compatible frozen representation.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.name
    if hasattr(value, "command_id"):
        return {"id": value.command_id, "name": value.name}
    if hasattr(value, "health") and hasattr(value, "name"):
        return {"player": value.name}
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(public_value(key)): public_value(item) for key, item in value.items() if key != "object_revisions"}
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # Sets need a deterministic order so identical runs produce stable logs.
        return sorted(
            (public_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if hasattr(value, "key"):
        return {"kind": type(value).__name__, "key": value.key}
    return {"kind": type(value).__name__}


@dataclass(frozen=True)
class TournamentPlayer:
    """!
    @brief Immutable tournament participant definition.

    A participant combines one deck with the controller that pilots it.
    A fresh controller instance is created for every match.

    @param name Human-readable participant name.
    @param deck Decklist piloted by the participant.
    @param controller Controller configuration passed to `make_controller`.
    """

    name: str
    deck: Any
    controller: dict


class MatchLog:
    """!
    @brief Append ordered decisions, events and results to one JSON Lines file.
    """

    def __init__(self, path: Path) -> None:
        """!
        @brief Create a new experiment log.

        Opening with mode `x` guarantees an existing run is never overwritten.

        @param path Destination JSONL path.
        """
        self.stream = path.open("x", encoding="utf-8")
        self.sequence = 0

    def write(self, kind: str, **data) -> None:
        """!
        @brief Append one sequenced log record and flush it immediately.

        @param kind Record category.
        @param data Structured record payload.
        """
        self.sequence += 1
        row = {"seq": self.sequence, "kind": kind, **public_value(data)}
        self.stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self.stream.flush()

    def decision(self, state, player, kind, details) -> None:
        """!
        @brief Record one controller decision with its turn/phase context.
        """
        self.write(
            "decision",
            turn=state.turn.number,
            phase=state.turn.phase,
            player=player,
            decision=kind,
            details=details,
        )

    def close(self) -> None:
        """! 
        @brief Close the underlying JSONL stream.
        """
        self.stream.close()


class NullMatchLog:
    """!
    @brief Drop-in match logger that discards all records.
    """

    def write(self, kind: str, **data) -> None:
        pass

    def decision(self, state, player, kind, details) -> None:
        pass

    def close(self) -> None:
        pass


class LoggedEventBus(EventBus):
    """!
    @brief Event bus that records events and detected triggers at observation time.
    """

    def __init__(self, log: MatchLog, state) -> None:
        super().__init__()
        self.log, self.state = log, state

    def collect_trigger_abilities(self, state, events):
        """!
        @brief Collect triggers normally and record each detected trigger.
        """
        triggers = super().collect_trigger_abilities(state, events)

        for trigger in triggers:
            self.log.write(
                "trigger_detected",
                turn=state.turn.number,
                phase=state.turn.phase,
                source=trigger.source,
                controller=trigger.controller,
                ability=trigger.key,
            )

        return triggers

    def emit(self, event, state=None):
        """!
        @brief Emit an event through the normal bus and record its captured form.
        """
        result = super().emit(event, state)

        self.log.write(
            "event",
            turn=self.state.turn.number,
            phase=self.state.turn.phase,
            event=result.key,
            source=result.source,
            controller=result.controller,
            payload=result.payload,
        )

        return result


class LoggedResolutionEngine(ResolutionEngine):
    """!
    @brief Resolution engine that records non-cost resolution outcomes.
    """

    def resolve(self, state, resolution):
        """!
        @brief Resolve normally and log the resulting success state.
        """
        result = super().resolve(state, resolution)
        context = getattr(resolution, "action_resolution", resolution).context

        # Cost resolutions are implementation details of their enclosing action;
        # recording only non-cost resolutions keeps the experiment log readable.
        if not context.is_cost:
            self._event_bus.log.write(
                "resolution",
                turn=state.turn.number,
                phase=state.turn.phase,
                source=context.source,
                controller=context.controller,
                ability=context.ability,
                success=result.success,
            )

        return result


class LoggedProcessor(ActionProcessor):
    """!
    @brief Action processor that records both accepted and rejected actions.
    """

    def process(self, state, action):
        """!
        @brief Process an action normally and persist its aggregate outcome.
        """
        results = super().process(state, action)

        self.executor._event_bus.log.write(
            "action_result",
            turn=state.turn.number,
            phase=state.turn.phase,
            success=all(result.success for result in results),
            errors=[str(result.error) for result in results if not result.success],
        )

        return results


@dataclass(frozen=True)
class MatchResult:
    """!
    @brief Final outcome and experiment metadata for one match.

    Limits, unsupported decks and runtime errors are distinct from game draws.
    """

    colors: tuple[str, str]
    seed: int
    starting_player: int
    status: str
    winner: str | None
    winner_index: int | None
    turns: int
    decisions: int
    log: str | None
    error: str | None = None
    elapsed_seconds: float = 0.0
    decision_timing: list[dict] = field(default_factory=list)


def run_match(
    colors,
    output: Path | None,
    *,
    seed: int | None = None,
    starting_player=0,
    max_turns=100,
    max_decisions=10000,
    controllers=None,
    names=None,
    decklists=None,
    metadata=None,
) -> MatchResult:
    """!
    @brief Run one reproducible duel between two decks and controllers.

    The match can use either starter-deck names or explicitly supplied decklists.
    Custom controllers may be provided; otherwise two `SimpleAgent` instances are
    created. When an output path is supplied, the match writes a structured JSONL
    log and derives a human-readable TXT log from it. When output is `None`, the
    match runs without persistent per-match log files.

    @param colors Two deck labels used for identification and fallback starter-deck
                loading when `decklists` is not supplied.
    @param output Optional new JSONL destination. When `None`, no persistent match
                log files are created. Existing experiment logs are not replaced.
    @param seed Deterministic setup seed. When omitted, a random seed is generated.
    @param starting_player Index of the player that starts the match.
    @param max_turns Turn safety limit.
    @param max_decisions Decision safety limit used by controllers that support it.
    @param controllers Optional pair of controller instances. When omitted, two
                    `SimpleAgent` instances are created.
    @param names Optional pair of player names.
    @param decklists Optional explicit pair of decklists. When omitted, decks are
                    loaded from `colors` using `ARENA_STARTERS`.
    @param metadata Optional experiment metadata recorded in the match log header.
    @return Match result containing outcome, seed, winner, limits and timing data.
    """
    if seed is None:
        seed = secrets.randbits(31)
    if len(colors) != 2 or (decklists is None and any(color not in ARENA_STARTERS for color in colors)):
        raise ValueError("Choose two starter colors.")
    if starting_player not in (0, 1) or min(max_turns, max_decisions) < 1:
        raise ValueError("Invalid starting player or run limits.")
    if controllers is not None and len(controllers) != 2:
        raise ValueError("A duel needs two controllers.")
    if output is not None and output.with_suffix(".txt").exists():
        raise FileExistsError(output.with_suffix(".txt"))

    if decklists is not None and len(decklists) != 2:
        raise ValueError("A duel needs two decklists.")

    log = MatchLog(output) if output is not None else NullMatchLog()
    started = perf_counter()
    agents = controllers or tuple(SimpleAgent(max_decisions=max_decisions) for _ in range(2))

    previous_logs = []
    for agent in agents:
        if isinstance(agent, SimpleAgent) and not any(agent is prior for prior, _ in previous_logs):
            previous_logs.append((agent, agent.log))
            agent.log = log.decision

    decision_statistics = DecisionStatistics()

    def record_decision(agent, request, metrics):
        player = request.player
        decision_statistics.record(player.idx, player.name, type(agent).__name__, agent.decision_mode, metrics)
        log.write(
            "decision_timing", player=player.name, player_index=player.idx,
            agent=type(agent).__name__, mode=agent.decision_mode,
            turn=request.state.turn.number if request.state is not None else 0,
            phase=request.state.turn.phase if request.state is not None else "SETUP",
            **metrics,
        )

    state = None
    status, winner, winner_index, error = "error", None, None, None

    log.write(
        "match",
        colors=colors,
        seed=seed,
        starting_player=starting_player,
        max_turns=max_turns,
        max_decisions=max_decisions,
        agent=[type(agent).__name__ for agent in agents],
        metadata=metadata,
        decklists=[{"name": d.name, "cards": d.cards} for d in decklists] if decklists is not None else None,
    )

    with observe_decisions(record_decision):
        try:
            decks = tuple(decklists) if decklists is not None else tuple(load_arena_starter(color) for color in colors)
            catalog = game_catalog()

            # Refuse to start an experiment whose deck contains cards not represented
            # by the current executable card catalog.
            missing = sorted({name for deck in decks for name, _ in deck.cards if name not in catalog})

            if missing:
                status, error = "unsupported", "Missing card definitions: " + ", ".join(missing)

            else:
                state = create_game(
                    decks,
                    agents,
                    catalog,
                    seed=seed,
                    starting_player_idx=starting_player,
                    names=names or (f"{colors[0]}-0", f"{colors[1]}-1"),
                )

                bus = LoggedEventBus(log, state)

                from game.console.demo_game import ConsoleDecisionMaker

                for agent in agents:
                    if isinstance(agent, ConsoleDecisionMaker):
                        agent.event_bus = bus
                        agent._event_index = 0

                engine = LoggedResolutionEngine(OperationExecutor(), bus)
                loop = GameLoop(None, LoggedProcessor(engine))

                log.write(
                    "setup",
                    hands={p.name: list(p.hand.values()) for p in state.players},
                    mulligans=state.mulligans_taken,
                )

                while not state.is_game_over and state.turn.number <= max_turns:
                    log.write(
                        "phase",
                        turn=state.turn.number,
                        phase=state.turn.phase,
                        active=state.active_player,
                    )

                    loop.step(state)

                    # State summaries are snapshots after a complete loop step rather
                    # than references to mutable player objects.
                    log.write(
                        "state",
                        turn=state.turn.number,
                        phase=state.turn.phase,
                        players=[
                            {
                                "name": p.name,
                                "life": p.health,
                                "hand_size": len(p.hand),
                                "library_size": len(p.deck),
                            }
                            for p in state.players
                        ],
                    )

                survivors = list(state.active_players)

                if not state.is_game_over:
                    status = "turn_limit"
                elif len(survivors) == 1:
                    winner_player = survivors[0]
                    winner_index = state.players.index(winner_player)
                    status, winner = "win", winner_player.name
                else:
                    status = "draw"

        except (EOFError, KeyboardInterrupt):
            status = "aborted"

        except DecisionLimitReached as exc:
            status, error = "decision_limit", str(exc)

        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
            log.write("exception", traceback=traceback.format_exc())
        finally:
            for agent, previous_log in previous_logs:
                agent.log = previous_log

    result = MatchResult(
        tuple(colors),
        seed,
        starting_player,
        status,
        winner,
        winner_index,
        state.turn.number if state else 0,
        sum(getattr(a, "decisions", 0) for a in agents),
        output.name if output is not None else None,
        error,
        perf_counter() - started,
        decision_timing=decision_statistics.summary(),
    )

    log.write("result", **asdict(result))
    log.close()

    if output is not None:
        # Human-readable rendering is derived from the authoritative JSONL record.
        render_match(output)

    return result


def _safe_rate(numerator: int, denominator: int) -> float | None:
    """!
    @brief Return a ratio or None when the denominator is zero.
    """
    return numerator / denominator if denominator else None


def _finalize_participant_stats(stats: dict) -> None:
    """!
    @brief Convert accumulated participant counters into final statistics.
    """
    games = stats["games"]
    decisive = stats["wins"] + stats["losses"]

    stats["win_rate"] = _safe_rate(stats["wins"], games)
    stats["decisive_win_rate"] = _safe_rate(stats["wins"], decisive)
    stats["average_turns"] = _safe_rate(stats.pop("turns_total"), games)
    stats["average_decisions"] = _safe_rate(stats.pop("decisions_total"), games)

    for split_name in ("when_starting", "when_second"):
        split = stats[split_name]
        split_games = split["games"]
        split_decisive = split["wins"] + split["losses"]

        split["win_rate"] = _safe_rate(split["wins"], split_games)
        split["decisive_win_rate"] = _safe_rate(split["wins"], split_decisive)
        split["average_turns"] = _safe_rate(
            split.pop("turns_total"),
            split_games,
        )
        split["average_decisions"] = _safe_rate(
            split.pop("decisions_total"),
            split_games,
        )


def summarize_tournament(
    results: list[MatchResult],
    *,
    players: tuple[TournamentPlayer, TournamentPlayer],
    games,
    seed,
    max_turns,
    max_decisions,
) -> dict:
    """!
    @brief Build aggregate tournament statistics from individual match results.
    """
    participants = []
    decision_statistics = DecisionStatistics()

    for index, player in enumerate(players):
        participants.append(
            {
                "index": index,
                "name": player.name,
                "deck": player.deck.name,
                "controller": player.controller,
                "games": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "turn_limits": 0,
                "decision_limits": 0,
                "errors": 0,
                "turns_total": 0,
                "decisions_total": 0,
                "when_starting": {
                    "games": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "turns_total": 0,
                    "decisions_total": 0,
                },
                "when_second": {
                    "games": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "turns_total": 0,
                    "decisions_total": 0,
                },
            }
        )

    for result in results:
        decision_statistics.merge(result.decision_timing)
        for player_index, stats in enumerate(participants):
            stats["games"] += 1
            stats["turns_total"] += result.turns
            stats["decisions_total"] += result.decisions

            split = (
                stats["when_starting"]
                if player_index == result.starting_player
                else stats["when_second"]
            )

            split["games"] += 1
            split["turns_total"] += result.turns
            split["decisions_total"] += result.decisions

            if result.status == "win":
                if result.winner_index == player_index:
                    stats["wins"] += 1
                    split["wins"] += 1
                else:
                    stats["losses"] += 1
                    split["losses"] += 1

            elif result.status == "draw":
                stats["draws"] += 1
                split["draws"] += 1

            elif result.status == "turn_limit":
                stats["turn_limits"] += 1

            elif result.status == "decision_limit":
                stats["decision_limits"] += 1

            elif result.status == "error":
                stats["errors"] += 1

    timing = decision_statistics.summary()
    for stats in participants:
        stats["decision_timing"] = [entry for entry in timing if entry["player_index"] == stats["index"]]
        _finalize_participant_stats(stats)

    statuses = Counter(result.status for result in results)

    completed = [
        result
        for result in results
        if result.status in {"win", "draw"}
    ]

    decisive = [
        result
        for result in results
        if result.status == "win"
    ]

    starting_player_wins = sum(
        result.winner_index == result.starting_player
        for result in decisive
    )

    global_stats = {
        "decision_timing": timing,
        "games": len(results),
        "completed_games": len(completed),
        "decisive_games": len(decisive),
        "draws": statuses["draw"],

        "average_turns": (
            sum(result.turns for result in results) / len(results)
            if results
            else None
        ),

        "min_turns": (
            min(result.turns for result in results)
            if results
            else None
        ),

        "max_turns": (
            max(result.turns for result in results)
            if results
            else None
        ),

        "average_decisions": (
            sum(result.decisions for result in results) / len(results)
            if results
            else None
        ),

        "average_elapsed_seconds": (
            sum(result.elapsed_seconds for result in results) / len(results)
            if results
            else None
        ),

        "total_elapsed_seconds": sum(
            result.elapsed_seconds for result in results
        ),

        "starting_player": {
            "wins": starting_player_wins,
            "losses": len(decisive) - starting_player_wins,
            "draws": statuses["draw"],
            "win_rate": _safe_rate(
                starting_player_wins,
                len(completed),
            ),
            "decisive_win_rate": _safe_rate(
                starting_player_wins,
                len(decisive),
            ),
        },

        "statuses": {
            "win": statuses["win"],
            "draw": statuses["draw"],
            "turn_limit": statuses["turn_limit"],
            "decision_limit": statuses["decision_limit"],
            "unsupported": statuses["unsupported"],
            "aborted": statuses["aborted"],
            "error": statuses["error"],
        },
    }

    return {
        "tournament": {
            "games_per_starting_position": games,
            "total_games": len(results),
            "base_seed": seed,
            "max_turns": max_turns,
            "max_decisions": max_decisions,
        },
        "participants": participants,
        "global": global_stats,
    }

def _file_name(value: str) -> str:
    return value.lower().replace(" ", "-")

def run_tournament(
    output: Path,
    players: tuple[TournamentPlayer, TournamentPlayer],
    *,
    games=1,
    seed:int | None = None,
    max_turns=100,
    max_decisions=10000,
    log_matches=True,
    log_batch=True,
):
    """!
    @brief Run repeated matches between two tournament participants.

    Each seed is played twice, once with each participant starting. A fresh
    controller instance is created for every match.

    @param output Directory for match logs and tournament summaries.
    @param players Two tournament participants containing deck and controller config.
    @param games Number of seeds to evaluate.
    @param seed Base seed. A random seed is generated when omitted.
    @param max_turns Per-match turn limit.
    @param max_decisions Per-agent decision limit.
    @param log_matches Whether to create JSONL/TXT logs for individual matches.
    @param log_batch Whether to create aggregate tournament summaries.
    @return List of individual match results.
    """
    if len(players) != 2:
        raise ValueError("A tournament requires exactly two players.")

    if min(games, max_turns, max_decisions) < 1:
        raise ValueError("Game counts and run limits must be positive.")

    if seed is None:
        seed = secrets.randbits(31)

    if log_matches or log_batch:
        output.mkdir(parents=True, exist_ok=False)

    from game.simulation.config import make_controller
    
    results = []

    for repeat in range(games):
        match_seed = seed + repeat

        for starting_player in (0, 1):
            controllers = tuple(
                make_controller(
                    player.controller,
                    seed=match_seed + index,
                    max_decisions=max_decisions,
                )
                for index, player in enumerate(players)
            )

            path = (
                output
                / (
                    f"{_file_name(players[0].name)}-vs-{_file_name(players[1].name)}"
                    f"-seed{match_seed}"
                    f"-start{starting_player}.jsonl"
                )
                if log_matches
                else None
            )

            results.append(
                run_match(
                    tuple(player.deck.name for player in players),
                    path,
                    seed=match_seed,
                    starting_player=starting_player,
                    max_turns=max_turns,
                    max_decisions=max_decisions,
                    controllers=controllers,
                    names=tuple(player.name for player in players),
                    decklists=tuple(player.deck for player in players),
                    metadata={
                        "players": [
                            {
                                "index": index,
                                "name": player.name,
                                "deck": player.deck.name,
                                "controller": player.controller,
                            }
                            for index, player in enumerate(players)
                        ],
                    },
                )
            )

    if log_batch:
        summary = summarize_tournament(
            results,
            players=players,
            games=games,
            seed=seed,
            max_turns=max_turns,
            max_decisions=max_decisions,
        )

        (output / "summary.json").write_text(
            json.dumps(
                summary,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        lines = [
            "=== Tournament Summary ===",
            "",
            f"Games: {summary['global']['games']}",
            f"Completed: {summary['global']['completed_games']}",
            f"Decisive: {summary['global']['decisive_games']}",
            f"Draws: {summary['global']['draws']}",
            f"Average turns: {summary['global']['average_turns']:.2f}",
            f"Average decisions: {summary['global']['average_decisions']:.2f}",
            f"Total elapsed: {summary['global']['total_elapsed_seconds']:.3f} s",
            "",
            "Starting player:",
            (
                "  Win rate: "
                f"{summary['global']['starting_player']['win_rate']:.2%}"
                if summary["global"]["starting_player"]["win_rate"] is not None
                else "  Win rate: -"
            ),
            (
                "  Decisive win rate: "
                f"{summary['global']['starting_player']['decisive_win_rate']:.2%}"
                if summary["global"]["starting_player"]["decisive_win_rate"] is not None
                else "  Decisive win rate: -"
            ),
            "",
            "Participants:",
        ]

        for stats in summary["participants"]:
            lines.extend(
                [
                    "",
                    f"{stats['name']} ({stats['deck']})",
                    f"  Controller: {stats['controller']}",
                    f"  Games: {stats['games']}",
                    f"  Wins: {stats['wins']}",
                    f"  Losses: {stats['losses']}",
                    f"  Draws: {stats['draws']}",
                    (
                        f"  Win rate: {stats['win_rate']:.2%}"
                        if stats["win_rate"] is not None
                        else "  Win rate: -"
                    ),
                    (
                        f"  Decisive win rate: {stats['decisive_win_rate']:.2%}"
                        if stats["decisive_win_rate"] is not None
                        else "  Decisive win rate: -"
                    ),
                    (
                        f"  WR when starting: {stats['when_starting']['win_rate']:.2%}"
                        if stats["when_starting"]["win_rate"] is not None
                        else "  WR when starting: -"
                    ),
                    (
                        f"  WR when second: {stats['when_second']['win_rate']:.2%}"
                        if stats["when_second"]["win_rate"] is not None
                        else "  WR when second: -"
                    ),
                    f"  Average turns: {stats['average_turns']:.2f}",
                    f"  Average decisions: {stats['average_decisions']:.2f}",
                ]
            )

        lines.extend(["", *format_decision_statistics(summary["global"]["decision_timing"])])

        (output / "summary.txt").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    return results