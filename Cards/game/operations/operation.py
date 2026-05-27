from __future__ import annotations

from abc import abstractmethod, ABC
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..game_state import State
    from ..game_actions.game_action import ResolutionContext
    from ..game_actions.event_bus import GameEvent


class Operation(ABC):

    def __init__(self, context: ResolutionContext) -> None:
        self.context = context
        
    @abstractmethod
    def execute(self, state: State) -> list[GameEvent]:
        pass



