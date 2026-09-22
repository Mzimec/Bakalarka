"""Compose priority, ability and mana generation using engine components."""
from __future__ import annotations
from collections.abc import Iterator
from itertools import chain
from typing import TYPE_CHECKING
from .requests import AbilityDecisionRequest, PriorityDecisionRequest, ManaGenerationRequest
from .policies import AbilityGenerationPolicy, PriorityGenerationPolicy, ManaGenerationPolicy
from .parameters import AbilityParameters
from ..generation_strategy import action_generation_strategy
from ..pruning.pruning_strategy import apply_pruning

if TYPE_CHECKING:
    from ...data_structs.game_action import GameAction
    from ....mana.mana_solver import ManaSolverResult


class AbilityDecisionGenerationPipeline:
    def generate(
        self,
        request: AbilityDecisionRequest,
        policy: AbilityGenerationPolicy,
    ) -> Iterator[GameAction]:
        if request.ability.controller is not request.player:
            raise ValueError("The ability must belong to the requesting player.")
        yield from apply_pruning(self._generate(request, policy), policy.pruning)

    def _generate(self, request, policy):
        if request.choices is not None:
            yield from request.choices
            return
        strategy = policy.strategy or action_generation_strategy()
        parameters = (
            policy.parameter_strategy.generate(request.ability, request.state)
            if policy.parameter_strategy is not None
            else (AbilityParameters(policy.x_value, policy.life_payment),)
        )
        for selected in parameters:
            if not isinstance(selected, AbilityParameters):
                raise TypeError("Parameter strategies must yield AbilityParameters.")
            yield from request.ability.generate_actions(
                strategy, request.state, x_value=selected.x_value,
                life_payment=selected.life_payment, skip_unpayable_costs=True,
            )



class PriorityDecisionGenerationPipeline:
    def generate(
        self,
        request: PriorityDecisionRequest,
        policy: PriorityGenerationPolicy,
    ) -> Iterator[GameAction]:
        from ...data_structs.game_action import PassPriorityAction, ConcedeAction
        from ....game_state.collectors.priority_ability_collector import PRIORITY_ABILITY_COLLECTOR
        if request.state.priority.current_player is not request.player:
            raise ValueError("PriorityDecisionRequest player does not currently have priority.")
        yield PassPriorityAction(request.player)
        collector = policy.collector or PRIORITY_ABILITY_COLLECTOR
        groups = (
            (collector.collect_lands(request.state, request.player), policy.land_play_ps),
            (collector.collect_abilities(request.state, request.player, include_mana=policy.include_mana),
             policy.ability_space_ps),
        )
        equivalence = None
        for candidates, pruning in groups:
            # Caller filters must run first: an excluded representative must not
            # hide another copy. Collapse before expensive action generation.
            candidates = iter(apply_pruning(candidates, pruning))
            first = next(candidates, None)
            if first is None:
                continue
            candidates = chain((first,), candidates)
            if policy.equivalence is not None:
                # Empty/fully filtered spaces need no state-wide equivalence
                # snapshot. Reuse one context across nonempty groups.
                if equivalence is None:
                    equivalence = policy.equivalence.bind(request.state)
                candidates = equivalence.representatives(candidates)
            for ability in candidates:
                yield from AbilityDecisionRequest(request.state, request.player, ability).option_space(policy.ability_gp)
        if policy.include_concede:
            yield ConcedeAction(request.player)


class ManaDecisionGenerationPipeline:
    def generate(self, request: ManaGenerationRequest, policy: ManaGenerationPolicy) -> Iterator[ManaSolverResult]:
        from ....mana.mana_solver import SourceActivatingManaSolver
        solver = policy.solver or SourceActivatingManaSolver()
        plan = solver.get_mana_plan(
            request.requirement, request.player, request.state, reserved=request.reserved,
        )
        if plan is not None:
            yield plan
