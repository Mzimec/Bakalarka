"""Effects which affect a query result at resolution, without selecting targets."""

from .data_structs.effect import Effect
from .data_structs.operation import Operation
from ..operations.card_operations import MoveCardOperation


class MoveMatchingCardsOperation(Operation):
    """!
    @brief Move every card matching a runtime query to one destination zone.

    Unlike targeted effects, the affected cards are not selected when the
    action is generated. The query is evaluated against the live game state
    when this operation resolves.
    """

    def __init__(self, context, spec, destination):
        """!
        @brief Create an operation that moves all cards matching a specification.

        @param context Bound resolution context.
        @param spec Target specification used as a runtime card query.
        @param destination Destination zone for each matching card.
        """
        super().__init__(context)
        self.spec = spec
        self.destination = destination

    def execute(self, state):
        """!
        @brief Evaluate the query and move the resulting cards.

        The complete result is snapshotted before any card is moved. This
        prevents earlier zone changes in the same operation from changing which
        later cards belong to the original query result.

        @param state Current game state.
        @return Events produced by the individual card moves.
        """
        # This is a resolution-time query, not target generation. No selection
        # is frozen when the spell or ability is created.
        cards = tuple(
            self.spec.generate_candidates(
                self.context.source,
                self.context.controller,
                state,
            )
        )

        events = []

        for card in cards:
            events.extend(
                MoveCardOperation(
                    self.context,
                    card,
                    self.destination,
                ).execute(state)
            )

        return events


class MoveMatchingCardsEffect(Effect):
    """!
    @brief Declarative effect that moves all cards matching a runtime query.

    @var spec
        Specification evaluated when the generated operation resolves.
    @var destination
        Zone to which matching cards are moved.
    """

    def __init__(self, key, spec, destination):
        super().__init__(key)
        self.spec = spec
        self.destination = destination

    def to_operations(self, state, context):
        """!
        @brief Generate the runtime query-and-move operation.

        @param state Current game state.
        @param context Bound resolution context.
        """
        yield MoveMatchingCardsOperation(
            context,
            self.spec,
            self.destination,
        )

    def get_info(self):
        """!
        @brief Return a human-readable description of this effect.
        """
        return f"Move all matching cards to {self.destination.name} in their owners' zones."