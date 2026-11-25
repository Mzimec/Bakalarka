"""Detailed timing hooks account for work once and restore their targets."""
import pytest
from benchmarks.profiler import ComponentProfiler
from game.game_actions.resolution.operation_executor import OperationExecutor


def test_operation_dispatch_tracks_concrete_types_and_restores():
    class First:
        def execute(self, state):
            return ["event"]
    class Second:
        def execute(self, state):
            return None
    original = OperationExecutor.execute_operation
    with ComponentProfiler() as profiler:
        profiler.patch_dispatched_method(OperationExecutor, "execute_operation", "execute")
        executor = OperationExecutor()
        assert executor.execute_operation(None, First()) == ["event"]
        assert executor.execute_operation(None, Second()) == ()
        assert profiler.stats["execute.First"].calls == 1
        assert profiler.stats["execute.Second"].calls == 1
        assert not profiler._stack
    assert OperationExecutor.execute_operation is original


def test_conditional_probe_skips_clean_calls_and_unwinds_on_error():
    class Target:
        dirty = False
        def run(self):
            if self.dirty:
                raise ValueError("failure")
    original = Target.run
    with ComponentProfiler() as profiler:
        profiler.patch_when(Target, "run", "work", lambda target: target.dirty)
        target = Target()
        target.run()
        assert "work" not in profiler.stats
        target.dirty = True
        with pytest.raises(ValueError):
            target.run()
        assert profiler.stats["work"].calls == 1
        assert not profiler._stack
    assert Target.run is original


def test_checkpoint_component_measures_whole_capture_and_restores():
    from types import SimpleNamespace
    from benchmarks.component_benchmark import configure_profiler
    from game.game_actions.resolution.cost_transaction import RuntimeCheckpoint
    original = RuntimeCheckpoint.__init__
    root = SimpleNamespace(values=[1, 2])
    with ComponentProfiler() as profiler:
        configure_profiler(profiler)
        checkpoint = RuntimeCheckpoint(root)
        root.values.append(3)
        checkpoint.rollback()
        assert root.values == [1, 2]
        assert profiler.stats["cost.checkpoint"].calls == 1
        assert profiler.stats["cost.rollback"].calls == 1
    assert RuntimeCheckpoint.__init__ is original
