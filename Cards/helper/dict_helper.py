from __future__ import annotations
from abc import ABC, abstractmethod
from typing import overload, override, cast
from collections.abc import Mapping, Hashable, Iterable, Callable
from immutabledict import immutabledict
from types import MappingProxyType


class DataMapping[KT: Hashable, VT](Mapping[KT, VT], ABC):

    @property
    @abstractmethod
    def data(self) -> Mapping[KT, VT]:
        ...

    def __getitem__(self, key: KT) -> VT:
        return self.data[key]
    
    def __iter__(self):
        return iter(self.data)
    
    def __len__(self):
        return len(self.data)



class MutableCounterMapping[KT: Hashable](DataMapping[KT, int]):

    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, data: Mapping[KT, int]) -> None: ...
    @overload
    def __init__(self, data: Iterable[tuple[KT, int]]) -> None: ...
    def __init__(
        self, 
        data: Mapping[KT, int] | Iterable[tuple[KT, int]] | None = None
    ) -> None:
        
        if data is not None:
            self._data: dict[KT, int] = dict(data)
        else:
            self._data = dict()
        
        self._proxy = MappingProxyType(self._data)

    @property
    @override
    def data(self):
        return self._proxy
       
    def add_pair(self, key: KT, value: int) -> None:
        if value == 0:
            return
        
        new_val = self._data.get(key, 0) + value
        if new_val == 0:
            self._data.pop(key, None)
        else:
            self._data[key] = new_val
    
    def substract_pair(self, key: KT, value: int) -> None:
        self.add_pair(key, -value)

    @overload
    def add(self, other: Mapping[KT, int]) -> None: ...
    @overload
    def add(self, other: Iterable[tuple[KT, int]]) -> None: ...
    def add(self, other: Mapping[KT, int] | Iterable[tuple[KT, int]]) -> None:
        if isinstance(other, Mapping):
                for k, v in other.items():
                    self.add_pair(k, v)

        else:
            for k, v in other:
                self.add_pair(k, v)    

    @overload
    def substract(self, other: Mapping[KT, int]) -> None: ...
    @overload
    def substract(self, other: Iterable[tuple[KT, int]]) -> None: ...
    def substract(self, other: Mapping[KT, int] | Iterable[tuple[KT, int]]) -> None:
        if isinstance(other, Mapping):
            for k, v in other.items():
                self.substract_pair(k, v)
        
        else:
            for k, v in other:
                self.substract_pair(k, v)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DataMapping):
            return self.data == other.data
        if isinstance(other, Mapping):
            return self.data == other
        return False


class MutablePositiveCounterMapping[KT: Hashable](MutableCounterMapping[KT]):

    @override
    def add_pair(self, key, value):
        new_val = self._data.get(key, 0) + value
        if new_val <= 0:
            self._data.pop(key, None)
        else:
            self._data[key] = new_val


class MutableSetMapping[KT: Hashable, T](DataMapping[KT, set[T]]):

    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, data: Mapping[KT, Iterable[T]]) -> None: ...
    @overload
    def __init__(self, data: Iterable[tuple[KT, Iterable[T]]]) -> None: ...
    def __init__(
        self, 
        data: Mapping[KT, Iterable[T]] | Iterable[tuple[KT, Iterable[T]]] | None = None
    ) -> None:

        if isinstance(data, Mapping):
            data = cast(Mapping[KT, Iterable[T]], data)
            self._data: dict[KT, set[T]] = {k: set(v) for k, v in data.items()}
        elif isinstance(data, Iterable):
            self._data = {k: set(v) for k, v in data}
        else:
            self._data = dict()
        
        self._proxy = MappingProxyType(self._data)

    @property
    @override
    def data(self):
        return self._proxy
    
    def mutate_pair(self, key: KT, value: Iterable[T], operation: Callable[[set[T], Iterable[T]], None]) -> None:
        if key not in self._data:
            if (
                operation is set.difference_update 
                or operation is set.intersection_update
            ):
                return
            
            self._data[key] = set()
        
        operation(self._data[key], value)

        if not self._data[key]:
            self._data.pop(key, None)


    @overload
    def mutate(self, other: Mapping[KT, Iterable[T]], operation: Callable[[set[T], Iterable[T]], None]) -> None: ...
    @overload
    def mutate(self, other: Iterable[tuple[KT, Iterable[T]]], operation: Callable[[set[T], Iterable[T]], None]) -> None: ...
    def mutate(
        self,
        other: Mapping[KT, Iterable[T]] | Iterable[tuple[KT, Iterable[T]]],
        operation: Callable[[set[T], Iterable[T]], None]
    ) -> None:
        
        if isinstance(other, Mapping):
            other = cast(Mapping[KT, Iterable[T]], other)
            items = other.items()
        else:
            items = other
        
        for k, v in items:
            self.mutate_pair(k, v, operation)
