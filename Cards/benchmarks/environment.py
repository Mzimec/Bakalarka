"""Benchmark provenance, collected outside the measured game interval."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig


SOURCE_DIRECTORIES = ("game", "helper", "benchmarks")


def source_fingerprint(cards_root: Path) -> dict:
    """Include local and untracked Python changes, but exclude results and bytecode."""
    digest = hashlib.sha256()
    files = sorted(
        path
        for directory in SOURCE_DIRECTORIES
        for path in (cards_root / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for path in files:
        name = path.relative_to(cards_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        for value in (name, content):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return {"sha256": digest.hexdigest(), "files": len(files),
            "scope": [f"{name}/**/*.py" for name in SOURCE_DIRECTORIES]}


def git_metadata(cards_root: Path) -> dict:
    repository = cards_root.parent

    def git(*args):
        # Trust only this explicitly selected checkout, without changing Git config.
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repository.as_posix()}",
             "-C", str(repository), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=True,
        )
        return result.stdout.rstrip("\r\n")

    try:
        commit = git("rev-parse", "HEAD")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        paths = [f":(glob){cards_root.name}/{name}/**/*.py"
                 for name in SOURCE_DIRECTORIES]
        status = git("status", "--porcelain", "--untracked-files=all", "--", *paths)
        return {"commit": commit, "branch": branch,
                "source_dirty": bool(status), "source_status": status.splitlines()}
    except (OSError, subprocess.SubprocessError) as error:
        # Running a downloaded source tree without Git must still work.
        return {"available": False, "error": str(error)}


def collect_environment(cards_root: Path) -> dict:
    monitoring = getattr(sys, "monitoring", None)
    monitoring_tools = []
    if monitoring is not None:
        for tool_id in range(6):
            name = monitoring.get_tool(tool_id)
            if name is not None:
                monitoring_tools.append({"id": tool_id, "name": name,
                                         "events": monitoring.get_events(tool_id)})
    return {
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
            "build": platform.python_build(),
            "compiler": platform.python_compiler(),
            "flags": str(sys.flags),
            "debug_build": bool(sysconfig.get_config_var("Py_DEBUG")),
            "gil_disabled_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        },
        "instrumentation": {
            "component_profiler_enabled": True,
            "trace_hook_active": sys.gettrace() is not None,
            "profile_hook_active": sys.getprofile() is not None,
            "debugger_modules_loaded": [name for name in ("debugpy", "pydevd", "pdb")
                                        if name in sys.modules],
            "monitoring_tools": monitoring_tools,
        },
        "system": {"platform": platform.platform(), "machine": platform.machine(),
                   "processor": platform.processor(), "logical_cpus": os.cpu_count()},
        "code": {"sources": source_fingerprint(cards_root),
                 "git": git_metadata(cards_root)},
    }
