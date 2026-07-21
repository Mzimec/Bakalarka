from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .action_node_option_generator import ActionNodeOptionGenerator
    from .exec_plan_gen_pipeline import ExecutionPlanPipelineBase
    from .subability_generator import SubAbilityGenerator
    from .target_binding_generator import TargetBindingGenerator
    from ...ai.mana_solver import ManaSolver


@dataclass(frozen=True)
class ExecutionPlanStrategy:

    options_gen: ActionNodeOptionGenerator
    target_gen: TargetBindingGenerator
    mana_solver: ManaSolver | None


@dataclass(frozen=True)
class ActionGenerationStrategy:

    subability_gen: SubAbilityGenerator

    cost_pipeline: ExecutionPlanPipelineBase
    cost_strategy: ExecutionPlanStrategy

    action_pipeline: ExecutionPlanPipelineBase
    action_strategy: ExecutionPlanStrategy
