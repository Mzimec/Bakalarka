"""Lazy request routing. Policies restrict generation, never execute choices.

Spaces are re-iterable views of a live state, not snapshots. Consume them before
applying an action. Values retain the engine's native types (actions, mappings,
booleans, card tuples and mana plans), without an extra wrapper to unwrap.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from itertools import combinations, permutations, product
from collections.abc import Iterator
from typing import TYPE_CHECKING
from immutabledict import immutabledict
from .requests import *

if TYPE_CHECKING:
    from ..generation_strategy import ActionGenerationStrategy
    from ..pruning.pruning_strategy import PruningStrategy
    from ....ai.mana_solver import ManaSolver

class GenerationPolicy:
    pass

@dataclass(frozen=True)
class AbilityGenerationPolicy(GenerationPolicy):
    strategy: ActionGenerationStrategy | None = None
    x_value: int = 0
    life_payment: int | None = None
    pruning: PruningStrategy | None = None

@dataclass(frozen=True)
class PriorityGenerationPolicy(GenerationPolicy):
    ability_gp: AbilityGenerationPolicy = field(default_factory=AbilityGenerationPolicy)
    ability_space_ps: PruningStrategy | None = None
    land_play_ps: PruningStrategy | None = None
    include_mana: bool = True
    include_concede: bool = True

@dataclass(frozen=True)
class SelectionGenerationPolicy(GenerationPolicy):
    pruning: PruningStrategy | None = None

@dataclass(frozen=True)
class ManaGenerationPolicy(GenerationPolicy):
    solver: ManaSolver | None = None


def _prune(candidates, strategy):
    return candidates if strategy is None else strategy.prune(candidates)


def default_ability_strategy():
    from ..generation_strategy import ActionGenerationStrategy, ExecutionPlanStrategy
    from ..subability_generator import FullSubAbilityGenerator
    from ..exec_plan_gen_pipeline import ExecutionPlanPipeline
    from ..action_node_option_generator import FullActionNodeOptionGenerator
    from ..target_binding_generator import FullTargetBindingGenerator
    from ....ai.mana_solver import SourceActivatingManaSolver
    return ActionGenerationStrategy(
        FullSubAbilityGenerator(), ExecutionPlanPipeline(),
        ExecutionPlanStrategy(FullActionNodeOptionGenerator(), FullTargetBindingGenerator(),
                              SourceActivatingManaSolver()),
        ExecutionPlanPipeline(),
        ExecutionPlanStrategy(FullActionNodeOptionGenerator(), FullTargetBindingGenerator(), None),
    )


class AbilityDecisionGenerationPipeline:
    def generate(self, request, policy):
        from ..ability_action_gen_pipeline import ABILITY_GENERATION_PIPELINE
        if request.ability.controller is not request.player:
            raise ValueError("The ability must belong to the requesting player.")
        yield from _prune(ABILITY_GENERATION_PIPELINE.generate(
            request.ability, policy.strategy or default_ability_strategy(), request.state,
            x_value=policy.x_value, life_payment=policy.life_payment, skip_unpayable_costs=True,
        ), policy.pruning)


class PriorityDecisionGenerationPipeline:
    def generate(self, request, policy):
        from ...data_structs.game_action import PassPriorityAction, ConcedeAction
        from ....game_state.collectors.ability_collector import ABILITY_COLLECTOR
        from ....rules.lands import LandPlayAction, land_play_error
        if request.state.priority.current_player is not request.player:
            raise ValueError("PriorityDecisionRequest player does not currently have priority.")
        yield PassPriorityAction(request.player)
        lands = (LandPlayAction(card, request.player) for card in request.player.hand.values()
                 if land_play_error(card, request.player, request.state) is None)
        yield from _prune(lands, policy.land_play_ps)
        collect = ABILITY_COLLECTOR.collect if policy.include_mana else ABILITY_COLLECTOR.collect_non_mana
        for ability in _prune(collect(request.state, request.player), policy.ability_space_ps):
            yield from AbilityDecisionRequest(request.state, request.player, ability).option_space(policy.ability_gp)
        if policy.include_concede:
            yield ConcedeAction(request.player)


class SelectionDecisionGenerationPipeline:
    def generate(self, request, policy):
        yield from _prune(self._generate(request), policy.pruning)

    def _generate(self, request):
        from ....enums import CardType, ZoneType, TurnPhase
        from ....rules.combat import CombatError
        state, player = request.state, request.player
        if isinstance(request, MulliganRequest):
            if type(request.mulligans_taken) is not int or request.mulligans_taken < 0:
                raise ValueError("Mulligan count must be a nonnegative integer.")
            yield False
            if request.can_mulligan:
                yield True
        elif isinstance(request, AbilityResolutionRequest):
            if (type(request.count) is not int or not 0 <= request.count <= len(request.candidates)
                    or len(set(request.candidates)) != len(request.candidates)):
                raise ValueError("Choose a valid number of distinct eligible objects.")
            yield from combinations(request.candidates, request.count)
        elif isinstance(request, (MulliganBottomRequest, DiscardRequest)):
            if type(request.count) is not int or not 0 <= request.count <= len(player.hand):
                raise ValueError("Choose a valid number of hand cards.")
            # Bottom order changes future draws; discard order does not.
            enumerate_cards = permutations if isinstance(request, MulliganBottomRequest) else combinations
            yield from enumerate_cards(tuple(player.hand.values()), request.count)
        elif isinstance(request, DeclareAttackersRequest):
            state.combat.validate_attackers(player, {})
            attackers = state.combat.legal_attackers(player)
            defenders = [p for p in state.active_players if p is not player]
            defenders += [c for c in state.get_cards(from_zones=[ZoneType.BATTLEFIELD])
                          if c.is_type(state, CardType.PLANESWALKER)
                          and c.get_controller(state) in defenders]
            for assignment in product((None, *defenders), repeat=len(attackers)):
                declaration = {a: d for a, d in zip(attackers, assignment) if d is not None}
                yield immutabledict(state.combat.validate_attackers(player, declaration))
        elif isinstance(request, DeclareBlockersRequest):
            if (not state.combat.active or state.turn.phase != TurnPhase.DECLARE_BLOCKERS
                    or not state.combat._attackers_declared or player is state.active_player
                    or player not in state.active_players or player in state.combat._blockers_declared):
                raise ValueError("This player cannot declare blockers in the current combat step.")
            # Validation is observational during generation, including stale combat entries.
            from copy import copy
            combat = copy(state.combat)
            for name in ("attackers", "blockers", "blocked", "_identities", "_removed_defenders"):
                setattr(combat, name, getattr(combat, name).copy())
            combat.prune()
            blockers = tuple(dict.fromkeys(b for a in combat.attackers for b in combat.legal_blockers(player, a)))
            choices = [(None, *(a for a in combat.attackers if combat.blocker_error(b, a, player) is None))
                       for b in blockers]
            for assignment in product(*choices):
                declaration = {b: a for b, a in zip(blockers, assignment) if a is not None}
                try:
                    validated = combat.validate_blockers(player, declaration)
                except CombatError:
                    continue
                yield immutabledict(validated)


class ManaDecisionGenerationPipeline:
    def generate(self, request, policy):
        from ....ai.mana_solver import SourceActivatingManaSolver
        solver = policy.solver or SourceActivatingManaSolver()
        kwargs = {"reserved": request.reserved} if isinstance(solver, SourceActivatingManaSolver) else {}
        if request.reserved and not kwargs:
            raise ValueError("This mana solver does not support reserved sources.")
        plan = solver.get_mana_plan(request.requirement, request.player, request.state, **kwargs)
        if plan is not None:
            yield plan


_SELECTION = SelectionDecisionGenerationPipeline()
DECISION_OPTION_GENERATORS = immutabledict({
    PriorityDecisionRequest: (PriorityDecisionGenerationPipeline(), PriorityGenerationPolicy),
    AbilityDecisionRequest: (AbilityDecisionGenerationPipeline(), AbilityGenerationPolicy),
    DeclareAttackersRequest: (_SELECTION, SelectionGenerationPolicy),
    DeclareBlockersRequest: (_SELECTION, SelectionGenerationPolicy),
    MulliganRequest: (_SELECTION, SelectionGenerationPolicy),
    MulliganBottomRequest: (_SELECTION, SelectionGenerationPolicy),
    DiscardRequest: (_SELECTION, SelectionGenerationPolicy),
    AbilityResolutionRequest: (_SELECTION, SelectionGenerationPolicy),
    ManaGenerationRequest: (ManaDecisionGenerationPipeline(), ManaGenerationPolicy),
})


def route_decision_generation(request, policy=None):
    registration = DECISION_OPTION_GENERATORS.get(type(request))
    if registration is None:
        raise ValueError(f"No decision generation pipeline registered for {type(request).__name__}")
    pipeline, policy_type = registration
    if policy is None:
        policy = policy_type()
    if not isinstance(policy, policy_type):
        raise TypeError(f"{type(request).__name__} requires {policy_type.__name__}")
    if request.player not in request.state.players:
        raise ValueError("The requesting player must belong to the game.")
    yield from pipeline.generate(request, policy)


@dataclass(frozen=True)
class DecisionOptionSpace[T]:
    request: DecisionRequest[T]
    policy: GenerationPolicy | None = None

    def __iter__(self) -> Iterator[T]:
        yield from route_decision_generation(self.request, self.policy)

    def generate(self) -> Iterator[T]:
        return iter(self)
