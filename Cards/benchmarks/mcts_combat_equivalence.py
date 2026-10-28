"""Combat-role prefix/root/search comparisons; all measurements stay in RAM/stdout."""
import argparse
from collections import defaultdict
import json
from statistics import median
from time import perf_counter

from benchmarks.mcts_top_k import make_request, make_case
from game.ai.combat_equivalence import CombatDeclarationEquivalence
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates
from game.ai.search_decision import DecisionProjector
from game.ai.search_recall import PlayerRecall
from game.ai.search_simulator import SearchSimulator
from game.simulation.search_config import SearchGenerationPolicy


class NoPrefetchModel:
    """Benchmark-only ablation: unchanged generator/identity, disabled root reservoir."""
    combat_equivalence = True

    def __init__(self, base):
        self.base = base

    def __getattr__(self, name):
        return getattr(self.base, name)

    def combat_reservoir_limit(self, request, width, maximum):
        return width


class NoPrefetchPolicy:
    """Expose the ablation to the simulator without changing declaration generation."""
    def __init__(self):
        self.base = SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True)
        self.candidate_model = NoPrefetchModel(self.base.candidate_model)

    def __call__(self, request):
        return self.base(request)


def policy(variant):
    return (NoPrefetchPolicy() if variant == 'native_no_prefetch' else
            SearchGenerationPolicy(progressive_candidates=True, candidate_native_ordering=True,
                                   candidate_combat_equivalence=variant == 'roles'))


def simulator(case, variant):
    return SearchSimulator(lambda controllers: make_case(case, controllers)[0],
        policy_for=policy(variant), track_information=True, structured_records=True,
        projection_cache_size=128)


def preparation(repeats):
    """Separate a raw four-option prefix from a full simulator root/reservoir."""
    for case in ('attackers', 'blockers'):
        request = make_request(case)
        context = CombatDeclarationEquivalence(request, blockers=case == 'blockers')
        classes = tuple(context.representatives())
        samples = defaultdict(list)
        variants = ('native', 'native_no_prefetch', 'roles')
        for repeat in range(repeats + 1):
            for variant in variants if repeat % 2 == 0 else reversed(variants):
                if variant != 'native_no_prefetch':
                    model = DeterministicCandidates(native_ordering=True, combat_equivalence=variant == 'roles')
                    started = perf_counter()
                    frontier = CandidateFrontier(model.generate(request, SearchGenerationPolicy()(request)), 4096)
                    try:
                        options, _ = frontier.take(4)
                    finally:
                        frontier.close()
                    info = DecisionProjector(request, options, PlayerRecall()).build().information
                    assert len(info.actions) == 4
                    elapsed = (perf_counter() - started) * 1000
                    if repeat:
                        samples['prefix_' + variant].append(elapsed)
                sim = simulator(case, variant)
                started = perf_counter()
                node = sim.start()
                elapsed = (perf_counter() - started) * 1000
                assert len(node.choices) == 4
                if repeat:
                    samples['root_' + variant].append(elapsed)
        print(json.dumps(dict(kind='preparation', case=case, repeats=repeats,
            attacker_groups=list(map(len, context.attacker_groups)),
            blocker_groups=list(map(len, context.blocker_groups)),
            role_classes=len(classes), concrete_declarations=sum(c.multiplicity for c in classes),
            medians_ms={k: median(v) for k, v in samples.items()}, samples_ms=samples)), flush=True)


def searches(repeats, iterations):
    """Fixed work budgets, not an assertion of identical paths between role menus."""
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    variants = ('native', 'native_no_prefetch', 'roles')
    for case in ('attackers', 'blockers'):
        rows = defaultdict(list)
        signatures = {}
        for repeat in range(repeats):
            for variant in variants if repeat % 2 == 0 else reversed(variants):
                factory = lambda seed: simulator(case, variant)
                root = factory(0).start().player_information()
                chooser = ObservationOpponentPolicy()
                search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
                    action_prior=HeuristicActionPrior(chooser), selection='puct',
                    compress_forced=True, require_root_coverage=False, force_initial_coverage=False,
                    widening_constant=4, budget=InformationSearchBudget(worlds=2, iterations=iterations,
                        max_depth=4, max_extensions=8, max_advances=4096))
                started = perf_counter()
                result = search.search(factory, root, seed=42)
                seconds = perf_counter() - started
                m = result.metrics
                signature = result.choice, result.estimates, result.value, m.advance_attempts
                if variant in signatures:
                    assert signatures[variant] == signature, 'Deterministic variant changed search work/results'
                signatures[variant] = signature
                row = dict(seconds=seconds, completed=m.iterations_completed, logical_steps=m.advance_attempts,
                    native_calls=m.simulator_advance_calls, candidate_expansions=m.candidate_expansions,
                    fallback=result.used_fallback, value=result.value)
                rows[variant].append(row)
                print(json.dumps(dict(kind='search_sample', case=case, variant=variant, repeat=repeat, **row)), flush=True)
        for variant in variants:
            print(json.dumps(dict(kind='search_summary', case=case, variant=variant,
                median_seconds=median(r['seconds'] for r in rows[variant]), samples=rows[variant],
                no_prefetch_preserves_results_and_logical_work=signatures['native'] == signatures['native_no_prefetch'])), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--iterations', type=int, default=16)
    parser.add_argument('--search', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error('Counts must be positive')
    if args.search:
        searches(args.repeats, args.iterations)
    else:
        preparation(args.repeats)


if __name__ == '__main__':
    main()
