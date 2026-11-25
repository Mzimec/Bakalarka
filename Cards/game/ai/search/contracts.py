"""Immutable search inputs/results shared by IS-MCTS and PIMC.

Owns portable data only: no engine, sampler or mutable runtime imports.
The historical InformationSearch names remain the serialized log schema.
"""
from dataclasses import dataclass
from game.ai.decisions.search_actions import PlayerAction


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
    @var checkpoint_captures Started checkpoint captures, including candidates not retained.
    @var checkpoint_evictions Number of checkpoint evictions recorded by this component.
    @var checkpoint_capture_ms Recorded checkpoint capture in milliseconds.
    @var checkpoint_copy_rejections Number of checkpoint copy rejections recorded by this component.
    @var policy_view_hits Exact policy-input cache hits, not information-node or world reuse.
    @var policy_action_hits Complete action ratings reused without rescoring.
    @var policy_action_misses Complete action ratings computed on a cache miss.
    @var policy_normalization_ms Time constructing or looking up exact policy input identities.
    @var trajectory_loop_ms Selection, execution, evaluation and backpropagation wall time; excludes world setup. Parallel results sum worker intervals.
    @var trajectory_loop_cpu_ms Process CPU time over the same serial loop; parallel results sum worker intervals.
    @var leaf_policy Horizon rollout or first new information-edge settled leaf.
    @var semantic_depth_total Sum of semantic depths over completed trajectories only.
    @var semantic_depth_max Maximum completed semantic depth, including settling extensions.
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
    checkpoint_captures: int = 0
    checkpoint_evictions: int = 0
    checkpoint_capture_ms: float = 0.0
    checkpoint_copy_rejections: int = 0
    policy_view_hits: int = 0
    policy_action_hits: int = 0
    policy_action_misses: int = 0
    policy_normalization_ms: float = 0.0
    trajectory_loop_ms: float = 0.0
    trajectory_loop_cpu_ms: float = 0.0
    leaf_policy: str = 'horizon'
    semantic_depth_total: int = 0
    semantic_depth_max: int = 0
    first_leaf_evaluations: int = 0
    horizon_evaluations: int = 0


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


