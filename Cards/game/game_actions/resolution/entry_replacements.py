"""Entry instructions are accumulated before the permanent exists on the battlefield."""

from copy import copy

from .replacement_effects import ReplacementEffectDefinition
from ...enums import ZoneType
from ...operations.card_operations import MoveCardOperation


def _entry_matches(state, effect, operation, self_only, predicate, target_spec=None):
    """!
    @brief Check whether a replacement effect applies to a battlefield entry.

    Accepts only moves from another zone onto the battlefield. Optional
    restrictions may limit the effect to its own source, a target
    specification evaluated against projected entry characteristics, or
    an additional custom predicate.

    @param state Current game state.
    @param effect Runtime replacement effect being evaluated.
    @param operation Candidate move operation.
    @param self_only Whether only the effect's own source may be replaced.
    @param predicate Optional additional applicability predicate.
    @param target_spec Optional target specification evaluated against the
           projected entering permanent.
    @return `True` if the operation is a matching battlefield entry.
    """
    if (
        not isinstance(operation, MoveCardOperation)
        or operation.destination != ZoneType.BATTLEFIELD
    ):
        return False

    # Moving an already-present permanent to the battlefield is not an
    # entering event.
    if operation.card.get_zone() == ZoneType.BATTLEFIELD:
        return False

    if self_only and operation.card is not effect.source:
        return False

    if target_spec is not None:
        # Replacement effects may depend on characteristics the permanent
        # will have as it enters, so validate against the projected state
        # rather than the current pre-entry state.
        incoming, projected = operation.entry_characteristics(state)

        source = (
            projected._projected_cards.get(effect.source.key)
            if effect.source is not None
            else None
        )

        controller = (
            source.get_controller(projected)
            if source is not None
            else operation.context.controller
        )

        if not target_spec.is_valid_target(
            incoming,
            source,
            controller,
            projected,
        ):
            return False

    return predicate is None or predicate(state, effect, operation)


def enters_tapped(
    key,
    *,
    self_only=True,
    predicate=None,
    target_spec=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect causing matching permanents to enter tapped.

    @param key Unique replacement-effect key.
    @param self_only Whether the effect applies only to its own source.
    @param predicate Optional additional applicability predicate.
    @param target_spec Optional specification restricting affected entries.
    @return Replacement-effect definition for entering tapped.
    """

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect should replace the entry operation.
        """
        return (
            _entry_matches(
                state,
                effect,
                operation,
                self_only,
                predicate,
                target_spec,
            )
            and not operation.entry_tapped
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the move operation marked to enter tapped.
        """
        changed = copy(operation)
        changed.entry_tapped = True
        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        self_entry=self_only,
        **kwargs,
    )


def enters_with_counters(
    key,
    counter,
    amount,
    *,
    self_only=True,
    predicate=None,
    target_spec=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect adding counters as a permanent enters.

    @param key Unique replacement-effect key.
    @param counter Counter type to place on the entering permanent.
    @param amount Number of counters to add.
    @param self_only Whether the effect applies only to its own source.
    @param predicate Optional additional applicability predicate.
    @param target_spec Optional specification restricting affected entries.
    @return Replacement-effect definition for entering with counters.
    @throws ValueError If `amount` is not a nonnegative integer.
    """
    if type(amount) is not int or amount < 0:
        raise ValueError("Entry counters must be a nonnegative integer.")

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect should replace the entry operation.
        """
        return amount > 0 and _entry_matches(
            state,
            effect,
            operation,
            self_only,
            predicate,
            target_spec,
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the move operation with added entry counters.
        """
        changed = copy(operation)

        # Copy the mapping before mutation so the original operation remains
        # unchanged.
        changed.entry_counters = dict(operation.entry_counters)
        changed.entry_counters[counter] = (
            changed.entry_counters.get(counter, 0) + amount
        )

        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        self_entry=self_only,
        **kwargs,
    )


def enters_under_control(
    key,
    controller,
    *,
    self_only=False,
    predicate=None,
    target_spec=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect changing who controls a permanent as it enters.

    `controller(state, effect, operation)` must return the proposed controller.

    @param key Unique replacement-effect key.
    @param controller Callback selecting the entering permanent's controller.
    @param self_only Whether the effect applies only to its own source.
    @param predicate Optional additional applicability predicate.
    @param target_spec Optional specification restricting affected entries.
    @return Replacement-effect definition for entry under another controller.
    """

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect should replace the entry operation.
        """
        return _entry_matches(
            state,
            effect,
            operation,
            self_only,
            predicate,
            target_spec,
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the move operation with a new entry controller.
        """
        chosen = controller(state, effect, operation)

        if chosen not in state.players:
            raise ValueError(
                "The entering permanent's controller must belong to this game."
            )

        changed = copy(operation)
        changed.entry_controller = chosen
        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        self_entry=self_only,
        priority=1,
        **kwargs,
    )


def enters_as_copy(
    key,
    choose,
    *,
    predicate=None,
    target_spec=None,
    optional=False,
    **kwargs,
):
    """!
    @brief Create a replacement effect causing a permanent to enter as a copy.

    `choose(state, effect, operation)` must return a battlefield card to
    copy, or `None` if no copy should be applied.

    @param key Unique replacement-effect key.
    @param choose Callback selecting the permanent to copy.
    @param predicate Optional additional applicability predicate.
    @param target_spec Optional specification restricting affected entries.
    @param optional Whether applying the replacement is optional.
    @return Replacement-effect definition for entering as a copy.
    """

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect should replace the entry operation.
        """
        return _entry_matches(
            state,
            effect,
            operation,
            True,
            predicate,
            target_spec,
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the move operation with copy characteristics set.
        """
        target = choose(state, effect, operation)

        if target is None:
            return (operation,)

        if (
            target._game_state is not state
            or target.get_zone() != ZoneType.BATTLEFIELD
        ):
            raise ValueError("Choose a battlefield permanent to copy.")

        changed = copy(operation)

        # Store the copied definition as an entry instruction; the permanent
        # itself does not yet exist on the battlefield.
        changed.entry_copy = target.definition

        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        self_entry=True,
        priority=2,
        optional=optional,
        **kwargs,
    )