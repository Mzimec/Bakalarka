"""Comparable-width recorded requests; explicit experimental cap, stdout only.

Replay verifies original root information keys. Each timing uses a fresh tree,
the same search budget and at most four admitted candidates per progressive
frontier. This is a diagnostic search configuration, not a production preset or
a proof that request kind alone determines cost. Resolution here is damage
ordering, not an arbitrary effect's execution time.
"""
import argparse
from contextlib import nullcontext
from dataclasses import asdict, replace
import json
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch
from statistics import mean, median

from benchmarks.mcts_recorded_roots import capture
from benchmarks.mcts_decision_latency import Components, detached_planner
from game.ai.search_candidates import ProgressiveWidening


def preparation(repeats=20):
    """! @brief Time bounded generation/projection without sampling or forward search. """
    from game.ai.search_simulator import _Replay
    from game.ai.generation_candidates import CandidateFrontier
    from game.ai.search_decision import DecisionProjector
    from game.ai.search_recall import PlayerRecall
    from game.ai.observation_policy import ObservationOpponentPolicy
    from game.game_actions.generation.decision_abstraction.requests import AbilityResolutionRequest
    from game.game_actions.data_structs.game_action import ResolutionContext
    from game.enums import ZoneType, CardType
    log = Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl'
    captured = capture(log, [87, 90, 92, 102])
    cases = []
    original = _Replay.decide
    for index, (_, factory, _, _) in captured.items():
        requests = []
        def remember(replay, request):
            if not requests:
                requests.append(request)
            return original(replay, request)
        simulator = factory(123)
        with patch.object(_Replay, 'decide', remember):
            simulator.start()
        request = requests[0]
        cases.append((index, request, simulator, False))
    request = cases[0][1]
    candidates = tuple(c for c in request.state.card_register.storage.get_all()
                       if c.get_zone() == ZoneType.BATTLEFIELD
                       and c.get_controller(request.state) is request.player
                       and c.is_type(request.state, CardType.CREATURE))[:4]
    assert len(candidates) == 4
    cases.append((87, AbilityResolutionRequest(request.state, request.player,
        ResolutionContext(controller=request.player), candidates, 1, 'sacrifice'), cases[0][2], True))
    samples = [[] for _ in cases]
    for repeat in range(repeats + 5):
        order = range(len(cases)) if repeat % 2 == 0 else reversed(range(len(cases)))
        for index in order:
            _, request, simulator, _ = cases[index]
            model = simulator.policy_for.candidate_model
            started = perf_counter()
            frontier = CandidateFrontier(model.generate(request, simulator.policy_for(request)), 4096)
            try:
                options, complete = frontier.take(4)
            finally:
                frontier.close()
            generated = perf_counter()
            info = DecisionProjector(request, options, PlayerRecall(), store=simulator.value_store,
                candidate_model=model.identity, menu_complete=complete, candidate_width=4).build().information
            projected = perf_counter()
            assert ObservationOpponentPolicy()(info) in info.actions
            finished = perf_counter()
            if repeat >= 5:
                samples[index].append(dict(generation_ms=1000*(generated-started),
                    projection_ms=1000*(projected-generated), policy_ms=1000*(finished-projected),
                    total_ms=1000*(finished-started)))
    for (root, request, _, synthetic), rows in zip(cases, samples):
        print(json.dumps(dict(kind='request_preparation', root=root, request=type(request).__name__,
            synthetic=synthetic, repeats=repeats, width_cap=4,
            mean_ms={k: mean(r[k] for r in rows) for k in rows[0]},
            median_ms={k: median(r[k] for r in rows) for k in rows[0]}, samples=rows)), flush=True)


