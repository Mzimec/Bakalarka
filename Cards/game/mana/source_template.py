"""Bounded, state-independent descriptions of ordinary tap-for-mana leaves.

Definitions contain mutable effect objects. Validate their live fields before
reusing a description; never cache activation legality or a runtime source.
"""
from dataclasses import dataclass

from .mana_value import ColoredSymbol, ImmutableManaValue
from ..enums import ManaType
from ..game_actions.card_effects import TapSourceEffect
from ..game_actions.mana_effects import AddManaEffect
from ..game_actions.data_structs.ability import ManaAbilityDefinition, SubAbilityDefinition
from ..game_actions.data_structs.action_node import EffectActionNode, ImmutableEffectToSlotMap


_GET_AMOUNT = AddManaEffect.get_amount
_TO_OPERATIONS = AddManaEffect.to_operations
_TO_ABILITY = ManaAbilityDefinition.to_ability
EMPTY_MANA = ImmutableManaValue()
_LIMIT = 256
_TEMPLATES = {}


def _leaf(parts):
    if type(parts) is not tuple or len(parts) != 1:
        return None
    part = parts[0]
    if (type(part) is not SubAbilityDefinition or part.mana_cost is not None
            or type(part.slots) is not frozenset or part.slots
            or type(part.effects) is not frozenset
            or type(part.action_node) is not EffectActionNode
            or type(part.action_node.effect_map) is not ImmutableEffectToSlotMap
            or any(type(slots) is not frozenset or slots
                   for slots in part.action_node.effect_map.values())):
        return None
    return part


def _signature(costs, effects):
    if any(type(effect) is not TapSourceEffect for effect in costs):
        return None
    values = []
    for effect in effects:
        if (type(effect) is not AddManaEffect or type(effect.amount) is not int
                or type(effect.mana) is not ManaType
                or "get_amount" in effect.__dict__ or "to_operations" in effect.__dict__):
            return None
        values.append((effect.key, effect.mana, effect.amount))
    return tuple(effect.key for effect in costs), tuple(values)


@dataclass(frozen=True)
class ManaSourceTemplate:
    definition: object
    cost_parts: tuple
    action_parts: tuple
    costs: tuple
    effects: tuple
    signature: tuple
    produces: ImmutableManaValue

    def current(self, definition):
        return (definition is self.definition
                and definition.cost_subdefs is self.cost_parts
                and definition.action_subdefs is self.action_parts
                and self.signature == _signature(self.costs, self.effects))


def static_mana_template(definition):
    """Return a validated immutable output, or defer to live compilation."""
    if (type(definition) is not ManaAbilityDefinition
            or definition.subdefs or definition.uses_stack or not definition.is_mana_ability
            or AddManaEffect.get_amount is not _GET_AMOUNT
            or AddManaEffect.to_operations is not _TO_OPERATIONS
            or ManaAbilityDefinition.to_ability is not _TO_ABILITY):
        return None
    identity = id(definition)
    cached = _TEMPLATES.get(identity)
    if cached is not None:
        if cached.current(definition):
            return cached
        del _TEMPLATES[identity]

    cost_part = _leaf(definition.cost_subdefs)
    action_part = _leaf(definition.action_subdefs)
    if cost_part is None or action_part is None:
        return None
    costs, effects = tuple(cost_part.effects), tuple(action_part.effects)
    signature = _signature(costs, effects)
    if signature is None:
        return None
    cost_map = {effect.key: effect for effect in costs}
    action_map = {effect.key: effect for effect in effects}
    cost_keys = tuple(cost_part.action_node.effect_map)
    action_keys = tuple(action_part.action_node.effect_map)
    if (len(cost_keys) != 1 or cost_keys[0] not in cost_map
            or not action_keys or any(key not in action_map for key in action_keys)):
        return None
    produced = {}
    for key in action_keys:
        effect = action_map[key]
        if effect.amount < 0:
            return None
        symbol = ColoredSymbol(frozenset({effect.mana}))
        produced[symbol] = produced.get(symbol, 0) + effect.amount
    if not sum(produced.values()):
        return None
    template = ManaSourceTemplate(definition, definition.cost_subdefs, definition.action_subdefs,
                                  costs, effects, signature, ImmutableManaValue(produced))
    if len(_TEMPLATES) >= _LIMIT:
        del _TEMPLATES[next(iter(_TEMPLATES))]
    # Retaining the definition prevents identity reuse. Eviction bounds memory;
    # entries hold definitions and effects only, never cards, players or states.
    _TEMPLATES[identity] = template
    return template
