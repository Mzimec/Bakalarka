"""Explicit experimental MCTS composition shared by JSON configuration and play."""
from dataclasses import dataclass, fields
from math import isfinite, sqrt



@dataclass(frozen=True)
class SearchAgentConfig:
    # Required declaration, never inferred from a tournament opponent's deck.
    deck_knowledge: str
    search_seed: int = 7001
    worlds: int = 2
    population_size: int = 2
    iterations: int = 16
    max_depth: int = 2
    max_extensions: int = 8
    max_advances: int = 128
    max_attempts: int = 4
    max_prefix_advances: int = 256
    max_options: int = 4096
    max_simulator_decisions: int = 2048
    max_phases: int = 512
    history_exploration: float = 0.2
    condition_draws: bool = True
    condition_opening_reveal: bool = True
    world_backend: str = "current"
    search_backend: str = 'information_set'
    pimc_leaf_policy: str = 'horizon'
    information_set_leaf_policy: str = 'horizon'
    selection: str = "uct"
    prior: str = "uniform"
    exploration: float = sqrt(2)
    prior_temperature: float = 1.0
    prior_uniform_mix: float = 0.05
    transition_cache_size: int = 2048
    checkpoint_limit: int = 0
    checkpoint_interval: int = 4
    checkpoint_continuous: bool = True
    checkpoint_min_visits: int = 2
    checkpoint_admission: str = 'eager'
    reuse_world: bool = False
    trajectory_mode: str = "continuous"
    compress_forced: bool = True
    require_root_coverage: bool = False
    force_initial_coverage: bool = False
    max_search_ms: float = 0.0
    min_trajectories: int = 1
    simulator_views: str = "search"
    projection_cache_size: int = 128
    staged_priority: bool = False
    staged_ability: bool = False
    sequential_combat: bool | str = False
    structured_records: bool = True
    runtime_observations: bool = False
    native_forced: bool = True
    progressive_candidates: bool = False
    candidate_initial_width: int = 4
    candidate_beam_width: int = 32
    candidate_rank_window: int = 0
    candidate_rank_windows: tuple = ()
    candidate_native_ordering: bool = False
    candidate_attack_risk: bool = False
    candidate_combat_equivalence: bool = False
    widening_constant: float = 0.0
    widening_exponent: float = 0.5
    tree_reuse_size: int = 0
    tree_reuse_bytes: int = 16*1024*1024
    reuse_rolling_horizon: bool = False
    forced_chain_cache: bool = True
    parallel_workers: int = 1
    parallel_backend: str = 'process'
    model_checkpoint: str | None = None
    value_model: str = 'heuristic'
    neural_cache_size: int = 256
    neural_cache_bytes: int = 32*1024*1024
    exact_policy_cache_size: int = 0
    exact_policy_cache_bytes: int = 16*1024*1024
    exact_policy_canonical_ids: bool = False

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown MCTS settings")
        if data.get("deck_knowledge") != "both_lists":
            raise ValueError("Experimental MCTS requires explicit mcts.deck_knowledge='both_lists'")
        return cls(**data)

    def __post_init__(self):
        if self.information_set_leaf_policy not in ('horizon', 'first_leaf'):
            raise ValueError('information_set_leaf_policy must be horizon or first_leaf')
        if self.information_set_leaf_policy != 'horizon' and self.search_backend != 'information_set':
            raise ValueError('information_set_leaf_policy requires search_backend=information_set')
        if self.pimc_leaf_policy not in ('horizon', 'first_leaf'):
            raise ValueError('pimc_leaf_policy must be horizon or first_leaf')
        if self.pimc_leaf_policy != 'horizon' and self.search_backend != 'pimc':
            raise ValueError('pimc_leaf_policy requires search_backend=pimc')
        if self.search_backend not in ('information_set', 'pimc'):
            raise ValueError('search_backend must be information_set or pimc')
        if self.search_backend == 'pimc' and (self.world_backend != 'current' or self.parallel_workers != 1
                or self.selection not in ('uct', 'puct')
                or self.prior != 'uniform' or self.value_model != 'heuristic' or self.model_checkpoint is not None
                or self.runtime_observations or self.tree_reuse_size or self.checkpoint_limit
                or self.candidate_rank_window or self.candidate_rank_windows
                or self.sequential_combat or self.staged_priority or self.staged_ability
                or self.trajectory_mode != 'continuous'):
            raise ValueError('PIMC requires serial current worlds, uniform prior, native value, complete live choices '
                             'and no information caches/checkpoints/tree reuse')
        if type(self.runtime_observations) is not bool:
            raise ValueError('runtime_observations must be boolean')
        if self.runtime_observations and (self.simulator_views != 'search' or not self.structured_records
                or self.parallel_workers != 1 or self.world_backend != 'current'
                or self.value_model != 'heuristic' or self.prior not in {'uniform', 'heuristic'}):
            raise ValueError('Runtime observations require serial current worlds, structured search views and native heuristic readers')
        if (type(self.sequential_combat) is not bool
                and (type(self.sequential_combat) is not str or self.sequential_combat not in {'attackers', 'blockers'})):
            raise ValueError('sequential_combat must be boolean, attackers or blockers')
        if self.sequential_combat and self.parallel_workers != 1:
            raise ValueError('sequential_combat currently requires one search worker for its continuation plan')
        if any(type(n) is not int or n < 0 for n in (self.exact_policy_cache_size, self.exact_policy_cache_bytes)):
            raise ValueError('Exact policy cache limits must be nonnegative integers')
        if type(self.exact_policy_canonical_ids) is not bool:
            raise ValueError('exact_policy_canonical_ids must be boolean')
        if any(type(n) is not int or n<1 for n in (self.checkpoint_interval,self.checkpoint_min_visits)):
            raise ValueError('Checkpoint interval/visits must be positive integers')
        if type(self.checkpoint_continuous) is not bool:
            raise ValueError('checkpoint_continuous must be boolean')
        if self.checkpoint_admission not in ('eager', 'resumed'):
            raise ValueError('checkpoint_admission must be eager or resumed')
        if type(self.tree_reuse_bytes) is not int or self.tree_reuse_bytes < 0:
            raise ValueError('tree_reuse_bytes must be a nonnegative integer')
        if any(type(n) is not int or n < 0 for n in (self.neural_cache_size,self.neural_cache_bytes)):
            raise ValueError('Neural cache limits must be nonnegative integers')
        if type(self.parallel_workers) is not int or not 1 <= self.parallel_workers <= 61:
            raise ValueError('parallel_workers must be an integer in [1, 61]')
        if self.parallel_backend not in ('process', 'thread'):
            raise ValueError('parallel_backend must be process or thread')
        if self.parallel_workers > 1 and self.world_backend != 'current':
            raise ValueError('Parallel MCTS currently requires world_backend=current; history populations are stateful')
        if self.simulator_views not in {"full", "search"}:
            raise ValueError("simulator_views must be full or search")
        if type(self.projection_cache_size) is not int or self.projection_cache_size < 0:
            raise ValueError("projection_cache_size must be a nonnegative integer")
        if type(self.staged_priority) is not bool:
            raise ValueError("staged_priority must be boolean")
        if type(self.structured_records) is not bool:
            raise ValueError('structured_records must be boolean')
        if type(self.native_forced) is not bool:
            raise ValueError('native_forced must be boolean')
        if type(self.reuse_rolling_horizon) is not bool:
            raise ValueError('reuse_rolling_horizon must be boolean')
        if type(self.progressive_candidates) is not bool:
            raise ValueError('progressive_candidates must be boolean')
        if any(type(n) is not int or n < 2 for n in (self.candidate_initial_width, self.candidate_beam_width)):
            raise ValueError('Candidate widths must be integers >= 2')
        from game.ai.decisions.candidate_ranking import (rank_window, normalize_rank_windows)
        rank_window(self.candidate_rank_window)
        object.__setattr__(self, 'candidate_rank_windows', normalize_rank_windows(self.candidate_rank_windows))
        if (self.candidate_rank_window or self.candidate_rank_windows) and not self.progressive_candidates:
            raise ValueError('Candidate ranking requires progressive_candidates')
        if type(self.candidate_native_ordering) is not bool:
            raise ValueError('candidate_native_ordering must be boolean')
        if type(self.candidate_attack_risk) is not bool or self.candidate_attack_risk and not self.candidate_native_ordering:
            raise ValueError('candidate_attack_risk requires native candidate ordering and must be boolean')
        if type(self.candidate_combat_equivalence) is not bool or self.candidate_combat_equivalence and not self.candidate_native_ordering:
            raise ValueError('candidate_combat_equivalence requires native candidate ordering and must be boolean')
        if self.candidate_native_ordering and (not self.progressive_candidates or
                self.candidate_rank_window or self.candidate_rank_windows):
            raise ValueError('Native candidate ordering requires progressive_candidates without batch ranking')
        if self.progressive_candidates and (self.widening_constant < 1 or self.world_backend != 'current'):
            raise ValueError('Progressive candidate generation requires widening and current-position worlds')
        if type(self.staged_ability) is not bool or (self.staged_ability and not self.staged_priority):
            raise ValueError("staged_ability requires staged_priority")
        if type(self.forced_chain_cache) is not bool:
            raise ValueError("forced_chain_cache must be boolean")
        if type(self.tree_reuse_size) is not int or self.tree_reuse_size < 0:
            raise ValueError("tree_reuse_size must be a nonnegative integer")
        if self.tree_reuse_size and self.world_backend != "current":
            raise ValueError("Tree reuse currently requires world_backend=current")
        if (type(self.widening_constant) not in (int, float) or not isfinite(self.widening_constant)
                or (self.widening_constant != 0 and self.widening_constant < 1)):
            raise ValueError("widening_constant must be zero or at least one")
        if (type(self.widening_exponent) not in (int, float) or not isfinite(self.widening_exponent)
                or not 0 < self.widening_exponent <= 1):
            raise ValueError("widening_exponent must be in (0, 1]")
        if (type(self.min_trajectories) is not int or type(self.iterations) is not int
                or not 0 <= self.min_trajectories <= self.iterations):
            raise ValueError("min_trajectories must be between zero and iterations")
        if (type(self.max_search_ms) not in (int, float) or not isfinite(self.max_search_ms)
                or self.max_search_ms < 0):
            raise ValueError("max_search_ms must be finite and nonnegative")
        if any(type(v) is not bool for v in (self.compress_forced, self.require_root_coverage, self.force_initial_coverage)):
            raise ValueError("Search coverage/compression settings must be boolean")
        if self.trajectory_mode not in {"continuous", "replay"}:
            raise ValueError("trajectory_mode must be continuous or replay")
        if type(self.reuse_world) is not bool:
            raise ValueError("reuse_world must be boolean")
        if type(self.checkpoint_limit) is not int or self.checkpoint_limit < 0:
            raise ValueError("checkpoint_limit must be a nonnegative integer")
        if self.checkpoint_limit and self.world_backend != "current":
            raise ValueError("Checkpoints currently require world_backend=current")
        if type(self.transition_cache_size) is not int or self.transition_cache_size < 0:
            raise ValueError("transition_cache_size must be a nonnegative integer")
        if self.selection not in {"uct", "puct", "hierarchical_puct"}:
            raise ValueError("selection must be uct, puct or hierarchical_puct")
        if self.prior not in {"uniform", "heuristic", "neural"}:
            raise ValueError("prior must be uniform, heuristic or neural")
        if self.value_model not in {'heuristic','neural'}:
            raise ValueError('value_model must be heuristic or neural')
        if self.model_checkpoint is not None and (type(self.model_checkpoint) is not str or not self.model_checkpoint):
            raise ValueError('model_checkpoint must be a nonempty path string')
        if (self.prior == 'neural' or self.value_model == 'neural') and self.model_checkpoint is None:
            raise ValueError('Neural prior/value requires model_checkpoint')
        if self.model_checkpoint is not None and self.prior != 'neural' and self.value_model != 'neural':
            raise ValueError('model_checkpoint requires neural prior or value_model')
        if self.selection == "uct" and self.prior != "uniform":
            raise ValueError("heuristic prior requires puct selection")
        for name in ("exploration", "prior_temperature", "prior_uniform_mix"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError(f"Invalid MCTS {name}")
        if self.exploration < 0 or self.prior_temperature <= 0 or not 0 <= self.prior_uniform_mix <= 1:
            raise ValueError("Invalid MCTS exploration or prior parameters")
        if self.world_backend not in {"current", "history"}:
            raise ValueError("world_backend must be current or history")
        if self.deck_knowledge != "both_lists":
            raise ValueError("Declare both_lists deck knowledge")
        if type(self.search_seed) is not int:
            raise ValueError("search_seed must be an integer")
        for name in ("worlds", "population_size", "iterations", "max_depth", "max_extensions", "max_advances",
                     "max_attempts", "max_prefix_advances", "max_options", "max_simulator_decisions", "max_phases"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "max_extensions" else 1):
                raise ValueError(f"Invalid MCTS {name}")
        if (type(self.history_exploration) not in (int, float)
                or not 0 < self.history_exploration <= 1):
            raise ValueError("history_exploration must be in (0, 1]")
        if type(self.condition_draws) is not bool:
            raise ValueError("condition_draws must be boolean")
        if type(self.condition_opening_reveal) is not bool:
            raise ValueError("condition_opening_reveal must be boolean")


def make_search_controller(config, origin, *, max_decisions):
    """Parse application settings, then build one isolated AI object graph."""
    from game.ai.composition import SearchAgentBuilder
    return SearchAgentBuilder(SearchAgentConfig.parse(config), origin, max_decisions).build()
