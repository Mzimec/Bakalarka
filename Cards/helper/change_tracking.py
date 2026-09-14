"""Mutable mappings which notify their owner about edits, including update/pop."""

from collections import UserDict
from collections.abc import MutableSet


class TrackedDict(UserDict):
    def __init__(self, values=(), on_change=lambda: None):
        self._on_change = on_change
        self.data = dict(values)

    def __setitem__(self, key, value):
        self.data[key] = value
        self._on_change()

    def __delitem__(self, key):
        del self.data[key]
        self._on_change()

    def __ior__(self, other):
        self.update(other)
        return self


class TrackedSet(MutableSet):
    def __init__(self, values=(), on_change=lambda: None):
        self._values = set(values)
        self._on_change = on_change

    def __contains__(self, value):
        return value in self._values

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def add(self, value):
        if value not in self._values:
            self._values.add(value)
            self._on_change()

    def discard(self, value):
        if value in self._values:
            self._values.discard(value)
            self._on_change()

    def update(self, values):
        for value in values:
            self.add(value)
