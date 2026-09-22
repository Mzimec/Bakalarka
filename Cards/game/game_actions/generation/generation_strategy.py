"""Injectable policies used by action and execution-plan generation."""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .action_node_option_generator import ActionNodeOptionGenerator
    from .exec_plan_gen_pipeline import ExecutionPlanPipelineBase
    from .subability_generator import SubAbilityGenerator
    from .target_binding_generator import TargetBindingGenerator
    from ...mana.mana_solver import ManaSolver
    from .pruning.pruning_strategy import PruningStrategy


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


    # Budgets apply to valid plans, before the cost/effect Cartesian product.
    cost_plan_pruning: PruningStrategy | None = None
    action_plan_pruning: PruningStrategy | None = None


def execution_plan_strategy(*, mode=None, targets=None, mana_solver=None) -> ExecutionPlanStrategy:
    """Compose existing generators for exhaustive or explicitly selected input.

    None means enumerate targets/modes; an empty tuple is an explicit selection
    of no targets. A solver is supplied only for a cost-side strategy.
    """
    from .action_node_option_generator import FullActionNodeOptionGenerator, SelectedActionNodeOptionGenerator
    from .target_binding_generator import FullTargetBindingGenerator, ProvidedTargetBindingGenerator
    return ExecutionPlanStrategy(
        FullActionNodeOptionGenerator() if mode is None else SelectedActionNodeOptionGenerator(mode),
        FullTargetBindingGenerator() if targets is None else ProvidedTargetBindingGenerator(targets),
        mana_solver,
    )


def action_generation_strategy(
    *, targets=None, cost_targets=None, mode=None, cost_mode=None,
    mana_solver=None, subability_gen=None, cost_plan_pruning=None, action_plan_pruning=None,
) -> ActionGenerationStrategy:
    """One composition root shared by decision spaces and command adapters."""
    from .exec_plan_gen_pipeline import ExecutionPlanPipeline
    from .subability_generator import FullSubAbilityGenerator
    from ...mana.mana_solver import SourceActivatingManaSolver
    return ActionGenerationStrategy(
        subability_gen if subability_gen is not None else FullSubAbilityGenerator(),
        ExecutionPlanPipeline(),
        execution_plan_strategy(mode=cost_mode, targets=cost_targets,
                                mana_solver=mana_solver if mana_solver is not None else SourceActivatingManaSolver()),
        ExecutionPlanPipeline(),
        execution_plan_strategy(mode=mode, targets=targets),
        cost_plan_pruning=cost_plan_pruning,
        action_plan_pruning=action_plan_pruning,
    )
