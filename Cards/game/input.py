from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .game_state.state import State
    from .game_state.player import Player
    from .game_state.card import Card
    from .abilities.ability import GameAction, Ability

VALID_MAIN_COMMANDS: dict[str, set[str]] = {
    "play": {"play", "p"},
    "activate": {"activate", "a"},
    "pass": {"pass", ""},
}

class CommandStep(Enum):
    COMMAND = 1
    SOURCE = 2
    COST = 3
    TARGET = 4
    CONFIRM = 5
    DONE = 6


@dataclass(frozen=True)
class CommandData:
    def __init__(self, command: str = None, source: str = None, c_targets: list[str] = None, targets: list[str] = None) -> None:
        self.command = command
        self.source = source
        self.c_targets = c_targets
        self.targets = targets
    
    def with_command(self, command: str) -> CommandData:
        return CommandData(command, self.source, self.c_targets, self.targets)
    
    def with_source(self, source: str) -> CommandData:
        return CommandData(self.command, source, self.c_targets, self.targets)
    
    def with_c_targets(self, c_targets: list[str]) -> CommandData:
        return CommandData(self.command, self.source, c_targets, self.targets)
    
    def with_targets(self, targets: list[str]) -> CommandData:
        return CommandData(self.command, self.source, self.c_targets, targets)
    
    def __repr__(self) -> str:
        return f""" Command: {self.command}
Source: {self.source}
Paying with: {self.c_targets}
Targeting: {self.targets}
        """

class ActionBuilderSession:
    def __init__(self, state, player):
        self.state: State = state
        self.player: Player = player

        self.command: str | None = None
        self.source: str | None = None
        self.cost_targets: list[str] = []
        self.targets: list[str] = []

        self.ability: Ability | None = None

        self.step = CommandStep.COMMAND

    # 🔹 co může hráč teď zadat
    def get_options(self) -> list[str]:
        match self.step:
            case CommandStep.COMMAND: return ["play", "activate", "pass"]
            case CommandStep.SOURCE: return self._get_sources()
            case CommandStep.COST: return self._get_cost_options()
            case CommandStep.TARGET: return self._get_target_options()
            case CommandStep.CONFIRM: return ["confirm", "back", "reset"]
            case _: return []

    # 🔹 aplikace jednoho tokenu
    def apply(self, token: str) -> bool:
        match self.step:
            case CommandStep.COMMAND: return self._apply_command(token)
            case CommandStep.SOURCE: return self._apply_source(token)
            case CommandStep.COST: return self._apply_cost(token)
            case CommandStep.TARGET: return self._apply_target(token)
            case CommandStep.CONFIRM: return self._apply_confirm(token)
            case _: return False

    def is_complete(self) -> bool:
        return self.step == "DONE"

    def build(self):
        # TODO: vytvoř GameAction
        return None
    
    def _get_sources(self) -> list[str]:
        match self.command:
            case "play": 
                options = self.player.get_play_actions()
                return [o.id() for o in options]
            case "activate":
                options = self.player.get_activatable_actions()
                return [o.id() for o in options] 
            case _: return []
    
    def _get_cost_options(self) -> list[str]:
        payment_options = self.ability.generate_payment_options(self.state)
        return [po.__repr__() for po in payment_options]
    
    def _get_target_options(self) -> list[str]:
        pass


    
    def _apply_command(self, token: str) -> bool:
        token = self._convert_command(token)
        if not token: return False
        if token == "pass":
            self.current_step = CommandStep.READY
            return True
        if self._validate_command(token, self.state, self.player):
            self.data = self.data.with_command(token)
            return True
        return False
    
    def _convert_command(self, token: str) -> str | None:
        for k, v in VALID_MAIN_COMMANDS.items():
            if token in v: return k
        return None
    
    def _validate_command(self, token: str, state: State, player: Player) -> bool:   
        match token:
            case "play":
                if not player.has_playable_card(state):
                    print("You have no playable cards!")
                    return False
            case "activate":
                if not player.has_activatable_ability(state):
                    print("You have no activatable ability!")
                    return False
            case _: pass
        return True
    


class ActionBuilder:
    def __init__(self) -> None:
        self.data: CommandData = CommandData()
        self.current_step: CommandStep = CommandStep.COMMAND
        self.source = None
    

    

    
    def _get_source(self, state: State) -> Card:
        return state.get_card(self.data.source)
    
    def _get_cost_requirements(self, state: State):
        return self.source.get_cost(state)
    
    def _get_target_requirements(self, state: State):
        return self.source.get_target_spec(state)




class PlayerInput:
    def get_command_data(self, state: State, player: Player) -> CommandData:
        command_data, input_split = self._get_command(state, player)

        return command_data
    
    def _try_parse_split_to_data(self, state: State, player: Player, input_str: list[str], data: CommandData = CommandData()) -> bool:
        if data.command == None:
            if not self._try_get_command(state, player, input_str, data): return False
            if self._is_no_args_command(data.command): return True

        if len(input_str) == 0: return False
        if data.source == None:
            pass

        if len(input_str) == 0: return False
        if data.c_targets == None:
            pass

        if len(input_str) == 0: return False
        if data.targets == None:
            pass

        if len(input_str) == 0: return True
        if data.options == None:
            pass
    
    def _try_get_command(self, state: State, player: Player, input_str: list[str], data: CommandData) -> bool:
        command = "pass" if len(input_str==0) else input_str.pop()
        command = self._convert_command(command)
        if not command: return False
        if self._validate_command(command, state, player): 
            data = data.with_command(command)
            return True
        return False

    def get_input(self, query: str) -> tuple[list[str]]:
        res = input(query).lower().split()
        res.reverse()
        return res
    
    def _print_help(self) -> None:
        print("For more info on available actions print help or h.")


    


