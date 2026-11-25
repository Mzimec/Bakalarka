"""Maintain cards needing layer evaluation without rescanning invariant cards."""
from dataclasses import dataclass, field

from ..card_snapshot_cache import CardSnapshotCache
from ...stat_type import STAT_ABILITIES, STAT_TRIGGERS


@dataclass
class _DefinitionWatch:
    definition: object
    signature: tuple
    cards: dict = field(default_factory=dict)


class LayerCandidateRegister:
    """Dirty and dynamic cards, plus shared definition dependencies.

    All containers belong to the game state and are restored by the ordinary
    runtime checkpoint. Unknown characteristic readers remain dynamic. Stable
    cards are revisited only after a notification or a definition change.
    """

    def __init__(self, register):
        self.register = register
        self._pending = {}
        self._dynamic = {}
        self._definitions = {}
        self._card_definitions = {}
        self.last_classified = 0
        self.last_definitions_checked = 0

    def invalidate(self, card):
        self._pending[card.key] = card

    def _unwatch(self, card):
        for identity in self._card_definitions.pop(card.key, ()):
            watch = self._definitions[identity]
            watch.cards.pop(card.key, None)
            if not watch.cards:
                del self._definitions[identity]

    def _watch(self, card, validation_cache):
        identities = set()
        for key in (STAT_ABILITIES, STAT_TRIGGERS):
            for definition in card.stats[key].base_value.values():
                identity = id(definition)
                identities.add(identity)
                watch = self._definitions.get(identity)
                if watch is None:
                    signature = validation_cache[identity][1]
                    watch = self._definitions[identity] = _DefinitionWatch(definition, signature)
                watch.cards[card.key] = card
        if identities:
            self._card_definitions[card.key] = identities

    def select(self):
        register = self.register
        validation_cache = {}
        self.last_definitions_checked = len(self._definitions)
        # Mutable class-level ability metadata is not a card notification.
        # Check each shared definition once, rather than every library copy.
        for identity, watch in self._definitions.items():
            signature = CardSnapshotCache.definition_index_signature(watch.definition)
            validation_cache[identity] = (watch.definition, signature)
            if signature != watch.signature:
                self._pending.update(watch.cards)
                watch.signature = signature

        checked = self._dynamic | self._pending | register._dirty
        self._pending.clear()
        self.last_classified = len(checked)
        selected = {}
        for key, card in checked.items():
            self._unwatch(card)
            if register._can_preserve_characteristics(card, validation_cache):
                self._dynamic.pop(key, None)
                self._watch(card, validation_cache)
            else:
                self._dynamic[key] = card
                selected[key] = card
            if key in register._dirty:
                selected[key] = card
        # Query results follow live runtime IDs; preserve that same order.
        return tuple(sorted(selected.values(), key=lambda card: card.runtime_id))

    def remove(self, card):
        self._pending.pop(card.key, None)
        self._dynamic.pop(card.key, None)
        self._unwatch(card)

    def clear(self):
        self._pending.clear()
        self._dynamic.clear()
        self._definitions.clear()
        self._card_definitions.clear()
