"""Bounded root-player information-set Monte Carlo against a fixed opponent.

Root decisions share UCT/PUCT statistics by PlayerInformation, not sampled State.
The opponent receives only its own information and is NOT trained on rollouts
conditioned on the root's private hand. This is a fixed-policy baseline, not
equilibrium solving, minimax over determinizations or unrestricted ISMCTS.
"""
from dataclasses import dataclass
from collections import Counter
from math import isfinite, log, nextafter, sqrt
from random import Random
from time import perf_counter, process_time

from game.ai.search_actions import PlayerAction
from game.ai.search_simulator import (SimulationBudgetExceeded, HistorySamplingExhausted, SimulationHistoryUnavailable,
                                      WorldInitializationMetrics, SearchSimulator, SimulationNode, ForcedBoundary)
from game.ai.state_value import StateValueEvaluator
from game.ai.search_horizon import quiet_priority
from game.ai.search_candidates import CandidateOrdering, ProgressiveWidening
from game.ai.action_priors import UniformActionPrior, normalize_prior
from game.ai.search_tree_memory import EdgeStatistics as _Statistics, EdgeTable, RetainedNode


class SampledRootMismatch(ValueError):
    """!
    @brief A proposed world is incompatible with the live player's information.
    """

    def __init__(self, message, *, reason="unspecified"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class InformationSearchBudget:
    """!
    @brief Immutable limits on sampled worlds, trajectories and logical transitions.
    @var worlds Number of sampled worlds prepared for one search.
    @var iterations Maximum attempted trajectories across the sampled pool.
    @var max_advances Hard limit on logical action transitions during one search.
    @var max_depth Decision depth before settled-leaf evaluation, not a turn count.
    @var max_extensions Additional nonforced steps permitted while seeking a settled leaf.
    """
    worlds: int = 8
    iterations: int = 128
    max_advances: int = 2048
    max_depth: int = 8
    max_extensions: int = 8

    def __post_init__(self):
        for name in ("worlds", "iterations", "max_advances", "max_depth", "max_extensions"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "max_extensions" else 1):
                raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class ActionEstimate:
    """!
    @brief Completed root-action visits, mean value and optional prior probability.
    @var action Complete offered player-visible action.
    @var visits Number of completed trajectory samples.
    @var mean_value Completed mean value, or None when no sample completed.
    @var prior_probability Normalized prior weight, not a calibrated win probability.
    """
    action: PlayerAction
    visits: int
    mean_value: float | None
    prior_probability: float | None = None


@dataclass(frozen=True)
class InformationSearchMetrics:
    """!
    @brief Detached work, timing, cache and worker diagnostics for one search.
    @var worlds_built Number of initialized compatible sampled worlds.
    @var iterations_started Number of attempted trajectories.
    @var iterations_completed Trajectories that produced an accepted terminal or leaf value.
    @var advance_attempts Logical transitions, including cached and forced steps.
    @var terminal_leaves Number of accepted terminal outcomes.
    @var heuristic_leaves Number of accepted settled heuristic leaves.
    @var interrupted_trajectories Attempts that did not produce an accepted outcome.
    @var interruption_reasons Per-cause counts of interrupted trajectories.
    @var information_nodes Actor-information nodes allocated in this search.
    @var elapsed_ms Recorded elapsed in milliseconds.
    @var rollout_policy_choices Number of rollout policy choices recorded by this component.
    @var rollout_random_choices Number of rollout random choices recorded by this component.
    @var history_sampling_attempts Number of history sampling attempts recorded by this component.
    @var history_rejected_samples Number of history rejected samples recorded by this component.
    @var history_prefix_advances History prefix advances retained by this record.
    @var population_updates Number of population updates recorded by this component.
    @var population_survivors Number of population survivors recorded by this component.
    @var population_size Population size retained by this record.
    @var population_distinct_origins Population distinct origins retained by this record.
    @var population_refreshes Number of population refreshes recorded by this component.
    @var history_mismatch_reasons History mismatch reasons retained by this record.
    @var selection Configured UCT or PUCT variant.
    @var prior_evaluations Number of prior evaluations recorded by this component.
    @var world_initialization_ms Recorded world initialization in milliseconds.
    @var advance_ms Recorded advance in milliseconds.
    @var simulator_advance_calls Number of simulator advance calls recorded by this component.
    @var transition_cache_hits Number of transition cache hits recorded by this component.
    @var transition_cache_entries Number of transition cache entries recorded by this component.
    @var root_actions Number of root actions recorded by this component.
    @var root_actions_visited Root actions visited retained by this record.
    @var checkpoint_hits Number of checkpoint hits recorded by this component.
    @var checkpoint_entries Number of checkpoint entries recorded by this component.
    @var checkpoint_skipped_decisions Number of checkpoint skipped decisions recorded by this component.
    @var world_materializations World materializations retained by this record.
    @var world_restores World restores retained by this record.
    @var trajectory_runs Trajectory runs retained by this record.
    @var hierarchy_nodes Number of hierarchy nodes recorded by this component.
    @var hierarchy_selection_steps Number of hierarchy selection steps recorded by this component.
    @var forced_transitions Number of forced transitions recorded by this component.
    @var root_coverage_required Root coverage required retained by this record.
    @var forced_initial_coverage Forced initial coverage retained by this record.
    @var cpu_ms Recorded cpu in milliseconds.
    @var time_limit_ms Recorded time limit in milliseconds.
    @var minimum_trajectories Minimum trajectories retained by this record.
    @var projection_cache_hits Number of projection cache hits recorded by this component.
    @var projection_cache_entries Number of projection cache entries recorded by this component.
    @var candidate_orderings Number of candidate orderings recorded by this component.
    @var eligible_actions Number of eligible actions recorded by this component.
    @var reused_information_nodes Number of reused information nodes recorded by this component.
    @var reused_root_visits Reused root visits retained by this record.
    @var forced_chain_hits Number of forced chain hits recorded by this component.
    @var forced_chain_steps Number of forced chain steps recorded by this component.
    @var native_forced_steps Number of native forced steps recorded by this component.
    @var candidate_expansions Number of candidate expansions recorded by this component.
    @var candidate_cache_hits Number of candidate cache hits recorded by this component.
    @var structured_value_hits Number of structured value hits recorded by this component.
    @var root_menu_complete Whether the root menu is known to be complete.
    @var parallel_workers Parallel workers retained by this record.
    @var parallel_requested_workers Parallel requested workers retained by this record.
    @var parallel_backend Parallel backend retained by this record.
    @var parallel_setup_ms Recorded parallel setup in milliseconds.
    @var parallel_worker_cpu_ms Recorded parallel worker cpu in milliseconds.
    @var parallel_worker_pids Parallel worker pids retained by this record.
    @var parallel_worker_threads Parallel worker threads retained by this record.
    @var parallel_payload_bytes Accounted parallel payload in bytes.
    @var reused_structure_nodes Number of reused structure nodes recorded by this component.
    @var reused_prior_nodes Number of reused prior nodes recorded by this component.
    @var reuse_pruned_nodes Number of reuse pruned nodes recorded by this component.
    @var reuse_charged_bytes Accounted reuse charged in bytes.
    @var reuse_reasons Reuse reasons retained by this record.
    @var encoding_cache_hits Number of encoding cache hits recorded by this component.
    @var neural_cache_hits Number of neural cache hits recorded by this component.
    @var encoding_ms Recorded encoding in milliseconds.
    @var inference_ms Recorded inference in milliseconds.
    @var checkpoint_copies Number of checkpoint copies recorded by this component.
    @var checkpoint_evictions Number of checkpoint evictions recorded by this component.
    @var checkpoint_capture_ms Recorded checkpoint capture in milliseconds.
    @var checkpoint_copy_rejections Number of checkpoint copy rejections recorded by this component.
    """
    worlds_built: int
    iterations_started: int
    iterations_completed: int
    advance_attempts: int
    terminal_leaves: int
    heuristic_leaves: int
    interrupted_trajectories: int
    interruption_reasons: tuple[tuple[str, int], ...]
    information_nodes: int
    elapsed_ms: float
    rollout_policy_choices: int = 0
    rollout_random_choices: int = 0
    history_sampling_attempts: int = 0
    history_rejected_samples: int = 0
    history_prefix_advances: int = 0
    population_updates: int = 0
    population_survivors: int = 0
    population_size: int = 0
    population_distinct_origins: int = 0
    population_refreshes: int = 0
    history_mismatch_reasons: tuple[tuple[str, int], ...] = ()
    selection: str = "uct"
    prior_evaluations: int = 0
    world_initialization_ms: float = 0.0
    advance_ms: float = 0.0
    simulator_advance_calls: int = 0
    transition_cache_hits: int = 0
    transition_cache_entries: int = 0
    root_actions: int = 0
    root_actions_visited: int = 0
    checkpoint_hits: int = 0
    checkpoint_entries: int = 0
    checkpoint_skipped_decisions: int = 0
    world_materializations: int = 0
    world_restores: int = 0
    trajectory_runs: int = 0
    hierarchy_nodes: int = 0
    hierarchy_selection_steps: int = 0
    forced_transitions: int = 0
    root_coverage_required: bool = True
    forced_initial_coverage: bool = True
    cpu_ms: float = 0.0
    time_limit_ms: float = 0.0
    minimum_trajectories: int = 0
    projection_cache_hits: int = 0
    projection_cache_entries: int = 0
    candidate_orderings: int = 0
    eligible_actions: int = 0
    reused_information_nodes: int = 0
    reused_root_visits: int = 0
    forced_chain_hits: int = 0
    forced_chain_steps: int = 0
    native_forced_steps: int = 0
    candidate_expansions: int = 0
    candidate_cache_hits: int = 0
    structured_value_hits: int = 0
    root_menu_complete: bool = True
    parallel_workers: int = 1
    parallel_requested_workers: int = 1
    parallel_backend: str = 'serial'
    parallel_setup_ms: float = 0.0
    parallel_worker_cpu_ms: float = 0.0
    parallel_worker_pids: tuple[int, ...] = ()
    parallel_worker_threads: tuple[int, ...] = ()
    parallel_payload_bytes: int = 0
    reused_structure_nodes: int = 0
    reused_prior_nodes: int = 0
    reuse_pruned_nodes: int = 0
    reuse_charged_bytes: int = 0
    reuse_reasons: tuple[tuple[str,int], ...] = ()
    encoding_cache_hits: int = 0
    neural_cache_hits: int = 0
    encoding_ms: float = 0.0
    inference_ms: float = 0.0
    checkpoint_copies: int = 0
    checkpoint_evictions: int = 0
    checkpoint_capture_ms: float = 0.0
    checkpoint_copy_rejections: int = 0


@dataclass(frozen=True)
class InformationSearchResult:
    """!
    @brief Recommended action and diagnostics with explicit fallback status.
    @var choice Recommended action or explicitly reported fallback choice.
    @var value Value retained by this record.
    @var used_fallback Whether the result recommends the configured fallback.
    @var root_coverage_complete Whether every action in the complete root menu was visited.
    @var stop_reason Reason the bounded search loop stopped.
    @var estimates Completed statistics for offered root actions.
    @var metrics Detached work, timing and cache diagnostics.
    @var fallback_reason Cause of fallback, independent of the loop stop reason.
    """
    choice: PlayerAction
    value: float | None
    used_fallback: bool
    root_coverage_complete: bool
    stop_reason: str
    estimates: tuple[ActionEstimate, ...]
    metrics: InformationSearchMetrics
    fallback_reason: str | None = None


class _Interrupted(Exception):
    """!
    @brief Stop an incomplete trajectory without inventing a game outcome.
    """
    pass


class InformationSetSearch:
    """!
    @brief Plan using explicitly sampled worlds and an observation-only opponent.

    world_factory(sample_seed) must return a fresh deterministic simulator with
    information tracking. Its start node must match root_information exactly. The
    fixed sampled pool approximates the supplied belief; it is never a live deck
    reader. opponent_policy(info) must be deterministic/pure and return an offered
    PlayerAction, without side channels to a simulator, root hand or search scores.

    At root-player nodes UCT (default) or PUCT with forced initial coverage selects
    until one new edge is expanded; subsequent
    root choices use uniform rollouts by default, or an observation-only rollout
    policy mixed with uniform exploration. Opponent choices always
    use the supplied policy. Only completed trajectories update statistics.
    """
    def __init__(self, *, opponent_policy, budget=None, evaluator=None, exploration=sqrt(2),
                 rollout_policy=None, rollout_exploration=0.1, selection="uct", action_prior=None,
                 transition_cache_size=2048, trajectory_mode="continuous", compress_forced=False,
                 require_root_coverage=True, force_initial_coverage=True, max_time_ms=0.0,
                 min_trajectories=0, widening_constant=0.0, widening_exponent=0.5,
                 tree_reuse_size=0, forced_chain_cache=True, reuse_rolling_horizon=False,
                 tree_reuse_bytes=16*1024*1024):
        if type(tree_reuse_bytes) is not int or tree_reuse_bytes < 0:
            raise ValueError('tree_reuse_bytes must be a nonnegative integer')
        self.tree_reuse_bytes = tree_reuse_bytes
        if type(forced_chain_cache) is not bool:
            raise ValueError("forced_chain_cache must be boolean")
        self.forced_chain_cache = forced_chain_cache
        if type(tree_reuse_size) is not int or tree_reuse_size < 0:
            raise ValueError("tree_reuse_size must be a nonnegative integer")
        from game.ai.search_tree_memory import SearchTreeMemory
        self.tree_reuse_size = tree_reuse_size
        self.tree_memory = SearchTreeMemory()
        self.model_revision = 0
        self.model_epoch = 0
        if type(reuse_rolling_horizon) is not bool:
            raise ValueError('reuse_rolling_horizon must be boolean')
        self.reuse_rolling_horizon = reuse_rolling_horizon
        if (type(widening_constant) not in (int, float) or not isfinite(widening_constant)
                or (widening_constant != 0 and widening_constant < 1)):
            raise ValueError("widening_constant must be zero (disabled) or at least one")
        if (type(widening_exponent) not in (int, float) or not isfinite(widening_exponent)
                or not 0 < widening_exponent <= 1):
            raise ValueError("widening_exponent must be in (0, 1]")
        self.widening_constant, self.widening_exponent = widening_constant, widening_exponent
        if type(max_time_ms) not in (int, float) or not isfinite(max_time_ms) or max_time_ms < 0:
            raise ValueError("max_time_ms must be finite and nonnegative")
        self.max_time_ms = max_time_ms
        if type(min_trajectories) is not int or min_trajectories < 0:
            raise ValueError("min_trajectories must be a nonnegative integer")
        self.min_trajectories = min_trajectories
        if any(type(v) is not bool for v in (compress_forced, require_root_coverage, force_initial_coverage)):
            raise ValueError("Search coverage/compression settings must be boolean")
        self.compress_forced = compress_forced
        self.require_root_coverage = require_root_coverage
        self.force_initial_coverage = force_initial_coverage
        if not callable(opponent_policy):
            raise TypeError("Supply an observation-only opponent policy")
        if not isfinite(exploration) or exploration < 0:
            raise ValueError("exploration must be finite and nonnegative")
        if rollout_policy is not None and not callable(rollout_policy):
            raise TypeError("rollout_policy must be an observation-only callable")
        if not isfinite(rollout_exploration) or not 0 <= rollout_exploration <= 1:
            raise ValueError("rollout_exploration must be in [0, 1]")
        if selection not in {"uct", "puct", "hierarchical_puct"}:
            raise ValueError("selection must be uct, puct or hierarchical_puct")
        if action_prior is not None and not callable(action_prior):
            raise TypeError("action_prior must be an observation-only callable")
        if selection == "uct" and action_prior is not None:
            raise ValueError("An action prior is only used by puct")
        self.selection = selection
        if type(transition_cache_size) is not int or transition_cache_size < 0:
            raise ValueError("transition_cache_size must be a nonnegative integer")
        self.transition_cache_size = transition_cache_size
        if trajectory_mode not in {"continuous", "replay"}:
            raise ValueError("trajectory_mode must be continuous or replay")
        self.trajectory_mode = trajectory_mode
        self.action_prior = action_prior if action_prior is not None else UniformActionPrior()
        self.opponent_policy = opponent_policy
        self.budget = budget if budget is not None else InformationSearchBudget()
        self.evaluator = evaluator if evaluator is not None else StateValueEvaluator()
        self.exploration = exploration
        self.rollout_policy, self.rollout_exploration = rollout_policy, rollout_exploration

    def reset(self):
        """!
        @brief Discard session-owned mutable state before preparing a new search context.
        """
        self.tree_memory.clear()

    def invalidate_belief(self):
        # Current observations still have to match exactly. Pure deterministic
        # priors/menus are independent of the sampled outcome distribution.
        """!
        @brief Invalidate outcome statistics tied to a changed sampled-belief contract.
        """
        self.tree_memory.invalidate_statistics()

    def invalidate_model(self, revision=None):
        """!
        @brief Call after an in-place policy/value/belief weight change.

        Statistics are never silently interpreted under another posterior.
        A caller can supply a stable model version or just advance the epoch.
        """
        if revision is not None and type(revision) not in (str, int):
            raise TypeError('Model revision must be a detached string/integer')
        self.model_epoch += 1
        if revision is not None:
            self.model_revision = revision
        models = set()
        for adapter in (self.action_prior,self.evaluator):
            model = getattr(adapter,'model',None)
            if model is not None and id(model) not in models and callable(getattr(model,'invalidate_cache',None)):
                model.invalidate_cache()
                models.add(id(model))
        self.tree_memory.clear()

    def _memory_signature(self):
        return (self.model_revision, self.model_epoch, self.reuse_rolling_horizon, self.budget, self.selection, self.exploration, id(self.evaluator), id(self.action_prior),
                id(self.opponent_policy), id(self.rollout_policy), self.rollout_exploration,
                self.compress_forced, self.force_initial_coverage, self.require_root_coverage,
                self.widening_constant, self.widening_exponent)

    def search(self, world_factory, root_information, *, seed=0, fallback=None, reuse_context=None):
        """!
        @brief Run bounded search and return a detached recommendation with diagnostics.
        """
        if type(seed) is not int:
            raise ValueError("A deterministic integer search seed is required")
        root_view = root_information.current.readonly()
        player = root_information.current.player
        if root_view.get("setup") or len(root_view.get("players", ())) != 2:
            raise ValueError("Search requires an in-game two-player decision")
        if player not in {p["id"] for p in root_view["players"]} or not root_information.actions:
            raise ValueError("Root must have a participating actor and offered actions")
        if fallback is None:
            fallback = next((a for a in root_information.actions if a.kind == "PassPriorityAction"),
                            root_information.actions[0])
        if fallback not in root_information.actions:
            raise ValueError("Fallback must be offered at the root")
        run = _Run(self, player, seed)
        signature = self._memory_signature()
        if self.tree_reuse_size:
            self.tree_memory.load(reuse_context, signature, root_information,
                horizon=(self.budget.max_depth, self.budget.max_extensions),
                rolling=self.reuse_rolling_horizon,export=False)
            run.reuse_reason = self.tree_memory.last_reason
            run.persistent = dict(self.tree_memory.nodes)
            run.loaded_menus = set(run.persistent)
            run.reused_structure = len(run.persistent)
            for key, node in run.persistent.items():
                run.information[key] = node.information
                run.horizons[key] = node.horizon
                if self.tree_memory.statistics_compatible:
                    run.table[key] = node.edges
                    if node.hierarchy is not None:
                        run.hierarchies[key] = node.hierarchy
                if node.probabilities:
                    run.priors[key] = node.probabilities
                    run.reused_priors += 1
                if node.candidates is not None:
                    run.candidate_orders[key] = node.candidates
            run.reused_nodes = sum(bool(rows.visits) for rows in run.table.values())
            run.loaded_keys = set(run.table)
            run.reused_root_visits = sum(s.visits for s in run.table.get(root_information.fast_key, {}).values())
        result = run.search(world_factory, root_information, fallback)
        if self.tree_reuse_size:
            if result.used_fallback:
                self.tree_memory.clear()
            else:
                for key,node in run.persistent.items():
                    node.probabilities = run.priors.get(key,node.probabilities)
                    node.hierarchy = run.hierarchies.get(key)
                    node.candidates = run.candidate_orders.get(key)
                self.tree_memory.commit(reuse_context,signature,run.persistent,root_information.fast_key,
                                        self.tree_reuse_size,self.tree_reuse_bytes)
            from dataclasses import replace
            result = replace(result,metrics=replace(result.metrics,
                reuse_pruned_nodes=self.tree_memory.pruned_nodes,reuse_charged_bytes=self.tree_memory.charged_bytes))
        return result


class _Run:
    """!
    @brief Own one search invocation, isolated RNG streams, statistics and transition caches.
    """
    def __init__(self, search, player, seed):
        self.searcher, self.player, self.budget = search, player, search.budget
        # Isolate the chance sample stream from UCT/rollout tie breaking.
        self.world_rng = Random(seed)
        self.choice_rng = Random(seed ^ 0x51A7C0DE)
        self.rollout_rng = Random(seed ^ 0xB0157E2)
        self.table = {}
        self.priors = {}
        self.prior_evaluations = 0
        self.hierarchies = {}
        self.transitions = {}
        self.forced_chains = {}
        self.horizons = {}
        self.candidate_visits = {}
        self.forced_chain_hits = self.forced_chain_steps = 0
        self.candidate_orders = {}
        self.candidate_ordering = CandidateOrdering()
        self.widening = ProgressiveWidening(search.widening_constant, search.widening_exponent)
        self.eligible_counts = {}
        self.information = {}
        self.reused_nodes = self.reused_root_visits = 0
        self.persistent = {}
        self.reused_structure = self.reused_priors = 0
        self.reuse_reason = 'disabled'
        self.loaded_keys = set()
        self.loaded_menus = set()
        self.initialization_ms = self.advance_ms = 0.0
        self.advance_calls = self.cache_hits = 0
        self.trajectory_runs = 0
        self.forced_transitions = 0
        self.worlds = self.started = self.completed = self.advances = 0
        self.terminals = self.leaves = self.interrupted = 0
        self.interruption_reasons = Counter()
        self.rollout_policy_choices = self.rollout_random_choices = 0
        self.history_attempts = self.history_rejected = self.history_advances = 0
        self.history_mismatches = Counter()
        self.population_metrics = {name: 0 for name in (
            "population_updates", "population_survivors", "population_size",
            "population_distinct_origins", "population_refreshes")}

    def search(self, factory, root_info, fallback):
        """!
        @brief Run bounded search and return a detached recommendation with diagnostics.
        """
        started = perf_counter()
        cpu_started = process_time()
        cache_before = self.evaluation_counters()
        self.deadline = started + self.searcher.max_time_ms / 1000 if self.searcher.max_time_ms else None
        pool = []
        reason = "iteration_budget"
        for _ in range(self.budget.worlds):
            if self.time_exhausted():
                reason = "time_budget"
                break
            initialization_started = perf_counter()
            sim = factory(self.world_rng.getrandbits(64))
            try:
                root = sim.start()
            except HistorySamplingExhausted:
                reason = "history_sampling_budget"
                break
            except SimulationHistoryUnavailable:
                reason = "unsupported_historical_information"
                break
            except SimulationBudgetExceeded:
                reason = "simulator_budget"
                break
            finally:
                self.initialization_ms += (perf_counter() - initialization_started) * 1000
                preparation = getattr(sim, "initialization_metrics", WorldInitializationMetrics())
                self.history_attempts += preparation.sampling_attempts
                self.history_rejected += preparation.rejected_samples
                self.history_advances += preparation.prefix_advance_attempts
                self.history_mismatches.update(dict(preparation.mismatch_reasons))
                for name in self.population_metrics:
                    self.population_metrics[name] += getattr(preparation, name, 0)
            self.worlds += 1
            if root.terminal or root.cutoff or root.player_information() != root_info:
                raise SampledRootMismatch("Sampled root does not match the player's information and actions")
            # A conditioned wrapper has completed its rejection sampling. Use
            # its owned deterministic backend for caching and continuous suffixes;
            # never condition again during a search trajectory.
            backend = getattr(sim, "simulation_backend", None)
            pool.append((backend if isinstance(backend, SearchSimulator) else sim, root))
        # Do not silently search a biased partial pool when setup exceeds a cap.
        if len(pool) == self.budget.worlds:
            for _ in range(self.budget.iterations):
                if self.time_exhausted():
                    reason = "time_budget"
                    break
                if self.advances >= self.budget.max_advances:
                    reason = "advance_budget"
                    break
                self.started += 1
                sim, root = pool[self.world_rng.randrange(len(pool))]
                try:
                    path, value = self.trajectory(sim, root)
                except _Interrupted as exc:
                    self.interrupted += 1
                    interrupted_by = str(exc)
                    self.interruption_reasons[interrupted_by] += 1
                    if interrupted_by in {"advance_budget", "simulator_budget", "time_budget", "unsupported_historical_information"}:
                        reason = interrupted_by
                        break
                    continue
                self.completed += 1
                for key, action in path:
                    self.table[key].record(action,value)
                    if key in self.hierarchies:
                        self.hierarchies[key].record(action, value)
                if self.searcher.tree_reuse_size:
                    for (parent,action),(child,_) in zip(path,path[1:]):
                        if parent!=child and parent in self.persistent and child in self.persistent:
                            self.persistent[parent].children[child] = action
        root_info = self.information.get(root_info.fast_key, root_info)
        rows = self.table.get(root_info.fast_key, {})
        estimates = tuple(ActionEstimate(a, rows[a].visits if a in rows else 0,
                                        rows[a].total / rows[a].visits if a in rows and rows[a].visits else None,
                                        self.priors.get(root_info.fast_key, {}).get(a))
                          for a in root_info.actions)
        covered = root_info.menu_complete and all(e.visits for e in estimates)
        # Finishing only the easy worlds can bias averages. Keep partial rows
        # for diagnostics, but do not recommend from censored outcomes. A global
        # work-budget interruption alone may retain already completed samples.
        enough = covered if self.searcher.require_root_coverage else any(e.visits for e in estimates)
        usable = (enough and self.completed > 0 and len(pool) == self.budget.worlds
                  and not any(reason not in {"advance_budget", "time_budget"} for reason in self.interruption_reasons))
        if usable:
            # Robust child recommendation; mean breaks equal-visit ties.
            best = max((e for e in estimates if e.visits), key=lambda e: (e.visits, e.mean_value))
            choice, value = best.action, best.mean_value
        else:
            choice, value = fallback, None
        cache_after = self.evaluation_counters()
        metrics = InformationSearchMetrics(self.worlds, self.started, self.completed, self.advances,
                                           self.terminals, self.leaves, self.interrupted,
                                           tuple(sorted(self.interruption_reasons.items())), len(self.table),
                                           (perf_counter() - started) * 1000,
                                           self.rollout_policy_choices, self.rollout_random_choices,
                                           self.history_attempts, self.history_rejected, self.history_advances,
                                           history_mismatch_reasons=tuple(sorted(self.history_mismatches.items())),
                                           selection=self.searcher.selection, prior_evaluations=self.prior_evaluations,
                                           reused_structure_nodes=self.reused_structure,
                                           reused_prior_nodes=self.reused_priors,
                                           reuse_reasons=((self.reuse_reason,1),),
                                           **{k:cache_after[k]-cache_before[k] for k in cache_before},
                                           world_initialization_ms=self.initialization_ms,
                                           advance_ms=self.advance_ms, simulator_advance_calls=self.advance_calls,
                                           transition_cache_hits=self.cache_hits,
                                           transition_cache_entries=len(self.transitions),
                                           root_actions=len(estimates),
                                           root_actions_visited=sum(e.visits > 0 for e in estimates),
                                           checkpoint_hits=sum(getattr(s, "checkpoint_hits", 0) for s, _ in pool),
                                           checkpoint_copies=sum(getattr(s,'checkpoint_copies',0) for s,_ in pool),
                                           checkpoint_copy_rejections=sum(getattr(s,'checkpoint_copy_rejections',0) for s,_ in pool),
                                           checkpoint_evictions=sum(getattr(s,'checkpoint_evictions',0) for s,_ in pool),
                                           checkpoint_capture_ms=sum(getattr(s,'checkpoint_capture_ms',0) for s,_ in pool),
                                           checkpoint_entries=sum(len(getattr(s, "_checkpoints", ())) for s, _ in pool),
                                           checkpoint_skipped_decisions=sum(getattr(s, "checkpoint_skipped_decisions", 0)
                                                                           for s, _ in pool),
                                           world_materializations=sum(getattr(getattr(s, "factory", None), "materializations", 0)
                                                                      for s, _ in pool),
                                           world_restores=sum(getattr(getattr(s, "factory", None), "restores", 0)
                                                              for s, _ in pool),
                                           trajectory_runs=self.trajectory_runs,
                                           hierarchy_nodes=sum(h.node_count for h in self.hierarchies.values()),
                                           hierarchy_selection_steps=sum(h.selection_steps for h in self.hierarchies.values()),
                                           forced_transitions=self.forced_transitions,
                                           root_coverage_required=self.searcher.require_root_coverage,
                                           forced_initial_coverage=self.searcher.force_initial_coverage,
                                           cpu_ms=(process_time() - cpu_started) * 1000,
                                           time_limit_ms=self.searcher.max_time_ms,
                                           minimum_trajectories=self.searcher.min_trajectories,
                                           projection_cache_hits=sum(getattr(s, "projection_cache_hits", 0) for s, _ in pool),
                                           projection_cache_entries=sum(len(getattr(s, "_projections", ())) for s, _ in pool),
                                           candidate_orderings=len(self.candidate_orders),
                                           eligible_actions=sum(self.eligible_counts.values()),
                                           reused_information_nodes=self.reused_nodes,
                                           reused_root_visits=self.reused_root_visits,
                                           forced_chain_hits=self.forced_chain_hits,
                                           forced_chain_steps=self.forced_chain_steps,
                                           native_forced_steps=sum(getattr(getattr(s, 'simulation_backend', None) or s,
                                               'native_forced_steps', 0) for s, _ in pool),
                                           candidate_expansions=sum(getattr(getattr(s, 'simulation_backend', None) or s,
                                               'candidate_expansions', 0) for s, _ in pool),
                                           candidate_cache_hits=sum(getattr(getattr(s, 'simulation_backend', None) or s,
                                               'candidate_cache_hits', 0) for s, _ in pool),
                                           structured_value_hits=sum(getattr(getattr(getattr(s, 'simulation_backend', None) or s,
                                               'value_store', None), 'hits', 0) for s, _ in pool),
                                           root_menu_complete=root_info.menu_complete,
                                           **self.population_metrics)
        fallback_reason = None
        if not usable:
            failures = [(name, n) for name, n in self.interruption_reasons.items() if name not in {"advance_budget", "time_budget"}]
            fallback_reason = (max(failures, key=lambda row: row[1])[0] if failures else
                "root_coverage" if enough is False and any(e.visits for e in estimates) else
                reason if reason in {"history_sampling_budget", "unsupported_historical_information", "simulator_budget", "advance_budget", "time_budget"} else
                "no_completed_trajectory")
        return InformationSearchResult(choice, value, not usable, covered, reason, estimates, metrics, fallback_reason)

    def time_exhausted(self):
        """!
        @brief Check the soft deadline after the minimum trajectory attempts.
        """
        return (self.deadline is not None
                and self.completed + self.interrupted >= self.searcher.min_trajectories
                and perf_counter() >= self.deadline)

    def evaluation_counters(self):
        """!
        @brief Read revision-scoped feature and evaluation cache counters.
        """
        result = dict(encoding_cache_hits=0,neural_cache_hits=0,encoding_ms=0.,inference_ms=0.)
        seen = set()
        for adapter in (self.searcher.action_prior,self.searcher.evaluator):
            model = getattr(adapter,'model',None)
            cache = getattr(model,'cache',None)
            if cache is None or id(model) in seen:
                continue
            seen.add(id(model))
            c = cache.counts
            result['encoding_cache_hits'] += c['state_hits']+c['menu_hits']
            result['neural_cache_hits'] += c['prediction_hits']+c['value_hits']
            result['encoding_ms'] += c['encoding_ms']
            result['inference_ms'] += c['inference_ms']
        return result

    def transition_key(self, sim, node, action):
        # Only native deterministic replay nodes qualify. A history identifies
        # one state within one sampled simulator, NOT across hidden worlds.
        # Cache lifetime is this search; no live runtime or mutable State is kept.
        """!
        @brief Identify a transition within its owning deterministic sampled world.
        """
        if (self.searcher.transition_cache_size and isinstance(sim, SearchSimulator)
                and isinstance(node, SimulationNode)):
            decision = node.decision
            info = node._projected_information
            if info is not None and info.candidate_model is not None:
                # Within ONE deterministic world, changing only admission width
                # cannot change execution of the same native descriptor. Keep
                # the cached child's original strict replay witness/width; do
                # not rebuild its history or share it with another sampled world.
                decision = ('frontier', info.fast_key, node.bind_action(action))
            return sim, (node.history, decision), action
        return None

    def store_transition(self, sim, node, action, child):
        """!
        @brief Cache detached transition facts without retaining a live engine request.
        """
        if not isinstance(child, SimulationNode):
            # Native forced callback boundaries are ephemeral and contain a
            # live request. Only detached public nodes may enter this cache.
            return
        key = self.transition_key(sim, node, action)
        if key is not None and len(self.transitions) < self.searcher.transition_cache_size:
            self.transitions[key] = child

    def cached_forced_chain(self, sim, key):
        """!
        @brief Pack exact cached edges through forced choices in one sampled world.

        The trajectory still consumes every logical step, updates budgets and
        stops at the same horizon. Only repeated history hashing/cache lookup
        is skipped; no observation or event is removed from player recall.
        """
        if not self.searcher.forced_chain_cache or not self.searcher.compress_forced or key is None:
            return ()
        if key in self.forced_chains:
            return self.forced_chains[key]
        start_key = key
        trail = []
        while key in self.transitions and len(trail) < 64:
            child = self.transitions[key]
            trail.append((key[2], child))
            if child.terminal or child.cutoff:
                break
            actions = child.player_information().actions
            if len(actions) != 1:
                break
            key = self.transition_key(sim, child, actions[0])
        if len(trail) < 2:
            return ()
        result = tuple(trail)
        if len(self.forced_chains) < self.searcher.transition_cache_size:
            self.forced_chains[start_key] = result
        return result

    def advance(self, sim, node, action):
        """!
        @brief Execute or reuse one transition under configured replay and budget checks.
        """
        key = self.transition_key(sim, node, action)
        if key is not None and key in self.transitions:
            self.cache_hits += 1
            return self.transitions[key]
        started = perf_counter()
        self.advance_calls += 1
        try:
            child = sim.advance_action(node, action)
        finally:
            self.advance_ms += (perf_counter() - started) * 1000
        self.store_transition(sim, node, action, child)
        return child

    def tree_action(self, info, *, horizon=None):
        # Only this player's information enters selection, never the simulator.
        """!
        @brief Select a complete action using information-keyed UCT/PUCT statistics.
        """
        key = info.fast_key
        remaining = horizon or (self.budget.max_depth, self.budget.max_extensions)
        if key in self.loaded_menus:
            self.loaded_menus.remove(key)
            previous = self.persistent[key].information
            if previous.actions != info.actions:
                # A new search may regenerate a narrower candidate prefix than
                # the retained node. Its information key deliberately ignores
                # width, but priors/order/hierarchy belong to a concrete menu.
                if self.priors.pop(key, None) is not None:
                    self.reused_priors -= 1
                self.candidate_orders.pop(key, None)
                self.hierarchies.pop(key, None)
                old_rows = self.table.get(key)
                if old_rows is not None and not set(old_rows) <= set(info.actions):
                    # Never admit stale actions through retained visit counts.
                    # Keep structure; restart counters for the offered prefix.
                    self.table.pop(key)
                    self.reused_nodes -= bool(old_rows.visits)
                    if key == self.searcher.tree_memory.root_key:
                        self.reused_root_visits = 0
        if key in self.loaded_keys and not self.searcher.reuse_rolling_horizon and self.horizons.get(key)!=remaining:
            self.table.pop(key, None)
            self.hierarchies.pop(key,None)
        self.loaded_keys.discard(key)
        self.horizons[key] = remaining
        if self.searcher.tree_reuse_size:
            self.information[key] = info
        rows = self.table.get(key)
        if rows is None:
            rows = self.table[key] = EdgeTable((a,_Statistics()) for a in info.actions)
        if self.searcher.tree_reuse_size:
            node = self.persistent.get(key)
            if node is None:
                node = self.persistent[key] = RetainedNode(info,rows)
            node.information, node.edges, node.horizon = info,rows,remaining
        if info.candidate_model is not None:
            if not set(rows) <= set(info.actions):
                raise ValueError('A growing candidate frontier removed admitted actions')
            grew = len(rows) != len(info.actions)
            for candidate in info.actions:
                rows.setdefault(candidate, _Statistics())
            self.information[key] = info
            if grew:
                # UCT has no priors, but its cached widening order must grow
                # too. Otherwise newly generated actions never become eligible.
                self.priors.pop(key, None)
                self.candidate_orders.pop(key, None)
                self.hierarchies.pop(key, None)
        if set(rows) != set(info.actions):
            raise ValueError("An information key has inconsistent offered actions")
        puct = self.searcher.selection != "uct"
        if puct and key not in self.priors:
            self.priors[key] = normalize_prior(info, self.searcher.action_prior(info))
            self.prior_evaluations += 1
        actions = info.actions
        if self.searcher.widening_constant:
            if key not in self.candidate_orders:
                self.candidate_orders[key] = self.candidate_ordering.order(info, self.priors.get(key))
            actions = self.widening.select(self.candidate_orders[key], rows.visits)
            self.eligible_counts[key] = len(actions)
        if self.searcher.selection == "hierarchical_puct":
            from game.ai.action_hierarchy import ActionHierarchy
            if key not in self.hierarchies:
                self.hierarchies[key] = ActionHierarchy(info.actions, self.priors[key])
                for candidate, stats in rows.items():
                    self.hierarchies[key].record_summary(candidate, stats.visits, stats.total)
            action, expanded = self.hierarchies[key].select(self.searcher.exploration, self.choice_rng,
                force_coverage=self.searcher.force_initial_coverage,
                allowed_actions=actions if self.searcher.widening_constant else None)
            return key, action, expanded
        unvisited = [a for a in actions if not rows[a].visits]
        if unvisited and (not puct or self.searcher.force_initial_coverage):
            # Explicit forced coverage variant: do not let a weak prior prevent
            # the existing recommendation safety check from ever completing.
            if puct:
                maximum = max(self.priors[key][a] for a in unvisited)
                unvisited = [a for a in unvisited if self.priors[key][a] == maximum]
            return key, self.choice_rng.choice(unvisited), True
        total = rows.visits
        exploration_base = sqrt(max(1,total)) if puct else log(total)
        def score(a):
            s = rows[a]
            bonus = (self.priors[key][a] * exploration_base / (1 + s.visits) if puct
                     else sqrt(exploration_base / s.visits))
            return (s.total / s.visits if s.visits else 0.0) + self.searcher.exploration * bonus
        scores = [(score(a), a) for a in actions]
        maximum = max(s for s, _ in scores)
        action = self.choice_rng.choice([a for s, a in scores if s == maximum])
        return key, action, rows[action].visits == 0

    def trajectory(self, sim, node):
        """!
        @brief Execute one object-owned traversal through an isolated sampled world.
        @return Completed backpropagation path and root-player leaf value.
        """
        from game.ai.search_trajectory import TrajectoryTraversal
        return TrajectoryTraversal(self, sim, node).execute()

    def trajectory_choices(self, node):
        """!
        @brief Common selection, leaf evaluation and logical budgets for both drivers.
        """
        path, expanded = [], False
        depth = extensions = 0
        while True:
            if node.terminal:
                self.terminals += 1
                view = (node.observation_readonly(self.player) if isinstance(node, SimulationNode)
                        else node.observation(self.player))
                return path, StateValueEvaluator().evaluate(view)
            if node.cutoff:
                raise _Interrupted("simulator_cutoff")
            info = node.player_information()
            if info.candidate_model is not None and not info.menu_complete:
                from game.ai.generation_candidates import CandidateExpansion
                visits = max(self.candidate_visits.get(info.fast_key, 0),
                             sum(s.visits for s in self.table.get(info.fast_key, {}).values()))
                width = self.widening.width(visits, minimum_constant=1)
                if width > info.candidate_width:
                    if self.time_exhausted():
                        raise _Interrupted('time_budget')
                    try:
                        node = yield CandidateExpansion(width)
                    except SimulationBudgetExceeded as exc:
                        raise _Interrupted('simulator_budget') from exc
                    continue
            forced = (self.searcher.compress_forced and info.menu_complete and len(info.actions) == 1)
            constructing = node.decision.kind in {"OrderNextRequest", "PriorityAbilityRequest", "AbilityConstructionRequest"}
            if depth >= self.budget.max_depth:
                compact = isinstance(node, (SimulationNode, ForcedBoundary))
                view = (node.observation_readonly(self.player) if compact
                        else node.observation(self.player))
                if quiet_priority(node.decision.kind, view):
                    evaluator = self.searcher.evaluator
                    structured = getattr(evaluator,'evaluate_structured',None)
                    if structured is not None and isinstance(node,SimulationNode):
                        value = float(structured(node.observation_payload(self.player),self.player))
                    else:
                        # External evaluators retain the detached-dict API.
                        value = float(evaluator.evaluate(view if type(evaluator) is StateValueEvaluator
                                      else node.observation(self.player)))
                    if not isfinite(value) or not -1 <= value <= 1:
                        raise ValueError("Leaf evaluator must return a finite value in [-1, 1]")
                    self.leaves += 1
                    limit = nextafter(1.0, 0.0)
                    return path, max(-limit, min(limit, value))
                if not forced and not constructing and extensions >= self.budget.max_extensions:
                    raise _Interrupted("extension_limit")
                extensions += not forced and not constructing
            info = node.player_information()
            if forced:
                action = info.actions[0]
                if not path:
                    self.table.setdefault(info.fast_key, EdgeTable(((action,_Statistics()),)))
                    path.append((info.fast_key, action))
            elif info.current.player != self.player:
                action = self.searcher.opponent_policy(info)
                if action not in info.actions:
                    raise ValueError("Opponent policy returned an action not offered to it")
            elif expanded:
                policy = self.searcher.rollout_policy
                if policy is not None and self.rollout_rng.random() >= self.searcher.rollout_exploration:
                    action = policy(info)
                    if action not in info.actions:
                        raise ValueError("Rollout policy returned an action not offered to it")
                    self.rollout_policy_choices += 1
                else:
                    # Preserve the original uniform stream when no policy is
                    # configured; guided rollouts get an independent stream.
                    rng = self.choice_rng if policy is None else self.rollout_rng
                    action = rng.choice(info.actions)
                    self.rollout_random_choices += 1
            else:
                key, action, expanded = self.tree_action(info,
                    horizon=(max(0, self.budget.max_depth - depth), self.budget.max_extensions - extensions))
                path.append((key, action))
            if self.advances >= self.budget.max_advances:
                raise _Interrupted("advance_budget")
            if self.time_exhausted():
                raise _Interrupted("time_budget")
            self.forced_transitions += int(forced)
            if info.candidate_model is not None:
                self.candidate_visits[info.fast_key] = self.candidate_visits.get(info.fast_key, 0) + 1
            self.advances += 1
            # Prefix microchoices construct one semantic decision; only its
            # completion consumes a depth step. Every microchoice still counts
            # against the hard advances/time/decision budgets.
            completes_order = (node.decision.kind == "OrderNextRequest" and
                info.current.readonly()["decision_context"]["remaining_count"] == 1)
            completes_ability = node.decision.kind in {"PriorityAbilityRequest", "AbilityConstructionRequest"}
            source_prefix = action.kind in {"PrioritySourceOption", "AbilityConstructionOption"}
            try:
                node = yield action
            except SimulationHistoryUnavailable as exc:
                raise _Interrupted("unsupported_historical_information") from exc
            except SimulationBudgetExceeded as exc:
                raise _Interrupted("simulator_budget") from exc
            depth += (not constructing or completes_order or completes_ability) and not source_prefix
