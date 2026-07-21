from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping
from collections.abc import Iterable
from immutabledict import immutabledict

if TYPE_CHECKING:
    from ...game_state import State, Player
    from ..data_structs.ability import TriggerAbility
    from ..data_structs.game_action import GameAction


@dataclass(frozen=True)
class GameEvent:
    """!
    @brief Immutable description of something that happened during the game.
    """

    key: str
    source: Any | None = None
    controller: Any | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


class EventBus:
    """!
    @brief Collects emitted events and finds Trigger abilities caused by them.
    """

    def __init__(self) -> None:
        """!
        @brief Create an empty event bus.
        """
        self.emitted_events: list[GameEvent] = []

    def emit(self, event: GameEvent, state: State | None = None) -> None:
        """!
        @brief Store an event that was emitted during resolution.
        @param event Event to record.
        @param state Optional current game state.
        """
        self.emitted_events.append(event)

    def collect_trigger_abilities(self, state: State, events: Iterable[GameEvent]) -> list[TriggerAbility]:
        """!
        @brief Find trigger abilities that match any of the given events.
        @param state Current game state.
        @param events Events that may cause trigger abilities.
        @return Trigger abilities whose conditions are satisfied.
        """
        triggers: list[TriggerAbility] = []

        for event in events:
            for ability in state.get_trigger_abilities(event):
                if ability.matches(state):
                    triggers.append(ability)

        return triggers

    def collect_trigger_actions(self, state: State, events: list[GameEvent]) -> list[TriggerAbility]:
        """!
        @brief Compatibility wrapper for collecting trigger abilities.
        @param state Current game state.
        @param events Events that may cause trigger abilities.
        @return Trigger abilities whose conditions are satisfied.
        """
        return self.collect_trigger_abilities(state, events)


class TriggerProcessor:
    """!
    @brief Orders trigger abilities and puts their selected actions on the stack.
    """

    def process(self, state: State, triggers: list[TriggerAbility]) -> None:
        """!
        @brief Resolve trigger choices and enqueue their intents.
        @param state Current game state.
        @param triggers Trigger abilities waiting to be handled.
        """
        if not triggers:
            return
        
        trigger_dict: Mapping[int, list[TriggerAbility]] = {i: [] for i in range(len(state.active_players))}
        for trigger in triggers:
            if trigger.controller.idx not in trigger_dict:
                raise KeyError(  f"Key '{trigger.controller.idx}' is not index of any active player.")
            else:
                trigger_dict[trigger.controller.idx].append(trigger)

        active_player_idx = state.active_player.idx
        for i in range(len(state.active_players)):
            player_idx = (active_player_idx + i) % len(state.active_players)
            self._decide_triggers(state.active_players[i], state, trigger_dict[player_idx])


    def _decide_triggers(self, controller: Player, state: State, triggers: Iterable[TriggerAbility]) -> None:
        """!
        @brief Choose one concrete action for a trigger ability.
        @param state Current game state.
        @param trigger Trigger ability being resolved.
        @return Chosen game action.
        """
        controller.decision_maker.process_triggers(state, triggers)
