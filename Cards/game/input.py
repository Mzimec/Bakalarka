from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from enum import Enum, auto
from dataclasses import dataclass

if TYPE_CHECKING:
    from .game_loop.game_loop import TurnPhase
    from .target import TargetBinding, TargetSlot
    from .abilities import Ability, SubAbilityDefinition
    from .game_state import State, Player, Card
    from .game_actions import GameAction

COMMAND_ALIASES: dict[str, set[str]] = {
    "play":     {"play", "p"},
    "activate": {"activate", "a"},
    "pass":     {"pass", ""},
    "attack":   {"attack", "a"},
    "rattack":  {"rattack", "ra"},
    "block":    {"block", "b"},
    "rblock":   {"rblock", "rb"}
}

POSSIBLE_COMMANDS: dict[TurnPhase, set[str]] = {
    TurnPhase.UPKEEP: {"pass", "play", "activate"},

    TurnPhase.PRECOMBAT_MAIN: {"pass", "play", "activate"},

    TurnPhase.DECLARE_ATTACKERS: {"pass", "declare_attacker", "remove_attacker"},
    TurnPhase.AFTER_ATTACKERS: {"pass", "play", "activate"},
    TurnPhase.DECLARE_BLOCKERS: {"pass", "declare_blocker", "remove_blocker"},
    TurnPhase.AFTER_BLOCKERS: {"pass", "play", "activate"},
    TurnPhase.FIRST_COMBAT_DAMAGE: {"pass", "play", "activate"},
    TurnPhase.SECOND_COMBAT_DAMAGE: {"pass", "play", "activate"},
    TurnPhase.END_COMBAT: {"pass", "play", "activate"},

    TurnPhase.POSTCOMBAT_MAIN: {"pass", "play", "activate"},

    TurnPhase.ENDstep: {"pass", "play", "activate"}
}


class StepType(int, Enum):
    COMMAND = auto()
    SOURCE  = auto()
    MODE = auto()
    COST    = auto()
    TARGET  = auto()
    CONFIRM = auto()
    DONE    = auto()


class StringParser(ABC):

    @abstractmethod
    def parse(self, line: str) -> list[Any] | None:
        pass

class KeyParser(StringParser):

    def __init__(self, state: State) -> None:
        self._state = state

    def parse(self, line: str) -> list[Any] | None:
        keys = line.lower().split()
        if len(keys) <= 0:
            return None
        
        res: list[Any] = []
        for key in keys:
            obj = self._read_key(key)
            if not obj:
                return None
            res.append(obj)
    
    def _read_key(self, key: str) -> Any | None:
        pass




@dataclass(frozen=True)
class ActionDefinition:

    needed_steps: set[StepType]
    ability_def: Ability | None

    @staticmethod
    def without_ability(steps: set[StepType]) -> ActionDefinition:
        return ActionDefinition(
            needed_steps=steps,
            ability_def=None
            )
    
    @staticmethod
    def with_ability(steps: set[StepType], ability: Ability) -> ActionDefinition:
        return ActionDefinition(
            needed_steps=steps,
            ability_def=ability
        )


@dataclass
class SessionData:

    step: StepType = StepType.COMMAND
    command: str | None = None
    action_def: ActionDefinition | None = None
    mode: int | None = None
    source: Any | None = None
    ability: Ability | None = None
    cost_binding: TargetBinding | None = None
    target_binding: TargetBinding | None = None


class ActionFactory:

    @staticmethod
    def build(data: SessionData, state: State) -> GameAction:
        """
        Build the GameAction that matches the cost and target bindings
        chosen by the player.
 
        Searches through Ability.generate_actions() for the action whose
        cost and effect generator bindings equal the recorded ones.
        """
        from .game_actions.game_action import PassPriorityAction
 
        if data.command == "pass":
            # data.ability.source is the Player set during SourceStep.
            return PassPriorityAction(player=data.ability.source)  # type: ignore[arg-type]
 
        assert data.ability is not None
 
        actions = data.ability.generate_actions(state)
        for action in actions:
            cost_intent, action_intent = action.get_intents()
            if (
                cost_intent.generator.binding   == data.cost_bindings
                and action_intent.generator.binding == data.target_bindings
            ):
                return action
 
        raise RuntimeError(
            "ActionFactory: no generated action matches the recorded bindings.\n"
            f"  cost_bindings   = {data.cost_bindings}\n"
            f"  target_bindings = {data.target_bindings}"
        )


