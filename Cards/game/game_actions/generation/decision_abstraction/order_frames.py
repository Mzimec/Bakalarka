"""Copyable, side-effect-free construction of a complete ordering.

One choice appends one candidate index. No operation is executed and no player
gets priority until finish() returns the same complete option as the exhaustive
pipeline. Runtime candidates are rebound by the ordinary world copier/replay.
"""
from dataclasses import dataclass

from ...data_structs.decision_option import DecisionOption
from .requests import StateDecisionRequest
from .policies import SelectionGenerationPolicy


@dataclass(frozen=True)
class OrderDecisionFrame:
    kind: str
    candidates: tuple
    prefix: tuple[int, ...] = ()
    version: int = 1

    def __post_init__(self):
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "prefix", tuple(self.prefix))
        if self.kind not in ("TriggerOrderRequest", "ReplacementOrderRequest") or self.version != 1:
            raise ValueError("Unsupported order frame contract")
        if (any(type(i) is not int or not 0 <= i < len(self.candidates) for i in self.prefix)
                or len(set(self.prefix)) != len(self.prefix)):
            raise ValueError("Order prefix must select distinct candidate positions")

    @property
    def remaining(self):
        selected = set(self.prefix)
        return tuple(i for i in range(len(self.candidates)) if i not in selected)

    def advance(self, index):
        if type(index) is not int or index not in self.remaining:
            raise ValueError("Choose one remaining candidate position")
        return OrderDecisionFrame(self.kind, self.candidates, (*self.prefix, index))

    def finish(self):
        if self.remaining:
            raise ValueError("Cannot execute an incomplete ordering")
        from .auxiliary import TriggerOrderOption, ReplacementOrderOption
        option = TriggerOrderOption if self.kind == "TriggerOrderRequest" else ReplacementOrderOption
        return option(tuple(self.candidates[i] for i in self.prefix))


@dataclass(frozen=True)
class OrderNextOption(DecisionOption):
    index: int


@dataclass(frozen=True)
class OrderNextRequest(StateDecisionRequest[OrderNextOption]):
    frame: OrderDecisionFrame
    option_type = OrderNextOption


class OrderNextPipeline:
    def generate(self, request, policy):
        if policy.pruning is not None:
            raise ValueError("Prefix pruning requires a dedicated candidate-space contract")
        for index in request.frame.remaining:
            yield OrderNextOption(index)


def choose_order(request, decide):
    frame = OrderDecisionFrame(type(request).__name__, request.candidates)
    previous = getattr(request.state, "order_decision_frame", None)
    if (type(previous) is OrderDecisionFrame and previous.kind == frame.kind
            and tuple(map(id, previous.candidates)) == tuple(map(id, frame.candidates))):
        frame = previous
    try:
        while frame.remaining:
            request.state.order_decision_frame = frame
            result = decide(OrderNextRequest(request.state, request.player, frame))
            frame = frame.advance(result.value.index)
        return frame.finish()
    finally:
        if previous is None:
            del request.state.order_decision_frame
        else:
            request.state.order_decision_frame = previous
