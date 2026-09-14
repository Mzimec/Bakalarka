"""Declarative replacement definitions, bound runtime effects, and arbitration.

Applicability is pure. Only the selected transform may consume a runtime budget.
Each effect applies once to an event, including all descendants of a split event.
"""

from dataclasses import dataclass, field
from collections import deque
from copy import copy

from ...enums import ZoneType
from ...operations.card_operations import DamagePlayerOperation, MoveCardOperation


@dataclass(frozen=True)
class ReplacementEffectDefinition:
    """!
    @brief Immutable definition of a replacement effect.

    Defines applicability, transformation, active zones, ordering priority,
    and optional runtime limits. A definition is bound to a concrete source
    card through `bind()` before participating in replacement resolution.

    @var key
        Stable identifier used by static-ability filtering.
    @var predicate
        Pure applicability function `(state, effect, operation) -> bool`.
    @var transform
        Transformation function returning replacement operations.
    @var active_zones
        Zones in which the source normally provides this effect.
    @var optional
        Whether the affected player may decline the replacement.
    @var priority
        CR 616 replacement ordering category.
    @var duration
        Optional runtime duration restricting effect activity.
    @var uses
        Optional number of times the effect may be applied.
    @var budget
        Optional shared numeric budget consumed by the transform.
    @var self_entry
        Whether this effect may apply while its own source is entering.
    """

    key: str
    predicate: object  # (state, bound_effect, operation) -> bool
    transform: object  # (state, bound_effect, operation) -> iterable[Operation]
    active_zones: frozenset = frozenset({ZoneType.BATTLEFIELD})
    optional: bool = False
    priority: int = 4  # CR 616: self=0, control-entry=1, copy-entry=2, face-down=3, ordinary=4
    duration: object | None = None
    uses: int | None = None
    budget: int | None = None
    self_entry: bool = False

    def __post_init__(self):
        """!
        @brief Validate priority and runtime-limit configuration.
        """
        if self.priority not in range(5):
            raise ValueError("Replacement priority must be between 0 and 4.")

        for value in (self.uses, self.budget):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Replacement limits must be nonnegative integers.")

    def bind(self, source=None):
        """!
        @brief Bind this definition to a runtime source.

        @param source Runtime object providing the replacement effect.
        @return Bound `ReplacementEffect` instance.
        """
        return ReplacementEffect(self, source)


@dataclass(eq=False)
class ReplacementEffect:
    """!
    @brief Runtime replacement effect bound to one source incarnation.

    Stores source revision and mutable usage/budget state so the same static
    definition can be instantiated independently for different card
    incarnations.

    @var definition
        Immutable replacement-effect definition.
    @var source
        Runtime source providing the effect.
    @var source_revision
        Zone revision of the source when this effect was bound.
    @var remaining_uses
        Remaining application count, or `None` if unlimited.
    @var remaining_budget
        Remaining numeric budget, or `None` if unlimited.
    """

    definition: ReplacementEffectDefinition
    source: object | None = None
    source_revision: int | None = field(init=False)
    remaining_uses: int | None = field(init=False)
    remaining_budget: int | None = field(init=False)

    def __post_init__(self):
        self.source_revision = getattr(self.source, "zone_revision", None)
        self.remaining_uses = self.definition.uses
        self.remaining_budget = self.definition.budget

    @property
    def key(self):
        """!
        @brief Return the stable key of the underlying definition.
        """
        return self.definition.key

    def active(self, state, operation=None):
        """!
        @brief Check whether this runtime effect is currently active.

        Considers exhausted uses/budget, duration, source zone, source
        incarnation, and special self-entry handling.

        @param state Current game state.
        @param operation Optional operation currently being replaced.
        @return `True` if this runtime effect may currently participate.
        """
        if self.remaining_uses == 0 or self.remaining_budget == 0:
            return False

        if (
            self.definition.duration is not None
            and self.definition.duration.is_over(state)
        ):
            return False

        if self.source is not None:
            # Self-entry effects are allowed to function before the source
            # actually reaches the battlefield, provided it is still the same
            # card incarnation that created this bound effect.
            if (
                self.definition.self_entry
                and isinstance(operation, MoveCardOperation)
                and operation.card is self.source
                and operation.destination == ZoneType.BATTLEFIELD
                and self.source.get_zone() != ZoneType.BATTLEFIELD
            ):
                return self.source.zone_revision == self.source_revision

            return (
                self.source._game_state is state
                and self.source.zone_revision == self.source_revision
                and self.source.get_zone() in self.definition.active_zones
            )

        return True

    def applies(self, state, operation):
        """!
        @brief Check both runtime activity and declarative applicability.
        """
        return (
            self.active(state, operation)
            and self.definition.predicate(state, self, operation)
        )

    def apply(self, state, operation):
        """!
        @brief Apply the selected transform and consume one use if limited.

        Applicability checks remain pure; mutable counters are consumed only
        after this effect has actually been selected by the resolver.

        @return Tuple of replacement operations.
        """
        result = tuple(
            self.definition.transform(
                state,
                self,
                operation,
            )
        )

        if self.remaining_uses is not None:
            self.remaining_uses -= 1

        return result


