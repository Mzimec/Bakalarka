"""Owned, per-characteristic plans for immutable native modifier inputs.

The plan is discarded by input mutations, not validated by walking every effect
on every read. Mutable definition maps, callbacks and extensions keep the live
evaluator. Layer probes always use that evaluator as well.
"""
from enum import Enum

from immutabledict import immutabledict

from helper.change_tracking import TrackedDict, TrackedSet
from helper.mutability_objs import ImmutableDict, ImmutableSet
from . import modifier as registry
from .modifier import (
    ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource,
    ContinuousEffect, ContinuousEffectDefinition, ContinuousEffectState, AddIntModifier, MultiplyIntModifier,
    SetModifier, AddSetModifier, RemoveSetModifier, TimeStampedModifier,
    only_empty_builtin_sources,
)
from .layers import LayeredModifier, SwitchPowerToughness, modifier_layer, dependency_order
from .stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
from ..enums import ModifierType
from ..stat_type import STAT_POWER, STAT_TOUGHNESS, STAT_ATTACH_MODS


MISSING = object()
# None survives ownership copies/checkpoints without sentinel rebinding.
UNKNOWN = None
_COUNTER_READERS = {registry.PlusCounter: registry.PlusCounter.get_modifiers,
                    registry.MinusCounter: registry.MinusCounter.get_modifiers}


def fixed_modifier(modifier):
    """Only native immutable transformations can have a persistent result."""
    cls = type(modifier)
    if cls is LayeredModifier:
        return (set(vars(modifier)) == {"modifier", "effect_layer"}
                and fixed_modifier(modifier.modifier))
    if cls is SwitchPowerToughness:
        return not vars(modifier)
    if cls in (AddIntModifier, MultiplyIntModifier):
        return set(vars(modifier)) == {"value"} and type(modifier.value) is int
    if cls is SetModifier:
        return (set(vars(modifier)) == {"value"}
                and type(modifier.value) in (int, bool, str, type(None)))
    if cls in (AddSetModifier, RemoveSetModifier):
        return (set(vars(modifier)) == {"value"}
                and type(modifier.value) in (frozenset, ImmutableSet)
                and all(type(value) is str or issubclass(type(value), Enum) for value in modifier.value))
    return False


def ordered(modifiers, stat):
    groups = {}
    for modifier in modifiers:
        groups.setdefault(modifier_layer(stat, modifier.modifier).value, []).append(modifier)
    return tuple(item.modifier for layer in sorted(groups) for item in dependency_order(
        groups[layer], key=lambda item: item.effect_key or str(id(item)),
        dependencies=lambda item: item.depends_on,
        timestamp=lambda item: (item.time_stamp, item.order),
    ))


class NativeModifierPlan:
    def __init__(self, card, manager):
        self.stats = card.stats
        self.sources = card.modifier_sources
        self.manager_token = manager.change_token
        self.by_stat = {}
        self.values = {}
        self.counter_schema = None
        self.counter_time = None

    def add(self, stat, modifiers):
        if modifiers is UNKNOWN:
            self.by_stat[stat] = UNKNOWN
        elif self.by_stat.get(stat, MISSING) is not UNKNOWN:
            self.by_stat.setdefault(stat, []).extend(modifiers)

    def read(self, card, stat_key, state):
        cached = self.values.get(stat_key, MISSING)
        if cached is not MISSING:
            return cached
        stat = self.stats.get(stat_key)
        if stat is None:
            return None
        if type(stat) not in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat):
            return MISSING
        pipeline = self.by_stat.get(stat_key, ())
        if pipeline is UNKNOWN:
            return MISSING
        if type(stat) is Stat or not pipeline:
            return stat.base_value
        if stat_key in (STAT_POWER, STAT_TOUGHNESS):
            if type(stat.base_value) is not int:
                return MISSING
            if sum(mod.behavior == ModifierType.SWITCH for mod in pipeline) % 2:
                other = STAT_TOUGHNESS if stat_key == STAT_POWER else STAT_POWER
                other_stat = self.stats.get(other)
                other_pipeline = self.by_stat.get(other, ())
                if (other_pipeline is UNKNOWN or type(other_stat) is not ModifiablePrimitiveStat
                        or type(other_stat.base_value) is not int):
                    return MISSING
                value = other_stat.value(state, tuple(mod for mod in other_pipeline
                                                     if mod.behavior != ModifierType.SWITCH))
            else:
                value = stat.value(state, pipeline)
        elif type(stat) is ModifiableReferenceStat and type(stat.base_value) is ImmutableSet:
            value = stat.value(state, pipeline)
        elif type(stat) is ModifiablePrimitiveStat and type(stat.base_value) in (bool, int, str):
            value = stat.value(state, pipeline)
        else:
            return MISSING
        # Shallow entry projections may share the plan; never publish into a
        # different card's input table (the caller checks stats identity).
        self.values[stat_key] = value
        return value


def counter_schema(counters):
    return tuple((kind, type(counter), getattr(type(counter), "get_modifiers", None),
                  tuple(vars(counter).items()) if counter is not None else ())
                 for kind in counters for counter in (registry.TYPE_TO_COUNTER.get(kind),)), tuple(
        (stat, frozenset(kinds)) for stat, kinds in registry.STAT_TO_COUNTERS.items())


