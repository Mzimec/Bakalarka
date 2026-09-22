"""Observe decisions and consumed options without expanding a lazy search."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter_ns


_active_measurement = ContextVar("active_decision_measurement", default=None)
_generation_depth = ContextVar("decision_generation_depth", default=0)
_decision_observer = ContextVar("decision_observer", default=None)


@contextmanager
def observe_decisions(observer):
    """Install a run-local observer, restoring any outer observer on exit."""
    token = _decision_observer.set(observer)
    try:
        yield
    finally:
        _decision_observer.reset(token)


@dataclass
class DecisionMeasurement:
    request: object
    streams: list[dict] = field(default_factory=list)

    def __enter__(self):
        self._token = _active_measurement.set(self)
        self._depth_token = _generation_depth.set(0)
        self.started_ns = perf_counter_ns()
        return self

    def __exit__(self, error_type, error, traceback):
        elapsed_ns = perf_counter_ns() - self.started_ns
        _generation_depth.reset(self._depth_token)
        _active_measurement.reset(self._token)
        size = None
        if len(self.streams) == 1:
            stream = self.streams[0]
            if stream["exhausted"] and stream["request_type"] == type(self.request).__name__:
                size = stream["options_observed"]
        # Mandatory triggers may have been enumerated by the engine beforehand.
        choices = getattr(self.request, "choices", None)
        if choices is not None and not self.streams:
            size = len(choices)
        self.metrics = {
            "request_type": type(self.request).__name__,
            "elapsed_ns": elapsed_ns,
            "status": "success" if error_type is None else "error",
            "error_type": error_type.__name__ if error_type else None,
            "options_observed": sum(s["options_observed"] for s in self.streams),
            "option_space_size": size,
            "option_spaces": [dict(s) for s in self.streams],
            "precomputed_choices": len(choices) if choices is not None else None,
        }

    def publish(self, controller):
        observer = _decision_observer.get()
        if observer is not None:
            observer(controller, self.request, self.metrics)


def measure_options(request, policy, options):
    """Count top-level yielded options; nested ability/mana expansion is excluded.

    Depth applies only during next(), never while the consumer has control.
    Finishing early leaves size unknown. Reiteration creates a separate stream.
    """
    measurement = _active_measurement.get()
    if measurement is None:
        yield from options
        return
    stream = None
    if measurement is not None and _generation_depth.get() == 0:
        stream = {
            "request_type": type(request).__name__,
            "policy_type": type(policy).__name__ if policy is not None else "default",
            "options_observed": 0,
            "exhausted": False,
        }
        measurement.streams.append(stream)
    iterator = iter(options)
    try:
        while True:
            token = _generation_depth.set(_generation_depth.get() + 1)
            try:
                option = next(iterator)
            except StopIteration:
                if stream is not None:
                    stream["exhausted"] = True
                return
            finally:
                _generation_depth.reset(token)
            if stream is not None:
                stream["options_observed"] += 1
            yield option
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
