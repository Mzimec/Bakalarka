"""Decision data, independent of controllers and generation strategies."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....game_state import State, Player, Card
    from ...data_structs.ability import Ability
    from ...data_structs.game_action import GameAction
    from ....ai.mana_solver import ManaSolverResult
    from ....mana.mana_value import ManaRequirement

@dataclass(frozen=True)
class DecisionRequest[T]:
    state: State
    player: Player

    def option_space(self, policy=None):
        from .decision_option import DecisionOptionSpace
        return DecisionOptionSpace(self, policy)

    @property
    def options(self):
        return self.option_space()

@dataclass(frozen=True)
class PriorityDecisionRequest(DecisionRequest["GameAction"]):
    pass

@dataclass(frozen=True)
class AbilityDecisionRequest(DecisionRequest["GameAction"]):
    ability: Ability

@dataclass(frozen=True)
class DeclareAttackersRequest(DecisionRequest[dict]):
    pass

@dataclass(frozen=True)
class DeclareBlockersRequest(DecisionRequest[dict]):
    pass

@dataclass(frozen=True)
class MulliganRequest(DecisionRequest[bool]):
    mulligans_taken: int = 0
    can_mulligan: bool = True

@dataclass(frozen=True)
class MulliganBottomRequest(DecisionRequest[tuple]):
    count: int

@dataclass(frozen=True)
class DiscardRequest(DecisionRequest[tuple]):
    count: int

@dataclass(frozen=True)
class ManaGenerationRequest(DecisionRequest["ManaSolverResult"]):
    requirement: ManaRequirement
    reserved: frozenset[Card] = frozenset()

# Names used by earlier versions of the decision-maker interface.
PriorityActionRequest = PriorityDecisionRequest
AbilityRequest = AbilityDecisionRequest
AttackerDeclarationRequest = DeclareAttackersRequest
BlockerDeclarationRequest = DeclareBlockersRequest


@dataclass(frozen=True)
class AbilityResolutionRequest(DecisionRequest[tuple]):
    """A choice made by the affected player while one effect is resolving.

    The resolving effect supplies eligible objects; activation, timing and mana
    checks do not apply a second time. Context identifies the originating ability.
    """
    context: object
    candidates: tuple
    count: int
    kind: str
