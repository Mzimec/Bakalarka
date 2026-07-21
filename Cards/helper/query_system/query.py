from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Hashable
from typing import overload, override
from dataclasses import dataclass

from ..runtime_object import RuntimeObject
from .object_register import IndexProvider, ObjectStorage, BitSet, IndexKey, ComparableHashable


class QueryContext[T: RuntimeObject]:

    def __init__(self, storage: ObjectStorage[T], idx_provider: IndexProvider):
        self.storage: ObjectStorage[T] = storage
        self.idx_provider: IndexProvider = idx_provider

class Query(ABC):

    @abstractmethod
    def eval(self, ctx: QueryContext) -> BitSet: ...

    @abstractmethod
    def estimate(self, ctx: QueryContext) -> int: ...

    @overload
    def __and__(self, other: Query) -> AndQuery: ...
    @overload
    def __and__(self, other: Iterable[Query]) -> AndQuery: ...
    def __and__(self, other: Iterable[Query] | Query) -> AndQuery:
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
    
    idx_key: IndexKey[KT]
    key: KT
    
    @override
    def eval(self, ctx):
        return ctx.idx_provider.get_index_group(self.idx_key).get(self.key, BitSet())
    
    @override
    def estimate(self, ctx):
        group = ctx.idx_provider.get_index_group(self.idx_key)
        bs = group.get(self.key)
        if bs is None:
            return 0
        return bs.count()


@dataclass(frozen=True)
class InQuery[KT: Hashable](Query):

    idx_key: IndexKey[KT]
    keys: frozenset[KT]
    
    @override
    def eval(self, ctx):
        group = ctx.idx_provider.get_index_group(self.idx_key)
        res = BitSet()
        res.update(bs for k in self.keys if (bs := group.get(k)) is not None)
        return res
    
    @override
    def estimate(self, ctx):
        group = ctx.idx_provider.get_index_group(self.idx_key)
        return min(
            len(ctx.storage),
            sum(bs for k in self.keys if (bs := group.get(k)) is not None)
        )


@dataclass(frozen=True)
class RangeQuery[KT: ComparableHashable](Query):

    idx_key: IndexKey[KT]
    min_value: KT | None = None
    max_value: KT | None = None
        
    @override
    def eval(self, ctx):
        group = ctx.idx_provider.get_ordered_group(self.idx_key)
        
        scoped_group = group.scope_map(self.min_value, self.max_value)

        res = BitSet()
        res.update(scoped_group.values())
        return res
    
    @override
    def estimate(self, ctx):
        group = ctx.idx_provider.get_ordered_group(self.idx_key)
        scoped_group = group.scope_map(self.min_value, self.max_value)
        return min(
            len(ctx.storage),
            sum(scoped_group.values())
        )


@dataclass(frozen=True)
class AndQuery(Query):

    children: tuple[Query, ...]

    @override
    def eval(self, ctx):
        if not self.children:
            return BitSet()
        
        sorted_children = sorted(self.children, key=lambda child: child.estimate(ctx))
        base_bs = sorted_children[0].eval(ctx)
        return base_bs.intersection(child.eval(ctx) for child in sorted_children[1:])
    
    @override
    def estimate(self, ctx):
        if not self.children:
            return 0
        
        return min(child.estimate(ctx) for child in self.children)


@dataclass(frozen=True)
class OrQuery(Query):

    children: tuple[Query, ...]
    
    @override
    def eval(self, ctx):
        if not self.children:
            return BitSet()
        
        base_bs = self.children[0].eval(ctx)
        return base_bs.union(child.eval(ctx) for child in self.children[1:])

    @override
    def estimate(self, ctx):
        return min(
            len(ctx.storage),
            sum(child.estimate(ctx) for child in self.children)
        )
        

@dataclass(frozen=True)
class DifferenceQuery(Query):

    base: Query
    excludes: tuple[Query, ...]

    @override
    def eval(self, ctx):
        base_bs = self.base.eval(ctx)
        if not base_bs:
            return BitSet()
        
        if not self.excludes:
            return base_bs

        return base_bs.difference((ex.eval(ctx) for ex in self.excludes))

    @override
    def estimate(self, ctx):
        if not self.excludes:
            return self.base.estimate(ctx)
        
        max_exclude = max(ex.estimate(ctx) for ex in self.excludes)
        return max(0, self.base.estimate(ctx) - max_exclude)
    

@dataclass(frozen=True)
class HasQuery[KT](Query):

    idx_key: IndexKey[KT]

    @override
    def eval(self, ctx):
        group = ctx.idx_provider.get_index_group(self.idx_key)        
        res = BitSet()
        res.update(group.values())
        return res
    
    @override
    def estimate(self, ctx):
        self.eval(ctx).count()
    
