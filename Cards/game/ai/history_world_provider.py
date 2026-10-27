"""Condition explicit scenario priors on player recall by rejection sampling.

Each attempt jointly samples a fresh hidden world and past opponent decisions.
An attempt is accepted only if it reproduces the complete exposed own history,
current view and menu. This conditions the *joint* model, rather than sampling
worlds uniformly after independently forcing a compatible opponent path.
"""
from math import isfinite
from random import Random
from collections import Counter

from game.ai.information_set_search import SampledRootMismatch
from game.ai.scenario_world_provider import resume_to_information
from game.ai.search_simulator import HistorySamplingExhausted, WorldInitializationMetrics


class UniformHistoryPolicy:
    """!
    @brief Explicit baseline assumption, not a claim about human play frequencies.
    """
    def __call__(self, information):
        return (1.0,) * len(information.actions)


def sample_history_action(model, information, rng):
    """!
    @brief One validated opponent distribution, shared by fresh and incremental sampling.
    """
    weights = tuple(float(w) for w in model(information))
    if len(weights) != len(information.actions) or not weights:
        raise ValueError("History weights must align with the opponent's offered actions")
    if any(not isfinite(w) or w < 0 for w in weights) or not max(weights):
        raise ValueError("History weights must be finite, nonnegative, and have positive total")
    scale = max(weights)
    return rng.choices(information.actions, weights=tuple(w / scale for w in weights), k=1)[0]


class ExploratoryHistoryPolicy:
    """!
    @brief A preferred visible action plus positive probability for every alternative.
    """
    def __init__(self, policy, *, exploration=0.2):
        if not callable(policy):
            raise TypeError("Supply an observation-only preferred-action policy")
        if not isfinite(exploration) or not 0 < exploration <= 1:
            raise ValueError("History exploration must be in (0, 1]")
        self.policy, self.exploration = policy, exploration

    def __call__(self, information):
        preferred = self.policy(information)
        if preferred not in information.actions:
            raise ValueError("Preferred history action is not offered")
        base = self.exploration / len(information.actions)
        return tuple(base + (1 - self.exploration if action == preferred else 0)
                     for action in information.actions)


class HistoryWorldProvider:
    """!
    @brief Return a sample-seed -> simulator factory from detached player information.

    world_factory(seed) must sample the declared prior and build a fresh tracking
    simulator at the known origin. It must not inspect a live game or capture its
    hidden state. opponent_weights(info) is pure, sees only that opponent's input,
    and returns nonnegative finite weights aligned with the offered actions.

    An optional world_factory.condition(information) returns a per-target factory
    that samples the prior exactly conditional on a necessary static constraint, such as the
    own complete setup deals or a provably untouched own library prefix for a fixed
    declared deck, or a revealed opponent card necessarily lying in an untouched
    opening prefix. The constraint must hold for EVERY world/path matching the
    target; remaining history rejection retains policy likelihoods. It must not force arbitrary
    opponent responses or state-dependent later draws: those still require the
    joint history likelihood. The normal seed-only factory contract is unchanged.

    This is an exact rejection baseline for the declared finite-history model,
    conditional on success. Long/rare histories can exhaust the attempt budget.
    Work-cap interruptions and unsupported rules propagate rather than rejecting
    only expensive samples. There is no general live-State import or deck inference.
    """
    def __init__(self, world_factory, *, opponent_weights=None, max_attempts=32, max_prefix_advances=256):
        if not callable(world_factory):
            raise TypeError("Supply a fresh scenario prior factory")
        if opponent_weights is not None and not callable(opponent_weights):
            raise TypeError("Supply an observation-only opponent weight model")
        for name, value in (("max_attempts", max_attempts), ("max_prefix_advances", max_prefix_advances)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.world_factory = world_factory
        self.opponent_weights = opponent_weights if opponent_weights is not None else UniformHistoryPolicy()
        self.max_attempts, self.max_prefix_advances = max_attempts, max_prefix_advances

    def __call__(self, information):
        # Optional exact conditioning on a necessary prior constraint.
        # Capture a factory per request, never mutate the prior
        # shared with older replay worlds or an incremental population.
        condition = getattr(self.world_factory, "condition", None)
        factory = condition(information) if condition is not None else self.world_factory
        if not callable(factory):
            raise TypeError("condition(information) must return a world factory")
        return lambda seed: _HistorySample(self, information, seed, factory)


class _HistorySample:
    """!
    @brief Lazily accept a compatible sampled history and delegate to its native backend.
    """
    def __init__(self, provider, information, seed, world_factory):
        if type(seed) is not int:
            raise ValueError("A deterministic integer sample seed is required")
        self.provider, self.information, self.seed = provider, information, seed
        self.world_factory = world_factory
        self._simulator = self._root = None
        self._attempts = self._rejected = self._advances = 0
        self._mismatches = Counter()

    @property
    def simulation_backend(self):
        """!
        @brief Expose the accepted native simulator without resampling its world.
        """
        return self._simulator

    @property
    def initialization_metrics(self):
        """!
        @brief Return detached diagnostics from sampled-world acceptance.
        """
        return WorldInitializationMetrics(self._attempts, self._rejected, self._advances,
                                          mismatch_reasons=tuple(sorted(self._mismatches.items())))

    def _advance(self):
        self._advances += 1

    def start(self):
        """!
        @brief Initialize the owned scenario and expose its first supported boundary.
        """
        if self._root is not None:
            return self._root
        # Restarting an unsuccessful initialization is deterministic too.
        self._attempts = self._rejected = self._advances = 0
        self._mismatches.clear()
        worlds, paths = Random(self.seed), Random(self.seed ^ 0x48A1570)
        for _ in range(self.provider.max_attempts):
            self._attempts += 1
            simulator = self.world_factory(worlds.getrandbits(64))
            path_rng = Random(paths.getrandbits(64))

            def choose(info):
                return sample_history_action(self.provider.opponent_weights, info, path_rng)

            try:
                root = resume_to_information(simulator, self.information, choose,
                                             self.provider.max_prefix_advances, on_advance=self._advance)
            except SampledRootMismatch as exc:
                self._rejected += 1
                self._mismatches[exc.reason] += 1
                continue
            self._simulator, self._root = simulator, root
            return root
        raise HistorySamplingExhausted("No history-compatible world within max_attempts")

    def advance_action(self, node, action):
        """!
        @brief Bind and execute one complete visible action in an isolated world.
        """
        if self._simulator is None:
            raise ValueError("Initialize a history-compatible world before advancing")
        return self._simulator.advance_action(node, action)

    def expand_candidates(self, node, limit):
        """!
        @brief Resume the native frontier to admit a wider candidate prefix.
        """
        return self._simulator.expand_candidates(node, limit)

    def follow_actions(self, choose, *, node=None):
        """!
        @brief Execute callback-selected complete actions within one owned runtime.
        """
        if self._simulator is None:
            raise ValueError("Initialize a history-compatible world before following")
        follow = getattr(self._simulator, "follow_actions", None)
        if follow is not None:
            return follow(choose, node=node)
        current = node if node is not None else self._root
        while not current.terminal and not current.cutoff:
            action = choose(current)
            if action is None:
                return current
            current = self.advance_action(current, action)
        return current