class BuildStep(ABC):

    @abstractmethod
    def run(self, state: State, player: Player, data: SessionData) -> None:
        pass

    def _get_possible_attackers(self, state: State, player: Player) -> list[Card]:
        return state.get_possible_attackers(player)
    
    def _get_possible_blockers(self, state: State, player: Player) -> list[Card]:
        return state.get_possible_blockers(player)
    
    def _get_attackers(self, state: State, player: Player) -> list[Card]:
        return state.get_attackers(player)
    
    def _get_blockers(self, state: State, player: Player) -> list[Card]:
        return state.get_blockers(player)
    
    def _get_playable_cards(self, state: State, player: Player) -> list[Card]:
        return state.get_playable_cards(player)

    def _get_activatable_abilities(self, state: State, player: Player) -> list[Ability]:
        return state.get_activatble_abilities(player)


class CommandStep(BuildStep):

    @property
    def step_type(self) -> StepType:
        return StepType.COMMAND
    
    def run(self, state: State, player: Player, data: SessionData) -> None:
        possible_commands = self._get_possible_commands(state)
        print("\n------COMMAND------")

        while True:
            print("Possible commands: ")
            print(self._commands_to_string(possible_commands))

            raw = input("Command: ").strip()
            cmd = self._resolve_command(raw)
            if cmd is None:
                print(f"Unrecognizable command: '{raw}'")
                continue

            if not self._command_is_legal(cmd):
                continue

            data.command = cmd
            data.action_def = self._create_action_def(data)
            return

    
    def _resolve_command(raw: str) -> str | None:
        raw = raw.strip().lower()
        for canonical, aliases in COMMAND_ALIASES.items():
            if raw in aliases:
                return canonical
        return None
    
    def _commands_to_string(self, commands: set[str]) -> str:
        res = "|"
        for c in commands:
            res += "  "
            res += c
            res += "  |"
        return res

    def _command_is_legal(self, cmd: str) -> bool:
        """Ověří, zda je příkaz proveditelný v aktuálním stavu."""
        match cmd:
            case "play":
                cards = self._get_playable_cards()
                if not cards:
                    print("You have no playable cards.")
                    return False
            case "activate":
                abilities = self._get_activatable_abilities()
                if not abilities:
                    print("You have no activatable abilities.")
                    return False
            case "attack":
                pos_attackers = self._get_possible_attackers()
                if not pos_attackers:
                    print("You have no available attackers.")
                    return False
            case "block":
                pos_attackers = self._get_possible_blockers()
                if not pos_attackers:
                    print("You have no available blockers.")
                    return False
            case "rattack":
                pos_attackers = self._get_attackers()
                if not pos_attackers:
                    print("You have no declared attackers.")
                    return False
            case "rblock":
                pos_attackers = self._get_blockers()
                if not pos_attackers:
                    print("You have no declared blockers.")
                    return False
        return True
    
    def _create_action_def(self, data: SessionData) -> ActionDefinition:
        match data.command:
            case "pass", "attack", "block", "rattack", "rblock":
                return ActionDefinition.without_ability({StepType.COMMAND, StepType.SOURCE})
            case "activate", "play":
                return ActionDefinition.with_ability({StepType.COMMAND, StepType.SOURCE, StepType.TARGET})
    

