"""Compatibility name for the action-resolution service.

`ResolutionEngine` is the canonical implementation.  Keeping this small
adapter prevents callers and tests written before the package reorganisation
from importing a deleted module.
"""

from __future__ import annotations
from .resolution_engine import ResolutionEngine


class ActionExecutor(ResolutionEngine):
    """!
    @brief Compatibility alias for the canonical resolution engine.
    """
    pass
