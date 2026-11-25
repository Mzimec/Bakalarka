"""Build one isolated AI object graph from validated application settings.

This is the composition root, not a running search session. Each build owns
fresh policy caches, world provider and tree memory; models load only on build.
The application parses JSON in game.simulation.search_config before entering
this layer. Search and simulation do not import that application module.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.ai.agents.mcts_agent import MCTSAgent
from game.ai.decisions.generation_policy import SearchGenerationPolicy
from game.ai.evaluation.action_priors import HeuristicActionPrior, UniformActionPrior
from game.ai.evaluation.exact_policy_memory import ExactPolicyMemory
from game.ai.evaluation.observation_policy import ObservationOpponentPolicy
from game.ai.sampling.game_origin import GameOrigin
from game.ai.search.contracts import InformationSearchBudget

if TYPE_CHECKING:
    from game.simulation.search_config import SearchAgentConfig


def make_neural_policy_controller(model, *, max_decisions=10000, max_options=4096,
                                  policy_for=None, origin=None):
    """Reuse the live observation/legal-option boundary without constructing search.

    model is an already loaded PolicyValueModel. Schemas 1/2 consume only the
    actor view; schema 3 also needs explicitly permitted immutable deck profiles.
    Keep generation compatible with the final-action menus used for training.
    """
    from game.ai.training.training_model import NeuralPolicy
    if getattr(model, 'feature_version', 1) >= 3:
        if origin is not None:
            model.bind_decks(origin.decks, origin.catalog_factory())
        elif not model.feature_encoder.profiles:
            raise ValueError('Deck-aware policy needs explicitly permitted deck profiles or GameOrigin')
    return MCTSAgent(world_provider=None, policy_only=True, fallback=NeuralPolicy(model),
                     policy_for=policy_for if policy_for is not None else SearchGenerationPolicy(),
                     max_decisions=max_decisions, max_options=max_options,
                     semantic_encoder=getattr(model,'semantic_encoder',None))


@dataclass(frozen=True)
class SearchAgentBuilder:
    """Immutable recipe; built mutable components belong to the returned agent."""
    settings: 'SearchAgentConfig'
    origin: GameOrigin
    max_decisions: int

    def __post_init__(self):
        if not isinstance(self.origin, GameOrigin):
            raise ValueError('MCTS requires an explicit compatible GameOrigin (use play or run_config)')

    def build(self):
        """Assemble the controller, sampler, search and optional model once."""
        policy = self._policy()
        generation = self._generation_policy()
        provider = self._world_provider(policy, generation)
        model = self._model()
        if model is not None and model.feature_version >= 2 and self.settings.runtime_observations:
            raise ValueError('Semantic neural models currently require snapshot observations')
        if model is not None and model.feature_version >= 2 and self.settings.parallel_workers != 1:
            raise ValueError('Semantic neural models currently require serial search')
        search = self._search(policy, model)
        if model is not None:
            search.invalidate_model(model.revision)
        if self.settings.parallel_workers > 1:
            from game.ai.search.parallel_search import ParallelInformationSetSearch
            search = ParallelInformationSetSearch(search, workers=self.settings.parallel_workers,
                                                  backend=self.settings.parallel_backend)
        return MCTSAgent(world_provider=provider, search=search, fallback=policy,
            policy_for=generation, seed=self.settings.search_seed, max_options=self.settings.max_options,
            max_decisions=self.max_decisions, search_setup=False, allow_incomplete_menu_fallback=True,
            semantic_encoder=getattr(model,'semantic_encoder',None))

    def _policy(self):
        settings = self.settings
        memory = (ExactPolicyMemory(settings.exact_policy_cache_size, settings.exact_policy_cache_bytes,
                                   canonical_ids=settings.exact_policy_canonical_ids)
                  if settings.exact_policy_cache_size and settings.exact_policy_cache_bytes else None)
        return ObservationOpponentPolicy(memory=memory)

    def _generation_policy(self):
        settings = self.settings
        return SearchGenerationPolicy(
            staged_priority=settings.staged_priority, staged_ability=settings.staged_ability,
            progressive_candidates=settings.progressive_candidates,
            candidate_initial_width=settings.candidate_initial_width,
            candidate_beam_width=settings.candidate_beam_width,
            candidate_rank_window=settings.candidate_rank_window,
            candidate_rank_windows=settings.candidate_rank_windows,
            candidate_native_ordering=settings.candidate_native_ordering,
            candidate_attack_risk=settings.candidate_attack_risk,
            candidate_combat_equivalence=settings.candidate_combat_equivalence,
            sequential_combat=settings.sequential_combat)

    def _world_provider(self, policy, generation):
        """Select one evidence boundary; both backends keep their original caps."""
        settings = self.settings
        common = dict(policy_for=generation, max_options=settings.max_options,
                      max_decisions=settings.max_simulator_decisions, max_phases=settings.max_phases)
        if settings.world_backend == 'current':
            from game.ai.sampling.current_position_worlds import CurrentPositionWorlds
            return CurrentPositionWorlds(self.origin, **common,
                continuation_attempts=settings.max_attempts,
                continuation_advances=settings.max_prefix_advances,
                history_exploration=settings.history_exploration,
                checkpoint_limit=settings.checkpoint_limit, checkpoint_interval=settings.checkpoint_interval,
                checkpoint_continuous=settings.checkpoint_continuous,
                checkpoint_min_visits=settings.checkpoint_min_visits,
                checkpoint_admission=settings.checkpoint_admission, reuse_world=settings.reuse_world,
                view_mode=settings.simulator_views, structured_records=settings.structured_records,
                native_forced=settings.native_forced, projection_cache_size=settings.projection_cache_size)
        from game.ai.sampling.game_origin import GameOriginWorlds
        from game.ai.sampling.history_world_provider import ExploratoryHistoryPolicy
        from game.ai.sampling.incremental_world_provider import IncrementalWorldProvider
        worlds = GameOriginWorlds(self.origin, **common, condition_draws=settings.condition_draws,
                                 condition_opening_reveal=settings.condition_opening_reveal)
        return IncrementalWorldProvider(worlds, size=settings.population_size, seed=settings.search_seed,
            opponent_weights=ExploratoryHistoryPolicy(policy, exploration=settings.history_exploration),
            max_attempts=settings.max_attempts, max_prefix_advances=settings.max_prefix_advances)

    def _model(self):
        if self.settings.model_checkpoint is None:
            return None
        from game.ai.training.training_model import PolicyValueModel
        model = PolicyValueModel.load(self.settings.model_checkpoint)
        model.configure_cache(self.settings.neural_cache_size, self.settings.neural_cache_bytes)
        if model.feature_version >= 3:
            model.bind_decks(self.origin.decks, self.origin.catalog_factory())
        return model

    def _prior(self, policy, model):
        settings = self.settings
        if settings.selection == 'uct':
            return None
        if settings.prior == 'neural':
            from game.ai.training.training_model import NeuralPrior
            return NeuralPrior(model)
        if settings.prior == 'heuristic':
            return HeuristicActionPrior(policy, temperature=settings.prior_temperature,
                                        uniform_mix=settings.prior_uniform_mix)
        return UniformActionPrior()

    def _search(self, policy, model):
        """Choose a search model without constructing and discarding another one."""
        settings = self.settings
        budget = InformationSearchBudget(worlds=settings.worlds, iterations=settings.iterations,
            max_advances=settings.max_advances, max_depth=settings.max_depth,
            max_extensions=settings.max_extensions)
        common = dict(budget=budget, selection=settings.selection, exploration=settings.exploration,
            max_time_ms=settings.max_search_ms, min_trajectories=settings.min_trajectories,
            require_root_coverage=settings.require_root_coverage,
            widening_constant=settings.widening_constant, widening_exponent=settings.widening_exponent)
        if settings.search_backend == 'pimc':
            from game.ai.search.pimc_search import PIMCSearch
            return PIMCSearch(**common, leaf_policy=settings.pimc_leaf_policy)
        from game.ai.search.information_set_search import InformationSetSearch
        search = InformationSetSearch(**common, opponent_policy=policy, rollout_policy=policy,
            action_prior=self._prior(policy, model), leaf_policy=settings.information_set_leaf_policy,
            transition_cache_size=settings.transition_cache_size, trajectory_mode=settings.trajectory_mode,
            compress_forced=settings.compress_forced, force_initial_coverage=settings.force_initial_coverage,
            tree_reuse_size=settings.tree_reuse_size, tree_reuse_bytes=settings.tree_reuse_bytes,
            runtime_observations=settings.runtime_observations,
            reuse_rolling_horizon=settings.reuse_rolling_horizon, forced_chain_cache=settings.forced_chain_cache)
        if settings.value_model == 'neural':
            from game.ai.training.training_model import NeuralValue
            search.evaluator = NeuralValue(model)
        return search
