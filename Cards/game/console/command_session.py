"""Incremental command input; choices remain local until explicit confirmation."""

import shlex
from itertools import islice

from game.console.console_commands import CommandError, resolve_card, card_reference
from game.enums import ZoneType, CardType
from game.game_actions.data_structs.ability import SubAbilityComposer
from game.game_actions.generation.command_action_builder import chosen_action
from game.console.command_choices import (
    legal_plans,
    feasible,
    target_groups,
    UnsupportedCommandDefinition,
)


class CommandSession:
    stages = ("card", "ability", "mode", "targets", "cost_mode", "cost", "confirm")

    def __init__(self, state, player, command, read, write, *, ability=None):
        self.state, self.player, self.command = state, player, command
        self.read, self.write = read, write
        self.fixed_ability = ability
        self.values = {}
        self.unsupported = set()
        self.minimum = 2 if ability else 0
        if ability:
            self.values.update(card=ability.source, ability=ability.definition)

    def ability(self):
        if self.fixed_ability is not None:
            return self.fixed_ability
        return self.values["ability"].to_ability(self.values["card"], self.player)

    def is_land(self, card):
        return self.command == "play" and CardType.LAND in card.get_types(self.state)

    def playable(self, card):
        if self.is_land(card):
            from game.rules.lands import land_play_error

            return land_play_error(card, self.player, self.state) is None
        return bool(self.definitions(card))

    def parts(self, cost=False):
        ability = self.ability()
        if ability.definition.subdefs:
            raise CommandError("Variable X/Y choices are not supported yet.")
        parts = ability.definition.cost_subdefs if cost else ability.definition.action_subdefs
        return SubAbilityComposer(parts).compile(ability, self.state)

    def modes(self, cost=False):
        part = self.parts(cost)
        return list(part.action_node.generate_options()) if part and part.action_node else []

    def slots(self, cost=False):
        part = self.parts(cost)
        modes = self.modes(cost)
        if not modes:
            return []
        index = self.values["cost_mode" if cost else "mode"]
        return sorted(
            part.get_used_slots_in_esmap(modes[index - 1].effects),
            key=lambda slot: (slot.slot.key, slot.runtime_key),
        )

    def definitions(self, card=None):
        card = card or self.values["card"]
        result = {}
        for key, definition in card.get_activatable_ability_defs(self.state).items():
            if definition.is_spell != (self.command == "play"):
                continue
            if definition.validation_error(card, self.player, self.state):
                continue
            try:
                if feasible(definition.to_ability(card, self.player), self.state):
                    result[key.casefold()] = definition
            except UnsupportedCommandDefinition:
                self.unsupported.add(f"{card_reference(card)}/{key}")
        return result

    def choices(self, stage):
        if stage == "card":
            from helper.query_system.query import EqQuery, HasQuery
            from game.game_state.registers.card_register import IK_TYPE, IK_ABILITY_KIND
            from game.enums import CardType
            for card in self.state.query_cards(
                HasQuery(IK_ABILITY_KIND) | EqQuery(IK_TYPE, CardType.LAND)
            ):
                if self.playable(card):
                    yield card
        elif stage == "ability":
            yield from self.definitions().values()
        elif stage in {"mode", "cost_mode"}:
            cost = stage == "cost_mode"
            for index in range(1, max(1, len(self.modes(cost))) + 1):
                if next(legal_plans(self.ability(), self.state, cost=cost, mode=index), None):
                    yield index
        elif stage in {"targets", "cost"}:
            cost = stage == "cost"
            for _, plan in legal_plans(
                self.ability(),
                self.state,
                cost=cost,
                mode=self.values["cost_mode" if cost else "mode"],
            ):
                yield target_groups(plan)

    def describe(self, value):
        if isinstance(value, tuple):
            return (
                " ".join(",".join(card_reference(obj) for obj in group) or "-" for group in value)
                or "none"
            )
        return str(card_reference(value) if hasattr(value, "key") else value)

    def options(self, stage):
        if stage == "card":
            for card in self.choices(stage):
                self.write(f"{card_reference(card)}: {card.name}")
        elif stage == "ability":
            for key in self.definitions():
                self.write(key)
        elif stage in {"mode", "cost_mode"}:
            part = self.parts(stage == "cost_mode")
            for index, option in enumerate(self.modes(stage == "cost_mode"), 1):
                if (
                    next(
                        legal_plans(
                            self.ability(), self.state, cost=stage == "cost_mode", mode=index
                        ),
                        None,
                    )
                    is None
                ):
                    continue
                self.write(
                    f"{index}: " + "; ".join(part.effects[key].get_info() for key in option.effects)
                )
        elif stage in {"targets", "cost"}:
            cost = stage == "cost"
            if cost:
                part = self.parts(True)
                modes = self.modes(True)
                if modes:
                    self.write(
                        "Cost: "
                        + "; ".join(
                            part.effects[key].get_info()
                            for key in modes[self.values["cost_mode"] - 1].effects
                        )
                    )
                    self.write(
                        "Mana required: "
                        + str(
                            {
                                "/".join(m.name for m in allowed): count
                                for allowed, count in modes[
                                    self.values["cost_mode"] - 1
                                ].mana_req.items()
                            }
                        )
                    )
                    self.write(
                        "Mana pool: "
                        + str({mana.name: count for mana, count in self.player.mana_pool.items()})
                    )
            slots = self.slots(cost)
            for slot in slots:
                resolver = slot.slot.target_resolver
                candidates = resolver.target_spec.generate_candidates(
                    self.values["card"], self.player, self.state
                )
                self.write(
                    f"{slot.runtime_key}: "
                    + ", ".join(f"{card_reference(obj)} ({obj.name})" for obj in candidates)
                )
                selector = resolver.target_selector
                self.write(f"  selector: {selector}")
            self.write(
                "One group per slot; separate targets within a group with commas. Use '-' for an empty group."
                if slots
                else "No target choices. Enter ok to continue."
            )
        else:
            self.write("confirm: execute the chosen action; back: change choices; cancel: discard")

    def accept(self, stage, raw):
        if stage == "card":
            args = shlex.split(raw)
            if len(args) != 1:
                raise CommandError("Enter one card ID or a quoted card name.")
            card = resolve_card(
                args[0],
                self.player,
                ZoneType.HAND if self.command == "play" else ZoneType.BATTLEFIELD,
                controlled=self.command != "play",
            )
            if not self.playable(card):
                raise CommandError(
                    "This card has no executable ability: check costs, targets and timing."
                )
            return card
        if stage == "ability":
            definition = self.definitions().get(raw.casefold())
            if definition is None:
                raise CommandError("Unknown or unavailable ability; type options.")
            return definition
        if stage in {"mode", "cost_mode"}:
            modes = self.modes(stage == "cost_mode")
            index = int(raw)
            if not 1 <= index <= max(1, len(modes)):
                raise CommandError("Unknown mode; type options.")
            if (
                next(
                    legal_plans(self.ability(), self.state, cost=stage == "cost_mode", mode=index),
                    None,
                )
                is None
            ):
                raise CommandError("This mode has no legal targets or payable cost.")
            return index
        if stage in {"targets", "cost"}:
            from game.console.demo_game import _resolve_target
            from game.game_actions.generation.target_binding_generator import (
                ProvidedTargetBindingGenerator,
            )
            from game.game_actions.generation.ability_action_gen_pipeline import (
                ActionGenerationContext,
            )

            slots = self.slots(stage == "cost")
            if not slots:
                if raw.casefold() != "ok":
                    raise CommandError("Enter ok to acknowledge, or back/cancel.")
                return ()
            groups = tuple(
                (
                    tuple(_resolve_target(ref, self.state, self.player) for ref in token.split(","))
                    if token != "-"
                    else ()
                )
                for token in shlex.split(raw)
            )
            next(
                ProvidedTargetBindingGenerator(groups).generate(
                    ActionGenerationContext(self.ability()), slots, self.state
                )
            )
            return groups

    def run(self):
        index = self.minimum
        history = []
        self.write("Build command: options/? | back | cancel. Nothing is paid until confirm.")
        while True:
            stage = self.stages[index]
            if stage == "ability" and self.is_land(self.values["card"]):
                from game.rules.lands import LandPlayAction, land_play_error

                raw = (
                    self.read(f"play land {card_reference(self.values['card'])}/confirm> ")
                    .strip()
                    .lower()
                )
                if raw in {"cancel", "quit"}:
                    return None
                if raw == "back":
                    index = 0
                    self.values.clear()
                    history.clear()
                    continue
                error = land_play_error(self.values["card"], self.player, self.state)
                if error:
                    self.write(error)
                    return None
                if raw == "confirm":
                    return LandPlayAction(self.values["card"], self.player)
                self.write("Play this land: confirm | back | cancel. It does not use the stack.")
                continue
            try:
                if stage != "confirm":
                    choices = list(islice(self.choices(stage), 2))
                    if not choices:
                        if self.unsupported:
                            self.write(
                                "No supported choices; feasibility is unknown for: "
                                + ", ".join(sorted(self.unsupported))
                            )
                        else:
                            self.write(
                                f"Cannot {self.command}: no executable choice at {stage} (costs, targets or timing)."
                            )
                        return None
                    if len(choices) == 1:
                        self.values[stage] = choices[0]
                        self.write(f"Selected {stage}: {self.describe(choices[0])}")
                        index += 1
                        continue
                if stage == "confirm":
                    action = chosen_action(
                        self.ability(),
                        self.state,
                        self.values["targets"],
                        mode=self.values["mode"],
                        cost_targets=self.values["cost"],
                        cost_mode=self.values["cost_mode"],
                    )
                    self.write(
                        f"Ready: {self.command} {card_reference(self.values['card'])} {self.values['ability'].key}"
                    )
                    self.write(
                        "Targets: "
                        + str(
                            [
                                [getattr(t, "name", str(t)) for t in group]
                                for group in self.values["targets"]
                            ]
                        )
                    )
                    self.write("Target IDs: " + self.describe(self.values["targets"]))
                    self.write("Cost targets: " + self.describe(self.values["cost"]))
                    for binding in action.cost_generator.effects.sequence:
                        self.write("Cost: " + binding.effect.get_info())
                    payment = action.cost_generator.mana_solver_result
                    if payment and payment.mana_plan:
                        self.write(
                            "Auto-tap: "
                            + ", ".join(card_reference(step.source) for step in payment.mana_plan)
                        )
                    if payment and payment.payment:
                        self.write(
                            "Pay mana: "
                            + str({mana.name: count for mana, count in payment.payment.items()})
                        )
                raw = self.read(f"{self.command}/{stage}> ").strip()
                if raw.casefold() in {"options", "?", "help"}:
                    self.options(stage)
                    continue
                if raw.casefold() in {"cancel", "quit"}:
                    return None
                if raw.casefold() == "back":
                    if not history:
                        self.write("No earlier choice to change; use cancel.")
                        continue
                    index = history.pop()
                    for key in self.stages[index:]:
                        self.values.pop(key, None)
                    continue
                if stage == "confirm":
                    if raw.casefold() != "confirm":
                        raise CommandError("Enter confirm, back or cancel.")
                    return action
                self.values[stage] = self.accept(stage, raw)
                history.append(index)
                index += 1
            except (ValueError, CommandError) as error:
                self.write(f"Invalid choice: {error}")
                if "not supported" in str(error):
                    return None
                if stage == "confirm":
                    index = self.minimum
                    history.clear()
                    for key in self.stages[index:]:
                        self.values.pop(key, None)
