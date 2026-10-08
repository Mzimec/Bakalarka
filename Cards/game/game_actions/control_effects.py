"""Stack interaction and simultaneous destruction for control spells."""

from __future__ import annotations
from game.enums import CardType, ZoneType
from game.target.target_spec import TargetSpec
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.event_bus import GameEvent
from game.operations.card_operations import MoveCardOperation


def spell_item(state: object, card: object) -> object:
    """!
    @brief Find the pending spell belonging to this card, excluding ability entries.
    """
    if card.get_zone() != ZoneType.STACK:
        return None
    for item in state.stack.items:
        context = item.action_resolution.context
        if context.source is card and context.ability is not None and context.ability.is_spell:
            return item
    return None


class SpellTargetSpec(TargetSpec):
    """!
    @brief Select pending spell cards; a spell cannot target itself.
    """
    def generate_candidates(
        self,
        source: object,
        controller: object,
        state: object,
        reserved: object = None,
    ) -> object:
        """!
        @brief Yield legal candidates for this target specification.
        """
        for card in state.get_cards(from_zones=[ZoneType.STACK]):
            if card is not source and (not reserved or card not in reserved):
                if spell_item(state, card) is not None:
                    yield card


class RemoveCounteredSpellOperation(Operation):
    """!
    @brief Remove a pending spell without removing abilities sharing its source.
    """
    def __init__(self, context: object, card: object) -> None:
        """!
        @brief Initialize this object.
        """
        super().__init__(context)
        self.card = card
        self.countered = False

    def execute(self, state: object) -> list[object]:
        """!
        @brief Apply this operation to the current game state.
        """
        item = spell_item(state, self.card)
        if item is None or self.card.has_keyword(state, "can't be countered"):
            return []
        state.stack.items.remove(item)
        self.countered = True
        return [GameEvent("spell_countered", self.card, self.card.get_controller(state),
                          {"counter_source": self.context.source})]


class CounterSpellEffect(Effect):
    """!
    @brief Counter a legal target and move it through normal zone replacements.
    """
    slot_key = "target"
    ai_role = "counter"

    def to_operations(self, state: object, context: object) -> object:
        """!
        @brief Generate operations for this effect.
        """
        for group in context.targets.get(self.slot_key, {}).values():
            for card in group:
                operation = RemoveCounteredSpellOperation(context, card)
                yield operation
                # Resolution consumes this generator lazily, after removal succeeds.
                if operation.countered:
                    yield MoveCardOperation(context, card, ZoneType.GRAVEYARD)

    def get_info(self) -> str:
        """!
        @brief Return a human-readable effect description.
        """
        return "Counter target spell."


class DestroyAllCreaturesOperation(Operation):
    """!
    @brief Destroy creatures in one event batch, preserving simultaneous death triggers.
    """
    def execute(self, state: object) -> list[object]:
        """!
        @brief Apply this operation to the current game state.
        """
        from game.game_actions.resolution.replacement_effects import ReplacementResolver

        from helper.query_system.query import EqQuery
        from game.game_state.registers.card_register import IK_ZONE, IK_TYPE
        moves = [
            MoveCardOperation(self.context, card, ZoneType.GRAVEYARD)
            for card in state.query_cards(EqQuery(IK_ZONE, ZoneType.BATTLEFIELD)
                & EqQuery(IK_TYPE, CardType.CREATURE))
            if not card.has_keyword(state, "indestructible")
        ]
        # Determine replacements before any creature leaves. The enclosing executor
        # captures one before/after trigger roster for the complete destruction.
        operations = ReplacementResolver().replace(state, moves)
        events = []
        for operation in operations:
            events.extend(operation.execute(state) or ())
        return events


class DestroyAllCreaturesEffect(Effect):
    """!
    @brief Generate the simultaneous board wipe as one atomic operation.
    """
    def to_operations(self, state: object, context: object) -> object:
        """!
        @brief Generate operations for this effect.
        """
        yield DestroyAllCreaturesOperation(context)

    def get_info(self) -> str:
        """!
        @brief Return a human-readable effect description.
        """
        return "Destroy all creatures."
