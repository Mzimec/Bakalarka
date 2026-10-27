"""Explicit experimental MCTS composition shared by JSON configuration and play."""
from dataclasses import dataclass, fields
from math import isfinite, sqrt
from functools import cached_property

from game.ai.game_origin import GameOrigin, GameOriginWorlds
from game.ai.history_world_provider import ExploratoryHistoryPolicy
from game.ai.incremental_world_provider import IncrementalWorldProvider
from game.ai.information_set_search import InformationSetSearch, InformationSearchBudget
from game.ai.mcts_agent import MCTSAgent
from game.ai.observation_policy import ObservationOpponentPolicy
from game.ai.action_priors import HeuristicActionPrior, UniformActionPrior


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
    structured_records: bool = True
    native_forced: bool = True
    progressive_candidates: bool = False
    candidate_initial_width: int = 4
    candidate_beam_width: int = 32
    candidate_rank_window: int = 0
    candidate_rank_windows: tuple = ()
    candidate_native_ordering: bool = False
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

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown MCTS settings")
        if data.get("deck_knowledge") != "both_lists":
            raise ValueError("Experimental MCTS requires explicit mcts.deck_knowledge='both_lists'")
        return cls(**data)

    def __post_init__(self):
        if any(type(n) is not int or n<1 for n in (self.checkpoint_interval,self.checkpoint_min_visits)):
            raise ValueError('Checkpoint interval/visits must be positive integers')
        if type(self.checkpoint_continuous) is not bool:
            raise ValueError('checkpoint_continuous must be boolean')
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
        from game.ai.candidate_ranking import rank_window, normalize_rank_windows
        rank_window(self.candidate_rank_window)
        object.__setattr__(self, 'candidate_rank_windows', normalize_rank_windows(self.candidate_rank_windows))
        if (self.candidate_rank_window or self.candidate_rank_windows) and not self.progressive_candidates:
            raise ValueError('Candidate ranking requires progressive_candidates')
        if type(self.candidate_native_ordering) is not bool:
            raise ValueError('candidate_native_ordering must be boolean')
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


def search_generation_policy(request):
    """Explicit automatic-payment menu, identical for live and replay players.

Separate mana activations and concession are excluded; no heuristic top-k is
applied. Replay rejects oversized menus; live emergency choices disable further
search in that game and are logged. General parameter/candidate policies
remain a separate extension, not silently borrowed from SimpleAgent.
"""
    from game.ai.decision_maker import PriorityDecisionRequest
    from game.game_actions.generation.decision_abstraction.policies import PriorityGenerationPolicy
    if isinstance(request, PriorityDecisionRequest):
        # A private effect in a sampled opponent hand must not toggle a global
        # symmetry guard and change the actor's menu. Search exposes concrete
        # sources until equivalence has an actor-observation proof contract.
        return PriorityGenerationPolicy(include_mana=False, include_concede=False, equivalence=None)
    return None


@dataclass(frozen=True)
class SearchGenerationPolicy:
    staged_priority: bool = False
    staged_ability: bool = False
    progressive_candidates: bool = False
    candidate_initial_width: int = 4
    candidate_beam_width: int = 32
    candidate_rank_window: int = 0
    candidate_rank_windows: tuple = ()
    candidate_native_ordering: bool = False

    def __post_init__(self):
        from game.ai.candidate_ranking import rank_window, normalize_rank_windows
        rank_window(self.candidate_rank_window)
        object.__setattr__(self, 'candidate_rank_windows', normalize_rank_windows(self.candidate_rank_windows))

    @cached_property
    def candidate_model(self):
        if not self.progressive_candidates:
            return None
        from game.ai.generation_candidates import DeterministicCandidates
        return DeterministicCandidates(self.candidate_initial_width, self.candidate_beam_width,
            self.candidate_rank_window, self.candidate_rank_windows, self.candidate_native_ordering)

    def __call__(self, request):
        from dataclasses import replace
        from game.ai.decision_maker import PriorityDecisionRequest
        policy = search_generation_policy(request)
        if isinstance(request, PriorityDecisionRequest) and self.staged_priority:
            policy = replace(policy, staged_sources=True, staged_ability=self.staged_ability)
        return policy


