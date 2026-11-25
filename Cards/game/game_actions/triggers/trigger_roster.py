"""Event-time trigger bindings without allocating execution data prematurely.

Bindings freeze source/controller eligibility, not matching results. Native
bindings are incrementally invalidated by the card register; extensions keep
live discovery at each capture.
"""
from typing import NamedTuple

from ..data_structs.ability import TriggerAbilityDefinition, TriggeredManaAbilityDefinition
from .trigger_condition import EventKeyCondition


class TriggerBinding(NamedTuple):
    definition: TriggerAbilityDefinition
    source: object
    controller: object


def iter_trigger_bindings(state):
    """Share the zone/controller discovery used by roster and public abilities."""
    from helper.query_system.query import EqQuery
    from ...game_state.registers.card_register import IK_HAS_TRIGGERS

    for card in state.query_cards(EqQuery(IK_HAS_TRIGGERS, True)):
        zone = card.get_zone()
        for definition in card.get_trigger_defs(state).values():
            if isinstance(definition, TriggerAbilityDefinition) and definition.is_usable_in_zone(zone):
                yield TriggerBinding(definition, card, card.get_controller(state))


def native_definition(definition):
    """Custom factories retain the original allocation/evaluation path."""
    return (type(definition) in (TriggerAbilityDefinition, TriggeredManaAbilityDefinition)
            and "to_ability" not in vars(definition))


def capture_native_roster(state):
    if "query_cards" in vars(state):
        # Preserve external query adapters as well as ability getter adapters.
        from .trigger_registry import TriggerRoster
        return TriggerRoster(
            binding if native_definition(binding.definition) else binding.definition.to_ability(
                binding.source, binding.controller,
            ) for binding in iter_trigger_bindings(state)
        )
    return state._trigger_registry.capture(state)


def event_key_possible(definition, event):
    """Reject only explicitly declared impossible events before runtime binding.

    Metadata must be declared on the concrete condition class: a subclass may
    override matching to accept additional keys. Custom factories, per-instance
    predicates and intervening-if callbacks keep their original call order.
    """
    if not native_definition(definition) or definition.intervening_if is not None:
        return True
    condition = definition.condition
    if condition is None:
        return False
    attributes = getattr(condition, "__dict__", {})
    if "matches" in attributes or "matches_trigger" in attributes:
        return True
    if type(condition) is EventKeyCondition:
        return event.key == condition.key
    keys = type(condition).__dict__.get("event_keys")
    return type(keys) is not frozenset or event.key in keys
