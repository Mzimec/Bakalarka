"""Lazy legal choices for one command step; no operations are materialized."""

from dataclasses import replace
from game.game_actions.data_structs.ability import SubAbilityComposer
from game.game_actions.data_structs.game_action import ResolutionContext
from game.game_actions.generation.ability_action_gen_pipeline import ActionGenerationContext
from game.game_actions.generation.exec_plan_gen_pipeline import ExecutionPlanPipeline
from game.game_actions.generation.generation_strategy import ExecutionPlanStrategy
from game.game_actions.generation.action_node_option_generator import (
    SelectedActionNodeOptionGenerator,
)
from game.game_actions.generation.target_binding_generator import FullTargetBindingGenerator
from game.ai.mana_solver import SourceActivatingManaSolver


class UnsupportedCommandDefinition(ValueError):
    pass


def legal_plans(ability, state, *, cost=False, mode=None, mana_solver=None, x_value=0):
    """!
    @brief Search only until the caller has enough witnesses (usually one or two).

    @param mana_solver Optional payment policy; defaults to automatic source activation.
    @param x_value Chosen X passed to both payment and effect parameter generation.
    """
    definition = ability.definition
    if definition.subdefs:
        raise UnsupportedCommandDefinition(
            "This definition requires choices not supported by the command builder."
        )
    if definition.validation_error(ability.source, ability.controller, state):
        return
    part = SubAbilityComposer(
        definition.cost_subdefs if cost else definition.action_subdefs
    ).compile(ability, state)
    count = sum(1 for _ in part.action_node.generate_options()) if part and part.action_node else 1
    ctx = ActionGenerationContext(ability)
    ctx.x_value = x_value
    if cost and definition.is_spell:
        ctx.mana_cost = ability.source.get_casting_cost(state)
    context = ResolutionContext(
        source=ability.source, controller=ability.controller, ability=definition, is_cost=cost
    )
    for index in range(1, count + 1):
        if mode is not None and index != mode:
            continue
        strategy = ExecutionPlanStrategy(
            SelectedActionNodeOptionGenerator(index),
            FullTargetBindingGenerator(),
            (mana_solver or SourceActivatingManaSolver()) if cost else None,
        )
        for plan in ExecutionPlanPipeline().generate(ctx, part, strategy, state):
            if not plan.binding.are_targets_valid(
                ability.source, ability.controller, state, plan.effects.get_used_slots()
            ):
                continue
            if cost and any(
                binding.effect.validation_error(
                    state, replace(context, targets=plan._scope_binding(binding.slots))
                )
                for binding in plan.effects.sequence
            ):
                continue
            yield index, plan


def feasible(ability, state):
    return (
        next(legal_plans(ability, state, cost=True), None) is not None
        and next(legal_plans(ability, state), None) is not None
    )


def target_groups(plan):
    slots = sorted(
        plan.effects.get_used_slots(), key=lambda slot: (slot.slot.key, slot.runtime_key)
    )
    return tuple(
        tuple(
            target
            for target, count in plan.binding[slot.slot.key][slot.runtime_key].items()
            for _ in range(count)
        )
        for slot in slots
    )
