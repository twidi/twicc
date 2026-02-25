#!/usr/bin/env -S uv run
"""Dev wrapper: adds src/ to sys.path then delegates to twicc.cli.main()."""

import sys
from pathlib import Path

# Add src/ directory to Python path so twicc is importable without installing
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from twicc.cli import main  # noqa: E402

main()
