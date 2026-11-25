"""Prove when the existing layer application graph can be reused.

Only fixed-value modifiers and explicit target dependencies are supported.
Unknown effects keep full layer evaluation. Index projection is still performed
for every dirty/dynamic candidate even when target/order evaluation is reused.
"""
from .modifier import (ContinuousEffect, ContinuousEffectDefinition,
                       DynamicTargetingStrategy, StaticTagetingStrategy,
                       AddIntModifier, MultiplyIntModifier, SetModifier,
                       AddSetModifier, RemoveSetModifier)
from .layers import LayeredModifier
from .card import Card
from .layer_index_plan import modified_stats
from ..target.continuous_targets import CardIncarnationsSpec, SourceControllerLifeSpec
from ..stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_KEYWORDS, STAT_STATIC_ABILITIES


def _modifier_signature(stat, modifier):
    if type(modifier) is LayeredModifier:
        inner = _modifier_signature(stat, modifier.modifier)
        return (LayeredModifier, modifier.effect_layer, inner) if inner is not None else None
    if (stat in (STAT_POWER, STAT_TOUGHNESS)
            and type(modifier) in (AddIntModifier, MultiplyIntModifier, SetModifier)
            and type(modifier.value) is int):
        return type(modifier), modifier.value
    if (stat == STAT_KEYWORDS and type(modifier) in (AddSetModifier, RemoveSetModifier, SetModifier)
            and type(modifier.value) is frozenset
            and all(type(value) is str for value in modifier.value)):
        return type(modifier), modifier.value
    return None


def application_signature(state, effects):
    """Describe a proven-independent graph, or None for the general path.

Supported modifiers cannot alter zone/incarnation, controller, life, or static
ability existence. Thus they cannot change the supported target dependencies
or acquire new inferred ordering dependencies between one another.
"""
    if getattr(state, "_projected_cards", None) is not None:
        return None
    result = []
    checked_sources = {}
    for effect in effects:
        if type(effect) is not ContinuousEffect or type(effect.definition) is not ContinuousEffectDefinition:
            return None
        definition = effect.definition
        source = definition.source
        if type(source) is not Card:
            return None
        if source not in checked_sources:
            checked_sources[source] = not source.state.attached and modified_stats(state, source) is not None
        if not checked_sources[source]:
            return None
        targeting = definition.targeting
        if type(targeting) not in (DynamicTargetingStrategy, StaticTagetingStrategy):
            return None
        spec = targeting.target_spec
        if type(spec) is CardIncarnationsSpec:
            if any(type(card) is not Card for card, _ in spec.incarnations):
                return None
        elif type(spec) is not SourceControllerLifeSpec:
            return None
        modifiers = []
        for stat, values in effect.modifiers.items():
            for modifier in values:
                signature = _modifier_signature(stat, modifier)
                if signature is None:
                    return None
                modifiers.append((stat, signature))
        targets = spec.dependency_targets(source, source.get_controller(state), state)
        exists = (definition.static_ability_key is None
                  or definition.static_ability_key in source.get_stat(STAT_STATIC_ABILITIES, state))
        # Keep references as well as IDs so identity reuse cannot match an old
        # plan, and compare incarnation revisions even if memberships coincide.
        result.append((id(effect), effect, definition.created_at, frozenset(definition.depends_on),
                       definition.characteristic_defining, effect.state.timestamp_order,
                       tuple(modifiers), targets, exists,
                       tuple(sorted((id(card), card.zone_revision) for card in targets))))
    return tuple(result)


def graph_signature(effects):
    """Detect external edits to memberships/layers before reusing a plan."""
    return tuple((frozenset(effect.state.currently_affected),
                  None if effect.state.active_layers is None else frozenset(effect.state.active_layers),
                  tuple((key, frozenset(value)) for key, value in effect.state.inferred_dependencies.items()),
                  tuple(sorted((id(card), effect.key in card.state.active_cont_effects)
                               for card in effect.state.currently_affected))) for effect in effects)
