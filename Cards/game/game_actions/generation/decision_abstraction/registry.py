"""Request-to-pipeline wiring and contract checks; game rules live in pipelines."""
from collections.abc import Iterator
from typing import cast
from immutabledict import immutabledict
from .requests import *
from .policies import *
from .options import DecisionOption
from .action_pipelines import (
    AbilityDecisionGenerationPipeline, PriorityDecisionGenerationPipeline, ManaDecisionGenerationPipeline,
)
from .selection_pipelines import (
    DeclareAttackersPipeline, DeclareBlockersPipeline, MulliganPipeline,
    MulliganBottomPipeline, DiscardPipeline, AbilityResolutionPipeline,
)
from .auxiliary import *
from .auxiliary import ObjectPipeline, OrderPipeline, BooleanPipeline, ScryPipeline, CombatDamagePipeline


AUXILIARY_GENERATORS = {
    StartingPlayerRequest: (ObjectPipeline(StartingPlayerOption), StartingPlayerPolicy),
    TriggerOrderRequest: (OrderPipeline(TriggerOrderOption), TriggerOrderPolicy),
    LegendRequest: (ObjectPipeline(LegendOption), LegendPolicy),
    ReplacementRequest: (ObjectPipeline(ReplacementOption), ReplacementPolicy),
    ReplacementOrderRequest: (OrderPipeline(ReplacementOrderOption), ReplacementOrderPolicy),
    OptionalEffectRequest: (BooleanPipeline(OptionalEffectOption), OptionalEffectPolicy),
    ReplacementAcceptanceRequest: (BooleanPipeline(ReplacementAcceptanceOption), ReplacementAcceptancePolicy),
    ScryRequest: (ScryPipeline(), ScryPolicy),
    CombatDamageRequest: (CombatDamagePipeline(), CombatDamagePolicy),
}


DECISION_OPTION_GENERATORS = immutabledict({
    **AUXILIARY_GENERATORS,
    PriorityDecisionRequest: (PriorityDecisionGenerationPipeline(), PriorityGenerationPolicy),
    AbilityDecisionRequest: (AbilityDecisionGenerationPipeline(), AbilityGenerationPolicy),
    DeclareAttackersRequest: (DeclareAttackersPipeline(), DeclareAttackersPolicy),
    DeclareBlockersRequest: (DeclareBlockersPipeline(), DeclareBlockersPolicy),
    MulliganRequest: (MulliganPipeline(), MulliganPolicy),
    MulliganBottomRequest: (MulliganBottomPipeline(), MulliganBottomPolicy),
    DiscardRequest: (DiscardPipeline(), DiscardPolicy),
    AbilityResolutionRequest: (AbilityResolutionPipeline(), AbilityResolutionPolicy),
    ManaGenerationRequest: (ManaDecisionGenerationPipeline(), ManaGenerationPolicy),
})


def route_decision_generation[T: DecisionOption](
    request: DecisionRequest[T], policy: GenerationPolicy | None = None,
) -> Iterator[T]:
    registration = DECISION_OPTION_GENERATORS.get(type(request))
    if registration is None:
        raise ValueError(f"No decision generation pipeline registered for {type(request).__name__}")
    pipeline, policy_type = registration
    if policy is None:
        policy = policy_type()
    if not isinstance(policy, policy_type):
        raise TypeError(f"{type(request).__name__} requires {policy_type.__name__}")
    if request.player not in request.participants:
        raise ValueError("The requesting player must belong to the game.")
    for option in pipeline.generate(request, policy):
        if not isinstance(option, request.option_type):
            raise TypeError(f"Generation must yield {request.option_type.__name__} values.")
        yield cast(T, option)


