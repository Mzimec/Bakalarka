"""Explicit parameter proposals, independent of agent scoring or card legality."""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ...data_structs.ability import Ability
    from ....game_state import State


@dataclass(frozen=True)
class AbilityParameters:
    x_value: int = 0
    life_payment: int | None = None

    def __post_init__(self):
        if type(self.x_value) is not int or self.x_value < 0:
            raise ValueError("X must be a nonnegative integer.")
        if self.life_payment is not None and (type(self.life_payment) is not int or self.life_payment < 0):
            raise ValueError("Life payment must be a nonnegative integer.")


class AbilityParameterStrategy(Protocol):
    def generate(self, ability: Ability, state: State) -> Iterable[AbilityParameters]: ...
