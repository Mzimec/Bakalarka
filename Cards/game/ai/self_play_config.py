"""Explicit actor/learner configuration, separate from live search parallelism."""
from dataclasses import dataclass, field, asdict
from math import isfinite

from game.simulation.search_config import SearchAgentConfig
from game.cards.decks import ARENA_STARTERS


@dataclass(frozen=True)
class SelfPlayConfig:
    """!
    @brief Validated immutable actor, replay, learner and evaluation configuration.
    @var actors Actors retained by this record.
    @var queued_games_per_actor Queued games per actor retained by this record.
    @var generations Generations retained by this record.
    @var games_per_generation Games per generation retained by this record.
    @var evaluation_games Evaluation games retained by this record.
    @var seed Seed retained by this record.
    @var decks Decks retained by this record.
    @var max_turns Max turns retained by this record.
    @var max_decisions Number of max decisions recorded by this component.
    @var replay_capacity Replay capacity retained by this record.
    @var batch_size Batch size retained by this record.
    @var max_training_actions Number of max training actions recorded by this component.
    @var updates_per_generation Updates per generation retained by this record.
    @var hidden Hidden retained by this record.
    @var learning_rate Learning rate retained by this record.
    @var weight_decay Weight decay retained by this record.
    @var value_weight Value weight retained by this record.
    @var clip_norm Clip norm retained by this record.
    @var temperature Temperature retained by this record.
    @var temperature_drop_turn Temperature drop turn retained by this record.
    @var dirichlet_alpha Dirichlet alpha retained by this record.
    @var dirichlet_epsilon Dirichlet epsilon retained by this record.
    @var validation_fraction Validation fraction retained by this record.
    @var bootstrap_generations Bootstrap generations retained by this record.
    @var collect_only Collect only retained by this record.
    @var log_matches Log matches retained by this record.
    @var mcts Mcts retained by this record.
    """
    actors: int = 2
    queued_games_per_actor: int = 1
    generations: int = 2
    games_per_generation: int = 4
    evaluation_games: int = 2
    seed: int = 42
    decks: tuple = ('red','white')
    max_turns: int = 100
    max_decisions: int = 10000
    replay_capacity: int = 2048
    batch_size: int = 16
    max_training_actions: int = 256
    updates_per_generation: int = 16
    hidden: int = 32
    learning_rate: float = .001
    weight_decay: float = .0001
    value_weight: float = 1.
    clip_norm: float = 5.
    temperature: float = 1.
    temperature_drop_turn: int = 8
    dirichlet_alpha: float = .3
    dirichlet_epsilon: float = .25
    validation_fraction: float = .1
    bootstrap_generations: int = 1
    collect_only: bool = False
    log_matches: bool = False
    mcts: dict = field(default_factory=lambda: dict(deck_knowledge='both_lists', selection='puct', prior='heuristic',
        iterations=16, worlds=2, max_depth=2, max_extensions=16, max_advances=2048, parallel_workers=1))

    def __post_init__(self):
        positive = ('actors','queued_games_per_actor','generations','games_per_generation','max_turns','max_decisions',
                    'replay_capacity','batch_size','updates_per_generation','hidden','max_training_actions')
        if any(type(getattr(self,k)) is not int or getattr(self,k)<1 for k in positive):
            raise ValueError('Actor counts, budgets and training sizes must be positive integers')
        if self.actors > 61 or self.batch_size > self.replay_capacity:
            raise ValueError('actors must be <=61 and batch_size <= replay_capacity')
        if any(type(getattr(self,k)) is not int or getattr(self,k)<0 for k in ('evaluation_games','bootstrap_generations','temperature_drop_turn')):
            raise ValueError('Invalid nonnegative count')
        if type(self.seed) is not int or self.seed < 0 or any(type(v) is not bool for v in (self.collect_only,self.log_matches)):
            raise ValueError('Invalid seed/boolean')
        for key in ('learning_rate','clip_norm','dirichlet_alpha'):
            if type(getattr(self,key)) not in (int,float) or not isfinite(getattr(self,key)) or getattr(self,key)<=0:
                raise ValueError('Invalid '+key)
        for key in ('weight_decay','value_weight','temperature'):
            if type(getattr(self,key)) not in (int,float) or not isfinite(getattr(self,key)) or getattr(self,key)<0:
                raise ValueError('Invalid '+key)
        for key in ('dirichlet_epsilon','validation_fraction'):
            if type(getattr(self,key)) not in (int,float) or not isfinite(getattr(self,key)) or not 0<=getattr(self,key)<1:
                raise ValueError('Invalid '+key)
        if len(self.decks)<2 or any(d not in ARENA_STARTERS for d in self.decks):
            raise ValueError('Declare at least two supported starter deck labels')
        object.__setattr__(self,'decks',tuple(self.decks))
        settings = SearchAgentConfig.parse(self.mcts)
        if settings.model_checkpoint is not None or settings.prior == 'neural' or settings.value_model != 'heuristic':
            raise ValueError('Actors receive generation snapshots in memory; do not embed a model checkpoint in mcts settings')
        if settings.parallel_workers != 1:
            raise ValueError('Self-play uses process actors; mcts.parallel_workers must be 1')
        if (settings.tree_reuse_size or settings.staged_priority or settings.staged_ability
                or settings.sequential_combat or settings.progressive_candidates):
            raise ValueError('Initial training contract requires fresh trees and complete final-action menus')
        if settings.max_search_ms:
            raise ValueError('Self-play requires a fixed work budget (max_search_ms=0)')
        if settings.selection == 'uct' and self.dirichlet_epsilon:
            raise ValueError('UCT has no prior: set dirichlet_epsilon=0')

    @classmethod
    def parse(cls, data):
        """!
        @brief Validate configuration fields and construct typed settings.
        """
        if not isinstance(data,dict) or set(data)-set(cls.__dataclass_fields__):
            raise ValueError('Unknown self-play settings')
        return cls(**data)

    def describe(self):
        """!
        @brief Return a detached mutable export while preserving immutable internal facts.
        """
        return asdict(self)
