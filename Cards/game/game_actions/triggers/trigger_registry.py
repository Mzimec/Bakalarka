"""Incremental native trigger bindings and event-key dispatch snapshots.

Snapshots own eligibility (source/controller), not condition evaluation. Unknown
readers, zone predicates and factories are rediscovered at every capture.
"""
from ..data_structs.ability import TriggerAbilityDefinition, TriggeredManaAbilityDefinition
from .trigger_roster import native_definition, event_key_possible, TriggerBinding
from helper.query_system.query import EqQuery
from helper.query_system.object_register import InMemoryObjectRegister
from ...game_state.registers.card_register import IK_HAS_TRIGGERS


class TriggerRoster(tuple):
    """Immutable sequence API with preselected, order-preserving event buckets."""
    def __new__(cls, bindings):
        result = super().__new__(cls, bindings)
        result._by_event = {}
        return result

    def for_event(self, event):
        # Most positions have very few listeners. Their existing early matcher
        # costs less than constructing a dispatch index; larger rosters amortize
        # one bucket across every event with the same key.
        if len(self) <= 4:
            return self
        if event.key not in self._by_event:
            self._by_event[event.key] = tuple(binding for binding in self
                                              if event_key_possible(binding.definition, event))
        return self._by_event[event.key]


def cacheable(card, state):
    from ...game_state.card import Card
    from ...game_state.modifier import only_empty_builtin_sources
    from ...game_state.stat import ModifiableReferenceStat
    from ...stat_type import STAT_TRIGGERS
    from helper.runtime_object import ImmutableKeyedCollection
    if (type(card) is not Card or card._game_state is not state
            or any(name in vars(card) for name in (
                "get_stat", "get_trigger_defs", "get_zone", "get_controller", "_get_modifier_pipeline"))
            or not only_empty_builtin_sources(card.modifier_sources)):
        return False
    stat = card.stats.get(STAT_TRIGGERS)
    return (type(stat) is ModifiableReferenceStat
            and type(stat.base_value) is ImmutableKeyedCollection
            and all(type(definition) in (TriggerAbilityDefinition, TriggeredManaAbilityDefinition)
                    and native_definition(definition)
                    and type(definition.allowed_zones) is frozenset
                    and "is_usable_in_zone" not in vars(definition)
                    for definition in stat.base_value.values()))


class NativeTriggerRegistry:
    def __init__(self):
        self._bindings = {}
        self._roster = None
        self._has_live_readers = False

    def invalidate(self, card):
        self._bindings.pop(card.key, None)
        self._roster = None

    def remove(self, card):
        self._bindings.pop(card.key, None)
        self._roster = None

    def clear(self):
        self._bindings.clear()
        self._roster = None

    def capture(self, state):
        state.card_register.synchronise()
        if self._roster is not None and not self._has_live_readers:
            return self._roster
        result = []
        live = False
        for card in InMemoryObjectRegister.query(state.card_register, EqQuery(IK_HAS_TRIGGERS, True)):
            bindings = self._bindings.get(card.key)
            if bindings is None:
                zone = card.get_zone()
                bindings = tuple(TriggerBinding(definition, card, card.get_controller(state))
                                 for definition in card.get_trigger_defs(state).values()
                                 if isinstance(definition, TriggerAbilityDefinition)
                                 and definition.is_usable_in_zone(zone))
                if cacheable(card, state):
                    self._bindings[card.key] = bindings
                else:
                    self._bindings.pop(card.key, None)
                    live = True
            result.extend(binding if native_definition(binding.definition)
                          else binding.definition.to_ability(binding.source, binding.controller)
                          for binding in bindings)
        self._has_live_readers = live
        self._roster = TriggerRoster(result)
        return self._roster
