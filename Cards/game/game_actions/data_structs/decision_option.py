"""Common nominal type for all values selectable by a decision maker.

Kept below generation and controllers so actions and payment plans can inherit
it without introducing dependency cycles.
"""
from abc import ABC


class DecisionOption(ABC):
    """A concrete decision value; executing it is the engine's responsibility."""
