"""Reproduce a tournament game with disjoint runtime timing categories.

Run with PYTHONPATH=Cards from the repository root. Instrumentation changes only
this process. Nested measured scopes are subtracted, so exclusive totals add up.
"""
import json
import sys
from collections import defaultdict
from functools import wraps
from pathlib import Path
from time import perf_counter_ns

from game.ai.decision_maker import DecisionMaker
from game.ai.simple_agent import SimpleAgent
from game.ai.modular_agent import ModularAgent
from game.simulation.match_runner import run_match
from game.game_actions.resolution import event_bus
from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint, CostTransaction
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.sba_resolver import SBAResolver
from game.game_state import continuous_rules
from game.game_state.registers.indexed_register import IndexedRegister

rows = defaultdict(lambda: {"calls": 0, "exclusive_ns": 0})
stack = []
decision_depth = 0


def timed(fn, label):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        global decision_depth
        is_decision = label == "decisions"
        if is_decision:
            decision_depth += 1
        inside = decision_depth > 0
        frame = [perf_counter_ns(), 0]
        stack.append(frame)
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed = perf_counter_ns() - frame[0]
            stack.pop()
            row = rows[(inside, label)]
            row["calls"] += 1
            row["exclusive_ns"] += elapsed - frame[1]
            if stack:
                stack[-1][1] += elapsed
            if is_decision:
                decision_depth -= 1
    return wrapper


def function(module, name, label):
    original = getattr(module, name)
    wrapper = timed(original, label)
    # Include existing from-import aliases; later imports see the wrapped export.
    for mod in list(sys.modules.values()):
        if mod is None or not getattr(mod, "__name__", "").startswith(("game.", "helper.")):
            continue
        for key, value in list(vars(mod).items()):
            if value is original:
                setattr(mod, key, wrapper)


def method(cls, name, label):
    setattr(cls, name, timed(getattr(cls, name), label))


function(event_bus, "capture_card_information", "card_snapshots")
function(event_bus, "capture_event", "event_matching")
function(event_bus, "capture_trigger_roster", "trigger_rosters")
function(continuous_rules, "refresh_continuous_effects", "continuous_effects")
method(IndexedRegister, "synchronise", "index_updates")
method(RuntimeCheckpoint, "__init__", "rollback_snapshots")
method(CostTransaction, "watch_operations", "rollback_snapshots")
method(OperationExecutor, "execute_batch", "operation_execution")
method(SBAResolver, "resolve", "state_based_actions")
method(DecisionMaker, "decide", "decisions")

result = timed(run_match, "other")(
    ("white", "white"), None, seed=1621711597, max_turns=100,
    controllers=(ModularAgent(), SimpleAgent()),
)
report = {
    "seed": result.seed, "status": result.status, "turns": result.turns,
    "decisions": result.decisions, "elapsed_seconds": result.elapsed_seconds,
    "error": result.error,
    "note": "Instrumented wall time; disjoint exclusive scopes; no match log files.",
    "categories": [
        {"inside_decision": inside, "category": label, **values,
         "seconds": values["exclusive_ns"] / 1e9}
        for (inside, label), values in sorted(rows.items(), key=lambda item: -item[1]["exclusive_ns"])
    ],
}
Path(__file__).with_name("runtime-breakdown.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
