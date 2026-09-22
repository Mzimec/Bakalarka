"""Reuse LKI and index projections when their characteristic inputs are invariant."""
from dataclasses import dataclass
from immutabledict import immutabledict
from helper.mutability_objs import ImmutableSet
from helper.runtime_object import ImmutableKeyedCollection
from .modifier import only_empty_builtin_sources
from .stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..stat_type import (STAT_TYPES, STAT_SUBTYPES, STAT_COLORS, STAT_KEYWORDS,
                         STAT_ABILITIES, STAT_TRIGGERS, STAT_POWER, STAT_TOUGHNESS,
                         STAT_CONTROLLER, STAT_INTRINSIC_MANA)


@dataclass(frozen=True)
class _Entry:
    card: object
    stats: object
    definition: object
    zone: object
    revision: int
    snapshot: object


class CardSnapshotCache:
    """Separate LKI/index projections, bounded by the state's registered cards.

    Modified/custom objects always use the supplied full reader. Unmodified
    cards depend only on an immutable stat table, definition and incarnation.
    Reference comparisons also handle rollback and entry-copy restoration.
    """
    def __init__(self):
        self._entries = {}
        self._index_entries = {}

    @staticmethod
    def _immutable_inputs(card):
        stats = card.stats
        if type(stats) is not immutabledict:
            return False
        for key in (STAT_TYPES, STAT_SUBTYPES, STAT_COLORS, STAT_KEYWORDS,
                    STAT_ABILITIES, STAT_TRIGGERS, STAT_POWER, STAT_TOUGHNESS,
                    STAT_CONTROLLER, STAT_INTRINSIC_MANA):
            stat = stats.get(key)
            if type(stat) not in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat):
                return False
            value = stat.base_value
            if key in (STAT_TYPES, STAT_SUBTYPES, STAT_COLORS, STAT_KEYWORDS):
                if type(value) not in (frozenset, ImmutableSet):
                    return False
            elif key in (STAT_ABILITIES, STAT_TRIGGERS):
                if type(value) is not ImmutableKeyedCollection:
                    return False
            elif key in (STAT_POWER, STAT_TOUGHNESS):
                if value is not None and type(value) is not int:
                    return False
            elif key == STAT_INTRINSIC_MANA and type(value) is not bool:
                return False
        return True

    @staticmethod
    def _index_definitions_are_static(card):
        # Custom definitions may override zone usability with dynamic logic.
        from ..game_actions.data_structs.ability import (
            ActivatedAbilityDefinition, CastSpellAbilityDefinition,
            ManaAbilityDefinition, PlayLandAbilityDefinition,
            TriggerAbilityDefinition, TriggeredManaAbilityDefinition,
        )
        supported = (ActivatedAbilityDefinition, CastSpellAbilityDefinition,
                     ManaAbilityDefinition, PlayLandAbilityDefinition,
                     TriggerAbilityDefinition, TriggeredManaAbilityDefinition)
        return all(type(definition) in supported and type(definition.allowed_zones) is frozenset
                   for key in (STAT_ABILITIES, STAT_TRIGGERS)
                   for definition in card.stats[key].base_value.values())

    def capture(self, state, card, reader):
        return self._capture(state, card, reader, self._entries)

    def index_characteristics(self, state, card, reader):
        """Cache only derived index fields; mutable runtime fields stay live."""
        return self._capture(state, card, reader, self._index_entries,
                             validator=self._index_definitions_are_static)

    @staticmethod
    def _eligible(state, card):
        from .card import Card, CardDefinition
        return (type(card) is Card and type(card.definition) is CardDefinition
                and state.card_register.get_by_key(card.key) is card
                and only_empty_builtin_sources(card.modifier_sources)
                and not card.state.counters)

    @staticmethod
    def _matches(entry, card):
        return (entry is not None and entry.card is card and entry.stats is card.stats
                and entry.definition is card.definition and entry.zone == card.get_zone()
                and entry.revision == card.zone_revision)

    def has_current_index_projection(self, state, card):
        return (self._eligible(state, card)
                and self._matches(self._index_entries.get(id(card)), card))

    def _capture(self, state, card, reader, entries, validator=None):
        if not self._eligible(state, card):
            return reader(state, card)
        identity = id(card)
        entry = entries.get(identity)
        if self._matches(entry, card):
            return entry.snapshot
        snapshot = reader(state, card)
        if self._immutable_inputs(card) and (validator is None or validator(card)):
            entries[identity] = _Entry(card, card.stats, card.definition,
                                            card.get_zone(), card.zone_revision, snapshot)
        else:
            entries.pop(identity, None)
        return snapshot

    def discard(self, card):
        self._entries.pop(id(card), None)
        self._index_entries.pop(id(card), None)
