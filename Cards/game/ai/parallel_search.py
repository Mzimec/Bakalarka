"""Root-parallel MCTS with isolated worlds, trees and deterministic reduction.

Only trusted in-memory factories prepared by the world boundary are transported.
No live request/controller or actual hidden state is an input to this layer.
Workers share a sampled seed pool, not mutable runtime objects or tree counters.
"""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import fields, replace
import multiprocessing
import os
from random import Random
from threading import Lock, get_ident
from time import perf_counter, thread_time

from game.ai.information_set_search import (
    InformationSetSearch, InformationSearchMetrics, InformationSearchResult, ActionEstimate,
)


def _codec():
    try:
        import cloudpickle
    except ImportError as error:
        raise RuntimeError('Parallel MCTS requires cloudpickle; install Cards/requirements.txt') from error
    return cloudpickle


def _parts(total, count):
    base, extra = divmod(total, count)
    return tuple(base + (i < extra) for i in range(count))


def _run_worker(payload):
    """!
    @brief Module-level spawn entry point. A job owns all its mutable search data.
    """
    cpu_started = thread_time()
    codec = _codec()
    search, factory, information, seed, fallback, world_seeds, old_rows, deadline = codec.loads(payload)
    if deadline is not None:
        # Zero disables the serial timer. Preserve an expired common deadline
        # with a tiny positive limit; the configured minimum attempt still runs.
        search.max_time_ms = max(1e-6, (deadline - perf_counter()) * 1000)
    token = object()
    if old_rows:
        rows,links,root = old_rows
        search.tree_memory.save(token, search._memory_signature(), rows, search.tree_reuse_size,
                                links=links,root=root,byte_limit=search.tree_reuse_bytes)
    seeds = iter(world_seeds)
    def sampled_factory(unused_seed):
        return factory(next(seeds))
    result = search.search(sampled_factory, information, seed=seed, fallback=fallback, reuse_context=token)
    rows = search.tree_memory.export() if search.tree_reuse_size else ()
    # Return detached actor statistics only. No sampled simulator leaves a job.
    packet = codec.dumps((result, rows), protocol=5)
    return packet, (thread_time() - cpu_started) * 1000, os.getpid(), get_ident()


def merge_results(search, information, fallback, results, *, elapsed_ms, cpu_ms, workers,
                  requested, backend, setup_ms=0.0, worker_cpu_ms=0.0, pids=(), threads=(), payload_bytes=0):
    """!
    @brief Visits add; values are visit-weighted. Never vote on a worker's winner.
    """
    actions = set(information.actions)
    accumulated = {}
    for result in results:
        for row in result.estimates:
            actions.add(row.action)
            visits, value, probability = accumulated.get(row.action, (0, 0.0, 0.0))
            accumulated[row.action] = (visits + row.visits,
                value + (row.visits * row.mean_value if row.visits and row.mean_value is not None else 0),
                probability + (row.prior_probability or 0))
    estimates = []
    for action in sorted(actions, key=lambda a: a.sort_key):
        visits, total, probability = accumulated.get(action, (0, 0.0, 0.0))
        probability = probability / len(results) if search.selection != 'uct' else None
        estimates.append(ActionEstimate(action, visits, total / visits if visits else None, probability))
    complete = information.menu_complete or any(r.metrics.root_menu_complete for r in results)
    covered = complete and all(row.visits for row in estimates)
    enough = covered if search.require_root_coverage else any(row.visits for row in estimates)
    # Coverage can be achieved jointly. Other censored/unsupported workers
    # cannot be silently discarded in favor of easier worlds/trajectories.
    failures = [(i, r.fallback_reason or r.stop_reason) for i, r in enumerate(results)
                if (r.used_fallback and r.fallback_reason != 'root_coverage')
                or not r.metrics.iterations_completed
                or r.metrics.worlds_built != search.budget.worlds]
    usable = enough and not failures
    if usable:
        best = max((row for row in estimates if row.visits), key=lambda row: (row.visits, row.mean_value))
        choice, value, failure = best.action, best.mean_value, None
    else:
        choice, value = fallback, None
        failure = (f'parallel_worker:{failures[0][0]}:{failures[0][1]}' if failures else
                   'root_coverage' if not covered else 'no_completed_trajectory')
    special = {
        'elapsed_ms', 'cpu_ms', 'selection', 'interruption_reasons', 'history_mismatch_reasons',
        'root_actions', 'root_actions_visited', 'root_coverage_required', 'forced_initial_coverage',
        'time_limit_ms', 'minimum_trajectories', 'root_menu_complete', 'reuse_reasons',
    }
    totals = {field.name: sum(getattr(r.metrics, field.name) for r in results)
              for field in fields(InformationSearchMetrics)
              if field.name not in special and not field.name.startswith('parallel_')}
    def counts(name):
        counter = Counter()
        for result in results:
            counter.update(dict(getattr(result.metrics, name)))
        return tuple(sorted(counter.items()))
    metrics = InformationSearchMetrics(**totals, elapsed_ms=elapsed_ms, cpu_ms=cpu_ms,
        selection=search.selection, interruption_reasons=counts('interruption_reasons'),
        history_mismatch_reasons=counts('history_mismatch_reasons'), root_actions=len(estimates),
        reuse_reasons=counts('reuse_reasons'),
        root_actions_visited=sum(bool(row.visits) for row in estimates),
        root_coverage_required=search.require_root_coverage, forced_initial_coverage=search.force_initial_coverage,
        time_limit_ms=search.max_time_ms, minimum_trajectories=sum(r.metrics.minimum_trajectories for r in results),
        root_menu_complete=complete, parallel_workers=workers, parallel_requested_workers=requested,
        parallel_backend=backend, parallel_setup_ms=setup_ms, parallel_worker_cpu_ms=worker_cpu_ms,
        parallel_worker_pids=tuple(pids), parallel_worker_threads=tuple(threads), parallel_payload_bytes=payload_bytes)
    stop = ('time_budget' if any(r.stop_reason == 'time_budget' for r in results) else
            'advance_budget' if any(r.stop_reason == 'advance_budget' for r in results) else 'iteration_budget')
    return InformationSearchResult(choice, value, not usable, covered, stop, tuple(estimates), metrics, failure)


