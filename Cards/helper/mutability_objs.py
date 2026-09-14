"""Collection wrappers with explicit mutable and immutable conversion."""

from __future__ import annotations
from typing import Protocol, Self
from immutabledict import immutabledict

__all__ = [
    "ImmutableSet",
    "MutableSet",
    "ImmutableDict",
    "MutableDict",
    "ImmutableSequence",
    "MutableSequence",
]


class ToMutableConvertible[MCT: ToImmutableConvertible[Self]](Protocol):
    """!
    @brief Protocol for values that can produce a mutable representation.
    """

    def to_mutable(self) -> MCT: ...


class ToImmutableConvertible[ICT: ToMutableConvertible[Self]](Protocol):
    """!
    @brief Protocol for values that can produce an immutable representation.
    """

    def to_immutable(self) -> ICT: ...


# --------------------------------------------------------------
# Set objects
# --------------------------------------------------------------


class ImmutableSet[T](frozenset[T]):

    def to_mutable(self) -> MutableSet[T]:
        """!
        @brief Return a mutable representation of this value.
        """
        return MutableSet(self)


class MutableSet[T](set[T]):

    def to_immutable(self) -> ImmutableSet[T]:
        """!
        @brief Return an immutable representation of this value.
        """
        return ImmutableSet(self)


# --------------------------------------------------------------
# Dict objects
# --------------------------------------------------------------


class ImmutableDict[KT, VT](immutabledict[KT, VT]):

    def to_mutable(self) -> MutableDict[KT, VT]:
        """!
        @brief Return a mutable representation of this value.
        """
        return MutableDict(self)


class MutableDict[KT, VT](dict[KT, VT]):

    def to_immutable(self) -> ImmutableDict[KT, VT]:
        """!
        @brief Return an immutable representation of this value.
        """
        return ImmutableDict(self)


# --------------------------------------------------------------
# Sequence objects
# --------------------------------------------------------------


class ImmutableSequence[T](tuple[T, ...]):

    def to_mutable(self) -> MutableSequence[T]:
        """!
        @brief Return a mutable representation of this value.
        """
        return MutableSequence(self)


class MutableSequence[T](list[T]):

    def to_immutable(self) -> ImmutableSequence[T]:
        """!
        @brief Return an immutable representation of this value.
        """
        return ImmutableSequence(self)
