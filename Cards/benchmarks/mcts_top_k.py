"""Generation + projection timings; stdout/RAM only, not a strength benchmark."""
import argparse
import json
from statistics import median
from time import perf_counter, process_time
from contextlib import nullcontext
from unittest.mock import patch

from benchmarks.search_tactics import tactical_scenario
from game.ai.generation_candidates import CandidateFrontier, DeterministicCandidates
from game.ai.modular_agent import ModularAgent
from game.ai.search_decision import DecisionProjector
from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_recall import PlayerRecall
from game.cards.starter_cards import starter_catalog
from game.enums import TurnPhase, ZoneType
from game.game_actions.generation.decision_abstraction.requests import (
    DeclareAttackersRequest, DeclareBlockersRequest, PriorityDecisionRequest)
from game.game_state import Card
from game.simulation.search_config import SearchGenerationPolicy


def make_request(case):
    return make_case(case, (ModularAgent(), ModularAgent()))[1]


def make_case(case, controllers):
    """Same known native fixture, optionally runnable by real search controllers."""
    game = tactical_scenario('useful_pacifism' if case == 'targets' else 'attack_pacified')(
        controllers)
    state = game.state
    def add(owner, count):
        cards = []
        for _ in range(count):
            card = Card(starter_catalog()['Impassioned Orator'], state.players[owner])
            card.owner.add_card(card, ZoneType.BATTLEFIELD)
            card.controlled_since = 0
            cards.append(card)
        return cards
    if case == 'targets':
        add(1, 40)
        return game, PriorityDecisionRequest(state, state.players[0])
    if case == 'attackers':
        add(0, 9)
        return game, DeclareAttackersRequest(state, state.players[0])
    attackers = add(0, 5)
    add(1, 4)
    state.combat.declare_attackers(state.players[0], {c: state.players[1] for c in attackers})
    state.turn.phase = TurnPhase.DECLARE_BLOCKERS
    return game, DeclareBlockersRequest(state, state.players[1])


def measure(request, variant, width):
    policy = SearchGenerationPolicy()(request)
    model = DeterministicCandidates(width, 32, 16 if variant == 'ranked' else 0,
                                    native_ordering=variant == 'native')
    started = perf_counter()
    if variant == 'full':
        options, complete = tuple(request.option_space(policy)), True
    else:
        frontier = CandidateFrontier(model.generate(request, policy), 4096)
        try:
            options, complete = frontier.take(width)
        finally:
            frontier.close()
    info = DecisionProjector(request, options, PlayerRecall(),
        candidate_model=None if variant == 'full' else model.identity,
        menu_complete=complete, candidate_width=0 if variant == 'full' else width).build().information
    return dict(seconds=perf_counter()-started, offered=len(info.actions), complete=complete)


def run(repeats=4, width=4):
    for case in ('targets', 'attackers', 'blockers'):
        request = make_request(case)
        native = set(map(ChoiceDescriptor.from_option, request.option_space(SearchGenerationPolicy()(request))))
        model = DeterministicCandidates(width, 32, 16)
        eventual = set(map(ChoiceDescriptor.from_option, model.generate(request, SearchGenerationPolicy()(request))))
        assert eventual == native
        # Warm imports/caches equally; alternate order to expose timing noise.
        variants = ('full', 'prefix', 'ranked', 'native')
        for variant in variants:
            measure(request, variant, width)
        rows = []
        for repeat in range(repeats):
            order = variants if repeat % 2 == 0 else tuple(reversed(variants))
            for variant in order:
                rows.append(dict(variant=variant, **measure(request, variant, width)))
        print(json.dumps(dict(case=case, legal_options=len(native), width=width,
            eventual_legal_set_equal=True, medians_ms={
                variant: 1000*median(row['seconds'] for row in rows if row['variant'] == variant)
                for variant in variants}, samples=rows)), flush=True)


