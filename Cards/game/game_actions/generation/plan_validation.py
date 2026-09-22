"""Shared legality checks for plans compiled by console and decision pipelines."""
from __future__ import annotations
from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import TYPE_CHECKING
from ..data_structs.game_action import ResolutionContext

if TYPE_CHECKING:
    from ..data_structs.ability import Ability
    from ..data_structs.game_action import AbilityExecutionPlan
    from ...game_state import State


class ExecutionPlanValidator:
    """Own target and cost validation shared by every plan consumer."""

    def validation_error(self, plan: AbilityExecutionPlan, ability: Ability, state: State, *, is_cost: bool) -> str | None:
        if not plan.binding.are_targets_valid(
            ability.source, ability.controller, state, plan.effects.get_used_slots()
        ):
            return "Targets were no longer valid."
        if is_cost:
            context = ResolutionContext(source=ability.source, controller=ability.controller,
                                        ability=ability.definition, action_key=ability.key, is_cost=True)
            for binding in plan.effects.sequence:
                error = binding.effect.validation_error(
                    state, replace(context, targets=plan._scope_binding(binding.slots))
                )
                if error:
                    return error
        return None

    def legal_plans(
        self, plans: Iterable[AbilityExecutionPlan], ability: Ability, state: State,
        *, is_cost: bool, strict: bool = False,
    ) -> Iterator[AbilityExecutionPlan]:
        """Validate before applying budgets; strict command input retains errors."""
        for plan in plans:
            error = self.validation_error(plan, ability, state, is_cost=is_cost)
            if error:
                if strict:
                    raise ValueError(error)
                continue
            yield plan


EXECUTION_PLAN_VALIDATOR = ExecutionPlanValidator()

# Compatibility entry points delegate to the single validation component.
plan_validation_error = EXECUTION_PLAN_VALIDATOR.validation_error
legal_execution_plans = EXECUTION_PLAN_VALIDATOR.legal_plans
