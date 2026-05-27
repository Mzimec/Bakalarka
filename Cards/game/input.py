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
    """!
    @brief Steps used while progressively building a player command.
    """

    COMMAND = 1
    SOURCE = 2
    COST = 3
    TARGET = 4
    CONFIRM = 5
    DONE = 6


@dataclass(frozen=True)
class CommandData:
    """!
    @brief Immutable command parsing result collected from player input.
    """

    def __init__(self, command: str = None, source: str = None, c_targets: list[str] = None, targets: list[str] = None) -> None:
        """!
        @brief Create parsed command data.
        @param command Main command name.
        @param source Selected source identifier.
        @param c_targets Selected cost targets.
        @param targets Selected effect targets.
        """
        self.command = command
        self.source = source
        self.c_targets = c_targets
        self.targets = targets
    
    def with_command(self, command: str) -> CommandData:
        """!
        @brief Return a copy with a different command.
        @param command New command value.
        @return Updated command data.
        """
        return CommandData(command, self.source, self.c_targets, self.targets)
    
    def with_source(self, source: str) -> CommandData:
        """!
        @brief Return a copy with a different source.
        @param source New source value.
        @return Updated command data.
        """
        return CommandData(self.command, source, self.c_targets, self.targets)
    
    def with_c_targets(self, c_targets: list[str]) -> CommandData:
        """!
        @brief Return a copy with different cost targets.
        @param c_targets New cost target values.
        @return Updated command data.
        """
        return CommandData(self.command, self.source, c_targets, self.targets)
    
    def with_targets(self, targets: list[str]) -> CommandData:
        """!
        @brief Return a copy with different effect targets.
        @param targets New target values.
        @return Updated command data.
        """
        return CommandData(self.command, self.source, self.c_targets, targets)
    
    def __repr__(self) -> str:
        """!
        @brief Return a readable multi-line command summary.
        @return Command summary text.
        """
        return f""" Command: {self.command}
Source: {self.source}
Paying with: {self.c_targets}
Targeting: {self.targets}
        """

class ActionBuilderSession:
    """!
    @brief Interactive state machine for building one player action.
    """

    def __init__(self, state, player):
        """!
        @brief Create a command-building session for one player.
        @param state Current game state.
        @param player Player entering the command.
        """
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
        """!
        @brief Return valid input options for the current command step.
        @return Available textual options.
        """
        match self.step:
            case CommandStep.COMMAND: return ["play", "activate", "pass"]
            case CommandStep.SOURCE: return self._get_sources()
            case CommandStep.COST: return self._get_cost_options()
            case CommandStep.TARGET: return self._get_target_options()
            case CommandStep.CONFIRM: return ["confirm", "back", "reset"]
            case _: return []

    # 🔹 aplikace jednoho tokenu
    def apply(self, token: str) -> bool:
        """!
        @brief Apply one input token to the current command step.
        @param token User-provided token.
        @return True if the token was accepted.
        """
        match self.step:
            case CommandStep.COMMAND: return self._apply_command(token)
            case CommandStep.SOURCE: return self._apply_source(token)
            case CommandStep.COST: return self._apply_cost(token)
            case CommandStep.TARGET: return self._apply_target(token)
            case CommandStep.CONFIRM: return self._apply_confirm(token)
            case _: return False

    def is_complete(self) -> bool:
        """!
        @brief Check whether command building has finished.
        @return True if the session reached the done step.
        """
        return self.step == "DONE"

    def build(self):
        """!
        @brief Build a game action from the collected command data.
        @return Built game action, or None while building is not implemented.
        """
        # TODO: vytvoř GameAction
        return None
    
    def _get_sources(self) -> list[str]:
        """!
        @brief Return source identifiers valid for the selected command.
        @return Available source identifiers.
        """
        match self.command:
            case "play": 
                options = self.player.get_play_actions()
                return [o.id() for o in options]
            case "activate":
                options = self.player.get_activatable_actions()
                return [o.id() for o in options] 
            case _: return []
    
    def _get_cost_options(self) -> list[str]:
        """!
        @brief Return readable payment options for the selected ability.
        @return Payment option labels.
        """
        payment_options = self.ability.generate_payment_options(self.state)
        return [po.__repr__() for po in payment_options]
    
    def _get_target_options(self) -> list[str]:
        """!
        @brief Return readable target options for the selected ability.
        @return Target option labels.
        """
        pass


    
    def _apply_command(self, token: str) -> bool:
        """!
        @brief Parse and validate the main command token.
        @param token Raw user token.
        @return True if the command was accepted.
        """
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
        """!
        @brief Convert a command alias into its canonical command name.
        @param token Raw user token.
        @return Canonical command name, or None if the token is unknown.
        """
        for k, v in VALID_MAIN_COMMANDS.items():
            if token in v: return k
        return None
    
    def _validate_command(self, token: str, state: State, player: Player) -> bool:   
        """!
        @brief Check whether a command is currently available to the player.
        @param token Canonical command name.
        @param state Current game state.
        @param player Player entering the command.
        @return True if the command is legal.
        """
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
    """!
    @brief Older command builder that stores parsed command data.
    """

    def __init__(self) -> None:
        """!
        @brief Create an empty action builder.
        """
        self.data: CommandData = CommandData()
        self.current_step: CommandStep = CommandStep.COMMAND
        self.source = None
    

    

    
    def _get_source(self, state: State) -> Card:
        """!
        @brief Resolve the selected source identifier into a card.
        @param state Current game state.
        @return Selected source card.
        """
        return state.get_card(self.data.source)
    
    def _get_cost_requirements(self, state: State):
        """!
        @brief Return cost requirements for the selected source.
        @param state Current game state.
        @return Cost requirements.
        """
        return self.source.get_cost(state)
    
    def _get_target_requirements(self, state: State):
        """!
        @brief Return target requirements for the selected source.
        @param state Current game state.
        @return Target requirements.
        """
        return self.source.get_target_spec(state)




class PlayerInput:
    """!
    @brief Reads and parses textual commands from a player.
    """

    def get_command_data(self, state: State, player: Player) -> CommandData:
        """!
        @brief Read command data for a player.
        @param state Current game state.
        @param player Player providing input.
        @return Parsed command data.
        """
        command_data, input_split = self._get_command(state, player)

        return command_data
    
    def _try_parse_split_to_data(self, state: State, player: Player, input_str: list[str], data: CommandData = CommandData()) -> bool:
        """!
        @brief Try to parse tokenized input into command data.
        @param state Current game state.
        @param player Player providing input.
        @param input_str Reversed input tokens.
        @param data Partially parsed command data.
        @return True if parsing succeeds.
        """
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
        """!
        @brief Parse and validate the command part of an input token list.
        @param state Current game state.
        @param player Player providing input.
        @param input_str Reversed input tokens.
        @param data Command data being filled.
        @return True if a valid command was parsed.
        """
        command = "pass" if len(input_str==0) else input_str.pop()
        command = self._convert_command(command)
        if not command: return False
        if self._validate_command(command, state, player): 
            data = data.with_command(command)
            return True
        return False

    def get_input(self, query: str) -> tuple[list[str]]:
        """!
        @brief Read one input line and split it into reversed lowercase tokens.
        @param query Prompt shown to the user.
        @return Reversed input tokens.
        """
        res = input(query).lower().split()
        res.reverse()
        return res
    
    def _print_help(self) -> None:
        """!
        @brief Print a short help hint for interactive input.
        """
        print("For more info on available actions print help or h.")


    


