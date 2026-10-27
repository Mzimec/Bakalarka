"""Bounded RAM-only cache of detached numeric features and pure evaluations."""
from collections import Counter, OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CachedEvaluation:
    """!
    @brief One detached cache result and its conservative storage charge.
    @var value Immutable numeric features, prediction or value estimate.
    @var charged_bytes Accounted Python storage, not measured process RSS.
    """

    value: object
    charged_bytes: int


class EvaluationCache:
    """!
    @brief Bound detached numeric features and evaluations by entries and charged bytes.
    @var rows LRU-ordered keys mapped to explicit CachedEvaluation records.
    @var bytes Current accounted cache storage, independent of process RSS.
    @var counts Per-kind cache hit/miss and eviction counters.
    """

    def __init__(self, entries: int = 256, byte_limit: int = 32 * 1024 * 1024) -> None:
        """!
        @brief Configure a bounded RAM-only LRU with empty counters and owned rows.
        @throws ValueError If an entry or byte limit is not a nonnegative integer.
        """
        if any(type(value) is not int or value < 0 for value in (entries, byte_limit)):
            raise ValueError("Evaluation cache limits must be nonnegative integers")
        self.entries, self.byte_limit = entries, byte_limit
        self.rows: OrderedDict[tuple, CachedEvaluation] = OrderedDict()
        self.bytes = 0
        self.counts: Counter[str] = Counter()

    def get(self, key: tuple) -> object | None:
        """!
        @brief Look up a cached result and update the owner access accounting.
        """
        found = self.rows.get(key)
        self.counts[key[0] + "_hits" if found is not None else key[0] + "_misses"] += 1
        if found is not None:
            self.rows.move_to_end(key)
            return found.value
        return None

    def put(self, key: tuple, value: object, numeric_count: int) -> object:
        """!
        @brief Store a detached result under the configured entry and byte limits.
        """
        # Charge Python numeric tuples, keys and LRU bookkeeping conservatively.
        charge = 512 + len(repr(key)) * 4 + numeric_count * 48
        if not self.entries or not self.byte_limit or charge > self.byte_limit:
            return value
        previous = self.rows.pop(key, None)
        if previous is not None:
            self.bytes -= previous.charged_bytes
        while self.rows and (len(self.rows) >= self.entries or self.bytes + charge > self.byte_limit):
            _, evicted = self.rows.popitem(last=False)
            self.bytes -= evicted.charged_bytes
            self.counts["evictions"] += 1
        self.rows[key] = CachedEvaluation(value, charge)
        self.bytes += charge
        return value

    def clear_outputs(self) -> None:
        """!
        @brief Discard model outputs while preserving reusable encoded features.
        """
        for key in tuple(self.rows):
            if key[0] in ("prediction", "value"):
                self.bytes -= self.rows.pop(key).charged_bytes

    def summary(self) -> dict[str, int]:
        """!
        @brief Return detached counters and current configured resource limits.
        """
        return dict(self.counts, entries=len(self.rows), charged_bytes=self.bytes,
                    entry_limit=self.entries, byte_limit=self.byte_limit)