class ParallelInformationSetSearch:
    """!
    @brief Independent UCT/PUCT/grouped/staged trees with a persistent worker pool.

    Iteration and advance caps are global and partitioned across effective workers.
    Every worker builds an isolated copy of the same sampled seed pool; preparation
    work is duplicated. The time limit is common, soft and includes dispatch/startup.
    At least one trajectory is attempted per worker, as with the serial soft minimum.
    """
    def __init__(self, search, *, workers=1, backend='process'):
        if not isinstance(search, InformationSetSearch):
            raise TypeError('Supply an InformationSetSearch')
        if type(workers) is not int or not 1 <= workers <= 61:
            raise ValueError('parallel_workers must be an integer in [1, 61]')
        if backend not in ('process', 'thread'):
            raise ValueError('parallel_backend must be process or thread')
        self.searcher, self.workers, self.backend = search, workers, backend
        self._executor = None
        self._pool_key = None
        self._rows = ()
        self._context = self._signature = None
        self._lock = Lock()

    def __getattr__(self, name):
        return getattr(self.searcher, name)

    def __setattr__(self, name, value):
        # Preserve the serial planner's tuning API (budget, evaluator, prior,
        # horizon, etc.). Silently shadowing these on the adapter would leave
        # worker jobs using stale settings. Change them between searches.
        searcher = self.__dict__.get('searcher')
        if name not in ('searcher', 'workers', 'backend') and searcher is not None and name in vars(searcher):
            setattr(searcher, name, value)
        else:
            object.__setattr__(self, name, value)

    def _clear_memory(self):
        self._rows = ()
        self._context = self._signature = None
        self.searcher.reset()

    def reset(self):
        """!
        @brief Discard session-owned mutable state before preparing a new search context.
        """
        self._clear_memory()

    def invalidate_model(self, revision=None):
        """!
        @brief Invalidate evaluations and statistics tied to the previous model revision.
        """
        self.searcher.invalidate_model(revision)
        self._rows = ()
        self._context = self._signature = None

    def invalidate_belief(self):
        """!
        @brief Invalidate outcome statistics tied to a changed sampled-belief contract.
        """
        self.searcher.invalidate_belief()
        self._rows = tuple((tuple(replace(r,statistics=tuple((a,0,0.) for a,n,w in r.statistics))
                                  for r in rows),links,root) for rows,links,root in self._rows)

    def close(self):
        """!
        @brief Release owned streams, worker pools or optional execution resources.
        """
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=True, cancel_futures=True)
                self._executor = None
                self._pool_key = None
            self._clear_memory()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def _pool(self, count):
        key = self.backend, count
        if self._executor is not None and self._pool_key != key:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        if self._executor is None:
            self._executor = (ProcessPoolExecutor(max_workers=count,
                mp_context=multiprocessing.get_context('spawn')) if self.backend == 'process' else
                ThreadPoolExecutor(max_workers=count, thread_name_prefix='mcts'))
            self._pool_key = key
        return self._executor

    def search(self, world_factory, root_information, *, seed=0, fallback=None, reuse_context=None):
        """!
        @brief Run bounded search and return a detached recommendation with diagnostics.
        """
        if self.workers == 1:
            return self.searcher.search(world_factory, root_information, seed=seed,
                                       fallback=fallback, reuse_context=reuse_context)
        if type(seed) is not int:
            raise ValueError('A deterministic integer search seed is required')
        if fallback is None:
            fallback = next((a for a in root_information.actions if a.kind == 'PassPriorityAction'),
                            root_information.actions[0])
        if fallback not in root_information.actions:
            raise ValueError('Fallback must be offered at the root')
        # One live request per controller; a reused pool must never overlap its
        # own session/model changes or mutate the parent tree during reduction.
        with self._lock:
            return self._search(world_factory, root_information, seed, fallback, reuse_context)

    def _search(self, factory, information, seed, fallback, context):
        started, cpu_started = perf_counter(), thread_time()
        base = self.searcher
        count = min(self.workers, base.budget.iterations, base.budget.max_advances)
        iterations, advances = _parts(base.budget.iterations, count), _parts(base.budget.max_advances, count)
        signature = (base._memory_signature(), count, self.backend)
        if context is None or context is not self._context or signature != self._signature:
            self._rows = ()
        self._context, self._signature = context, signature
        previous_rows, self._rows = self._rows, ()
        deadline = started + base.max_time_ms / 1000 if base.max_time_ms else None
        world_rng, worker_rng = Random(seed), Random(seed ^ 0x504152414C4C454C)
        worlds = tuple(world_rng.getrandbits(64) for _ in range(base.budget.worlds))
        codec = _codec()
        futures, payload_bytes = [], 0
        # Serialize each job before starting it: even the thread backend owns
        # private factory captures, caches, search policies and reusable rows.
        payloads = []
        for index in range(count):
            worker = codec.loads(codec.dumps(base, protocol=5))
            worker.reset()
            if context is None:
                worker.tree_reuse_size = 0
            worker.budget = replace(base.budget, iterations=iterations[index], max_advances=advances[index])
            worker.min_trajectories = min(iterations[index], max(1, _parts(base.min_trajectories, count)[index]))
            old = previous_rows[index] if previous_rows else ()
            payload = codec.dumps((worker, factory, information, worker_rng.getrandbits(64),
                                   fallback, worlds, old, deadline), protocol=5)
            payload_bytes += len(payload)
            payloads.append(payload)
        pool = None
        try:
            pool = self._pool(count)
            for payload in payloads:
                futures.append(pool.submit(_run_worker, payload))
            setup_ms = (perf_counter() - started) * 1000
            results, memories, worker_cpu, pids, threads = [], [], 0.0, [], []
            # Slot order, not completion order, defines reduction/tie behavior.
            for future in futures:
                packet, cpu, pid, thread = future.result()
                result, rows = codec.loads(packet)
                results.append(result)
                memories.append(rows)
                worker_cpu += cpu
                pids.append(pid)
                threads.append(thread)
                payload_bytes += len(packet)
        except BaseException:
            for future in futures:
                future.cancel()
            self._rows = ()
            # Drain running jobs before another request can use this pool. Rule
            # errors remain errors; they are never relabeled as usable samples.
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
            self._executor = None
            self._pool_key = None
            raise
        result = merge_results(base, information, fallback, results,
            elapsed_ms=(perf_counter() - started) * 1000,
            cpu_ms=(thread_time() - cpu_started) * 1000 + worker_cpu,
            workers=count, requested=self.workers, backend=self.backend, setup_ms=setup_ms,
            worker_cpu_ms=worker_cpu, pids=pids, threads=threads, payload_bytes=payload_bytes)
        self._rows = tuple(memories) if base.tree_reuse_size and not result.used_fallback and context is not None else ()
        return result
