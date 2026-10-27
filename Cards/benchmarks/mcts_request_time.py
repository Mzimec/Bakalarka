"""Request, generation and submitted-action costs; short serial runs, RAM/stdout only.

Profiler hooks are installed only inside this benchmark. Inclusive request time
contains search descendants; exclusive submitted-action time excludes nested
decisions/actions. Neither is an additive breakdown of total match time.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import asdict
import json
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter, process_time
from unittest.mock import patch

SEARCH_COMPONENTS = ('world_initialization_ms', 'advance_ms', 'checkpoint_capture_ms',
                     'simulator_advance_calls', 'transition_cache_hits', 'candidate_expansions',
                     'candidate_cache_hits', 'world_restores', 'reused_information_nodes',
                     'reused_structure_nodes', 'reused_prior_nodes', 'reused_root_visits',
                     'reuse_charged_bytes', 'checkpoint_hits', 'checkpoint_copies')


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0, total_ms=0, mean_ms=None, median_ms=None, p95_ms=None, max_ms=None)
    return dict(count=len(values), total_ms=sum(values), mean_ms=sum(values)/len(values),
                median_ms=median(values), p95_ms=values[ceil(.95*len(values))-1], max_ms=values[-1])


class RequestProfiler:
    """Scoped diagnostic hooks; no retained requests, controllers or engine states."""
    def __init__(self):
        self.mode = ContextVar('request_profile_mode', default='live')
        self.request = ContextVar('request_profile_request', default='outside_decision')
        self.stack = ContextVar('request_profile_stack', default=())
        self.calls = defaultdict(list)
        self.stages = defaultdict(list)
        self.live = []

    @contextmanager
    def timed(self, section, domain, kind):
        parents = self.stack.get()
        frame = [perf_counter(), 0.]
        token = self.stack.set((*parents, frame))
        try:
            yield
        finally:
            elapsed = perf_counter()-frame[0]
            self.stack.reset(token)
            if parents:
                parents[-1][1] += elapsed
            self.calls[section, domain, kind].append((elapsed*1000, max(0., elapsed-frame[1])*1000))

    @contextmanager
    def installed(self):
        from game.ai.decision_maker import DecisionMaker
        from game.ai.search_simulator import SearchSimulator, _ReplayController
        from game.game_actions.resolution.action_processor import ActionProcessor
        original_decide, original_replay_decide = DecisionMaker.decide, _ReplayController.decide
        original_process, original_run = ActionProcessor.process, SearchSimulator._run_replay
        original_stage, original_init = SearchSimulator.record_stage, SearchSimulator.__init__
        profiler = self

        def decision(original, controller, request):
            kind = type(request).__name__
            domain = ('simulation' if profiler.mode.get() == 'simulation' else
                      'live_'+type(controller).__name__)
            token = profiler.request.set(kind)
            try:
                with profiler.timed('decision', domain, kind):
                    return original(controller, request)
            finally:
                profiler.request.reset(token)

        def decide(controller, request):
            return decision(original_decide, controller, request)

        def replay_decide(controller, request):
            return decision(original_replay_decide, controller, request)

        def process(processor, state, action):
            with profiler.timed('submitted_action', profiler.mode.get(), type(action).__name__):
                return original_process(processor, state, action)

        def run(sim, replay, factory):
            token = profiler.mode.set('simulation')
            try:
                return original_run(sim, replay, factory)
            finally:
                profiler.mode.reset(token)

        def stage(sim, name, started, *, return_start=False):
            before = sim.stages.get(name, (0, 0))[1]
            result = original_stage(sim, name, started, return_start=return_start)
            if started:
                profiler.stages[profiler.request.get(), name].append(
                    1000*(sim.stages[name][1]-before))
            return result

        def initialize(sim, *args, **kwargs):
            kwargs['profile_advance'] = True
            original_init(sim, *args, **kwargs)

        with ExitStack() as patches:
            for cls, name, replacement in (
                (DecisionMaker, 'decide', decide), (_ReplayController, 'decide', replay_decide),
                (ActionProcessor, 'process', process), (SearchSimulator, '_run_replay', run),
                (SearchSimulator, 'record_stage', stage), (SearchSimulator, '__init__', initialize)):
                patches.enter_context(patch.object(cls, name, replacement))
            yield self

    def observe(self, request, detail):
        metrics = (detail.get('search') or {}).get('metrics', {})
        self.live.append(dict(request=type(request).__name__,
            turn=request.state.turn.number if request.state else 0, options=detail['options'],
            reason=detail['reason'], searched=detail['used_search'], fallback=detail['used_fallback'],
            elapsed_ms=detail['elapsed_ms'], cpu_ms=detail['cpu_ms'], stages=dict(detail['stage_ms']),
            iterations=metrics.get('iterations_completed', 0), advances=metrics.get('advance_attempts', 0),
            search_components={k: metrics.get(k, 0) for k in SEARCH_COMPONENTS}))

    def summary(self):
        roots = []
        for kind in sorted({r['request'] for r in self.live}):
            rows = [r for r in self.live if r['request'] == kind]
            searched = [r for r in rows if r['searched']]
            stages = sorted({s for r in rows for s in r['stages']})
            roots.append(dict(request=kind, decision=distribution([r['elapsed_ms'] for r in rows]),
                searched=distribution([r['elapsed_ms'] for r in searched]),
                forced=distribution([r['elapsed_ms'] for r in rows if r['reason'] == 'forced_choice']),
                options_mean=sum(r['options'] for r in rows)/len(rows),
                options_max=max(r['options'] for r in rows),
                iterations=sum(r['iterations'] for r in searched), advances=sum(r['advances'] for r in searched),
                fallbacks=sum(r['fallback'] for r in rows), reasons=dict(Counter(r['reason'] for r in rows)),
                search_components_mean={k: sum(r.get('search_components', {}).get(k, 0) for r in searched)/len(searched)
                                        if searched else None for k in SEARCH_COMPONENTS},
                stages={s: distribution([r['stages'][s] for r in rows if s in r['stages']]) for s in stages},
                search_stages={s: distribution([r['stages'][s] for r in searched if s in r['stages']]) for s in stages}))
        return dict(live_roots=roots, slowest=sorted(self.live, key=lambda r: -r['elapsed_ms'])[:8],
            retention=dict(
                searches=sum(r['searched'] for r in self.live),
                searches_with_structure=sum(bool(r.get('search_components', {}).get('reused_structure_nodes')) for r in self.live),
                searches_with_old_visits=sum(bool(r.get('search_components', {}).get('reused_root_visits')) for r in self.live),
                totals={key: sum(r.get('search_components', {}).get(key, 0) for r in self.live)
                        for key in ('reused_structure_nodes', 'reused_prior_nodes', 'reused_root_visits',
                                    'checkpoint_hits', 'checkpoint_copies', 'transition_cache_hits')},
                peak_charged_bytes=max((r.get('search_components', {}).get('reuse_charged_bytes', 0)
                                        for r in self.live), default=0)),
            scopes=[dict(section=section, domain=domain, kind=kind,
                    inclusive=distribution([a for a, _ in values]),
                    exclusive=distribution([b for _, b in values]))
                    for (section, domain, kind), values in sorted(self.calls.items())],
            simulation_stages=[dict(request=kind, stage=stage, **distribution(values))
                               for (kind, stage), values in sorted(self.stages.items())])


def settings_for(variant, *, iterations=16, worlds=2, depth=4):
    from game.simulation.search_config import SearchAgentConfig
    if variant not in ('full', 'native'):
        raise ValueError('Choose full or native candidate generation')
    name = 'mcts_play_128_native.json' if variant == 'native' else 'mcts_play_128.json'
    settings = json.loads((Path(__file__).resolve().parents[1]/'configs'/name).read_text(encoding='utf-8'))
    settings.update(iterations=iterations, worlds=worlds, max_depth=depth,
                    max_extensions=8, max_advances=4096, max_search_ms=0)
    return asdict(SearchAgentConfig.parse(settings))


def run_one(variant, *, colors=('red', 'white'), opponent='simple', seed=42, turns=12,
            iterations=16, worlds=2, depth=4):
    from game.ai.game_origin import GameOrigin
    from game.cards.catalog import game_catalog
    from game.cards.decks import load_arena_starter
    from game.simulation.config import make_controller
    from game.simulation.match_runner import run_match
    settings = settings_for(variant, iterations=iterations, worlds=worlds, depth=depth)
    decks = tuple(load_arena_starter(c) for c in colors)
    origin = GameOrigin(decks, game_catalog, names=('MCTS', opponent), starting_player_idx=0)
    agents = (make_controller(dict(type='mcts', mcts=settings), seed=seed,
                             max_decisions=2000, search_origin=origin),
              make_controller(dict(type=opponent), seed=seed+1, max_decisions=2000))
    profiler = RequestProfiler()
    last = started = perf_counter()
    cpu_started = process_time()

    def observe(request, detail):
        nonlocal last
        profiler.observe(request, detail)
        if perf_counter()-last >= 20:
            last = perf_counter()
            print(json.dumps(dict(kind='request_profile_progress', variant=variant, opponent=opponent,
                colors=colors, turn=request.state.turn.number if request.state else 0,
                seconds=last-started, live_decisions=len(profiler.live))), flush=True)

    agents[0].on_search_decision = observe
    print(json.dumps(dict(kind='request_profile_start', variant=variant, colors=colors,
                         opponent=opponent, seed=seed, turns=turns, settings=settings)), flush=True)
    with profiler.installed():
        result = run_match(colors, seed=seed, starting_player=0, controllers=agents,
            decklists=decks, names=('MCTS', opponent), max_turns=turns,
            max_decisions=2000, collect_decision_stats=False)
    # Statistics building is outside the measured match interval.
    elapsed, cpu = perf_counter()-started, process_time()-cpu_started
    return dict(kind='request_profile_result', variant=variant, colors=colors, opponent=opponent,
        seed=seed, settings=settings, wall_seconds=elapsed, cpu_seconds=cpu,
        status=result.status, turns=result.turns, error=result.error,
        **profiler.summary())


def compact(report):
    """Readable JSON rows; retain the full result in the caller when needed."""
    roots = []
    total = sum(r['decision']['total_ms'] for r in report['live_roots'])
    for row in report['live_roots']:
        roots.append(dict(request=row['request'], count=row['decision']['count'],
            searches=row['searched']['count'], forced=row['forced']['count'],
            options_mean=row['options_mean'], options_max=row['options_max'],
            mean_ms=row['decision']['mean_ms'], search_mean_ms=row['searched']['mean_ms'],
            search_p95_ms=row['searched']['p95_ms'], max_ms=row['decision']['max_ms'],
            total_ms=row['decision']['total_ms'], share=100*row['decision']['total_ms']/total if total else 0,
            iterations=row['iterations'], advances=row['advances'], fallbacks=row['fallbacks'],
            search_components_mean=row['search_components_mean'],
            stage_total_ms={k: v['total_ms'] for k, v in row['stages'].items()},
            search_stage_mean_ms={k: v['mean_ms'] for k, v in row['search_stages'].items()}))
    stages = defaultdict(dict)
    for row in report['simulation_stages']:
        stages[row['request']][row['stage']] = dict(count=row['count'], mean_ms=row['mean_ms'],
                                                    total_ms=row['total_ms'], max_ms=row['max_ms'])
    return dict(**{k: report[k] for k in ('variant', 'colors', 'opponent', 'seed', 'status', 'turns',
                                         'error', 'wall_seconds', 'cpu_seconds')},
        live_roots=roots, retention=report['retention'], simulation_stages=dict(stages), slowest=report['slowest'][:3],
        submitted_actions=[dict(domain=r['domain'], action=r['kind'], count=r['exclusive']['count'],
            own_mean_ms=r['exclusive']['mean_ms'], own_p95_ms=r['exclusive']['p95_ms'],
            own_total_ms=r['exclusive']['total_ms'], inclusive_mean_ms=r['inclusive']['mean_ms'])
            for r in report['scopes'] if r['section'] == 'submitted_action'])


def fixed_roots(*, colors=('red', 'white'), seed=42, repeats=2, iterations=16, worlds=2, depth=4,
                stress=False):
    """Compare each algorithm on identical captured priority/attack/block states."""
    from benchmarks.mcts_parallel import capture
    from game.ai.decision_maker import PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest
    from game.ai.action_priors import HeuristicActionPrior
    from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.simulation.search_config import SearchGenerationPolicy
    from game.ai.search_simulator import SearchSimulator
    kinds = ((DeclareAttackersRequest, DeclareBlockersRequest) if stress else
             (PriorityDecisionRequest, DeclareAttackersRequest, DeclareBlockersRequest))
    for kind in kinds:
        if stress:
            from benchmarks.mcts_top_k import make_case
            case = 'attackers' if kind is DeclareAttackersRequest else 'blockers'
            def scenario(controllers):
                return make_case(case, controllers)[0]
            def recipes(world_seed):
                return SearchSimulator(scenario, track_information=True, structured_records=True,
                                       projection_cache_size=128)
        else:
            recipes, _ = capture(*colors, seed, 9, request_kind=kind)
        signatures = {}
        for repeat in range(repeats):
            for variant in ('full', 'native') if repeat % 2 == 0 else ('native', 'full'):
                native = variant == 'native'
                policy = SearchGenerationPolicy(progressive_candidates=native, candidate_native_ordering=native)
                def factory(world_seed):
                    sim = recipes(world_seed)
                    if not stress:
                        sim.factory.reuse = True
                    sim.policy_for = policy
                    return sim
                info = factory(0).start().player_information()
                assert info.current.request_kind == kind.__name__
                chooser = ObservationOpponentPolicy()
                search = InformationSetSearch(selection='puct', action_prior=HeuristicActionPrior(chooser),
                    opponent_policy=chooser, rollout_policy=chooser, compress_forced=True,
                    require_root_coverage=False, force_initial_coverage=False,
                    widening_constant=4 if native else 0,
                    budget=InformationSearchBudget(worlds=worlds, iterations=iterations, max_depth=depth,
                                                  max_extensions=8, max_advances=4096))
                profiler = RequestProfiler()
                with profiler.installed():
                    started, cpu_started = perf_counter(), process_time()
                    result = search.search(factory, info, seed=seed)
                    elapsed, cpu = perf_counter()-started, process_time()-cpu_started
                signature = result.choice, result.value, result.estimates, result.stop_reason, result.metrics.advance_attempts
                if variant in signatures:
                    assert signatures[variant] == signature
                signatures[variant] = signature
                row = compact(dict(variant=variant, colors=colors, opponent='captured_simple', seed=seed,
                    status='fallback' if result.used_fallback else 'searched', turns=None,
                    error=result.fallback_reason, wall_seconds=elapsed, cpu_seconds=cpu, **profiler.summary()))
                row.update(root_kind=kind.__name__, fixture='large_combat' if stress else 'captured',
                    repeat=repeat, options=len(info.actions),
                    iterations=result.metrics.iterations_completed, advances=result.metrics.advance_attempts,
                    metrics={k: getattr(result.metrics, k) for k in SEARCH_COMPONENTS},
                    choice=result.choice.key, value=result.value)
                print(json.dumps(row), flush=True)
        print(json.dumps(dict(kind='fixed_root_estimates', request=kind.__name__,
                              all_estimates_equal=signatures['full'] == signatures['native'])), flush=True)


def main():
    from benchmarks.environment import source_fingerprint
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants', nargs='+', choices=('full', 'native'), default=['full', 'native'])
    parser.add_argument('--colors', nargs=2, default=['red', 'white'])
    parser.add_argument('--opponents', nargs='+', choices=('simple', 'modular'), default=['simple', 'modular'])
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--turns', type=int, default=12)
    parser.add_argument('--iterations', type=int, default=16)
    parser.add_argument('--worlds', type=int, default=2)
    parser.add_argument('--depth', type=int, default=4)
    parser.add_argument('--compact', action='store_true', help='Print abbreviated tables instead of full distributions')
    parser.add_argument('--fixed-roots', action='store_true', help='Identical captured states instead of match prefixes')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--stress-combat', action='store_true', help='Known-world 1024/1296-option combat search fixtures')
    args = parser.parse_args()
    if min(args.turns, args.iterations, args.worlds, args.depth, args.repeats) < 1:
        parser.error('turns, iterations, worlds, depth and repeats must be positive')
    root = Path(__file__).resolve().parents[1]
    before = source_fingerprint(root)
    if args.fixed_roots or args.stress_combat:
        fixed_roots(colors=tuple(args.colors), seed=args.seed, repeats=args.repeats,
                    iterations=args.iterations, worlds=args.worlds, depth=args.depth, stress=args.stress_combat)
    else:
        for index, opponent in enumerate(args.opponents):
            for variant in args.variants if index % 2 == 0 else reversed(args.variants):
                row = run_one(variant, colors=tuple(args.colors), opponent=opponent, seed=args.seed,
                    turns=args.turns, iterations=args.iterations, worlds=args.worlds, depth=args.depth)
                print(json.dumps(compact(row) if args.compact else row), flush=True)
    print(json.dumps(dict(kind='request_profile_source', source=before,
                         unchanged=before == source_fingerprint(root))), flush=True)


if __name__ == '__main__':
    main()
