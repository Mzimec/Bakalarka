"""Isolate test temporary files from shared Windows pytest directories."""

import tempfile
from pathlib import Path


def pytest_configure(config):
    """!
    @brief Allocate a private temporary root unless the caller supplied one.

    IDE and sandbox runs can share a username while having different Windows
    access tokens. Avoid pytest-of-<username>, whose ACL may belong to another
    execution context. TemporaryDirectory creates a unique root for this run
    and removes only that owned root when pytest finishes.
    """
    if config.option.basetemp is not None:
        return

    temporary = tempfile.TemporaryDirectory(prefix="mtg-pytest-")
    config.add_cleanup(temporary.cleanup)
    config.option.basetemp = str(Path(temporary.name) / "tests")
