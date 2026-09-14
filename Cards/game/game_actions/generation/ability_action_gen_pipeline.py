"""Compile legal cost and effect plans into ability actions."""

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
    """!
    @brief Read-only interface for the state threaded through action generation.

    Defines the minimal set of properties any concrete generation
    context must expose so that generation strategies (sub-ability,
    cost, and action pipelines) can read the ability being resolved,
    its mana cost (if applicable), and the most recently produced
    sub-ability generation result, without depending on the concrete
    mutable implementation.
    """

    @property
    @abstractmethod
    def ability(self) -> Ability:
        """!
        @brief The `Ability` currently being resolved into game actions.
        """
        ...

    @property
    @abstractmethod
    def mana_cost(self) -> ImmutableManaValue | None:
        """!
        @brief The mana cost to pay for this action, or `None` if not applicable
               (e.g. non-spell abilities without their own mana cost).
        """
        ...

    @property
    @abstractmethod
    def subability_gen_res(self) -> SubAbilityGenerationResult | None:
        """!
        @brief The most recently generated cost/action sub-ability pairing,
               or `None` before generation has produced one.
        """
        ...


@dataclass
class ActionGenerationContext(ActionGenerationContextBase):
    """!
    @brief Mutable, per-generation-attempt state shared across the pipeline stages.

    A single instance is created per `AbilityActionGenerationPipeline.generate`
    call and threaded through the sub-ability, cost, and action
    generation strategies, being updated in place as each stage
    produces its result. This avoids re-deriving shared context (such as
    the X value chosen, or optional life payment) at every pipeline
    stage.

    @var _ability
        Backing field for the `ability` property.
    @var _subability_gen_res
        Backing field for the `subability_gen_res` property.
    @var _mana_cost
        Backing field for the `mana_cost` property.
    @var x_value
        The chosen value for any X/Y-style variable cost or effect on
        this ability. Defaults to 0.
    @var life_payment
        Optional amount of life being paid as part of this action's
        cost (e.g. Phyrexian mana), or `None` if not applicable/not yet
        decided.
    """

    _ability: Ability
    _subability_gen_res: SubAbilityGenerationResult | None = None
    _mana_cost: ImmutableManaValue | None = None
    x_value: int = 0
    life_payment: int | None = None

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
    """!
    @brief Orchestrates turning an `Ability` into every legal, concrete `AbilityAction`.

    Ties together three generation stages driven by an
    `ActionGenerationStrategy`:
    1. Sub-ability generation — decides which cost/effect sub-ability
       pairing(s) apply (e.g. for modal or X-cost abilities).
    2. Cost plan generation — enumerates legal ways to pay the chosen
       cost sub-ability, validating each generated cost effect binding.
    3. Action (effect) plan generation — enumerates legal ways to
       resolve the chosen effect sub-ability (target selection, etc.).

    The full cartesian product of legal sub-ability results × cost
    plans × action plans is yielded as concrete `AbilityAction`
    instances, ready to be offered to a player or AI agent as legal
    choices.
    """

    def generate(
        self,
        ability: Ability,
        strategy: ActionGenerationStrategy,
        state: State,
        *,
        x_value=0,
        life_payment=None,
    ) -> Iterator[AbilityAction]:
        """!
        @brief Yield choices supported by this generation strategy.

        First validates that the ability is legal to use at all
        (`AbilityDefinition.validation_error`) and that `x_value` is a
        valid nonnegative integer. Then builds a shared
        `ActionGenerationContext` (resolving the spell's casting mana
        cost up front, if applicable) and drives the three-stage
        pipeline described in the class docstring, validating each
        generated cost effect binding via `Effect.validation_error`
        before it is offered as part of a plan.

        @param ability The `Ability` to generate concrete actions for.
        @param strategy Bundle of sub-strategies (`subability_gen`,
               `cost_pipeline`/`cost_strategy`,
               `action_pipeline`/`action_strategy`) driving each
               generation stage.
        @param state Current game state to generate against.
        @param x_value Chosen value for any X/Y-style variable on this
               ability. Must be a nonnegative integer; defaults to 0.
        @param life_payment Optional life payment to associate with the
               generated context (e.g. for Phyrexian mana costs).
        @return Iterator of `AbilityAction` instances, one per legal
                combination of sub-ability result, cost plan, and action
                plan.
        @throws ValueError If the ability itself is not currently legal
                to use (per `AbilityDefinition.validation_error`), if
                `x_value` is not a nonnegative integer, or if a generated
                cost effect binding fails its own
                `Effect.validation_error` check.
        """
        error = ability.definition.validation_error(ability.source, ability.controller, state)
        if error:
            raise ValueError(error)
        if not isinstance(x_value, int) or isinstance(x_value, bool) or x_value < 0:
            raise ValueError("X must be a nonnegative integer.")
        ctx = ActionGenerationContext(_ability=ability, x_value=x_value, life_payment=life_payment)
        if ability.definition.is_spell:
            # Spells' mana cost comes from the card itself (accounting for
            # any continuous cost-modifying effects), rather than from a
            # sub-ability's cost definition.
            ctx.mana_cost = ability.source.get_casting_cost(state)

        for subability_result in strategy.subability_gen.generate(ctx, state):
            ctx.subability_gen_res = subability_result

            for cost_execution_plan in strategy.cost_pipeline.generate(
                ctx, subability_result.cost_subability, strategy.cost_strategy, state
            ):
                from ..data_structs.game_action import ResolutionContext
                from dataclasses import replace

                cost_context = ResolutionContext(
                    controller=ability.controller,
                    source=ability.source,
                    ability=ability.definition,
                    action_key=ability.key,
                )
                # Validate every effect used to pay this cost plan (e.g.
                # "sacrifice a creature" must currently be payable) before
                # offering it as a legal option.
                for binding in cost_execution_plan.effects.sequence:
                    error = binding.effect.validation_error(
                        state,
                        replace(
                            cost_context, targets=cost_execution_plan._scope_binding(binding.slots)
                        ),
                    )
                    if error:
                        raise ValueError(error)

                for action_execution_plan in strategy.action_pipeline.generate(
                    ctx, subability_result.action_subability, strategy.action_strategy, state
                ):

                    yield ctx.ability.to_game_action(cost_execution_plan, action_execution_plan)