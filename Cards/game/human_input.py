"""!
@file human_input.py
@brief Console-driven action builder for human players.

Provides BuildStep subclasses that walk a player through choosing a command,
source, cost, targets, and confirmation.  HumanDecisionMaker wires these steps
into a looping ActionBuilderSession.
"""



from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .game_loop.game_loop import TurnPhase
    from .target import TargetBinding, TargetSlot
    from .game_actions.data_structs.ability import Ability, SubAbilityDefinition
    from .game_state import State, Player, Card
    from .game_actions import GameAction
    from .game_actions.data_structs.game_action import EffectSequence

from .game_actions.data_structs.game_action import PassPriorityAction, DeclareAttackerAction, DeclareBlockerAction, RemoveAttackerAction, RemoveBlockerAction, AbilityAction, AbilityOperationGenerator
from .enums import *

_COMMAND_ALIASES: dict[str, set[str]] = {
    "play":     {"play", "p"},
    "activate": {"activate", "a"},
    "pass":     {"pass", ""},
    "attack":   {"attack"},
    "rattack":  {"rattack", "ra"},
    "block":    {"block", "b"},
    "rblock":   {"rblock", "rb"},
}

_ALIAS_LOOKUP = {
    alias: canonical
    for canonical, aliases in _COMMAND_ALIASES.items()
    for alias in aliases
}


_POSSIBLE_COMMANDS: dict[TurnPhase, set[str]] = {
    TurnPhase.UPKEEP: {"pass", "play", "activate"},

    TurnPhase.PRECOMBAT_MAIN: {"pass", "play", "activate"},

    TurnPhase.DECLARE_ATTACKERS: {"pass", "attack", "rattack"},
    TurnPhase.AFTER_ATTACKERS: {"pass", "play", "activate"},
    TurnPhase.DECLARE_BLOCKERS: {"pass", "block", "rblock"},
    TurnPhase.AFTER_BLOCKERS: {"pass", "play", "activate"},
    TurnPhase.FIRST_COMBAT_DAMAGE: {"pass", "play", "activate"},
    TurnPhase.SECOND_COMBAT_DAMAGE: {"pass", "play", "activate"},
    TurnPhase.END_COMBAT: {"pass", "play", "activate"},

    TurnPhase.POSTCOMBAT_MAIN: {"pass", "play", "activate"},

    TurnPhase.ENDstep: {"pass", "play", "activate"}
}


def _resolve_command(raw: str) -> str | None:
    """!
    @brief Map a raw console string to a canonical command name.
    @param raw Unprocessed player input.
    @return Canonical command string, or None when no alias matches.
    """
    raw = raw.strip().lower()
    return _ALIAS_LOOKUP.get(raw)



_META_BACK = "back"
_META_RESET = "reset"
_META_OPTIONS = "options"

_META_HINT = f"  (type '{_META_BACK}' to go back  |  '{_META_RESET}' to restart  |  '{_META_OPTIONS}' for help)"


def _is_meta(raw: str) -> bool:
    """!
    @brief Return True when the input is a reserved meta-command.
    @param raw Stripped player input.
    @return True for back / reset / options.
    """
    return raw.lower() in (_META_BACK, _META_RESET, _META_OPTIONS)


@dataclass
class SessionData:
    """!
    @brief Partial action data accumulated across build steps.

    All fields start as None / empty and are populated incrementally as the
    player advances through the ActionBuilderSession pipeline.  cost_bindings
    and target_bindings are keyed by slot key so that cross-slot distinct
    constraints can be evaluated before merging.

    @var command        Canonical command string chosen in CommandStep.
    @var source         Card or Player resolved in SourceStep.
    @var ability        Runtime Ability resolved in SourceStep (None for
                        combat and pass commands).
    @var cost_effect_seq  EffectSequence selected in CostModeStep.
    @var effect_seq       EffectSequence selected in EffectModeStep.
    @var cost_bindings  Target bindings filled for the cost sub-ability.
    @var target_bindings Target bindings filled for the effect sub-ability.
    """

    command: str | None = None

    source: Any | None = None
    ability: Ability | None = None

    cost_effect_seq: EffectSequence | None = None
    effect_seq: EffectSequence | None = None

    cost_bindings: TargetBinding = field(default_factory=dict)
    target_bindings: TargetBinding = field(default_factory=dict)


