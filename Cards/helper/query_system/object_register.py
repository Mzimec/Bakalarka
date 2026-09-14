"""Indexed object storage, bit sets and incremental query synchronization."""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Hashable, Mapping, Iterator, Callable
from typing import overload, Protocol, TYPE_CHECKING, TypeVar
from dataclasses import dataclass

if TYPE_CHECKING:
    from .query import Query, QueryContext

from ..runtime_object import RuntimeObject

RT = TypeVar("RT", bound=RuntimeObject)


@dataclass(frozen=True)
class IndexUpdate[HT: Hashable]:
    """!
    @brief Values removed from and added to one index group.

    @var remove
        Characteristic values the object no longer belongs to.
    @var add
        Characteristic values the object newly belongs to.
    """

    remove: Iterable[HT]
    add: Iterable[HT]


@dataclass(frozen=True)
class RegisterUpdateContext[RT: RuntimeObject]:
    """!
    @brief Complete set of index membership changes for one register operation.

    Each `IndexKey` maps to the values removed from or added to that index.
    """

    data: dict[IndexKey[Hashable], IndexUpdate[Hashable]]


class ComparableHashable(Hashable, Protocol):
    """!
    @brief Hashable index value that can also participate in ordered queries.
    """

    def __lt__(self, other) -> bool: ...

    def __eq__(self, value) -> bool: ...


