"""Indexes of live cards, using the shared bitset query engine."""

from __future__ import annotations

from typing import TYPE_CHECKING
from contextlib import nullcontext

from helper.query_system.object_register import IndexKey
from .indexed_register import IndexedRegister
from ...enums import CardType, CardSubtype, ZoneType, ActivatableAbilityType
from ..card_changes import CardChange
from ..modifier import only_empty_builtin_sources
from ...stat_type import (STAT_MANA_COST, STAT_TYPES, STAT_SUBTYPES, STAT_TOUGHNESS,
                         STAT_POWER, STAT_CONTROLLER, STAT_ABILITIES, STAT_TRIGGERS,
                         STAT_INTRINSIC_MANA, STAT_ATTACH_MODS)
from ..stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from ...mana.mana_value import (
    ImmutableManaValue, ManaSymbol, DeterministicSymbol, ColoredSymbol,
    GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol, GeneralisedSymbol,
)

if TYPE_CHECKING:
    from ..card import Card

IK_KEY = IndexKey[str]("card.key")
IK_NAME = IndexKey[str]("card.name")
IK_OWNER = IndexKey("card.owner")
IK_CONTROLLER = IndexKey("card.controller")
IK_CMC = IndexKey[int]("card.cmc")
IK_ZONE = IndexKey[ZoneType]("card.zone")
IK_TYPE = IndexKey[CardType]("card.type")
IK_SUBTYPE = IndexKey[CardSubtype]("card.subtype")
IK_POWER = IndexKey[int]("card.power")
IK_TOUGHNESS = IndexKey[int]("card.toughness")
IK_TAPPED = IndexKey[bool]("card.tapped")
IK_ABILITY_KIND = IndexKey[ActivatableAbilityType]("card.ability")

IK_INTRODUCED = IndexKey[int]("card.introduced")
IK_SKIP_UNTAP_PLAYER = IndexKey("card.skip_untap_player")
IK_HAS_DAMAGE = IndexKey[bool]("card.has_damage")
IK_IS_TOKEN = IndexKey[bool]("card.is_token")
IK_KEY_SUFFIX = IndexKey[str]("card.key_suffix")
IK_HAS_TRIGGERS = IndexKey[bool]("card.has_triggers")
IK_STATIC_CONTINUOUS = IndexKey[bool]("card.static_continuous")
IK_STATIC_REPLACEMENT = IndexKey[bool]("card.static_replacement")
IK_ATTACHED = IndexKey[bool]("card.attached")
IK_LEGENDARY = IndexKey[bool]("card.legendary")
IK_HAS_COUNTERS = IndexKey[bool]("card.has_counters")
IK_DYNAMIC_CHARACTERISTICS = IndexKey[bool]("card.dynamic_characteristics")

CARD_INDEX_KEYS = (
    IK_DYNAMIC_CHARACTERISTICS,
    IK_ATTACHED,
    IK_LEGENDARY,
    IK_HAS_COUNTERS,
    IK_INTRODUCED,
    IK_SKIP_UNTAP_PLAYER,
    IK_HAS_DAMAGE,
    IK_IS_TOKEN,
    IK_KEY_SUFFIX,
    IK_HAS_TRIGGERS,
    IK_STATIC_CONTINUOUS,
    IK_STATIC_REPLACEMENT,
    IK_KEY,
    IK_NAME,
    IK_OWNER,
    IK_CONTROLLER,
    IK_CMC,
    IK_ZONE,
    IK_TYPE,
    IK_SUBTYPE,
    IK_POWER,
    IK_TOUGHNESS,
    IK_TAPPED,
    IK_ABILITY_KIND,
)

