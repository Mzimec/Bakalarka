"""Reusable rule objects required by the five ANB starter decklists."""

from __future__ import annotations

from dataclasses import dataclass, replace

from game.enums import CardType, CounterType, Layer, ModifierType, ZoneType
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.operation import Operation
from game.game_actions.data_structs.ability import TriggerCondition
from game.game_actions.resolution.event_bus import GameEvent
from game.game_state.modifier import Modifier, SetModifier
from game.game_state.layers import LayeredModifier
from game.operations.card_operations import MoveCardOperation, DamageCreatureOperation


def targets(context, key="target"):
    """!
    @brief Return the distinct objects in one bound target slot.
    """
    return tuple(card for group in context.targets.get(key, {}).values() for card in group)


class RuleEffect(Effect):
    """!
    @brief Adapt a pure operation generator to the declarative ability pipeline.
    """

    def __init__(self, key, generate, *, slot_key="target", info=None):
        super().__init__(key)
        self.generate, self.slot_key = generate, slot_key
        self.info = info or key.replace("_", " ")

    def to_operations(self, state, context):
        """!
        @brief Generate the operations for this effect and its bound context.
        """
        yield from self.generate(state, context)

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return self.info


class EventCondition(TriggerCondition):
    """!
    @brief Evaluate a bound trigger against event-time source and controller data.
    """

    def __init__(self, predicate, *, looks_back=False):
        self.predicate = predicate
        self.looks_back_in_time = looks_back

    def matches(self, state, event):
        """!
        @brief Check whether the supplied event satisfies this condition.
        """
        return True  # Source-aware checks are performed by matches_trigger.

    def matches_trigger(self, trigger, state):
        """!
        @brief Evaluate the condition using the bound trigger source and event.
        """
        return self.predicate(state, trigger)


@dataclass(frozen=True)
class FormulaModifier(Modifier):
    """!
    @brief A pure, source-bound characteristic-defining integer expression.
    """

    formula: object

    @property
    def behavior(self):
        return ModifierType.SET

    @property
    def layer(self):
        return Layer.PT_CDA

    def for_context(self, source, state):
        return LayeredModifier(SetModifier(self.formula(state, source)), self.layer)

    def modify(self, original):
        """!
        @brief Apply this modifier to the current characteristic value.
        """
        raise RuntimeError("Bind a formula to its continuous effect source first.")


class ScryOperation(Operation):
    """!
    @brief Reorder a chosen prefix of a library without drawing the cards.
    """

    def __init__(self, context, amount=1):
        super().__init__(context)
        self.amount = amount

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        player = self.context.controller
        viewed = tuple(reversed(tuple(player.deck.values())))[: self.amount]
        choose = getattr(player.decision_maker, "choose_scry", None)
        top, bottom = choose(state, player, viewed) if choose else (viewed, ())
        top, bottom = tuple(top), tuple(bottom)
        if len(top + bottom) != len(viewed) or set(top + bottom) != set(viewed):
            raise ValueError("Scry must partition exactly the viewed cards.")
        remainder = [card for card in player.deck.values() if card not in viewed]
        player.deck.clear()
        player.deck.update(
            (card.key, card) for card in (*reversed(bottom), *remainder, *reversed(top))
        )
        return [
            GameEvent(
                "scry",
                self.context.source,
                player,
                {"amount": len(viewed), "top": top, "bottom": bottom},
            )
        ]


class UntapOperation(Operation):
    def __init__(self, context, card):
        super().__init__(context)
        self.card = card

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        self.card.is_tapped = False
        return [GameEvent("card_untapped", self.card, self.context.controller)]


class SkipUntapOperation(Operation):
    """!
    @brief Remember the affected incarnation and the specified player's next untap.
    """

    def __init__(self, context, card, player):
        super().__init__(context)
        self.card, self.player = card, player

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        self.card._skip_untap = (self.card.zone_revision, self.player)
        return [
            GameEvent(
                "skip_next_untap", self.card, self.context.controller, {"player": self.player}
            )
        ]


class LoseLifeOperation(Operation):
    def __init__(self, context, player, amount):
        super().__init__(context)
        self.player, self.amount = player, amount

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        self.player.health -= self.amount
        return [
            GameEvent(
                "life_lost",
                self.context.source,
                self.context.controller,
                {"target_object": self.player, "amount": self.amount},
            )
        ]


class ChooseMoveOperation(Operation):
    """!
    @brief Resolve a nontargeted discard or sacrifice chosen by the affected player.
    """

    def __init__(self, context, player, amount, *, sacrifice=False):
        super().__init__(context)
        self.player, self.amount, self.sacrifice = player, amount, sacrifice

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        if self.sacrifice:
            options = tuple(
                card
                for card in state.get_cards(from_zones=[ZoneType.BATTLEFIELD])
                if card.get_controller(state) is self.player
                and card.is_type(state, CardType.CREATURE)
            )
            chooser = getattr(self.player.decision_maker, "choose_sacrifices", None)
        else:
            options = tuple(self.player.hand.values())
            chooser = getattr(self.player.decision_maker, "choose_discards", None)
        count = min(self.amount, len(options))
        chosen = tuple(
            chooser(state, self.player, options, count)
            if chooser and self.sacrifice
            else chooser(state, self.player, count) if chooser else options[:count]
        )
        if (
            len(chosen) != count
            or len(set(chosen)) != count
            or any(card not in options for card in chosen)
        ):
            raise ValueError("Choose the required distinct discard or sacrifice cards.")
        from game.game_actions.resolution.replacement_effects import ReplacementResolver
        from game.game_actions.resolution.operation_executor import OperationExecutor

        operations = [MoveCardOperation(self.context, card, ZoneType.GRAVEYARD) for card in chosen]
        replaced = ReplacementResolver().replace(state, operations)
        return OperationExecutor().execute_batch(state, replaced)


class FightOperation(Operation):
    """!
    @brief Assign both creatures' fight damage before either creature can die.
    """

    def __init__(self, context, first, second, *, one_way=False):
        super().__init__(context)
        self.first, self.second, self.one_way = first, second, one_way

    def execute(self, state):
        """!
        @brief Apply this executable object to the supplied game state.
        """
        if any(
            card.get_zone() != ZoneType.BATTLEFIELD or not card.is_type(state, CardType.CREATURE)
            for card in (self.first, self.second)
        ):
            return []
        from game.game_actions.resolution.replacement_effects import ReplacementResolver
        from game.game_actions.resolution.operation_executor import OperationExecutor

        pairs = [(self.first, self.second)]
        if not self.one_way:
            pairs.append((self.second, self.first))
        operations = [
            DamageCreatureOperation(
                replace(self.context, source=source, controller=source.get_controller(state)),
                target,
                max(0, source.get_power(state)),
            )
            for source, target in pairs
        ]
        return OperationExecutor().execute_batch(
            state, ReplacementResolver().replace(state, operations)
        )


def is_death(event):
    return (
        event.key == "card_moved"
        and event.payload.get("from") == "BATTLEFIELD"
        and event.payload.get("to") == "GRAVEYARD"
        and CardType.CREATURE in event.payload["last_known"].types
    )


def lost_life_this_turn(state, player):
    if getattr(state, "_history_turn", None) != state.turn.number:
        return False
    return any(
        event.key in {"damage_dealt", "life_lost", "life_paid"}
        and event.payload.get("amount", 0) > 0
        and (event.key != "damage_dealt" or event.payload.get("life_loss", 0) > 0)
        and (
            event.payload.get("target_object") is player
            or (event.key == "life_paid" and event.controller is player)
        )
        for event in getattr(state, "turn_events", ())
    )
