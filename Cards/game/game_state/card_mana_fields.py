"""One derived mana-field entry per native card, checked against live inputs."""
from game.mana.mana_value import (
    ImmutableManaValue, ManaValue, GenericSymbol, VariableSymbol, ColoredSymbol,
    HybridGenericSymbol, PhyrexianSymbol, DeterministicSymbol, format_mana_cost,
)
from game.enums import ZoneType, ManaType
from game.stat_type import STAT_GENERIC_COST_REDUCTION
from game.game_state.card import Card, _NATIVE_MANA_READERS


_READERS = _NATIVE_MANA_READERS
_SYMBOLS = (GenericSymbol, VariableSymbol, ColoredSymbol, HybridGenericSymbol,
            PhyrexianSymbol, DeterministicSymbol)


class ManaFields:
    def __init__(self):
        self.version = self.value = None

    def __reduce__(self):
        return type(self), ()

    def __deepcopy__(self, memo):
        result = type(self)()
        memo[id(self)] = result
        return result


def live_fields(card, state):
    return (format_mana_cost(card.get_mana_cost(state)),
            format_mana_cost(card.get_casting_cost(state)), card.get_mana_value(state))


def mana_fields(card, state):
    """Reuse formatting and reduction after reading the effective runtime stats.

No global revision is assumed: mana cost, reduction, symbol data and stack X
are examined on reuse. Unknown symbols/getters retain the public live path.
    """
    if (type(card) is not Card or state._layer_ceiling is not None or state._refreshing_effects
            or any(name in vars(card) or getattr(Card, name) is not reader for name, reader in _READERS.items())):
        return live_fields(card, state)
    state.refresh_continuous_effects()
    cost = card.get_mana_cost(state)
    if cost is None:
        return 'none', 'none', 0
    if type(cost) is not ImmutableManaValue:
        return live_fields(card, state)
    parts = []
    for symbol, count in cost.items():
        if type(symbol) not in _SYMBOLS or type(count) is not int or type(symbol.cmc) is not int:
            return live_fields(card, state)
        values = tuple(vars(symbol).items())
        if any(type(v) not in (str, int, ManaType)
               and not (type(v) is frozenset and all(type(m) is ManaType for m in v))
               for _, v in values):
            return live_fields(card, state)
        parts.append((type(symbol), values, symbol.cmc, count))
    reduction = card.get_stat(STAT_GENERIC_COST_REDUCTION, state)
    x = card.state.mana_x if card.get_zone() == ZoneType.STACK else 0
    if type(reduction) is not int or type(x) is not int:
        return live_fields(card, state)
    version = tuple(parts), reduction, x
    cached = card.__dict__.get('_mana_fields')
    if cached is not None and cached.version == version:
        return cached.value
    reduced = ManaValue(cost)
    amount = min(reduced.generic, reduction)
    if amount > 0:
        reduced.substract_pair(GenericSymbol(), amount)
    value = format_mana_cost(cost), format_mana_cost(reduced), cost.cmc(x)
    # Replace rather than mutate: shallow card projections may share an old entry.
    cached = ManaFields()
    cached.version, cached.value = version, value
    card.__dict__['_mana_fields'] = cached
    return value
