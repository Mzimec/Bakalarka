from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from ..game_state import State
    from ..abilities.ability import TriggeredAbility
    from .game_action import GameAction


@dataclass(frozen=True)
class GameEvent:
    key: str
    source: Any | None = None
    controller: Any | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self.emitted_events: list[GameEvent] = []

    def emit(self, event: GameEvent, state: State | None = None) -> None:
        self.emitted_events.append(event)

    def collect_triggered_abilities(self, state: State, events: list[GameEvent]) -> list[TriggeredAbility]:
        triggers: list[TriggeredAbility] = []

        for event in events:
            for ability in state.get_triggered_abilities(event):
                if ability.matches(state):
                    triggers.append(ability)

        return triggers

    def collect_triggered_actions(self, state: State, events: list[GameEvent]) -> list[TriggeredAbility]:
        return self.collect_triggered_abilities(state, events)


class TriggerResolver:
    def resolve(self, state: State, triggers: list[TriggeredAbility]) -> None:
        for trigger in self._order_apnap(state, triggers):
            action: GameAction = self._decide_trigger(state, trigger)
            for intent in action.get_intents():
                state.stack.push(intent)

    def _order_apnap(self, state: State, triggers: list[TriggeredAbility]) -> list[TriggeredAbility]:
        players = list(state.players)
        active_idx = players.index(state.active_player)

        def order_key(trigger: TriggeredAbility) -> int:
            controller = trigger.source.owner
            return (players.index(controller) - active_idx) % len(players)

        return sorted(triggers, key=order_key)

    def _decide_trigger(self, state: State, trigger: TriggeredAbility) -> GameAction:
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
