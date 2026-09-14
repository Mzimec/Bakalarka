"""Composable indexed queries with candidate-size estimates."""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Hashable
from typing import overload, override
from dataclasses import dataclass

from ..runtime_object import RuntimeObject
from .object_register import IndexProvider, ObjectStorage, BitSet, IndexKey, ComparableHashable


class QueryContext[T: RuntimeObject]:
    """!
    @brief Provide object storage and indexes for query evaluation.
    """

    def __init__(self, storage: ObjectStorage[T], idx_provider: IndexProvider):
        self.storage: ObjectStorage[T] = storage
        self.idx_provider: IndexProvider = idx_provider


class Query(ABC):
    """!
    @brief Base contract for composable indexed object queries.

    Queries produce a `BitSet` of runtime ids and may expose a cheap candidate
    estimate used to choose a more efficient evaluation order.
    """

    @abstractmethod
    def eval(self, ctx: QueryContext) -> BitSet: ...

    @abstractmethod
    def estimate(self, ctx: QueryContext) -> int: ...

    @overload
    def __and__(self, other: Query) -> AndQuery: ...
    @overload
    def __and__(self, other: Iterable[Query]) -> AndQuery: ...
    def __and__(self, other: Iterable[Query] | Query) -> AndQuery:
        """!
        @brief Combine this query with one or more queries by intersection.

        Nested `AndQuery` instances are flattened so evaluation works over one
        child sequence instead of building a recursive binary tree.
        """
        left_side: tuple[Query, ...] = self.children if isinstance(self, AndQuery) else (self,)

        if isinstance(other, AndQuery):
            return AndQuery((*left_side, *other.children))
        elif isinstance(other, Query):
            return AndQuery((*left_side, other))

        children: list[Query] = []
        for q in other:
            if isinstance(q, AndQuery):
                children.extend(q.children)
            else:
                children.append(q)

        return AndQuery((*left_side, *children))

    @overload
    def __or__(self, other: Query) -> OrQuery: ...
    @overload
    def __or__(self, other: Iterable[Query]) -> OrQuery: ...
    def __or__(self, other: Iterable[Query] | Query) -> OrQuery:
        """!
        @brief Combine this query with one or more queries by union.

        Nested `OrQuery` instances are flattened for the same reason as `AndQuery`.
        """
        left_side: tuple[Query, ...] = self.children if isinstance(self, OrQuery) else (self,)

        if isinstance(other, OrQuery):
            return OrQuery((*left_side, *other.children))
        if isinstance(other, Query):
            return OrQuery((*left_side, other))

        children: list[Query] = []
        for q in other:
            if isinstance(q, OrQuery):
                children.extend(q.children)
            else:
                children.append(q)

        return OrQuery((*left_side, *children))


@dataclass(frozen=True)
class EqQuery[KT: Hashable](Query):
    """!
    @brief Select objects whose indexed value equals one value.
    """

    idx_key: IndexKey[KT]
    key: KT

    @override
    def eval(self, ctx):
        """!
        @brief Return the bit set stored under the requested index value.
        """
        return BitSet(ctx.idx_provider.get_index_group(self.idx_key).get(self.key, BitSet()))

    @override
    def estimate(self, ctx):
        """!
        @brief Return the exact current size of the corresponding index bucket.
        """
        group = ctx.idx_provider.get_index_group(self.idx_key)
        bs = group.get(self.key)
        if bs is None:
            return 0
        return bs.count()


@dataclass(frozen=True)
class InQuery[KT: Hashable](Query):
    """!
    @brief Select objects whose indexed value belongs to a supplied set.
    """

    idx_key: IndexKey[KT]
    keys: frozenset[KT]

    @override
    def eval(self, ctx):
        """!
        @brief Union all index buckets belonging to the requested values.
        """
        group = ctx.idx_provider.get_index_group(self.idx_key)
        res = BitSet()
        res.update(bs for k in self.keys if (bs := group.get(k)) is not None)
        return res

    @override
    def estimate(self, ctx):
        """!
        @brief Estimate result size from the summed bucket sizes.

        Different buckets may overlap, so the estimate is capped by the number
        of stored objects.
        """
        group = ctx.idx_provider.get_index_group(self.idx_key)
        return min(
            len(ctx.storage), sum(bs.count() for k in self.keys if (bs := group.get(k)) is not None)
        )


