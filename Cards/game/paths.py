"""Checkout paths shared by data loaders and command-line entry points."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
ARTIFACTS_DIR = PROJECT_DIR / "artifacts"
RUNS_DIR = ARTIFACTS_DIR / "runs"