class ActionFactory:
    """!
    @brief Assembles a final GameAction from completed SessionData.
    """

    @staticmethod
    def build(data: SessionData) -> GameAction:
        """!
        @brief Build the GameAction described by the completed session data.

        Constructs cost and effect OperationGenerators from the stored bindings
        and wraps them in the appropriate GameAction subclass.

        @param data Completed session data.
        @return GameAction ready to be processed by ActionProcessor.
        """
        

        match data.command:
            case "pass":
                return PassPriorityAction(player=data.source)
            case "attack":
                return DeclareAttackerAction(attacker=data.source)
            case "block":
                return DeclareBlockerAction(blocker=data.source)
            case "rattack":
                return RemoveAttackerAction(attacker=data.source)
            case "rblock":
                return RemoveBlockerAction(blocker=data.source)

        assert data.ability is not None
        
        cost_generator = (AbilityOperationGenerator(tuple(), data.cost_bindings)
            if data.cost_effect_seq is None else AbilityOperationGenerator(data.cost_effect_seq, data.cost_bindings))
        
        generator = (AbilityOperationGenerator(tuple(), data.target_bindings)
            if data.effect_seq is None else AbilityOperationGenerator(data.effect_seq, data.target_bindings))


        action = AbilityAction(
            action_key=data.ability.data.key, 
            source=data.ability.source, 
            cost_generator=cost_generator,
            action_generator=generator,
            uses_stack=data.ability.data.uses_stack
        )
        
        return action
        


class BuildStep(ABC):
    """!
    @brief One interactive step in the action-building pipeline.

    run() mutates SessionData in place and returns either True (advance),
    False (go back one step), or a _MetaResult for session-wide signals.
    """

    @abstractmethod
    def run(self, state: State, player: Player, data: SessionData) -> bool | _MetaResult:
        """!
        @brief Execute this step.
        @param state  Current game state.
        @param player Player whose action is being built.
        @param data   Mutable session data shared across all steps.
        @return True to advance, False to go back, or a _MetaResult.
        """
        pass

    def _get_playable_cards(self, state: State, player: Player) -> list[Card]:
        """!
        @brief Return cards the player may currently play.
        @param state  Current game state.
        @param player Acting player.
        @return Playable cards.
        """
        return state.get_playable_cards(player)

    def _get_activatable_abilities(self, state: State, player: Player) -> list[Ability]:
        """!
        @brief Return activated abilities available to the player.
        @param state  Current game state.
        @param player Acting player.
        @return Activatable abilities.
        """
        return state.get_activatable_abilities(player)

    def _get_possible_attackers(self, state: State, player: Player) -> list[Card]:
        """!
        @brief Return creatures that may be declared as attackers.
        @param state  Current game state.
        @param player Attacking player.
        @return Possible attackers.
        """
        return state.get_possible_attackers(player)

    def _get_possible_blockers(self, state: State, player: Player) -> list[Card]:
        """!
        @brief Return creatures that may be declared as blockers.
        @param state  Current game state.
        @param player Blocking player.
        @return Possible blockers.
        """
        return state.get_possible_blockers(player)

    def _get_attackers(self, state: State, player: Player) -> list[Card]:
        """!
        @brief Return creatures already declared as attackers.
        @param state  Current game state.
        @param player Attacking player.
        @return Declared attackers.
        """
        return state.get_attackers(player)

    def _get_blockers(self, state: State, player: Player) -> list[Card]:
        """!
        @brief Return creatures already declared as blockers.
        @param state  Current game state.
        @param player Blocking player.
        @return Declared blockers.
        """
        return state.get_blockers(player)

    def _read_line(self, prompt: str, options_fn: Any | None = None) -> str | _MetaResult:
        """!
        @brief Read a line from the console and handle meta-commands inline.

        Intercepts back / reset / options before returning the raw input to
        the caller.  When options_fn is provided it is called with no arguments
        and its output is printed when the player types 'options'.

        @param prompt     Prompt string shown to the player.
        @param options_fn Zero-argument callable that prints contextual help.

        @note  The 'options' meta-command is handled internally; it never appears
        in the return value.

        @return Stripped player input or a _MetaResult for back / reset.
        """
        print(_META_HINT)
        while True:
            raw = input(prompt).strip()
            lower = raw.lower()

            if lower == _META_BACK:
                return _MetaResult.BACK
            if lower == _META_RESET:
                return _MetaResult.RESET
            if lower == _META_OPTIONS:
                if options_fn is not None:
                    options_fn()
                else:
                    print("  No options available at this step.")
                continue

            return raw


