"""Native sampled-world priority checkpoints, inspired by XMage state restore.

This is trusted simulation infrastructure, never a live-state sampler or policy
input. Copy the already sampled world, including its RNG and both memories.
Nested choices replay from the nearest supported boundary; no Python call stack
is copied. The strict SearchSimulator remains the differential reference.
"""
from dataclasses import dataclass, field
from collections import OrderedDict
from time import perf_counter
from types import SimpleNamespace

from game.ai.runtime_clone import RuntimeClone, UnsupportedSnapshot
from game.ai.search_simulator import SearchSimulator, _Replay, _DecisionBoundary
from game.ai.decision_maker import PriorityDecisionRequest
from game.enums import TurnPhase
from game.game_loop.continuations import PriorityDecisionBoundary
from game.ai.simulation_world import copy_world as _copy_world, native_loop as _native_loop


@dataclass
class _Checkpoint:
    """!
    @brief Owned native priority continuation with player memories and replay position.
    @var state State retained by this record.
    @var knowledge Knowledge retained by this record.
    @var recalls Recalls retained by this record.
    @var phase_steps Native phase progress counter for replay and simulator limits.
    @var in_factory In factory retained by this record.
    @var _immutable_values Immutable values retained by this record.
    """
    state: object
    knowledge: tuple
    recalls: tuple
    phase_steps: int
    in_factory: bool
    _immutable_values: dict = field(default_factory=dict, repr=False)

    @classmethod
    def capture(cls, request, replay):
        """!
        @brief Capture a supported owned priority continuation and player memories.
        """
        state = request.state
        # This runtime already belongs exclusively to this sampled simulator.
        # At an actual native priority suspension it is surrendered, not resumed
        # or mutated. Only restore needs a graph copy. Save memory BEFORE the
        # decision projection, which will run again on restore.
        memories = RuntimeClone({id(state.knowledge_token): state.knowledge_token}).copy(
            tuple(p.controller.knowledge for p in state.players))
        # Reuse already PROVEN immutable root values; never scan or compile
        # fresh recipes for a checkpoint that may be restored only once.
        # New/modified checkpoint objects simply take the ordinary copy path.
        from game.ai.current_position_worlds import _PreparedWorld
        factory = replay.simulator.factory
        constants = {}
        if type(factory) is _PreparedWorld and factory.reuse:
            constants = factory.immutable_values
        return cls(state, memories, tuple(replay.recalls.items()), replay.phase_steps,
                   replay.in_factory, constants)

    def copy(self):
        """!
        @brief Reuse root constants; all mutable data still gets an ownership copy.
        """
        return _copy_world(self.state, self.knowledge, immutable_values=self._immutable_values)

    def freeze(self):
        """!
        @brief Own a copy while the source runtime continues its native call stack.
        """
        self.state, self.knowledge = self.copy()
        # The original priority frame would remove this marker on unwind.
        self.state.__dict__.pop('priority_decision_boundary',None)

    def build(self, controllers):
        """!
        @brief Copy a captured continuation into an independently owned native world.
        """
        state, memories = self.copy()
        for player, controller, memory in zip(state.players, controllers, memories):
            player.controller = controller
            controller.knowledge = memory
        loop = _native_loop()
        loop.resume_priority(state)
        # The reference runner counts completed step() calls, but not the
        # initial current-position factory's own resume_priority() call.
        if not self.in_factory:
            controllers[0].driver.phase_steps += 1
        return SimpleNamespace(state=state, loop=loop)


class _CheckpointReplay(_Replay):
    """!
    @brief Capture and resume supported priority checkpoints during replay.
    """
    def decide(self, request):
        """!
        @brief Validate the native decision prefix and choose or expose its boundary.
        """
        simulator = self.simulator
        simulator.decisions_replayed += int(self.cursor < len(self.history))
        prefix = self.history[:self.cursor]
        pending = None
        copied = False
        boundary = getattr(request.state, "priority_decision_boundary", None)
        if (self.cursor == len(self.history)
                and simulator.checkpoint_limit
                and prefix not in simulator._checkpoints
                and type(request) is PriorityDecisionRequest
                and type(boundary) is PriorityDecisionBoundary
                and boundary.phase == request.state.turn.phase and boundary.player == request.player.idx
                and boundary.allow_sorcery_speed == (boundary.phase in
                    (TurnPhase.PRECOMBAT_MAIN, TurnPhase.POSTCOMBAT_MAIN))
                and request.state.turn.phase not in (TurnPhase.UNTAP, TurnPhase.CLEANUP)
                and request.state.priority.current_player is request.player
                and not request.state._refreshing_effects and not request.state._deferred_events):
            nearest = max((len(p) for p in simulator._checkpoints if prefix[:len(p)] == p), default=0)
            if self.cursor - nearest >= simulator.checkpoint_interval:
                visits = simulator._boundary_visits.get(prefix,0)
                continuing = self.follow is not None
                started = perf_counter()
                pending = _Checkpoint.capture(request, self)
                if (continuing and simulator.checkpoint_continuous and prefix==self.resume_history
                        and visits >= simulator.checkpoint_min_visits):
                    try:
                        pending.freeze()
                    except UnsupportedSnapshot:
                        # A cache is optional; unsupported copy adapters do not
                        # change an otherwise valid native trajectory.
                        simulator.checkpoint_copy_rejections += 1
                    else:
                        copied = True
                        simulator.checkpoint_copies += 1
                simulator.checkpoint_capture_ms += (perf_counter() - started) * 1000
        try:
            result = super().decide(request)
            if copied:
                simulator.remember_checkpoint(prefix,pending)
            return result
        except _DecisionBoundary:
            if pending is not None:
                # No engine operation/transaction is active at this boundary.
                # Native frames only remove the transient boundary marker on
                # unwind; no gameplay state changes after surrender.
                # Remove replay controllers so snapshots retain no driver/cache.
                for player in pending.state.players:
                    player.controller = None
                simulator.remember_checkpoint(prefix,pending)
            raise


