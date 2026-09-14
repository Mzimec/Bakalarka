"""Injectable policies used by action and execution-plan generation."""

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
    """!
    @brief Bundle of policies used to generate one execution plan.

    Defines how action-node options and target bindings are generated,
    and optionally how mana requirements are solved.

    @var options_gen
        Generator used to enumerate or validate `ActionNodeOption`s.
    @var target_gen
        Generator used to enumerate or validate target bindings.
    @var mana_solver
        Solver used to produce a legal mana payment plan, or `None`
        when this execution-plan side does not pay mana.
    """

    options_gen: ActionNodeOptionGenerator
    target_gen: TargetBindingGenerator
    mana_solver: ManaSolver | None


@dataclass(frozen=True)
class ActionGenerationStrategy:
    """!
    @brief Collection of policies controlling complete ability-action generation.

    Separates generation of the selected sub-ability, its cost execution
    plan, and its main action execution plan. This allows the same
    pipelines to be reused for exhaustive AI generation and validation
    of player-supplied choices.

    @var subability_gen
        Generator selecting the cost/action sub-ability pair to process.
    @var cost_pipeline
        Pipeline compiling the cost sub-ability into execution plans.
    @var cost_strategy
        Policies used by the cost execution-plan pipeline.
    @var action_pipeline
        Pipeline compiling the main action sub-ability into execution plans.
    @var action_strategy
        Policies used by the main action execution-plan pipeline.
    """

    subability_gen: SubAbilityGenerator

    cost_pipeline: ExecutionPlanPipelineBase
    cost_strategy: ExecutionPlanStrategy

    action_pipeline: ExecutionPlanPipelineBase
    action_strategy: ExecutionPlanStrategy

