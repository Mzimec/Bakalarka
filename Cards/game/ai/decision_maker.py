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
from ..game_actions.generation.decision_abstraction.measurement import DecisionMeasurement, decision_measurement_enabled

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
    """!
    @brief Typed result wrapper returned by a decision maker.
    """
    def __init__(self, value: T, info: Mapping[str, Any] | None = None) -> None:
        """!
        @brief Initialize this object.
        """
        if not isinstance(value, DecisionOption):
            raise TypeError("DecisionResult requires a DecisionOption.")
        self.value: T = value
        self.info = dict(info or {})


class DecisionMaker(ABC):
    """!
    @brief Abstract base class for request-based decision makers.
    """
    decision_mode = "agent"

    def decide[T: DecisionOption](self, request: DecisionRequest[T]) -> DecisionResult[T]:
        """!
        @brief Return a validated decision for one request.
        """
        if not decision_measurement_enabled():
            return self._validated_decision(request)
        measurement = DecisionMeasurement(request)
        try:
            with measurement:
                result = self._validated_decision(request)
            measurement.metrics["auto_pass"] = result.info.get("auto_pass", False)
            measurement.metrics["auto_pass_reason"] = result.info.get("auto_pass_reason")
            result.info["elapsed_time"] = measurement.metrics["elapsed_ns"]
            result.info["decision_metrics"] = measurement.metrics
            return result
        finally:
            measurement.publish(self)

    def _validated_decision[T: DecisionOption](self, request: DecisionRequest[T]) -> DecisionResult[T]:
        """!
        @brief Run a decision and validate its result type.
        """
        result = self._decide(request)
        if not isinstance(result, DecisionResult) or not isinstance(result.value, request.option_type):
            raise TypeError(f"{type(request).__name__} requires DecisionResult[{request.option_type.__name__}].")
        return result

    @abstractmethod
    def _decide[T: DecisionOption](self, request: DecisionRequest[T]) -> DecisionResult[T]:
        """!
        @brief Dispatch or implement one decision request.
        """
        ...
def _first[T: DecisionOption](request: DecisionRequest[T]) -> DecisionResult[T]:
    """!
    @brief Internal helper for this module.
    """
    option = next(iter(request.options), None)
    if option is None:
        raise ValueError(f"No options available for {type(request).__name__}.")
    return DecisionResult(option)


class ModularDecisionMaker(DecisionMaker):
    """!
    @brief Override typed decide_* hooks independently; dispatch has no legacy logic.
    """

    def _decide(self, request: object) -> object:
        """!
        @brief Dispatch or implement one decision request.
        """
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
        """!
        @brief Choose an action while the player has priority.
        """
        return _first(request)

    def decide_ability(self, request: AbilityDecisionRequest) -> DecisionResult[GameAction]:
        """!
        @brief Choose a concrete action for one ability request.
        """
        return _first(request)

    def decide_attackers(self, request: DeclareAttackersRequest) -> DecisionResult[DeclareAttackersOption]:
        """!
        @brief Choose an attacker declaration.
        """
        return _first(request)

    def decide_blockers(self, request: DeclareBlockersRequest) -> DecisionResult[DeclareBlockersOption]:
        """!
        @brief Choose a blocker declaration.
        """
        return _first(request)

    def decide_mulligan(self, request: MulliganRequest) -> DecisionResult[MulliganOption]:
        """!
        @brief Choose whether to take a mulligan.
        """
        return _first(request)

    def decide_mulligan_bottom(self, request: MulliganBottomRequest) -> DecisionResult[MulliganBottomOption]:
        """!
        @brief Choose cards to bottom after a mulligan.
        """
        return _first(request)

    def decide_discard(self, request: DiscardRequest) -> DecisionResult[DiscardOption]:
        """!
        @brief Choose cards to discard.
        """
        return _first(request)

    def decide_ability_resolution(self, request: AbilityResolutionRequest) -> DecisionResult[AbilityResolutionOption]:
        """!
        @brief Choose cards for an ability-resolution request.
        """
        return _first(request)

    def decide_mana(self, request: ManaGenerationRequest) -> DecisionResult[ManaSolverResult]:
        """!
        @brief decide_mana helper.
        """
        return _first(request)

    def decide_starting_player(self, request: StartingPlayerRequest) -> DecisionResult[StartingPlayerOption]:
        """!
        @brief decide_starting_player helper.
        """
        return DecisionResult(StartingPlayerOption(request.player))

    def decide_trigger_order(self, request: TriggerOrderRequest) -> DecisionResult[TriggerOrderOption]:
        """!
        @brief decide_trigger_order helper.
        """
        return _first(request)

    def decide_legend(self, request: LegendRequest) -> DecisionResult[LegendOption]:
        """!
        @brief decide_legend helper.
        """
        return _first(request)

    def decide_replacement(self, request: ReplacementRequest) -> DecisionResult[ReplacementOption]:
        """!
        @brief decide_replacement helper.
        """
        return _first(request)

    def decide_replacement_order(self, request: ReplacementOrderRequest) -> DecisionResult[ReplacementOrderOption]:
        """!
        @brief decide_replacement_order helper.
        """
        return _first(request)

    def decide_optional_effect(self, request: OptionalEffectRequest) -> DecisionResult[OptionalEffectOption]:
        """!
        @brief decide_optional_effect helper.
        """
        return _first(request)

    def decide_replacement_acceptance(self, request: ReplacementAcceptanceRequest) -> DecisionResult[ReplacementAcceptanceOption]:
        """!
        @brief decide_replacement_acceptance helper.
        """
        return _first(request)

    def decide_scry(self, request: ScryRequest) -> DecisionResult[ScryOption]:
        """!
        @brief decide_scry helper.
        """
        return _first(request)

    def decide_combat_damage(self, request: CombatDamageRequest) -> DecisionResult[CombatDamageOption]:
        """!
        @brief decide_combat_damage helper.
        """
        return _first(request)
