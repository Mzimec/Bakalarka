"""Budgeted two-player minimax for a fixed, explicitly known simulator world.

This is a tactical correctness baseline, not an imperfect-information agent.
Opponent menus/draws belong to the supplied world; do not use the real hidden
match state as an implicit determinization. No production selector is replaced.
"""
from dataclasses import dataclass
from math import inf, isfinite, nextafter
from time import perf_counter

from game.ai.search_descriptors import ChoiceDescriptor
from game.ai.search_simulator import RecordedChoice, SimulationBudgetExceeded
from game.ai.state_value import StateValueEvaluator
from game.ai.search_horizon import quiet_priority


@dataclass(frozen=True)
class SearchBudget:
    """!
    @brief Immutable work and horizon limits for the perfect-information reference.
    @var max_depth Decision depth before settled-leaf evaluation, not a turn count.
    @var max_advances Hard limit on logical action transitions during one search.
    @var max_extensions Additional nonforced steps permitted while seeking a settled leaf.
    """
    max_depth: int = 3
    max_advances: int = 256
    max_extensions: int = 8

    def __post_init__(self):
        for name in ("max_depth", "max_advances", "max_extensions"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "max_extensions" else 1):
                raise ValueError(f"Invalid {name}")


@dataclass(frozen=True)
class RootEvaluation:
    """!
    @brief Completed value estimate for one perfect-information root choice.
    @var choice Recommended action or explicitly reported fallback choice.
    @var value Value retained by this record.
    @var variation Variation retained by this record.
    """
    choice: ChoiceDescriptor
    value: float
    variation: tuple[RecordedChoice, ...]


@dataclass(frozen=True)
class SearchMetrics:
    """!
    @brief Detached counters for the perfect-information reference search.
    @var advance_attempts Logical transitions, including cached and forced steps.
    @var cache_hits Number of cache hits recorded by this component.
    @var visited_nodes Number of visited nodes recorded by this component.
    @var evaluated_leaves Evaluated leaves retained by this record.
    @var terminal_leaves Number of accepted terminal outcomes.
    @var pruned_choices Number of pruned choices recorded by this component.
    @var elapsed_ms Recorded elapsed in milliseconds.
    """
    advance_attempts: int
    cache_hits: int
    visited_nodes: int
    evaluated_leaves: int
    terminal_leaves: int
    pruned_choices: int
    elapsed_ms: float


@dataclass(frozen=True)
class SearchResult:
    """!
    @brief Perfect-information recommendation with completion diagnostics.
    @var choice Recommended action or explicitly reported fallback choice.
    @var value Value retained by this record.
    @var completed_depth Completed depth retained by this record.
    @var interrupted_depth Interrupted depth retained by this record.
    @var stop_reason Reason the bounded search loop stopped.
    @var used_fallback Whether the result recommends the configured fallback.
    @var root_evaluations Number of root evaluations recorded by this component.
    @var variation Variation retained by this record.
    @var metrics Detached work, timing and cache diagnostics.
    """
    choice: ChoiceDescriptor | None
    value: float | None
    completed_depth: int
    interrupted_depth: int | None
    stop_reason: str
    used_fallback: bool
    root_evaluations: tuple[RootEvaluation, ...]
    variation: tuple[RecordedChoice, ...]
    metrics: SearchMetrics


class _Interrupted(Exception):
    """!
    @brief Stop an incomplete trajectory without inventing a game outcome.
    """
    pass


