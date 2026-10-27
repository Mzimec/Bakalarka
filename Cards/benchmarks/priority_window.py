"""Inspect bounded agent generation at one reproducibly reached priority window."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from functools import wraps
import json
import os
from pathlib import Path
from time import perf_counter

from benchmarks.component_benchmark import configure_profiler
from benchmarks.environment import collect_environment
from benchmarks.profiler import ComponentProfiler
from game.ai.modular_agent import ModularAgent, CandidateGenerator, observation
from game.ai.simple_agent import SimpleAgent
from game.enums import TurnPhase
from game.simulation.match_runner import run_match


class WindowAgent(ModularAgent):
    """Stop before generation; EOFError follows the runner's normal abort path."""

    def __init__(self, turn, phase, window):
        super().__init__()
        self.capture_turn, self.capture_phase, self.capture_window = turn, phase, window
        self.windows_seen = 0
        self.captured = None

    def decide_priority(self, request):
        state = request.state
        if (state.turn.number >= self.capture_turn and state.turn.phase == self.capture_phase
                and state.active_player is request.player and not state.stack.items):
            self.windows_seen += 1
            if self.windows_seen == self.capture_window:
                self.captured = request
                raise EOFError("Priority diagnostic captured the requested window")
        return super().decide_priority(request)


def _iterator_probe(profiler, owner, method, label):
    original = getattr(owner, method)

    @wraps(original)
    def wrapped(*args, **kwargs):
        name = label(*args, **kwargs) if callable(label) else label
        return profiler.profile_iterator(original(*args, **kwargs), name)

    profiler.patch_replacement(owner, method, wrapped)


def diagnose(request, agent):
    """No action is executed. The second, profiled pass uses warmed caches.

This measures CandidateGenerator, including ranking, not autopass preparation,
selector/LLM calls or execution. Profiles and descriptions are debug-only.
"""
    from game.game_actions.generation.decision_abstraction.measurement import observe_decisions
    with observe_decisions(None, enabled=False):
        return _diagnose(request, agent)