def run(repeats=2, indices=(87, 90, 92, 102), iterations=32, width=4, *, captures=None):
    """! @brief Measure different requests on nearby boards with bounded menus. """
    log = Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl'
    captured = captures if captures is not None else capture(log, list(indices))
    original_width = ProgressiveWidening.width

    def bounded(schedule, visits, *, minimum_constant=0.0):
        return min(width, original_width(schedule, visits, minimum_constant=minimum_constant))

    for repeat in range(repeats):
        for index in indices if repeat % 2 == 0 else reversed(indices):
            original, factory, info, kwargs = captured[index]
            planner = detached_planner(original)
            planner.budget = replace(planner.budget, iterations=iterations, worlds=2,
                                     max_depth=4, max_extensions=16, max_advances=32768)
            if len(info.actions) > width:
                raise ValueError('Recorded initial menu already exceeds the diagnostic width')
            # Tables are cold, and the visit-based schedule alone is capped.
            # An unfinished frontier remains explicitly incomplete.
            components = Components()
            started, cpu = perf_counter(), process_time()
            with patch.object(ProgressiveWidening, 'width', bounded), components.installed():
                result = planner.search(factory, info, **kwargs)
            elapsed, cpu_elapsed = perf_counter()-started, process_time()-cpu
            view = info.current.readonly()
            row = dict(kind='matched_request', repeat=repeat, root=index,
                request=info.current.request_kind, options=len(info.actions),
                turn=view['turn'], phase=view['phase'], permanents=len(view['battlefield']),
                seconds=elapsed, cpu_seconds=cpu_elapsed, width_cap=width,
                budget=asdict(planner.budget), result=asdict(result), **components.summary())
            assert result.metrics.root_actions <= width
            assert not result.used_fallback and result.metrics.iterations_completed == iterations
            print(json.dumps(row), flush=True)


