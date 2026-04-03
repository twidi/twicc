#!/usr/bin/env -S uv run
"""Dev wrapper: adds src/ to sys.path then delegates to twicc.cli.main()."""

import sys
from pathlib import Path

# Add src/ directory to Python path so twicc is importable without installing
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# The __main__ guard is required because the background compute task uses
# multiprocessing with "spawn" start method. Spawn re-imports the main module
# in the child process — without this guard, the child would call main() and
# start an entire second server instance instead of running the worker function.
if __name__ == "__main__":
    from twicc.cli import main  # noqa: E402

    main()