class CommandStep(BuildStep):
    """!
    @brief Prompt the player for a top-level command.

    On success writes data.command with the canonical command string.
    """

    def run(self, state: State, player: Player, data: SessionData) -> bool | _MetaResult:
        """!
        @brief Ask for a command until a legal one is entered.
        @param state  Current game state.
        @param player Acting player.
        @param data   Session data to update.
        @return True on success, _MetaResult.BACK / RESET on meta-input.
        """
        possible = self._possible_commands(state)

        if possible is None:
            raise RuntimeError("TurnPhase is not known to _POSSIBLE_COMMANDS!")
        
        print("\n------COMMAND------")

        def _options() -> None:
            print(f"  Available commands: {' | '.join(sorted(possible))}")

        while True:
            result = self._read_line("Command: ", _options)

            if isinstance(result, _MetaResult):
                return result

            cmd = _resolve_command(result)
            if cmd is None:
                print(f"  Unrecognizable command: '{result}'")
                continue

            if cmd not in possible:
                print(f"  '{cmd}' is not available in this phase.")
                continue

            if not self._command_is_legal(cmd, state, player):
                continue

            data.command = cmd
            return True

    def _possible_commands(self, state: State) -> set[str] | None:
        """!
        @brief Return commands available in the current turn phase.
        @param state Current game state.
        @return Set of canonical command strings.
        """
        phase: TurnPhase = state.get_phase()
        return _POSSIBLE_COMMANDS.get(phase)

    def _command_is_legal(self, cmd: str, state: State, player: Player) -> bool:
        """!
        @brief Return True when the command has at least one legal source.
        @param cmd    Canonical command string.
        @param state  Current game state.
        @param player Acting player.

        @note  Prints a diagnostic message to stdout when the command is not legal.

        @return True if the command can currently be carried out.
        """
        match cmd:
            case "play":
                if not self._get_playable_cards(state, player):
                    print("  You have no playable cards.")
                    return False
            case "activate":
                if not self._get_activatable_abilities(state, player):
                    print("  You have no activatable abilities.")
                    return False
            case "attack":
                if not self._get_possible_attackers(state, player):
                    print("  You have no available attackers.")
                    return False
            case "rattack":
                if not self._get_attackers(state, player):
                    print("  You have no declared attackers.")
                    return False
            case "block":
                if not self._get_possible_blockers(state, player):
                    print("  You have no available blockers.")
                    return False
            case "rblock":
                if not self._get_blockers(state, player):
                    print("  You have no declared blockers.")
                    return False
        return True


class SourceStep(BuildStep):
    """!
    @brief Resolve the source of the chosen command via a state key lookup.

    For 'pass'             -- source is the acting player; no input needed.
    For 'activate'         -- key must resolve to an Ability via state.lookup().
    For 'play'             -- key must resolve to a Card; cast ability is fetched
                              from the card itself via Card.get_cast_ability().
    For combat commands    -- key must resolve to a Card; the matching combat
                              ability is fetched via Card.get_combat_ability().

    Setting source and ability (where needed) field to the SessionData.

    On success writes data.source and potentionaly data.ability.
    """

    def run(self, state: State, player: Player, data: SessionData) -> bool | _MetaResult:
        """!
        @brief Ask for a source key and resolve it to an Ability.
        @param state  Current game state.
        @param player Acting player.
        @param data   Session data to update.
        @return True on success, _MetaResult on meta-input.
        """

        # Logic for pass command 
        if data.command == "pass":
            data.source = player
            return True

        print("\n------SOURCE------")

        def _options() -> None:
            self._print_options(data.command, state, player)

        while True:
            result = self._read_line("Source key: ", _options)

            if isinstance(result, _MetaResult):
                return result

            lookup = state.lookup(result)
            if not lookup.success:
                print(lookup.error)
                continue

            data.source = lookup.final

            if data.command in {"attack", "block", "rattack", "rblock"}:
                return True

            ability = self._resolve_to_ability(data.source, data.command)
            if ability is None:
                continue 

            data.ability = ability
            return True

    def _resolve_to_ability(self, obj: Any, command: str) -> Ability | None:
        """!
        @brief Convert a looked-up object to the Ability appropriate for the command.
        @param obj     Object returned by state.lookup().
        @param command Canonical command string.
        @return Resolved Ability, or None when the object type does not match.
        """
        from .game_state.card import Card

        match command:
            case "activate":
                if not isinstance(obj, Ability):
                    print("  Key did not resolve to an activatable ability.")
                    return None
                if not obj.is_activatable():
                    print("   Selected Ability cannot be activated from this zone.")
                    return None
                return obj
                

            case "play":
                if not isinstance(obj, Card):
                    print("  Key did not resolve to a card.")
                    return None
                ability = obj.get_cast_ability()
                if ability is None:
                    print("  That card has no play/cast ability defined.")
                return ability

            case _:
                print(f"  No source resolution defined for command '{command}'.")
                return None

    def _print_options(self, command: str | None, state: State, player: Player) -> None:
        """!
        @brief Print the objects currently available as sources for the command.
        @param command Canonical command string.
        @param state   Current game state.
        @param player  Acting player.
        """
        match command:
            case "play":
                cards = self._get_playable_cards(state, player)
                print("  Playable cards:")
                for c in cards:
                    print(f"    {c.key}  {c.card_def.name}")
            case "activate":
                abilities = self._get_activatable_abilities(state, player)
                print("  Activatable abilities:")
                for a in abilities:
                    print(f"    {a.source.key}  {a.source.card_def.name} -- {a.data.key}")
            case "attack":
                cards = self._get_possible_attackers(state, player)
                print("  Possible attackers:")
                for c in cards:
                    print(f"    {c.key}  {c.card_def.name}")
            case _:
                print("  No options available for this command.")


