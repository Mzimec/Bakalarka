"""Bounded reuse of definition structure, never runtime legality or choices.

Only native immutable action nodes are shared. Effect objects remain live
references; changing their key invalidates the graph, while changing their
behavior/amount is observed during normal validation and resolution.
"""
from __future__ import annotations
from typing import Any, Callable
from dataclasses import dataclass
from immutabledict import immutabledict

from ..data_structs.action_node import (
    AndActionNode, OrActionNode, EffectActionNode, ManaActionNode,
    ImmutableEffectToSlotMap,
)
from ..data_structs.ability import SubAbilityDefinition, RuntimeSubAbility
from ..data_structs.effect import Effect
from ...target.target_resolver import TargetSlot
from ...mana.mana_value import ImmutableManaRequirement


_GRAPH_LIMIT = 256
_OPTION_LIMIT = 512
_GRAPHS = {}
_OPTIONS = {}
_SUFFIX_METHODS = {kind: kind.create_with_sufix for kind in
                   (AndActionNode, OrActionNode, EffectActionNode, ManaActionNode)}
_NORMALIZE = RuntimeSubAbility.normalize_effect_map
_USED_SLOTS = RuntimeSubAbility.get_used_slots_in_esmap


def _map_signature(mapping: object) -> tuple[object, ...] | None:
    """!
    @brief Build a compact signature for an immutable effect-to-slot map.
    @param mapping Mapping to inspect.
    @return Signature tuple, or `None` when the mapping is not cache-safe.
    """
    if type(mapping) is not ImmutableEffectToSlotMap or len(mapping) > 64:
        return None
    if any(type(key) is not str or type(slots) is not frozenset
           or any(type(slot) is not str for slot in slots)
           for key, slots in mapping.items()):
        return None
    # Order matters for sequential effects, even though dict equality ignores it.
    return tuple(mapping.items())


def _node_signature(node: object, budget: list[int], fields: list[tuple[object, ...]], nodes: list[tuple[object, type]]) -> tuple[object, ...] | None:
    """!
    @brief Build a cache signature for a supported immutable action-node tree.
    @param node Action node to inspect.
    @param budget Mutable node budget consumed during traversal.
    @param fields Output list of identity-checked fields.
    @param nodes Output list of visited nodes and exact types.
    @return Structural node signature, or `None` when unsafe.
    """
    if node is None:
        return ()
    budget[0] -= 1
    kind = type(node)
    if (budget[0] < 0 or kind not in _SUFFIX_METHODS
            or kind.create_with_sufix is not _SUFFIX_METHODS[kind]
            or "create_with_sufix" in vars(node)):
        return None
    nodes.append((node, kind))
    if kind is EffectActionNode:
        fields.append((node, "effect_map", node.effect_map))
        mapping = _map_signature(node.effect_map)
        return None if mapping is None else (kind, id(node), mapping)
    if kind is ManaActionNode:
        fields.append((node, "mana_req", node.mana_req))
        if type(node.mana_req) is not ImmutableManaRequirement:
            return None
        return kind, id(node), node.mana_req
    if type(node.children) is not tuple:
        return None
    fields.append((node, "children", node.children))
    children = tuple(_node_signature(child, budget, fields, nodes) for child in node.children)
    return None if any(child is None for child in children) else (kind, id(node), children)


@dataclass(frozen=True)
class _Graph:
    """!
    @brief Cached compiled graph plus the identity checks that keep it valid.
    """
    definitions: tuple
    fields: tuple
    nodes: tuple
    effects: tuple
    value: tuple

    def current(self) -> bool:
        # Immutable containers were validated on construction. Comparing their
        # identities avoids rebuilding their signatures on every cache hit.
        # Mutable effect keys and method overrides are still checked live.
        """!
        @brief Check whether all cached graph dependencies are still valid.
        @return True if the cached graph may still be reused.
        """
        return (all(type(part) is SubAbilityDefinition for part in self.definitions)
                and all(type(node) is kind
                        and kind.create_with_sufix is _SUFFIX_METHODS[kind]
                        and "create_with_sufix" not in vars(node)
                        for node, kind in self.nodes)
                and all(getattr(obj, name) is value for obj, name, value in self.fields)
                and all(type(effect).__getattribute__ is object.__getattribute__
                        and type(effect).key is Effect.key and effect.key == key
                        for effect, key in self.effects))


def compiled_graph(subdefs: dict[SubAbilityDefinition, int], build: Callable[[], tuple[Any, ...]]) -> tuple[Any, ...]:
    """!
    @brief Reuse suffixes and lookup maps; mana-cost evaluation stays in compile().
    """
    key = tuple((id(part), count) for part, count in subdefs.items())
    cached = _GRAPHS.get(key)
    if cached is not None and cached.current():
        return cached.value
    fields, nodes, effect_keys = [], [], []
    budget = [64]
    if sum(subdefs.values()) > 32:
        return build()
    for part, count in subdefs.items():
        if (type(part) is not SubAbilityDefinition or type(count) is not int
                or type(part.effects) is not frozenset or type(part.slots) is not frozenset):
            return build()
        fields.extend((part, name, getattr(part, name)) for name in ("action_node", "effects", "slots"))
        node = _node_signature(part.action_node, budget, fields, nodes)
        if node is None:
            return build()
        for effect in part.effects:
            if (not isinstance(effect, Effect) or type(effect).key is not Effect.key
                    or type(effect).__getattribute__ is not object.__getattribute__
                    or type(effect.key) is not str):
                return build()
            effect_keys.append((effect, effect.key))
        if any(type(slot) is not TargetSlot or type(slot.key) is not str for slot in part.slots):
            return build()
        fields.extend((slot, "key", slot.key) for slot in part.slots)
    value = build()
    if len(_GRAPHS) >= _GRAPH_LIMIT:
        del _GRAPHS[next(iter(_GRAPHS))]
    _GRAPHS[key] = _Graph(tuple(subdefs), tuple(fields), tuple(nodes), tuple(effect_keys), value)
    return value


def prepared_effects(subability: RuntimeSubAbility, mapping: object) -> tuple[object, set | None]:
    """!
    @brief Bind definition effects to slots once; target selections remain live.
    Custom normalization/slot hooks preserve their original dispatch. None
    requests deferred slot collection after payment feasibility is checked.
    Return a fresh set so a target strategy cannot mutate cached slots.
    """
    if (type(subability) is not RuntimeSubAbility
            or RuntimeSubAbility.normalize_effect_map is not _NORMALIZE
            or RuntimeSubAbility.get_used_slots_in_esmap is not _USED_SLOTS
            or "normalize_effect_map" in vars(subability)
            or "get_used_slots_in_esmap" in vars(subability)
            or type(subability.effects) is not immutabledict
            or type(subability.slots) is not immutabledict):
        return subability.normalize_effect_map(mapping), None
    if type(mapping) is not ImmutableEffectToSlotMap:
        return subability.normalize_effect_map(mapping), None
    key = (id(subability.effects), id(subability.slots), id(mapping))
    cached = _OPTIONS.get(key)
    if cached is None:
        if _map_signature(mapping) is None:
            return subability.normalize_effect_map(mapping), None
        effects = subability.normalize_effect_map(mapping)
        slots = frozenset(subability.get_used_slots_in_esmap(mapping))
        # Retain maps to prevent identity reuse, but no state/player/target binding.
        cached = (subability.effects, subability.slots, mapping, effects, slots)
        if len(_OPTIONS) >= _OPTION_LIMIT:
            del _OPTIONS[next(iter(_OPTIONS))]
        _OPTIONS[key] = cached
    return cached[3], set(cached[4])