_CHARACTERISTIC_KEYS = frozenset({
    IK_POWER, IK_TOUGHNESS, IK_CONTROLLER, IK_TYPE, IK_SUBTYPE,
    IK_HAS_TRIGGERS, IK_ABILITY_KIND,
})
_LAYER_KEYS = _CHARACTERISTIC_KEYS | {
    IK_CMC, IK_ZONE, IK_STATIC_CONTINUOUS, IK_STATIC_REPLACEMENT, IK_DYNAMIC_CHARACTERISTICS,
}
_RUNTIME_KEYS = {
    CardChange.TAPPED: frozenset({IK_TAPPED}),
    CardChange.DAMAGE: frozenset({IK_HAS_DAMAGE}),
    CardChange.SKIP_UNTAP: frozenset({IK_SKIP_UNTAP_PLAYER}),
}
_PARTIAL_KEYS = _LAYER_KEYS.union(*_RUNTIME_KEYS.values())
_STAT_INDEXES = {
    STAT_POWER: {IK_POWER, IK_TOUGHNESS},
    STAT_TOUGHNESS: {IK_POWER, IK_TOUGHNESS},
    STAT_CONTROLLER: {IK_CONTROLLER},
    STAT_TYPES: {IK_TYPE, IK_ABILITY_KIND},
    STAT_SUBTYPES: {IK_SUBTYPE, IK_ABILITY_KIND},
    STAT_ABILITIES: {IK_ABILITY_KIND},
    STAT_INTRINSIC_MANA: {IK_ABILITY_KIND},
    STAT_TRIGGERS: {IK_HAS_TRIGGERS},
    STAT_MANA_COST: {IK_CMC},
}