class ObjectRegister[RT: RuntimeObject](ABC):
    """!
    @brief Registry contract combining object storage with derived query indexes.
    """

    @property
    @abstractmethod
    def storage(self) -> ObjectStorage[RT]: ...

    @property
    @abstractmethod
    def idx_provider(self) -> ObjectIndexProvider[RT]: ...

    def add(self, obj: RT, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Register an object and add all supplied index memberships.
        """
        obj_id = self.storage.register(obj)
        self.idx_provider.add(obj_id, ctx)

    @overload
    def remove(self, obj: RT, ctx: RegisterUpdateContext) -> None: ...
    @overload
    def remove(self, id: int, ctx: RegisterUpdateContext) -> None: ...
    def remove(self, first: RT | int, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Remove an object's index memberships and free its runtime id.
        """
        if isinstance(first, int):
            obj = self.storage.get(first)
        else:
            obj = self.storage.get(first.runtime_id)

        # Indexes must be updated before the runtime id is released.
        self.idx_provider.remove(obj.runtime_id, ctx)
        self.storage.unregister(obj)

    @overload
    def update(self, obj: RT, ctx: RegisterUpdateContext) -> None: ...
    @overload
    def update(self, id: int, ctx: RegisterUpdateContext) -> None: ...
    def update(self, first: RT | int, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Apply incremental index changes for an already registered object.
        """
        if isinstance(first, int):
            obj = self.storage.get(first)
        else:
            obj = self.storage.get(first.runtime_id)

        self.idx_provider.update(obj.runtime_id, ctx)

    def update_all(self, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Apply the same index update context to every stored object.
        """
        for obj in self.storage.get_all():
            self.update(obj, ctx)

    def clear(self) -> None:
        """!
        @brief Remove every stored object and clear all derived indexes.
        """
        self.storage.clear()
        self.idx_provider.clear()

    def query(self, query: Query) -> Iterator[RT]:
        """!
        @brief Evaluate an indexed query and yield the matching runtime objects.

        @param query Query evaluated against this register.
        @return Iterator over matching objects.
        """
        from .query import QueryContext

        bs = query.eval(QueryContext(self.storage, self.idx_provider))
        for id in bs:
            yield self.storage.get(id)


class RegisterSynchroniser[RT: RuntimeObject](ABC):
    """!
    @brief Interface for producing pending incremental register updates.
    """

    @abstractmethod
    def synchronise(self) -> RegisterUpdateContext: ...


class ObjectStorage[RT: RuntimeObject]:
    """!
    @brief Store runtime objects independently of their derived indexes.

    Runtime identifiers correspond directly to positions in `_id_to_obj`.
    Freed identifiers are recycled through `_free_ids`.
    """

    def __init__(self):
        self._id_to_obj: list[RT | None] = []
        self._free_ids: list[int] = []

    def register(self, obj: RT) -> int:
        """!
        @brief Assign a runtime id and store an object.

        @return Assigned runtime identifier.
        @throws KeyError If the object is already registered.
        """
        if obj.runtime_id is not None:
            raise KeyError(f"  RuntimeObject with key '{obj.key}' is already registered.")

        # Reuse freed slots before extending the backing array.
        if self._free_ids:
            obj_id = self._free_ids.pop()
            self._id_to_obj[obj_id] = obj
        else:
            obj_id = len(self._id_to_obj)
            self._id_to_obj.append(obj)

        obj.runtime_id = obj_id
        return obj_id

    def unregister(self, obj: RT) -> None:
        """!
        @brief Remove an object and make its runtime id reusable.
        """
        r_id = obj.runtime_id
        if r_id is None or r_id < 0 or r_id >= len(self._id_to_obj):
            raise KeyError(f"  RuntimeObject with key '{obj.key}' was not registered in registry.")
        if self._id_to_obj[r_id] is not obj:
            raise KeyError("Object belongs to a different storage.")

        self._id_to_obj[r_id] = None
        obj.runtime_id = None
        self._free_ids.append(r_id)

    def get_all(self) -> Iterator[RT]:
        """!
        @brief Yield all currently registered objects.
        """
        for obj in self._id_to_obj:
            if obj:
                yield obj

    def get(self, id: int) -> RT:
        """!
        @brief Return the object assigned to a runtime id.

        @throws IndexError If the id is outside storage capacity.
        @throws ValueError If the id refers to a freed slot.
        """
        if id < 0 or id >= len(self._id_to_obj):
            raise IndexError(f"  Id '{id}' is out of range of _id_to_obj")

        obj = self._id_to_obj[id]
        if obj is None:
            raise ValueError(f"  RuntimeObject with id {id} was freed.")

        return obj

    def clear(self) -> None:
        """!
        @brief Detach runtime ids from all objects and reset the storage.
        """
        for obj in self.get_all():
            obj.runtime_id = None
        self._free_ids.clear()
        self._id_to_obj.clear()

    def __len__(self) -> int:
        return len(self._id_to_obj) - len(self._free_ids)


class BitSet:
    """!
    @brief Represent a set of indexed object identifiers using one integer bitmask.
    """

    @overload
    def __init__(self): ...
    @overload
    def __init__(self, other: BitSet): ...
    @overload
    def __init__(self, bits: int = 0): ...
    def __init__(self, first: BitSet | int = 0):
        if isinstance(first, BitSet):
            self._max_elements: int = first._max_elements
            self._all_bits_mask: int = first._all_bits_mask
            self._bits: int = first._bits

        else:
            if first < 0:
                raise ValueError("  Bitmask (bits) cannot be negative number.")

            self._max_elements = 128
            self._all_bits_mask = (1 << self._max_elements) - 1
            if first > 0:
                self._ensure_capacity(first.bit_length() - 1)

            self._bits = first

    def _ensure_capacity(self, idx: int) -> None:
        """!
        @brief Expand the logical inversion mask to contain an identifier.
        """
        if idx >= self._max_elements:
            bit_need = idx + 1
            factor = (bit_need + self._max_elements - 1) // self._max_elements
            new_capacity = self._max_elements * (1 << (factor - 1).bit_length())

            self._update_max_el(new_capacity)

    def _update_max_el(self, max_el: int) -> None:
        self._max_elements = max_el
        self._all_bits_mask = (1 << self._max_elements) - 1

    def add(self, idx: int) -> None:
        if idx < 0:
            raise ValueError("  Bitmask (bits) cannot be negative number.")

        self._ensure_capacity(idx)
        self._bits |= 1 << idx

    def remove(self, idx: int) -> None:
        if idx < 0:
            raise ValueError("  Bitmask (bits) cannot be negative number.")
        self._bits &= ~(1 << idx)

    def contains(self, idx: int) -> bool:
        return (self._bits & (1 << idx)) != 0

    def count(self) -> int:
        return self._bits.bit_count()

    def clear(self) -> None:
        self._bits = 0

    def __bool__(self) -> bool:
        return self._bits != 0

    def __eq__(self, other):
        if not isinstance(other, BitSet):
            return False
        return self._bits == other._bits

    def __and__(self, other: BitSet) -> BitSet:
        return BitSet(self._bits & other._bits)

    def __or__(self, other: BitSet) -> BitSet:
        return BitSet(self._bits | other._bits)

    def __sub__(self, other: BitSet) -> BitSet:
        return BitSet(self._bits & ~other._bits)

    def __ior__(self, other: BitSet) -> BitSet:
        max_elements = max(self._max_elements, other._max_elements)
        if max_elements != self._max_elements:
            self._update_max_el(max_elements)
        self._bits |= other._bits
        return self

    def __iand__(self, other: BitSet) -> BitSet:
        self._bits &= other._bits
        return self

    def __isub__(self, other: BitSet) -> BitSet:
        self._bits &= ~other._bits
        return self

    def __invert__(self) -> BitSet:
        # Inversion is intentionally bounded by the logical capacity mask.
        return BitSet(self._bits ^ self._all_bits_mask)

    def __iter__(self):
        """!
        @brief Yield set bit positions in ascending order.
        """
        temp = self._bits
        while temp:
            lsb = temp & -temp
            yield lsb.bit_length() - 1
            temp ^= lsb

    def update(self, bitsets: Iterable[BitSet]) -> None:
        for bs in bitsets:
            self._bits |= bs._bits

        if self._bits > 0:
            self._ensure_capacity(self._bits.bit_length() - 1)

    def intersection_update(self, bitsets: Iterable[BitSet]) -> None:
        for bs in bitsets:
            if not self._bits:
                return
            self._bits &= bs._bits

    def difference_update(self, bitsets: Iterable[BitSet]) -> None:
        for bs in bitsets:
            if not self._bits:
                return
            self._bits &= ~bs._bits

    def union(self, bitsets: Iterable[BitSet]) -> BitSet:
        res: BitSet = BitSet(self)
        for bs in bitsets:
            res._bits |= bs._bits

        if res._bits > 0:
            res._ensure_capacity(res._bits.bit_length() - 1)

        return res

    def intersection(self, bitsets: Iterable[BitSet]) -> BitSet:
        res = BitSet(self)
        for bs in bitsets:
            if res._bits == 0:
                return res
            res._bits &= bs._bits
        return res

    def difference(self, excludes: Iterable[BitSet]) -> BitSet:
        res = BitSet(self)
        for bs in excludes:
            if res._bits == 0:
                return res
            res._bits &= ~bs._bits
        return res


@dataclass(frozen=True)
class IndexKey[T: Hashable]:
    """!
    @brief Stable typed identifier for one index maintained by a register.
    """

    name: str


class OrderedMap[KT: ComparableHashable, VT](Mapping[KT, VT], ABC):
    """!
    @brief Mapping that additionally supports ordered range lookup.
    """

    @abstractmethod
    def bisect_left(self, key: KT | None) -> int: ...

    @abstractmethod
    def bisect_right(self, key: KT | None) -> int: ...

    @abstractmethod
    def scope_map(self, min: KT | None, max: KT | None) -> Mapping[KT, VT]: ...


class IndexProvider(ABC):
    """!
    @brief Provide index groups used by equality, membership and range queries.
    """

    @abstractmethod
    def get_index_group[KT: Hashable](self, key: IndexKey[KT]) -> Mapping[KT, BitSet]: ...

    @abstractmethod
    def get_ordered_group[KT: ComparableHashable](
        self, key: IndexKey[KT]
    ) -> OrderedMap[KT, BitSet]: ...


class ObjectIndexProvider(IndexProvider, ABC):
    """!
    @brief Maintain object-id memberships for a fixed set of index keys.
    """

    @property
    @abstractmethod
    def data(self) -> Mapping[IndexKey[Hashable], dict[Hashable, BitSet]]: ...

    def _add_on_idx(
        self, idx: int, upd: IndexUpdate[Hashable], group: dict[Hashable, BitSet]
    ) -> None:

        for value in upd.add:
            bs = group.get(value)
            if bs is None:
                bs = BitSet()
                group[value] = bs
            bs.add(idx)

    def _remove_on_idx(
        self, idx: int, upd: IndexUpdate[Hashable], group: dict[Hashable, BitSet]
    ) -> None:

        for value in upd.remove:
            bs = group.get(value)
            if bs is None:
                raise RuntimeError(f"  Missing BitSet for value '{value!r}'.")

            if not bs.contains(idx):
                raise RuntimeError(f"  Missing RuntimeObject with id '{idx}' in BitSet.")
            bs.remove(idx)

            # Empty value buckets carry no useful query information.
            if not bs:
                del group[value]

    def _update_on_idx(
        self, idx: int, upd: IndexUpdate[Hashable], group: dict[Hashable, BitSet]
    ) -> None:

        self._remove_on_idx(idx, upd, group)
        self._add_on_idx(idx, upd, group)

    def _process_register_update(
        self,
        idx: int,
        ctx: RegisterUpdateContext,
        op: Callable[[int, IndexUpdate[Hashable], dict[Hashable, BitSet]], None],
    ) -> None:

        for key, upd in ctx.data.items():
            group = self.data.get(key)
            if group is None:
                raise KeyError(
                    "  Trying to update IndexProvider with key that this Provider does not Support."
                )

            op(idx, upd, group)

    def add(self, idx: int, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Add an object's memberships described by the update context.
        """
        self._process_register_update(idx, ctx, self._add_on_idx)

    def remove(self, idx: int, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Remove an object's memberships described by the update context.
        """
        self._process_register_update(idx, ctx, self._remove_on_idx)

    def update(self, idx: int, ctx: RegisterUpdateContext) -> None:
        """!
        @brief Apply removals followed by additions for an existing object.
        """
        self._process_register_update(idx, ctx, self._update_on_idx)

    def clear(self) -> None:
        """!
        @brief Remove all value buckets from every configured index.
        """
        for v in self.data.values():
            v.clear()


class SortedOrderedMap[KT: ComparableHashable, VT](OrderedMap[KT, VT]):
    """!
    @brief Small dependency-free ordered mapping used by range queries.

    The registry normally contains a modest number of distinct index values;
    sorting keys on access keeps updates simple and avoids an external package.
    """

    def __init__(self, data: Mapping[KT, VT]) -> None:
        self._data = data

    def __getitem__(self, key: KT) -> VT:
        return self._data[key]

    def __iter__(self) -> Iterator[KT]:
        return iter(sorted(self._data))

    def __len__(self) -> int:
        return len(self._data)

    def bisect_left(self, key: KT | None) -> int:
        import bisect

        keys = sorted(self._data)
        return 0 if key is None else bisect.bisect_left(keys, key)

    def bisect_right(self, key: KT | None) -> int:
        import bisect

        keys = sorted(self._data)
        return len(keys) if key is None else bisect.bisect_right(keys, key)

    def scope_map(self, min: KT | None, max: KT | None) -> Mapping[KT, VT]:
        """!
        @brief Return values whose keys lie within the inclusive ordered range.
        """
        keys = sorted(self._data)
        return {
            key: self._data[key] for key in keys[self.bisect_left(min) : self.bisect_right(max)]
        }


class SimpleObjectIndexProvider(ObjectIndexProvider):
    """!
    @brief In-memory index provider for equality, membership, and range queries.
    """

    def __init__(
        self,
        index_keys: Iterable[IndexKey[Hashable]],
        ordered_keys: Iterable[IndexKey[ComparableHashable]] = (),
    ) -> None:
        self._data: dict[IndexKey[Hashable], dict[Hashable, BitSet]] = {
            key: {} for key in index_keys
        }
        self._ordered_keys = set(ordered_keys)

    @property
    def data(self) -> Mapping[IndexKey[Hashable], dict[Hashable, BitSet]]:
        return self._data

    def get_index_group[KT: Hashable](self, key: IndexKey[KT]) -> Mapping[KT, BitSet]:
        """!
        @brief Return the value-to-object-id buckets for one index.
        """
        try:
            return self._data[key]  # type: ignore[return-value]
        except KeyError as exc:
            raise KeyError(f"Unsupported index '{key.name}'.") from exc

    def get_ordered_group[KT: ComparableHashable](
        self, key: IndexKey[KT]
    ) -> OrderedMap[KT, BitSet]:
        """!
        @brief Return an ordered view of an index configured for range queries.
        """
        if key not in self._ordered_keys:
            raise KeyError(f"Index '{key.name}' is not configured for range queries.")
        return SortedOrderedMap(self.get_index_group(key))


class InMemoryObjectRegister(ObjectRegister[RT]):
    """!
    @brief Ready-to-use object register backed by standard in-memory storage and indexes.
    """

    def __init__(
        self,
        index_keys: Iterable[IndexKey[Hashable]],
        ordered_keys: Iterable[IndexKey[ComparableHashable]] = (),
    ) -> None:
        self._storage = ObjectStorage[RT]()
        self._idx_provider = SimpleObjectIndexProvider(index_keys, ordered_keys)

    @property
    def storage(self) -> ObjectStorage[RT]:
        return self._storage

    @property
    def idx_provider(self) -> ObjectIndexProvider[RT]:
        return self._idx_provider