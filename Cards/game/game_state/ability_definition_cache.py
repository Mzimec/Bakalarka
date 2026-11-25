"""Reuse unbound ability mappings whose native inputs are immutable.

This caches construction, never activation legality or callback results.
Entries belong to cards and follow the runtime checkpoint/copy ownership graph.
"""
from immutabledict import immutabledict
from helper.mutability_objs import ImmutableSet
from helper.runtime_object import ImmutableKeyedCollection
from .native_modifier_plan import live_sources_empty
from .stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..stat_type import STAT_ABILITIES, STAT_INTRINSIC_MANA, STAT_TYPES, STAT_SUBTYPES
from ..enums import CardType, CardSubtype


_OVERRIDES = frozenset({"get_stat", "get_types", "get_subtypes", "_get_modifier_pipeline", "_build_ability_defs"})
_STAT_CLASSES = (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat)
_CARD = None
_LANDS = None
_RULES = None


def native_ability_definitions(card, state):
    global _CARD, _LANDS, _RULES
    if _CARD is None:
        from .card import Card
        from ..rules import lands
        _LANDS, _CARD = lands, Card
    if (type(card) is not _CARD or card._game_state is not state
            or state._refreshing_effects or state._layer_ceiling is not None
            or not _OVERRIDES.isdisjoint(card.__dict__)
            or not live_sources_empty(card)):
        return card._build_ability_defs(state)
    lands = _LANDS
    if (lands.basic_land_abilities is not lands._NATIVE_ABILITY_BUILDERS[0]
            or lands.mana_ability is not lands._NATIVE_ABILITY_BUILDERS[1]):
        return card._build_ability_defs(state)
    stats = card._stats
    if type(stats) is not immutabledict:
        return card._build_ability_defs(state)
    rules = _RULES
    if (rules is None or rules[0] != lands.BASIC_LAND_MANA
            or rules[1] is not lands.PLAY_LAND_DEFINITION
            or rules[2] != lands.PLAY_LAND_DEFINITION.key):
        rules = _RULES = (lands.BASIC_LAND_MANA.copy(), lands.PLAY_LAND_DEFINITION,
                         lands.PLAY_LAND_DEFINITION.key)
    entry = card.__dict__.get("_ability_definition_cache")
    if entry is not None and entry[0] is stats and entry[1] is rules:
        return entry[2]
    for key in (STAT_ABILITIES, STAT_INTRINSIC_MANA, STAT_TYPES, STAT_SUBTYPES):
        stat = stats.get(key)
        if type(stat) not in _STAT_CLASSES:
            return card._build_ability_defs(state)
        value = stat.base_value
        if key == STAT_ABILITIES:
            supported = type(value) is ImmutableKeyedCollection
        elif key == STAT_INTRINSIC_MANA:
            supported = type(value) is bool
        else:
            kinds = (CardType, str) if key == STAT_TYPES else (CardSubtype, str)
            supported = (type(value) in (frozenset, ImmutableSet)
                         and all(type(item) in kinds for item in value))
        if not supported:
            return card._build_ability_defs(state)
    result = card._build_ability_defs(state)
    if type(result) is ImmutableKeyedCollection:
        # A single replaceable record per card. Mutable globals are compared
        # live; immutable stat replacement/rollback is checked by identity.
        card.__dict__["_ability_definition_cache"] = (stats, rules, result)
    return result
