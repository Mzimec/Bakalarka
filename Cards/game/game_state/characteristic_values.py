"""Shared effective values for native card reads and index projections.

Use live, characteristic-specific signatures rather than index membership
revisions: memberships can be stale or intermediate during layer evaluation.
Unknown callbacks retain the general evaluator. No final value is reused while
layer evaluation is active. Entries belong to the card's runtime, not a global
cache; replacement of its stat object also replaces the cached result.
"""
from immutabledict import immutabledict

from helper.mutability_objs import ImmutableDict
from .modifier import (
    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
    ContinuousEffect, ContinuousEffectDefinition, AddIntModifier,
    MultiplyIntModifier, SetModifier,
    PlusCounter, MinusCounter, only_empty_builtin_sources,
)
from . import modifier as registry
from .layers import LayeredModifier, modifier_layer
from .stat import Stat, ModifiableReferenceStat
from ..stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_ATTACH_MODS


UNSUPPORTED = object()
# Signature validation pays off for numeric pipelines. Ordinary type/controller
# reads are cheaper through the existing evaluator on measured game workloads.
SUPPORTED = frozenset({STAT_POWER, STAT_TOUGHNESS})


def _fixed_modifier(stat, modifier):
    if hasattr(modifier, "for_context"):
        return UNSUPPORTED
    if type(modifier) is LayeredModifier:
        inner = _fixed_modifier(stat, modifier.modifier)
        return UNSUPPORTED if inner is UNSUPPORTED else (modifier.effect_layer, inner)
    cls = type(modifier)
    if stat in (STAT_POWER, STAT_TOUGHNESS):
        if cls in (AddIntModifier, MultiplyIntModifier, SetModifier) and type(modifier.value) is int:
            return cls, modifier.value
    return UNSUPPORTED


def _signature(card, stat, state):
    sources = card.modifier_sources
    if (len(sources) != 3
            or type(sources[0]) is not ContinuouosEffectModifierSource
            or type(sources[1]) is not CounterModifierSource
            or type(sources[2]) is not AttachedModifierSource):
        return UNSUPPORTED
    if ("get_modifiers" in sources[0].__dict__ or "get_modifiers" in sources[1].__dict__
            or "get_modifiers" in sources[2].__dict__ or "_get_cont_effect" in sources[0].__dict__):
        return UNSUPPORTED
    parts = []
    for key in sources[0].active_cont_effects:
        effect = state.get_cont_effect(key)
        if type(effect) is not ContinuousEffect or type(effect.definition) is not ContinuousEffectDefinition:
            return UNSUPPORTED  # Includes stale memberships; general reader cleans them.
        if type(effect.modifiers) not in (dict, immutabledict, ImmutableDict):
            return UNSUPPORTED
        values = effect.modifiers.get(stat)
        if values is None:
            continue
        if type(values) not in (tuple, list):
            return UNSUPPORTED  # Do not consume a callback-backed or one-shot iterable.
        for mod in values:
            fixed = _fixed_modifier(stat, mod)
            if fixed is UNSUPPORTED:
                return UNSUPPORTED
            layer = modifier_layer(stat, mod)
            if effect.state.active_layers is not None and layer not in effect.state.active_layers:
                continue
            parts.append((fixed, effect.definition.created_at, effect.state.timestamp_order,
                          effect.key, frozenset(effect.state.inferred_dependencies.get(
                              layer, effect.definition.depends_on))))
    counters = sources[1].counters
    for kind in (registry.STAT_TO_COUNTERS.get(stat, ()) if counters else ()):
        if kind not in counters or kind not in registry.TYPE_TO_COUNTER:
            continue
        counter = registry.TYPE_TO_COUNTER[kind]
        amount = counters[kind]
        if type(counter) not in (PlusCounter, MinusCounter) or vars(counter) or type(amount) is not int:
            return UNSUPPORTED
        # Counter plugins can change the declared affected stats; keep that
        # case live instead of assuming every registry entry emits a modifier.
        if stat not in (STAT_POWER, STAT_TOUGHNESS):
            return UNSUPPORTED
        parts.append((type(counter), kind, amount, state.turn.number, state.turn.phase))
    if sources[2].attached:
        for attachment in sources[2].attached.values():
            if (type(attachment) is not type(card)
                    or "modifiers_to_attach" in attachment.__dict__
                    or not only_empty_builtin_sources(attachment.modifier_sources)):
                return UNSUPPORTED
            grant = attachment.stats.get(STAT_ATTACH_MODS)
            if (type(grant) not in (Stat, ModifiableReferenceStat)
                    or type(grant.base_value) not in (dict, immutabledict, ImmutableDict)):
                return UNSUPPORTED
            values = grant.base_value.get(stat)
            if values is None:
                continue
            if type(values) not in (tuple, list):
                return UNSUPPORTED
            for mod in values:
                fixed = _fixed_modifier(stat, mod)
                if fixed is UNSUPPORTED:
                    return UNSUPPORTED
                parts.append((fixed, attachment.last_attach_at, attachment.attachment_order))
    return tuple(parts)


def read_native_value(card, stat_key, stat, state, reader):
    """Return a proven current value or UNSUPPORTED, without refreshing indexes.

Mutable effect maps, counter registries and attachment grants are examined on
each read, so in-place edits and rollback do not rely on missing notifications.
Signatures contain only inputs of this characteristic. Type/controller reads
retain the ordinary evaluator: their signature checks did not pay off in games.
"""
    if (state._layer_ceiling is not None
            or (state._refreshing_effects and not getattr(state, "_reusing_continuous_graph", False))):
        return UNSUPPORTED
    signature = _signature(card, stat_key, state)
    if signature is UNSUPPORTED:
        return UNSUPPORTED
    if not signature:
        return stat.base_value
    if type(stat.base_value) is not int:
        return UNSUPPORTED
    entries = card.__dict__.get("_characteristic_values")
    entry = entries.get(stat_key) if entries is not None else None
    if entry is not None and entry[0] is stat and entry[1] == signature:
        return entry[2]
    value = reader(card, stat_key, state)
    # Shallow card projections can share the old dictionary. Replace it rather
    # than mutating it, so a projection cannot edit another runtime's cache.
    card.__dict__["_characteristic_values"] = {
        **(entries or {}), stat_key: (stat, signature, value),
    }
    return value
