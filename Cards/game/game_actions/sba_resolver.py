from __future__ import annotations

from typing import TYPE_CHECKING
from abc import abstractmethod

if TYPE_CHECKING:
    from ..game_state import State
    from .event_bus import GameEvent


class SBAResolver:
    def __init__(self, rules: list[StateBasedAction] | None = None) -> None:
        self.rules = list(rules) if rules is not None else []

    def resolve(self, state: State) -> list[GameEvent]:
        events: list[GameEvent] = []
        is_changed = True
        while is_changed:
            is_changed = False

            for rule in self.rules:
                if not rule.applies(state):
                    continue

                events.extend(rule.execute(state))
                is_changed = True
        
        return events
    
        


class StateBasedAction:
    
    @abstractmethod
    def applies(self, state: State) -> bool:
        pass

    @abstractmethod
    def execute(self, state: State) -> list[GameEvent]:
        pass
