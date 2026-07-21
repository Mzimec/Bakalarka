from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from ..mana.mana_value import ManaRequirement
    from ..game_state import State, Player
    from ..game_actions.data_structs.game_action import ManaAbilityAction



@dataclass(frozen=True)
class ManaSolverResult:

    mana_plan: tuple[ManaAbilityAction]
    future_state: State


# TODO

class ManaSolver(ABC):

    @abstractmethod
    def get_mana_plan(
        self, 
        mana_req: ManaRequirement, 
        controller: Player, state: State
    ) -> ManaSolverResult | None: ...