def _diagnose(request, agent):
    from game.game_actions.generation.decision_abstraction.action_pipelines import (
        PriorityDecisionGenerationPipeline, AbilityDecisionGenerationPipeline,
    )
    from game.game_actions.generation.exec_plan_gen_pipeline import ExecutionPlanPipeline
    from game.game_actions.generation.subability_generator import FullSubAbilityGenerator
    from game.game_actions.generation.action_node_option_generator import FullActionNodeOptionGenerator
    from game.game_actions.generation.target_binding_generator import FullTargetBindingGenerator
    from game.game_actions.generation.plan_validation import ExecutionPlanValidator
    from game.game_state.collectors.priority_ability_collector import PriorityAbilityCollector

    state, player = request.state, request.player
    window = (state.turn.number, state.turn.phase)
    attempted = set(agent._attempted) if agent._window == window else set()
    generator = agent.candidates
    start = perf_counter()
    first = generator.generate(state, player, attempted)
    first_ms = (perf_counter() - start) * 1000
    raw, scored, abilities = [], {}, {}
    profiler = ComponentProfiler()
    with profiler:
        configure_profiler(profiler)
        for owner, method, label in (
            (FullSubAbilityGenerator, "generate", "stage.subabilities"),
            (FullActionNodeOptionGenerator, "generate", "stage.node_options"),
            (FullTargetBindingGenerator, "generate", "stage.target_bindings"),
            (PriorityAbilityCollector, "collect_lands", "stage.collect_lands"),
            (PriorityAbilityCollector, "collect_abilities", "stage.collect_abilities"),
        ):
            _iterator_probe(profiler, owner, method, label)
        _iterator_probe(
            profiler, ExecutionPlanPipeline, "generate",
            lambda owner, ctx, subability, strategy, state:
                ("stage.cost_plans" if strategy.mana_solver is not None else "stage.effect_plans")
                + (".mana_ability" if ctx.ability.definition.is_mana_ability else ""),
        )
        profiler.patch_method(ExecutionPlanValidator, "validation_error", "stage.validate_plan")
        profiler.patch_method(CandidateGenerator, "_ability_candidate", "stage.score_and_describe")

        def ability_label(owner, ability_request, policy):
            ability = ability_request.ability
            name = f"ability.{ability.source.command_id}.{ability.key}"
            abilities[name] = {"source": ability.source.command_id, "name": ability.source.name,
                               "ability": ability.key}
            return name

        _iterator_probe(profiler, AbilityDecisionGenerationPipeline, "generate", ability_label)
        original_priority = PriorityDecisionGenerationPipeline.generate
        original_score = CandidateGenerator._ability_candidate

        def priority(owner, *args, **kwargs):
            for action in original_priority(owner, *args, **kwargs):
                raw.append(action)
                yield action

        def score(owner, *args, **kwargs):
            candidate = original_score(owner, *args, **kwargs)
            scored[id(candidate.action)] = candidate
            return candidate

        profiler.patch_replacement(PriorityDecisionGenerationPipeline, "generate", priority)
        profiler.patch_replacement(CandidateGenerator, "_ability_candidate", score)
        start = perf_counter()
        retained = generator.generate(state, player, attempted)
        profiled_ms = (perf_counter() - start) * 1000

    kept = {id(candidate.action): candidate for candidate in retained}
    actions = []
    for index, action in enumerate(raw):
        candidate = scored.get(id(action)) or kept[id(action)]
        effects = {}
        for side in ("cost_generator", "action_generator"):
            sequence = getattr(getattr(action, side, None), "effects", None)
            effects[side] = [type(binding.effect).__name__ for binding in sequence.sequence] if sequence else []
        actions.append({"index": index, "type": type(action).__name__,
                        "score": candidate.score, "retained": id(action) in kept,
                        "description": candidate.description, "effects": effects})
    components, work = profiler.to_dict(), profiler.work_to_dict()
    for name, row in abilities.items():
        row.update(components.get(name, {}))
        row["actions"] = work.get(f"{name}.yields", 0)
        row["streams"] = work.get(f"{name}.streams", 0)
    unchanged = ([(c.score, c.description) for c in first]
                 == [(c.score, c.description) for c in retained])
    return {
        "scope": "ModularAgent CandidateGenerator; no selector, autopass preparation or execution",
        "state": observation(state, player),
        "policy": {"cost_limit": generator.cost_limit, "target_limit": generator.target_limit,
                   "keep_per_ability": generator.keep_per_ability,
                   "candidate_limit": agent.candidate_limit, "attempted": sorted(attempted),
                   "include_mana": False, "include_concede": False},
        "first_unprofiled_ms": first_ms, "second_warm_profiled_ms": profiled_ms,
        "same_retained_candidates": unchanged,
        "generated_actions": len(raw), "retained_candidates": len(retained),
        "actions": actions, "abilities": list(abilities.values()),
        "components": components, "work": work,
    }


