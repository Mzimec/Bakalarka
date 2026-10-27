"""Equal-answer sampled-world copy/search ablation; RAM/stdout only."""
import argparse
import gc
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from unittest.mock import patch

from game.ai.simulation_clone import SimulationClone


@contextmanager
def primitive_reference():
    """Original per-primitive memo path, with the same replacement mapping."""
    original = SimulationClone.__init__
    def initialize(clone, *args, **kwargs):
        original(clone, *args, **kwargs)
        clone._primitive_identity = False
    with patch.object(SimulationClone, '__init__', initialize):
        yield


@contextmanager
def component_reference():
    """Keep the primitive fast path; disable only native component adapters."""
    original = SimulationClone.__init__
    def initialize(clone, *args, **kwargs):
        original(clone, *args, **kwargs)
        clone.component_copies = False
    with patch.object(SimulationClone, '__init__', initialize):
        yield


@contextmanager
def runtime_reference():
    """Previous copier and observation path, keeping primitive optimization."""
    with component_reference():
        yield


@contextmanager
def observation_reference():
    yield


@contextmanager
def mana_projection_experiment():
    from game.ai.observation import PlayerObservationBuilder
    from game.ai.observation_mana import native_mana_projection
    original = PlayerObservationBuilder._mana_values
    def cached(builder, card):
        result = native_mana_projection(card, builder.state)
        return original(builder, card) if result is None else result
    # Plain functions: Mock call histories would retain cards and whole worlds.
    with patch.object(PlayerObservationBuilder, '_mana_values', cached):
        yield


_REFERENCES = dict(primitive=primitive_reference, components=component_reference,
                   observation=observation_reference, runtime=runtime_reference)


def search_probe(*, colors=('red', 'white'), kind='priority', native=False, repeats=4, iterations=16,
                 ablation='primitive'):
    from benchmarks.mcts_parallel import capture
    from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    from game.simulation.search_config import SearchGenerationPolicy
    request_kind = dict(priority=PriorityDecisionRequest, attackers=DeclareAttackersRequest,
                        blockers=DeclareBlockersRequest)[kind]
    recipes, _ = capture(*colors, 42 if colors[0] == 'red' else 43, 9, request_kind=request_kind)
    policy = SearchGenerationPolicy(progressive_candidates=native, candidate_native_ordering=native)
    def factory(seed):
        sim = recipes(seed)
        sim.factory.reuse = True
        sim.policy_for = policy
        return sim
    information = factory(0).start().player_information()
    expected = None
    samples = []
    for repeat in range(repeats):
        for mode in ('reference', 'optimized') if repeat % 2 == 0 else ('optimized', 'reference'):
            chooser = ObservationOpponentPolicy()
            search = InformationSetSearch(selection='puct', action_prior=HeuristicActionPrior(chooser),
                opponent_policy=chooser, rollout_policy=chooser, compress_forced=True,
                require_root_coverage=False, force_initial_coverage=False,
                widening_constant=4 if native else 0,
                budget=InformationSearchBudget(worlds=2, iterations=iterations, max_depth=4,
                                               max_extensions=8, max_advances=4096))
            # Remove unreachable worlds from preceding samples outside timing.
            # Automatic collections remain enabled during every measured search.
            gc.collect()
            reference = _REFERENCES[ablation]
            optimized = mana_projection_experiment if ablation in ('runtime', 'observation') else nullcontext
            with reference() if mode == 'reference' else optimized():
                started, cpu_started = perf_counter(), process_time()
                result = search.search(factory, information, seed=42)
                wall, cpu = perf_counter()-started, process_time()-cpu_started
            m = result.metrics
            actual = (result.choice, result.value, result.estimates, result.stop_reason,
                      result.used_fallback, m.iterations_completed, m.advance_attempts,
                      m.simulator_advance_calls, m.world_restores)
            if expected is None:
                expected = actual
            assert expected == actual
            assert not result.used_fallback and m.iterations_completed == iterations
            samples.append(dict(mode=mode, repeat=repeat, wall_ms=1000*wall, cpu_ms=1000*cpu,
                initialization_ms=m.world_initialization_ms, advance_ms=m.advance_ms,
                iterations=m.iterations_completed, advances=m.advance_attempts, restores=m.world_restores))
    return dict(colors=colors, root=kind, generation='native' if native else 'full', ablation=ablation, all_estimates_equal=True,
        samples=samples, medians={mode:{key:median(s[key] for s in samples if s['mode'] == mode)
            for key in ('wall_ms', 'cpu_ms', 'initialization_ms', 'advance_ms')}
            for mode in ('reference', 'optimized')})


