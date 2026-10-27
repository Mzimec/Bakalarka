"""COW memberships must preserve native replay and world ownership."""
from random import Random

import pytest

from helper.query_system.object_register import (
    BitSet, IndexKey, IndexUpdate, RegisterUpdateContext, SimpleObjectIndexProvider,
)
from helper.query_system.cow_index import CowIndexProvider
from game.ai.runtime_clone import RuntimeClone


def update(key, remove=(), add=()):
    return RegisterUpdateContext({key: IndexUpdate(remove, add)})


def test_forks_detach_parent_sibling_and_grandchild_and_preserve_capacity():
    key = IndexKey("number")
    original = SimpleObjectIndexProvider((key,), (key,))
    original.add(300, update(key, add=(1,)))
    original.add(7, update(key, add=(2,)))
    parent = CowIndexProvider.capture(original)
    original.clear()
    a, b = RuntimeClone().copy(parent), RuntimeClone().copy(parent)
    assert a._data[key] is b._data[key] is parent._data[key]
    assert a.get_index_group(key)[1]._max_elements == 512
    a.update(300, update(key, remove=(1,), add=(3,)))
    child = RuntimeClone().copy(a)
    a.remove(300, update(key, remove=(3,)))
    parent.clear()
    assert list(child.get_index_group(key)[3]) == [300]
    assert 3 not in a.get_index_group(key)
    assert list(b.get_index_group(key)[1]) == [300]
    assert list(child.get_ordered_group(key).scope_map(2, 3)) == [2, 3]
    with pytest.raises(TypeError):
        b.get_index_group(key)[1].clear()
    with pytest.raises(TypeError):
        b.get_index_group(key)[1] = BitSet()
    # A query result remains independently mutable.
    result = BitSet(b.get_index_group(key)[1])
    result.remove(300)
    assert b.get_index_group(key)[1].contains(300)


def test_runtime_keys_rebind_even_when_added_after_capture():
    class Owner:
        pass
    owner = Owner()
    key = IndexKey("owner")
    provider = CowIndexProvider.capture(SimpleObjectIndexProvider((key,)))
    provider.add(2, update(key, add=(owner,)))
    owner.provider = provider  # Include a cycle through a runtime key.
    other, copied = RuntimeClone().copy((owner, provider))
    assert other is not owner and other.provider is copied
    assert owner not in copied.get_index_group(key)
    assert copied.get_index_group(key)[other].contains(2)


def test_shared_pages_allow_independent_worker_writes():
    from concurrent.futures import ThreadPoolExecutor
    key = IndexKey("worker")
    original = SimpleObjectIndexProvider((key,))
    original.add(1, update(key, add=(0,)))
    parent = CowIndexProvider.capture(original)
    children = [RuntimeClone().copy(parent) for _ in range(8)]

    def run(pair):
        index, child = pair
        child.update(1, update(key, remove=(0,), add=(index + 1,)))
        return list(child.get_index_group(key))

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(run, enumerate(children))) == [[i + 1] for i in range(8)]
    assert list(parent.get_index_group(key)) == [0]


@pytest.mark.parametrize("mutate", [
    lambda bs: bs.add(400), lambda bs: bs.remove(1), lambda bs: bs.clear(),
    lambda bs: bs.__ior__(BitSet(2)), lambda bs: bs.__iand__(BitSet(2)),
    lambda bs: bs.__isub__(BitSet(2)), lambda bs: bs.update([BitSet(2)]),
    lambda bs: bs.intersection_update([BitSet(2)]),
    lambda bs: bs.difference_update([BitSet(2)]),
])
def test_shared_bitset_mutators_cannot_change_siblings(mutate):
    from helper.query_system.cow_index import FrozenBitSet
    bits = FrozenBitSet(BitSet(2))
    before = dict(vars(bits))
    with pytest.raises(TypeError):
        mutate(bits)
    assert vars(bits) == before


def test_random_updates_match_plain_provider_including_clear_and_forks():
    key = IndexKey("value")
    plain = SimpleObjectIndexProvider((key,), (key,))
    cow = CowIndexProvider.capture(plain)
    members = {}
    rng = Random(21)
    for step in range(200):
        idx, value = rng.randrange(400), rng.randrange(10)
        old = (members[idx],) if idx in members else ()
        ctx = update(key, remove=old, add=(value,))
        plain.update(idx, ctx)
        cow.update(idx, ctx)
        members[idx] = value
        assert cow.data == plain.data
        if step % 17 == 0:
            cow = RuntimeClone().copy(cow)
        if step % 63 == 0:
            cow.clear()
            plain.clear()
            members.clear()


@pytest.mark.parametrize("seed", [7, 31])
@pytest.mark.parametrize("checkpoints", [0, 4])
def test_native_replay_with_cow_matches_plain_worlds(seed, checkpoints):
    from test_current_position_worlds import position, prepare
    from game.ai.current_position_worlds import _PreparedWorld
    from game.ai.search_simulator import SearchSimulator
    from game.ai.checkpoint_simulator import CheckpointSimulator
    from game.simulation.search_config import search_generation_policy

    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    template = _PreparedWorld(snapshot, seed, cow_indexes=True)
    options = dict(track_information=True, policy_for=search_generation_policy, max_phases=12)
    reference = SearchSimulator(_PreparedWorld(snapshot, seed), **options)
    experiment = (CheckpointSimulator(template, checkpoint_limit=checkpoints, **options)
                  if checkpoints else SearchSimulator(template, **options))
    before_rng = live.state.rng.getstate()
    a, b = reference.start(), experiment.start()
    rng = Random(9)
    for step in range(35):
        assert a == b
        if a.decision is None:
            break
        assert a.player_information() == b.player_information()
        if step < 4:
            for choice in a.choices[:2]:
                assert reference.advance(a, choice) == experiment.advance(b, choice)
        choice = rng.choice(a.choices)
        a, b = reference.advance(a, choice), experiment.advance(b, choice)
    assert a == b
    assert live.state.rng.getstate() == before_rng
    assert type(template.state.card_register.idx_provider) is CowIndexProvider


def test_world_copy_rebinds_owner_and_isolates_native_card_mutation():
    from test_current_position_worlds import position, prepare, controllers
    from game.ai.current_position_worlds import _PreparedWorld
    from game.ai.simulation_world import copy_world
    from game.game_state.registers.card_register import IK_OWNER
    from game.enums import ZoneType

    origin, live = position()
    _, _, snapshot = prepare(origin, live)
    template = _PreparedWorld(snapshot, 7, cow_indexes=True)
    a = template.materialize(controllers()).state
    b = template.materialize(controllers()).state
    card = next(iter(a.players[0].hand.values()))
    twin = b.card_register.get_by_key(card.key)
    a.players[0].move_card(card, ZoneType.GRAVEYARD)
    card.state.tapped = True
    a.synchronise_registers()
    c, _ = copy_world(a, ())
    a.card_register.unregister(card)
    assert twin.zone == ZoneType.HAND and not twin.state.tapped
    assert c.card_register.get_by_key(card.key).zone == ZoneType.GRAVEYARD
    for state in (b, c):
        group = state.card_register.idx_provider.get_index_group(IK_OWNER)
        assert all(any(owner is player for player in state.players) for owner in group)
        assert all(all(owner is not player for player in live.state.players) for owner in group)