class ModeStep(BuildStep):

    @abstractmethod
    def _get_sub_def(self, data: SessionData) -> SubAbilityDefinition | None:
        pass

    @abstractmethod
    def _set_stored_mode(self, data: SessionData, mode: EffectSequence) -> None:
        pass

    @abstractmethod
    def _header(self) -> str:
        pass

    def run(self, state: State, player: Player, data: SessionData) -> bool | _MetaResult:
        assert data.ability is not None
        sub_def = self._get_sub_def(data)

        if sub_def is None:
            return True
        
        possible = sub_def.get_effect_sequences()

        def _options() -> None:
            for i, p in enumerate(possible):
                effects = p.get_used_effects()
                print(f"\n  Mode {i}")
                for e in effects:
                    print(f"    {e.get_info()}")

        print(f"\n------{self._header()}------")
        while True:
            raw = self._read_line(f"  Enter number of chosen mode from 0 to {len(possible) - 1}: ", _options)

            if isinstance(raw, _MetaResult):
                return raw
        
            try:
                idx = int(raw)
            except:
                print(f"  Entered query '{raw}' was not number.")
                continue

            if 0 > idx or idx >= len(possible):
                print(f"  Entered query '{idx}' was out of bounds 0 - {len(possible)}.")
                continue
            
            mode = possible[idx]
            self._set_stored_mode(data, mode)
            return True
            

class CostModeStep(ModeStep):

    def _get_sub_def(self, data) -> SubAbilityDefinition | None:
        assert data.ability is not None
        return data.ability.data.cost_action
    
    def _set_stored_mode(self, data, mode) -> None:
        data.cost_effect_seq = mode
    
    def _header(self) -> str:
        return "COST MODE"
    

class EffectModeStep(ModeStep):

    def _get_sub_def(self, data) -> SubAbilityDefinition | None:
        assert data.ability is not None
        return data.ability.data.action
    
    def _set_stored_mode(self, data, mode) -> None:
        data.effect_seq = mode
    
    def _header(self) -> str:
        return "MODE"



