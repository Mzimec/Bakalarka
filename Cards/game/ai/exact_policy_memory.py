"""Bounded RAM-only memoization of exact built-in per-action ratings."""
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from sys import getsizeof
from time import perf_counter

from game.ai.search_abstraction import CanonicalPolicyView, CanonicalAction
from game.ai.search_actions import PlayerAction
from game.ai.structured_values import Value


@dataclass(frozen=True, slots=True)
class PolicyRating:
    """!
    @brief Pure rating without native bindings, tree visits or sampled outcomes.
    @var score Original heuristic score, unchanged by memoization.
    @var supported Whether the built-in scorer supports these action semantics.
    @var reasons Original diagnostic explanations.
    """
    score: float
    supported: bool
    reasons: tuple[str, ...]


@dataclass(slots=True)
class PolicyMemoryEntry:
    """!
    @brief One canonical input and independently reusable complete-action ratings.
    @var ratings Complete canonical actions mapped to pure ratings.
    @var charged_bytes Conservative storage charge including lazy immutable readers.
    """
    ratings: dict[CanonicalAction, PolicyRating] = field(default_factory=dict)
    charged_bytes: int = 0


class ExactPolicyMemory:
    """!
    @brief Reuse ratings under exact policy inputs, including across different menus.

    Owned by one policy/worker. Values contain detached immutable facts only.
    No approximate value, native world or completed trajectory is stored here.
    @var entries Maximum number of canonical contexts retained in the LRU.
    @var byte_limit Maximum charged storage, independent of process RSS.
    @var counts Cumulative hits, misses, normalization time and evictions.
    """
    def __init__(self, entries=128, byte_limit=16 * 1024 * 1024, *, canonical_ids=False):
        if any(type(value) is not int or value < 0 for value in (entries, byte_limit)):
            raise ValueError('Exact policy memory limits must be nonnegative integers')
        self.entries, self.byte_limit = entries, byte_limit
        if type(canonical_ids) is not bool:
            raise ValueError('canonical_ids must be boolean')
        self.canonical_ids = canonical_ids
        self.rows = OrderedDict()
        self.bytes = 0
        self.counts = Counter()
        self._source = self._context = None
        self._actions = {}
        self._rank_menu = frozenset()
        self._rank_rows = ()
        self._memo_bytes = 0

    def __getstate__(self):
        # Worker jobs receive the configuration, not megabytes of the parent's
        # past actor views. Every worker owns and warms its own pure cache.
        return self.entries, self.byte_limit, self.canonical_ids

    def __setstate__(self, state):
        entries, byte_limit, canonical_ids = state
        self.__init__(entries, byte_limit, canonical_ids=canonical_ids)

    @staticmethod
    def _charge(value):
        # Count shared immutable subtrees once within each owned graph, including
        # headroom for lazily created Value readers and their field indexes.
        seen, stack, total = set(), [value], 512
        while stack:
            item = stack.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            total += getsizeof(item) + 128
            if isinstance(item, Value):
                total += getsizeof(item.__dict__)
                stack.extend((item.kind, item.items, item.digest))
                if item.kind == 'object':
                    total += 128 * len(item.items)
                elif item.kind == 'array':
                    total += getsizeof(item.items)  # Reserve the lazy tuple reader too.
            elif isinstance(item, (tuple, list)):
                stack.extend(item)
            elif isinstance(item, (set, frozenset)):
                stack.extend(item)
            elif isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, CanonicalPolicyView):
                total += getsizeof(item.__dict__)
                stack.extend((item.value, item.bindings, item.external, item.counter_present, item.player, item.request_kind))
            elif isinstance(item, CanonicalAction):
                stack.extend((item.kind, item.value))
            elif isinstance(item, PlayerAction):
                stack.extend((item.kind, item.structured.value))
            elif isinstance(item, PolicyRating):
                stack.extend((item.score, item.supported, item.reasons))
        return total

    def clear(self):
        """!
        @brief Release pure results after reset or an evaluator/model change.
        """
        self.rows.clear()
        self.bytes = 0
        self._source = self._context = None
        self._actions = {}
        self._rank_menu = frozenset()
        self._rank_rows = ()
        self._memo_bytes = 0

    def rank(self, information, counter_sources, scorer_factory):
        """!
        @brief Return exactly ranked local actions, evaluating only missing actions.
        @param scorer_factory Reviewed scorer receiving only the canonical input.
        """
        started = perf_counter()
        source = CanonicalPolicyView.source_key(information, counter_sources)
        offered = frozenset(information.actions)
        if source == self._source and offered == self._rank_menu and self._rank_rows:
            self.counts['view_hits'] += 1
            self.counts['action_hits'] += len(information.actions)
            self.counts['normalization_ms'] += (perf_counter() - started) * 1000
            from game.ai.observation_policy import ActionRating
            local = {action: action for action in information.actions}
            return tuple(ActionRating(local[action], score, supported, reasons)
                         for action, score, supported, reasons in self._rank_rows)
        fresh = source != self._source
        if fresh:
            self._context = CanonicalPolicyView(information, counter_sources, canonical_ids=self.canonical_ids)
            self._source, self._actions = source, {}
        context = self._context
        actions = []
        for action in information.actions:
            raw = action.kind, action.structured.value
            canonical = self._actions.get(raw)
            if canonical is None:
                canonical = self._actions[raw] = context.action(action)
            actions.append(canonical)
        self.counts['normalization_ms'] += (perf_counter() - started) * 1000
        key = context.key
        entry = self.rows.get(key)
        self.counts['view_hits' if entry is not None else 'view_misses'] += 1
        if entry is not None:
            self.rows.move_to_end(key)
        else:
            entry = PolicyMemoryEntry()
        scorer, result = None, []
        for original, canonical in zip(information.actions, actions):
            rating = entry.ratings.get(canonical)
            self.counts['action_hits' if rating is not None else 'action_misses'] += 1
            if rating is None:
                if scorer is None:
                    scorer = scorer_factory(context.value.readonly, context.counter_present)
                # Score canonical facts, then bind only the scalar result back
                # to the freshly offered original action. Sorting/ties stay local.
                from game.ai.structured_values import StructuredJSON
                scored = scorer.rate(PlayerAction(canonical.kind, StructuredJSON(canonical.value)))
                rating = PolicyRating(scored.score, scored.supported, scored.reasons)
                entry.ratings[canonical] = rating
            from game.ai.observation_policy import ActionRating
            result.append(ActionRating(original, rating.score, rating.supported, rating.reasons))
        result.sort(key=lambda row: (-row.score, row.action.kind != 'PassPriorityAction', row.action.sort_key))
        self._rank_menu = offered
        self._rank_rows = tuple((row.action, row.score, row.supported, row.reasons) for row in result)
        self._memo_bytes = self._charge((source, context, self._actions, self._rank_menu, self._rank_rows))
        entry.charged_bytes = self._charge((key, entry.ratings))
        self.rows.pop(key, None)
        self.bytes = sum(row.charged_bytes for row in self.rows.values())
        if self.entries and entry.ratings and entry.charged_bytes + self._memo_bytes <= self.byte_limit:
            while self.rows and (len(self.rows) >= self.entries or self.bytes + entry.charged_bytes + self._memo_bytes > self.byte_limit):
                _, evicted = self.rows.popitem(last=False)
                self.bytes -= evicted.charged_bytes
                self.counts['evictions'] += 1
            self.rows[key] = entry
            self.bytes += entry.charged_bytes
        if not self.entries or self._memo_bytes > self.byte_limit:
            self._source = self._context = None
            self._actions = {}
            self._rank_menu = frozenset()
            self._rank_rows = ()
            self._memo_bytes = 0
        while self.rows and self.bytes + self._memo_bytes > self.byte_limit:
            _, evicted = self.rows.popitem(last=False)
            self.bytes -= evicted.charged_bytes
            self.counts['evictions'] += 1
        self.bytes += self._memo_bytes
        return tuple(result)

    def summary(self):
        """!
        @brief Report detached diagnostics and configured RAM limits.
        """
        return dict(self.counts, entries=len(self.rows), charged_bytes=self.bytes,
                    entry_limit=self.entries, byte_limit=self.byte_limit)
