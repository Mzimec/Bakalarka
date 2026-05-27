from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from ..game_state import State
    from ..abilities.ability import TriggeredAbility
    from .game_action import GameAction


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
    @brief Collects emitted events and finds triggered abilities caused by them.
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

    def collect_triggered_abilities(self, state: State, events: list[GameEvent]) -> list[TriggeredAbility]:
        """!
        @brief Find triggered abilities that match any of the given events.
        @param state Current game state.
        @param events Events that may cause triggered abilities.
        @return Triggered abilities whose conditions are satisfied.
        """
        triggers: list[TriggeredAbility] = []

        for event in events:
            for ability in state.get_triggered_abilities(event):
                if ability.matches(state):
                    triggers.append(ability)

        return triggers

    def collect_triggered_actions(self, state: State, events: list[GameEvent]) -> list[TriggeredAbility]:
        """!
        @brief Compatibility wrapper for collecting triggered abilities.
        @param state Current game state.
        @param events Events that may cause triggered abilities.
        @return Triggered abilities whose conditions are satisfied.
        """
        return self.collect_triggered_abilities(state, events)


class TriggerResolver:
    """!
    @brief Orders triggered abilities and puts their selected actions on the stack.
    """

    def resolve(self, state: State, triggers: list[TriggeredAbility]) -> None:
        """!
        @brief Resolve trigger choices and enqueue their intents.
        @param state Current game state.
        @param triggers Triggered abilities waiting to be handled.
        """
        if not triggers:
            return

        for trigger in self._order_apnap(state, triggers):
            action: GameAction = self._decide_trigger(state, trigger)
            for intent in action.get_intents():
                state.stack.push(intent)

    def _order_apnap(self, state: State, triggers: list[TriggeredAbility]) -> list[TriggeredAbility]:
        """!
        @brief Sort triggers in active-player, non-active-player order.
        @param state Current game state.
        @param triggers Triggered abilities to order.
        @return Ordered triggered abilities.
        """
        players = list(state.players)
        active_idx = players.index(state.active_player)

        def order_key(trigger: TriggeredAbility) -> int:
            controller = trigger.source.owner
            return (players.index(controller) - active_idx) % len(players)

        return sorted(triggers, key=order_key)

    def _decide_trigger(self, state: State, trigger: TriggeredAbility) -> GameAction:
        """!
        @brief Choose one concrete action for a triggered ability.
        @param state Current game state.
        @param trigger Triggered ability being resolved.
        @return Chosen game action.
        """
        actions = trigger.generate_actions(state)
        if not actions:
            raise RuntimeError(f"Triggered ability {trigger.data.key!r} generated no actions.")
        if len(actions) == 1:
            return actions[0]

        controller = trigger.source.owner.controller
        chooser = getattr(controller, "choose_trigger_action", None)
        if chooser is not None:
            return chooser(state, trigger, actions)

        return actions[0]
