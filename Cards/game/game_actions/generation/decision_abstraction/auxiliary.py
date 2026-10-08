"""Typed choices for setup, resolution and rule-specific decisions.

The engine supplies eligible objects; pipelines validate proposals by identity.
They never apply effects or change the game state.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
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
    """!
    @brief Decision option selecting one object.
    """
    selected: T


@dataclass(frozen=True)
class StartingPlayerOption(ObjectOption["Player"]):
    """!
    @brief Decision option selecting the starting player.
    """
    pass
@dataclass(frozen=True)
class LegendOption(ObjectOption["Card"]):
    """!
    @brief Decision option selecting which legend to keep.
    """
    pass
@dataclass(frozen=True)
class ReplacementOption(ObjectOption["ReplacementEffect"]):
    """!
    @brief Decision option selecting a replacement effect.
    """
    pass


@dataclass(frozen=True)
class OrderOption[T](DecisionOption):
    """!
    @brief Decision option selecting an explicit ordering.
    """
    items: tuple[T, ...]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class TriggerOrderOption(OrderOption["TriggerAbility"]):
    """!
    @brief Decision option ordering triggered abilities.
    """
    pass
@dataclass(frozen=True)
class ReplacementOrderOption(OrderOption["Operation"]):
    """!
    @brief Decision option ordering replacement operations.
    """
    pass


@dataclass(frozen=True)
class BooleanOption(DecisionOption):
    """!
    @brief Boolean decision option.
    """
    accept: bool

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        if type(self.accept) is not bool:
            raise TypeError("A boolean decision requires bool.")


@dataclass(frozen=True)
class OptionalEffectOption(BooleanOption):
    """!
    @brief Decision option for accepting an optional effect.
    """
    pass
@dataclass(frozen=True)
class ReplacementAcceptanceOption(BooleanOption):
    """!
    @brief Decision option for accepting a replacement effect.
    """
    pass


@dataclass(frozen=True)
class ScryOption(DecisionOption):
    """!
    @brief Decision option splitting cards between top and bottom.
    """
    top: tuple[Card, ...]
    bottom: tuple[Card, ...]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "top", tuple(self.top))
        object.__setattr__(self, "bottom", tuple(self.bottom))


@dataclass(frozen=True)
class CombatDamageOption(DecisionOption):
    """!
    @brief Decision option assigning combat damage.
    """
    assignments: Mapping[Card | Player, int]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "assignments", immutabledict(self.assignments))


@dataclass(frozen=True)
class StartingPlayerRequest(DecisionRequest[StartingPlayerOption]):
    # Setup occurs before State and card identities are created.
    """!
    @brief Setup request for choosing the starting player.
    """
    state: None
    candidates: tuple[Player, ...]
    option_type = StartingPlayerOption


    @property
    def participants(self) -> tuple[Player, ...]:
        """!
        @brief Return participants eligible for the setup request.
        @return Candidate players.
        """
        return self.candidates


@dataclass(frozen=True)
class TriggerOrderRequest(StateDecisionRequest[TriggerOrderOption]):
    """!
    @brief Request to choose triggered ability order.
    """
    candidates: tuple[TriggerAbility, ...]
    option_type = TriggerOrderOption


@dataclass(frozen=True)
class LegendRequest(StateDecisionRequest[LegendOption]):
    """!
    @brief Request to choose which legendary permanent to keep.
    """
    candidates: tuple[Card, ...]
    option_type = LegendOption


@dataclass(frozen=True)
class ReplacementRequest(StateDecisionRequest[ReplacementOption]):
    """!
    @brief Request to select one applicable replacement effect.
    """
    operation: Operation
    candidates: tuple[ReplacementEffect, ...]
    option_type = ReplacementOption


@dataclass(frozen=True)
class ReplacementOrderRequest(StateDecisionRequest[ReplacementOrderOption]):
    """!
    @brief Request to order replacement operations.
    """
    candidates: tuple[Operation, ...]
    option_type = ReplacementOrderOption


@dataclass(frozen=True)
class OptionalEffectRequest(StateDecisionRequest[OptionalEffectOption]):
    """!
    @brief Request to accept or decline an optional effect.
    """
    context: object
    description: str
    option_type = OptionalEffectOption


@dataclass(frozen=True)
class ReplacementAcceptanceRequest(StateDecisionRequest[ReplacementAcceptanceOption]):
    """!
    @brief Request to accept or decline a replacement effect.
    """
    operation: Operation
    effect: ReplacementEffect
    option_type = ReplacementAcceptanceOption


@dataclass(frozen=True)
class ScryRequest(StateDecisionRequest[ScryOption]):
    """!
    @brief Request to choose a scry ordering and split.
    """
    candidates: tuple[Card, ...]
    option_type = ScryOption


@dataclass(frozen=True)
class CombatDamageRequest(StateDecisionRequest[CombatDamageOption]):
    """!
    @brief Request to assign combat damage.
    """
    creature: Card
    recipients: tuple[Card | Player, ...]
    amount: int
    option_type = CombatDamageOption


class FullObjectStrategy:
    """!
    @brief Strategy enumerating one option per object candidate.
    """
    def generate(self, request: object) -> Iterator[DecisionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterator over generated options.
        """
        for candidate in request.candidates:
            yield request.option_type(candidate)


