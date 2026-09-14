"""Render structured match records as a compact, chronological match report."""

import json
from pathlib import Path

from game.cards.decks import ARENA_STARTERS


def describe(value):
    """! @brief Format frozen player and card references without JSON syntax."""
    if value is None:
        return "-"
    if isinstance(value, dict):
        if "name" in value and "id" in value:
            return f"{value['name']} [{value['id']}]"
        if set(value) == {"player"}:
            return describe(value["player"])
        if "key" in value:
            return describe(value["key"])
        return "; ".join(f"{k.replace('_', ' ')}: {describe(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(map(describe, value)) or "none"
    return str(value)


def render_match(source: Path, output: Path | None = None):
    """!
    @brief Convert a completed or interrupted JSONL log to a new text report.
    @return The report path; historical logs have no inferred simulation time.
    """
    output = output or source.with_suffix(".txt")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    header = next(row for row in rows if row["kind"] == "match")
    result = next((row for row in reversed(rows) if row["kind"] == "result"), {})
    colors = header["colors"]
    decks = [f"{ARENA_STARTERS.get(c, (c,))[0]} ({c})" for c in colors]
    elapsed = result.get("elapsed_seconds")
    lines = [
        " vs ".join(decks),
        f"Result: {result.get('status', 'unfinished')} | Winner: {result.get('winner') or '-'}"
        f" | Player turn: {result.get('turns', '?')}",
        (
            f"Elapsed wall time: {elapsed:.3f} s"
            if elapsed is not None
            else "Elapsed wall time: unavailable (not recorded in this historical log)"
        ),
        f"Seed: {header.get('seed')} | Starting player: {header.get('starting_player')}",
        "Player turns are numbered individually. Interactive time includes waiting for input.",
        "",
    ]
    if result.get("error"):
        lines.append("Reason: " + result["error"])
    last_turn = None
    for row in rows:
        kind = row["kind"]
        message = None
        who = describe(row.get("controller", row.get("player")))
        card = describe(row.get("source"))
        if kind == "event":
            event = row["event"]
            payload = row.get("payload", {})
            if event in {"phase_started", "priority_passed"}:
                continue
            if event in {"attackers_declared", "blockers_declared"} and not any(payload.values()):
                continue
            verbs = {
                "spell_cast": "casts",
                "land_played": "plays land",
                "ability_activated": "activates",
                "attacker_declared": "attacks with",
                "blocker_declared": "blocks with",
            }
            if event in verbs:
                message = f"{who} {verbs[event]} {card}"
            else:
                message = f"{event.replace('_', ' ').capitalize()}: {who}; {card}"
            if payload:
                message += " — " + describe(payload)
        elif kind in {"trigger_detected", "resolution"}:
            message = f"{kind.replace('_', ' ').capitalize()}: {who}; {card}; {describe(row.get('ability'))}"
            if "success" in row:
                message += f"; success: {row['success']}"
        elif kind == "decision" and row.get("decision") != "pass":
            message = f"{who} chooses {row['decision']}: {describe(row.get('details'))}"
        elif kind == "action_result" and not row["success"]:
            message = "Action rejected: " + describe(row["errors"])
        if message is not None:
            turn = row.get("turn")
            if turn != last_turn:
                lines.extend(["", f"Turn {turn}"])
                last_turn = turn
            phase = row.get("phase", "").replace("_", " ").lower()
            lines.append(f"  [{phase}] {message}")
    with output.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return output
