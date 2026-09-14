"""Reusable declarative effects; runtime operations are created at resolution."""

from .data_structs.effect import Effect
from ..enums import ZoneType
from ..operations.card_operations import DamagePlayerOperation, MoveCardOperation, TapCardOperation


class DamagePlayerEffect(Effect):
    """!
    @brief Deal fixed damage to players selected through one target slot.

    @var amount
        Damage dealt per selected target occurrence.
    @var slot_key
        Target slot containing the affected players.
    """

    def __init__(self, key: str, amount: int, slot_key: str):
        super().__init__(key)
        self.amount = amount
        self.slot_key = slot_key

    def to_operations(self, state, context):
        """!
        @brief Generate one damage operation for each selected player.

        Target multiplicity scales the total damage assigned to that player.

        @param state Current game state.
        @param context Bound resolution context containing selected targets.
        """
        for option in context.targets.get(self.slot_key, {}).values():
            for player, count in option.items():
                yield DamagePlayerOperation(
                    context,
                    player,
                    self.amount * count,
                )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Deal {self.amount} damage to the player in '{self.slot_key}'."


class MoveSourceEffect(Effect):
    """!
    @brief Move the effect's own source to a configured destination zone.

    @var destination
        Zone to which the source should move on resolution.
    """

    def __init__(self, key: str, destination: ZoneType):
        super().__init__(key)
        self.destination = destination

    def to_operations(self, state, context):
        """!
        @brief Generate the source zone-change operation.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield MoveCardOperation(
            context,
            context.source,
            self.destination,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Move this card to {self.destination.name}."


class TapSourceEffect(Effect):
    """!
    @brief Tap the effect's source as a cost or resolution instruction.
    """

    def validation_error(self, state, context):
        """!
        @brief Validate whether the source can currently be tapped.

        This is primarily used by cost validation before the corresponding
        `TapCardOperation` is materialized and executed.

        @param state Current game state.
        @param context Bound resolution context.
        @return Validation message on failure, otherwise `None`.
        """
        if context.source.get_zone() != ZoneType.BATTLEFIELD:
            return "The source must be on the battlefield."

        if context.source.is_tapped:
            return "The source is already tapped."

        if context.source.is_summoning_sick(state):
            return "A creature with summoning sickness cannot pay a tap cost."

        return None

    def to_operations(self, state, context):
        """!
        @brief Generate the operation that taps the source.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield TapCardOperation(
            context,
            context.source,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return "Tap this card."