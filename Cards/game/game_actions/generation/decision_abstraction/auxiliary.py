"""Typed choices for setup, resolution and rule-specific decisions.

The engine supplies eligible objects; pipelines validate proposals by identity.
They never apply effects or change the game state.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from itertools import permutations
from immutabledict import immutabledict

from ...data_structs.decision_option import DecisionOption
from .requests import DecisionRequest, StateDecisionRequest
from .policies import SelectionGenerationPolicy
from .selection_pipelines import SelectionPipeline
from .selection_strategies import combat_view, SelectionStrategy

if TYPE_CHECKING:
    from ....game_state import Card, Player
    from ...data_structs.ability import TriggerAbility
    from ...data_structs.operation import Operation
    from ...resolution.replacement_effects import ReplacementEffect


@dataclass(frozen=True)
class ObjectOption[T](DecisionOption):
    selected: T


@dataclass(frozen=True)
class StartingPlayerOption(ObjectOption["Player"]):
    pass
@dataclass(frozen=True)
class LegendOption(ObjectOption["Card"]):
    pass
@dataclass(frozen=True)
class ReplacementOption(ObjectOption["ReplacementEffect"]):
    pass


@dataclass(frozen=True)
class OrderOption[T](DecisionOption):
    items: tuple[T, ...]

    def __post_init__(self):
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class TriggerOrderOption(OrderOption["TriggerAbility"]):
    pass
@dataclass(frozen=True)
class ReplacementOrderOption(OrderOption["Operation"]):
    pass


@dataclass(frozen=True)
class BooleanOption(DecisionOption):
    accept: bool

    def __post_init__(self):
        if type(self.accept) is not bool:
            raise TypeError("A boolean decision requires bool.")


@dataclass(frozen=True)
class OptionalEffectOption(BooleanOption):
    pass
@dataclass(frozen=True)
class ReplacementAcceptanceOption(BooleanOption):
    pass


@dataclass(frozen=True)
class ScryOption(DecisionOption):
    top: tuple[Card, ...]
    bottom: tuple[Card, ...]

    def __post_init__(self):
        object.__setattr__(self, "top", tuple(self.top))
        object.__setattr__(self, "bottom", tuple(self.bottom))


@dataclass(frozen=True)
class CombatDamageOption(DecisionOption):
    assignments: Mapping[Card | Player, int]

    def __post_init__(self):
        object.__setattr__(self, "assignments", immutabledict(self.assignments))


@dataclass(frozen=True)
class StartingPlayerRequest(DecisionRequest[StartingPlayerOption]):
    # Setup occurs before State and card identities are created.
    state: None
    candidates: tuple[Player, ...]
    option_type = StartingPlayerOption


    @property
    def participants(self) -> tuple[Player, ...]:
        return self.candidates


@dataclass(frozen=True)
class TriggerOrderRequest(StateDecisionRequest[TriggerOrderOption]):
    candidates: tuple[TriggerAbility, ...]
    option_type = TriggerOrderOption


@dataclass(frozen=True)
class LegendRequest(StateDecisionRequest[LegendOption]):
    candidates: tuple[Card, ...]
    option_type = LegendOption


@dataclass(frozen=True)
class ReplacementRequest(StateDecisionRequest[ReplacementOption]):
    operation: Operation
    candidates: tuple[ReplacementEffect, ...]
    option_type = ReplacementOption


@dataclass(frozen=True)
class ReplacementOrderRequest(StateDecisionRequest[ReplacementOrderOption]):
    candidates: tuple[Operation, ...]
    option_type = ReplacementOrderOption


@dataclass(frozen=True)
class OptionalEffectRequest(StateDecisionRequest[OptionalEffectOption]):
    context: object
    description: str
    option_type = OptionalEffectOption


@dataclass(frozen=True)
class ReplacementAcceptanceRequest(StateDecisionRequest[ReplacementAcceptanceOption]):
    operation: Operation
    effect: ReplacementEffect
    option_type = ReplacementAcceptanceOption


@dataclass(frozen=True)
class ScryRequest(StateDecisionRequest[ScryOption]):
    candidates: tuple[Card, ...]
    option_type = ScryOption


@dataclass(frozen=True)
class CombatDamageRequest(StateDecisionRequest[CombatDamageOption]):
    creature: Card
    recipients: tuple[Card | Player, ...]
    amount: int
    option_type = CombatDamageOption


class FullObjectStrategy:
    def generate(self, request):
        for candidate in request.candidates:
            yield request.option_type(candidate)


class FullOrderStrategy:
    def generate(self, request):
        for ordered in permutations(request.candidates):
            yield request.option_type(ordered)


class FullBooleanStrategy:
    def generate(self, request):
        yield request.option_type(True)
        yield request.option_type(False)


class FullScryStrategy:
    def generate(self, request):
        for ordered in permutations(request.candidates):
            for split in range(len(ordered), -1, -1):
                yield ScryOption(ordered[:split], ordered[split:])


class FullCombatDamageStrategy:
    def generate(self, request):
        combat = combat_view(request.state)
        yield CombatDamageOption(combat.default_assignment(request.creature, request.recipients, request.amount))

        def distribute(remaining, recipients):
            if not recipients:
                if remaining == 0:
                    yield {}
                return
            for amount in range(remaining + 1):
                for tail in distribute(remaining - amount, recipients[1:]):
                    yield {recipients[0]: amount, **tail}

        for assignment in distribute(request.amount, request.recipients):
            yield CombatDamageOption(assignment)


@dataclass(frozen=True)
class StartingPlayerPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[StartingPlayerRequest, StartingPlayerOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class LegendPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[LegendRequest, LegendOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class ReplacementPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[ReplacementRequest, ReplacementOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class TriggerOrderPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[TriggerOrderRequest, TriggerOrderOption] = field(default_factory=FullOrderStrategy)

@dataclass(frozen=True)
class ReplacementOrderPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[ReplacementOrderRequest, ReplacementOrderOption] = field(default_factory=FullOrderStrategy)

@dataclass(frozen=True)
class OptionalEffectPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[OptionalEffectRequest, OptionalEffectOption] = field(default_factory=FullBooleanStrategy)

@dataclass(frozen=True)
class ReplacementAcceptancePolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[ReplacementAcceptanceRequest, ReplacementAcceptanceOption] = field(default_factory=FullBooleanStrategy)

@dataclass(frozen=True)
class ScryPolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[ScryRequest, ScryOption] = field(default_factory=FullScryStrategy)

@dataclass(frozen=True)
class CombatDamagePolicy(SelectionGenerationPolicy):
    strategy: SelectionStrategy[CombatDamageRequest, CombatDamageOption] = field(default_factory=FullCombatDamageStrategy)

class ObjectPipeline(SelectionPipeline):
    def __init__(self, option_type):
        self.option_type = option_type

    def validate_request(self, request):
        if not request.candidates:
            raise ValueError("At least one eligible candidate is required.")

    def is_legal(self, request, option):
        return any(option.selected is candidate for candidate in request.candidates)


class OrderPipeline(ObjectPipeline):
    def validate_request(self, request):
        pass

    def is_legal(self, request, option):
        return sorted(map(id, option.items)) == sorted(map(id, request.candidates))


class BooleanPipeline(ObjectPipeline):
    def validate_request(self, request):
        pass

    def is_legal(self, request, option):
        return type(option.accept) is bool


class ScryPipeline(OrderPipeline):
    def __init__(self):
        super().__init__(ScryOption)

    def is_legal(self, request, option):
        return sorted(map(id, option.top + option.bottom)) == sorted(map(id, request.candidates))


class CombatDamagePipeline(SelectionPipeline):
    option_type = CombatDamageOption

    def validate_request(self, request):
        if type(request.amount) is not int or request.amount < 0:
            raise ValueError("Combat damage must be nonnegative.")

    def is_legal(self, request, option):
        from ....rules.combat import CombatError
        try:
            combat_view(request.state)._validate_assignment(
                request.creature, request.recipients, request.amount, option.assignments,
            )
        except CombatError:
            return False
        return True


__all__ = [
    "StartingPlayerRequest",
    "StartingPlayerOption",
    "StartingPlayerPolicy",
    "LegendRequest",
    "LegendOption",
    "LegendPolicy",
    "ReplacementRequest",
    "ReplacementOption",
    "ReplacementPolicy",
    "TriggerOrderRequest",
    "TriggerOrderOption",
    "TriggerOrderPolicy",
    "ReplacementOrderRequest",
    "ReplacementOrderOption",
    "ReplacementOrderPolicy",
    "OptionalEffectRequest",
    "OptionalEffectOption",
    "OptionalEffectPolicy",
    "ReplacementAcceptanceRequest",
    "ReplacementAcceptanceOption",
    "ReplacementAcceptancePolicy",
    "ScryRequest",
    "ScryOption",
    "ScryPolicy",
    "CombatDamageRequest",
    "CombatDamageOption",
    "CombatDamagePolicy",
]
