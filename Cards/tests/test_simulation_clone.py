"""Fast simulation copies retain graph ownership and reference replay behavior."""
from functools import partial
from random import Random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from game.ai.runtime_clone import RuntimeClone, DictionaryCopyPlans
from game.ai.simulation_clone import SimulationClone


@pytest.mark.parametrize("copier", [RuntimeClone, SimulationClone])
def test_cycles_runtime_keys_callbacks_and_primitive_replacements(copier):
    owner = SimpleNamespace(events=[])
    def callback(value, default=owner):
        owner.events.append(value)
        assert default is owner
    cycle = []
    pair = (owner, cycle)
    cycle.append(pair)
    source = {"owner": owner, "cycle": pair, "callback": callback,
              "append": owner.events.append, "marker": 987654321}
    clone = copier({id(source["marker"]): 7}).copy(source)
    assert clone["marker"] == 7
    assert clone["cycle"][1][0] is clone["cycle"]
    assert clone["cycle"][0] is clone["owner"]
    clone["callback"]("a")
    clone["append"]("b")
    assert clone["owner"].events == ["a", "b"] and not owner.events


@pytest.mark.parametrize("copier", [RuntimeClone, SimulationClone])
def test_sets_rebind_hashable_runtime_keys_and_preserve_backreferences(copier):
    class Member:
        pass
    item = Member()
    item.members = frozenset({item})
    result = copier().copy(({item: item.members}, {item}, item.members))
    member = next(iter(result[0]))
    assert member is not item and member.members is result[2]
    assert result[0][member] is result[2]
    assert next(iter(result[1])) is member


def test_fast_copy_obeys_native_custom_serialization_and_slots():
    class NativeExtension:
        __module__ = "game.extension"
        __slots__ = ("items",)
        def __getstate__(self):
            return self.items
        def __setstate__(self, state):
            self.items = state
    value = NativeExtension()
    value.items = [value]
    other = SimulationClone().copy(value)
    assert type(other) is NativeExtension and other is not value
    assert other.items is not value.items and other.items[0] is other


def test_native_component_mapping_keeps_cycles_attributes_and_runtime_members():
    from game.game_state.battlefield import CardCollection
    from helper.runtime_object import ImmutableKeyedCollection
    from test_player_observation import board, add
    state = board()
    card = add(state, 'Member')
    zone = CardCollection([card])
    frozen = ImmutableKeyedCollection({card.key: card})
    zone.metadata = {'zone': zone, 'frozen': frozen}
    # Copy must not execute a mutation hook against either the source or copy.
    def forbidden():
        raise AssertionError('copy executed gameplay write hook')
    zone._cost_before_write = forbidden
    copied_zone, copied_frozen, copied_card, attrs = SimulationClone().copy((zone, frozen, card, zone.__dict__))
    assert type(copied_zone) is CardCollection
    assert type(copied_frozen) is ImmutableKeyedCollection
    assert copied_zone[card.key] is copied_frozen[card.key] is copied_card
    assert copied_card is not card
    assert copied_zone.metadata['zone'] is copied_zone
    assert copied_zone.metadata['frozen'] is copied_frozen
    assert attrs is copied_zone.__dict__
    assert copied_zone.__dict__ is not zone.__dict__


def test_native_container_subclasses_keep_their_serialization_contract():
    from game.game_state.battlefield import CardCollection
    from immutabledict import immutabledict
    class Extension(CardCollection):
        def __reduce_ex__(self, protocol):
            return (type(self), (), {'restored': True})
    class FrozenExtension(immutabledict):
        def __reduce_ex__(self, protocol):
            return (type(self), (), {'restored': True})
    for cls in (Extension, FrozenExtension):
        result = SimulationClone().copy(cls())
        assert type(result) is cls and result.restored


def test_fast_copy_keeps_live_mapping_proxies_and_tracked_callbacks():
    from helper.dict_helper import MutableCounterMapping, MutableSetMapping
    from helper.change_tracking import TrackedDict, TrackedSet
    events = []
    values = (MutableCounterMapping({"a": 1}), MutableSetMapping({"a": {1}}),
              TrackedDict(on_change=lambda: events.append("dict")),
              TrackedSet(on_change=lambda: events.append("set")), events)
    a, b, c, d, copied_events = SimulationClone().copy(values)
    a.add_pair("a", 2)
    b.mutate_pair("a", {2}, set.update)
    c["x"] = 1
    d.add(1)
    assert dict(a) == {"a": 3} and dict(values[0]) == {"a": 1}
    assert dict(b) == {"a": {1, 2}} and dict(values[1]) == {"a": {1}}
    assert copied_events == ["dict", "set"] and not events
    assert not values[2] and not values[3]


