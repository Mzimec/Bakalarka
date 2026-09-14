"""Compile the selected subabilities of an ability definition."""

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

from ..data_structs.ability import (
    SubAbilityComposer,
    SubAbilityDefinition,
    RuntimeSubAbility,
)
from ...enums import *


@dataclass(frozen=True)
class SubAbilityGenerationResult:
    """!
    @brief Result of compiling one concrete set of sub-ability choices.

    @var selected_variables
        Selected values for variable sub-abilities such as X or Y.
    @var cost_subability
        Runtime sub-ability representing the combined cost side.
    @var action_subability
        Runtime sub-ability representing the combined effect side.
    """

    selected_variables: immutabledict[SAVariableType, int] | None = None
    cost_subability: RuntimeSubAbility | None = None
    action_subability: RuntimeSubAbility | None = None


class SubAbilityGenerator(ABC):
    """!
    @brief Interface for compiling an ability definition into runtime sub-abilities.
    """

    @abstractmethod
    def generate(
        self, ctx: ActionGenerationContextBase, state: State
    ) -> Iterator[SubAbilityGenerationResult]:
        """!
        @brief Yield legal compiled sub-ability combinations.
        """
        ...


class FixedSubAbilityGenerator(SubAbilityGenerator):
    """!
    @brief Compile the fixed cost/effect parts of a definition with no variable choice.
    """

    def generate(self, ctx, state):
        """!
        @brief Yield the fixed sub-ability combination.

        Rejects definitions containing variable sub-abilities, since this
        generator does not choose values for them.

        @param ctx Shared action-generation context.
        @param state Current game state.
        @return Iterator containing the compiled fixed sub-ability result.
        @throws ValueError If the definition contains variable sub-abilities.
        """
        definition = ctx.ability.definition

        if definition.subdefs:
            raise ValueError(
                "This command requires explicit variable choices; fixed generation cannot select them."
            )

        # Full generation also handles definitions with no variable
        # sub-abilities, so reuse it for the actual compilation.
        yield from FullSubAbilityGenerator().generate(ctx, state)


class FullSubAbilityGenerator(SubAbilityGenerator):
    """!
    @brief Enumerate legal values of variable sub-abilities and compile each result.
    """

    @override
    def generate(self, ctx, state) -> Iterator[SubAbilityGenerationResult]:
        """!
        @brief Yield every legal compiled cost/effect sub-ability combination.

        Variable sub-abilities are selected recursively. Their maximum
        values are evaluated against the cost sub-ability accumulated so
        far, allowing later variables to depend on already chosen costs.

        @param ctx Shared action-generation context.
        @param state Current game state.
        @return Iterator of compiled `SubAbilityGenerationResult` instances.
        """

        def _recursion_step(
            accumulated: dict[SAVariableType, int],
            unused: set[SAVariableType],
        ) -> Iterator[SubAbilityGenerationResult]:
            """!
            @brief Recursively assign remaining variable sub-abilities.
            """

            def _mutate_action_subability(
                operation: Callable[
                    [SubAbilityComposer, Iterable[SubAbilityDefinition]],
                    None,
                ],
            ) -> None:
                # Apply each selected variable's action sub-definition as
                # many times as required by its chosen value.
                operation(
                    action_composer,
                    (
                        (
                            ctx.ability.definition.subdefs[k].action_subdef
                            for _ in range(v)
                        )
                        for k, v in accumulated.items()
                    ),
                )

            # Once all variables are assigned, temporarily materialize their
            # action-side contributions and compile both composers.
            if len(unused) == 0:
                _mutate_action_subability(SubAbilityComposer.extend_subdefs)

                yield SubAbilityGenerationResult(
                    immutabledict(accumulated),
                    cost_composer.compile(ctx.ability, state),
                    action_composer.compile(ctx.ability, state),
                )

                # Restore the shared composer before exploring another branch.
                _mutate_action_subability(SubAbilityComposer.remove_subdefs)
                return

            # Choose the variable with the smallest currently available
            # value range first to reduce the branching factor.
            cur_var_type: SAVariableType
            max_value: int
            cur_var_type, max_value = min(
                (
                    (
                        x_var,
                        ctx.ability.definition.subdefs[x_var].get_max_value(
                            state,
                            cost_composer.compile(ctx.ability, state),
                        ),
                    )
                    for x_var in unused
                ),
                key=lambda item: (
                    item[1]
                    - ctx.ability.definition.subdefs[item[0]].min_value
                ),
            )

            cur_var = ctx.ability.definition.subdefs[cur_var_type]
            unused.remove(cur_var_type)

            # The minimum value contributes no additional cost sub-definition;
            # costs are added incrementally as the selected value increases.
            accumulated[cur_var_type] = cur_var.min_value
            yield from _recursion_step(accumulated, unused)

            for x in range(cur_var.min_value + 1, max_value + 1):
                accumulated[cur_var_type] = x
                cost_composer.extend_subdefs([cur_var.cost_subdef])
                yield from _recursion_step(accumulated, unused)

            # Undo all cost contributions added while iterating this variable.
            cost_composer.remove_subdefs(
                [cur_var.cost_subdef] * (max_value - cur_var.min_value)
            )

            del accumulated[cur_var_type]
            unused.add(cur_var_type)

        # Start with the definition's fixed cost and action components.
        cost_composer = SubAbilityComposer(ctx.ability.definition.cost_subdefs)
        action_composer = SubAbilityComposer(ctx.ability.definition.action_subdefs)

        yield from _recursion_step(
            {},
            set(ctx.ability.definition.subdefs.keys()),
        )
