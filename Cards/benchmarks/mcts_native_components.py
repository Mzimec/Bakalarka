"""Owned copy and whole combat-declaration timings, RAM and stdout only."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time

from benchmarks.mcts_top_k import make_request, make_case


def copies(repeats):
    """! @brief Alternate both attribute-copy paths on the same native ownership graph. """
    from game.ai.simulation_world import copy_world, immutable_world_values
    from game.ai.knowledge import PlayerKnowledge
    from game.ai.observation import observation
    state = make_request('blockers').state
    constants = immutable_world_values(state)
    memories = (PlayerKnowledge(), PlayerKnowledge())
    reference_state, _ = copy_world(state, memories, immutable_values=constants)
    reference = observation(reference_state, reference_state.players[0])
    samples = defaultdict(list)
    for repeat in range(repeats):
        for variant in ('reference', 'components') if repeat % 2 == 0 else ('components', 'reference'):
            start = perf_counter()
            for _ in range(25):
                copied, _ = copy_world(state, memories, immutable_values=constants,
                                       scalar_fields=variant == 'components')
            elapsed = perf_counter() - start
            assert observation(copied, copied.players[0]) == reference
            assert copied.combat.state is copied
            samples[variant].append(elapsed)
    print(json.dumps(dict(kind='owned_copy_25', samples_seconds=samples,
                         medians_seconds={k: median(v) for k, v in samples.items()})), flush=True)


def recorded_copies(repeats, root=87):
    """! @brief Isolate restoration of one real sampled template, including all repairs. """
    from benchmarks.mcts_recorded_roots import capture
    from game.ai.simulation_world import copy_world
    from game.ai.observation import observation
    captures = capture(Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl', [root])
    simulator = captures[root][1](123)
    simulator.start()
    template = simulator.factory
    reference = None
    for repeat in range(repeats):
        for fast in (False, True) if repeat % 2 == 0 else (True, False):
            start, cpu = perf_counter(), process_time()
            for _ in range(25):
                state, _ = copy_world(template.state, template.knowledge,
                    immutable_values=template.immutable_values, scalar_fields=fast)
            elapsed, cpu_elapsed = perf_counter() - start, process_time() - cpu
            view = observation(state, state.players[0])
            reference = reference or view
            assert view == reference
            print(json.dumps(dict(kind='recorded_root_copy', root=root, fast=fast, repeat=repeat,
                                  seconds=elapsed, cpu_seconds=cpu_elapsed)), flush=True)


def declaration(case, sequential, iterations):
    """! @brief Time one complete live declaration, including every prefix callback. """
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.decision_maker import DecisionResult
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.mcts_agent import MCTSAgent
    from game.ai.modular_agent import ModularAgent
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.search_simulator import SearchSimulator
    from game.ai.search_descriptors import ChoiceDescriptor
    from game.simulation.search_config import SearchGenerationPolicy
    policy = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
                                    sequential_combat=sequential)
    def factory(seed):
        return SearchSimulator(lambda controllers: make_case(case, controllers)[0], policy_for=policy,
            track_information=True, structured_records=True, projection_cache_size=128)
    chooser = ObservationOpponentPolicy()
    search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
        action_prior=HeuristicActionPrior(chooser), selection='puct', widening_constant=4,
        require_root_coverage=False, force_initial_coverage=False,
        budget=InformationSearchBudget(worlds=2, iterations=iterations, max_depth=4,
                                       max_extensions=8, max_advances=16384))
    actor = MCTSAgent(world_provider=lambda info: factory, policy_for=policy, search=search, fallback=chooser,
                      seed=42, search_setup=False)
    controllers = (actor, ModularAgent()) if case == 'attackers' else (ModularAgent(), actor)
    game, request = make_case(case, controllers)
    rows = []
    actor.on_search_decision = lambda request, detail: rows.append(detail)
    start = perf_counter()
    cpu_start = process_time()
    result = actor.decide(request)
    elapsed = perf_counter() - start
    cpu_seconds = process_time() - cpu_start
    assert type(result) is DecisionResult and isinstance(result.value, request.option_type)
    validator = game.state.combat.validate_attackers if case == 'attackers' else game.state.combat.validate_blockers
    validator(request.player, result.value.declarations)
    # No partial declaration or tap has been committed by this benchmark.
    assert not game.state.combat.blockers
    assert sum(row['used_search'] for row in rows) <= 1
    assert not any(row['used_fallback'] for row in rows)
    assert actor._combat_plan is None and not actor._combat_search_started
    return dict(case=case, sequential=sequential, seconds=elapsed, cpu_seconds=cpu_seconds,
                choice=repr(ChoiceDescriptor.from_option(result.value)),
                summary=actor.search_summary(),
                requests=[dict(kind=r['request_kind'], reason=r['reason'], milliseconds=r['elapsed_ms'],
                               options=r['options'], metrics=(r.get('search') or {}).get('metrics')) for r in rows])


def main():
    """! @brief Emit repeatable ablations without implicit output files. """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--iterations', type=int, default=32)
    parser.add_argument('--copy-only', action='store_true')
    parser.add_argument('--recorded-copy', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error('Counts must be positive')
    copies(args.repeats)
    if args.recorded_copy:
        recorded_copies(args.repeats)
    elif not args.copy_only:
        for case in ('attackers', 'blockers'):
            for repeat in range(args.repeats):
                for sequential in (False, True) if repeat % 2 == 0 else (True, False):
                    print(json.dumps(dict(kind='combat_declaration', repeat=repeat,
                                          **declaration(case, sequential, args.iterations))), flush=True)


if __name__ == '__main__':
    main()
