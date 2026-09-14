"""Replacement rules return None (unchanged) or a sequence, including prevention ()."""

from dataclasses import dataclass

from ...enums import ZoneType
from ...operations.card_operations import DamagePlayerOperation


@dataclass(frozen=True)
class ControllerDamageShield:
    """!
    @brief Reduce preventable damage dealt to this source's controller.

    This is a legacy replacement rule using the `replace()` compatibility
    contract: `None` means unchanged, `()` means fully prevented, and a
    sequence contains the replacement operations.

    @var source
        Permanent providing the damage shield.
    @var reduction
        Amount of damage prevented from each matching operation.
    """

    source: object
    reduction: int = 1

    def replace(self, state, operation):
        """!
        @brief Replace matching controller damage with a reduced damage operation.

        @param state Current game state.
        @param operation Operation being considered for replacement.
        @return `None` if unaffected, `()` if fully prevented, or a tuple
                containing the reduced damage operation.
        """
        if (
            self.source.get_zone() != ZoneType.BATTLEFIELD
            or type(operation) is not DamagePlayerOperation
            or getattr(operation, "unpreventable", False)
            or operation.target is not self.source.get_controller(state)
        ):
            return None

        amount = max(0, operation.amount - self.reduction)

        # An empty replacement sequence represents complete prevention.
        if not amount:
            return ()

        from copy import copy

        # Replacement previews must not mutate the original operation.
        reduced = copy(operation)
        reduced.amount = amount
        return (reduced,)