"""Typed, immutable values for decisions that are not game actions."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from collections.abc import Mapping
from immutabledict import immutabledict
from ...data_structs.decision_option import DecisionOption

if TYPE_CHECKING:
    from ....game_state import Card, Player


@dataclass(frozen=True)
class DeclareAttackersOption(DecisionOption):
    declarations: Mapping[Card, Player | Card]

    def __post_init__(self):
        object.__setattr__(self, "declarations", immutabledict(self.declarations))


@dataclass(frozen=True)
class DeclareBlockersOption(DecisionOption):
    declarations: Mapping[Card, Card]

    def __post_init__(self):
        object.__setattr__(self, "declarations", immutabledict(self.declarations))


@dataclass(frozen=True)
class MulliganOption(DecisionOption):
    take_mulligan: bool

    def __post_init__(self):
        if type(self.take_mulligan) is not bool:
            raise TypeError("MulliganOption requires a boolean.")


@dataclass(frozen=True)
class CardSelectionOption(DecisionOption):
    cards: tuple[Card, ...]

    def __post_init__(self):
        object.__setattr__(self, "cards", tuple(self.cards))


@dataclass(frozen=True)
class MulliganBottomOption(CardSelectionOption):
    """Cards in future draw order."""


@dataclass(frozen=True)
class DiscardOption(CardSelectionOption):
    pass


@dataclass(frozen=True)
class AbilityResolutionOption(CardSelectionOption):
    pass


__all__ = [
    "DecisionOption", "DeclareAttackersOption", "DeclareBlockersOption", "MulliganOption",
    "CardSelectionOption", "MulliganBottomOption", "DiscardOption", "AbilityResolutionOption",
]
