"""Reuse LKI and index projections when their characteristic inputs are invariant."""
from dataclasses import dataclass
from immutabledict import immutabledict
from helper.mutability_objs import ImmutableSet
from helper.runtime_object import ImmutableKeyedCollection
from .modifier import only_empty_builtin_sources
from .stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from .card import Card, CardDefinition
from .lki_signature import local_lki_signature
from ..game_actions.data_structs.ability import AbilityDefinition
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
    signature: object = None


class CardSnapshotCache:
    """Separate LKI/index projections, bounded by the state's registered cards.

    Custom/dynamic cards always use the supplied full reader. Unmodified
    cards depend only on an immutable stat table, definition and incarnation.
    LKI also accepts proven constant counter/attachment bonuses, keyed by
    their live inputs. Index projections keep their stricter eligibility.
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
    def definition_index_signature(definition):
        """Read the live zone/mana metadata relevant to index membership."""
        cls = type(definition)
        if (cls.__getattribute__ is not object.__getattribute__
                or hasattr(cls, "__getattr__")
                or type(getattr(cls, "is_mana_ability", None)) is not bool
                or type(getattr(cls, "allowed_zones", None)) not in (type(None), frozenset)):
            return None
        if (getattr(definition.is_usable_in_zone, "__func__", None)
                is not AbilityDefinition.is_usable_in_zone):
            return None
        zones, mana = definition.allowed_zones, definition.is_mana_ability
        if type(zones) is not frozenset or type(mana) is not bool:
            return None
        return zones, mana

    @staticmethod
    def _index_definition_signature(card, validation_cache=None):
        """Describe only ability data used by the query indexes.

        Subclasses may change casting validation without changing zone
        membership. Accept the inherited zone predicate, but evaluate its
        inputs on every reuse: even class-level mana flags can change.
        Overridden predicates and mutable zone sets retain the full reader.
        """
        signature = []
        for key in (STAT_ABILITIES, STAT_TRIGGERS):
            for definition in card.stats[key].base_value.values():
                identity = id(definition)
                cached = validation_cache.get(identity) if validation_cache is not None else None
                if cached is not None and cached[0] is definition:
                    part = cached[1]
                    if part is None:
                        return None
                    signature.append(part)
                    continue
                part = CardSnapshotCache.definition_index_signature(definition)
                if part is None:
                    return None
                signature.append(part)
                if validation_cache is not None:
                    validation_cache[identity] = (definition, part)
        return tuple(signature)

    def capture(self, state, card, reader):
        return self._capture(state, card, reader, self._entries)

    def index_characteristics(self, state, card, reader):
        """Cache only derived index fields; mutable runtime fields stay live."""
        return self._capture(state, card, reader, self._index_entries,
                             signature_reader=self._index_definition_signature)

    def has_immutable_inputs(self, card):
        """Reuse structural validation of an unchanged immutable stat table."""
        entry = self._index_entries.get(id(card))
        return ((entry is not None and entry.card is card and entry.stats is card.stats)
                or self._immutable_inputs(card))

    @staticmethod
    def _eligible(state, card):
        return (type(card) is Card and type(card.definition) is CardDefinition
                and state.card_register.get_by_key(card.key) is card
                and only_empty_builtin_sources(card.modifier_sources)
                and not card.state.counters)

    @staticmethod
    def _matches(entry, card):
        return (entry is not None and entry.card is card and entry.stats is card.stats
                and entry.definition is card.definition and entry.zone == card.get_zone()
                and entry.revision == card.zone_revision)

    def has_current_index_projection(self, state, card, validation_cache=None):
        entry = self._index_entries.get(id(card))
        return (self._eligible(state, card) and self._matches(entry, card)
                and entry.signature == self._index_definition_signature(card, validation_cache))

    def _capture(self, state, card, reader, entries, signature_reader=None):
        if not self._eligible(state, card):
            if entries is self._entries:
                return self._capture_modified(state, card, reader)
            return reader(state, card)
        identity = id(card)
        entry = entries.get(identity)
        if self._matches(entry, card) and (
            entry.signature == (signature_reader(card) if signature_reader else None)
        ):
            return entry.snapshot
        snapshot = reader(state, card)
        immutable = self._immutable_inputs(card)
        signature = signature_reader(card) if immutable and signature_reader else None
        if immutable and (signature_reader is None or signature is not None):
            entries[identity] = _Entry(card, card.stats, card.definition,
                                      card.get_zone(), card.zone_revision, snapshot, signature)
        else:
            entries.pop(identity, None)
        return snapshot

    def _capture_modified(self, state, card, reader):
        if (type(card) is not Card or type(card.definition) is not CardDefinition
                or state.card_register.get_by_key(card.key) is not card):
            return reader(state, card)
        signature = local_lki_signature(state, card)
        if signature is None:
            return reader(state, card)
        identity = id(card)
        entry = self._entries.get(identity)
        if self._matches(entry, card) and entry.signature == signature:
            return entry.snapshot
        snapshot = reader(state, card)
        if self.has_immutable_inputs(card):
            self._entries[identity] = _Entry(
                card, card.stats, card.definition, card.get_zone(),
                card.zone_revision, snapshot, signature,
            )
        else:
            self._entries.pop(identity, None)
        return snapshot

    def discard(self, card):
        self._entries.pop(id(card), None)
        self._index_entries.pop(id(card), None)
        state = getattr(card, "_game_state", None)
        if state is not None:
            state.card_register.invalidate_layer_inputs(card)
