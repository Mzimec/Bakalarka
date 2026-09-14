"""Compatibility name for the action-resolution service.

`ResolutionEngine` is the canonical implementation.  Keeping this small
adapter prevents callers and tests written before the package reorganisation
from importing a deleted module.
"""

from .resolution_engine import ResolutionEngine


class ActionExecutor(ResolutionEngine):
    pass