def affected_player(state, operation):
    """!
    @brief Determine which player makes replacement choices for an operation.

    @param state Current game state.
    @param operation Operation being replaced.
    @return Player considered affected by the operation.
    """
    if (
        isinstance(operation, MoveCardOperation)
        and operation.destination == ZoneType.BATTLEFIELD
        and operation.entry_controller is not None
    ):
        return operation.entry_controller

    target = getattr(operation, "target", None)

    if target is None:
        target = getattr(operation, "card", None)

    if target in getattr(state, "players", ()):
        return target

    if target is not None and hasattr(target, "get_controller"):
        if target.get_zone() in {
            ZoneType.BATTLEFIELD,
            ZoneType.STACK,
        }:
            return target.get_controller(state)

        return target.owner

    return operation.context.controller


def active_effects(state):
    """!
    @brief Return active replacement effects bound to current card incarnations.

    Static replacement definitions are cached per card zone revision so their
    runtime limits survive repeated replacement passes but reset when the card
    becomes a new incarnation.

    @param state Current game state.
    @return Tuple of currently active replacement effects and global rules.
    """
    if hasattr(state, "refresh_continuous_effects"):
        state.refresh_continuous_effects()

    from ...stat_type import STAT_STATIC_ABILITIES

    cache = getattr(state, "_replacement_instances", None)

    if cache is None:
        cache = state._replacement_instances = {}

    desired = {}

    for card in getattr(state, "get_cards", lambda: ())():
        for index, definition in enumerate(card.definition.replacement_effects):
            if card.get_zone() in definition.active_zones:
                identity = (
                    id(card),
                    card.zone_revision,
                    index,
                )

                desired[identity] = (
                    cache.get(identity)
                    or definition.bind(card)
                )

    state._replacement_instances = desired

    # Static-ability modifiers may suppress individual replacement effects,
    # so filter bound instances through the card's current static abilities.
    return tuple(
        effect
        for effect in desired.values()
        if effect.key in effect.source.get_stat(
            STAT_STATIC_ABILITIES,
            state,
        )
    ) + tuple(
        getattr(state, "replacement_rules", ())
    )


def incoming_effects(state, operation):
    """!
    @brief Collect self-entry replacement effects for an entering permanent.

    Entry replacement effects must be evaluated before the permanent exists
    on the battlefield. Their availability is therefore determined against
    projected entry characteristics rather than the current state.

    @param state Current game state.
    @param operation Candidate battlefield-entry operation.
    @return Tuple of bound replacement effects available during entry.
    """
    if (
        not isinstance(operation, MoveCardOperation)
        or operation.destination != ZoneType.BATTLEFIELD
    ):
        return ()

    if operation.card.get_zone() == ZoneType.BATTLEFIELD:
        return ()

    from ...stat_type import STAT_STATIC_ABILITIES
    from .entry_replacements import enters_tapped

    operation.entry_prepared = True
    operation._entry_view = None

    # Copy effects may already have changed which definition the permanent
    # will use as it enters.
    definition = operation.entry_copy or operation.card.definition

    definitions = [
        rule
        for rule in definition.replacement_effects
        if rule.self_entry
    ]

    if definition.enters_tapped:
        # Built-in "enters tapped" behavior is represented as a normal
        # replacement effect with stable identity across replacement passes.
        builtin = operation._entry_bindings.get(
            "builtin:enter_tapped"
        )

        if builtin is None:
            builtin = enters_tapped(
                "builtin:enter_tapped"
            ).bind(operation.card)

            operation._entry_bindings[
                "builtin:enter_tapped"
            ] = builtin

        definitions.append(builtin.definition)

    if not definitions:
        return ()

    projected_card, projected_state = operation.entry_characteristics(state)

    available = projected_card.get_stat(
        STAT_STATIC_ABILITIES,
        projected_state,
    )

    result = []

    for rule in definitions:
        if rule.key not in available:
            continue

        identity = id(rule)
        effect = operation._entry_bindings.get(identity)

        if effect is None:
            effect = operation._entry_bindings[identity] = rule.bind(
                operation.card
            )

        result.append(effect)

    return tuple(result)


