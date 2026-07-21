from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Hashable, Mapping, Iterator, Callable
from typing import overload, Protocol, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .query import Query, QueryContext

from ..runtime_object import RuntimeObject

@dataclass(frozen=True)
class IndexUpdate[HT: Hashable]:

    remove: Iterable[HT]
    add: Iterable[HT]


@dataclass(frozen=True)
class RegisterUpdateContext[RT: RuntimeObject]:
    
    data: dict[IndexKey[Hashable], IndexUpdate[Hashable]]


class ComparableHashable(Hashable, Protocol):

    def __lt__(self, other) -> bool: ...

    def __eq__(self, value) -> bool: ...


class ObjectRegister[RT: RuntimeObject](ABC):

    @property
    @abstractmethod
    def storage(self) -> ObjectStorage[RT]: ...

    @property
    @abstractmethod
    def idx_provider(self) -> ObjectIndexProvider[RT]: ...

    def add(self, obj: RT, ctx: RegisterUpdateContext) -> None: 
        self.storage.register(obj)
        self.idx_provider.add(obj, ctx)

    @overload
    def remove(self, obj: RT, ctx: RegisterUpdateContext) -> None: ...
    @overload
    def remove(self, id: int, ctx: RegisterUpdateContext) -> None: ...
    def remove(self, first: RT | int, ctx: RegisterUpdateContext) -> None:
        if isinstance(first, int):
            obj = self.storage.get(first)
        else:
            obj = self.storage.get(first.runtime_id)
        
        self.storage.unregister(obj)
        self.idx_provider.remove(obj, ctx)
    
    @overload
    def update(self, obj: RT, ctx: RegisterUpdateContext) -> None: ...
    @overload
    def update(self, id: int, ctx: RegisterUpdateContext) -> None: ...
    def update(self, first: RT | int, ctx: RegisterUpdateContext) -> None:
        if isinstance(first, int):
            obj = self.storage.get(first)
        else:
            obj = self.storage.get(first.runtime_id)
        
        self.idx_provider.update(obj.runtime_id, ctx)
    
    def update_all(self) -> None:
        for obj in self.storage.get_all():
            self.update(obj)
    
    def clear(self) -> None:
        self.storage.clear()
        self.idx_provider.clear()

    def query(self, query: Query) -> Iterator[RT]:
        bs = query.eval(QueryContext(self.storage, self.idx_provider))
        for id in bs:
            yield self.storage.get(id)


class RegisterSynchroniser[RT: RuntimeObject](ABC):

    @abstractmethod
    def synchronise(self) -> RegisterUpdateContext: ...


class ObjectStorage[RT: RuntimeObject]:

    def __init__(self):
        self._id_to_obj: list[RT | None] = []
        self._free_ids: list[int] = []

    def register(self, obj: RT) -> int:
        if obj.runtime_id is not None:
            raise KeyError(f"  RuntimeObject with key '{obj.key}' is already registered.")
        
        if self._free_ids:
            obj_id = self._free_ids.pop()
            self._id_to_obj[obj_id] = obj
        else:
            obj_id = len(self._id_to_obj)
            self._id_to_obj.append(obj)
        
        obj.runtime_id = obj_id
        return obj_id

    def unregister(self, obj: RT) -> None:
        r_id = obj.runtime_id
        if r_id is None or r_id < 0 or r_id >= len(self._id_to_obj):
            raise KeyError(f"  RuntimeObject with key '{obj.key}' was not registered in registry.")
        
        self._id_to_obj[r_id] = None
        obj.runtime_id = None
        self._free_ids.append(r_id)

    def get_all(self) -> Iterator[RT]:
        for obj in self._id_to_obj:
            if obj:
                yield obj
    
    def get(self, id: int) -> RT:
        if id < 0 or id >= len(self._id_to_obj):
            raise IndexError(f"  Id '{id}' is out of range of _id_to_obj")
        
        obj = self._id_to_obj[id]
        if obj is None:
            raise ValueError(f"  RuntimeObject with id {id} was freed.")
        
        return obj
    
    def clear(self) -> None:
        self._free_ids.clear()
        self._id_to_obj.clear()
    
    def __len__(self) -> int:
        return len(self._id_to_obj) - len(self._free_ids)
    

