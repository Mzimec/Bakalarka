"""Playable game composed from static card abilities and the real engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from game.console.console_commands import (
    CommandError,
    card_reference,
    parse_command,
    resolve_card,
    resolve_player,
)

from game.enums import CardType, CounterType, TurnPhase, ZoneType
from game.game_actions import ConcedeAction, PassPriorityAction
from game.game_actions.data_structs.game_action import GameAction
from game.game_actions.generation.command_action_builder import chosen_action, ability_actions
from game.game_actions.resolution.action_processor import ActionProcessor
from game.game_actions.resolution.event_bus import EventBus
from game.game_actions.resolution.operation_executor import OperationExecutor
from game.game_actions.resolution.resolution_engine import ResolutionEngine
from game.game_loop.game_loop import GameLoop
from game.game_state import Card, Player, State
from game.game_state.player import DecisionMaker
from helper.query_system.query import EqQuery
from game.game_state.registers.card_register import IK_ZONE, IK_CONTROLLER
from game.cards.demo_cards import (
    LIGHTNING_BOLT,
    EMBER_ADEPT,
    RECALL_TAPPED,
    TAPPED_CREATURES,
    RECALL_EFFECT,
)


def _resolve_target(reference, state, player):
    try:
        return resolve_player(reference, state, player)
    except CommandError:
        return resolve_card(reference, player, global_scope=True)


def build_action(state: State, player: Player, raw: str) -> GameAction:
    """!
    @brief Choose a definition and bind supplied targets; card rules live in definitions.
    """
    command = parse_command(raw)
    args = command.arguments
    if state.is_game_over:
        raise CommandError("The game has ended.")
    if (
        state.priority.current_player is not player
        or state.turn.phase == TurnPhase.UNTAP
        or (state.turn.phase == TurnPhase.CLEANUP and not state.cleanup_priority)
    ):
        raise CommandError("You do not have priority now.")
    if command.name in {"pass", "concede"}:
        if args:
            raise CommandError(f"Usage: {command.name}")
        return PassPriorityAction(player) if command.name == "pass" else ConcedeAction(player)
    if command.name not in {"play", "activate"}:
        raise CommandError(f"Unknown command '{command.name}'. Type help for commands.")
    if not args:
        raise CommandError(f"Usage: {command.name} <card> [ability] [targets...]")
    is_spell = command.name == "play"
    card = resolve_card(
        args[0],
        player,
        ZoneType.HAND if is_spell else ZoneType.BATTLEFIELD,
        controlled=not is_spell,
    )
    if is_spell and CardType.LAND in card.get_types(state):
        from game.rules.lands import LandPlayAction, land_play_error

        if len(args) != 1:
            raise CommandError("Usage: play <land>")
        error = land_play_error(card, player, state)
        if error:
            raise CommandError(error)
        return LandPlayAction(card, player)
    definitions = {
        key.casefold(): definition
        for key, definition in card.get_ability_defs(state).items()
        if definition.is_spell == is_spell and definition.is_usable_in_zone(card.get_zone())
    }
    remaining = args[1:]
    if remaining and (
        remaining[0].casefold() in definitions or (not is_spell and len(remaining) > 1)
    ):
        key = remaining[0].casefold()
        if key not in definitions:
            raise CommandError(f"Ability '{key}' is not available on {card_reference(card)}.")
        definition = definitions[key]
        remaining = remaining[1:]
    else:
        if len(definitions) != 1:
            raise CommandError("Specify an available ability key; use inspect <card>.")
        definition = next(iter(definitions.values()))
    mode = None
    x_value = 0
    target_args = []
    for ref in remaining:
        if ref.startswith("mode="):
            try:
                mode = int(ref[5:])
            except ValueError:
                raise CommandError("Mode must be a number, e.g. mode=2.")
        elif ref.lower().startswith("x="):
            try:
                x_value = int(ref[2:])
                if x_value < 0:
                    raise ValueError()
            except ValueError:
                raise CommandError("X must be a nonnegative integer, e.g. x=2.")
        else:
            target_args.append(ref)
    targets = tuple(
        tuple(_resolve_target(item, state, player) for item in ref.split(",")) if ref != "-" else ()
        for ref in target_args
    )
    try:
        return chosen_action(
            definition.to_ability(card, player), state, targets, mode=mode, x_value=x_value
        )
    except ValueError as error:
        raise CommandError(str(error)) from error


@dataclass(frozen=True)
class ActionOption:
    label: str
    action: GameAction


def available_actions(state: State, player: Player) -> list[ActionOption]:
    """!
    @brief Optional AI enumeration through the same definition-based pipeline.
    """
    if (
        any(p.health <= 0 for p in state.players)
        or state.turn.phase in {TurnPhase.UNTAP, TurnPhase.CLEANUP}
        or state.priority.current_player is not player
    ):
        return []
    sources = [(card, True) for card in player.hand.values()]
    sources.extend(
        (card, False)
        for card in state.query_cards(
            EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_CONTROLLER, player)
        )
    )
    options = []
    for card, is_spell in sources:
        if is_spell and CardType.LAND in card.get_types(state):
            from game.rules.lands import LandPlayAction, land_play_error

            if land_play_error(card, player, state) is None:
                options.append(ActionOption(f"Play land {card}", LandPlayAction(card, player)))
            continue
        for definition in card.get_ability_defs(state).values():
            if definition.is_spell != is_spell:
                continue
            try:
                for action in ability_actions(definition.to_ability(card, player), state):
                    verb = (
                        ("Summon" if CardType.CREATURE in card.get_types(state) else "Cast")
                        if is_spell
                        else "Activate"
                    )
                    targets = [
                        str(target)
                        for inner in action.action_generator.binding.values()
                        for group in inner.values()
                        for target in group
                    ]
                    label = f"{verb} {card}: {definition.key}"
                    if targets:
                        label += " -> " + ", ".join(targets)
                    options.append(ActionOption(label, action))
            except ValueError:
                continue
    return options


def permanent_details(card, state):
    details = ["tapped" if card.is_tapped else "ready"]
    if CardType.CREATURE in card.get_types(state):
        details.append(f"{card.get_power(state)}/{card.get_toughness(state)}")
    if CardType.PLANESWALKER in card.get_types(state):
        details.append(f"loyalty {card.state.counters.get(CounterType.LOYALTY, 0)}")
    if card.is_token:
        details.append("token")
    if card.definition.legendary:
        details.append("legendary")
    if card.attached_to is not None:
        details.append(f"attached to {card_reference(card.attached_to)}")
    return ", ".join(details)


def format_combat(state: State) -> list[str]:
    """!
    @brief Show current attack/block assignments at every combat priority prompt.
    @return Display lines; formatting does not change combat or advance priority.
    """
    combat = getattr(state, "combat", None)
    if combat is None or not combat.active:
        return []
    lines = ["Combat:"]
    if not combat.attackers:
        return lines + ["  No current attackers."]

    def describe(card):
        return (
            f"{card_reference(card)} ({card.name}, "
            f"{card.get_power(state)}/{card.get_toughness(state)}, "
            f"damage {card.state.damage_marked})"
        )

    for attacker, defender in combat.attackers.items():
        blockers = [card for card, target in combat.blockers.items() if target is attacker]
        lines.append(f"  {describe(attacker)} attacks {defender.name}")
        if blockers:
            lines.extend(f"    Blocked by: {describe(blocker)}" for blocker in blockers)
        elif attacker in combat.blocked:
            lines.append("    Blocked; no blockers remain in combat.")
        elif combat.defending_player(defender, declared=True) not in combat._blockers_declared:
            lines.append("    Blockers not declared yet.")
        else:
            lines.append("    Unblocked.")
    return lines


def describe_event_value(value):
    """! @brief Render event payloads using readable card names and command IDs."""
    if isinstance(value, Card):
        return f"{card_reference(value)} ({value.name})"
    if isinstance(value, Player):
        return value.name
    if hasattr(value, "items"):
        return "; ".join(f"{key}: {describe_event_value(item)}" for key, item in value.items() if key != "object_revisions")
    if isinstance(value, (tuple, list, set, frozenset)):
        return ", ".join(describe_event_value(item) for item in value) or "none"
    if hasattr(value, "key"):
        return str(value.key)
    if hasattr(value, "name"):
        return str(value.name)
    if value is None or isinstance(value, (str, int, float, bool)):
        return str(value)
    return type(value).__name__


def format_state(state: State) -> str:
    lines = [f"Turn {state.turn.number} | {state.active_player.name} | {state.turn.phase.name}"]
    for player in state.players:
        board = (
            ", ".join(
                f"{card_reference(card)}: {card} ({permanent_details(card, state)})"
                for card in state.query_cards(
                    EqQuery(IK_ZONE, ZoneType.BATTLEFIELD) & EqQuery(IK_CONTROLLER, player)
                )
            )
            or "empty"
        )
        lines.append(
            f"{player.name}: {player.health} hp | hand {len(player.hand)} | "
            f"deck {len(player.deck)} | poison {player.poison_counters} | battlefield: {board}"
        )
        lines.append(f"Player ID: {player.key}")
        lines.append(
            "Mana: "
            + (
                ", ".join(f"{mana.name}={count}" for mana, count in player.mana_pool.items())
                or "empty"
            )
        )
    lines.extend(format_combat(state))
    stack = " > ".join(item.action_resolution.context.action_key for item in state.stack.items)
    lines.append(f"Stack (last resolves first): {stack or 'empty'}")
    return "\n".join(lines)


class ConsoleDecisionMaker(DecisionMaker):
    """!
    @brief Read explicit player commands and return real engine actions.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        read: Callable[[str], str] | None = None,
        write: Callable[[str], None] | None = None,
        *,
        auto_pass: bool = False,
    ):
        self.event_bus = event_bus
        self.read = read or input
        self.write = write or print
        self._event_index = 0
        self.auto_pass = auto_pass

    def choose_starting_player(self, players):
        while True:
            self.write(
                "Choose who starts: "
                + ", ".join(f"{i + 1}: {p.name}" for i, p in enumerate(players))
            )
            answer = self.read("Starting player [1/2]: ").strip()
            if answer in {"1", "2"}:
                return players[int(answer) - 1]

    def choose_optional_effect(self, state, context, description):
        """!
        @brief Ask the controller whether to perform a resolving optional effect.
        """
        while True:
            answer = self.read(f"{description} [y/n]: ").strip().lower()
            if answer in {"y", "yes", "n", "no"}:
                return answer in {"y", "yes"}

    def choose_scry(self, state, player, cards):
        """!
        @brief Choose cards to keep on top in draw order; put the remainder below.
        """
        self.write("Scry: " + ", ".join(f"{card_reference(card)}: {card.name}" for card in cards))
        if not cards:
            return (), ()
        while True:
            answer = (
                self.read("Top IDs in draw order (Enter keeps all, - bottoms all): ")
                .strip()
                .lower()
            )
            if not answer:
                return cards, ()
            if answer == "-":
                return (), cards
            by_id = {card_reference(card): card for card in cards}
            ids = answer.split()
            if len(ids) == len(set(ids)) and all(key in by_id for key in ids):
                top = tuple(by_id[key] for key in ids)
                return top, tuple(card for card in cards if card not in top)
            self.write("Choose distinct IDs from the revealed cards.")

    def choose_mulligan(self, state, player, mulligans_taken):
        self.write(
            f"{player.name}: "
            + ", ".join(f"{card.key}: {card.name}" for card in player.hand.values())
        )
        while True:
            answer = self.read(f"Mulligan again (taken {mulligans_taken})? [y/n]: ").strip().lower()
            if answer in {"y", "yes", "n", "no"}:
                return answer in {"y", "yes"}

    def choose_mulligan_bottom(self, state, player, count):
        self.write(
            f"{player.name}: "
            + ", ".join(f"{card.key}: {card.name}" for card in player.hand.values())
        )
        while True:
            keys = self.read(
                f"Choose {count} card keys for the bottom (first will be drawn first): "
            ).split()
            if (
                len(keys) == count
                and len(set(keys)) == count
                and all(key in player.hand for key in keys)
            ):
                return tuple(player.hand[key] for key in keys)
            self.write("Choose the required number of distinct cards from your hand.")

    def show_events(self) -> None:
        if self.event_bus is None:
            return
        for event in self.event_bus.emitted_events[self._event_index :]:
            if event.key == "blocker_declared":
                blocker = event.payload["blocker"]
                attacker = event.payload["attacker"]
                self.write(
                    f"{event.controller.name}: {card_reference(blocker)} ({blocker.name}) "
                    f"blocks {card_reference(attacker)} ({attacker.name})."
                )
            elif event.key == "blockers_declared" and not event.payload["blockers"]:
                self.write(f"{event.controller.name} declares no blockers.")
            elif event.key == "attacker_declared":
                attacker = event.payload["attacker"]
                defender = event.payload["defender"]
                self.write(
                    f"{event.controller.name}: {card_reference(attacker)} ({attacker.name}) "
                    f"attacks {defender.name}."
                )
            elif event.key == "damage_dealt":
                self.write(f"{event.payload['target']} takes {event.payload['amount']} damage.")
            elif event.key == "card_moved":
                self.write(f"{event.source}: {event.payload['from']} -> {event.payload['to']}")
            elif event.key == "card_tapped":
                self.write(f"{event.source} taps.")
            elif event.key not in {"phase_started", "priority_passed"}:
                source = describe_event_value(event.source) if event.source is not None else ""
                controller = event.controller.name if event.controller is not None else ""
                details = describe_event_value(event.payload) if event.payload else ""
                self.write(
                    " | ".join(
                        part
                        for part in (
                            event.key.replace("_", " ").capitalize(),
                            controller,
                            source,
                            details,
                        )
                        if part
                    )
                )
        self._event_index = len(self.event_bus.emitted_events)

    def choose_attackers(self, state, player):
        from game.console.combat_commands import parse_attackers

        if not state.combat.legal_attackers(player):
            return {}
        return self._combat_choice(
            state, player, parse_attackers, "attack <card>... [defender] | pass"
        )

    def choose_blockers(self, state, player):
        from game.console.combat_commands import parse_blockers

        if not any(
            state.combat.legal_blockers(player, attacker) for attacker in state.combat.attackers
        ):
            return {}
        return self._combat_choice(
            state, player, parse_blockers, "block <blocker>:<attacker>... | pass"
        )

    def _combat_choice(self, state, player, parser, usage):
        self.show_events()
        self.write(format_state(state))
        self.write(usage)
        while True:
            raw = self.read(f"{player.name}/combat> ")
            if raw.strip().lower() == "quit":
                raise EOFError
            if raw.strip().lower() == "concede":
                player.has_lost = True
                player.loss_reason = "concede"
                return {}
            try:
                return parser(raw, state, player)
            except CommandError as error:
                self.write(f"Invalid input: {error}")

    def choose_legend(self, state, player, cards):
        self.write(
            "Legend rule: choose one permanent to keep: "
            + ", ".join(card_reference(c) for c in cards)
        )
        while True:
            raw = self.read(f"{player.name}/keep legend> ")
            try:
                card = resolve_card(raw, player, global_scope=True)
                if card in cards:
                    return card
                self.write("Choose one of the listed permanents.")
            except CommandError as error:
                self.write(str(error))

    def choose_replacement(self, state, player, operation, effects):
        for index, effect in enumerate(effects, 1):
            self.write(f"{index}: {getattr(effect, 'key', type(effect).__name__)}")
        while True:
            raw = self.read(f"{player.name}/replacement number> ")
            if raw.strip().lower() == "quit":
                raise EOFError
            try:
                index = int(raw) - 1
                if 0 <= index < len(effects):
                    return effects[index]
            except ValueError:
                pass
            self.write("Choose a listed replacement number.")

    def order_replacement_events(self, state, player, operations):
        from game.game_actions.resolution.replacement_effects import (
            active_effects,
            ReplacementEffect,
        )

        competing = any(
            isinstance(effect, ReplacementEffect)
            and (effect.remaining_budget is not None or effect.remaining_uses is not None)
            and sum(bool(effect.applies(state, op)) for op in operations) > 1
            for effect in active_effects(state)
        )
        if not competing:
            return operations
        for index, op in enumerate(operations, 1):
            if hasattr(op, "amount") and hasattr(op, "target"):
                description = f"{op.amount} damage to {op.target}"
            elif hasattr(op, "destination"):
                description = f"Move {op.card} to {op.destination.name}"
            else:
                description = f"Event affecting {player.name}"
            self.write(f"{index}: {description}")
        while True:
            raw = self.read(f"{player.name}/shared shield: order event numbers> ")
            if raw.strip().lower() == "quit":
                raise EOFError
            try:
                order = tuple(int(number) - 1 for number in raw.split())
                if sorted(order) == list(range(len(operations))):
                    return tuple(operations[index] for index in order)
            except ValueError:
                pass
            self.write("List every event number exactly once.")

    def accept_replacement(self, state, player, operation, effect):
        while True:
            raw = self.read(f"{player.name}/apply {effect.key}? yes/no> ").strip().lower()
            if raw == "quit":
                raise EOFError
            if raw in {"yes", "y", "no", "n"}:
                return raw in {"yes", "y"}

    def choose_discards(self, state, player, count):
        self.write(self._hand(player))
        while True:
            raw = self.read(f"{player.name}/discard {count} card IDs> ")
            if raw.strip().lower() == "quit":
                raise EOFError
            try:
                cards = tuple(resolve_card(ref, player, ZoneType.HAND) for ref in raw.split())
                if len(cards) != count or len(set(cards)) != count:
                    raise CommandError(f"Choose {count} distinct cards from your hand.")
                return cards
            except CommandError as error:
                self.write(f"Invalid input: {error}")

    def process_triggers(self, state, triggers):
        from game.console.command_session import CommandSession

        pending = list(triggers)
        while pending:
            if len(pending) > 1:
                self.write("Choose trigger to put on stack next (last resolves first):")
                for index, trigger in enumerate(pending, 1):
                    self.write(f"{index}: {card_reference(trigger.source)} {trigger.key}")
                raw = self.read("trigger/order> ").strip()
                if raw in {"?", "options"}:
                    continue
                try:
                    index = int(raw) - 1
                    if not 0 <= index < len(pending):
                        raise ValueError()
                except ValueError:
                    self.write("Enter a listed trigger number.")
                    continue
            else:
                index = 0
            trigger = pending[index]
            self.write(f"Triggered: {card_reference(trigger.source)} {trigger.key}")
            action = CommandSession(
                state, trigger.controller, "trigger", self.read, self.write, ability=trigger
            ).run()
            if action is None:
                self.write(
                    "This trigger is mandatory; choose its action or quit the game with EOF/Ctrl+C."
                )
                continue
            for intent in action.get_intents():
                if not intent.context.is_cost:
                    state.stack.push(intent.to_stack_item())
            pending.pop(index)

    def get_action(self, state: State, player: Player) -> GameAction:
        """!
        @brief Choose the next action for the player with priority.
        """
        self.show_events()
        if self.auto_pass:
            from game.console.priority_choices import can_auto_pass

            if can_auto_pass(state, player):
                return PassPriorityAction(player)
        self.write("\n" + format_state(state))
        self.write(f"Priority: {player.name}. " + self._hand(player))
        self.write("Commands: play, activate, pass, hand, inspect, status, help, concede, quit")
        while True:
            raw = self.read(f"{player.name}> ")
            try:
                command = parse_command(raw)
                if command.name == "autopass":
                    if command.arguments not in {("on",), ("off",)}:
                        raise CommandError("Usage: autopass on | autopass off")
                    self.auto_pass = command.arguments == ("on",)
                    self.write("Automatic priority passing: " + ("on" if self.auto_pass else "off"))
                    continue
                if command.name in {"play", "activate"} and not command.arguments:
                    from game.console.command_session import CommandSession

                    action = CommandSession(
                        state, player, command.name, self.read, self.write
                    ).run()
                    if action is not None:
                        return action
                    continue
                if command.name in {"quit", "status", "hand", "help", "options", "?"}:
                    if command.arguments:
                        raise CommandError(f"Usage: {command.name}")
                    if command.name == "quit":
                        raise EOFError
                    if command.name == "status":
                        self.write(format_state(state))
                    elif command.name == "hand":
                        self.write(self._hand(player))
                    else:
                        self.write(
                            "play <card> [target] | activate <card> [ability] <target>\n"
                            "Enter play or activate alone to build step by step; options, back, cancel work inside.\n"
                            "Use the short global card IDs shown in hand/status, e.g. play c7 bob; inspect c8.\n"
                            "Targets: player name, self or opponent. Use the stable card IDs shown in hand/status.\n"
                            'Quoted names also work: play "Ember Adept". Duplicate names need a card ID.\n'
                            "hand | inspect <global-card-id> | status | pass/Enter | concede | quit\n"
                            "Both players pass to resolve the top stack entry; pass with an empty stack to advance.\n"
                            "Incremental input: play/activate alone; options/? | back | cancel; confirm to submit.\n"
                            "Modes: play <id> mode=2 bob. Multiple targets: play <id> c11,c30.\n"
                            "Mana: casting automatically taps supported mana sources; activate a land to choose manually.\n"
                            "Lands: play mountain1; costs accept x=2.\n"
                            "autopass on | autopass off: pass empty priority windows; keep stack and available reactions.\n"
                            "Combat prompts: attack <id>:<player-or-planeswalker-id>; block <blocker>:<attacker>; pass chooses none.\n"
                            "Creatures need haste or continuous control since your turn began to attack/tap.\n"
                            "Most teaching spells are free; Ember Adept and Watchful Adept have haste."
                        )
                    continue
                if command.name == "inspect":
                    if len(command.arguments) != 1:
                        raise CommandError("Usage: inspect <card>")
                    card = resolve_card(command.arguments[0], player, global_scope=True)
                    self.write(f"{card_reference(card)}: {card.name} | {card.get_zone().name}")
                    self.write(
                        "Types: " + " ".join(sorted(kind.name for kind in card.get_types(state)))
                    )
                    self.write(
                        "Subtypes: "
                        + (
                            " ".join(sorted(kind.name for kind in card.get_subtypes(state)))
                            or "none"
                        )
                    )
                    from game.mana.mana_value import format_mana_cost

                    self.write("Mana cost: " + format_mana_cost(card.get_mana_cost(state)))

                    if CardType.CREATURE in card.get_types(state):
                        self.write(f"Creature {card.get_power(state)}/{card.get_toughness(state)}")
                    if card.get_zone() == ZoneType.BATTLEFIELD:
                        self.write(permanent_details(card, state))
                    rules_text = card.definition.oracle_text
                    if rules_text:
                        self.write("Rules text (printed abilities):")
                        for line in rules_text.splitlines():
                            self.write("  " + line)
                    else:
                        self.write("Rules text unavailable; showing engine effect descriptions.")
                    self.write("Available commands:")
                    for definition in card.get_ability_defs(state).values():
                        timing = (
                            "your main phase, empty stack"
                            if definition.sorcery_speed or definition.loyalty_cost is not None
                            else "with priority"
                        )
                        if definition.loyalty_cost is not None:
                            self.write(
                                f"Loyalty cost: {definition.loyalty_cost:+d}; once per permanent per turn"
                            )
                        self.write(
                            f"{definition.key} ({'play' if definition.is_spell else 'activate'}, {timing})"
                        )
                        if rules_text:
                            continue
                        for kind, subdefs in (
                            ("Cost", definition.cost_subdefs),
                            ("Effect", definition.action_subdefs),
                        ):
                            for subdef in subdefs:
                                for effect in sorted(subdef.effects, key=lambda effect: effect.key):
                                    self.write(f"  {kind}: {effect.get_info()}")
                    continue
                return build_action(state, player, raw)
            except CommandError as error:
                self.write(f"Invalid input: {error}")

    @staticmethod
    def _hand(player: Player) -> str:
        return "Hand: " + (
            ", ".join(f"{card_reference(card)}: {card.name}" for card in player.hand.values())
            or "empty"
        )