class RequestStepMeasurement:
    """!
    @brief Measure two menu boundaries and exactly one executed root action.
    @var simulator Fresh deterministic simulator, never a live match.
    @var root Immutable root used to validate the resumed callback.
    @var action Offered branch executed independently of the selection probe.
    @var selector Fresh PUCT statistics used only to time a local selection.
    @var row Disjoint wall intervals and nested generation/projection stages.
    """

    def __init__(self, simulator, root, action, planner, seed, *, execution_breakdown=False):
        """! @brief Prepare a cold local selector outside all measured intervals. """
        from game.ai.information_set_search import _Run
        self.simulator, self.root, self.action = simulator, root, action
        self.selector = _Run(planner, root.player_information().current.player, seed)
        self.calls, self.row = 0, {}
        self.request_started = self.stages_before = None
        from benchmarks.mcts_step_components import ExecutionBreakdown
        self.execution = ExecutionBreakdown() if execution_breakdown else None
        self.child = None

    def menu(self, ready):
        """! @brief Read nonoverlapping native stage counters for this boundary. """
        stages = {name: 1000*(seconds-self.stages_before.get(name, (0, 0.))[1])
                  for name, (_, seconds) in self.simulator.stages.items()}
        total = 1000*(ready-self.request_started)
        generation = sum(stages.get(name, 0.) for name in ('candidate_generation', 'generation'))
        projection = sum(stages.get(name, 0.) for name in (
            'descriptors', 'views', 'player_projection', 'views_and_address_storage', 'cached_projection'))
        # Other includes frontier setup, node construction and stage timer overhead.
        assert total+0.01 >= generation+projection
        return dict(total_ms=total, generation_ms=generation, projection_ms=projection,
                    other_ms=max(0., total-generation-projection), stages_ms=stages)

    def choose(self, node):
        """! @brief Time local selection at root; stop before any successor choice. """
        ready = perf_counter()
        self.calls += 1
        if self.calls == 1:
            self.row['root_menu'] = self.menu(ready)
            assert node.history == self.root.history
            info = node.player_information()
            assert info == self.root.player_information()
            started = perf_counter()
            _, selected, _ = self.selector.tree_action(info, horizon=(1, 0))
            self.row['selection_ms'] = 1000*(perf_counter()-started)
            assert selected in info.actions
            # Execute every offered branch in turn, rather than only the prior's
            # favourite. Selection timing does not alter the forced branch.
            self.sent = self.execution.arm() if self.execution is not None else perf_counter()
            self.cpu = process_time()
            return self.action
        assert self.calls == 2
        self.row['step_ms'] = 1000*(ready-self.sent)
        self.row['cpu_ms'] = 1000*(process_time()-self.cpu)
        self.row['execution_to_request_ms'] = 1000*(self.request_started-self.sent)
        self.row['successor_menu'] = self.menu(ready)
        assert abs(self.row['step_ms']-self.row['execution_to_request_ms']
                   -self.row['successor_menu']['total_ms']) < 0.01
        return None

    def execute(self):
        """! @brief Observe native menu entry, excluding prior sampling and restore. """
        from game.ai.search_simulator import _Replay
        simulator = self.simulator
        original = _Replay.decide

        def decide(replay, request):
            if replay.simulator is simulator and replay.cursor == len(replay.history):
                self.request_started = (self.execution.stop() if self.execution is not None
                                        and self.execution.active else perf_counter())
                self.stages_before = dict(simulator.stages)
            return original(replay, request)

        simulator.profile_advance = True
        simulator.stages.clear()
        started = perf_counter()
        with (self.execution.installed() if self.execution is not None else nullcontext()), \
                patch.object(_Replay, 'decide', decide):
            child = simulator.follow_actions(self.choose, node=self.root, coalesce_forced=False)
            if self.execution is not None and self.execution.active:
                self.execution.stop()
        self.child = child
        self.row['restore_and_step_ms'] = 1000*(perf_counter()-started)
        if self.calls == 1:
            assert child.terminal
            finished = self.execution.finished if self.execution is not None else perf_counter()
            self.row.update(step_ms=1000*(finished-self.sent),
                            cpu_ms=1000*(process_time()-self.cpu), successor_menu=None)
            self.row['execution_to_request_ms'] = self.row['step_ms']
        assert 1 <= self.calls <= 2 and not child.cutoff
        assert len(child.history) == len(self.root.history)+1
        self.row.update(successor=child.decision.kind if child.decision else 'terminal',
                        logical_steps=1, executed_root_actions=1, successor_actions_executed=0,
                        root_callbacks=1, successor_callbacks=self.calls-1)
        if self.execution is not None:
            self.row['execution_breakdown'] = self.execution.summary()
            assert abs(self.row['execution_to_request_ms']-self.row['execution_breakdown']['total_ms']) < 0.01
        return self.row


