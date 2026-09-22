"""Typed decisions with one explicit handler per request kind."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING
from ..game_actions.data_structs.decision_option import DecisionOption
from ..game_actions.generation.decision_abstraction.requests import (
    DecisionRequest, PriorityDecisionRequest, AbilityDecisionRequest, DeclareAttackersRequest,
    DeclareBlockersRequest, MulliganRequest, MulliganBottomRequest, DiscardRequest,
    AbilityResolutionRequest, ManaGenerationRequest,
    PriorityActionRequest, AbilityRequest, AttackerDeclarationRequest, BlockerDeclarationRequest,
)
from ..game_actions.generation.decision_abstraction.options import (
    DeclareAttackersOption, DeclareBlockersOption, MulliganOption, MulliganBottomOption,
    DiscardOption, AbilityResolutionOption,
)
from ..game_actions.generation.decision_abstraction.option_space import DecisionOptionSpace
from ..game_actions.generation.decision_abstraction.measurement import DecisionMeasurement

from ..game_actions.generation.decision_abstraction.auxiliary import (
    StartingPlayerRequest, StartingPlayerOption,
    TriggerOrderRequest, TriggerOrderOption,
    LegendRequest, LegendOption,
    ReplacementRequest, ReplacementOption,
    ReplacementOrderRequest, ReplacementOrderOption,
    OptionalEffectRequest, OptionalEffectOption,
    ReplacementAcceptanceRequest, ReplacementAcceptanceOption,
    ScryRequest, ScryOption,
    CombatDamageRequest, CombatDamageOption
)

if TYPE_CHECKING:
    from ..game_actions.data_structs.game_action import GameAction
    from ..mana.mana_solver import ManaSolverResult


class DecisionResult[T: DecisionOption]:
    def __init__(self, value: T, info: Mapping[str, Any] | None = None):
        if not isinstance(value, DecisionOption):
            raise TypeError("DecisionResult requires a DecisionOption.")
        self.value: T = value
        self.info = dict(info or {})


class DecisionMaker(ABC):
    decision_mode = "agent"

    def decide[T: DecisionOption](self, request: DecisionRequest[T]) -> DecisionResult[T]:
        measurement = DecisionMeasurement(request)
        try:
            with measurement:
                result = self._decide(request)
                if not isinstance(result, DecisionResult) or not isinstance(result.value, request.option_type):
                    raise TypeError(f"{type(request).__name__} requires DecisionResult[{request.option_type.__name__}].")
            measurement.metrics["auto_pass"] = result.info.get("auto_pass", False)
            measurement.metrics["auto_pass_reason"] = result.info.get("auto_pass_reason")
            result.info["elapsed_time"] = measurement.metrics["elapsed_ns"]
            result.info["decision_metrics"] = measurement.metrics
            return result
        finally:
            measurement.publish(self)

    @abstractmethod
    def _decide[T: DecisionOption](self, request: DecisionRequest[T]) -> DecisionResult[T]: ...


def _first[T: DecisionOption](request: DecisionRequest[T]) -> DecisionResult[T]:
    option = next(iter(request.options), None)
    if option is None:
        raise ValueError(f"No options available for {type(request).__name__}.")
    return DecisionResult(option)


class ModularDecisionMaker(DecisionMaker):
    """Override typed decide_* hooks independently; dispatch has no legacy logic."""

    def _decide(self, request):
        if isinstance(request, PriorityDecisionRequest):
            return self.decide_priority(request)
        if isinstance(request, AbilityDecisionRequest):
            return self.decide_ability(request)
        if isinstance(request, DeclareAttackersRequest):
            return self.decide_attackers(request)
        if isinstance(request, DeclareBlockersRequest):
            return self.decide_blockers(request)
        if isinstance(request, MulliganRequest):
            return self.decide_mulligan(request)
        if isinstance(request, MulliganBottomRequest):
            return self.decide_mulligan_bottom(request)
        if isinstance(request, DiscardRequest):
            return self.decide_discard(request)
        if isinstance(request, AbilityResolutionRequest):
            return self.decide_ability_resolution(request)
        if isinstance(request, ManaGenerationRequest):
            return self.decide_mana(request)
        if isinstance(request, StartingPlayerRequest):
            return self.decide_starting_player(request)
        if isinstance(request, TriggerOrderRequest):
            return self.decide_trigger_order(request)
        if isinstance(request, LegendRequest):
            return self.decide_legend(request)
        if isinstance(request, ReplacementRequest):
            return self.decide_replacement(request)
        if isinstance(request, ReplacementOrderRequest):
            return self.decide_replacement_order(request)
        if isinstance(request, OptionalEffectRequest):
            return self.decide_optional_effect(request)
        if isinstance(request, ReplacementAcceptanceRequest):
            return self.decide_replacement_acceptance(request)
        if isinstance(request, ScryRequest):
            return self.decide_scry(request)
        if isinstance(request, CombatDamageRequest):
            return self.decide_combat_damage(request)
        raise NotImplementedError(f"Unsupported request: {type(request).__name__}")

    def decide_priority(self, request: PriorityDecisionRequest) -> DecisionResult[GameAction]:
        return _first(request)

    def decide_ability(self, request: AbilityDecisionRequest) -> DecisionResult[GameAction]:
        return _first(request)

    def decide_attackers(self, request: DeclareAttackersRequest) -> DecisionResult[DeclareAttackersOption]:
        return _first(request)

    def decide_blockers(self, request: DeclareBlockersRequest) -> DecisionResult[DeclareBlockersOption]:
        return _first(request)

    def decide_mulligan(self, request: MulliganRequest) -> DecisionResult[MulliganOption]:
        return _first(request)

    def decide_mulligan_bottom(self, request: MulliganBottomRequest) -> DecisionResult[MulliganBottomOption]:
        return _first(request)

    def decide_discard(self, request: DiscardRequest) -> DecisionResult[DiscardOption]:
        return _first(request)

    def decide_ability_resolution(self, request: AbilityResolutionRequest) -> DecisionResult[AbilityResolutionOption]:
        return _first(request)

    def decide_mana(self, request: ManaGenerationRequest) -> DecisionResult[ManaSolverResult]:
        return _first(request)

    def decide_starting_player(self, request: StartingPlayerRequest) -> DecisionResult[StartingPlayerOption]:
        return DecisionResult(StartingPlayerOption(request.player))

    def decide_trigger_order(self, request: TriggerOrderRequest) -> DecisionResult[TriggerOrderOption]:
        return _first(request)

    def decide_legend(self, request: LegendRequest) -> DecisionResult[LegendOption]:
        return _first(request)

    def decide_replacement(self, request: ReplacementRequest) -> DecisionResult[ReplacementOption]:
        return _first(request)

    def decide_replacement_order(self, request: ReplacementOrderRequest) -> DecisionResult[ReplacementOrderOption]:
        return _first(request)

    def decide_optional_effect(self, request: OptionalEffectRequest) -> DecisionResult[OptionalEffectOption]:
        return _first(request)

    def decide_replacement_acceptance(self, request: ReplacementAcceptanceRequest) -> DecisionResult[ReplacementAcceptanceOption]:
        return _first(request)

    def decide_scry(self, request: ScryRequest) -> DecisionResult[ScryOption]:
        return _first(request)

    def decide_combat_damage(self, request: CombatDamageRequest) -> DecisionResult[CombatDamageOption]:
        return _first(request)
