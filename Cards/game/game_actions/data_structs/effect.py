"""Declarative effects that generate executable operations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, override
from collections.abc import Iterator, Iterable

if TYPE_CHECKING:
    from ...game_state import State
    from ...operations import Operation
    from .game_action import ResolutionContext

from helper.runtime_object import RuntimeObject
from ...enums import *

__all__ = ["Effect"]


class Effect(RuntimeObject):
    """!
    @brief Base class for ability effects that generate executable operations.

    An `Effect` is the declarative description of "what should happen"
    (e.g. deal damage, draw a card, destroy a permanent) as authored on
    a card. It doesn't perform any mutation itself — instead, given a
    game state and a `ResolutionContext` carrying its resolved targets,
    it produces a stream of `Operation` objects that the engine actually
    executes. This keeps effect definitions side-effect free and lets
    the same effect be used both for planning (e.g. cost validation,
    action generation) and for real execution.

    Effects are also used to represent costs (e.g. "sacrifice a
    creature", "pay life"), which is why `validation_error` exists
    separately from `to_operations` — some costs need to check legality
    before generating the operations that pay them.
    """

    def __init__(self, key: str) -> None:
        """!
        @brief Create an effect identified by its definition key.
        @param key Effect identifier used by ability definitions.
        """
        self._key: str = key

    @property
    @override
    def key(self):
        """!
        @brief The effect's identifier, as referenced by ability/action-node definitions.
        """
        return self._key

    def validation_error(self, state: State, context: ResolutionContext) -> str | None:
        """!
        @brief Return an error when this effect cannot currently be paid as a cost.

        Default implementation always considers the effect legal;
        subclasses representing costs with extra legality requirements
        (e.g. "sacrifice a creature you control" when you control none)
        should override this to report why the cost currently cannot be
        paid.

        @param state Current game state.
        @param context Resolution context with targets scoped to this
               effect.
        @return `None` if the effect/cost is currently legal, otherwise a
                short human-readable string explaining why it is not.
        """
        return None

    @abstractmethod
    def to_operations(self, state: State, context: ResolutionContext) -> Iterator[Operation]:
        """!
        @brief Convert this effect into operations for the selected targets.
        @param state Current game state.
        @param context Resolution context with targets scoped to this effect.
        @return Operations that implement the effect.
        """
        ...

    @abstractmethod
    def get_info(self) -> str:
        """!
        @brief Return a human-readable description of this effect.

        Intended for UI/logging purposes (e.g. rules text or a
        stack/log description of what the effect does), not for
        gameplay logic.

        @return Short, human-readable description of the effect.
        """
        pass