def run_search(repeats=2, iterations=16):
    """Same root/budgets, plus a strict cache-only ablation of identical work."""
    from game.ai.search_simulator import SearchSimulator
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget, _Run
    def recipe(controllers):
        game = tactical_scenario('useful_pacifism')(controllers)
        owner = game.state.players[1]
        definition = starter_catalog()['Impassioned Orator']
        for _ in range(8):
            owner.add_card(Card(definition, owner), ZoneType.BATTLEFIELD)
        return game
    variants = ('full', 'frontier_no_cache', 'ranked_no_cache', 'ranked', 'native')
    rows = []
    signatures = {}
    for repeat in range(repeats):
        for variant in variants if repeat % 2 == 0 else reversed(variants):
            policy = SearchGenerationPolicy(progressive_candidates=variant != 'full',
                candidate_rank_window=16 if variant.startswith('ranked') else 0,
                candidate_native_ordering=variant == 'native')
            sims, builds = [], []
            def world(controllers):
                builds.append(1)
                return recipe(controllers)
            def factory(seed):
                sim = SearchSimulator(world, policy_for=policy, track_information=True,
                    structured_records=True, projection_cache_size=128)
                if variant.endswith('no_cache'):
                    sim._candidate_cache_limit = 0
                sims.append(sim)
                return sim
            root = factory(0).start().player_information()
            initial_builds = len(builds)
            chooser = ObservationOpponentPolicy()
            search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
                selection='puct', action_prior=HeuristicActionPrior(chooser), compress_forced=True,
                require_root_coverage=False, force_initial_coverage=False,
                widening_constant=0 if variant == 'full' else 4,
                budget=InformationSearchBudget(worlds=1, iterations=iterations, max_depth=2,
                    max_extensions=8, max_advances=1024))
            started = perf_counter()
            cpu_started = process_time()
            with patch.object(_Run, 'transition_key', legacy_transition_key) if variant.endswith('no_cache') else nullcontext():
                result = search.search(factory, root, seed=42)
            seconds = perf_counter()-started
            assert not result.used_fallback and result.metrics.iterations_completed == iterations
            signature = (result.choice, result.value, result.estimates, result.stop_reason,
                         result.metrics.advance_attempts)
            if variant in signatures:
                assert signatures[variant] == signature
            signatures[variant] = signature
            row = dict(variant=variant, seconds=seconds, cpu_seconds=process_time()-cpu_started,
                builds=len(builds)-initial_builds,
                expansions=result.metrics.candidate_expansions,
                cache_hits=sum(s.candidate_cache_hits for s in sims),
                completed=result.metrics.iterations_completed, advances=result.metrics.advance_attempts)
            rows.append(row)
            print(json.dumps(dict(kind='search_sample', **row)), flush=True)
    assert signatures['ranked_no_cache'] == signatures['ranked']
    print(json.dumps(dict(kind='search_summary', cache_preserves_all_estimates=True,
        iterations=iterations, samples=rows, medians_ms={variant:
            1000*median(row['seconds'] for row in rows if row['variant'] == variant)
            for variant in variants})), flush=True)


def legacy_transition_key(run, sim, node, action):
    """Pre-fix key, solely for cache/work/latency ablations."""
    from game.ai.search_simulator import SearchSimulator, SimulationNode
    if run.searcher.transition_cache_size and isinstance(sim, SearchSimulator) and isinstance(node, SimulationNode):
        return sim, (node.history, node.decision), action
    return None


def run_captured(color='red', repeats=2, iterations=16):
    from benchmarks.mcts_parallel import capture
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget, _Run
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.ai.action_priors import HeuristicActionPrior
    worlds, _ = capture(color, 'white' if color == 'red' else 'black', 42 if color == 'red' else 43, 9)
    variants = ('full', 'ranked_no_cache', 'native')
    rows = []
    signatures = {}
    for repeat in range(repeats):
        for variant in variants if repeat % 2 == 0 else reversed(variants):
            policy = SearchGenerationPolicy(progressive_candidates=variant != 'full',
                candidate_rank_window=16 if variant == 'ranked_no_cache' else 0,
                candidate_native_ordering=variant == 'native')
            def factory(seed):
                sim = worlds(seed)
                sim.factory.reuse = True
                sim.policy_for = policy
                if variant == 'ranked_no_cache':
                    sim._candidate_cache_limit = 0
                return sim
            info = factory(0).start().player_information()
            chooser = ObservationOpponentPolicy()
            search = InformationSetSearch(opponent_policy=chooser, rollout_policy=chooser,
                selection='puct', action_prior=HeuristicActionPrior(chooser), compress_forced=True,
                require_root_coverage=False, force_initial_coverage=False,
                widening_constant=0 if variant == 'full' else 4,
                budget=InformationSearchBudget(worlds=2, iterations=iterations, max_depth=4,
                                               max_extensions=8, max_advances=4096))
            started = perf_counter()
            cpu_started = process_time()
            with patch.object(_Run, 'transition_key', legacy_transition_key) if variant == 'ranked_no_cache' else nullcontext():
                result = search.search(factory, info, seed=42)
            m = result.metrics
            signature = result.choice, result.value, result.estimates, result.stop_reason, m.advance_attempts
            if variant in signatures:
                assert signatures[variant] == signature
            signatures[variant] = signature
            assert not result.used_fallback and m.iterations_completed == iterations
            row = dict(variant=variant, seconds=perf_counter()-started, cpu_seconds=process_time()-cpu_started,
                completed=m.iterations_completed,
                advances=m.advance_attempts, expansions=m.candidate_expansions, menu_hits=m.candidate_cache_hits,
                restores=m.world_restores, fallback=result.used_fallback, reason=result.fallback_reason,
                choice=result.choice.key, value=result.value)
            rows.append(row)
            print(json.dumps(dict(kind='captured_sample', color=color, **row)), flush=True)
    print(json.dumps(dict(kind='captured_summary', color=color, samples=rows,
        same_estimates_across_variants=all(s == signatures['full'] for s in signatures.values()),
        medians_ms={variant: 1000*median(r['seconds'] for r in rows if r['variant'] == variant)
                    for variant in variants})), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--width', type=int, default=4)
    parser.add_argument('--search', action='store_true', help='Short real-engine search ablations instead of generation')
    parser.add_argument('--iterations', type=int, default=16)
    parser.add_argument('--captured', choices=('red', 'blue'), help='Short captured production-world root comparisons')
    args = parser.parse_args()
    if args.repeats < 1 or args.width < 2 or args.iterations < 1:
        parser.error('repeats and iterations must be positive; width must be at least two')
    if args.captured:
        run_captured(args.captured, args.repeats, args.iterations)
    elif args.search:
        run_search(args.repeats, args.iterations)
    else:
        run(args.repeats, args.width)


if __name__ == '__main__':
    main()
