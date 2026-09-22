"""Isolated characteristic projection for CR 614.12 entry replacement checks.

Cards and effect memberships are copied, never players, decks, stack or history.

The projection has its own card indexes. No operations are executed; custom
predicates must remain pure and use the supplied state instead of captured state.
"""

from copy import copy
from dataclasses import replace

from .card import Card, CardRuntimeState
from .modifier import (
    ContinuousEffectsManager,
    ContinuousEffect,
    ContinuousEffectState,
    ContinuouosEffectModifierSource,
    CounterModifierSource,
    AttachedModifierSource,
)
from .registers.card_register import CardRegister
from ..enums import ZoneType
from ..stat_type import STAT_CONTROLLER
from .stat import ModifiablePrimitiveStat
from immutabledict import immutabledict


def entry_projection(state, operation):
    """!
    @brief Build an isolated projected state for an entering permanent.

    Creates shallow copies of the game state and all relevant runtime cards,
    then projects `operation.card` onto the battlefield with its proposed copy,
    controller, tapped state, counters and attachment relationship.

    Players and unrelated game structures are intentionally shared. No
    operations are executed in the projection; it exists only so entry
    replacement predicates and characteristic queries can evaluate the object
    as it would exist on the battlefield.

    Existing continuous effects are recreated against cloned card identities
    and then refreshed through the ordinary layer engine.

    @param state Real game state before the permanent enters.
    @param operation Pending move operation carrying entry replacement data.
    @return Tuple `(incoming, projected)` containing the projected entering card
        and isolated projected state.
    """
    # Start from fully current characteristics before cloning the effect graph.
    state.refresh_continuous_effects()

    # The projection shares unrelated state objects but receives its own effect
    # manager, dirty state and query indexes.
    projected = copy(state)
    projected._refreshing_effects = False
    projected._effects_dirty = True
    projected._effects_checked_at = None
    projected._layer_ceiling = None
    projected._static_effect_keys = set()
    projected._cont_effect_manager = ContinuousEffectsManager()
    from .card_snapshot_cache import CardSnapshotCache
    projected._card_snapshots = CardSnapshotCache()
    projected.card_register = CardRegister(projected)

    originals = tuple(state.get_cards())

    # The incoming card may not yet belong to any live zone/register.
    if operation.card not in originals:
        originals += (operation.card,)

    clones = {}

    for original in originals:
        cloned = copy(original)
        cloned.runtime_id = None
        cloned._game_state = projected

        # Runtime state is recreated rather than shared so projected counters,
        # tapping, damage and attachments cannot mutate the real game.
        cloned._state = CardRuntimeState(
            tapped=original.is_tapped,
            counters=dict(original.state.counters),
            damage_marked=original.state.damage_marked,
            on_change=cloned._notify_changed,
        )
        cloned._state.zone_info = copy(original.state.zone_info)

        # Modifier sources must reference the cloned runtime containers rather
        # than those belonging to the original card.
        cloned._modifier_sources = (
            ContinuouosEffectModifierSource(cloned.state.active_cont_effects),
            CounterModifierSource(cloned.state.counters),
            AttachedModifierSource(cloned.state.attached),
        )

        clones[original.key] = cloned

    incoming = clones[operation.card.key]

    # Copy effects may replace the printed definition before entry
    # characteristics are evaluated.
    definition = operation.entry_copy or operation.card.definition
    base = Card(definition, operation.card.owner, operation.card.key)

    incoming._definition = definition
    incoming._stats = immutabledict(
        {
            **base.stats,
            STAT_CONTROLLER: ModifiablePrimitiveStat(
                operation.entry_controller or operation.card.owner,
                STAT_CONTROLLER,
            ),
        }
    )

    # Model the incoming object as its new battlefield incarnation without
    # invoking normal zone-change operations or emitting events.
    incoming.state.zone_info.zone = ZoneType.BATTLEFIELD
    incoming.zone_revision += 1
    incoming.state.counters.clear()
    incoming.state.tapped = operation.entry_tapped

    projected._projected_cards = clones
    projected._projected_entrant = incoming

    # Some entry replacements, notably Auras, need the projected attachment
    # relationship to exist while characteristic predicates are evaluated.
    attached_host = getattr(operation, "entry_attachment_host", None)
    if attached_host is not None:
        host = clones[attached_host.key]
        incoming.attached_to = host
        incoming.last_attach_at = state.time_stamp
        host.state.attached[incoming.key] = incoming

    # Registration evaluates base/indexed characteristics. Suppress recursive
    # continuous-effect refresh until every clone is present in the register.
    projected._refreshing_effects = True

    for original in originals:
        cloned = clones[original.key]

        if cloned is not incoming:
            # Rebuild attachment edges entirely in terms of projected objects.
            for key, attached in original.state.attached.items():
                cloned.state.attached[key] = clones[attached.key]

            if original.attached_to is not None:
                cloned.attached_to = clones[original.attached_to.key]
                cloned.last_attach_at = original.last_attach_at

        projected.card_register.register(cloned)

    projected._refreshing_effects = False

    # Recreate currently live effects so their source references point into the
    # projected card graph. Effect membership itself is recomputed below.
    for effect in state._cont_effect_manager.query():
        # The entering permanent is a new incarnation. Static abilities from its
        # old incarnation must not be carried across; its new printed/copy
        # definition will create the appropriate static rules during refresh.
        if (
            effect.definition.static_ability_key
            and effect.definition.source is operation.card
        ):
            continue

        definition = replace(
            effect.definition,
            source=(
                clones.get(
                    effect.definition.source.key,
                    effect.definition.source,
                )
                if effect.definition.source is not None
                else None
            ),
        )

        cloned_effect = ContinuousEffect(
            effect.key,
            definition,
            ContinuousEffectState(set()),
        )

        projected._cont_effect_manager.add(cloned_effect)
        cloned_effect.state.timestamp_order = effect.state.timestamp_order

        if effect.key in state._static_effect_keys:
            projected._static_effect_keys.add(effect.key)

    # Preserve timestamp sequencing so newly discovered effects compare against
    # existing effects exactly as they would in the real game.
    projected._cont_effect_manager._sequence = max(
        projected._cont_effect_manager._sequence,
        state._cont_effect_manager._sequence,
    )

    projected.synchronise_registers = (
        lambda: projected.card_register.synchronise()
    )

    # Recompute targeting, dependencies and layered characteristics using only
    # projected card identities and the projected card index.
    projected.refresh_continuous_effects()

    return incoming, projected