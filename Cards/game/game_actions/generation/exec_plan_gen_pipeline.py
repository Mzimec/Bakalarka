from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from.ability_action_gen_pipeline import ActionGenerationContextBase
    from .generation_strategy import ExecutionPlanStrategy
    from ..data_structs.ability import SubAbilityBase
    from ...game_state import State
    from ...ai.mana_solver import ManaSolverResult

from ..data_structs.game_action import AbilityExecutionPlan
from ..data_structs.ability import EffectSequence
from ...target.target_resolver import FrozenTargetBinding


class ExecutionPlanPipelineBase(ABC):

    @abstractmethod
    def generate(
        self, 
        ctx: ActionGenerationContextBase, 
        subability: SubAbilityBase, 
        strategy: ExecutionPlanStrategy,
        state: State
    ) -> Iterator[AbilityExecutionPlan]: ...



class ExecutionPlanPipeline(ExecutionPlanPipelineBase):

    @override
    def generate(self, ctx, subability, strategy, state) -> Iterator[AbilityExecutionPlan]:
    
        if (
            not subability.subdefs
            or not subability.action_node
        ):
            yield AbilityExecutionPlan(
                effects=EffectSequence(sequence=()),
                binding=FrozenTargetBinding(),
                mana_solver_result=None
            )
            return
            
        for option in strategy.options_gen.generate(subability.action_node):
            mana_solver_result: ManaSolverResult | None
            if strategy.mana_solver:
                mana_solver_result = strategy.mana_solver.get_mana_plan(option.mana_req, ctx.ability.controller, state)
                if not mana_solver_result:
                    continue

            used_slots = subability.get_used_slots_in_esmap(option.effects)
            effect_bindings = subability._normalize_effect_map(option.effects)


            for binding in strategy.target_gen.generate(ctx, used_slots, state): 
                yield (AbilityExecutionPlan(
                    effects=effect_bindings, 
                    binding=binding,
                    mana_solver_result=mana_solver_result
                )) 