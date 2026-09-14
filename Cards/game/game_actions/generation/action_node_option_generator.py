"""Enumerate or select branches of an action graph."""

from __future__ import annotations
from typing import TYPE_CHECKING, override
from collections.abc import Iterator
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from ..data_structs.action_node import ActionNode, ActionNodeOption


class ActionNodeOptionGenerator(ABC):
    """!
    @brief Strategy interface for turning an `ActionNode` into concrete options.

    Different implementations control how many, and which, of an
    action node's possible resolutions (`ActionNodeOption`s) get
    offered — e.g. every legal combination for full enumeration, or a
    single pre-selected structural mode. Used as a pluggable strategy
    within `ActionGenerationStrategy` for both the cost and effect
    portions of an ability.
    """

    @abstractmethod
    def generate(self, action_node: ActionNode) -> Iterator[ActionNodeOption]:
        """!
        @brief Produce the action-node options this strategy chooses to expose.
        @param action_node The action node (cost or effect graph) to
               generate options from.
        @return Iterator of `ActionNodeOption`s selected by this
                strategy.
        """
        ...


class FullActionNodeOptionGenerator(ActionNodeOptionGenerator):
    """!
    @brief Exposes every legal option the action node can produce.

    A pass-through strategy: simply delegates to the node's own
    `generate_options`, without filtering or limiting anything. Used
    when the caller wants to enumerate all legal combinations (e.g. for
    exhaustive legality checking or full AI search).
    """

    @override
    def generate(self, action_node) -> Iterator[ActionNodeOption]:
        """!
        @brief Yield choices supported by this generation strategy.
        """
        yield from action_node.generate_options()


class SelectedActionNodeOptionGenerator(ActionNodeOptionGenerator):
    """!
    @brief Select a structural mode without enumerating target combinations.

    Used when a specific structural choice (e.g. which mode of a modal
    spell, or which branch of an `OrActionNode`) has already been
    decided — for instance, when re-generating a plan for an action
    the player has already chosen, or replaying a recorded choice —
    and only that single option among the node's full set should be
    produced, rather than re-enumerating everything.
    """

    def __init__(self, index):
        """!
        @brief Stores which option (by 1-based position) should be selected.
        @param index 1-based position of the desired option within the
               sequence produced by `action_node.generate_options()`.
        """
        self.index = index

    def generate(self, action_node):
        """!
        @brief Yield choices supported by this generation strategy.

        Walks the node's options in order, counting from 1, and yields
        only the one matching `self.index`.

        @param action_node The action node to select an option from.
        @return Iterator yielding exactly the single selected
                `ActionNodeOption`.
        @throws ValueError If `self.index` does not correspond to any
                option produced by `action_node.generate_options()`
                (i.e. the requested mode doesn't exist).
        """
        for index, option in enumerate(action_node.generate_options(), 1):
            if index == self.index:
                yield option
                return
        raise ValueError("Unknown ability mode.")