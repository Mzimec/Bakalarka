from __future__ import annotations
from typing import Protocol, Self
from immutabledict import immutabledict 

__all__ = [
    "ImmutableSet",
    "MutableSet",
    "ImmutableDict",
    "MutableDict",
    "ImmutableSequence",
    "MutableSequence"
]


class ToMutableConvertible[MCT: ToImmutableConvertible[Self]](Protocol):
    
    def to_mutable(self) -> MCT: 
        ...


class ToImmutableConvertible[ICT: ToMutableConvertible[Self]](Protocol):

    def to_immutable(self) -> ICT: 
        ...


#--------------------------------------------------------------
# Set objects
#--------------------------------------------------------------

class ImmutableSet[T](frozenset[T]):

    def to_mutable(self) -> MutableSet[T]:
        return MutableSet(self)


class MutableSet[T](set[T]):
    
    def to_immutable(self) -> ImmutableSet[T]:
        return ImmutableSet(self)


#--------------------------------------------------------------
# Dict objects
#--------------------------------------------------------------

class ImmutableDict[KT, VT](immutabledict[KT, VT]):

    def to_mutable(self) -> MutableDict[KT, VT]:
        return MutableDict(self)
    

class MutableDict[KT, VT](dict[KT, VT]):
    
    def to_immutable(self) -> ImmutableDict[KT, VT]:
        return ImmutableDict(self)


#--------------------------------------------------------------
# Sequence objects
#--------------------------------------------------------------

class ImmutableSequence[T](tuple[T, ...]):

    def to_mutable(self) -> MutableSequence[T]:
        return MutableSequence(self)
    

class MutableSequence[T](list[T]):

    def to_immutable(self) -> ImmutableSequence[T]:
        return ImmutableSequence(self)