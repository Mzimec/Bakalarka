"""Bounded bootstrap population over immutable history-compatible worlds.

This is a finite-particle approximation, not exact posterior sampling. Each old
particle proposes one new history segment, survivors are resampled with equal
weights, and complete depletion triggers fresh full-history initialization.
Search trajectories never update this population; only live player recall does.
"""
from dataclasses import dataclass, replace
from random import Random
from collections import Counter

from game.ai.history_world_provider import HistoryWorldProvider, sample_history_action
from game.ai.information_set_search import SampledRootMismatch
from game.ai.scenario_world_provider import resume_to_information
from game.ai.search_simulator import WorldInitializationMetrics


@dataclass(frozen=True)
class _Particle:
    """!
    @brief One accepted sampled history retained by the incremental belief population.
    @var simulator Simulator retained by this record.
    @var node Node retained by this record.
    @var origin Origin retained by this record.
    """
    simulator: object
    node: object
    origin: tuple


class IncrementalWorldProvider:
    """!
    @brief One population per live controller, scenario prior and opponent model.

    The callable receives only PlayerInformation and returns seed -> simulator,
    matching MCTSAgent's provider API. Preparation is lazy and atomic: a partial or
    failed update never replaces the previous complete population. reset() must be
    called between games (MCTSAgent does this automatically). No live State is read.
    """
    def __init__(self, world_factory, *, size=16, seed=0, opponent_weights=None,
                 max_attempts=32, max_prefix_advances=256):
        if type(size) is not int or size < 1:
            raise ValueError("Population size must be a positive integer")
        if type(seed) is not int:
            raise ValueError("A deterministic integer population seed is required")
        self.fresh = HistoryWorldProvider(world_factory, opponent_weights=opponent_weights,
                                         max_attempts=max_attempts, max_prefix_advances=max_prefix_advances)
        self.size, self.seed = size, seed
        self._version = 0
        self.reset()

    def reset(self):
        """!
        @brief Discard session-owned mutable state before preparing a new search context.
        """
        self._version += 1
        self._information = None
        self._population = ()

    def __call__(self, information):
        cohort = _Cohort(self, information)
        return lambda seed: _PopulationSample(cohort, seed)


class _Cohort:
    """!
    @brief Own compatible particles, survivor accounting and per-request preparation.
    """
    def __init__(self, provider, information):
        self.provider, self.information = provider, information
        self.version = provider._version
        self.parent_info, self.parents = provider._information, provider._population
        self.particles = None
        self.metrics = WorldInitializationMetrics()

    def add(self, **counts):
        """!
        @brief Admit an accepted history under the particle population capacity.
        """
        self.metrics = replace(self.metrics, **{k: getattr(self.metrics, k) + v for k, v in counts.items()})

    def add_mismatches(self, counts):
        """!
        @brief Accumulate rejection reasons without exposing hidden runtime data.
        """
        combined = Counter(dict(self.metrics.mismatch_reasons))
        combined.update(dict(counts))
        self.metrics = replace(self.metrics, mismatch_reasons=tuple(sorted(combined.items())))

    def prepare(self):
        """!
        @brief Prepare compatible sampled-world inputs for the current decision.
        """
        if self.particles is not None:
            return self.particles
        if self.version != self.provider._version:
            raise ValueError("Population request was superseded or reset; request a fresh factory")
        self.metrics = WorldInitializationMetrics()
        target, old = self.information, self.parent_info
        rng = Random(self.provider.seed ^ int(target.key, 16))
        if old == target:
            population = self.parents
        else:
            survivors = []
            extends = (old is not None and old.current.player == target.current.player
                       and target.recall.entries[:len(old.recall.entries)] == old.recall.entries)
            if extends:
                self.add(population_updates=1)
                for particle in self.parents:
                    path_rng = Random(rng.getrandbits(64))
                    choose = lambda info: sample_history_action(self.provider.fresh.opponent_weights, info, path_rng)
                    try:
                        node = resume_to_information(particle.simulator, target, choose,
                            self.provider.fresh.max_prefix_advances, start_node=particle.node,
                            on_advance=lambda: self.add(prefix_advance_attempts=1))
                    except SampledRootMismatch as exc:
                        self.add_mismatches(((exc.reason, 1),))
                        continue
                    survivors.append(_Particle(particle.simulator, node, particle.origin))
                self.add(population_survivors=len(survivors))
            if survivors:
                # Equal-weight residual resampling preserves every survivor;
                # if all survive, it introduces no unnecessary ancestry loss.
                copies, remainder = divmod(self.provider.size, len(survivors))
                population = survivors * copies + rng.choices(survivors, k=remainder)
                rng.shuffle(population)
            else:
                self.add(population_refreshes=1)
                population = []
                factory = self.provider.fresh(target)
                for index in range(self.provider.size):
                    sim = factory(rng.getrandbits(64))
                    try:
                        node = sim.start()
                    finally:
                        metrics = sim.initialization_metrics
                        self.add(sampling_attempts=metrics.sampling_attempts,
                                 rejected_samples=metrics.rejected_samples,
                                 prefix_advance_attempts=metrics.prefix_advance_attempts)
                        self.add_mismatches(metrics.mismatch_reasons)
                    population.append(_Particle(sim, node, (target.key, index)))
            # Commit only a complete population. Errors and work caps escape
            # without treating expensive/unsupported worlds as incompatible.
            self.provider._population = tuple(population)
            self.provider._information = target
            self.provider._version += 1
        self.particles = tuple(population)
        self.add(population_size=len(self.particles),
                 population_distinct_origins=len({p.origin for p in self.particles}))
        return self.particles


class _PopulationSample:
    """!
    @brief Expose one incremental population sample through the simulator interface.
    """
    def __init__(self, cohort, seed):
        if type(seed) is not int:
            raise ValueError("A deterministic integer sample seed is required")
        self.cohort, self.seed = cohort, seed
        self._particle = None
        self._report_preparation = False

    @property
    def initialization_metrics(self):
        # Charge the shared preparation once, including a failed attempt.
        """!
        @brief Return detached diagnostics from sampled-world acceptance.
        """
        return self.cohort.metrics if self._report_preparation else WorldInitializationMetrics()

    def start(self):
        """!
        @brief Initialize the owned scenario and expose its first supported boundary.
        """
        if self._particle is None:
            self._report_preparation = self.cohort.particles is None
            particles = self.cohort.prepare()
            self._particle = Random(self.seed).choice(particles)
        return self._particle.node

    def advance_action(self, node, action):
        """!
        @brief Bind and execute one complete visible action in an isolated world.
        """
        if self._particle is None:
            raise ValueError("Initialize a population sample before advancing")
        return self._particle.simulator.advance_action(node, action)
