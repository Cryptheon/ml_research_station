"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the src layout is importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
