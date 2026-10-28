"""Verify exclusive attribution across measurement boundaries and hook cleanup."""
import gc
from types import SimpleNamespace

import pytest

from benchmarks.mcts_step_components import ExecutionBreakdown
from game.ai.search_simulator import _Replay
from runtime_write_guard import deny_writes


def test_execution_scopes_charge_only_active_time_without_double_counting(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr('benchmarks.mcts_step_components.perf_counter', lambda: clock.now)
    profiler = ExecutionBreakdown()

    class Calls:
        def outer(self):
            clock.now += 50  # Preparation, before the measured action begins.
            profiler.arm()
            clock.now += 2
            self.inner()
            clock.now += 4
            profiler.stop()
            clock.now += 70  # Successor menu, outside the measured action.

        @classmethod
        def inner(cls):
            assert cls is Calls
            clock.now += 3

    with deny_writes(), profiler.hook(Calls, 'outer', 'outer'), profiler.hook(Calls, 'inner', 'inner'):
        Calls().outer()
    summary = profiler.summary()
    assert summary['total_ms'] == 9000
    assert summary['categories_ms'] == {'outer': 6000, 'inner': 3000}
    assert not profiler.stack
    assert all(row['calls'] == 1 for row in summary['functions'].values())
    assert isinstance(Calls.__dict__['inner'], classmethod)


def test_execution_hooks_restore_after_an_exception():
    original, callbacks = _Replay._select, tuple(gc.callbacks)
    profiler = ExecutionBreakdown()
    with deny_writes(), pytest.raises(RuntimeError, match='interrupted'):
        with profiler.installed():
            assert _Replay._select is not original
            profiler.arm()
            raise RuntimeError('interrupted')
    assert _Replay._select is original
    assert tuple(gc.callbacks) == callbacks
    assert not profiler.active and not profiler.stack
    assert profiler.summary()['total_ms'] >= 0


@pytest.mark.parametrize('kind,terminal', [('PassPriorityAction', False), ('ConcedeAction', True)])
def test_profiled_single_action_preserves_native_successor(kind, terminal):
    from benchmarks.mcts_matched_requests import RequestStepMeasurement
    from benchmarks.search_tactics import tactical_scenario
    from game.ai.search_simulator import SearchSimulator
    from game.ai.information_set_search import InformationSetSearch
    from game.ai.observation_policy import ObservationOpponentPolicy
    planner = InformationSetSearch(selection='puct', opponent_policy=ObservationOpponentPolicy())
    results = []
    with deny_writes():
        for enabled in (False, True):
            simulator = SearchSimulator(tactical_scenario('lethal_shock'),
                                        track_information=True, structured_records=True)
            root = simulator.start()
            action = next(a for a in root.player_information().actions if a.kind == kind)
            measured = RequestStepMeasurement(simulator, root, action, planner, 123,
                                             execution_breakdown=enabled)
            row = measured.execute()
            child = measured.child
            assert child.terminal is terminal
            assert row['logical_steps'] == 1 and row['successor_actions_executed'] == 0
            if enabled:
                assert row['execution_breakdown']['total_ms'] == pytest.approx(row['execution_to_request_ms'])
            results.append((child.history, child._views, child._recalls, child.phase_steps))
    assert results[0] == results[1]
