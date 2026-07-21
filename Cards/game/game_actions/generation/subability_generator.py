from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator, Callable, Iterable
from dataclasses import dataclass
from immutabledict import immutabledict
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .ability_action_gen_pipeline import ActionGenerationContextBase
    from ...game_state import State
    from ...mana.mana_value import ManaValueBase

from ..data_structs.ability import SubAbilityComposer, SubAbilityDefinition, RuntimeSubAbility
from ...enums import *



@dataclass(frozen=True)
class SubAbilityGenerationResult:

    selected_variables: immutabledict[SAVariableType, int] | None = None
    cost_subability: RuntimeSubAbility | None = None
    action_subability: RuntimeSubAbility | None = None

class SubAbilityGenerator(ABC):

    @abstractmethod
    def generate(self, ctx: ActionGenerationContextBase, state: State) -> Iterator[SubAbilityGenerationResult]: ...


class FullSubAbilityGenerator(SubAbilityGenerator):

    @override
    def generate(self, ctx, state) -> Iterator[SubAbilityGenerationResult]: 

        def _recursion_step(
            accumulated: dict[SAVariableType, int],
            unused: set[SAVariableType],
        ) -> Iterator[SubAbilityGenerationResult]:
            
            def _mutate_action_subability(operation: Callable[[SubAbilityComposer, Iterable[SubAbilityDefinition]], None]) -> None:
                operation(
                    action_composer, 
                    (
                        (
                            ctx.ability.definition.subdefs[k].action_subdef 
                            for _ in range(v)
                        ) 
                        for k, v in accumulated.items()
                    )
                )


            if len(unused) == 0:
                _mutate_action_subability(SubAbilityComposer.extend_subdefs)

                yield SubAbilityGenerationResult(
                    immutabledict(accumulated), 
                    cost_composer.compile(ctx.ability, state), 
                    action_composer.compile(ctx.ability, state)
                )

                _mutate_action_subability(SubAbilityComposer.remove_subdefs)
                return
            
            cur_var_type: SAVariableType
            max_value: int
            cur_var_type, max_value = min(
                (
                    (
                        x_var, 
                        ctx.ability.definition.subdefs[x_var].get_max_value(state, cost_composer.compile(ctx.ability, state))
                    ) 
                    for x_var in unused
                ),
                key=lambda item: item[1] - ctx.ability.definition.subdefs[item[0]].min_value
            )

            cur_var = ctx.ability.definition.subdefs[cur_var_type]

            unused.remove(cur_var_type)

            accumulated[cur_var_type] = cur_var.min_value
            yield from _recursion_step(accumulated, unused)

            for x in range(cur_var.min_value + 1, max_value + 1):
                accumulated[cur_var_type] = x
                cost_composer.extend_subdefs([cur_var.cost_subdef])
                yield from _recursion_step(accumulated, unused)
            
            cost_composer.remove_subdefs(
                [cur_var.cost_subdef] * (max_value - cur_var.min_value)
            )

            del accumulated[cur_var_type]

            unused.add(cur_var_type)


        cost_composer: SubAbilityComposer = SubAbilityComposer()
        action_composer: SubAbilityComposer = SubAbilityComposer()
        yield from _recursion_step({}, set(ctx.ability.definition.subdefs.keys()))
