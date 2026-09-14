"""Stack interaction and simultaneous destruction for control spells."""

from game.enums import CardType, ZoneType
from game.target.target_spec import TargetSpec
from game.game_actions.data_structs.effect import Effect
from game.game_actions.data_structs.operation import Operation
from game.game_actions.resolution.event_bus import GameEvent
from game.operations.card_operations import MoveCardOperation


def spell_item(state, card):
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
    def generate_candidates(self, source, controller, state, reserved=None):
        for card in state.get_cards(from_zones=[ZoneType.STACK]):
            if card is not source and (not reserved or card not in reserved):
                if spell_item(state, card) is not None:
                    yield card


class RemoveCounteredSpellOperation(Operation):
    """!
    @brief Remove a pending spell without removing abilities sharing its source.
    """
    def __init__(self, context, card):
        super().__init__(context)
        self.card = card
        self.countered = False

    def execute(self, state):
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

    def to_operations(self, state, context):
        for group in context.targets.get(self.slot_key, {}).values():
            for card in group:
                operation = RemoveCounteredSpellOperation(context, card)
                yield operation
                # Resolution consumes this generator lazily, after removal succeeds.
                if operation.countered:
                    yield MoveCardOperation(context, card, ZoneType.GRAVEYARD)

    def get_info(self):
        return "Counter target spell."


class DestroyAllCreaturesOperation(Operation):
    """!
    @brief Destroy creatures in one event batch, preserving simultaneous death triggers.
    """
    def execute(self, state):
        from game.game_actions.resolution.replacement_effects import ReplacementResolver

        moves = [
            MoveCardOperation(self.context, card, ZoneType.GRAVEYARD)
            for card in state.get_cards(from_zones=[ZoneType.BATTLEFIELD])
            if card.is_type(state, CardType.CREATURE)
            and not card.has_keyword(state, "indestructible")
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
    def to_operations(self, state, context):
        yield DestroyAllCreaturesOperation(context)

    def get_info(self):
        return "Destroy all creatures."
