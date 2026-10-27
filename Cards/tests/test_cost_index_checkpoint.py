"""Selective index snapshots preserve identities and transaction lifetimes."""
from types import SimpleNamespace

import pytest

from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
from helper.query_system.object_register import (
    SimpleObjectIndexProvider, BitSet, IndexKey, IndexUpdate, RegisterUpdateContext,
)


def fixture(provider_type=SimpleObjectIndexProvider):
    a, b = IndexKey("a"), IndexKey("b")
    provider = provider_type((a, b))
    provider.add(1, RegisterUpdateContext({a: IndexUpdate((), ("old",)),
                                         b: IndexUpdate((), ("untouched",))}))
    state = SimpleNamespace(card_register=SimpleNamespace(_snapshots={}),
                            player_register=SimpleNamespace(_snapshots={}), provider=provider)
    # External SimpleNamespace children are normally ignored; expose provider
    # directly on the forced root for the real runtime traversal.
    return state, provider, a, b


def changed(provider, key, remove=("old",), add=("new",), idx=1):
    provider.update(idx, RegisterUpdateContext({key: IndexUpdate(remove, add)}))


def assert_unhooked(provider):
    assert "_process_register_update" not in vars(provider)
    assert "clear" not in vars(provider)


def test_capture_only_written_group_restore_deleted_bucket_and_capacity():
    state, provider, a, b = fixture()
    group = provider.data[a]
    old = group["old"]
    old_fields = vars(old).copy()
    untouched = provider.data[b]["untouched"]
    checkpoint = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    assert not any(type(obj) is BitSet for _, obj, _ in checkpoint._restore)
    changed(provider, a, (), ("old",), idx=300)
    assert old._max_elements > old_fields["_max_elements"]
    changed(provider, a, ("old",), (), idx=300)
    changed(provider, a)  # Delete old bucket and create new one.
    changed(provider, a, (), ("large",), idx=300)  # Grow capacity.
    captured = {id(obj) for _, obj, _ in checkpoint._restore}
    assert id(old) in captured and id(untouched) not in captured
    size = len(checkpoint._restore)
    changed(provider, a, (), ("again",))
    assert len(checkpoint._restore) == size
    checkpoint.rollback()
    assert provider.data[a] is group
    assert group == {"old": old} and vars(old) == old_fields
    assert provider.data[b]["untouched"] is untouched
    assert_unhooked(provider)


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_engine_transaction_releases_hooks_on_success_and_exception(direct, failure, monkeypatch):
    from dataclasses import dataclass
    from benchmarks.core_reads import board
    from game.game_actions.data_structs.game_action import (
        GameAction, ResolutionContext, FixedExecutionPlan, ScheduledResolution,
    )
    from game.game_actions.mana_effects import PayLifeOperation
    from game.game_actions.resolution.resolution_engine import ResolutionEngine
    from game.game_actions.resolution.operation_executor import OperationExecutor
    from game.game_actions.resolution.action_processor import ActionProcessor
    from game.game_actions.resolution.event_bus import EventBus

    state, card = board(0)
    engine = ResolutionEngine(OperationExecutor(), EventBus(), sba_rules=[])
    context = ResolutionContext(source=card, controller=card.owner, is_cost=True)
    intent = ScheduledResolution(FixedExecutionPlan((PayLifeOperation(context, 1),)), context)
    health = card.owner.health
    observed = []
    original = engine._resolve

    def resolve(*args):
        assert "_process_register_update" in vars(state.player_register.idx_provider)
        result = original(*args)
        observed.append(True)
        if failure:
            raise RuntimeError("after payment")
        return result

    monkeypatch.setattr(engine, "_resolve", resolve)

    @dataclass(frozen=True)
    class Action(GameAction):
        def get_intents(self):
            return (intent,)

    def run():
        return engine.resolve(state, intent) if direct else ActionProcessor(engine).process(state, Action())

    if failure:
        with pytest.raises(RuntimeError, match="after payment"):
            run()
    else:
        run()
    assert observed
    assert card.owner.health == health - (0 if failure else 1)
    assert_unhooked(state.card_register.idx_provider)
    assert_unhooked(state.player_register.idx_provider)


def test_clear_restores_all_groups_and_commit_keeps_writes():
    state, provider, a, b = fixture()
    before = {key: values.copy() for key, values in provider.data.items()}
    checkpoint = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    provider.clear()
    assert not any(provider.data.values())
    checkpoint.rollback()
    assert provider.data == before
    checkpoint = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    changed(provider, a)
    checkpoint.close()
    checkpoint.close()
    assert "new" in provider.data[a] and "old" not in provider.data[a]
    assert_unhooked(provider)
    count = len(checkpoint._restore)
    changed(provider, b, ("untouched",), ("later",))
    assert len(checkpoint._restore) == count  # No retained observer after commit.


def test_full_and_custom_providers_keep_eager_capture():
    class CustomProvider(SimpleObjectIndexProvider):
        __module__ = "helper.test_cost_index_checkpoint"

    for cls, scope in ((SimpleObjectIndexProvider, None), (CustomProvider, frozenset())):
        state, provider, a, _ = fixture(cls)
        checkpoint = RuntimeCheckpoint(state, card_scope=scope, defer_indexes=True)
        assert not checkpoint._index_hooks
        changed(provider, a)
        checkpoint.rollback()
        assert "old" in provider.data[a] and "new" not in provider.data[a]


def test_inner_checkpoint_restores_outer_hook_and_outer_original_memberships():
    state, provider, a, _ = fixture()
    outer = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    changed(provider, a)
    inner = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    assert not inner._index_hooks  # An existing observer forces eager capture.
    changed(provider, a, ("new",), ("inner",))
    inner.rollback()
    assert "new" in provider.data[a] and "inner" not in provider.data[a]
    changed(provider, a, ("new",), ("after",))
    outer.rollback()
    assert "old" in provider.data[a] and "after" not in provider.data[a]
    assert_unhooked(provider)


def test_failed_update_and_checkpoint_construction_remove_hooks(monkeypatch):
    state, provider, a, _ = fixture()
    checkpoint = RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    with pytest.raises(RuntimeError):
        changed(provider, a, ("missing",), ())
    checkpoint.rollback()
    assert_unhooked(provider)
    original = RuntimeCheckpoint.watch

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise ValueError("construction failed")

    monkeypatch.setattr(RuntimeCheckpoint, "watch", fail)
    with pytest.raises(ValueError):
        RuntimeCheckpoint(state, card_scope=frozenset(), defer_indexes=True)
    assert_unhooked(provider)
