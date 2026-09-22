"""Explicit mana production and payment through ordinary effects and operations."""

from .data_structs.effect import Effect
from .data_structs.operation import Operation
from .resolution.event_bus import GameEvent


class AddManaOperation(Operation):
    """!
    @brief Add a fixed amount of one mana type to the controller's mana pool.
    """

    def __init__(self, context, mana, amount):
        """!
        @brief Create a mana-production operation.

        @param context Resolution context identifying the source and controller.
        @param mana Mana type to produce.
        @param amount Nonnegative amount of mana to add.
        @throws ValueError If `amount` is not a nonnegative integer.
        """
        super().__init__(context)

        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("Mana amounts must be nonnegative integers.")

        self.mana = mana
        self.amount = amount

    def lki_cards(self, state):
        return self.source_lki_cards()

    def execute(self, state):
        """!
        @brief Add mana to the controller's pool and emit a production event.

        @param state Current game state.
        @return A single `mana_added` event.
        """
        self.context.controller.mana_pool.add(
            {self.mana: self.amount}
        )

        return [
            GameEvent(
                "mana_added",
                self.context.source,
                self.context.controller,
                {
                    "mana": self.mana.name,
                    "amount": self.amount,
                },
            )
        ]


class SpendManaOperation(Operation):
    """!
    @brief Pay an explicit immutable mana selection from the controller's pool.

    The payment is fixed when the operation is created; execution only verifies
    that the selected mana remains available and removes it from the live pool.
    """

    def __init__(self, context, payment):
        """!
        @brief Create an operation for a concrete mana payment.

        @param context Resolution context identifying the source and controller.
        @param payment Mapping of mana types to amounts being spent.
        """
        super().__init__(context)

        from ..mana.mana_value import ImmutableManaPool

        # Freeze the solver-selected payment so later mutations of the source
        # mapping cannot change what this operation intends to pay.
        self.payment = ImmutableManaPool(payment)

    def lki_cards(self, state):
        return self.source_lki_cards()

    def validation_error(self, state):
        """!
        @brief Check whether every selected mana amount is still available.

        @param state Current game state.
        @return Validation message on failure, otherwise `None`.
        """
        if any(
            self.context.controller.mana_pool.get(mana, 0) < count
            for mana, count in self.payment.items()
        ):
            return "Insufficient mana for this payment."

        return None

    def execute(self, state):
        """!
        @brief Remove the selected mana and emit a payment event.

        The payment is revalidated immediately before mutation because earlier
        operations in the same cost may have changed the mana pool.

        @param state Current game state.
        @return A single `mana_paid` event.
        @throws ValueError If the selected payment is no longer available.
        """
        error = self.validation_error(state)

        if error:
            raise ValueError(error)

        self.context.controller.mana_pool.substract(
            self.payment
        )

        return [
            GameEvent(
                "mana_paid",
                self.context.source,
                self.context.controller,
                {
                    "payment": {
                        mana.name: count
                        for mana, count in self.payment.items()
                    }
                },
            )
        ]


class AddManaEffect(Effect):
    """!
    @brief Declarative effect that produces mana during resolution.

    Subclasses may override `get_amount()` to derive production from the
    current state without mutating it.
    """

    def __init__(self, key, mana, amount=1):
        """!
        @brief Create a mana-producing effect.

        @param key Stable effect identifier.
        @param mana Mana type produced by the effect.
        @param amount Default nonnegative amount produced.
        @throws ValueError If `amount` is not a nonnegative integer.
        """
        super().__init__(key)

        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("Mana amounts must be nonnegative integers.")

        self.mana = mana
        self.amount = amount

    def to_operations(self, state, context):
        """!
        @brief Materialize the current mana production as an operation.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield AddManaOperation(
            context,
            self.mana,
            self.get_amount(state, context),
        )

    def get_amount(self, state, context):
        """!
        @brief Return the amount of mana produced in the supplied state.

        Overrides must remain pure because this method may be queried while
        constructing or evaluating prospective mana-production plans.

        @param state Current game state.
        @param context Bound resolution context.
        @return Amount of mana this effect would produce.
        """
        return self.amount

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Add {self.amount} {self.mana.name} mana."


class PayLifeOperation(Operation):
    """!
    @brief Pay a fixed amount of life as an explicit cost operation.
    """

    def __init__(self, context, amount):
        """!
        @brief Create a life-payment operation.

        @param context Resolution context identifying the paying controller.
        @param amount Nonnegative amount of life to pay.
        @throws ValueError If `amount` is not a nonnegative integer.
        """
        super().__init__(context)

        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("A life payment must be a nonnegative integer.")

        self.amount = amount

    def lki_cards(self, state):
        return self.source_lki_cards()

    def validation_error(self, state):
        """!
        @brief Check whether the controller can pay the requested life.

        @param state Current game state.
        @return Validation message on failure, otherwise `None`.
        """
        if self.context.controller.health < self.amount:
            return "Insufficient life for this payment."

        return None

    def execute(self, state):
        """!
        @brief Pay life and emit the corresponding payment event.

        The operation revalidates against the live life total immediately
        before modifying it.

        @param state Current game state.
        @return A single `life_paid` event.
        @throws ValueError If the life payment is no longer possible.
        """
        error = self.validation_error(state)

        if error:
            raise ValueError(error)

        self.context.controller.health -= self.amount

        return [
            GameEvent(
                "life_paid",
                self.context.source,
                self.context.controller,
                {"amount": self.amount},
            )
        ]