class CardRegister(IndexedRegister):
    """!
    @brief Indexed registry for all live runtime cards.

    Besides the shared query indexes, the register assigns each card a stable
    human-facing command reference such as `c1`. These references are preserved
    for a card identity even if the card is temporarily unregistered.
    """

    def __init__(self, state):
        """!
        @brief Initialize the card register and its command-reference namespace.

        @param state Owning game state.
        """
        super().__init__(
            state,
            CARD_INDEX_KEYS,
            (IK_CMC, IK_POWER, IK_TOUGHNESS, IK_INTRODUCED),
        )
        self._introduction_sequence = 0
        self._introductions = {}
        self._next_command_id = 1
        self._command_cards = {}
        self._assigned_command_ids = {}
        self._reference_owners = {}
        self._layer_read_cards = set()
        from .layer_candidate_register import LayerCandidateRegister
        self.layer_candidates = LayerCandidateRegister(self)

    def validate_new(self, card):
        """!
        @brief Validate that a card can be added to this register.

        In addition to the base entity-key checks, normal card keys may not
        collide with command references that have already been assigned.

        @param card Runtime card being registered.
        @throws ValueError If its key conflicts with an existing card or command ID.
        """
        super().validate_new(card)

        if (
            card.key in self._reference_owners
            and self._reference_owners[card.key] is not card
        ):
            raise ValueError(
                f"Card key '{card.key}' conflicts with an existing command ID."
            )

    def register(self, card):
        """!
        @brief Register a card and assign or restore its stable command reference.

        @param card Runtime card to register.
        """
        self.validate_new(card)
        if id(card) not in self._introductions:
            self._introduction_sequence += 1
            # Retain identity even after unregister; runtime IDs may be recycled.
            self._introductions[id(card)] = (card, self._introduction_sequence)
        super().register(card)
        self.layer_candidates.invalidate(card)

        identity = id(card)

        if identity not in self._assigned_command_ids:
            # Card keys and command IDs share lookup syntax, so skip any
            # generated reference already occupied by a real card key.
            while self.get_by_key(f"c{self._next_command_id}") is not None:
                self._next_command_id += 1

            reference = f"c{self._next_command_id}"
            self._next_command_id += 1

            # Retain the object together with its reference so Python cannot
            # recycle this identity and accidentally inherit another card's ID.
            self._assigned_command_ids[identity] = (card, reference)

        _, reference = self._assigned_command_ids[identity]

        card.command_id = reference
        self._command_cards[reference] = card
        self._reference_owners[reference] = card

    @property
    def registration_cursor(self):
        return self._introduction_sequence

    def introduced_after(self, cursor):
        """Query live new identities without refreshing characteristics during rollback."""
        from helper.query_system.object_register import InMemoryObjectRegister
        from helper.query_system.query import RangeQuery
        return tuple(InMemoryObjectRegister.query(self, RangeQuery(IK_INTRODUCED, min_value=cursor + 1)))

    def get_by_reference(self, reference):
        """!
        @brief Resolve either a command ID or a normal card key.

        Command references take precedence over ordinary entity-key lookup.

        @param reference Command reference or card key.
        @return Matching card, or `None` if no card is registered under it.
        """
        return self._command_cards.get(reference) or self.get_by_key(reference)

    def unregister(self, card):
        """!
        @brief Remove a card from the live register.

        Its historical command-ID assignment is retained so registering the
        same runtime card again restores the same reference.

        @param card Runtime card to unregister.
        """
        super().unregister(card)
        self.layer_candidates.remove(card)
        self._command_cards.pop(card.command_id, None)
        self.state._card_snapshots.discard(card)

    def mark_changed(self, card, indexes=None):
        super().mark_changed(card, indexes)
        if self._by_key.get(card.key) is card:
            self.layer_candidates.invalidate(card)

    def invalidate_layer_inputs(self, card):
        """Reclassify replaced input tables without widening queued index writes."""
        if self._by_key.get(card.key) is card:
            self.layer_candidates.invalidate(card)
            self.state._effects_dirty = True

    def clear(self):
        super().clear()
        self.layer_candidates.clear()
        self._layer_read_cards.clear()

    def mark_layer_changed(self, card, indexes=None):
        """Do not reindex unchanged base-only cards at a layer boundary.

        Actual mutations already queued by notifications are never discarded.
        Custom/modified cards and mutable mana or stack X retain full updates.
        """
        if indexes is not None:
            self.mark_changed(card, indexes)
            return
        if card.key not in self._dirty and self._can_preserve_characteristics(card):
            return
        self.mark_changed(card, _LAYER_KEYS)

    def layer_index_keys(self, card, *, force_general=False):
        """Plan a layer update, keeping unknown cross-stat readers conservative."""
        from ..layer_index_plan import modified_stats
        self._layer_read_cards.discard(card)
        if force_general:
            return _LAYER_KEYS
        stats = modified_stats(self.state, card)
        # Granted abilities can introduce arbitrary zone predicates, while
        # changed attachment grants can affect characteristics of other cards.
        if stats is None or stats & {STAT_ABILITIES, STAT_TRIGGERS, STAT_ATTACH_MODS}:
            return _LAYER_KEYS
        self._layer_read_cards.add(card)
        # New/unmodified cards still need their first complete projection.
        # Otherwise a narrow update would never populate the invariant cache
        # and every subsequent refresh would classify them as dynamic again.
        if not stats and not self.state._card_snapshots.has_current_index_projection(self.state, card):
            return _LAYER_KEYS
        indexes = {IK_DYNAMIC_CHARACTERISTICS}
        for stat in stats:
            indexes.update(_STAT_INDEXES.get(stat, ()))
        # X and runtime mana remain live independently of modifier write sets.
        if card.get_zone() == ZoneType.STACK:
            indexes.add(IK_CMC)
        return frozenset(indexes)

    def needs_layer_refresh(self, card, validation_cache=None):
        """Classify once before a refresh; newly affected targets join later."""
        return card.key in self._dirty or not self._can_preserve_characteristics(card, validation_cache)

    def _can_preserve_characteristics(self, card, validation_cache=None):
        if (self.state._card_snapshots.has_current_index_projection(self.state, card, validation_cache)
                and card.get_zone() != ZoneType.STACK
                and not card.definition.continuous_effects
                and not card.definition.replacement_effects):
            cost = card.stats.get(STAT_MANA_COST)
            if type(cost) in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat):
                value = cost.base_value
                if value is None or (type(value) is ImmutableManaValue and all(
                    type(symbol) in (ManaSymbol, DeterministicSymbol, ColoredSymbol,
                                     GenericSymbol, VariableSymbol, HybridGenericSymbol,
                                     PhyrexianSymbol, GeneralisedSymbol)
                    for symbol in value
                )):
                    return True
        return False

    def mark_runtime_changed(self, card, change):
        self.mark_changed(card, _RUNTIME_KEYS[change])

    def changed_index_values(self, card, indexes):
        from ..card import Card, CardDefinition
        if (indexes is None or type(card) is not Card or type(card.definition) is not CardDefinition
                or not indexes <= _PARTIAL_KEYS):
            return self.index_values(card)
        # Runtime fields may feed a custom modifier or a changed stat table.
        # Their narrow updates are safe only with a current base projection.
        if (IK_DYNAMIC_CHARACTERISTICS not in indexes and not indexes & _CHARACTERISTIC_KEYS
                and not self._can_preserve_characteristics(card)):
            indexes = indexes | _LAYER_KEYS
        return self.partial_index_values(card, indexes)

    def partial_index_values(self, card, indexes):
        """Read only the groups invalidated by notifications/layer evaluation."""
        values = {}
        if indexes & _CHARACTERISTIC_KEYS:
            if _CHARACTERISTIC_KEYS <= indexes:
                characteristics = self.state._card_snapshots.index_characteristics(
                    self.state, card, self._characteristic_index_values,
                )
                values.update(characteristics)
            else:
                values.update(self._characteristic_index_values(self.state, card, indexes))
        if IK_CMC in indexes:
            values[IK_CMC] = frozenset({card.get_mana_value(self.state)})
        if IK_DYNAMIC_CHARACTERISTICS in indexes:
            values[IK_DYNAMIC_CHARACTERISTICS] = frozenset({self._dynamic_characteristics(card)})
        if IK_ZONE in indexes:
            values[IK_ZONE] = frozenset({card.get_zone()})
        if IK_STATIC_CONTINUOUS in indexes:
            values[IK_STATIC_CONTINUOUS] = frozenset({any(card.get_zone() in rule.active_zones for rule in card.definition.continuous_effects)})
        if IK_STATIC_REPLACEMENT in indexes:
            values[IK_STATIC_REPLACEMENT] = frozenset({any(card.get_zone() in rule.active_zones for rule in card.definition.replacement_effects)})
        if IK_TAPPED in indexes:
            values[IK_TAPPED] = frozenset({card.is_tapped})
        if IK_HAS_DAMAGE in indexes:
            values[IK_HAS_DAMAGE] = frozenset({bool(card.state.damage_marked or card.state.damage_by_deathtouch)})
        if IK_SKIP_UNTAP_PLAYER in indexes:
            values[IK_SKIP_UNTAP_PLAYER] = frozenset({card.state.skip_untap[1]}) if card.state.skip_untap is not None else frozenset()
        return values

    @staticmethod
    def _dynamic_characteristics(card):
        # SBA must still inspect custom/state-dependent characteristics even
        # when their last indexed value did not indicate a violation.
        from ..card import Card, CardDefinition
        from helper.mutability_objs import ImmutableSet
        if (type(card) is not Card or type(card.definition) is not CardDefinition
                or not only_empty_builtin_sources(card.modifier_sources)):
            return True
        for key in (STAT_TYPES, STAT_SUBTYPES, STAT_TOUGHNESS):
            stat = card.stats.get(key)
            if type(stat) not in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat):
                return True
            value = stat.base_value
            if key == STAT_TOUGHNESS:
                if value is not None and type(value) is not int:
                    return True
            elif type(value) not in (frozenset, ImmutableSet):
                return True
        return False

    def index_values(self, card: Card):
        """!
        @brief Compute all query-index memberships for the current card state.

        Values are derived through the card's public characteristic accessors so
        continuous effects and controller/type/stat modifications are reflected
        in the index snapshot.

        Optional characteristics such as power and toughness contribute no
        membership when they are not defined.

        @param card Runtime card being indexed.
        @return Mapping from card index keys to immutable membership sets.
        """
        characteristics = self.state._card_snapshots.index_characteristics(
            self.state, card, self._characteristic_index_values,
        )
        # Runtime-only fields do not require rebuilding the characteristic
        # projection. Mana value stays live: X can change while on the stack.
        cmc = card.get_mana_value(self.state)
        return {
            IK_DYNAMIC_CHARACTERISTICS: frozenset({self._dynamic_characteristics(card)}),
            IK_ATTACHED: frozenset({card.attached_to is not None}),
            IK_LEGENDARY: frozenset({card.definition.legendary}),
            IK_HAS_COUNTERS: frozenset({bool(card.state.counters)}),
            IK_INTRODUCED: frozenset({self._introductions[id(card)][1]}),
            IK_SKIP_UNTAP_PLAYER: (frozenset({card.state.skip_untap[1]})
                                   if card.state.skip_untap is not None else frozenset()),
            IK_HAS_DAMAGE: frozenset({bool(card.state.damage_marked or card.state.damage_by_deathtouch)}),
            IK_IS_TOKEN: frozenset({card.is_token}),
            IK_KEY_SUFFIX: frozenset(card.key[index + 1:]
                                     for index, char in enumerate(card.key) if char == "-"),
            IK_STATIC_CONTINUOUS: frozenset({any(card.get_zone() in rule.active_zones
                                                  for rule in card.definition.continuous_effects)}),
            IK_STATIC_REPLACEMENT: frozenset({any(card.get_zone() in rule.active_zones
                                                   for rule in card.definition.replacement_effects)}),
            IK_KEY: frozenset({card.key}),
            IK_NAME: frozenset({card.name.casefold()}),
            IK_OWNER: frozenset({card.owner}),
            IK_ZONE: frozenset({card.get_zone()}),
            **characteristics,
            IK_CMC: frozenset({cmc}),
            IK_TAPPED: frozenset({card.is_tapped}),
        }

    @staticmethod
    def _characteristic_index_values(state, card, indexes=_CHARACTERISTIC_KEYS):
        """Read only fields derived from characteristics/ability zone usability."""
        from ..characteristic_scope import characteristic_scope
        from immutabledict import immutabledict
        scope = (characteristic_scope(state, card)
                 if card in state.card_register._layer_read_cards else nullcontext())
        with scope:
            values = {}
            if IK_POWER in indexes:
                power = card.get_power(state)
                values[IK_POWER] = frozenset() if power is None else frozenset({power})
            if IK_TOUGHNESS in indexes:
                toughness = card.get_toughness(state)
                values[IK_TOUGHNESS] = frozenset() if toughness is None else frozenset({toughness})
            if IK_CONTROLLER in indexes:
                values[IK_CONTROLLER] = frozenset({card.get_controller(state)})
            if IK_TYPE in indexes:
                values[IK_TYPE] = frozenset(card.get_types(state))
            if IK_SUBTYPE in indexes:
                values[IK_SUBTYPE] = frozenset(card.get_subtypes(state))
            if IK_HAS_TRIGGERS in indexes:
                values[IK_HAS_TRIGGERS] = frozenset({any(
                    definition.is_usable_in_zone(card.get_zone())
                    for definition in card.get_trigger_defs(state).values()
                )})
            if IK_ABILITY_KIND in indexes:
                values[IK_ABILITY_KIND] = frozenset(
                    ActivatableAbilityType.MANA if definition.is_mana_ability
                    else ActivatableAbilityType.NON_MANA
                    for definition in card.get_activatable_ability_defs(state).values()
                )
            return immutabledict(values)