class PerfectInformationSearch:
    """!
    @brief Iterative deepening; use the deepest *fully completed* root iteration.

    Depth counts decisions, not alternating players or turns. The actual requesting
    player determines max/min, including retained priority and nested effect choices.
    An unfinished iteration cannot overwrite comparable results from the previous
    depth. If depth one cannot complete, return an explicitly unscored fallback.
    """
    def __init__(self, *, budget=None, evaluator=None):
        self.budget = budget if budget is not None else SearchBudget()
        self.evaluator = evaluator if evaluator is not None else StateValueEvaluator()

    def search(self, simulator, root, *, player=None, fallback=None):
        """!
        @brief Run bounded search and return a detached recommendation with diagnostics.
        """
        started = perf_counter()
        if player is None:
            if root.decision is None:
                raise ValueError("Specify the evaluation player at a terminal node")
            player = root.decision.player
        view = root.observation(player)
        if view.get("setup") or len(view["players"]) != 2:
            raise ValueError("Search requires an in-game two-player position")
        if player not in {p["id"] for p in view["players"]}:
            raise ValueError("Evaluation player is not a participant")
        if root.cutoff:
            raise ValueError("Cannot search a simulator cutoff")
        if not root.terminal and (root.decision is None or root.decision.player != player):
            raise ValueError("The root decision must belong to the evaluation player")
        run = _SearchRun(simulator, player, self.budget, self.evaluator)
        if root.terminal:
            value, variation = run.leaf(root)
            return SearchResult(None, value, 0, None, "terminal", False, (), variation, run.metrics(started))
        if not root.choices:
            raise ValueError("Root has no legal choices")
        if fallback is None:
            fallback = next((c for c in root.choices if c.kind == "PassPriorityAction"), root.choices[0])
        if fallback not in root.choices:
            raise ValueError("Fallback must be a choice offered at the root")
        completed = 0
        evaluations = ()
        interrupted = None
        reason = "depth_limit"
        for depth in range(1, self.budget.max_depth + 1):
            try:
                current = []
                for choice in root.choices:
                    child = run.advance(root, choice)
                    # Independent full windows make all reported root values
                    # exact for this truncated tree, rather than alpha-beta bounds.
                    value, tail = run.visit(child, depth - 1, self.budget.max_extensions, -inf, inf)
                    current.append(RootEvaluation(choice, value, (RecordedChoice(root.decision, choice), *tail)))
                evaluations, completed = tuple(current), depth
            except _Interrupted as exc:
                interrupted, reason = depth, str(exc)
                break
        if evaluations:
            best = max(evaluations, key=lambda row: row.value)  # Stable ties.
            return SearchResult(best.choice, best.value, completed, interrupted, reason,
                                False, evaluations, best.variation, run.metrics(started))
        return SearchResult(fallback, None, 0, interrupted, reason, True, (), (), run.metrics(started))


class _SearchRun:
    """!
    @brief Own recursive perfect-information selection and work accounting.
    """
    def __init__(self, simulator, player, budget, evaluator):
        self.simulator, self.player, self.budget, self.evaluator = simulator, player, budget, evaluator
        # Prefix cache only: no claim that different histories are interchangeable.
        self.children = {}
        self.attempts = self.hits = self.visits = self.leaves = self.terminals = self.pruned = 0

    def advance(self, node, choice):
        """!
        @brief Execute or reuse one transition under configured replay and budget checks.
        """
        key = (node.history, choice)
        if key in self.children:
            self.hits += 1
            return self.children[key]
        if self.attempts >= self.budget.max_advances:
            raise _Interrupted("advance_budget")
        self.attempts += 1
        try:
            child = self.simulator.advance(node, choice)
        except SimulationBudgetExceeded as exc:
            raise _Interrupted("simulator_budget") from exc
        self.children[key] = child
        return child

    def leaf(self, node):
        """!
        @brief Evaluate a settled leaf within the configured search horizon.
        """
        self.leaves += 1
        view = node.observation(self.player)
        if node.terminal:
            self.terminals += 1
            value = StateValueEvaluator().evaluate(view)
        else:
            value = float(self.evaluator.evaluate(view))
            if not isfinite(value) or not -1 <= value <= 1:
                raise ValueError("Leaf evaluator must return a finite value in [-1, 1]")
            # A heuristic leaf must never tie an actual terminal win/loss.
            limit = nextafter(1.0, 0.0)
            value = max(-limit, min(limit, value))
        return value, ()

    def visit(self, node, depth, extension, alpha, beta):
        """!
        @brief Select and back up one bounded perfect-information trajectory.
        """
        self.visits += 1
        if node.terminal:
            return self.leaf(node)
        if node.cutoff:
            raise _Interrupted("simulator_cutoff")
        if node.decision is None or not node.choices:
            raise ValueError("Nonterminal search node needs a decision and choices")
        if depth <= 0:
            # Paying for a spell temporarily loses material; evaluating before
            # it resolves creates a severe horizon artifact. Do not auto-pass:
            # search every offered response/mandatory choice until stable.
            view = node.observation(self.player)
            if quiet_priority(node.decision.kind, view):
                return self.leaf(node)
            if extension == 0:
                raise _Interrupted("extension_limit")
            extension -= 1
        maximize = node.decision.player == self.player
        value = -inf if maximize else inf
        variation = ()
        for index, choice in enumerate(node.choices):
            child = self.advance(node, choice)
            score, tail = self.visit(child, depth - 1, extension, alpha, beta)
            better = score > value if maximize else score < value
            if better:
                value, variation = score, (RecordedChoice(node.decision, choice), *tail)
            if maximize:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if alpha >= beta:
                self.pruned += len(node.choices) - index - 1
                break
        return value, variation

    def metrics(self, started):
        """!
        @brief Return detached counters for the completed bounded search.
        """
        return SearchMetrics(self.attempts, self.hits, self.visits, self.leaves,
                             self.terminals, self.pruned, (perf_counter() - started) * 1000)
