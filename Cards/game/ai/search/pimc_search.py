"""PIMC ensemble over full sampled runtimes, separate from information-set MCTS.

The sampler owns root evidence. Trees own only paths, portable actions and
statistics. No observation/recall identity is built inside a trajectory.
"""
from collections import Counter
from dataclasses import dataclass, field
from math import ceil, isfinite, log, nextafter, sqrt
from random import Random
from time import perf_counter, process_time

from game.ai.search.contracts import (
    ActionEstimate, InformationSearchBudget, InformationSearchMetrics, InformationSearchResult,
    SampledRootMismatch,
)
from game.ai.simulation.perfect_information_world import (
    FinishedTrajectory, InterruptedTrajectory, RuntimeStateValue, settled_priority,
)
from game.ai.simulation.search_simulator import (
    ReplayDiverged, SimulationBudgetExceeded, SimulationHistoryUnavailable,
)


@dataclass
class _Node:
    visits: int = 0
    total: float = 0.0
    header: tuple | None = None
    order: tuple = ()
    complete: bool = False
    children: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)  # portable root actions only

    def sync(self, decision, menu):
        if self.header is not None and self.header != decision.header:
            raise ReplayDiverged('Deterministic PIMC path changed its decision header')
        if menu.descriptors[:len(self.order)] != self.order:
            raise ReplayDiverged('Deterministic PIMC path changed its candidate prefix')
        self.header, self.order, self.complete = decision.header, menu.descriptors, menu.complete


@dataclass(frozen=True)
class PIMCMetrics(InformationSearchMetrics):
    """Shared work counters plus explicitly state-path, not information-set nodes."""
    path_nodes: int = 0
    search_backend: str = 'pimc'


def aggregate_roots(roots, actions):
    """Equal sample weights, not visit-weighted mixing across sampled worlds.

An action without a completed value in EVERY world has no ensemble estimate.
This avoids substituting zero or silently omitting difficult worlds.
"""
    rows = []
    for action in actions:
        edges = [root.children.get(root.actions.get(action)) for root in roots]
        visits = sum(edge.visits for edge in edges if edge is not None)
        mean = (sum(edge.total / edge.visits for edge in edges) / len(roots)
                if roots and all(edge is not None and edge.visits for edge in edges) else None)
        rows.append(ActionEstimate(action, visits, mean))
    return tuple(rows)


class PIMCSearch:
    """Explicit two-player, serial PIMC backend with uniform UCT/PUCT priors.

128 iterations means 128 trajectories TOTAL, distributed round-robin over the
sample pool. Root observations exist only at the live sampler boundary.
"""
    tree_reuse_size = 0
    construction_choices = ()

    def __init__(self, *, budget=None, exploration=sqrt(2), selection='uct',
                 max_time_ms=0, min_trajectories=1, require_root_coverage=False,
                 widening_constant=1.0, widening_exponent=0.5, evaluator=None,
                 leaf_policy='horizon'):
        self.budget = budget if budget is not None else InformationSearchBudget()
        if selection not in ('uct', 'puct'):
            raise ValueError('PIMC supports uniform UCT or PUCT')
        if leaf_policy not in ('horizon', 'first_leaf'):
            raise ValueError('PIMC leaf_policy must be horizon or first_leaf')
        self.leaf_policy = leaf_policy
        if not isfinite(exploration) or exploration < 0:
            raise ValueError('Invalid exploration')
        if not isfinite(max_time_ms) or max_time_ms < 0:
            raise ValueError('Invalid search deadline')
        if type(min_trajectories) is not int or min_trajectories < 0:
            raise ValueError('Invalid minimum trajectory count')
        if not isfinite(widening_constant) or widening_constant < 0 or not 0 < widening_exponent <= 1:
            raise ValueError('Invalid widening settings')
        self.exploration, self.selection = exploration, selection
        self.max_time_ms, self.min_trajectories = max_time_ms, min_trajectories
        self.require_root_coverage = require_root_coverage
        self.widening_constant, self.widening_exponent = widening_constant, widening_exponent
        self.evaluator = evaluator if evaluator is not None else RuntimeStateValue()
        self.reset()

    def reset(self):
        self.last_world_metrics = ()
        self.runtime_observation_metrics = {}

    def search(self, world_factory, root_information, *, seed=0, fallback=None):
        self.reset()
        if len(root_information.current.readonly()['players']) != 2:
            raise ValueError('PIMC currently supports two players')
        initial_actions = tuple(root_information.actions)
        if not initial_actions:
            raise ValueError('Search requires root actions')
        fallback = (next((action for action in initial_actions if action.kind == 'PassPriorityAction'),
                         initial_actions[0]) if fallback is None else fallback)
        if fallback not in initial_actions:
            raise ValueError('Fallback is not in the actual root menu')
        observer = root_information.current.player
        run = _SearchRun(self, root_information, observer, seed)
        return run.execute(world_factory, fallback)


