"""Conservative write sets for standard characteristic modifiers.

Unknown readers/modifiers retain the general projection. A plan lives for one
layer refresh only; it is rebuilt when an effect acquires a new target.
"""
from .modifier import (
    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
    ContinuousEffect, SetModifier, AddIntModifier, MultiplyIntModifier,
    AddSetModifier, RemoveSetModifier, AddCollectionModifier, AddManaCostModifier,
    PlusCounter, MinusCounter, TYPE_TO_COUNTER, STAT_TO_COUNTERS,
    only_empty_builtin_sources,
)
from .layers import LayeredModifier, SwitchPowerToughness
from .stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..stat_type import STAT_ATTACH_MODS, STAT_MANA_COST
from ..mana.mana_value import (
    ImmutableManaValue, ManaSymbol, DeterministicSymbol, ColoredSymbol,
    GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol, GeneralisedSymbol,
)


def static_modifier(modifier):
    if type(modifier) is LayeredModifier:
        return static_modifier(modifier.modifier)
    return type(modifier) in (
        SetModifier, AddIntModifier, MultiplyIntModifier, AddSetModifier,
        RemoveSetModifier, AddCollectionModifier, AddManaCostModifier,
        SwitchPowerToughness,
    )


def modified_stats(state, card):
    """Return all possibly modified stat keys, or None for unknown dependencies."""
    from .card import Card, CardDefinition
    from .card_snapshot_cache import CardSnapshotCache
    # An old invariant projection already proved this immutable stat table's
    # shape, even if the card now has modifiers or occupies another zone.
    if (type(card) is not Card or type(card.definition) is not CardDefinition
            or not state._card_snapshots.has_immutable_inputs(card)
            or CardSnapshotCache._index_definition_signature(card) is None):
        return None
    cost = card.stats.get(STAT_MANA_COST)
    if (type(cost) not in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat)
            or (cost.base_value is not None and type(cost.base_value) is not ImmutableManaValue)):
        return None
    if cost.base_value is not None and any(type(symbol) not in (
        ManaSymbol, DeterministicSymbol, ColoredSymbol, GenericSymbol, VariableSymbol,
        HybridGenericSymbol, PhyrexianSymbol, GeneralisedSymbol,
    ) for symbol in cost.base_value):
        return None
    sources = card.modifier_sources
    if tuple(type(source) for source in sources) != (
        ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
    ):
        return None
    mappings = []
    for key in sources[0].active_cont_effects:
        effect = state.get_cont_effect(key)
        if type(effect) is not ContinuousEffect:
            return None
        mappings.append(effect.modifiers)
    result = set()
    for kind in sources[1].counters:
        counter = TYPE_TO_COUNTER.get(kind)
        if counter is not None and type(counter) not in (PlusCounter, MinusCounter):
            return None
        result.update(stat for stat, kinds in STAT_TO_COUNTERS.items() if kind in kinds)
    for attachment in sources[2].attached.values():
        if type(attachment) is not Card or not only_empty_builtin_sources(attachment.modifier_sources):
            return None
        stat = attachment.stats.get(STAT_ATTACH_MODS)
        if type(stat) not in (Stat, ModifiableReferenceStat):
            return None
        mappings.append(stat.base_value)
    for mapping in mappings:
        for stat, modifiers in mapping.items():
            if not all(static_modifier(modifier) for modifier in modifiers):
                return None
            result.add(stat)
    return frozenset(result)
