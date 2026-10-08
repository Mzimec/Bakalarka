"""Prove that a modified card's LKI depends only on local constant inputs.

This is deliberately narrower than the characteristic evaluator. Unknown
sources, continuous effects and ordered/contextual modifiers stay live.
"""
from immutabledict import immutabledict
from helper.mutability_objs import ImmutableDict
from .card import Card
from .modifier import (
    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
    only_empty_builtin_sources, AddIntModifier, AddSetModifier, PlusCounter,
    MinusCounter,
)
from . import modifier as modifiers
from .stat import Stat, ModifiableReferenceStat
from ..enums import CounterType
from ..stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_KEYWORDS, STAT_ATTACH_MODS


_COUNTER_STATS = {
    STAT_POWER: frozenset({CounterType.PLUS_ONE, CounterType.MINUS_ONE}),
    STAT_TOUGHNESS: frozenset({CounterType.PLUS_ONE, CounterType.MINUS_ONE}),
}


def local_lki_signature(state, card):
    """Return an immutable dependency signature, or None for a live read.

Only additive P/T and keyword grants are accepted, so timestamps cannot
change their result. Every mutable container is read again on reuse; neither
in-place edits nor rollback depend on a separate invalidation notification.
The caller must also validate the host's base stats and incarnation.
"""
    if state._refreshing_effects or state._layer_ceiling is not None:
        return None
    sources = card.modifier_sources
    if (len(sources) != 3
            or type(sources[0]) is not ContinuouosEffectModifierSource
            or type(sources[1]) is not CounterModifierSource
            or type(sources[2]) is not AttachedModifierSource
            or sources[0].active_cont_effects
            or sources[1].counters is not card.state.counters):
        return None
    counters = []
    if sources[1].counters:
        # Registries are extensible. Never reuse a native-counter proof after
        # a plugin changes which characteristics counters affect.
        if modifiers.STAT_TO_COUNTERS != _COUNTER_STATS:
            return None
        for kind, amount in sources[1].counters.items():
            if type(kind) is not CounterType or type(amount) is not int:
                return None
            counter = modifiers.TYPE_TO_COUNTER.get(kind)
            if counter is not None and (
                type(counter) not in (PlusCounter, MinusCounter) or vars(counter)
            ):
                return None
            counters.append((kind, amount, type(counter)))
    attachments = []
    for attachment in sources[2].attached.values():
        if (type(attachment) is not Card
                or not only_empty_builtin_sources(attachment.modifier_sources)):
            return None
        stat = attachment.stats.get(STAT_ATTACH_MODS)
        if type(stat) not in (Stat, ModifiableReferenceStat):
            return None
        mapping = stat.base_value
        if type(mapping) not in (dict, immutabledict, ImmutableDict):
            return None
        grants = []
        for key, values in mapping.items():
            if type(values) not in (tuple, list):
                return None
            for value in values:
                if (key in (STAT_POWER, STAT_TOUGHNESS)
                        and type(value) is AddIntModifier and type(value.value) is int):
                    continue
                if (key == STAT_KEYWORDS and type(value) is AddSetModifier
                        and type(value.value) is frozenset
                        and all(type(keyword) is str for keyword in value.value)):
                    continue
                return None
            grants.append((key, tuple(values)))
        attachments.append(tuple(grants))
    return tuple(counters), tuple(attachments)
