"""A small end-to-end game built from the engine's existing core layers.

This module is deliberately modest: it has no mana, cards, targeting, or AI.
It demonstrates the intended integration boundary instead: a controller picks a
GameAction, ActionProcessor resolves its Operations, EventBus records the
result, and GameLoop advances phases until a player loses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable

from game.game_actions import GameAction, ResolutionContext, ScheduledResolution, FixedExecutionPlan
from game.game_actions.game_stack import GameStack
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.action_executor import ActionExecutor
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus, GameEvent
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_loop.game_loop import GameLoop, Turn


@dataclass
class MinimalPlayer:
    """!
    @brief Minimal player implementation required by the integration example.

    The class intentionally exposes only the state and methods needed by the
    game loop and scripted priority controller.

    @var name
        Human-readable player name.
    @var controller
        Object responsible for selecting priority actions.
    @var health
        Life total used as the example's game-loss condition.
    @var battlefield
        Minimal battlefield collection used by turn-phase infrastructure.
    """

    name: str
    controller: "ScriptedController"
    health: int = 10

    # The untap phase only needs an iterable battlefield. A dict also leaves
    # room for a real card collection to replace it later.
    battlefield: dict[str, object] = field(default_factory=dict)

    def get_action(self, state: "MinimalState") -> GameAction | None:
        """!
        @brief Delegate priority action selection to this player's controller.

        @param state Current minimal game state.
        @return Selected action, or `None` to pass priority.
        """
        return self.controller.get_action(state, self)


class ScriptedController:
    """!
    @brief Deterministic FIFO action source for examples and integration tests.

    Once all injected actions have been consumed, the controller returns
    `None`, which the priority system interprets as passing priority.
    """

    def __init__(self, actions: Iterable[GameAction] = ()) -> None:
        """!
        @brief Initialize the controller with a fixed action sequence.

        @param actions Actions returned in insertion order.
        """
        self._actions = list(actions)

    def get_action(
        self,
        state: "MinimalState",
        player: MinimalPlayer,
    ) -> GameAction | None:
        """!
        @brief Return the next scripted action for the player.

        @param state Current game state.
        @param player Player currently requesting an action.
        @return Next queued action, or `None` when the script is exhausted.
        """
        return self._actions.pop(0) if self._actions else None


class MinimalState:
    """!
    @brief Small game-state adapter implementing the core loop's required API.

    This is not a reduced copy of the full engine state. It deliberately
    implements only the integration points required by `GameLoop`, stack
    resolution and trigger discovery.
    """

    def __init__(
        self,
        players: Iterable[MinimalPlayer],
        active_player_idx: int = 0,
    ) -> None:
        """!
        @brief Create a two-player minimal game state.

        @param players Exactly two participating players.
        @param active_player_idx Index of the initial active player.
        @throws ValueError If the supplied player count is not exactly two.
        """
        self.players = tuple(players)

        if len(self.players) != 2:
            raise ValueError("The minimal game supports exactly two players.")

        self._active_player_idx = active_player_idx
        self.stack = GameStack()
        self.turn = Turn()

    @property
    def active_player(self) -> MinimalPlayer:
        """!
        @brief Return the player whose turn is currently active.
        """
        return self.players[self._active_player_idx]

    def switch_active_player(self) -> None:
        """!
        @brief Advance active-player ownership to the other player.
        """
        self._active_player_idx = (
            self._active_player_idx + 1
        ) % len(self.players)

    def get_triggered_abilities(
        self,
        event: GameEvent | None = None,
    ) -> list[object]:
        """!
        @brief Return triggered abilities currently available in this state.

        The minimal example intentionally has no cards or triggered abilities.

        @param event Optional event being inspected.
        @return Always an empty list.
        """
        return []


class DamageOperation(Operation):
    """!
    @brief Subtract a fixed amount of life from one minimal player.
    """

    def __init__(
        self,
        context: ResolutionContext,
        target: MinimalPlayer,
        amount: int,
    ) -> None:
        """!
        @brief Create a direct-damage operation.

        @param context Resolution context associated with the action.
        @param target Player receiving the damage.
        @param amount Amount of life to remove.
        """
        super().__init__(context)
        self.target = target
        self.amount = amount

    def execute(self, state: MinimalState) -> list[GameEvent]:
        """!
        @brief Apply damage and emit the corresponding game event.

        @param state Current minimal game state.
        @return A single `damage_dealt` event.
        """
        self.target.health -= self.amount

        return [
            GameEvent(
                "damage_dealt",
                controller=self.context.controller,
                payload={
                    "target": self.target.name,
                    "amount": self.amount,
                },
            )
        ]


@dataclass(frozen=True)
class DealDamageAction(GameAction):
    """!
    @brief Example action resolving to one direct-damage operation.

    @var controller
        Player taking the action.
    @var target
        Player receiving the damage.
    @var amount
        Damage dealt when the action resolves.
    """

    controller: MinimalPlayer
    target: MinimalPlayer
    amount: int

    def get_intents(self) -> tuple[ScheduledResolution, ...]:
        """!
        @brief Build the scheduled resolution implementing this action.

        @return Single scheduled resolution containing one damage operation.
        """
        context = ResolutionContext(
            controller=self.controller,
            action_key="deal_damage",
        )

        return (
            ScheduledResolution(
                FixedExecutionPlan(
                    [
                        DamageOperation(
                            context,
                            self.target,
                            self.amount,
                        )
                    ]
                ),
                context,
            ),
        )


@dataclass(frozen=True)
class MinimalGame:
    """!
    @brief Bundle the state, event bus and loop of the runnable example.
    """

    state: MinimalState
    event_bus: EventBus
    loop: GameLoop

    def run(self) -> MinimalPlayer:
        """!
        @brief Run the example until the game-over condition is reached.

        @return Player whose life total reached zero or less.
        """
        self.loop.run(self.state)

        return next(
            player
            for player in self.state.players
            if player.health <= 0
        )


def create_minimal_game(
    first_actions: Iterable[GameAction] = (),
    second_actions: Iterable[GameAction] = (),
) -> MinimalGame:
    """!
    @brief Assemble a runnable two-player game using the normal engine pipeline.

    Controllers provide actions, `ActionProcessor` resolves them through
    `ActionExecutor` and `OperationExecutor`, `EventBus` records emitted events,
    and `GameLoop` manages turn progression and priority windows.

    @param first_actions Scripted actions for Player 1.
    @param second_actions Scripted actions for Player 2.
    @return Fully constructed minimal game.
    """
    first = MinimalPlayer(
        "Player 1",
        ScriptedController(),
    )
    second = MinimalPlayer(
        "Player 2",
        ScriptedController(),
    )

    first.controller = ScriptedController(first_actions)
    second.controller = ScriptedController(second_actions)

    state = MinimalState(
        (first, second)
    )

    event_bus = EventBus()

    processor = ActionProcessor(
        ActionExecutor(
            OperationExecutor(),
            event_bus,
        )
    )

    return MinimalGame(
        state=state,
        event_bus=event_bus,
        loop=GameLoop(
            None,
            processor,
        ),
    )