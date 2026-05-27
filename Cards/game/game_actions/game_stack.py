from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game_action import GameActionResolutionPayload, PassPriorityAction
    from action_executor import ActionExecutor
    from action_processor import ActionProcessor
    from ..game_state import State, Player

class GameStack:

    def __init__(self) -> None:
        self.items: list[GameActionResolutionPayload] = []

    def push(
        self,
        item: GameActionResolutionPayload
    ) -> None:

        self.items.append(item)

    def pop(self) -> GameActionResolutionPayload:

        if not self.items:
            raise RuntimeError("Stack is empty.")

        return self.items.pop()

    def peek(self) -> GameActionResolutionPayload | None:

        if not self.items:
            return None

        return self.items[-1]

    def is_empty(self) -> bool:
        return len(self.items) == 0


class PrioritySystem:

    def __init__(self, state: State) -> None:
        self.state = state
        self.current_player = state.active_player
        self.passed_players: set[Player] = set()

    def pass_priority(self, player: Player) -> None:
        self.passed_players.add(player)
        self.current_player = self.state.get_next_player(player)

    def reset(self) -> None:
        self.current_player = self.state.active_player
        self.passed_players.clear()

    def all_passed(self, state: State) -> bool:
        return len(self.passed_players) >= len(state.players)
    

class PriorityLoop:

    def __init__(self, action_processor: ActionProcessor, action_executor: ActionExecutor) -> None:
        self.action_processor = action_processor
        self.action_executor = action_executor

    def run(self,state: State) -> None:
        priority = state.priority_system
        while True:
            player = priority.current_player
            action = player.get_action(state)

            if isinstance(action, PassPriorityAction):
                priority.pass_priority(player)
            else:
                self.action_processor.process(state, action)
                priority.reset()

            if not priority.all_passed(state):
                continue

            if state.stack.is_empty():
                return

            payload = state.stack.pop()
            self.action_executor.execute(state, payload)

            priority.reset()

class ReactionWindow:
    def __init__(self, action_processor: ActionProcessor) -> None:
        self.action_processor = action_processor
    
    def run(self, state: State) -> None:

        priority = state.priority

        while not priority.all_passed():
            player = priority.current_player
            action = player.get_action(state)
            self.action_processor.process(state, action)             



class StackResolver:

    def __init__(self, executor: ActionExecutor) -> None:
        self.executor = executor

    def resolve_top(self, state: State) -> None:
        payload = state.stack.pop()
        self.executor.execute(state, payload)
