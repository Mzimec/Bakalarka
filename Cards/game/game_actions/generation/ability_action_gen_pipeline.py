from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator
from dataclasses import dataclass
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .generation_strategy import ActionGenerationStrategy
    from .subability_generator import SubAbilityGenerationResult
    from ..data_structs.ability import Ability
    from ..data_structs.game_action import AbilityAction
    from ...game_state import State
    from ...mana.mana_value import ImmutableManaValue

from ...enums import *


class ActionGenerationContextBase(ABC):

    @property
    @abstractmethod
    def ability(self) -> Ability: ...
    
    @property
    @abstractmethod
    def mana_cost(self) -> ImmutableManaValue | None: ...
    
    @property
    @abstractmethod
    def subability_gen_res(self) -> SubAbilityGenerationResult | None: ...


@dataclass
class ActionGenerationContext(ActionGenerationContextBase):

    _ability: Ability
    _subability_gen_res: SubAbilityGenerationResult | None = None

    @property
    @override
    def ability(self) -> Ability: 
        return self._ability
    @ability.setter
    def ability(self, value: Ability) -> None:
        self._ability = value
    
    @property
    @override
    def mana_cost(self) -> ImmutableManaValue | None: 
        return self._mana_cost
    @mana_cost.setter
    def mana_cost(self, value: ImmutableManaValue | None) -> None:
        self._mana_cost = value
    
    @property
    @override
    def subability_gen_res(self) -> SubAbilityGenerationResult | None:
        return self._subability_gen_res
    @subability_gen_res.setter
    def subability_gen_res(self, value: SubAbilityGenerationResult | None) -> None:
        self._subability_gen_res = value


class AbilityActionGenerationPipeline:

    def generate(
            self,
            ability: Ability,
            strategy: ActionGenerationStrategy,
            state: State 
        ) -> Iterator[AbilityAction]:

        ctx = ActionGenerationContext(ability=ability)

        for subability_result in strategy.subability_gen.generate(ctx, state):
            ctx.subability_gen_res = subability_result
            
            for cost_execution_plan in strategy.cost_pipeline.generate(
                ctx, 
                subability_result.cost_subability, 
                strategy.cost_strategy, 
                state
            ):
                
                for action_execution_plan in strategy.action_pipeline.generate(
                    ctx, 
                    subability_result.action_subability,
                    strategy.action_strategy, 
                    state
                ):
                    
                    yield ctx.ability.to_game_action(cost_execution_plan, action_execution_plan)
