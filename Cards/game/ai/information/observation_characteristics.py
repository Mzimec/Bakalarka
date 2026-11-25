"""Reuse six proven native characteristics inside one owned runtime only."""
from game.game_state.card import Card
from game.game_state.native_modifier_plan import live_sources_empty


_READERS = {name: getattr(Card, name) for name in
            ('get_stat', 'get_types', 'get_subtypes', 'get_power', 'get_toughness')}


def native_characteristics(card, state, reader):
    """!
    @brief Reuse six proven native characteristics within an owned runtime.
    """
    if (type(card) is not Card or state._refreshing_effects or state._layer_ceiling is not None
            or '_get_modifier_pipeline' in vars(card)
            or any(name in vars(card) or getattr(Card, name) is not method for name, method in _READERS.items())
            or not live_sources_empty(card)):
        return reader()
    cache = state._card_snapshots
    rows = getattr(cache, '_observation_base_rows', None)
    if rows is None:
        cache._observation_base_rows = rows = {}
    entry = rows.get(id(card))
    if entry is not None and entry[0] is card and entry[1] is card.stats:
        return entry[2]
    if not cache.has_immutable_inputs(card):
        return reader()
    values = reader()
    if len(rows) >= 256:
        rows.clear()
    rows[id(card)] = card, card.stats, values
    return values
