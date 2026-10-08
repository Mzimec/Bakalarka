"""Benchmark-only switches, scoped so repeated runs restore normal behavior."""
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def source_cache_mode(enabled):
    if enabled:
        yield
        return
    from game.mana.source_cache import StaticManaSourceCache
    with (patch.object(StaticManaSourceCache, "lookup", lambda *args: None),
          patch.object(StaticManaSourceCache, "remember", lambda *args: None),
          patch.object(StaticManaSourceCache, "supported", staticmethod(lambda state: False))):
        yield
