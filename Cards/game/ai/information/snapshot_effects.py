"""Project only proven source-local hidden static bindings out of a snapshot.

Native self-only static effects are derived from printed definitions and are
rebuilt on sampled cards. Keep public and resolved effects with their ordering;
arbitrary hidden anchors still fail the ordinary visibility-aware copier.
"""
from copy import copy
from dataclasses import replace


def project_hidden_static_effects(state, visible):
    """!
    @brief Rebuild supported hidden-source static bindings from sampled definitions.
    """
    from game.game_state.modifier import (ContinuousEffectsManager, ContinuousEffect,
        ContinuousEffectDefinition, ContinuousEffectState, AnchoredDuration, DynamicTargetingStrategy)
    from game.game_state.registers.effect_register import EffectRegister
    from game.target.target_spec import SelfSpec
    manager = state._cont_effect_manager
    if type(manager) is not ContinuousEffectsManager:
        return None, frozenset()
    removed = set()
    derived = {}
    for key, effect in manager._continuous_effects.items():
        definition = effect.definition
        source = definition.source
        native_state = (type(effect.state) is ContinuousEffectState
            and not (set(vars(effect.state)) - {"currently_affected", "active_layers",
                "timestamp_order", "inferred_dependencies", "_tracks_native_plan"}))
        if (source in visible and type(effect) is ContinuousEffect
                and type(definition) is ContinuousEffectDefinition
                and native_state
                and type(definition.targeting) is DynamicTargetingStrategy):
            # Membership and inferred layer dependencies are derived caches,
            # not a record of targets selected on resolution. Retain visible
            # memberships so the ordinary layered refresh can detach them, but
            # never copy the actual private targets. The definition, duration,
            # explicit dependencies and timestamp remain authoritative.
            derived[key] = replace(effect, state=ContinuousEffectState(
                set(effect.state.currently_affected) & visible,
                timestamp_order=effect.state.timestamp_order))
        if source is None or source in visible:
            continue
        targeting = definition.targeting
        if (type(effect) is ContinuousEffect and type(definition) is ContinuousEffectDefinition
                and native_state
                and key in state._static_effect_keys and definition.static_ability_key is not None
                and type(definition.duration) is AnchoredDuration and definition.duration.anchor is source
                and type(targeting) is DynamicTargetingStrategy
                and type(targeting.target_spec) is SelfSpec
                and set(vars(targeting.target_spec)) == {"condition"}
                and not definition.depends_on
                and all(obj is source for obj in effect.state.currently_affected)):
            removed.add(key)
    if not removed and not derived:
        return None, frozenset()
    # A shallow projection is never run. RuntimeClone subsequently copies its
    # graph through the same State/Card memo, preserving shared references.
    result = copy(manager)
    result._continuous_effects = {k: derived.get(k, v) for k, v in manager._continuous_effects.items() if k not in removed}
    result.register = EffectRegister()
    for effect in result._continuous_effects.values():
        result.register.add(effect)  # does not mutate effect.state/order
    result._sequence = len(result._continuous_effects)
    return result, frozenset(removed)


def normalize_effect_order(state):
    """!
    @brief Keep relative public order, without importing hidden binding counters.
    """
    manager = state._cont_effect_manager
    effects = sorted(manager._continuous_effects.values(), key=lambda e: e.state.timestamp_order)
    for index, effect in enumerate(effects, 1):
        effect.state.timestamp_order = index
    manager._sequence = len(effects)