class SourceStep(BuildStep):

    key_parser = StringParser()

    @property
    def step_type(self) -> StepType:
        return StepType.SOURCE

    def run(self, state: State, player: Player, data: SessionData) -> None:
        if data.command == "pass":
            data.source = player
            return
        
        print("\n------SOURCE------")
        while True:
            keys = input("Source key: ").strip().split()
            if len(keys) != 1:
                print(f"Wrong number of source keys entered: {len(keys)}. Expected number: 1.")
                continue

            key = keys[0]
            obj = state.lookup(key)

            if obj is None:
                print(f"Unrecognized source key: {key}.")
                continue

            ability = self._resolve_to_ability(obj, data.command, state, player)
            if ability is None:
                continue
            
            data.ability = ability
            return
        
    def _resolve_to_ability(self, obj: Any, command: str, state: State, player: Player) -> Ability | None:
        match command:
            case "activate":
                if not isinstance(obj, Ability):
                    print("  Key did not resolve to an activatable ability.")
                    return None
                return obj
            
            case "play":
                if not isinstance(obj, Card):
                    print("  Key did not resolve to a card.")
                    return None
                cast_ability = obj.get_cast_ability()
            
            case "attack" | "rattack" | "block" | "rblock":
                if not isinstance(obj, Card):
                    print("  Key did not resolve to a card.")
                    return None
                combat_ability = obj.get_combat_ability(command)
                if combat_ability is None:
                    print(f"  That card has no ability for '{command}'.")
                    return None
                return combat_ability
            case _:
                print(f"  No source resolution defined for command '{command}'.")
                return None


class SlotFillingStep(BuildStep):
    """
    Base for CostStep and TargetStep.
 
    Iterates the slots defined in a SubAbilityDefinition and asks the player
    to resolve each one via a state key lookup followed by legality validation.
 
    Subclasses provide:
        _get_sub_def()     -- which SubAbilityDefinition to use
        _get_bindings()    -- which dict on SessionData to write into
        _set_bindings()    -- how to store the completed dict back
        _header()          -- display header string
    """
 
    @abstractmethod
    def _get_sub_def(self, data: SessionData) -> SubAbilityDefinition | None:
        pass
 
    @abstractmethod
    def _get_stored_bindings(self, data: SessionData) -> TargetBinding:
        pass
 
    @abstractmethod
    def _set_stored_bindings(self, data: SessionData, binding: TargetBinding) -> None:
        pass
 
    @abstractmethod
    def _header(self) -> str:
        pass
 
    def run(self, state: State, player: Player, data: SessionData) -> bool:
        assert data.ability is not None
        sub_def = self._get_sub_def(data)
 
        if sub_def is None or not sub_def.slots:
            self._set_stored_bindings(data, {})
            return True
 
        print(f"\n------{self._header()}------")
 
        slots: list[tuple[str, TargetSlot]] = list(sub_def.slots.items())
        bindings: dict[str, dict[Any, int]] = {}
 
        slot_index = 0
        while slot_index < len(slots):
            slot_key, slot = slots[slot_index]
 
            result = self._fill_slot(slot_key, slot, state, data.ability, bindings)
 
            if result is None:
                # Player backed out of this slot.
                if slot_index == 0:
                    # First slot -- propagate back to the previous BuildStep.
                    return False
                # Otherwise retreat to the previous slot.
                prev_key = slots[slot_index - 1][0]
                bindings.pop(prev_key, None)
                slot_index -= 1
            else:
                bindings[slot_key] = result
                slot_index += 1
 
        self._set_stored_bindings(data, bindings)
        return True
 
    # -- Single-slot resolution ---------------------------------------------
 
    def _fill_slot(
        self,
        slot_key: str,
        slot: "TargetSlot",
        state: "State",
        ability: "Ability",
        current_bindings: dict[str, dict[Any, int]],
    ) -> "dict[Any, int] | None":
        """
        Prompt the player to enter space-separated keys for one slot.
 
        The player types:  key1 key2 key3 ...
        Duplicate keys are allowed and translate to a higher count
        (e.g. "g1 g1" -> {goblin: 2}).
 
        The assembled {obj: count} dict must exactly match one of the legal
        groups produced by slot.get_bindings().  This delegates all grouping
        logic -- how many targets are required, which combinations are valid --
        entirely to the TargetSelector defined on the slot.
 
        Returns the matched {target: count} dict on success, or None when the
        player enters an empty line (go back).  Repeats on any invalid input.
        """
        legal_bindings = slot.get_bindings(ability.source, state)
        # Each binding is { slot_key: { obj: count } }; extract the inner dicts.
        legal_groups: list[dict[Any, int]] = [b[slot_key] for b in legal_bindings]
 
        #print(f"  Slot '{slot_key}'  ({len(legal_groups)} legal target group(s) available)")
 
        while True:
            raw = input(f"Keys for slot: {slot_key}: ").strip()
 
            # Look up every key; abort the whole line on the first unknown key.
            objects: list[Any] = []
            valid = True
            for k in raw.split():
                obj = state.lookup(k)
                if obj is None:
                    print(f"Unrecognized key: '{k}'")
                    valid = False
                    break
                objects.append(obj)
            if not valid:
                continue
 
            # Build {obj: count} -- duplicates accumulate.
            candidate: dict[Any, int] = {}
            for obj in objects:
                candidate[obj] = candidate.get(obj, 0) + 1
 
            # The candidate must exactly match one of the legal groups.
            if candidate not in legal_groups:
                print(f"That target combination is not legal for slot '{slot_key}'. ")
                continue
 
            # Check distinct_from against already-filled slots in this step.
            if self._violates_distinct_multi(candidate, slot, current_bindings):
                print(f"One or more targets overlap with a slot that '{slot_key}' must be distinct from.")
                continue
 
            return candidate
 
    # -- Validation helpers -------------------------------------------------
 
    def _violates_distinct_multi(self, candidate: dict[Any, int],
        slot: "TargetSlot", current_bindings: dict[str, dict[Any, int]]) -> bool:
        """
        Return True when any object in candidate already appears in a slot
        that this slot must produce distinct targets from.
        """
        for distinct_key in slot.distinct_from:
            already = current_bindings.get(distinct_key, {})
            if set(candidate.keys()) & set(already.keys()):
                return True
        return False   


