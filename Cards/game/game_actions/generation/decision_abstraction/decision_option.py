"""Public decision-generation API; implementations live in dedicated modules."""
from .requests import *
from .policies import *
from .options import *
from .auxiliary import *
from .option_space import DecisionOptionSpace
from .parameters import AbilityParameters
from .registry import DECISION_OPTION_GENERATORS, AUXILIARY_GENERATORS, route_decision_generation
from .action_pipelines import (
    AbilityDecisionGenerationPipeline, PriorityDecisionGenerationPipeline, ManaDecisionGenerationPipeline,
)
from .selection_pipelines import (
    DeclareAttackersPipeline, DeclareBlockersPipeline, MulliganPipeline,
    MulliganBottomPipeline, DiscardPipeline, AbilityResolutionPipeline,
)
from ..generation_strategy import action_generation_strategy

# Compatibility name for the shared strategy factory.
default_ability_strategy = action_generation_strategy