def make_search_controller(config, origin, *, max_decisions):
    settings = SearchAgentConfig.parse(config)
    if not isinstance(origin, GameOrigin):
        raise ValueError("MCTS requires an explicit compatible GameOrigin (use play or run_config)")
    policy = ObservationOpponentPolicy()
    generation_policy = SearchGenerationPolicy(settings.staged_priority, settings.staged_ability,
        settings.progressive_candidates, settings.candidate_initial_width, settings.candidate_beam_width,
        settings.candidate_rank_window, settings.candidate_rank_windows, settings.candidate_native_ordering)
    if settings.world_backend == "current":
        from game.ai.current_position_worlds import CurrentPositionWorlds
        provider = CurrentPositionWorlds(origin, policy_for=generation_policy,
            continuation_attempts=settings.max_attempts,
            continuation_advances=settings.max_prefix_advances,
            history_exploration=settings.history_exploration,
            checkpoint_limit=settings.checkpoint_limit,
            checkpoint_interval=settings.checkpoint_interval,
            checkpoint_continuous=settings.checkpoint_continuous,
            checkpoint_min_visits=settings.checkpoint_min_visits,
            reuse_world=settings.reuse_world,
            view_mode=settings.simulator_views,
            structured_records=settings.structured_records,
            native_forced=settings.native_forced,
            projection_cache_size=settings.projection_cache_size,
            max_options=settings.max_options, max_decisions=settings.max_simulator_decisions,
            max_phases=settings.max_phases)
    else:
        worlds = GameOriginWorlds(origin, policy_for=generation_policy, max_options=settings.max_options,
            max_decisions=settings.max_simulator_decisions, max_phases=settings.max_phases,
            condition_draws=settings.condition_draws, condition_opening_reveal=settings.condition_opening_reveal)
        provider = IncrementalWorldProvider(worlds, size=settings.population_size, seed=settings.search_seed,
            opponent_weights=ExploratoryHistoryPolicy(policy, exploration=settings.history_exploration),
            max_attempts=settings.max_attempts, max_prefix_advances=settings.max_prefix_advances)
    prior = None
    model = None
    if settings.model_checkpoint is not None:
        from game.ai.training_model import PolicyValueModel, NeuralPrior, NeuralValue
        model = PolicyValueModel.load(settings.model_checkpoint)
        model.configure_cache(settings.neural_cache_size,settings.neural_cache_bytes)
    if settings.selection != "uct":
        prior = (NeuralPrior(model) if settings.prior == 'neural' else
                 HeuristicActionPrior(policy, temperature=settings.prior_temperature,
                 uniform_mix=settings.prior_uniform_mix) if settings.prior == "heuristic" else UniformActionPrior())
    search = InformationSetSearch(opponent_policy=policy, rollout_policy=policy,
        selection=settings.selection, action_prior=prior, exploration=settings.exploration,
        transition_cache_size=settings.transition_cache_size,
        trajectory_mode=settings.trajectory_mode,
        compress_forced=settings.compress_forced, require_root_coverage=settings.require_root_coverage,
        force_initial_coverage=settings.force_initial_coverage,
        max_time_ms=settings.max_search_ms,
        min_trajectories=settings.min_trajectories,
        widening_constant=settings.widening_constant, widening_exponent=settings.widening_exponent,
        tree_reuse_size=settings.tree_reuse_size,
        tree_reuse_bytes=settings.tree_reuse_bytes,
        reuse_rolling_horizon=settings.reuse_rolling_horizon,
        forced_chain_cache=settings.forced_chain_cache,
        budget=InformationSearchBudget(
        worlds=settings.worlds, iterations=settings.iterations, max_advances=settings.max_advances,
        max_depth=settings.max_depth, max_extensions=settings.max_extensions))
    if settings.value_model == 'neural':
        search.evaluator = NeuralValue(model)
    if model is not None:
        search.invalidate_model(model.revision)
    if settings.parallel_workers > 1:
        from game.ai.parallel_search import ParallelInformationSetSearch
        search = ParallelInformationSetSearch(search, workers=settings.parallel_workers, backend=settings.parallel_backend)
    return MCTSAgent(world_provider=provider, search=search, fallback=policy,
        policy_for=generation_policy, seed=settings.search_seed, max_options=settings.max_options,
        max_decisions=max_decisions, search_setup=False, allow_incomplete_menu_fallback=True)
