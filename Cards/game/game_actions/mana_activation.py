"""Event marker for a mana activation nested inside another action's payment."""

from .data_structs.operation import Operation
from .resolution.event_bus import GameEvent


class ManaActivationEventOperation(Operation):
    """!
    @brief Emit the activation event for a mana ability used during payment.

    The actual activation cost and mana production are represented by separate
    operations in the payment plan. This operation exists only so the nested
    mana ability still produces the normal `ability_activated` game event.
    """

    def reserve_cost(self, state, resources):
        """!
        @brief Reserve no resources for this event-only operation.

        Tapping, sacrificing, mana spending and other payment requirements are
        represented and reserved by their own operations.

        @param state Current game state.
        @param resources Cost-resource reservation set.
        @return `None`.
        """
        return None

    def lki_cards(self, state):
        return self.source_lki_cards()

    def execute(self, state):
        """!
        @brief Emit the activation event without modifying game state directly.

        @param state Current game state.
        @return A single `ability_activated` event for the nested mana ability.
        """
        return [
            GameEvent(
                "ability_activated",
                self.context.source,
                self.context.controller,
                {"ability": self.context.ability},
            )
        ]