def tracked_native_sources(card):
    """Prove once that every membership edit notifies this exact native card."""
    sources = card.modifier_sources
    if (len(sources) != 3 or tuple(type(source) for source in sources) != (
            ContinuouosEffectModifierSource, CounterModifierSource, AttachedModifierSource)
            or any("get_modifiers" in vars(source) for source in sources)
            or "_get_cont_effect" in vars(sources[0])
            or type(sources[0].active_cont_effects) is not TrackedSet
            or type(sources[1].counters) is not TrackedDict
            or type(sources[2].attached) is not TrackedDict
            or sources[0].active_cont_effects is not card.state.active_cont_effects
            or sources[1].counters is not card.state.counters
            or sources[2].attached is not card.state.attached):
        return False
    if any(getattr(container._on_change, "__self__", None) is not card
           or getattr(container._on_change, "__func__", None) is not type(card)._notify_changed
           for container in (sources[0].active_cont_effects, sources[1].counters, sources[2].attached)):
        return False
    return True


def sources_empty(card):
    """Empty native sources are invalidated by writes, not rescanned per stat."""
    entry = card.__dict__.get("_native_empty_sources")
    if entry is not None and entry[0] is card:
        return entry[1]
    empty = only_empty_builtin_sources(card.modifier_sources)
    if tracked_native_sources(card):
        card.__dict__["_native_empty_sources"] = card, empty
    return empty


def live_sources_empty(card):
    """Reuse native ownership proof, but examine contents on every call.

    Index/LKI and ability-map eligibility historically also observe edits to
    exposed backing containers. They must not reuse the notification-only
    empty flag used by characteristic getters.
    """
    entry = card.__dict__.get("_native_empty_sources")
    if entry is not None and entry[0] is card:
        sources = card.modifier_sources
        return (not sources[0].active_cont_effects._values
                and not sources[1].counters.data and not sources[2].attached.data)
    return only_empty_builtin_sources(card.modifier_sources)


def build_plan(card, state):
    sources = card.modifier_sources
    if type(card.stats) is not immutabledict or not tracked_native_sources(card):
        return None
    plan = NativeModifierPlan(card, state._cont_effect_manager)
    for key in sources[0].active_cont_effects:
        effect = state.get_cont_effect(key)
        if (type(effect) is not ContinuousEffect
                or type(effect.definition) is not ContinuousEffectDefinition
                or type(effect.state) is not ContinuousEffectState
                or type(effect.modifiers) not in (immutabledict, ImmutableDict)
                or type(effect.definition.depends_on) is not frozenset
                or (effect.state.active_layers is not None
                    and type(effect.state.active_layers) is not TrackedSet)
                or type(effect.state.inferred_dependencies) is not TrackedDict
                or any(type(dependencies) is not frozenset
                       for dependencies in effect.state.inferred_dependencies.values())):
            return None
        for stat, values in effect.modifiers.items():
            if values is None:
                continue
            if type(values) is not tuple:
                return None
            if not all(fixed_modifier(mod) for mod in values):
                plan.add(stat, UNKNOWN)
                continue
            plan.add(stat, [TimeStampedModifier(
                mod, effect.definition.created_at, effect.state.timestamp_order, effect.key,
                effect.state.inferred_dependencies.get(modifier_layer(stat, mod), effect.definition.depends_on),
            ) for mod in values if effect.state.active_layers is None
                or modifier_layer(stat, mod) in effect.state.active_layers])
    if sources[1].counters:
        plan.counter_schema = counter_schema(sources[1].counters)
        plan.counter_time = state.turn.number, state.turn.phase
        for kind, amount in sources[1].counters.items():
            counter = registry.TYPE_TO_COUNTER.get(kind)
            if (type(counter) not in (registry.PlusCounter, registry.MinusCounter)
                    or vars(counter) or type(amount) is not int
                    or type(counter).get_modifiers is not _COUNTER_READERS[type(counter)]):
                return None
        for stat in registry.STAT_TO_COUNTERS:
            plan.add(stat, sources[1].get_modifiers(stat, state))
    for attachment in sources[2].attached.values():
        if (type(attachment) is not type(card) or attachment._game_state is not state
                or any(name in vars(attachment) for name in (
                    "modifiers_to_attach", "get_stat", "_get_modifier_pipeline"))
                or not only_empty_builtin_sources(attachment.modifier_sources)):
            return None
        grant = attachment.stats.get(STAT_ATTACH_MODS)
        if type(grant) not in (Stat, ModifiableReferenceStat) or type(grant.base_value) is not ImmutableDict:
            return None
        for stat, values in grant.base_value.items():
            if type(values) is not tuple:
                return None
            plan.add(stat, [TimeStampedModifier(mod, attachment.last_attach_at, attachment.attachment_order)
                            for mod in values])
    for stat, modifiers in tuple(plan.by_stat.items()):
        if modifiers is UNKNOWN:
            continue
        plan.by_stat[stat] = (ordered(modifiers, stat) if all(fixed_modifier(item.modifier)
                                                          for item in modifiers) else UNKNOWN)
    # Unrelated characteristics need neither a pipeline nor a repeated stat
    # classification. These are the same base references the live reader returns.
    plan.values.update((key, stat.base_value) for key, stat in card.stats.items()
                       if key not in plan.by_stat
                       and type(stat) in (Stat, ModifiablePrimitiveStat, ModifiableReferenceStat))
    return plan


def read_planned_value(card, stat, state):
    if (state._refreshing_effects or state._layer_ceiling is not None
            or card._game_state is not state
            or state.card_register.get_by_key(card.key) is not card):
        return MISSING
    plan = card.__dict__.get("_native_modifier_plan", MISSING)
    if plan is not MISSING:
        if plan is None:
            return MISSING
        if (plan.stats is not card.stats or plan.sources is not card.modifier_sources
                or plan.manager_token is not state._cont_effect_manager.change_token
                or (plan.counter_schema is not None
                    and (plan.counter_schema != counter_schema(card.state.counters)
                         or plan.counter_time != (state.turn.number, state.turn.phase)))):
            plan = MISSING
    if plan is MISSING:
        plan = build_plan(card, state)
        card.__dict__["_native_modifier_plan"] = plan
    return MISSING if plan is None else plan.read(card, stat, state)