class SlotFillingStep(BuildStep):
    """!
    @brief Base for CostStep and TargetStep.

    Iterates the slots of a SubAbilityDefinition in insertion order.  For each
    slot the player enters space-separated state keys that are looked up and
    validated against the legal target groups produced by the slot.

    Subclasses supply:
        _get_sub_def()          -- SubAbilityDefinition to iterate.
        _get_stored_bindings()  -- current dict on SessionData.
        _set_stored_bindings()  -- write the completed dict back.
        _header()               -- section header string.
    """

    @abstractmethod
    def _get_sub_def(self, data: SessionData) -> SubAbilityDefinition | None:
        """!
        @brief Return the SubAbilityDefinition whose slots should be filled.
        @param data Current session data.
        @return SubAbilityDefinition or None when this step should be skipped.
        """
        pass

    @abstractmethod
    def _set_stored_bindings(self, data: SessionData, bindings: TargetBinding) -> None:
        """!
        @brief Write a completed binding dict back to session data.
        @param data     Current session data.
        @param bindings Completed slot bindings to store.
        """
        pass

    @abstractmethod
    def _header(self) -> str:
        """!
        @brief Return the section header displayed at the top of this step.
        @return Header string.
        """
        pass

    @abstractmethod
    def _get_effect_seq(self, data: SessionData) -> EffectSequence | None:
        pass

    def run(self, state: State, player: Player, data: SessionData) -> bool | _MetaResult:
        """!
        @brief Fill every slot of the sub-ability definition interactively.
        @param state  Current game state.
        @param player Acting player.
        @param data   Session data to update.
        @return True when all slots are filled, False / _MetaResult otherwise.
        """
        assert data.ability is not None
        assert self._get_effect_seq(data) is not None

        print(f"\n------{self._header()}------")

        slots = list(self._get_effect_seq(data).get_used_slots())
        bindings: TargetBinding = {}


        slot_index = 0
        while slot_index < len(slots):
            slot = slots[slot_index]
            bindings.pop(slot.key, None)

            result = self._fill_slot(slot, state, data.ability, bindings)

            if isinstance(result, _MetaResult):
                return result  # Propagate back / reset to the session.

            if result is None:
                if slot_index == 0:
                    continue
                else:
                    slot_index -= 1
            else:
                bindings[slot.key] = result
                slot_index += 1

        self._set_stored_bindings(data, bindings)
        return True


    def _fill_slot(self, slot: TargetSlot, state: State, ability: Ability, current_bindings: TargetBinding) -> dict[Any, int] | None | _MetaResult:
        """!
        @brief Prompt the player for space-separated target keys for one slot.

        The player types one or more keys on a single line.  Duplicate keys
        are permitted and raise the corresponding target count (e.g. 'g1 g1'
        yields {goblin: 2}).  The assembled dict must exactly match one of the
        legal groups returned by slot.get_bindings(), delegating all grouping
        logic to the TargetSelector defined on the slot.

        @param slot            TargetSlot that defines legality.
        @param state           Current game state.
        @param ability         Ability whose source is used for candidate lookup.
        @param current_bindings Slots already filled in this step.
        @return Resolved target dict, None to go back one slot, or _MetaResult.
        """
        legal_bindings = slot.get_bindings(ability.source, state)
        legal_groups: list[dict[Any, int]] = [b[slot.key] for b in legal_bindings]

        def _options() -> None:
            legal_objs: set[Any] = set()
            for group in legal_groups:
                legal_objs.update(group.keys())
            print(f"  Legal targets for slot '{slot.key}':")
            for obj in legal_objs:
                key_attr = getattr(obj, "key", None)
                label = key_attr if key_attr else repr(obj)
                print(f"    {label}  {getattr(getattr(obj, 'card_def', None), 'name', '')}")
            print(f"  ({len(legal_groups)} valid group(s))")

        while True:
            result = self._read_line(f"Keys for slot '{slot.key}': ", _options)

            if isinstance(result, _MetaResult):
                return result

            if result == "":
                return None  # Go back one slot.

            # Look up every key; abort on the first unknown key.
            objects: list[Any] = []
            valid = True
            for k in result.split():
                lookup = state.lookup(k)
                if not lookup.success:
                    print(lookup.error)
                    valid = False
                    break
                objects.append(lookup.final)
            if not valid:
                continue

            # Build {obj: count}; duplicates accumulate.
            candidate: dict[Any, int] = {}
            for obj in objects:
                candidate[obj] = candidate.get(obj, 0) + 1

            if candidate not in legal_groups:
                print(f"  That target combination is not legal for slot '{slot.key}'. ")
                continue

            if self._violates_distinct_multi(candidate, slot, current_bindings):
                print(
                    f"  One or more targets overlap with a slot that "
                    f"'{slot.key}' must be distinct from."
                )
                continue

            return candidate


    def _violates_distinct_multi(self, candidate: dict[Any, int], slot: TargetSlot, current_bindings: TargetBinding) -> bool:
        """!
        @brief Return True when candidate shares an object with a distinct-from slot.
        @param candidate       Target dict being validated.
        @param slot            Slot whose distinct_from constraints apply.
        @param current_bindings Slots already filled in this step.
        @return True when a distinct constraint is violated.
        """
        for distinct_key in slot.distinct_from:
            already = current_bindings.get(distinct_key, {})
            if set(candidate.keys()) & set(already.keys()):
                return True
        return False