def single_step(repeats=2, indices=(87, 90, 92, 102), seeds=(123, 124), *, execution_breakdown=False):
    """! @brief Execute each root candidate once and stop at the very next boundary.

    Report root generation/projection, a cold local PUCT selection probe, native
    execution to successor request, and successor menu preparation separately.
    Every sample uses a fresh simulator; no rollout, cache or leaf evaluation.
    """
    log = Path(__file__).resolve().parents[1]/'runs/mcts-game-diagnostics-20261027.jsonl'
    captured = capture(log, list(indices))
    samples = {index: [] for index in indices}
    for repeat in range(repeats):
        for index in indices if repeat % 2 == 0 else reversed(indices):
            planner, factory, _, _ = captured[index]
            for seed in seeds:
                # A separate probe determines the bounded offered root width.
                probe = factory(seed).start()
                actions = probe.player_information().actions
                assert 1 <= len(actions) <= 4
                for ordinal, expected in enumerate(actions):
                    measurements = {}
                    modes = ((False, True) if repeat % 2 == 0 else (True, False)) if execution_breakdown else (False,)
                    for enabled in modes:
                        simulator = factory(seed)
                        root = simulator.start()
                        action = root.player_information().actions[ordinal]
                        assert action == expected
                        measurement = RequestStepMeasurement(simulator, root, action, planner, seed,
                                                             execution_breakdown=enabled)
                        measurement.execute()
                        measurements[enabled] = measurement
                    row = measurements[execution_breakdown].row
                    if execution_breakdown:
                        left, right = measurements[False].child, measurements[True].child
                        assert (left.history, left.phase_steps, left.terminal, left.cutoff,
                                left._views, left._recalls) == (right.history, right.phase_steps,
                                right.terminal, right.cutoff, right._views, right._recalls)
                        assert left.terminal or left.player_information() == right.player_information()
                        row.update(control_execution_ms=measurements[False].row['execution_to_request_ms'],
                                   control_step_ms=measurements[False].row['step_ms'], control_equivalent=True)
                    samples[index].append(dict(repeat=repeat, seed=seed, ordinal=ordinal,
                        action=action.describe(), **row))
    for index, rows in samples.items():
        info = captured[index][2]
        extra = {}
        if execution_breakdown:
            categories = set().union(*(r['execution_breakdown']['categories_ms'] for r in rows))
            extra['execution_breakdown'] = {
                'mean_ms': {c: mean(r['execution_breakdown']['categories_ms'].get(c, 0.) for r in rows)
                            for c in sorted(categories)},
                'median_ms': {c: median(r['execution_breakdown']['categories_ms'].get(c, 0.) for r in rows)
                              for c in sorted(categories)},
                'control_mean_ms': mean(r['control_execution_ms'] for r in rows),
                'control_median_ms': median(r['control_execution_ms'] for r in rows),
                'equivalent_controls': sum(r['control_equivalent'] for r in rows),
            }
        print(json.dumps(dict(kind='single_request_step', root=index,
            request=info.current.request_kind, repeats=repeats, seeds=seeds,
            options=len(info.actions), samples=rows,
            mean_ms=mean(r['step_ms'] for r in rows),
            median_ms=median(r['step_ms'] for r in rows),
            min_ms=min(r['step_ms'] for r in rows), max_ms=max(r['step_ms'] for r in rows),
            selection=captured[index][0].selection, selection_probe='cold_local_tree_action_without_rollout',
            mean_pipeline_ms={name: mean(r[name] for r in rows) for name in (
                'selection_ms', 'execution_to_request_ms', 'step_ms')},
            median_pipeline_ms={name: median(r[name] for r in rows) for name in (
                'selection_ms', 'execution_to_request_ms', 'step_ms')},
            menus={name: {stat: {key: function(r[name][key] for r in rows if r[name] is not None)
                                for key in ('total_ms', 'generation_ms', 'projection_ms', 'other_ms')}
                          for stat, function in (('mean_ms', mean), ('median_ms', median))}
                   for name in ('root_menu', 'successor_menu') if any(r[name] is not None for r in rows)},
            counts={name: sum(r[name] for r in rows) for name in (
                'logical_steps', 'executed_root_actions', 'successor_actions_executed',
                'root_callbacks', 'successor_callbacks')},
            mean_restore_and_step_ms=mean(r['restore_and_step_ms'] for r in rows), **extra)), flush=True)


def main():
    """! @brief Print measurements without creating runtime artifacts. """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=32)
    parser.add_argument('--preparation', action='store_true')
    parser.add_argument('--single-step', action='store_true',
                        help='Execute only one root action, without rollout or settled-leaf extensions')
    parser.add_argument('--execution-breakdown', action='store_true',
                        help='Split execution into exclusive scopes and compare an unprofiled control')
    args = parser.parse_args()
    if min(args.repeats, args.iterations) < 1:
        parser.error('Counts must be positive')
    if args.preparation and args.single_step:
        parser.error('Select preparation or single-step, not both')
    if args.execution_breakdown and not args.single_step:
        parser.error('--execution-breakdown requires --single-step')
    if args.single_step:
        single_step(args.repeats, execution_breakdown=args.execution_breakdown)
    elif args.preparation:
        preparation(repeats=max(20, args.repeats))
    else:
        run(args.repeats, iterations=args.iterations)


if __name__ == '__main__':
    main()
