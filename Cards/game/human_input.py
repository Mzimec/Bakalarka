"""
human_input.py — Console-driven DecisionMaker.

Pipeline:
    HumanDecisionMaker.get_action(state, player)
        → ActionBuilderSession(state, player).run()
            → postupné kroky: command → source → cost-targets → targets → confirm
            → Ability.generate_actions() vrátí kandidáty, hráč vybere index
        → GameAction | PassPriorityAction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from game.game_state import State, Player, Card
    from game.game_state.player import DecisionMaker
    from game.abilities.ability import Ability, AbilityDefinition
    from game.game_actions.game_action import GameAction
    from game.target import TargetBinding


# ---------------------------------------------------------------------------
# Canonical command aliases
# ---------------------------------------------------------------------------

_COMMAND_ALIASES: dict[str, set[str]] = {
    "play":     {"play", "p"},
    "activate": {"activate", "a"},
    "pass":     {"pass", ""},
}


def _resolve_command(raw: str) -> str | None:
    """Převede alias na kanonický název příkazu, nebo None."""
    raw = raw.strip().lower()
    for canonical, aliases in _COMMAND_ALIASES.items():
        if raw in aliases:
            return canonical
    return None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

class _Step(int, Enum):
    COMMAND = auto()
    SOURCE  = auto()
    COST    = auto()
    TARGET  = auto()
    CONFIRM = auto()
    DONE    = auto()


@dataclass
class _SessionData:
    """Mezistav který se plní krok za krokem."""
    command:       str | None            = None
    ability:       "Ability | None"      = None
    cost_binding:  "TargetBinding | None"= None
    target_binding:"TargetBinding | None"= None


# ---------------------------------------------------------------------------
# Helper: výběr z číslovaného seznamu
# ---------------------------------------------------------------------------

def _pick(prompt: str, options: list[Any], display: list[str]) -> Any | None:
    """
    Vypíše očíslovaný seznam a vrátí vybraný prvek.
    Vrátí None pokud uživatel zadá prázdný vstup (= zpět / zrušit krok).
    """
    for i, label in enumerate(display):
        print(f"  [{i}] {label}")
    raw = input(f"{prompt} (číslo, nebo Enter = zpět): ").strip()
    if raw == "":
        return None
    try:
        idx = int(raw)
        if 0 <= idx < len(options):
            return options[idx]
        print(f"  ⚠ Index mimo rozsah 0–{len(options)-1}.")
    except ValueError:
        print(f"  ⚠ Zadejte číslo.")
    return None


def _pick_binding(prompt: str, bindings: list["TargetBinding"]) -> "TargetBinding | None":
    """Vybere TargetBinding ze seznamu kandidátů."""
    labels = [repr(b) for b in bindings]
    return _pick(prompt, bindings, labels)


# ---------------------------------------------------------------------------
# ActionBuilderSession
# ---------------------------------------------------------------------------

class ActionBuilderSession:
    """
    Interaktivní stavový stroj pro sestavení jedné herní akce z konzole.

    Každý krok lze přeskočit (Enter = zpět), takže hráč může kdykoli
    začít znovu bez ukončení smyčky.

    Použití:
        action = ActionBuilderSession(state, player).run()
        # action je GameAction nebo None (hráč odešel zpět až na začátek)
    """

    def __init__(self, state: "State", player: "Player") -> None:
        self._state  = state
        self._player = player
        self._data   = _SessionData()
        self._step   = _Step.COMMAND

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> "GameAction | None":
        """
        Spustí smyčku kroků a vrátí hotový GameAction, nebo None pokud
        hráč celý vstup zrušil (šel zpět přes první krok).
        """
        while self._step != _Step.DONE:
            advanced = self._dispatch()
            if not advanced:
                # Hráč šel zpět — vrátíme se o jeden krok
                prev = self._prev_step()
                if prev is None:
                    # Jsme na začátku, předáváme None zpět
                    return None
                self._step = prev
                self._reset_from(self._step)

        return self._build_action()

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    def _dispatch(self) -> bool:
        """Zavolá správný handler; vrátí True = postup vpřed, False = zpět."""
        match self._step:
            case _Step.COMMAND: return self._step_command()
            case _Step.SOURCE:  return self._step_source()
            case _Step.COST:    return self._step_cost()
            case _Step.TARGET:  return self._step_target()
            case _Step.CONFIRM: return self._step_confirm()
            case _:
                return True

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------

    def _step_command(self) -> bool:
        print("\n── Příkaz ──────────────────────────")
        print("  play (p)  |  activate (a)  |  pass")
        raw = input("Příkaz: ").strip()
        cmd = _resolve_command(raw)
        if cmd is None:
            print(f"  ⚠ Neznámý příkaz: '{raw}'")
            return False  # zůstaneme na stejném kroku (znovu zeptáme)

        if cmd == "pass":
            self._data.command = "pass"
            self._step = _Step.DONE
            return True

        if not self._command_is_legal(cmd):
            return False  # chyba už byla vypsána

        self._data.command = cmd
        self._step = _Step.SOURCE
        return True

    def _step_source(self) -> bool:
        print("\n── Zdroj ───────────────────────────")
        abilities = self._get_available_abilities()
        if not abilities:
            print("  ⚠ Žádné dostupné akce pro tento příkaz.")
            return False

        labels = [self._ability_label(a) for a in abilities]
        chosen = _pick("Vyber zdroj", abilities, labels)
        if chosen is None:
            return False  # zpět

        self._data.ability = chosen
        self._step = _Step.COST if self._needs_cost() else _Step.TARGET
        return True

    def _step_cost(self) -> bool:
        print("\n── Platba (cost targets) ───────────")
        assert self._data.ability is not None

        cost_action = self._data.ability.data.cost_action
        if cost_action is None:
            self._data.cost_binding = {}
            self._step = _Step.TARGET
            return True

        from game.game_actions.game_action import AbilityOperationGenerator
        cost_generators = cost_action.generate_actions(
            self._data.ability.source, self._state
        )
        if not cost_generators:
            print("  ⚠ Nelze zaplatit cenu — žádné legální targety.")
            return False

        bindings = [g.binding for g in cost_generators]
        chosen = _pick_binding("Vyber způsob platby", bindings)
        if chosen is None:
            return False

        self._data.cost_binding = chosen
        self._step = _Step.TARGET
        return True

    def _step_target(self) -> bool:
        print("\n── Targety efektu ──────────────────")
        assert self._data.ability is not None

        action_def = self._data.ability.data.action
        if action_def is None:
            self._data.target_binding = {}
            self._step = _Step.CONFIRM
            return True

        from game.game_actions.game_action import AbilityOperationGenerator
        action_generators = action_def.generate_actions(
            self._data.ability.source, self._state
        )
        if not action_generators:
            print("  ⚠ Žádné legální targety pro efekt.")
            return False

        bindings = [g.binding for g in action_generators]
        chosen = _pick_binding("Vyber targety", bindings)
        if chosen is None:
            return False

        self._data.target_binding = chosen
        self._step = _Step.CONFIRM
        return True

    def _step_confirm(self) -> bool:
        print("\n── Potvrzení ────────────────────────")
        self._print_summary()
        raw = input("Potvrdit? (y/n/reset): ").strip().lower()
        match raw:
            case "y" | "yes":
                self._step = _Step.DONE
                return True
            case "n" | "no":
                return False  # zpět o jeden krok
            case "reset":
                self._step = _Step.COMMAND
                self._data = _SessionData()
                return True  # True = „pokračuj" (ale jsme na COMMAND)
            case _:
                print("  ⚠ Zadejte y, n, nebo reset.")
                return False

    # ------------------------------------------------------------------
    # Build final action
    # ------------------------------------------------------------------

    def _build_action(self) -> "GameAction":
        from game.game_actions.game_action import PassPriorityAction

        if self._data.command == "pass":
            return PassPriorityAction(player=self._player)

        assert self._data.ability is not None
        assert self._data.cost_binding is not None
        assert self._data.target_binding is not None

        ability = self._data.ability
        actions = ability.generate_actions(self._state)

        # Najdeme akci jejíž target_binding odpovídá výběru
        for action in actions:
            _, action_intent = action.get_intents()
            gen = action_intent.generator
            if hasattr(gen, "binding") and gen.binding == self._data.target_binding:
                return action

        # Fallback: první dostupná akce (nemělo by nastat)
        return actions[0]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _command_is_legal(self, cmd: str) -> bool:
        """Ověří, zda je příkaz proveditelný v aktuálním stavu."""
        match cmd:
            case "play":
                cards = self._get_playable_cards()
                if not cards:
                    print("  ⚠ Nemáš žádné zahratelné karty.")
                    return False
            case "activate":
                abilities = self._get_activatable_abilities()
                if not abilities:
                    print("  ⚠ Nemáš žádné aktivovatelné schopnosti.")
                    return False
        return True

    def _get_available_abilities(self) -> list["Ability"]:
        match self._data.command:
            case "play":
                return self._get_playable_cards()
            case "activate":
                return self._get_activatable_abilities()
            case _:
                return []

    def _get_playable_cards(self) -> list["Ability"]:
        """
        Vrátí schopnosti karet v ruce, které lze nyní zahrát.
        Zatím vrací prázdný list — implementace závisí na zbytku enginu.
        """
        # TODO: projít player.hand, pro každou kartu zavolat
        #   card.get_abilities(state) a filtrovat dle zóny / timing
        return []

    def _get_activatable_abilities(self) -> list["Ability"]:
        """
        Vrátí aktivovatelné schopnosti karet na battlefield.
        Zatím vrací prázdný list.
        """
        # TODO: projít player.battlefield.permanents, get_abilities, filtrovat
        return []

    def _needs_cost(self) -> bool:
        if self._data.ability is None:
            return False
        return self._data.ability.data.cost_action is not None

    def _ability_label(self, ability: "Ability") -> str:
        name = getattr(ability.source, "card_def", None)
        card_name = name.name if name else repr(ability.source)
        key = ability.data.key
        return f"{card_name} — {key}"

    def _print_summary(self) -> None:
        d = self._data
        print(f"  Příkaz  : {d.command}")
        ability = d.ability
        if ability:
            print(f"  Zdroj   : {self._ability_label(ability)}")
        print(f"  Platba  : {d.cost_binding}")
        print(f"  Targety : {d.target_binding}")

    def _prev_step(self) -> _Step | None:
        order = [_Step.COMMAND, _Step.SOURCE, _Step.COST, _Step.TARGET, _Step.CONFIRM]
        try:
            idx = order.index(self._step)
        except ValueError:
            return None
        return order[idx - 1] if idx > 0 else None

    def _reset_from(self, step: _Step) -> None:
        """Vyčistí data od daného kroku dál."""
        if step <= _Step.COMMAND:
            self._data = _SessionData()
        elif step <= _Step.SOURCE:
            self._data.ability = None
            self._data.cost_binding = None
            self._data.target_binding = None
        elif step <= _Step.COST:
            self._data.cost_binding = None
            self._data.target_binding = None
        elif step <= _Step.TARGET:
            self._data.target_binding = None


# ---------------------------------------------------------------------------
# HumanDecisionMaker
# ---------------------------------------------------------------------------

class HumanDecisionMaker:
    """
    DecisionMaker řízený konzolí.

    Podpis get_action(state, player) je v souladu s Player.get_action()
    a základní třídou DecisionMaker.
    """

    def get_action(self, state: "State", player: "Player") -> "GameAction":
        while True:
            session = ActionBuilderSession(state, player)
            action = session.run()
            if action is not None:
                return action
            # session.run() vrátilo None = hráč šel zpět přes COMMAND
            # → jednoduše zahájíme novou session