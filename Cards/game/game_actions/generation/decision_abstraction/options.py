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
    """!
    @brief Decision option containing attacker-to-defender declarations.
    """
    declarations: Mapping[Card, Player | Card]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "declarations", immutabledict(self.declarations))


@dataclass(frozen=True)
class DeclareBlockersOption(DecisionOption):
    """!
    @brief Decision option containing blocker-to-attacker declarations.
    """
    declarations: Mapping[Card, Card]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "declarations", immutabledict(self.declarations))


@dataclass(frozen=True)
class MulliganOption(DecisionOption):
    """!
    @brief Decision option indicating whether to take a mulligan.
    """
    take_mulligan: bool

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        if type(self.take_mulligan) is not bool:
            raise TypeError("MulliganOption requires a boolean.")


@dataclass(frozen=True)
class CardSelectionOption(DecisionOption):
    """!
    @brief Decision option containing an ordered tuple of selected cards.
    """
    cards: tuple[Card, ...]

    def __post_init__(self) -> None:
        """!
        @brief Normalize or validate immutable option payloads.
        """
        object.__setattr__(self, "cards", tuple(self.cards))


@dataclass(frozen=True)
class MulliganBottomOption(CardSelectionOption):
    """!
    @brief Cards selected for the bottom of the library in future draw order.
    """


@dataclass(frozen=True)
class DiscardOption(CardSelectionOption):
    """!
    @brief Decision option selecting cards to discard.
    """
    pass


@dataclass(frozen=True)
class AbilityResolutionOption(CardSelectionOption):
    """!
    @brief Decision option selecting cards during ability resolution.
    """
    pass


__all__ = [
    "DecisionOption", "DeclareAttackersOption", "DeclareBlockersOption", "MulliganOption",
    "CardSelectionOption", "MulliganBottomOption", "DiscardOption", "AbilityResolutionOption",
]
