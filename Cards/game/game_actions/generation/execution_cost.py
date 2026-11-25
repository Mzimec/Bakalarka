"""Combine mana inputs for a plan without copying already immutable costs."""
from __future__ import annotations
from ...mana.mana_value import ImmutableManaValue, ManaValue


_EMPTY_COST = ImmutableManaValue()


class ExecutionManaCostCompiler:
    """!
    @brief Prepare a cost snapshot; payment feasibility is resolved separately.
    The common empty/single immutable input needs no mutable accumulator.
    Mutable inputs and custom types retain normalization through ManaValue.
    This component keeps no state, targets, payment results or cross-call cache.
    """

    def combine(self, ability_cost: object | None, subability_cost: object | None) -> ImmutableManaValue | ManaValue:
        """!
        @brief Combine ability-level and sub-ability-level mana costs.
        @param ability_cost Ability-level cost, or `None`.
        @param subability_cost Sub-ability-level cost, or `None`.
        @return Immutable empty/single cost when possible, otherwise a mutable combined cost.
        """
        if ability_cost is None:
            if subability_cost is None:
                return _EMPTY_COST
            if type(subability_cost) is ImmutableManaValue:
                return subability_cost
        elif subability_cost is None and type(ability_cost) is ImmutableManaValue:
            return ability_cost

        combined = ManaValue()
        for cost in (ability_cost, subability_cost):
            if cost is not None:
                combined.add(ManaValue(cost))
        return combined


EXECUTION_MANA_COST_COMPILER = ExecutionManaCostCompiler()