class CostStep(SlotFillingStep):
    """!
    @brief Fill target slots required by the cost sub-ability.

    Writes results into data.cost_bindings.
    """

    def _get_sub_def(self, data: SessionData) -> SubAbilityDefinition | None:
        """!
        @brief Return the cost SubAbilityDefinition for the selected ability.
        @param data Current session data.
        @return Cost sub-ability definition, or None when there is no cost.
        """
        assert data.ability is not None
        return data.ability.data.cost_action

    def _set_stored_bindings(self, data: SessionData, bindings: TargetBinding) -> None:
        """!
        @brief Write completed cost bindings back to session data.
        @param data     Current session data.
        @param bindings Completed cost slot bindings.
        """
        data.cost_bindings = bindings

    def _header(self) -> str:
        """!
        @brief Return the section header for the cost step.
        @return Header string.
        """
        return "COST"
    
    def _get_effect_seq(self, data: SessionData) -> EffectSequence | None:
        return data.cost_effect_seq


class TargetStep(SlotFillingStep):
    """!
    @brief Fill target slots required by the effect sub-ability.

    Writes results into data.target_bindings.
    """

    def _get_sub_def(self, data: SessionData) -> SubAbilityDefinition | None:
        """!
        @brief Return the action SubAbilityDefinition for the selected ability.
        @param data Current session data.
        @return Action sub-ability definition, or None when there is no action.
        """
        assert data.ability is not None
        return data.ability.data.action

    def _set_stored_bindings(self, data: SessionData, bindings: TargetBinding) -> None:
        """!
        @brief Write completed target bindings back to session data.
        @param data     Current session data.
        @param bindings Completed target slot bindings.
        """
        data.target_bindings = bindings

    def _header(self) -> str:
        """!
        @brief Return the section header for the target step.
        @return Header string.
        """
        return "TARGETS"
    
    def _get_effect_seq(self, data: SessionData) -> EffectSequence | None:
        return data.effect_seq


class ConfirmStep(BuildStep):
    """!
    @brief Show a summary and ask the player to confirm, go back, or reset.

    Returns True on confirmation.
    A 'reset' input or decline clears the session and returns _MetaResult.RESET so
    ActionBuilderSession can restart from COMMAND.
    """

    def run(self, state: State, player: Player, data: SessionData,) -> bool | _MetaResult:
        """!
        @brief Display a summary and wait for player confirmation.
        @param state  Current game state.
        @param player Acting player.
        @param data   Session data to display.
        @return True to confirm, False to go back, _MetaResult.RESET to restart.
        """
        print("\n------CONFIRM------")
        _print_summary(data)
        print(_META_HINT)

        def _options() -> None:
            print("  Enter 'y' ot 'yes' to confirm.")
            print("  Enter 'n', 'no' or 'reset to start session from start.")
            print("  Enter 'back' to return to last build step.")

        while True:
            result = self._read_line("Confirm? (y / n): ", _options)
            if isinstance(result, _MetaResult):
                return result
            match result:
                case "y" | "yes":
                    return True
                case "n" | "no":
                    return _MetaResult.RESET
                case _:
                    print(f"Unrecognized input: {result}")


