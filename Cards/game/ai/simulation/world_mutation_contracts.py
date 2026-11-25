"""Reviewed native attribute and container ownership for world mutation loans."""

def native_record_type(cls):
    return cls.__module__.startswith(('game.', 'helper.', 'immutabledict'))


def walk_containers(value, *, recursive=False):
    pending, seen = [value], set()
    while pending:
        current = pending.pop()
        if id(current) in seen or type(current) not in (dict, list, set):
            continue
        seen.add(id(current))
        yield current
        if recursive:
            pending.extend(current.values() if type(current) is dict else current)


def container_fields(source):
    """Only these reviewed backing graphs may leave complete restoration."""
    from game.game_actions.game_stack import GameStack, PrioritySystem
    from game.game_loop.game_loop import Turn
    from game.rules.combat import CombatState
    from game.game_state.turn_history import TurnHistory
    from game.ai.information.knowledge import PlayerKnowledge
    from game.game_state.card import Card, CardRuntimeState
    from game.game_state.state import State
    from game.game_state.card_repository import CardRepository
    from game.game_state.registers.indexed_register import IndexedRegister
    from game.game_state.registers.layer_candidate_register import LayerCandidateRegister, _DefinitionWatch
    from game.game_state.registers.effect_register import EffectRegister
    from game.game_state.modifier import ContinuousEffectsManager, ContinuousEffectState
    from helper.query_system.object_register import ObjectStorage, SimpleObjectIndexProvider
    from helper.heap import Heap
    from immutabledict import immutabledict
    from helper.mutability_objs import ImmutableDict
    from helper.runtime_object import ImmutableKeyedCollection
    from game.mana.mana_value import ImmutableManaValue
    cls = type(source)
    if cls is State:
        return ('_deferred_events','replacement_rules','_static_effect_keys'), False, False
    if cls in (immutabledict, ImmutableDict, ImmutableKeyedCollection, ImmutableManaValue):
        return ('_dict',), False, True  # Public immutable API; backing aliases stay eager.
    if cls is GameStack:
        return ('items',), False, False
    if cls is PrioritySystem:
        return ('passed_players',), False, False
    if cls is Turn:
        return (), False, False
    if cls is CombatState:
        return ('attackers', 'blockers', 'blocked', '_identities', '_defenders',
            '_removed_defenders', '_blockers_declared', '_normal_damage_creatures'), False, False
    if cls is TurnHistory:
        return ('_events', '_damage', '_unhashable_damage', '_life_losers'), True, False
    if cls is PlayerKnowledge:
        return ('events', 'seen', 'locations'), False, False
    if cls is Card:
        return ('_incarnation_history',), False, False
    if cls is CardRuntimeState:
        return ('_incarnation_history',), False, False
    if cls is CardRepository:
        return ('_cards', 'states'), False, False
    if isinstance(source, IndexedRegister):
        return ('_by_key', '_snapshots', '_dirty', '_dirty_indexes', '_membership_tokens',
                '_introductions', '_command_cards', '_assigned_command_ids',
                '_reference_owners', '_layer_read_cards'), False, False
    if cls is LayerCandidateRegister:
        return ('_pending', '_dynamic', '_definitions', '_card_definitions'), False, False
    if cls is _DefinitionWatch:
        return ('cards',), False, False
    if cls is EffectRegister:
        return ('_entries',), False, False
    if cls is ObjectStorage:
        return ('_id_to_obj', '_free_ids'), False, False
    if cls is SimpleObjectIndexProvider:
        return ('_data', '_ordered_keys'), True, False
    if cls is ContinuousEffectsManager:
        return ('_continuous_effects', '_time_stamp_bucket', '_moment_heap_map', '_moment_buckets'), True, False
    if cls is ContinuousEffectState:
        return ('currently_affected',), False, False
    if cls is Heap:
        return ('_data',), False, False
    return (), False, False