@dataclass(frozen=True)
class DemoGame:
    state: State
    event_bus: EventBus
    loop: GameLoop

    def run(self) -> Player | None:
        """!
        @brief Run to a loss and return the surviving player, or None for a draw.
        """
        self.loop.run(self.state)
        survivors = [player for player in self.state.players if player.is_alive]
        return survivors[0] if len(survivors) == 1 else None


def create_demo_game(
    controllers: tuple[DecisionMaker, DecisionMaker] | None = None,
    health: int = 10,
    *,
    expanded: bool = False,
    full_rules: bool | None = None,
) -> DemoGame:
    """!
    @brief Build a deterministic two-player example with replaceable controllers.
    """
    event_bus = EventBus()
    if controllers is None:
        console = ConsoleDecisionMaker(event_bus)
        controllers = (console, console)
    if len(controllers) != 2:
        raise ValueError("The demo requires two controllers.")
    players = [
        Player([], controller, idx=i, name=name)
        for i, (name, controller) in enumerate(zip(("Alice", "Bob"), controllers))
    ]
    full_rules = expanded if full_rules is None else full_rules
    state = State(players, skip_first_draw=full_rules)
    # Reuse Turn's phase sequencing, with a short cycle for terminal play.
    if not full_rules:
        state.turn._phase_order = [
            TurnPhase.UNTAP,
            TurnPhase.DRAW,
            TurnPhase.PRECOMBAT_MAIN,
            TurnPhase.END_STEP,
        ]
        if expanded:
            state.turn._phase_order.append(TurnPhase.CLEANUP)
    for player in players:
        player.health = health
        # CardCollection draws the last inserted card; open with a creature
        # and two Bolts, with more Bolts available on subsequent turns.
        cards = [(LIGHTNING_BOLT, f"bolt{i}") for i in range(7, 0, -1)] + [(EMBER_ADEPT, "adept1")]
        for definition, reference in cards:
            player.add_card(
                Card(definition, player, key=f"{player.name.casefold()}-{reference}"),
                ZoneType.DECK,
                state,
            )
        for _ in range(3):
            player.draw(state)
        player.add_card(
            Card(RECALL_TAPPED, player, key=f"{player.name.casefold()}-recall1"),
            ZoneType.HAND,
            state,
        )
        if expanded:
            from game.cards.demo_cards import EXPANDED_CARDS

            for reference, definition in EXPANDED_CARDS.items():
                player.add_card(
                    Card(definition, player, key=f"{player.name.casefold()}-{reference}"),
                    ZoneType.HAND,
                    state,
                )
            from game.rules.lands import basic_land

            for name in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
                player.add_card(
                    Card(
                        basic_land(name), player, key=f"{player.name.casefold()}-{name.casefold()}1"
                    ),
                    ZoneType.HAND,
                    state,
                )
    processor = ActionProcessor(ResolutionEngine(OperationExecutor(), event_bus))
    return DemoGame(state, event_bus, GameLoop(None, processor))