class CheckpointSimulator(SearchSimulator):
    """!
    @brief Opt-in native-loop backend with a bounded cache per sampled world.

    Factories must use the standard GameLoop/processor and side-effect-free replay
    contract, with priority windows at native phase boundaries. Custom loops and
    arbitrary nested priority callbacks must use SearchSimulator instead. A zero
    limit disables capture. Immutable node histories/observations and logical
    decision/phase budgets are identical to the strict backend.
    """
    def __init__(self, factory, *, checkpoint_limit=32, checkpoint_interval=4,
                 checkpoint_continuous=True, checkpoint_min_visits=2, **options):
        if type(checkpoint_limit) is not int or checkpoint_limit < 0:
            raise ValueError("checkpoint_limit must be a nonnegative integer")
        if type(checkpoint_interval) is not int or checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be a positive integer")
        if type(checkpoint_continuous) is not bool or type(checkpoint_min_visits) is not int or checkpoint_min_visits<1:
            raise ValueError('Invalid continuous checkpoint settings')
        super().__init__(factory, **options)
        self.checkpoint_limit = checkpoint_limit
        self.checkpoint_interval = checkpoint_interval
        self.checkpoint_continuous, self.checkpoint_min_visits = checkpoint_continuous,checkpoint_min_visits
        self._checkpoints = OrderedDict()
        self._boundary_visits = OrderedDict()
        self.checkpoint_copies = self.checkpoint_evictions = 0
        self.checkpoint_copy_rejections = 0
        self.checkpoint_hits = 0
        self.checkpoint_skipped_decisions = 0
        self.decisions_replayed = 0
        self.checkpoint_capture_ms = 0.0

    def note_boundary(self, prefix):
        """!
        @brief Record visits to a supported native priority checkpoint boundary.
        """
        visits = self._boundary_visits.pop(prefix,0)+1
        self._boundary_visits[prefix] = visits
        if len(self._boundary_visits)>max(1,2*self.checkpoint_limit):
            self._boundary_visits.popitem(last=False)
        return visits

    def remember_checkpoint(self, prefix, checkpoint):
        """!
        @brief Admit a supported continuation under the checkpoint cache limits.
        """
        if prefix in self._checkpoints:
            return
        if len(self._checkpoints)>=self.checkpoint_limit:
            self._checkpoints.popitem(last=False)
            self.checkpoint_evictions += 1
        self._checkpoints[prefix] = checkpoint

    def _make_replay(self, history):
        return _CheckpointReplay(self, history)

    def _replay(self, history, *, follow=None, coalesce_forced=False):
        replay = self._make_replay(history)
        replay.resume_history = history
        # Demand counts concern actual continuation starts, not every visited
        # priority window. Capturing every window evicts the useful prefix.
        if history:
            self.note_boundary(history)
        replay.follow = follow
        replay.coalesce_forced = coalesce_forced and self.native_forced
        # Bounded cache traversal avoids hashing every long history prefix and
        # the nested complete-action descriptors it contains.
        for prefix in sorted(self._checkpoints, key=len, reverse=True):
            length = len(prefix)
            if history[:length] == prefix:
                checkpoint = self._checkpoints[prefix]
                self._checkpoints.move_to_end(prefix)
                self.checkpoint_hits += 1
                self.checkpoint_skipped_decisions += length
                replay.cursor = length
                replay.recalls = dict(checkpoint.recalls)
                replay.phase_steps = checkpoint.phase_steps
                # Decisions inside resume_priority inherit the phase-step
                # accounting of the original boundary, even on later restores.
                def restore(controllers):
                    replay.in_factory = checkpoint.in_factory
                    return checkpoint.build(controllers)
                return self._run_replay(replay, restore)
        return self._run_replay(replay, self.factory)