class ReplacementResolver:
    """!
    @brief Resolve competing replacement effects until operations are final.

    Each replacement effect may apply at most once to an original event and
    all descendant operations produced from it. Competing effects are first
    restricted by CR 616 priority and then chosen by the affected player.
    """

    def replace(self, state, operations):
        """!
        @brief Apply replacement effects to one operation or simultaneous batch.

        Simultaneous operations are ordered in APNAP order where necessary so
        players can choose the order in which finite replacement resources are
        consumed. Each operation is repeatedly replaced until no applicable
        effect remains.

        @param state Current game state.
        @param operations Operations to pass through replacement processing.
        @return Tuple of final operations after all replacements.
        """
        operations = tuple(operations)

        # A simultaneous batch may compete for limited replacement resources.
        # Let each affected player order their own events in APNAP order.
        if len(operations) > 1 and hasattr(state, "players"):
            players = list(state.players)
            start = players.index(state.active_player)
            ordered = []

            for player in (
                players[start:]
                + players[:start]
            ):
                group = tuple(
                    op
                    for op in operations
                    if affected_player(state, op) is player
                )

                choose_order = getattr(
                    player.decision_maker,
                    "order_replacement_events",
                    None,
                )

                if choose_order and len(group) > 1:
                    chosen = tuple(
                        choose_order(
                            state,
                            player,
                            group,
                        )
                    )

                    if sorted(map(id, chosen)) != sorted(map(id, group)):
                        raise ValueError(
                            "Return every simultaneous operation exactly once."
                        )

                    group = chosen

                ordered.extend(group)

            ordered.extend(
                op
                for op in operations
                if affected_player(state, op) not in players
            )

            operations = tuple(ordered)

        # Track effect identities already applied to each event lineage so a
        # replacement cannot reapply to descendants it created itself.
        queue = deque(
            (operation, frozenset())
            for operation in operations
        )

        result = []

        while queue:
            operation, applied = queue.popleft()

            candidates = []
            previews = {}

            incoming = incoming_effects(
                state,
                operation,
            )

            for effect in (
                active_effects(state)
                + incoming
            ):
                if id(effect) in applied:
                    continue

                if isinstance(effect, ReplacementEffect):
                    if effect.applies(state, operation):
                        candidates.append(effect)

                else:
                    # Compatibility contract: legacy `replace()` must be pure,
                    # since only the selected candidate may mutate runtime state.
                    preview = effect.replace(
                        state,
                        operation,
                    )

                    if preview is not None:
                        candidates.append(effect)
                        previews[id(effect)] = tuple(preview)

            if not candidates:
                result.append(operation)
                continue

            def priority(effect):
                return (
                    effect.definition.priority
                    if isinstance(effect, ReplacementEffect)
                    else 4
                )

            # Only effects from the earliest replacement category compete.
            first_priority = min(
                map(priority, candidates)
            )

            candidates = tuple(
                effect
                for effect in candidates
                if priority(effect) == first_priority
            )

            player = affected_player(
                state,
                operation,
            )

            choose = getattr(
                getattr(player, "decision_maker", None),
                "choose_replacement",
                None,
            )

            selected = (
                choose(
                    state,
                    player,
                    operation,
                    candidates,
                )
                if choose and len(candidates) > 1
                else candidates[0]
            )

            if not any(
                selected is effect
                for effect in candidates
            ):
                raise ValueError(
                    "Choose one of the applicable replacement effects."
                )

            next_applied = applied | {id(selected)}

            if isinstance(selected, ReplacementEffect):
                accept = getattr(
                    getattr(player, "decision_maker", None),
                    "accept_replacement",
                    None,
                )

                if (
                    selected.definition.optional
                    and accept
                    and not accept(
                        state,
                        player,
                        operation,
                        selected,
                    )
                ):
                    # Declining still marks this effect as considered for this
                    # event lineage so it is not offered repeatedly.
                    queue.appendleft(
                        (operation, next_applied)
                    )
                    continue

                replacements = selected.apply(
                    state,
                    operation,
                )

            else:
                replacements = previews[id(selected)]

            # Replacement descendants are processed before unrelated queued
            # operations while preserving their original order.
            queue.extendleft(
                (
                    replacement,
                    next_applied,
                )
                for replacement in reversed(replacements)
            )

        return tuple(result)


