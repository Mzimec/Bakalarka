"""Compiled native record copies within one never-executed position template.

Only dispatch and attribute-copy setup are reused. Every dynamic record and
container still belongs to its new restoration, with aliases and callback
bindings resolved through the ordinary single ownership memo.
"""
from functools import lru_cache


@lru_cache(maxsize=1)
def record_types():
    """Exact reviewed records; subclasses and custom reducers stay generic."""
    from helper.change_tracking import TrackedDict, TrackedSet
    from game.game_state.card import ZoneInfo, AttachInfo, CardRuntimeState
    from game.game_state.modifier import (ContinuouosEffectModifierSource,
        CounterModifierSource, AttachedModifierSource)
    from game.game_actions.resolution.event_bus import CardLastKnownInformation, GameEvent
    return frozenset((TrackedDict, TrackedSet, ZoneInfo, AttachInfo, CardRuntimeState,
        ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
        CardLastKnownInformation, GameEvent))


class RecordCopyPlan:
    """Copy one native shell and its fields without rediscovering its layout."""
    __slots__ = ('source', 'kind', 'attributes', 'dynamic')

    def __init__(self, source, kind, attributes, dynamic):
        self.source, self.kind = source, kind
        self.attributes, self.dynamic = attributes, dynamic

    def copy(self, clone):
        memo, source, attributes = clone.memo, self.source, self.attributes
        result = object.__new__(self.kind)
        memo[id(source)] = result
        identity = id(attributes)
        if identity in memo:
            object.__setattr__(result, '__dict__', memo[identity])
        else:
            copied = attributes.copy()
            memo[identity] = copied
            object.__setattr__(result, '__dict__', copied)
            # Both shells exist before following any callback or mutable edge.
            for key, item in self.dynamic:
                copied[key] = clone.copy(item)
            clone._originals.append(attributes)
        clone._originals.append(source)
        return result
