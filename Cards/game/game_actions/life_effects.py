"""Life gain operations shared by spell effects and simultaneous lifelink."""

from __future__ import annotations
from .data_structs.operation import Operation
from .resolution.event_bus import GameEvent


class GainLifeOperation(Operation):
    """!
    @brief Increase a player's life total and emit a life-gained event.
    The affected player is taken from the resolution context controller,
    allowing the same operation to be reused by ordinary spell effects and
    generated lifelink life gain.
    """

    def __init__(self, context: object, amount: int) -> None:
        """!
        @brief Create a life-gain operation.
        @param context Resolution context identifying the source and player.
        @param amount Nonnegative amount of life to gain.
        @throws ValueError If `amount` is not a nonnegative integer.
        """
        super().__init__(context)

        if type(amount) is not int or amount < 0:
            raise ValueError("Life gain must be a nonnegative integer.")

        self.amount = amount

    def lki_cards(self, state: object) -> tuple[object, ...] | None:
        """!
        @brief Return cards whose last-known information may be needed.
        """
        return self.source_lki_cards()

    def execute(self, state: object) -> list[object]:
        """!
        @brief Apply the life gain to the context controller.
        Zero life gain is a no-op and produces no event.
        @param state Current game state.
        @return A single `life_gained` event, or an empty list for zero gain.
        """
        if not self.amount:
            return []

        player = self.context.controller
        player.health += self.amount

        # Life totals may affect derived continuous effects, so force their
        # dependent state to be refreshed before it is queried again.
        state._effects_dirty = True

        return [
            GameEvent(
                "life_gained",
                self.context.source,
                player,
                {
                    "amount": self.amount,
                    "player": player,
                },
            )
        ]