class BitSet:

    @overload
    def __init__(self): ...
    @overload
    def __init__(self, other: BitSet): ...
    @overload
    def __init__(self, bits: int = 0): ...
    def __init__(self, first: BitSet | int = 0):
        if isinstance(first, BitSet):
            self._max_elements: int = first._max_elements
            self._all_bits_mask:int = first._all_bits_mask
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
        return BitSet(self._bits ^ self._all_bits_mask)
    
    def __iter__(self):
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


class IndexKey[T: Hashable]: pass


class OrderedMap[KT: ComparableHashable, VT](Mapping[KT, VT], ABC):

    @abstractmethod
    def bisect_left(self, key: KT | None) -> int: ...

    @abstractmethod
    def bisect_right(self, key: KT | None) -> int: ...

    @abstractmethod
    def scope_map(self, min: KT | None, max: KT | None) -> Mapping[KT, VT]: ...


class IndexProvider(ABC):

    @abstractmethod
    def get_index_group[KT: Hashable](self, key: IndexKey[KT]) -> Mapping[KT, BitSet]: ...

    @abstractmethod
    def get_ordered_group[KT: ComparableHashable](self, key: IndexKey[KT]) -> OrderedMap[KT, BitSet]: ...


class ObjectIndexProvider(IndexProvider, ABC): 

    @property
    @abstractmethod
    def data(self) -> Mapping[IndexKey[Hashable], dict[Hashable, BitSet]]: ...

    def _add_on_idx( 
        self,
        idx: int, 
        upd: IndexUpdate[Hashable], 
        group: dict[Hashable, BitSet]
    ) -> None:
        
        for value in upd.add:
            bs = group.get(value)
            if bs is None:
                bs = BitSet()
                group[value] = bs
            bs.add(idx)
    
    def _remove_on_idx(
        self,
        idx: int, 
        upd: IndexUpdate[Hashable], 
        group: dict[Hashable, BitSet]
    ) -> None:
        
        for value in upd.remove:
            bs = group.get(value)
            if bs is None:
                raise RuntimeError(f"  Missing BitSet for value '{value!r}'.")
    
            if not bs.contains(idx):
                raise RuntimeError(f"  Missing RuntimeObject with id '{idx}' in BitSet.")
            bs.remove(idx)

            if not bs:
                del group[value]
    
    def _update_on_idx(
        self,
        idx: int,
        upd: IndexUpdate[Hashable],
        group: dict[Hashable, BitSet]
    ) -> None:
        
        self._remove_on_idx(idx, upd, group)
        self._add_on_idx(idx, upd, group)

    def _process_register_update(
        self,
        idx: int,
        ctx: RegisterUpdateContext,
        op: Callable[[int, IndexUpdate[Hashable], dict[Hashable, BitSet]], None]
    ) -> None:
        
        for key, upd in ctx.data.items():
            group = self.data.get(key)
            if group is None:
                raise KeyError("  Trying to update IndexProvider with key that this Provider does not Support.")
            
            op(idx, upd, group)

    def add(self, idx: int, ctx: RegisterUpdateContext) -> None: 
        self._process_register_update(idx, ctx, self._add_on_idx)
            
    def remove(self, idx: int, ctx: RegisterUpdateContext) -> None: 
        self._process_register_update(idx, ctx, self._remove_on_idx)

    def update(self, idx: int, ctx: RegisterUpdateContext) -> None: 
        self._process_register_update(idx, ctx, self._update_on_idx)
   
    def clear(self) -> None:
        for v in self.data.values():
            v.clear()





 




    