def damage_multiplier(
    key,
    factor,
    *,
    predicate=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect multiplying matching player damage.

    @param key Unique replacement-effect key.
    @param factor Positive integer damage multiplier.
    @param predicate Optional additional applicability predicate.
    @return Damage-multiplying replacement-effect definition.
    """
    if type(factor) is not int or factor < 1:
        raise ValueError(
            "Damage multiplier must be a positive integer."
        )

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect applies to the damage operation.
        """
        return (
            isinstance(operation, DamagePlayerOperation)
            and operation.amount > 0
            and (
                predicate is None
                or predicate(
                    state,
                    effect,
                    operation,
                )
            )
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the damage operation with multiplied damage.
        """
        changed = copy(operation)
        changed.amount *= factor
        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        **kwargs,
    )


def prevent_damage(
    key,
    amount,
    *,
    total=False,
    predicate=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect preventing matching player damage.

    If `total` is false, each application prevents up to `amount`. If true,
    the amount is a shared runtime budget consumed across applications.

    @param key Unique replacement-effect key.
    @param amount Positive prevention amount.
    @param total Whether `amount` is a shared prevention budget.
    @param predicate Optional additional applicability predicate.
    @return Damage-prevention replacement-effect definition.
    """
    if type(amount) is not int or amount < 1:
        raise ValueError(
            "Prevention amount must be a positive integer."
        )

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect applies to the damage operation.
        """
        return (
            isinstance(operation, DamagePlayerOperation)
            and operation.amount > 0
            and not getattr(
                operation,
                "unpreventable",
                False,
            )
            and (
                predicate is None
                or predicate(
                    state,
                    effect,
                    operation,
                )
            )
        )

    def transform(state, effect, operation):
        """!
        @brief Return the remaining damage after prevention.
        """
        prevented = min(
            operation.amount,
            effect.remaining_budget
            if total
            else amount,
        )

        if total:
            effect.remaining_budget -= prevented

        changed = copy(operation)
        changed.amount -= prevented

        return (
            (changed,)
            if changed.amount
            else ()
        )

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        budget=amount if total else None,
        **kwargs,
    )


def replace_zone(
    key,
    origin,
    destination,
    instead,
    *,
    predicate=None,
    **kwargs,
):
    """!
    @brief Create a replacement effect redirecting matching zone changes.

    @param key Unique replacement-effect key.
    @param origin Required origin zone, or `None` for any origin.
    @param destination Destination zone being replaced.
    @param instead New destination zone.
    @param predicate Optional additional applicability predicate.
    @return Zone-change replacement-effect definition.
    """

    def matches(state, effect, operation):
        """!
        @brief Check whether this effect applies to the move operation.
        """
        return (
            isinstance(operation, MoveCardOperation)
            and operation.destination == destination
            and operation.card.get_zone() != destination
            and (
                origin is None
                or operation.card.get_zone() == origin
            )
            and (
                predicate is None
                or predicate(
                    state,
                    effect,
                    operation,
                )
            )
        )

    def transform(state, effect, operation):
        """!
        @brief Return a copy of the move operation with redirected destination.
        """
        changed = copy(operation)
        changed.destination = instead
        return (changed,)

    return ReplacementEffectDefinition(
        key,
        matches,
        transform,
        **kwargs,
    )