def render(report):
    state = report["state"]
    lines = ["# Priority window diagnostic", "",
             f"Turn {state['turn']}, {state['phase']}, player {state['self']}; empty stack.", "",
             f"Scope: {report['scope']}.",
             "This is the agent's bounded space, not an enumeration of all legal actions.", "",
             f"First generation without profiler: {report['first_unprofiled_ms']:.3f} ms.",
             f"Second generation, warmed caches with profiler: {report['second_warm_profiled_ms']:.3f} ms.",
             "These are separate passes; profiling adds overhead. Neither executes an action.",
             f"Same retained candidates: {report['same_retained_candidates']}.", "",
             f"Generated actions: {report['generated_actions']}; retained before selector limit: "
             f"{report['retained_candidates']}.", "", "## Position", ""]
    for player in state["players"]:
        lines.append(f"- Player {player['id']}: life {player['life']}, hand {player['hand_size']}, mana {player['mana']}.")
    for zone in ("hand", "battlefield"):
        lines.extend(["", f"### {zone}", ""])
        for card in state[zone]:
            lines.append(f"- {card['id']}: {card['name']} (controller {card['controller']}, "
                         f"cost {card['cost']}, {card['power']}/{card['toughness']}, tapped={card['tapped']})")
    lines.extend(["", "## Expanded abilities (including zero-result attempts)", "",
                  "| Source | Ability | Streams | Actions | Total ms |",
                  "|---|---|---:|---:|---:|"])
    for row in sorted(report["abilities"], key=lambda r: -r.get("total_ms", 0)):
        lines.append(f"| {row['source']} {row['name']} | {row['ability']} | {row['streams']} | "
                     f"{row['actions']} | {row.get('total_ms', 0):.3f} |")
    lines.extend(["", "## Components", "",
                  "Total includes nested components; do not add totals. Calls on iterators count next() advances,",
                  "including exhaustion. Streams/yields below distinguish invocations from results.", "",
                  "Stage counters also include nested mana-ability compilation. Cost/effect plans separate this",
                  "under `.mana_ability`; these are not additional priority actions.", "",
                  "| Component | Calls | Total ms | Exclusive ms |", "|---|---:|---:|---:|"])
    for name, stats in sorted(report["components"].items(), key=lambda item: -item[1]["exclusive_ms"]):
        lines.append(f"| {name} | {stats['calls']} | {stats['total_ms']:.3f} | {stats['exclusive_ms']:.3f} |")
    lines.extend(["", "## All generated actions", "",
                  "Descriptions include source IDs, targets, cost targets, X and payment where applicable.", ""])
    for action in report["actions"]:
        lines.append(f"- #{action['index']}: score={action['score']}, retained={action['retained']}; "
                     f"`{json.dumps(action['description'], ensure_ascii=False)}`; "
                     f"effects: `{json.dumps(action['effects'])}`")
    lines.extend(["", "## Work counters", "", "```json", json.dumps(report["work"], indent=2), "```", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--turn", type=int, default=9, help="Minimum global turn number")
    parser.add_argument("--window", type=int, default=1, help="Nth eligible empty-stack window at/after turn")
    parser.add_argument("--phase", choices=("precombat", "postcombat"), default="precombat")
    parser.add_argument("--decks", nargs=2, default=("white", "white"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.turn < 1 or args.window < 1:
        parser.error("turn and window must be positive")
    phase = TurnPhase.PRECOMBAT_MAIN if args.phase == "precombat" else TurnPhase.POSTCOMBAT_MAIN
    agent = WindowAgent(args.turn, phase, args.window)
    result = run_match(tuple(args.decks), None, seed=args.seed, starting_player=0,
                       controllers=(agent, SimpleAgent()), collect_decision_stats=False)
    if agent.captured is None:
        raise RuntimeError(f"No matching priority window reached: {result.status}, {result.error}")
    report = diagnose(agent.captured, agent)
    root = Path(__file__).resolve().parents[1]
    report["environment"] = collect_environment(root)
    report["configuration"] = {"seed": args.seed, "minimum_turn": args.turn,
                               "window": args.window, "phase": args.phase, "decks": args.decks,
                               "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
                               "setup_result": asdict(result)}
    destination = args.output or root / "runs" / f"priority-window-{datetime.now():%Y%m%d-%H%M%S-%f}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (destination / "report.md").write_text(render(report), encoding="utf-8")
    print(f"{destination}\nGenerated: {report['generated_actions']}; retained: {report['retained_candidates']}; "
          f"unprofiled: {report['first_unprofiled_ms']:.3f} ms; profiled: {report['second_warm_profiled_ms']:.3f} ms")
    if not report["same_retained_candidates"]:
        raise RuntimeError("Instrumented generation changed the retained candidates; inspect report")


if __name__ == "__main__":
    main()