class CostStep(SlotFillingStep):
    """
    Fill target slots required by the cost sub-ability.
 
    Writes into data.cost_bindings.
    """
 
    def _get_sub_def(self, data: SessionData) -> "SubAbilityDefinition | None":
        assert data.ability is not None
        return data.ability.data.cost_action
 
    def _get_stored_bindings(self, data: SessionData) -> dict[str, dict[Any, int]]:
        return data.cost_bindings
 
    def _set_stored_bindings(
        self, data: SessionData, bindings: dict[str, dict[Any, int]]
    ) -> None:
        data.cost_bindings = bindings
 
    def _header(self) -> str:
        return "COST" 


class TargetStep(SlotFillingStep):
    """
    Fill target slots required by the effect sub-ability.
 
    Writes into data.target_bindings.
    """
 
    def _get_sub_def(self, data: SessionData) -> "SubAbilityDefinition | None":
        assert data.ability is not None
        return data.ability.data.action
 
    def _get_stored_bindings(self, data: SessionData) -> dict[str, dict[Any, int]]:
        return data.target_bindings
 
    def _set_stored_bindings(
        self, data: SessionData, bindings: dict[str, dict[Any, int]]
    ) -> None:
        data.target_bindings = bindings
 
    def _header(self) -> str:
        return "TARGETS"  
    

class ConfirmStep(BuildStep):
    """
    Show an action summary and ask the player to confirm, go back, or reset.
 
    Returns True on confirmation, False to go back one step.
    Resets the full session when the player types 'reset'.
    """
 
    def run(self, state: "State", player: "Player", data: SessionData) -> bool:
        print("\n------CONFIRM------")
        _print_summary(data)
 
        while True:
            raw = input("Confirm? (y / n / reset): ").strip().lower()
            match raw:
                case "y" | "yes":
                    return True
                case "n" | "no":
                    return False  # Go back one step.
                case "reset":
                    _reset_session(data)
                    return True   # Pipeline will restart from COMMAND.
                case _:
                    print("Enter y, n, or reset.")