class _SearchRun:
    def __init__(self, search, information, observer, seed):
        self.search, self.observer = search, observer
        self.root_key, self.root_kind = information.fast_key, information.current.request_kind
        self.root_width, self.initial_actions = information.candidate_width, tuple(information.actions)
        self.budget = search.budget
        self.rng = Random(seed)
        self.started_at, self.cpu_started = perf_counter(), process_time()
        self.worlds, self.roots = [], []
        self.actions = dict.fromkeys(information.actions)
        self.started = self.completed = self.advances = self.nodes = self.terminals = self.leaves = self.forced = 0
        self.interruptions = Counter()
        self.init_seconds = self.loop_seconds = self.loop_cpu = 0.0
        self.depth_total = self.depth_max = self.first_leaf_evaluations = self.horizon_evaluations = 0

    def expired(self):
        return bool(self.search.max_time_ms and
                    (perf_counter() - self.started_at) * 1000 >= self.search.max_time_ms)

    def bind_root(self, root, decision, menu):
        from game.ai.decisions.search_actions import PlayerActionBuilder
        if decision.actor != self.observer or decision.kind != self.root_kind:
            raise SampledRootMismatch('PIMC root actor/request differs from live root')
        if len(root.actions) < len(menu.options):
            builder = PlayerActionBuilder(decision.request)
            for option, descriptor in zip(menu.options, menu.descriptors):
                action = builder.action(option)
                if action in root.actions and root.actions[action] != descriptor:
                    raise SampledRootMismatch('Ambiguous portable root action')
                root.actions[action] = descriptor
                self.actions.setdefault(action, None)

    def initialize(self, factory):
        from game.ai.sampling.perfect_information_sampling import PerfectInformationSampler
        sampler = PerfectInformationSampler(factory, self.root_key, self.observer)
        for _ in range(self.budget.worlds):
            if self.expired():
                return 'time_budget'
            seed = self.rng.getrandbits(64)
            world = sampler(seed)
            world.evaluator = self.search.evaluator
            root = _Node()
            def capture(decision):
                width = self.root_width or len(self.initial_actions)
                menu = decision.menu(width)
                root.sync(decision, menu)
                self.bind_root(root, decision, menu)
                if set(root.actions) != set(self.initial_actions):
                    raise SampledRootMismatch('Sampled root menu differs from actual offered actions')
                raise FinishedTrajectory(0.0)
            self.worlds.append(world)
            self.roots.append(root)
            self.nodes += 1
            world.run(capture)
            if root.header is None:
                raise SampledRootMismatch('Sampled world ended before its root decision')
        return None

    def width(self, world, node, *, root):
        model = getattr(world.policy_for, 'candidate_model', None)
        if model is None:
            return world.max_options
        minimum = max(model.initial_width, self.root_width if root else 0)
        width = ceil(max(1.0, self.search.widening_constant) * (node.visits + 1) ** self.search.widening_exponent)
        return min(world.max_options, max(minimum, width))

    def select(self, node, actor):
        missing = [d for d in node.order if d not in node.children or not node.children[d].visits]
        if missing:
            return missing[0]
        sign = 1.0 if actor == self.observer else -1.0
        def utility(descriptor):
            child = node.children[descriptor]
            bonus = (sqrt(log(max(1, node.visits)) / child.visits) if self.search.selection == 'uct'
                     else sqrt(node.visits) / ((child.visits + 1) * len(node.order)))
            return sign * child.total / child.visits + self.search.exploration * bonus
        return max(node.order, key=utility)

    def trajectory(self, world, root):
        node, depth, extensions = root, 0, 0
        expanded = False
        evaluated_at = None
        path = [root]
        def choose(decision):
            nonlocal node, depth, extensions, expanded, evaluated_at
            if self.expired():
                raise InterruptedTrajectory('time_budget')
            first_leaf = self.search.leaf_policy == 'first_leaf' and expanded
            if (first_leaf or depth >= self.budget.max_depth) and settled_priority(decision.request):
                value = float(self.search.evaluator.evaluate(decision.request.state, self.observer))
                if not isfinite(value) or not -1 <= value <= 1:
                    raise ValueError('Runtime evaluator returned an invalid value')
                limit = nextafter(1.0, 0.0)
                evaluated_at = 'first_leaf' if first_leaf else 'horizon'
                raise FinishedTrajectory(max(-limit, min(limit, value)))
            if self.advances >= self.budget.max_advances:
                raise InterruptedTrajectory('advance_budget')
            menu = decision.menu(self.width(world, node, root=node is root))
            node.sync(decision, menu)
            if node is root:
                self.bind_root(root, decision, menu)
            forced = menu.complete and len(menu.options) == 1
            constructing = decision.kind in {'OrderNextRequest', 'CombatNextRequest',
                                           'PriorityAbilityRequest', 'AbilityConstructionRequest'}
            if depth >= self.budget.max_depth and not forced and not constructing:
                if extensions >= self.budget.max_extensions:
                    raise InterruptedTrajectory('extension_limit')
                extensions += 1
            descriptor = self.select(node, decision.actor)
            option = menu.options[menu.descriptors.index(descriptor)]
            if descriptor not in node.children:
                node.children[descriptor] = _Node()
                self.nodes += 1
            node = node.children[descriptor]
            # A previously interrupted edge is still an unvisited frontier.
            expanded = expanded or not node.visits
            path.append(node)
            completes = decision.kind in {'PriorityAbilityRequest', 'AbilityConstructionRequest'}
            if decision.kind == 'OrderNextRequest':
                completes = len(decision.request.frame.remaining) == 1
            elif decision.kind == 'CombatNextRequest':
                completes = decision.request.frame.remaining_count == 1
            source = type(option).__name__ in {'PrioritySourceOption', 'AbilityConstructionOption'}
            depth += int((not constructing or completes) and not source)
            self.advances += 1
            self.forced += int(forced)
            return option
        value, terminal = world.run(choose)
        if not isfinite(value) or not -1 <= value <= 1:
            raise ValueError('Runtime evaluator returned an invalid outcome')
        for visited in path:
            visited.visits += 1
            visited.total += value
        self.terminals += int(terminal)
        self.leaves += int(not terminal)
        self.depth_total += depth
        self.depth_max = max(self.depth_max, depth)
        self.first_leaf_evaluations += int(evaluated_at == 'first_leaf')
        self.horizon_evaluations += int(evaluated_at == 'horizon')

    def execute(self, factory, fallback):
        reason = 'iteration_budget'
        try:
            initialized = perf_counter()
            try:
                stopped = self.initialize(factory)
            except (SimulationBudgetExceeded, SimulationHistoryUnavailable) as error:
                stopped = type(error).__name__
            except InterruptedTrajectory as error:
                stopped = error.reason
            self.init_seconds = perf_counter() - initialized
            loop, cpu = perf_counter(), process_time()
            if stopped is None:
                for index in range(self.budget.iterations):
                    if self.expired() or self.advances >= self.budget.max_advances:
                        reason = 'time_budget' if self.expired() else 'advance_budget'
                        break
                    self.started += 1
                    world_index = index % len(self.worlds)
                    try:
                        self.trajectory(self.worlds[world_index], self.roots[world_index])
                    except InterruptedTrajectory as error:
                        self.interruptions[error.reason] += 1
                        if error.reason in ('time_budget', 'advance_budget'):
                            reason = error.reason
                            break
                    except (SimulationBudgetExceeded, SimulationHistoryUnavailable) as error:
                        self.interruptions[type(error).__name__] += 1
                    else:
                        self.completed += 1
            else:
                reason = stopped
            self.loop_seconds, self.loop_cpu = perf_counter() - loop, process_time() - cpu
            estimates = aggregate_roots(self.roots, self.actions) if stopped is None else tuple(
                ActionEstimate(action, 0, None) for action in self.actions)
            covered = [row for row in estimates if row.mean_value is not None]
            complete = (bool(self.roots) and all(root.complete for root in self.roots)
                        and len(covered) == len(estimates) and stopped is None)
            fallback_reason = ('incomplete_world_pool' if stopped is not None else
                'minimum_trajectories' if self.completed < self.search.min_trajectories else
                'insufficient_world_coverage' if not covered else
                'root_coverage' if self.search.require_root_coverage and not complete else None)
            chosen = (max(covered, key=lambda row: (row.mean_value, row.visits))
                      if fallback_reason is None else None)
            metrics = PIMCMetrics(len(self.worlds), self.started, self.completed, self.advances,
                self.terminals, self.leaves, self.started - self.completed, tuple(sorted(self.interruptions.items())),
                0, (perf_counter() - self.started_at) * 1000,
                selection=self.search.selection, world_initialization_ms=self.init_seconds * 1000,
                simulator_advance_calls=self.advances, root_actions=len(estimates),
                root_actions_visited=len(covered), root_menu_complete=bool(self.roots) and all(root.complete for root in self.roots),
                world_materializations=sum(w.factory.materializations for w in self.worlds),
                world_restores=sum(w.factory.restores for w in self.worlds), trajectory_runs=self.started,
                forced_transitions=self.forced, root_coverage_required=self.search.require_root_coverage,
                forced_initial_coverage=True, cpu_ms=(process_time() - self.cpu_started) * 1000,
                time_limit_ms=self.search.max_time_ms, minimum_trajectories=self.search.min_trajectories,
                trajectory_loop_ms=self.loop_seconds * 1000, trajectory_loop_cpu_ms=self.loop_cpu * 1000,
                path_nodes=self.nodes, leaf_policy=self.search.leaf_policy,
                semantic_depth_total=self.depth_total, semantic_depth_max=self.depth_max,
                first_leaf_evaluations=self.first_leaf_evaluations, horizon_evaluations=self.horizon_evaluations)
            return InformationSearchResult(chosen.action if chosen else fallback,
                chosen.mean_value if chosen else None, chosen is None, complete, reason,
                estimates, metrics, fallback_reason)
        finally:
            self.search.last_world_metrics = tuple(dict(world.counts, **world.factory.worker_metrics,
                materializations=world.factory.materializations, restores=world.factory.restores)
                for world in self.worlds)
            for world in self.worlds:
                world.factory.release_worker()
