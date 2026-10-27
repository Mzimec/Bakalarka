"""Object-owned traversal through cached prefixes and native continuations."""
from __future__ import annotations

from collections.abc import Generator
from time import perf_counter
from typing import TYPE_CHECKING

from game.ai.search_simulator import (
    SearchSimulator, SimulationNode, ForcedBoundary, SimulationBudgetExceeded, ReplayDiverged,
)
from game.ai.generation_candidates import CandidateExpansion
from game.ai.search_actions import PlayerAction

if TYPE_CHECKING:
    from game.ai.information_set_search import _Run

TrajectoryResult = tuple[list[tuple[str, PlayerAction]], float]
TrajectoryBoundary = SimulationNode | ForcedBoundary
TrajectoryChoices = Generator[PlayerAction | CandidateExpansion, TrajectoryBoundary, TrajectoryResult]


class TrajectoryTraversal:
    """!
    @brief Own one trajectory's search context, simulator and starting boundary.
    @var search Current search counters, selection state and transition cache.
    @var simulator Sampled-world simulator; accepted native backends may replace it.
    @var start Immutable boundary from which this trajectory begins.
    """

    def __init__(self, search: _Run, simulator, start: SimulationNode) -> None:
        """!
        @brief Bind one isolated trajectory without cloning or advancing its world.
        """
        self.search, self.simulator, self.start = search, simulator, start

    def execute(self) -> TrajectoryResult:
        """!
        @brief Traverse cached prefixes, then enter a continuous native suffix.
        @return Backpropagation path and completed root-player value.
        """
        run, sim, node = self.search, self.simulator, self.start
        backend = getattr(sim, 'simulation_backend', None)
        if isinstance(backend, SearchSimulator) and getattr(node, '_owner', None) is backend._identity:
            sim = backend
        choices = run.trajectory_choices(node)
        continuous = (run.searcher.trajectory_mode == "continuous"
                      and isinstance(sim, SearchSimulator) and sim.track_information
                      and isinstance(node, SimulationNode))
        try:
            action = next(choices)
            while True:
                if isinstance(action, CandidateExpansion):
                    try:
                        node = sim.expand_candidates(node, action.limit)
                    except SimulationBudgetExceeded as failure:
                        choices.throw(failure)
                        raise AssertionError('Candidate generation must handle its work budget')
                    action = choices.send(node)
                    continue
                key = run.transition_key(sim, node, action)
                if continuous and (key is None or key not in run.transitions):
                    return ContinuationTraversal(run, sim, node, action, choices).execute()
                chain = run.cached_forced_chain(sim, key)
                if chain:
                    run.forced_chain_hits += 1
                    for expected, child in chain:
                        if expected != action:
                            raise ReplayDiverged("Cached forced chain changed its action")
                        run.cache_hits += 1
                        run.forced_chain_steps += 1
                        node = child
                        action = choices.send(node)
                    continue
                try:
                    node = run.advance(sim, node, action)
                except SimulationBudgetExceeded as error:
                    choices.throw(error)
                    raise AssertionError("Trajectory must handle a simulator budget")
                action = choices.send(node)
        except StopIteration as completed:
            return completed.value
        finally:
            choices.close()


class ContinuationTraversal:
    """!
    @brief Own callback state while a native runtime executes an uncached suffix.
    @var search Search counters and immutable transition cache.
    @var simulator Exclusive sampled-world runtime used by this continuation.
    @var start Boundary whose history and observations must be reproduced.
    @var choices Selection generator shared with the edge-by-edge traversal.
    @var parent Last detached boundary associated with the selected action.
    @var action Complete action selected at parent.
    @var first Whether the next callback validates the starting boundary.
    @var finished Whether the selection generator returned a completed sample.
    @var result Completed path/value returned by the selection generator.
    @var error Search exception deferred until native rule frames have unwound.
    @var callback_seconds Selection/evaluation time excluded from simulator timing.
    """

    def __init__(self, search: _Run, simulator: SearchSimulator,
                 start: SimulationNode, first_action: PlayerAction, choices: TrajectoryChoices) -> None:
        """!
        @brief Initialize continuation state without executing any engine action.
        """
        self.search, self.simulator, self.start = search, simulator, start
        self.first_action, self.choices = first_action, choices
        self.parent, self.action = start, first_action
        self.first, self.finished = True, False
        self.result = self.error = None
        self.callback_seconds = 0.0

    def receive(self, child: TrajectoryBoundary) -> PlayerAction | None:
        """!
        @brief Feed a boundary into selection and store detached transition facts.
        @param child Native or immutable decision boundary reached by the engine.
        @return Next complete action, or None when the trajectory is finished.
        """
        before, selected, edge_child = (self.parent, self.action, child)
        try:
            self.action = self.choices.send(child)
            while isinstance(self.action, CandidateExpansion):
                try:
                    child = self.simulator.expand_candidates(child, self.action.limit)
                except SimulationBudgetExceeded as failure:
                    self.choices.throw(failure)
                    raise AssertionError('Candidate generation must handle its work budget')
                self.action = self.choices.send(child)
        except StopIteration as completed:
            self.finished, self.result = (True, completed.value)
            return None
        finally:
            detached = edge_child.detached() if isinstance(edge_child, ForcedBoundary) else edge_child
            self.search.store_transition(self.simulator, before, selected, detached)
        self.parent = child.detached() if isinstance(child, ForcedBoundary) else child
        self.search.advance_calls += 1
        return self.action

    def choose(self, current: TrajectoryBoundary) -> PlayerAction | None:
        """!
        @brief Validate the resumed prefix and safely dispatch an engine callback.
        @param current Boundary exposed by the owned simulator runtime.
        @return Chosen action, or None before propagating a deferred search error.
        """
        started = perf_counter()
        try:
            if self.first:
                self.first = False
                if current != self.start:
                    sparse_match = (
                        getattr(self.start, '_sparse', False)
                        and current.history == self.start.history
                        and current.decision == self.start.decision
                        and current.phase_steps == self.start.phase_steps
                        and current._recalls == self.start._recalls
                        and current.player_information() == self.start.player_information()
                        and all(current.observation(player) == self.start.observation(player)
                                for player, _ in self.start._views)
                    )
                    if not sparse_match:
                        raise ReplayDiverged('Trajectory prefix differs from its starting node')
                return self.first_action
            return self.receive(current)
        except Exception as failure:
            # Rule transactions must unwind before seeing policy/budget errors;
            # otherwise they could treat a search failure as a failed game action.
            self.error = failure
            return None
        finally:
            self.callback_seconds += perf_counter() - started

    def execute(self) -> TrajectoryResult:
        """!
        @brief Run the uncached suffix and exclude callback work from advance timing.
        @return Completed search path and root-player value.
        @throws ReplayDiverged If the runtime cannot reproduce the selected prefix.
        """
        self.search.trajectory_runs += 1
        self.search.advance_calls += 1
        started = perf_counter()
        try:
            try:
                end = self.simulator.follow_actions(
                    self.choose, node=self.start,
                    coalesce_forced=self.search.searcher.compress_forced,
                )
            except SimulationBudgetExceeded as failure:
                self.choices.throw(failure)
                raise AssertionError('Trajectory must handle a simulator budget')
        finally:
            self.search.advance_ms += max(0.0, perf_counter() - started - self.callback_seconds) * 1000
        if self.error is not None:
            raise self.error
        if not self.finished:
            self.receive(end)
        if not self.finished:
            raise ReplayDiverged('Trajectory ended before its selected action completed')
        return self.result
