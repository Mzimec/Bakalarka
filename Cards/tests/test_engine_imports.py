"""Every engine module must import, including namespace-only subpackages."""
import importlib
from pathlib import Path


def test_all_engine_modules_import():
    root = Path(__file__).resolve().parents[1]
    for package in ("game", "helper"):
        for path in sorted((root / package).rglob("*.py")):
            relative = path.relative_to(root).with_suffix("")
            parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
            importlib.import_module(".".join(parts))
