"""Stable card identities separate from zone membership and per-card state.

The repository belongs to one game/world. Zones store identifiers and resolve
their public Card interface here. It is never shared across sampled worlds.
"""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CardRecordSnapshot:
    """Detached dynamic records for one repository; global game data is separate."""
    _owner_token: object
    _records: object


class CardStates(dict):
    """Canonical key -> CardRuntimeState table for one position."""


class CardRepository(Mapping):
    """Own physical card handles once, independently of their current zones.

    Removed objects remain known identities, like historical command references.
    The live register, not this repository, determines whether a card is in play.
    """
    def __init__(self, cards=(), states=None):
        self._cards = {}
        self._position_token = object()
        self.states = CardStates() if states is None else states
        for card in cards:
            self.add(card)

    def add(self, card):
        previous = self._cards.get(card.key)
        if previous is not None and previous is not card:
            raise ValueError(f'Duplicate card key: {card.key}')
        self._cards[card.key] = card
        self.states[card.key] = card.state

    def __getitem__(self, key):
        return self._cards[key]

    def __iter__(self):
        return iter(self._cards)

    def __len__(self):
        return len(self._cards)

    def _copy_records(self, records):
        from game.ai.simulation.runtime_clone import RuntimeClone
        # Stable handles and their world/owners are context, not snapshot data.
        replacements = {id(self): self}
        for card in self._cards.values():
            replacements[id(card)] = card
            replacements[id(card.owner)] = card.owner
            state = card._game_state
            if state is not None:
                replacements[id(state)] = state
        return RuntimeClone(replacements.items()).copy(records)

    def capture_records(self):
        """Copy per-card data without cloning Card handles, players or State.

        This covers card records only. Zone memberships, stack, events, RNG,
        player resources and extension fields on Card handles are separate.
        """
        return CardRecordSnapshot(self._position_token, self._copy_records(self.states))

    def restore_records(self, snapshot):
        """Restore reusable card data into the same physical card handles."""
        if type(snapshot) is not CardRecordSnapshot or snapshot._owner_token is not self._position_token:
            raise ValueError('Card records belong to another game/world')
        if snapshot._records.keys() != self.states.keys():
            raise ValueError('Restore physical membership before applying card records')
        for card in self._cards.values():
            readers = getattr(card._game_state, '_observation_readers', None)
            if readers is not None and any(view._builder is not None for view in readers.views.values()):
                raise RuntimeError('Cannot restore card records during a live read')
        records = self._copy_records(snapshot._records)
        for key, record in records.items():
            self._cards[key]._state = record