class FullOrderStrategy:
    """!
    @brief Strategy enumerating all candidate orderings.
    """
    def generate(self, request: object) -> Iterator[DecisionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterator over generated options.
        """
        for ordered in permutations(request.candidates):
            yield request.option_type(ordered)


class FullBooleanStrategy:
    """!
    @brief Strategy enumerating both boolean choices.
    """
    def generate(self, request: object) -> Iterator[DecisionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterator over generated options.
        """
        yield request.option_type(True)
        yield request.option_type(False)


class FullScryStrategy:
    """!
    @brief Strategy enumerating all scry orderings and splits.
    """
    def generate(self, request: object) -> Iterator[DecisionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterator over generated options.
        """
        for ordered in permutations(request.candidates):
            for split in range(len(ordered), -1, -1):
                yield ScryOption(ordered[:split], ordered[split:])


class FullCombatDamageStrategy:
    """!
    @brief Strategy enumerating combat damage assignments.
    """
    def generate(self, request: object) -> Iterator[DecisionOption]:
        """!
        @brief Yield candidate options for the request.
        @param request Decision request.
        @return Iterator over generated options.
        """
        combat = combat_view(request.state)
        yield CombatDamageOption(combat.default_assignment(request.creature, request.recipients, request.amount))

        def distribute(remaining: int, recipients: tuple[Card | Player, ...]) -> Iterator[dict[Card | Player, int]]:
            """!
            @brief Recursively distribute remaining damage among recipients.
            @param remaining Damage still to assign.
            @param recipients Remaining recipients.
            @return Iterator of assignment dictionaries.
            """
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
    """!
    @brief Policy for starting-player selection.
    """
    strategy: SelectionStrategy[StartingPlayerRequest, StartingPlayerOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class LegendPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for legend-rule selection.
    """
    strategy: SelectionStrategy[LegendRequest, LegendOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class ReplacementPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for replacement-effect selection.
    """
    strategy: SelectionStrategy[ReplacementRequest, ReplacementOption] = field(default_factory=FullObjectStrategy)

@dataclass(frozen=True)
class TriggerOrderPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for trigger ordering.
    """
    strategy: SelectionStrategy[TriggerOrderRequest, TriggerOrderOption] = field(default_factory=FullOrderStrategy)

@dataclass(frozen=True)
class ReplacementOrderPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for replacement operation ordering.
    """
    strategy: SelectionStrategy[ReplacementOrderRequest, ReplacementOrderOption] = field(default_factory=FullOrderStrategy)

@dataclass(frozen=True)
class OptionalEffectPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for optional-effect decisions.
    """
    strategy: SelectionStrategy[OptionalEffectRequest, OptionalEffectOption] = field(default_factory=FullBooleanStrategy)

@dataclass(frozen=True)
class ReplacementAcceptancePolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for replacement acceptance decisions.
    """
    strategy: SelectionStrategy[ReplacementAcceptanceRequest, ReplacementAcceptanceOption] = field(default_factory=FullBooleanStrategy)

@dataclass(frozen=True)
class ScryPolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for scry decisions.
    """
    strategy: SelectionStrategy[ScryRequest, ScryOption] = field(default_factory=FullScryStrategy)

@dataclass(frozen=True)
class CombatDamagePolicy(SelectionGenerationPolicy):
    """!
    @brief Policy for combat damage assignment decisions.
    """
    strategy: SelectionStrategy[CombatDamageRequest, CombatDamageOption] = field(default_factory=FullCombatDamageStrategy)

class ObjectPipeline(SelectionPipeline):
    """!
    @brief Pipeline validating object-selection options.
    """
    def __init__(self, option_type: type[DecisionOption]) -> None:
        """!
        @brief Initialize the strategy or pipeline object.
        """
        self.option_type: type[DecisionOption] = option_type

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Decision request.
        """
        if not request.candidates:
            raise ValueError("At least one eligible candidate is required.")

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Decision request.
        @param option Option to validate.
        @return True when legal.
        """
        return any(option.selected is candidate for candidate in request.candidates)


class OrderPipeline(ObjectPipeline):
    """!
    @brief Pipeline validating order-selection options.
    """
    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Decision request.
        """
        pass

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Decision request.
        @param option Option to validate.
        @return True when legal.
        """
        return sorted(map(id, option.items)) == sorted(map(id, request.candidates))


class BooleanPipeline(ObjectPipeline):
    """!
    @brief Pipeline validating boolean options.
    """
    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Decision request.
        """
        pass

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Decision request.
        @param option Option to validate.
        @return True when legal.
        """
        return type(option.accept) is bool


class ScryPipeline(OrderPipeline):
    """!
    @brief Pipeline validating scry options.
    """
    def __init__(self) -> None:
        """!
        @brief Initialize the strategy or pipeline object.
        """
        super().__init__(ScryOption)

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Decision request.
        @param option Option to validate.
        @return True when legal.
        """
        return sorted(map(id, option.top + option.bottom)) == sorted(map(id, request.candidates))


class CombatDamagePipeline(SelectionPipeline):
    """!
    @brief Pipeline validating combat damage assignments.
    """
    option_type = CombatDamageOption

    def validate_request(self, request: object) -> None:
        """!
        @brief Validate request-level invariants.
        @param request Decision request.
        """
        if type(request.amount) is not int or request.amount < 0:
            raise ValueError("Combat damage must be nonnegative.")

    def is_legal(self, request: object, option: DecisionOption) -> bool:
        """!
        @brief Return whether an option is legal for the request.
        @param request Decision request.
        @param option Option to validate.
        @return True when legal.
        """
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
