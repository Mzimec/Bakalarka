"""Discover CR 613.8 dependencies in the supported declarative effect model.

Probes change only effect memberships/active layers and derived query indexes.

They never execute an operation or modify a card's base characteristics.

Target specs must be pure, just as for ordinary continuous-effect evaluation.
"""

from .layers import dependency_order

from ..stat_type import STAT_STATIC_ABILITIES


def exists(state, effect, started):
    """!
    @brief Return whether a continuous effect currently exists for dependency probing.

    Effects that already started remain present. Non-static effects always
    exist, while static abilities must still be present on their source.

    @param state Current game state.
    @param effect Continuous effect being inspected.
    @param started Keys of effects already started in this layer evaluation.
    @return Whether the effect should participate in dependency analysis.
    """
    key = effect.definition.static_ability_key

    return (
        effect.key in started
        or key is None
        or key in effect.definition.source.get_stat(STAT_STATIC_ABILITIES, state)
    )


def signature(state, effect, started):
    """!
    @brief Compute the observable dependency signature of an effect.

    Within the supported declarative model, dependency probing tracks whether
    another effect changes this effect's existence or affected-object set.
    Constant modifier values themselves are intentionally not part of the
    signature.

    @param state Current game state.
    @param effect Continuous effect being inspected.
    @param started Keys of effects already started in this layer.
    @return Frozen set of affected objects, or `None` if the effect does not exist.
    """
    if not exists(state, effect, started):
        return None

    # Constant modifiers do not change what they do when the input value changes.
    # In particular, +N and *N must not acquire a spurious numeric dependency.
    return frozenset(
        effect.state.currently_affected
        if effect.key in started
        else effect.get_affected(state)
    )


def order_layer(state, effects, layer, started, cards):
    """!
    @brief Yield effects in CR 613 dependency order for one continuous-effect layer.

    Dependencies are inferred by temporarily applying each candidate effect and
    observing whether another effect's existence or target signature changes.
    Probe mutations are limited to effect membership, active-layer state and
    derived card indexes, and are fully rolled back after each probe.

    Characteristic-defining abilities are processed separately before other
    effects in layers 2 through 6.

    @param state Current game state.
    @param effects Effects contributing to this layer.
    @param layer Layer currently being evaluated.
    @param started Keys of effects already started in earlier processing.
    @param cards Cards participating in the layer evaluation.
    @return Iterator yielding effects in resolved dependency/timestamp order.
    """
    pending = list(effects)
    applied = []

    while pending:
        # CR 613.3: CDA effects precede other effects in layers 2 through 6.
        cda = [
            effect
            for effect in pending
            if effect.definition.characteristic_defining
        ]
        group = cda if cda and 2 <= layer.value <= 6 else pending

        # Capture the baseline observable state before any dependency probes.
        before = {
            effect.key: signature(state, effect, started)
            for effect in group
        }

        # Explicit dependencies participate alongside dependencies inferred by
        # probing. CDA and non-CDA effects are not allowed to depend across the
        # CR 613.3 ordering boundary.
        dependencies = {
            effect.key: set(effect.definition.depends_on)
            & {
                other.key
                for other in group
                if (
                    other.definition.characteristic_defining
                    == effect.definition.characteristic_defining
                )
            }
            for effect in group
        }

        if len(group) > 1:
            for other in group:
                if before[other.key] is None:
                    continue

                old_targets = set(other.state.currently_affected)
                old_layers = set(other.state.active_layers)

                try:
                    # Temporarily expose `other` exactly as if it were active in
                    # this layer, then refresh indexes before probing peers.
                    for target in before[other.key]:
                        other._register(target)

                    other.state.active_layers.add(layer)

                    for target in other.state.currently_affected:
                        state.card_register.mark_changed(target)

                    state.card_register.synchronise()

                    # If applying `other` changes another effect's existence or
                    # affected set, that effect depends on `other`.
                    for effect in group:
                        if (
                            effect is not other
                            and (
                                effect.definition.characteristic_defining
                                == other.definition.characteristic_defining
                            )
                            and signature(state, effect, started)
                            != before[effect.key]
                        ):
                            dependencies[effect.key].add(other.key)

                finally:
                    # Remove only memberships introduced by this probe and
                    # restore the exact active-layer state that existed before it.
                    for target in (
                        set(other.state.currently_affected) - old_targets
                    ):
                        other._unregister(target)

                    other.state.active_layers = old_layers

                    # Both the probe targets and previously affected objects may
                    # have had queryable characteristics changed by the temporary
                    # layer activation, so refresh their derived indexes.
                    for target in before[other.key] | old_targets:
                        state.card_register.mark_changed(target)

                    state.card_register.synchronise()

        chosen = next(
            dependency_order(
                group,
                key=lambda effect: effect.key,
                dependencies=lambda effect: dependencies[effect.key],
                timestamp=lambda effect: (
                    effect.definition.created_at,
                    effect.state.timestamp_order,
                ),
            )
        )

        pending.remove(chosen)

        if before[chosen.key] is not None:
            # Record the resolved order, not an obsolete graph from an earlier
            # probe: dependencies may disappear after another effect applies.
            chosen.state.inferred_dependencies[layer] = frozenset(applied)

            yield chosen
            applied.append(chosen.key)