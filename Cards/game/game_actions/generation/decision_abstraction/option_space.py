"""Reiterable, lazy views of selectable options in the current live state."""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Iterator
from .requests import DecisionRequest
from .policies import GenerationPolicy
from ...data_structs.decision_option import DecisionOption
from .registry import route_decision_generation
from .measurement import measure_options


@dataclass(frozen=True)
class DecisionOptionSpace[T: DecisionOption]:
    request: DecisionRequest[T]
    policy: GenerationPolicy | None = None

    def __iter__(self) -> Iterator[T]:
        yield from measure_options(
            self.request, self.policy, route_decision_generation(self.request, self.policy),
        )

    def generate(self) -> Iterator[T]:
        return iter(self)
