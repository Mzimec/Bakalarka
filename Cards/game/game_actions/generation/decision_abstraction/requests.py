"""Decision data, independent of controllers and generation strategies."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from ...data_structs.decision_option import DecisionOption
from .options import (DeclareAttackersOption, DeclareBlockersOption, MulliganOption,
                      MulliganBottomOption, DiscardOption, AbilityResolutionOption)

if TYPE_CHECKING:
    from .option_space import DecisionOptionSpace
    from .policies import GenerationPolicy
    from ....game_state import State, Player, Card
    from ...data_structs.ability import Ability
    from ...data_structs.game_action import GameAction
    from ....mana.mana_solver import ManaSolverResult
    from ....mana.mana_value import ManaRequirement

@dataclass(frozen=True)
class DecisionRequest[T: DecisionOption]:
    state: State | None
    player: Player

    @property
    def participants(self) -> tuple[Player, ...]:
        if self.state is None:
            raise ValueError("This decision requires a game state.")
        return tuple(self.state.players)

    @property
    def option_type(self) -> type[T]:
        raise NotImplementedError

    def option_space(self, policy: GenerationPolicy | None = None) -> DecisionOptionSpace[T]:
        from .option_space import DecisionOptionSpace
        return DecisionOptionSpace(self, policy)

    @property
    def options(self) -> DecisionOptionSpace[T]:
        return self.option_space()

@dataclass(frozen=True)
class StateDecisionRequest[T: DecisionOption](DecisionRequest[T]):
    """An in-game request always carries a State; setup requests may not."""
    state: State

@dataclass(frozen=True)
class PriorityDecisionRequest(StateDecisionRequest["GameAction"]):
    @property
    def option_type(self):
        from ...data_structs.game_action import GameAction
        return GameAction

@dataclass(frozen=True)
class AbilityDecisionRequest(StateDecisionRequest["GameAction"]):
    ability: Ability
    choices: tuple[GameAction, ...] | None = None

    @property
    def option_type(self):
        from ...data_structs.game_action import GameAction
        return GameAction

@dataclass(frozen=True)
class DeclareAttackersRequest(StateDecisionRequest[DeclareAttackersOption]):
    option_type = DeclareAttackersOption

@dataclass(frozen=True)
class DeclareBlockersRequest(StateDecisionRequest[DeclareBlockersOption]):
    option_type = DeclareBlockersOption

@dataclass(frozen=True)
class MulliganRequest(StateDecisionRequest[MulliganOption]):
    option_type = MulliganOption
    mulligans_taken: int = 0
    can_mulligan: bool = True

@dataclass(frozen=True)
class MulliganBottomRequest(StateDecisionRequest[MulliganBottomOption]):
    option_type = MulliganBottomOption
    count: int

@dataclass(frozen=True)
class DiscardRequest(StateDecisionRequest[DiscardOption]):
    option_type = DiscardOption
    count: int

@dataclass(frozen=True)
class ManaGenerationRequest(StateDecisionRequest["ManaSolverResult"]):
    requirement: ManaRequirement
    reserved: frozenset[Card] = frozenset()

    @property
    def option_type(self):
        from ....mana.mana_solver import ManaSolverResult
        return ManaSolverResult

# Names used by earlier versions of the decision-maker interface.
PriorityActionRequest = PriorityDecisionRequest
AbilityRequest = AbilityDecisionRequest
AttackerDeclarationRequest = DeclareAttackersRequest
BlockerDeclarationRequest = DeclareBlockersRequest


@dataclass(frozen=True)
class AbilityResolutionRequest(StateDecisionRequest[AbilityResolutionOption]):
    """A choice made by the affected player while one effect is resolving.

    The resolving effect supplies eligible objects; activation, timing and mana
    checks do not apply a second time. Context identifies the originating ability.
    """
    option_type = AbilityResolutionOption
    context: object
    candidates: tuple
    count: int
    kind: str

__all__ = [
    "DecisionRequest", "StateDecisionRequest", "PriorityDecisionRequest", "AbilityDecisionRequest",
    "DeclareAttackersRequest", "DeclareBlockersRequest", "MulliganRequest",
    "MulliganBottomRequest", "DiscardRequest", "ManaGenerationRequest",
    "AbilityResolutionRequest", "PriorityActionRequest", "AbilityRequest",
    "AttackerDeclarationRequest", "BlockerDeclarationRequest",
]