def create_starter_demo_game(
    controllers: tuple[DecisionMaker, DecisionMaker] | None = None,
    *,
    colors=("white", "red"),
    seed=1,
    starting_player_idx=0,
    names=("Alice", "Bob"),
    config=None,
) -> DemoGame:
    """!
    @brief Build a normal 20-life game from two Arena Beginner starter lists.

    The default is the requested Keep the Peace (white) versus Goblins
    Everywhere (red) matchup.  Deck construction, shuffle, opening hands and
    mulligans are delegated to :func:`game.game_loop.setup.create_game`; this helper only
    supplies the real engine loop and event bus used by the console demo.
    """
    from game.cards.decks import load_arena_starter
    from game.game_loop.setup import SetupConfig, create_game
    from game.cards.starter_cards import starter_catalog

    if len(colors) != 2:
        raise ValueError("A starter demo needs exactly two colours.")
    event_bus = EventBus()
    if controllers is None:
        console = ConsoleDecisionMaker(event_bus, auto_pass=True)
        controllers = (console, console)
    if len(controllers) != 2:
        raise ValueError("The starter demo requires two controllers.")
    # Rebind console controllers supplied by a caller when they did not yet
    # have an event stream.
    for controller in controllers:
        if isinstance(controller, ConsoleDecisionMaker) and controller.event_bus is None:
            controller.event_bus = event_bus
    game_config = config or SetupConfig(
        starting_life=20, opening_hand_size=7, minimum_deck_size=60, maximum_copies=4
    )
    state = create_game(
        tuple(load_arena_starter(color) for color in colors),
        controllers,
        starter_catalog(),
        seed=seed,
        starting_player_idx=starting_player_idx,
        names=names,
        config=game_config,
    )
    processor = ActionProcessor(ResolutionEngine(OperationExecutor(), event_bus))
    return DemoGame(state, event_bus, GameLoop(None, processor))