@dataclass(frozen=True)
class RangeQuery[KT: ComparableHashable](Query):
    """!
    @brief Select objects whose indexed value lies within an inclusive range.
    """

    idx_key: IndexKey[KT]
    min_value: KT | None = None
    max_value: KT | None = None

    @override
    def eval(self, ctx):
        """!
        @brief Union all index buckets inside the requested ordered range.
        """
        group = ctx.idx_provider.get_ordered_group(self.idx_key)

        scoped_group = group.scope_map(self.min_value, self.max_value)

        res = BitSet()
        res.update(scoped_group.values())
        return res

    @override
    def estimate(self, ctx):
        """!
        @brief Estimate result size from the buckets covered by the range.
        """
        group = ctx.idx_provider.get_ordered_group(self.idx_key)
        scoped_group = group.scope_map(self.min_value, self.max_value)
        return min(len(ctx.storage), sum(bs.count() for bs in scoped_group.values()))


@dataclass(frozen=True)
class AndQuery(Query):
    """!
    @brief Intersect the results of child queries.
    """

    children: tuple[Query, ...]

    @override
    def eval(self, ctx):
        """!
        @brief Evaluate the most selective child first and intersect the rest.

        Starting with the smallest estimated candidate set minimizes the size of
        the intermediate bit set used by later intersections.
        """
        if not self.children:
            return BitSet()

        sorted_children = sorted(self.children, key=lambda child: child.estimate(ctx))
        base_bs = sorted_children[0].eval(ctx)
        return base_bs.intersection(child.eval(ctx) for child in sorted_children[1:])

    @override
    def estimate(self, ctx):
        """!
        @brief Bound the intersection size by its smallest child estimate.
        """
        if not self.children:
            return 0

        return min(child.estimate(ctx) for child in self.children)


@dataclass(frozen=True)
class OrQuery(Query):
    """!
    @brief Combine the results of child queries.
    """

    children: tuple[Query, ...]

    @override
    def eval(self, ctx):
        """!
        @brief Evaluate all children and union their result bit sets.
        """
        if not self.children:
            return BitSet()

        base_bs = self.children[0].eval(ctx)
        return base_bs.union(child.eval(ctx) for child in self.children[1:])

    @override
    def estimate(self, ctx):
        """!
        @brief Estimate the union by summing child estimates and cap by storage size.
        """
        return min(len(ctx.storage), sum(child.estimate(ctx) for child in self.children))


@dataclass(frozen=True)
class DifferenceQuery(Query):
    """!
    @brief Subtract excluded query results from an initial result set.
    """

    base: Query
    excludes: tuple[Query, ...]

    @override
    def eval(self, ctx):
        """!
        @brief Evaluate the base query and remove all matching excluded ids.
        """
        base_bs = self.base.eval(ctx)
        if not base_bs:
            return BitSet()

        if not self.excludes:
            return base_bs

        return base_bs.difference((ex.eval(ctx) for ex in self.excludes))

    @override
    def estimate(self, ctx):
        """!
        @brief Estimate the remaining candidates after exclusions.

        Only the largest exclusion estimate is subtracted, keeping this a cheap
        heuristic rather than attempting to model overlap between exclusions.
        """
        if not self.excludes:
            return self.base.estimate(ctx)

        max_exclude = max(ex.estimate(ctx) for ex in self.excludes)
        return max(0, self.base.estimate(ctx) - max_exclude)


@dataclass(frozen=True)
class HasQuery[KT](Query):
    """!
    @brief Select objects present in at least one bucket of an index.
    """

    idx_key: IndexKey[KT]

    @override
    def eval(self, ctx):
        """!
        @brief Union every bucket in the requested index group.
        """
        group = ctx.idx_provider.get_index_group(self.idx_key)
        res = BitSet()
        res.update(group.values())
        return res

    @override
    def estimate(self, ctx):
        """!
        @brief Return the exact number of ids currently matched by this query.
        """
        return self.eval(ctx).count()