def test_fast_copy_reuses_only_unchanged_frozen_members_and_honors_recipes():
    shared = frozenset({"stable", 3})
    source = {"constant": shared, "mutable": []}
    plans = DictionaryCopyPlans({id(shared): shared})
    first = SimulationClone({id(shared): shared}, dictionary_plans=plans).copy(source)
    plans.collecting = False
    second = SimulationClone({id(shared): shared}, dictionary_plans=plans).copy(source)
    assert first["constant"] is second["constant"] is shared
    first["mutable"].append(1)
    assert not source["mutable"] and not second["mutable"]


def test_object_replacements_prove_primitive_identity_without_sharing_mutable_graphs():
    original, other = SimpleNamespace(items=[]), SimpleNamespace(items=[])
    source = {'original': original, 'unchanged': (123456789, 'text', None, True), 'items': []}
    source['cycle'] = source
    clone = SimulationClone(object_replacements=((original, other),))
    assert clone._primitive_identity
    result = clone.copy(source)
    assert result['original'] is other and result['unchanged'] is source['unchanged']
    assert result['cycle'] is result
    result['items'].append(1)
    assert not source['items']


@pytest.mark.parametrize('explicit_objects', [False, True])
def test_object_proof_falls_back_when_any_primitive_is_actually_replaced(explicit_objects):
    marker = 123456789
    objects = ((marker, 7),) if explicit_objects else ()
    replacements = {} if explicit_objects else {id(marker): 7}
    clone = SimulationClone(replacements, object_replacements=objects)
    assert not clone._primitive_identity
    assert clone.copy({marker: [marker, (marker,)]}) == {7: [7, (7,)]}


def test_proven_primitive_path_and_warm_recipes_keep_callback_and_runtime_key_ownership():
    class Member:
        pass
    owner = Member()
    owner.values = []
    def callback(value):
        owner.values.append(value)
    source = {'owner': owner, 'callback': callback, 'keyed': {owner: owner.values}, 'constant': 19}
    owner.root = source
    plans = DictionaryCopyPlans()
    first = SimulationClone(object_replacements=(), dictionary_plans=plans).copy(source)
    plans.collecting = False
    second = SimulationClone(object_replacements=(), dictionary_plans=plans).copy(source)
    first['callback'](3)
    assert first['owner'].values == [3] and not owner.values and not second['owner'].values
    for row in (first, second):
        assert next(iter(row['keyed'])) is row['owner']
        assert row['keyed'][row['owner']] is row['owner'].values
        assert row['owner'].root is row


@pytest.mark.parametrize("checkpoints", [False, True])
@pytest.mark.parametrize("seed", [7, 31])
@pytest.mark.parametrize("reuse", [False, True])
def test_fast_world_edges_match_generic_copy_with_sibling_branches(checkpoints, seed, reuse):
    from game.ai import simulation_world, checkpoint_simulator
    from game.ai.current_position_worlds import _PreparedWorld, _Snapshot
    from game.ai.search_simulator import SearchSimulator
    from game.ai.checkpoint_simulator import CheckpointSimulator
    from game.simulation.search_config import search_generation_policy
    from test_current_position_worlds import position, prepare
    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    reference_copy = partial(simulation_world.copy_world, fast_copy=False)
    copy_root = _Snapshot.copy_state
    def reference_root(root):
        return copy_root(root, fast_copy=False)
    def run():
        factory = _PreparedWorld(snapshot, seed, reuse=reuse)
        options = dict(track_information=True, policy_for=search_generation_policy, max_phases=12)
        simulator = (CheckpointSimulator(factory, checkpoint_interval=1, checkpoint_limit=8, **options)
                     if checkpoints else SearchSimulator(factory, **options))
        node = simulator.start()
        nodes = [node]
        rng = Random(9)
        for step in range(25):
            if node.decision is None:
                break
            if step < 5:
                nodes.extend(simulator.advance(node, option) for option in node.choices[:2])
            node = simulator.advance(node, rng.choice(node.choices))
            nodes.append(node)
        return nodes
    initial_rng = live.state.rng.getstate()
    with patch.object(simulation_world, "copy_world", reference_copy), \
         patch.object(checkpoint_simulator, "_copy_world", reference_copy), \
         patch.object(_Snapshot, "copy_state", reference_root):
        reference = run()
    with patch.object(_Snapshot, "copy_state", lambda root: copy_root(root, fast_copy=True)):
        assert run() == reference
    assert live.state.rng.getstate() == initial_rng
