"""Compile effect bindings and combined mana costs without mutating state."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import replace
from immutabledict import immutabledict

from ..data_structs.game_action import AbilityExecutionPlan
from ..data_structs.ability import EffectSequence
from ..data_structs.action_node import ActionNodeOption, ImmutableEffectToSlotMap
from ...mana.mana_value import ManaValue, ManaRequirement, ImmutableManaRequirement
from ...abilities.parameter_context import ParameterContext
from ...enums import SAVariableType
from .structural_templates import prepared_effects


_EMPTY_EFFECTS = EffectSequence(())
_EMPTY_OPTIONS = (ActionNodeOption(ImmutableEffectToSlotMap(), ImmutableManaRequirement()),)


class ExecutionPlanPipelineBase(ABC):
    """!
    @brief Interface for compiling a sub-ability into concrete execution plans.
    """

    @abstractmethod
    def generate(self, ctx, subability, strategy, state):
        """!
        @brief Yield legal execution plans for the supplied sub-ability.
        """
        ...


class ExecutionPlanPipeline(ExecutionPlanPipelineBase):
    """!
    @brief Compile action-node options, mana payment, and target bindings.

    Combines the selected sub-ability with the supplied generation
    strategy and produces concrete `AbilityExecutionPlan` instances
    without mutating the game state.
    """

    def generate(self, ctx, subability, strategy, state):
        """!
        @brief Yield legal execution plans supported by this strategy.

        Builds the effective mana cost from the ability-level and
        sub-ability-level costs, resolves action-node options, finds a
        legal mana payment plan, generates target bindings, and combines
        the results into immutable execution plans.

        @param ctx Shared action-generation context.
        @param subability Sub-ability whose execution plans are generated,
               or `None` for an empty cost/effect side.
        @param strategy Strategy providing option, target, and mana
               generation behavior.
        @param state Current game state.
        @return Iterator of legal `AbilityExecutionPlan` instances.
        @throws ValueError If mana must be paid but no mana solver is
                available.
        """
        # Missing sub-abilities and sub-abilities without an action node
        # still produce one empty option so the rest of the pipeline can
        # treat them uniformly.
        empty = subability is None or subability.action_node is None
        options = (
            _EMPTY_OPTIONS
            if empty
            else strategy.options_gen.generate(subability.action_node)
        )

        # Combine mana costs inherited from the enclosing ability with any
        # additional mana cost defined by this sub-ability.
        cost = ManaValue()
        if strategy.mana_solver is not None and ctx.mana_cost is not None:
            cost.add(ManaValue(ctx.mana_cost))

        if subability is not None and subability.mana_cost is not None:
            cost.add(ManaValue(subability.mana_cost))

        x_value = getattr(ctx, "x_value", 0)
        param_context = ParameterContext(
            immutabledict({SAVariableType.X: x_value})
        )

        for option in options:
            effects, used_slots = ((_EMPTY_EFFECTS, frozenset()) if empty
                                   else prepared_effects(subability, option.effects))

            mana_solver_result = None

            # Any non-empty mana requirement must be resolved before this
            # option can become a legal execution plan.
            if (cost or option.mana_req) and strategy.mana_solver is None:
                raise ValueError(
                    "A mana solver is required for this ability's mana cost."
                )

            if strategy.mana_solver is not None:
                # ManaValue may have several legal payment forms, e.g.
                # alternative life payments or variable X costs.
                for symbol_requirement, life in cost.payment_options(x_value):
                    selected_life = getattr(ctx, "life_payment", None)

                    if (
                        selected_life is not None and life != selected_life
                    ) or life > ctx.ability.controller.health:
                        continue

                    requirement = ManaRequirement(option.mana_req)
                    requirement.add(symbol_requirement)

                    if not requirement:
                        from ...mana.mana_solver import ManaSolverResult

                        mana_solver_result = ManaSolverResult(
                            (),
                            state,
                            payment=None,
                            life_payment=life,
                        )
                        break

                    from ..card_effects import TapSourceEffect

                    # A source tapped by this cost cannot also produce its mana.
                    reserved = (
                        frozenset({ctx.ability.source})
                        if any(isinstance(binding.effect, TapSourceEffect)
                               for binding in effects.sequence)
                        else frozenset()
                    )

                    from .decision_abstraction.requests import ManaGenerationRequest
                    from .decision_abstraction.policies import ManaGenerationPolicy

                    request = ManaGenerationRequest(
                        state, ctx.ability.controller, requirement,
                        reserved=reserved,
                    )
                    candidate = next(iter(request.option_space(
                        ManaGenerationPolicy(strategy.mana_solver)
                    )), None)

                    if candidate is not None:
                        mana_solver_result = replace(
                            candidate,
                            life_payment=life,
                        )
                        break

                # This option cannot be paid in the current state.
                if mana_solver_result is None:
                    continue

            # Target generation is performed only for slots actually used
            # by the selected action-node option.
            if used_slots is None:
                used_slots = subability.get_used_slots_in_esmap(option.effects)
            for binding in strategy.target_gen.generate(ctx, used_slots, state):
                yield AbilityExecutionPlan(
                    effects=effects,
                    binding=binding,
                    param_context=param_context,
                    mana_solver_result=mana_solver_result,
                )

