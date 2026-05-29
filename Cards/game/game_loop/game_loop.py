from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..game_state import State, Player
    from ..game_actions import GameAction, PassPriorityAction
    from ..game_actions.action_processor import ActionProcessor



class TurnPhase(Enum):
    UNTAP = auto()
    UPKEEP = auto()
    DRAW = auto()

    PRECOMBAT_MAIN = auto()

    BEGIN_COMBAT = auto()
    DECLARE_ATTACKERS = auto()
    DECLARE_BLOCKERS = auto()
    COMBAT_DAMAGE = auto()
    END_COMBAT = auto()

    POSTCOMBAT_MAIN = auto()

    END_STEP = auto()
    CLEANUP = auto()


@dataclass
class Turn:
    phase: TurnPhase = TurnPhase.UNTAP

    _phase_order: list[TurnPhase] = field(default_factory=lambda: [
        TurnPhase.UNTAP,
        TurnPhase.UPKEEP,
        TurnPhase.DRAW,

        TurnPhase.PRECOMBAT_MAIN,

        TurnPhase.BEGIN_COMBAT,
        TurnPhase.DECLARE_ATTACKERS,
        TurnPhase.DECLARE_BLOCKERS,
        TurnPhase.COMBAT_DAMAGE,
        TurnPhase.END_COMBAT,

        TurnPhase.POSTCOMBAT_MAIN,

        TurnPhase.END_STEP,
        TurnPhase.CLEANUP,
    ])

    def advance_phase(self) -> bool:
        """
        Returns:
            True  -> new turn should begin
            False -> normal phase advance
        """

        idx = self._phase_order.index(self.phase)
        idx += 1

        if idx >= len(self._phase_order):
            self.phase = self._phase_order[0]
            return True

        self.phase = self._phase_order[idx]
        return False


@dataclass(frozen=True)
class PriorityWindowContext:
    allow_sorcery_speed: bool


class PriorityWindow:

    def __init__(self, state: State, context: PriorityWindowContext, processor: ActionProcessor) -> None:
        self.state = state
        self.context = context
        self.processor = processor

        self.players = self._order_players(
            state.players,
            state.active_player
        )

        self.current_index = 0

        self.passed_players: set[Player] = set()

    def run(self) -> None:
        while True:
            player = self.current_player
            action = self._get_player_action(player)

            if isinstance(action, PassPriorityAction):
                self._pass(player)
                self._advance_priority()
            else:
                self._process_action(action)

            if self._should_resolve_stack():
                self._resolve_top_stack()

            if self._should_close():
                return

            

    def _get_player_action(self, player: Player) -> GameAction:
        legal_actions = self._get_legal_actions(player)
        return player.controller.choose_action(
            self.state,
            legal_actions
        )
    
    def _process_action(self, action: GameAction) -> None:
        self.processor.process(self.state, action)
        self.passed_players.clear()
        self.current_index = 0

    def _get_legal_actions(self, player: Player) -> list[GameAction]:

        # TODO:
        # central legality system
        #
        # timing restrictions
        # sorcery speed
        # mana
        # target legality
        # etc.

        return player.get_actions(
            self.state,
            self.context
        )

    def _pass(self, player: Player) -> None:
        self.passed_players.add(player)

    def _all_players_passed(self) -> bool:
        return len(self.passed_players) >= len(self.players)

    def _play_action(self, action: GameAction) -> None:
        self.passed_players.clear()

        for intent in action.get_intents():
            self.state.stack.push(intent)

    def _should_resolve_stack(self) -> bool:
        return (
            self._all_players_passed()
            and
            not self.state.stack.is_empty()
        )

    def _resolve_top_stack(self) -> None:
        self.state.stack.resolve_top()

        # after resolution:
        # AP gets priority again

        self.passed_players.clear()
        self.current_index = 0

    def _should_close(self) -> bool:
        return (
            self._all_players_passed()
            and
            self.state.stack.is_empty()
        )

    @property
    def current_player(self) -> Player:
        return self.players[self.current_index]

    def _advance_priority(self) -> None:
        self.current_index += 1
        if self.current_index >= len(self.players):
            self.current_index = 0

    def _order_players(self, players: list[Player], active_player: Player) -> list[Player]:
        i = players.index(active_player)

        return players[i:] + players[:i]
    

class PhaseController(ABC):

    @property
    @abstractmethod
    def priority_context(self) -> PriorityWindowContext:
        pass

    def run(self, state: State, processor: ActionProcessor) -> None:

        self.execute_turn_based_actions(state)

        PriorityWindow(
            state,
            self.priority_context,
            processor=processor
        ).run()

    @abstractmethod
    def execute_turn_based_actions(self, state: State) -> None:
        pass


class UntapPhaseController(PhaseController):

    @property
    def priority_context(self) -> PriorityWindowContext:

        # no priority in untap step in MTG

        return PriorityWindowContext(
            allow_sorcery_speed=False
        )

    def run(self, state: State) -> None:

        self.execute_turn_based_actions(state)

        # no priority window

    def execute_turn_based_actions(self, state: State) -> None:
        for permanent in state.active_player.battlefield.permanents:
            permanent.is_tapped = False


class PrecombatMainPhaseController(PhaseController):

    @property
    def priority_context(self) -> PriorityWindowContext:

        return PriorityWindowContext(
            allow_sorcery_speed=True
        )

    def execute_turn_based_actions(self, state: State) -> None:
        pass


class EndStepPhaseController(PhaseController):

    @property
    def priority_context(self) -> PriorityWindowContext:

        return PriorityWindowContext(
            allow_sorcery_speed=False
        )

    def execute_turn_based_actions(self, state: State) -> None:
        pass


class GameLoop:

    def __init__(self, phase_map: dict[TurnPhase, PhaseController], processor: ActionProcessor) -> None:
        self.phase_map = phase_map
        self.processor = processor

    def run(self, state: State) -> None:

        while not self._is_game_over(state):
            phase = state.turn.phase
            controller = self.phase_map[phase]
            controller.run(state, self.processor)
            new_turn = state.turn.advance_phase()

            if new_turn:
                state.switch_active_player()

    def _is_game_over(self, state: State) -> bool:

        # TODO

        return any(p.health <= 0 for p in state.players)