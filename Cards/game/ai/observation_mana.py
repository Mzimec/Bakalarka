"""Experimental, viewer-independent mana projection reuse in one owned state.

Not used by default: enable only through the benchmark's explicit context.
Only native readers and native immutable symbols qualify. Read the effective
cost/reduction afresh on every lookup; no card identity, visibility decision,
memory, or result of a state-dependent modifier is cached by identity.
"""
from game.enums import ManaType, ZoneType
from game.game_state.card import Card
from game.game_state.stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from game.game_state.modifier import only_empty_builtin_sources
from game.mana.mana_value import (
    ImmutableManaValue, ManaSymbol, DeterministicSymbol, ColoredSymbol,
    GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol,
    format_mana_cost, MANA_LETTERS,
)
from game.stat_type import STAT_GENERIC_COST_REDUCTION, STAT_MANA_COST


_READERS = {name: getattr(Card, name) for name in (
    'get_stat', 'get_mana_cost', 'get_casting_cost', 'get_mana_value', 'get_zone')}
_SYMBOLS = frozenset((ManaSymbol, DeterministicSymbol, ColoredSymbol,
                     GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol))


def native_mana_projection(card, state):
    """!
    @brief Project mana facts through the opt-in guarded reuse path.
    """
    if (type(card) is not Card or any(name in card.__dict__ or getattr(Card, name) is not reader
                                     for name, reader in _READERS.items())):
        return None
    # Match get_casting_cost's refresh before deciding whether a cached result
    # applies. This also handles reductions granted by other permanents.
    state.refresh_continuous_effects()
    # Unknown stat readers can execute extension code. Decide eligibility
    # before calling them, so a rejected optimization adds no extra reads.
    if (not only_empty_builtin_sources(card.modifier_sources)
            or '_get_modifier_pipeline' in card.__dict__
            or any(type(card.stats.get(key)) not in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat)
                   for key in (STAT_MANA_COST, STAT_GENERIC_COST_REDUCTION))):
        return None
    cost = card.get_mana_cost(state)
    if cost is not None and type(cost) is not ImmutableManaValue:
        return None
    reduction = card.get_stat(STAT_GENERIC_COST_REDUCTION, state)
    x = card.state.mana_x if card.get_zone() == ZoneType.STACK else 0
    if type(reduction) is not int or type(x) is not int:
        return None
    items = None if cost is None else tuple(cost.items())
    for symbol, count in items or ():
        if type(symbol) not in _SYMBOLS or type(count) is not int or type(symbol.cmc) is not int:
            return None
        if type(symbol) in (DeterministicSymbol, ColoredSymbol, PhyrexianSymbol):
            if type(symbol.allowed) is not frozenset or any(type(mana) is not ManaType for mana in symbol.allowed):
                return None
        elif type(symbol) is HybridGenericSymbol and type(symbol.mana) is not ManaType:
            return None
        elif type(symbol) is VariableSymbol and type(symbol.name) is not str:
            return None
    # Tuple order matters to the textual cost even when mapping equality holds.
    # Include the formatter registry, so changing notation cannot return old text.
    key = items, reduction, x, tuple(MANA_LETTERS.items())
    cache = state._card_snapshots
    if not hasattr(cache, '_mana_views'):
        cache._mana_views = {}
        cache.mana_view_hits = cache.mana_view_misses = 0
    found = cache._mana_views.get(key)
    if found is not None:
        cache.mana_view_hits += 1
        return found
    result = (format_mana_cost(cost), format_mana_cost(card.get_casting_cost(state)),
              card.get_mana_value(state))
    if len(cache._mana_views) >= 256:
        cache._mana_views.clear()
    cache._mana_views[key] = result
    cache.mana_view_misses += 1
    return result
