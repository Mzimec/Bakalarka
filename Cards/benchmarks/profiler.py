from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from functools import wraps
from time import perf_counter_ns
from typing import Any, Callable
import sys


@dataclass
class ComponentStats:
    calls: int = 0
    total_ns: int = 0
    exclusive_ns: int = 0

    @property
    def average_ns(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_ns / self.calls

    @property
    def average_exclusive_ns(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.exclusive_ns / self.calls


class _Frame:
    __slots__ = ("start_ns", "child_ns")

    def __init__(self):
        self.start_ns = perf_counter_ns()
        self.child_ns = 0


class ComponentProfiler:
    def __init__(self):
        self.stats: dict[str, ComponentStats] = defaultdict(ComponentStats)
        self._stack: list[_Frame] = []
        self._patches: list[tuple[object, str, object]] = []
        self.work: dict[str, int] = defaultdict(int)

    def count(self, name: str, amount: int = 1):
        self.work[name] += amount


    def work_to_dict(self) -> dict[str, int]:
        return dict(self.work)


    def patch_replacement(
        self,
        owner: object,
        name: str,
        replacement: object,
    ):
        original = getattr(owner, name)

        self._patches.append((owner, name, original))
        setattr(owner, name, replacement)


    def profile_iterator(
        self,
        iterator,
        component_name: str,
    ):
        """Measure only work done while advancing a lazy iterator.

        Time while the consumer holds a yielded option is intentionally excluded.
        """
        iterator = iter(iterator)

        self.count(f"{component_name}.streams")

        try:
            while True:
                frame = _Frame()
                self._stack.append(frame)

                exhausted = False

                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                    item = None
                finally:
                    elapsed = perf_counter_ns() - frame.start_ns
                    self._stack.pop()

                    stats = self.stats[component_name]
                    stats.calls += 1
                    stats.total_ns += elapsed
                    stats.exclusive_ns += elapsed - frame.child_ns

                    if self._stack:
                        self._stack[-1].child_ns += elapsed

                if exhausted:
                    self.count(f"{component_name}.exhaustions")
                    return

                self.count(f"{component_name}.yields")
                yield item

        finally:
            close = getattr(iterator, "close", None)

            if close is not None:
                close()

    def _wrap(self, fn: Callable, name: str) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            frame = _Frame()
            self._stack.append(frame)

            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = perf_counter_ns() - frame.start_ns
                self._stack.pop()

                stats = self.stats[name]
                stats.calls += 1
                stats.total_ns += elapsed
                stats.exclusive_ns += elapsed - frame.child_ns

                if self._stack:
                    self._stack[-1].child_ns += elapsed

        return wrapper

    def patch_method(
        self,
        cls: type,
        method_name: str,
        component_name: str | None = None,
    ):
        original = getattr(cls, method_name)

        name = component_name or f"{cls.__name__}.{method_name}"
        wrapped = self._wrap(original, name)

        self._patches.append((cls, method_name, original))
        setattr(cls, method_name, wrapped)

    def patch_dispatched_method(self, cls, method_name, component_name):
        """Time a shared dispatch point under the concrete operation's name."""
        original = getattr(cls, method_name)
        wrappers = {}

        @wraps(original)
        def wrapped(owner, state, operation):
            name = f"{component_name}.{type(operation).__name__}"
            if name not in wrappers:
                wrappers[name] = self._wrap(original, name)
            return wrappers[name](owner, state, operation)

        self.patch_replacement(cls, method_name, wrapped)

    def patch_when(self, owner, method_name, component_name, predicate):
        """Do not time the numerous recursive/clean fast returns."""
        original = getattr(owner, method_name)
        timed = self._wrap(original, component_name)

        @wraps(original)
        def wrapped(*args, **kwargs):
            return (timed if predicate(*args, **kwargs) else original)(*args, **kwargs)

        self.patch_replacement(owner, method_name, wrapped)

    def patch_function(
        self,
        module: object,
        function_name: str,
        component_name: str | None = None,
    ):
        original = getattr(module, function_name)

        name = component_name or function_name
        wrapped = self._wrap(original, name)

        self._patches.append((module, function_name, original))
        setattr(module, function_name, wrapped)

        # Replace already imported aliases of the function.
        for loaded_module in list(sys.modules.values()):
            if loaded_module is None:
                continue

            module_name = getattr(loaded_module, "__name__", "")

            if not module_name.startswith(("game.", "helper.")):
                continue

            for attr_name, value in list(vars(loaded_module).items()):
                if value is original:
                    self._patches.append(
                        (loaded_module, attr_name, original)
                    )
                    setattr(loaded_module, attr_name, wrapped)

    def restore(self):
        for owner, name, original in reversed(self._patches):
            setattr(owner, name, original)

        self._patches.clear()

    def clear(self):
        self.stats.clear()

    def to_dict(self) -> dict[str, dict[str, Any]]:
        result = {}

        for name, stats in self.stats.items():
            result[name] = {
                **asdict(stats),
                "total_ms": stats.total_ns / 1_000_000,
                "exclusive_ms": stats.exclusive_ns / 1_000_000,
                "average_us": stats.average_ns / 1_000,
                "average_exclusive_us":
                    stats.average_exclusive_ns / 1_000,
            }

        return result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.restore()
