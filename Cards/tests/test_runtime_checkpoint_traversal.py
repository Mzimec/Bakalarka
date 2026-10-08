"""The fast graph traversal retains rollback boundaries and runtime identities."""
from collections import deque
from dataclasses import dataclass
from random import Random
from types import SimpleNamespace

from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint


def test_shared_cyclic_containers_and_rng_are_restored_in_place():
    shared = [1]
    mapping = {"shared": shared}
    members = {1, 2}
    queue = deque([shared, mapping])
    state = SimpleNamespace(mapping=mapping, members=members, queue=queue,
                            alias=shared, rng=Random(17))
    shared.append(state)
    rng_before = state.rng.getstate()
    checkpoint = RuntimeCheckpoint(state)
    shared.clear()
    mapping.clear()
    members.add(3)
    queue.popleft()
    state.alias = []
    state.rng.random()
    state.extra = True
    checkpoint.rollback()
    assert state.alias is shared and shared == [1, state]
    assert mapping == {"shared": shared}
    assert list(queue) == [shared, mapping]
    assert members == {1, 2}
    assert state.rng.getstate() == rng_before
    assert not hasattr(state, "extra")


def test_frozen_wrappers_watch_state_and_runtime_scalar_subclasses_keep_attributes():
    @dataclass(frozen=True)
    class Wrapper:
        state: list
        definition: list

    class RuntimeInt(int):
        pass

    Wrapper.__module__ = RuntimeInt.__module__ = "game.checkpoint_test"
    value = RuntimeInt(3)
    value.values = [1]
    wrapper = Wrapper([value], ["definition"])
    root = SimpleNamespace(wrapper=wrapper)
    checkpoint = RuntimeCheckpoint(root)
    value.values.append(2)
    wrapper.state.clear()
    wrapper.definition.append("outside rollback")
    checkpoint.rollback()
    assert root.wrapper is wrapper and wrapper.state[0] is value
    assert value.values == [1]
    assert wrapper.definition == ["definition", "outside rollback"]


def test_tuple_subclasses_retain_iteration_semantics():
    mutable = [1]

    class CustomTuple(tuple):
        def __iter__(self):
            return iter([mutable])

    checkpoint = RuntimeCheckpoint(SimpleNamespace(values=CustomTuple()))
    mutable.append(2)
    checkpoint.rollback()
    assert mutable == [1]


def test_deep_graph_and_explicit_operation_roots_can_be_watched():
    leaf = []
    root = leaf
    for _ in range(2000):
        root = [root]
    checkpoint = RuntimeCheckpoint(root)
    operation = SimpleNamespace(values=[1])
    checkpoint.watch(operation, root=True)
    leaf.append("tentative")
    operation.values.append(2)
    checkpoint.rollback()
    assert leaf == []
    assert operation.values == [1]


def test_native_index_snapshot_restores_memberships_capacity_aliases_and_nested_boundaries():
    from helper.query_system.object_register import BitSet, SimpleObjectIndexProvider
    provider = SimpleObjectIndexProvider(("zone", "alias"))
    bucket = BitSet(3)
    groups = provider.data
    group = groups["zone"]
    group["hand"] = bucket
    groups["alias"] = group
    bucket.extra = ["before"]
    capacity = bucket._max_elements, bucket._all_bits_mask
    outer = RuntimeCheckpoint(provider)
    bucket.add(1024)
    inner = RuntimeCheckpoint(provider)
    bucket.remove(0)
    bucket.extra.append("tentative")
    group.clear()
    group["stack"] = BitSet(8)
    provider._data = {}
    inner.rollback()
    assert provider.data is groups and groups["zone"] is group
    assert groups["alias"] is group and group["hand"] is bucket
    assert bucket.contains(0) and bucket.contains(1024)
    assert bucket.extra == ["before"]
    outer.rollback()
    assert tuple(bucket) == (0, 1)
    assert (bucket._max_elements, bucket._all_bits_mask) == capacity
    assert tuple(~bucket) == tuple(range(2, capacity[0]))


def test_index_snapshot_preserves_generic_traversal_for_custom_buckets_and_containers():
    from helper.query_system.object_register import BitSet, SimpleObjectIndexProvider

    class CustomBitSet(BitSet):
        pass

    class CustomGroup(dict):
        pass

    class RuntimeInt(int):
        pass

    CustomBitSet.__module__ = RuntimeInt.__module__ = "helper.checkpoint_test"
    provider = SimpleObjectIndexProvider(("native", "custom"))
    custom = CustomBitSet(1)
    custom.extra = [1]
    native = BitSet(2)
    native._bits = RuntimeInt(2)
    native._bits.extra = [2]
    provider.data["native"]["custom"] = custom
    provider.data["native"]["native"] = native
    provider.data["custom"] = CustomGroup(shared=custom, mutable=[3])
    before = RuntimeCheckpoint(provider)
    custom.extra.clear()
    native._bits.extra.clear()
    provider.data["custom"]["mutable"].clear()
    provider.clear()
    before.rollback()
    assert provider.data["native"]["custom"] is custom
    assert custom.extra == [1] and native._bits.extra == [2]
    assert provider.data["custom"]["mutable"] == [3]