def copy_probe(*, colors=('red', 'white'), repeats=16, ablation='primitive'):
    from benchmarks.mcts_parallel import capture
    from game.ai.simulation_world import copy_world
    from game.ai.runtime_clone import DictionaryCopyPlans
    recipes, _ = capture(*colors, 42 if colors[0] == 'red' else 43, 9)
    sim = recipes(0)
    sim.factory.reuse = True
    sim.start()
    source = sim.factory
    expected = source.state.rng.getstate()
    samples = []
    for repeat in range(repeats):
        for mode in ('reference', 'optimized') if repeat % 2 == 0 else ('optimized', 'reference'):
            for cache in ('cold', 'warm'):
                plans = (DictionaryCopyPlans(source.immutable_values) if cache == 'cold'
                         else source.dictionary_plans)
                gc.collect()
                reference = _REFERENCES[ablation]
                with reference() if mode == 'reference' else nullcontext():
                    started, cpu_started = perf_counter(), process_time()
                    copied, memories = copy_world(source.state, source.knowledge,
                        immutable_values=source.immutable_values, dictionary_plans=plans)
                    wall, cpu = perf_counter()-started, process_time()-cpu_started
                assert copied.rng.getstate() == expected and source.state.rng.getstate() == expected
                assert copied.players[0] is not source.state.players[0]
                samples.append(dict(mode=mode, cache=cache, wall_ms=1000*wall, cpu_ms=1000*cpu))
                del copied, memories
    return dict(colors=colors, repeats=repeats, ablation=ablation, scope='owned copy only; cold/warm dictionary plans',
        medians={cache:{mode:{key:median(s[key] for s in samples if s['cache']==cache and s['mode']==mode)
                     for key in ('wall_ms', 'cpu_ms')} for mode in ('reference','optimized')}
                 for cache in ('cold','warm')}, samples=samples)


def main():
    from benchmarks.environment import source_fingerprint
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--iterations', type=int, default=16)
    parser.add_argument('--colors', nargs=2, default=['red', 'white'])
    parser.add_argument('--roots', nargs='+', choices=('priority', 'attackers', 'blockers'),
                        default=['priority', 'attackers', 'blockers'])
    parser.add_argument('--generation', choices=('full', 'native', 'both'), default='both')
    parser.add_argument('--copy-only', action='store_true', help='Isolate cold/warm owned-world copies')
    parser.add_argument('--ablation', choices=tuple(_REFERENCES), default='primitive')
    args = parser.parse_args()
    if min(args.repeats, args.iterations) < 1:
        parser.error('repeats and iterations must be positive')
    before = source_fingerprint(Path(__file__).resolve().parents[1])
    if args.copy_only:
        print(json.dumps(copy_probe(colors=tuple(args.colors), repeats=args.repeats, ablation=args.ablation)), flush=True)
    else:
        for kind in args.roots:
            for native in (False, True) if args.generation == 'both' else (args.generation == 'native',):
                print(json.dumps(search_probe(colors=tuple(args.colors), kind=kind, native=native,
                    repeats=args.repeats, iterations=args.iterations, ablation=args.ablation)), flush=True)
    print(json.dumps(dict(source=before,
        source_unchanged=before == source_fingerprint(Path(__file__).resolve().parents[1]))), flush=True)


if __name__ == '__main__':
    main()
