"""Owned native-world copies shared by root templates and branch checkpoints."""
from game.ai.runtime_clone import RuntimeClone


def prepare_cow_indexes(state):
    """!
    @brief Opt-in experiment on an OWNED sampled world, never the live game root.

    Call once before repeated copy_world restores. Sampling/visibility filtering
    must already have happened. Runtime cards, RNG and all other mutable state
    continue to use the ordinary ownership copier.
    """
    from helper.query_system.cow_index import CowIndexProvider
    from helper.query_system.object_register import SimpleObjectIndexProvider

    for register in (state.card_register, state.player_register):
        if register is not None and type(register.idx_provider) is SimpleObjectIndexProvider:
            register._idx_provider = CowIndexProvider.capture(register.idx_provider)


def rebind_replacement_instances(state):
    """!
    @brief Retain consumed replacement budgets under copied source identities.
    """
    instances = getattr(state, "_replacement_instances", None)
    if instances is not None:
        state._replacement_instances = {
            (id(effect.source), revision, index): effect
            for (_, revision, index), effect in instances.items()
        }


def immutable_world_values(state):
    """!
    @brief Prove a narrow set of index/stat constants shareable once per template.

    Frozen containers alone are not sufficient: players, callbacks, mutable
    effects, arbitrary definitions and custom subclasses always retain graph copy.
    Only reviewed native value records with recursively immutable fields qualify.
    """
    from dataclasses import fields
    from enum import Enum
    from immutabledict import immutabledict
    from helper.mutability_objs import ImmutableSet, ImmutableSequence, ImmutableDict
    from helper.runtime_object import ImmutableKeyedCollection
    from helper.query_system.object_register import IndexKey
    from game.stat_type import StatType
    from game.game_state.stat import Stat, ModifiablePrimitiveStat, ModifiableReferenceStat
    from game.mana.mana_value import (ImmutableManaValue, DeterministicSymbol, ColoredSymbol,
                                     GenericSymbol, VariableSymbol, HybridGenericSymbol, PhyrexianSymbol)
    records = {IndexKey, StatType, Stat, ModifiablePrimitiveStat, ModifiableReferenceStat,
               DeterministicSymbol, ColoredSymbol, GenericSymbol, VariableSymbol,
               HybridGenericSymbol, PhyrexianSymbol}
    shared, checked = {}, {}

    def visit(value):
        kind, identity = type(value), id(value)
        if kind in (str, int, float, bool, type(None)) or isinstance(value, Enum):
            return True
        if identity in checked:
            return checked[identity]
        checked[identity] = False  # Reject recursive structures conservatively.
        if kind in (tuple, frozenset, ImmutableSet, ImmutableSequence):
            if getattr(value, "__dict__", {}):
                return False
            children = value
        elif kind in (immutabledict, ImmutableManaValue, ImmutableDict, ImmutableKeyedCollection):
            # immutabledict stores a private backing dict and a memoized hash;
            # neither is a user-mutable mapping API. Reject extra runtime data.
            attrs = getattr(value, "__dict__", {})
            if (set(attrs) - {"_dict", "_hash"}
                    or type(attrs.get("_hash")) not in (int, type(None))):
                return False
            children = [item for pair in value.items() for item in pair]
        elif kind in records:
            names = tuple(f.name for f in fields(value))
            if set(value.__dict__) != set(names):
                return False
            children = [getattr(value, name) for name in names]
        else:
            return False
        valid = all([visit(child) for child in children])
        checked[identity] = valid
        if valid:
            shared[identity] = value
        return valid

    for register in (state.card_register, state.player_register):
        if register is None:  # Visibility-filtered roots have no live indexes.
            continue
        for snapshot in register._snapshots.values():
            for key, values in snapshot.items():
                visit(key)
                visit(values)
    cards = (state.card_register.storage.get_all() if state.card_register is not None
             else [card for player in state.players for card in player.get_cards()])
    for card in cards:
        visit(card.stats)
    for card in getattr(state, "_sampled_known_cards", {}).values():
        visit(card.stats)
    return shared


def copy_world(state, knowledge, *, immutable_values=(), dictionary_plans=None,
               fast_copy=True):
    """!
    @brief Copy ownership graph and repair Python-identity indexes, not stable IDs.

    Bitset memberships, runtime IDs, free slots and command/incarnation references
    survive unchanged. Derived caches are reset; actual effects and callbacks are
    copied together so captured runtime references point into the new world.
    """
    from game.game_state.card_snapshot_cache import CardSnapshotCache
    from game.mana.source_cache import StaticManaSourceCache
    from game.game_state.registers.layer_candidate_register import LayerCandidateRegister
    from game.ai.simulation_clone import SimulationClone
    from game.game_actions.triggers.trigger_registry import NativeTriggerRegistry

    replacements = dict(immutable_values)
    objects = [(p.controller, None) for p in state.players if p.controller is not None]
    objects.extend(((state._card_snapshots, CardSnapshotCache()),
                    (state._mana_source_cache, StaticManaSourceCache()),
                    (state._trigger_registry, NativeTriggerRegistry()),
                    (state.card_register.layer_candidates, None)))
    if fast_copy:
        clone = SimulationClone(replacements, object_replacements=objects, dictionary_plans=dictionary_plans)
    else:
        replacements.update((id(old), new) for old, new in objects)
        clone = RuntimeClone(replacements, dictionary_plans=dictionary_plans)
    copied, memories = clone.copy((state, knowledge))
    rebind_replacement_instances(copied)
    register = copied.card_register
    register._introductions = {id(card): (card, seq) for card, seq in register._introductions.values()}
    register._assigned_command_ids = {id(card): (card, ref) for card, ref in register._assigned_command_ids.values()}
    register.layer_candidates = LayerCandidateRegister(register)
    for card in register.storage.get_all():
        register.layer_candidates.invalidate(card)
    return copied, memories


def native_loop():
    """!
    @brief Construct the native resolution loop used by isolated sampled worlds.
    """
    from game.game_loop.game_loop import GameLoop
    from game.game_actions.resolution.action_processor import ActionProcessor
    from game.game_actions.resolution.resolution_engine import ResolutionEngine
    from game.game_actions.resolution.operation_executor import OperationExecutor
    from game.game_actions.resolution.event_bus import EventBus
    return GameLoop(None, ActionProcessor(ResolutionEngine(OperationExecutor(), EventBus())))