class ActionBuilderSession:
    """!
    @brief Drives the ordered pipeline of BuildSteps for a single action.

    Steps run in order: COMMAND -> SOURCE -> COST -> TARGET -> CONFIRM.
    At every prompt the player may type:
        back    -- retreat to the previous step.
        reset   -- restart the entire session from COMMAND.
        options -- display contextual help for the current step.

    run() returns a GameAction on completion or None when the player cancels
    before the first step.
    """

    _STEPS: list[tuple[StepType, BuildStep]] = [
        (StepType.COMMAND, CommandStep()),
        (StepType.SOURCE, SourceStep()),
        (StepType.COST_MODE, CostModeStep()),
        (StepType.COST, CostStep()),
        (StepType.MODE, EffectModeStep()),
        (StepType.TARGET, TargetStep()),
        (StepType.CONFIRM, ConfirmStep()),
    ]

    def __init__(self, state: State, player: Player) -> None:
        """!
        @brief Create a new session for the given state and player.
        @param state  Current game state.
        @param player Player whose action is being built.
        """
        self._state  = state
        self._player = player
        self._data   = SessionData()

    def run(self) -> GameAction | None:
        """!
        @brief Execute the step pipeline until completion or full cancellation.
        @return Completed GameAction, or None when the player cancels entirely.
        """
        idx = 0
        while idx < len(self._STEPS):
            step_type, step = self._STEPS[idx]

            if not self._is_needed(step_type):
                idx += 1
                continue

            result = step.run(self._state, self._player, self._data)

            if result is _MetaResult.RESET:
                _reset_session(self._data)
                idx = 0
                continue

            if result is _MetaResult.BACK or result is False:
                if idx == 0:
                    return None  # Cancelled before the first step.
                idx -= 1
                self._clear_from(idx)
                continue

            idx += 1

        return ActionFactory.build(self._data)


    def _is_needed(self, step_type: StepType) -> bool:
        """!
        @brief Return True when the given step must run for the current command.

        COST_MODE and COST are skipped when the selected ability has no cost_action.
        MODE and TARGET are skipped when the selected ability has no action.

        @param step_type Step to evaluate.
        @return True when the step should be executed.
        """

        def _is_cost_needed() -> bool:
            if self._data.ability is None:
                return False
            return self._data.ability.data.cost_action is not None
        
        def _is_action_needed() -> bool:
            if self._data.ability is None:
                return False
            return self._data.ability.data.action is not None
        
        match step_type:
            case StepType.COST | StepType.COST_MODE:
                return _is_cost_needed()
            case StepType.TARGET | StepType.MODE:
                return _is_action_needed()
            case _:
                return True


    def _clear_from(self, idx: int) -> None:
        """!
        @brief Clear session data for all steps from idx onward.

        Ensures that when the player re-enters a step they start with a clean
        slate rather than seeing stale choices from a previous attempt.

        @param idx Index into _STEPS from which to clear data.
        """

        step_type = self._STEPS[idx][0]

        if step_type <= StepType.TARGET:
            self._data.target_bindings = {}
        if step_type <= StepType.MODE:
            self._data.effect_seq = None
        if step_type <= StepType.COST:
            self._data.cost_bindings = {}
        if step_type <= StepType.COST_MODE:
            self._data.cost_effect_seq = None
        if step_type <= StepType.SOURCE:
            self._data.source = None
            self._data.ability = None
        if step_type <= StepType.COMMAND:
            self._data.command = None

class HumanDecisionMaker:
    """!
    @brief DecisionMaker implementation driven entirely by console input.

    Starts a new ActionBuilderSession on each call and retries automatically
    when the player cancels entirely by typing 'back' past the first step.
    """

    def get_action(self, state: State, player: Player) -> GameAction:
        """!
        @brief Prompt the player until a valid action is returned.
        @param state  Current game state.
        @param player Player whose action is being chosen.
        @return A completed GameAction (never None).
        """
        while True:
            session = ActionBuilderSession(state, player)
            action = session.run()
            if action is not None:
                return action
            # Session returned None -- player cancelled. Start fresh.


def _print_summary(data: SessionData) -> None:
    """!
    @brief Print a formatted summary of the current session choices.
    @param data Session data to display.
    """
    print(f"  Command  : {data.command}")
    if data.ability is not None:
        src  = data.ability.source
        key  = data.ability.data.key
        name = getattr(getattr(src, "card_def", None), "name", repr(src))
        print(f"  Source   : {name} -- {key}")
    if data.cost_effect_seq:
        print(f"  Cost Effect Sequnce   : {data.cost_effect_seq}")
    if data.cost_bindings:
        print(f"  Cost     : {data.cost_bindings}")
    if data.effect_seq:
        print(f"  Effect Sequnce   : {data.effect_seq}")
    if data.target_bindings:
        print(f"  Targets  : {data.target_bindings}")
    


def _reset_session(data: SessionData) -> None:
    """!
    @brief Reset all session data fields to their initial values.
    @param data Session data to clear.
    """
    data.command = None
    data.ability = None
    data.cost_bindings = {}
    data.target_bindings = {}
    data.cost_effect_seq = None
    data.effect_seq = None
    data.source = None