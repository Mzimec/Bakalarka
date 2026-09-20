"""Controllers choose values; request option spaces own their generation."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Mapping
from time import perf_counter_ns
from typing import Any
from ..game_actions.generation.decision_abstraction.requests import *
from ..game_actions.generation.decision_abstraction.decision_option import DecisionOptionSpace

class DecisionResult[T]:
    def __init__(self, value: T, info: Mapping[str, Any] | None = None):
        self.value: T = value
        self.info = dict(info or {})

class DecisionMaker(ABC):
    def decide[T](self, request: DecisionRequest[T]) -> DecisionResult[T]:
        started = perf_counter_ns()
        result = self._decide(request)
        if not isinstance(result, DecisionResult):
            raise TypeError("A decision maker must return DecisionResult.")
        result.info["elapsed_time"] = perf_counter_ns() - started
        return result

    @abstractmethod
    def _decide[T](self, request: DecisionRequest[T]) -> DecisionResult[T]: ...

    def get_action(self, state, player):
        return self.decide(PriorityDecisionRequest(state, player)).value

    def choose_attackers(self, state, player):
        return self.decide(DeclareAttackersRequest(state, player)).value

    def choose_blockers(self, state, player):
        return self.decide(DeclareBlockersRequest(state, player)).value

    def choose_mulligan(self, state, player, mulligans_taken):
        return self.decide(MulliganRequest(state, player, mulligans_taken)).value

    def choose_mulligan_bottom(self, state, player, count):
        return self.decide(MulliganBottomRequest(state, player, count)).value

    def choose_discards(self, state, player, count):
        return self.decide(DiscardRequest(state, player, count)).value

class ModularDecisionMaker(DecisionMaker):
    """Compatibility bridge for controllers implementing the earlier hooks.

    New controllers should implement DecisionMaker._decide and consume
    request.option_space(policy). Existing hooks can migrate independently.
    """
    def _decide(self, request):
        if isinstance(request, PriorityDecisionRequest):
            # Some existing controllers customize get_action directly.
            if type(self).get_action is not DecisionMaker.get_action:
                return DecisionResult(self.get_action(request.state, request.player))
            return self._decide_priority_action(request.state, request.player)
        if isinstance(request, AbilityResolutionRequest):
            if request.kind == "sacrifice":
                chooser = getattr(self, "choose_sacrifices", None)
                if chooser is not None:
                    return DecisionResult(chooser(request.state, request.player, request.candidates, request.count))
            elif request.kind == "discard":
                return self.decide(DiscardRequest(request.state, request.player, request.count))
            return DecisionResult(next(iter(request.options)))
        hooks = {
            DeclareAttackersRequest: ("choose_attackers", ()),
            DeclareBlockersRequest: ("choose_blockers", ()),
            MulliganRequest: ("choose_mulligan", (getattr(request, "mulligans_taken", 0),)),
            MulliganBottomRequest: ("choose_mulligan_bottom", (getattr(request, "count", 0),)),
            DiscardRequest: ("choose_discards", (getattr(request, "count", 0),)),
        }
        hook = hooks.get(type(request))
        if hook is not None:
            name, args = hook
            if getattr(type(self), name) is not getattr(DecisionMaker, name):
                return DecisionResult(getattr(self, name)(request.state, request.player, *args))
        if isinstance(request, (DeclareAttackersRequest, DeclareBlockersRequest)):
            return DecisionResult({})
        if isinstance(request, MulliganRequest):
            return DecisionResult(False)
        if isinstance(request, (MulliganBottomRequest, DiscardRequest)):
            return DecisionResult(tuple(request.player.hand.values())[:request.count])
        if isinstance(request, (AbilityDecisionRequest, ManaGenerationRequest)):
            return DecisionResult(next(iter(request.options), None))
        raise NotImplementedError(f"Unsupported request: {type(request).__name__}")

    def process_triggers(self, state, triggers):
        """Legacy hook sentinel; trigger processing belongs to TriggerProcessor."""
        raise NotImplementedError

    def _decide_priority_action(self, state, player):
        raise NotImplementedError("Implement priority selection or _decide.")


def decision_hook(controller, name):
    """Resolve an engine callback through decide, or a legacy controller hook."""
    requests = {
        "choose_attackers": DeclareAttackersRequest,
        "choose_blockers": DeclareBlockersRequest,
        "choose_mulligan": MulliganRequest,
        "choose_mulligan_bottom": MulliganBottomRequest,
        "choose_discards": DiscardRequest,
    }
    if isinstance(controller, DecisionMaker) and name in requests:
        return lambda state, player, *args: controller.decide(requests[name](state, player, *args)).value
    return getattr(controller, name, None)