class ActionBuilderSession:

    def __init__(self, state: State, player: Player) -> None:
        self._state = state
        self._player = player
        self._data = SessionData()
        self._cur_step = StepType.COMMAND
    
    _STEPS: list[tuple[StepType, BuildStep]] = [
        (StepType.COMMAND, CommandStep()),
        (StepType.SOURCE,  SourceStep()),
        (StepType.COST,    CostStep()),
        (StepType.TARGET,  TargetStep()),
        (StepType.CONFIRM, ConfirmStep()),
    ]

    def run(self) -> GameAction:
        idx = 0
        while idx < len(self._STEPS):
            step_type, step = self._STEPS[idx]
 
            if not self._is_needed(step_type):
                idx += 1
                continue
 
            advanced = step.run(self._state, self._player, self._data)
 
            if not advanced:
                if idx == 0:
                    return None  # Cancelled before the first step.
                idx -= 1
                self._clear_from(idx)
            else:
                # ConfirmStep may have issued a full reset.
                if self._data.step == StepType.COMMAND and step_type == StepType.CONFIRM:
                    idx = 0
                else:
                    idx += 1
 
        return ActionFactory.build(self._data, self._state)
    
    def _is_needed(self, step_type: StepType) -> bool:
        """
        Return True when the given step should be executed for the current command.
 
        COST is skipped when the selected ability has no cost_action.
        TARGET is skipped when the selected ability has no action or no slots.
        """
        match step_type:
            case StepType.COST:
                if self._data.ability is None:
                    return False
                return self._data.ability.data.cost_action is not None
            case StepType.TARGET:
                if self._data.ability is None:
                    return False
                a = self._data.ability.data.action
                return a is not None and bool(a.slots)
            case _:
                return True
    
    def _clear_from(self, idx: int) -> None:
        """
        Clear session data for all steps from idx onward so the player
        can make fresh choices when re-entering those steps.
        """
        step_type = self._STEPS[idx][0]
        if step_type <= StepType.COMMAND:
            self._data.command         = None
            self._data.ability         = None
            self._data.cost_bindings   = {}
            self._data.target_bindings = {}
        elif step_type <= StepType.SOURCE:
            self._data.ability         = None
            self._data.cost_bindings   = {}
            self._data.target_bindings = {}
        elif step_type <= StepType.COST:
            self._data.cost_bindings   = {}
            self._data.target_bindings = {}
        elif step_type <= StepType.TARGET:
            self._data.target_bindings = {}
    

class HumandDecisionMaker:
    def get_action(self, state: State, player: Player) -> GameAction:
        """
        Prompt the player until a valid action is returned.
 
        Args:
            state:  Current game state.
            player: Player whose action is being chosen.
 
        Returns:
            A completed GameAction (never None).
        """
        while True:
            session = ActionBuilderSession(state, player)
            action = session.run()
            if action is not None:
                return action


def _print_summary(data: SessionData) -> None:
    """Print a formatted summary of the current session choices."""
    print(f"Command  : {data.command}")
    if data.ability is not None:
        src = data.ability.source
        key = data.ability.data.key
        name = getattr(getattr(src, "card_def", None), "name", repr(src))
        print(f"Source : {name} -- {key}")
    if data.cost_bindings:
        print(f"Cost : {data.cost_bindings}")
    if data.target_bindings:
        print(f"Targets : {data.target_bindings}")


def _reset_session(data: SessionData) -> None:
    """Reset all session data fields back to their initial values."""
    data.step            = StepType.COMMAND
    data.command         = None
    data.ability         = None
    data.cost_bindings   = {}
    data.target_bindings = {}