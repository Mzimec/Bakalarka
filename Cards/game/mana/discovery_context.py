"""Share static mana discovery only while advancing one generation iterator.

No context survives a yield to the caller. Register tokens and live dependency
checks also protect nested generators which mutate or roll back game state.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from ..game_state.modifier import only_empty_builtin_sources


@dataclass
class _Entry:
    token: tuple
    cards: tuple
    effects: tuple
    sources: tuple

    def current(self):
        return all(
            card.stats is stats and card.definition is definition
            and card.zone_revision == revision and card.get_zone() == zone
            and only_empty_builtin_sources(card.modifier_sources)
            for card, stats, definition, revision, zone in self.cards
        ) and all(
            effect.key == key and getattr(effect, "mana", None) == mana
            and getattr(effect, "amount", None) == amount
            for effect, key, mana, amount in self.effects
        )


class ManaDiscoveryContext:
    def __init__(self):
        self.entries = {}

    @staticmethod
    def token(state):
        return (state.card_register.change_token, state.player_register.change_token,
                state.time_stamp, state.priority.current_player,
                state._refreshing_effects, state._layer_ceiling)

    def lookup(self, state, player):
        entry = self.entries.get((state, player))
        if entry is not None and entry.token == self.token(state) and entry.current():
            return entry.sources
        return None

    def remember(self, state, player, cards, effects, sources):
        from dataclasses import replace
        from .mana_value import ImmutableManaValue
        # A search must not modify a shared description through mutable mana maps.
        sources = tuple(source if (type(source.produces) is ImmutableManaValue
                                   and type(source.costs) is ImmutableManaValue)
                        else replace(source, produces=ImmutableManaValue(source.produces),
                                     costs=ImmutableManaValue(source.costs)) for source in sources)
        self.entries[state, player] = _Entry(
            self.token(state),
            tuple((card, card.stats, card.definition, card.zone_revision, card.get_zone()) for card in cards),
            tuple((effect, effect.key, getattr(effect, "mana", None), getattr(effect, "amount", None)) for effect in effects),
            sources,
        )
        return sources


_CURRENT = ContextVar("mana_discovery_context", default=None)


def current_discovery_context():
    return _CURRENT.get()


@contextmanager
def mana_discovery_scope():
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    token = _CURRENT.set(ManaDiscoveryContext())
    try:
        yield _CURRENT.get()
    finally:
        _CURRENT.reset(token)


def share_mana_discovery(iterator):
    iterator = iter(iterator)
    try:
        while True:
            with mana_discovery_scope():
                try:
                    option = next(iterator)
                except StopIteration:
                    return
            yield option
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
