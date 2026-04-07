import importlib.metadata
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent  # src/twicc/


def get_version() -> str:
    """Get version from pyproject.toml (dev) or installed metadata (wheel).

    In a dev layout (src/twicc/), read pyproject.toml directly so the version
    stays in sync without reinstalling. In a wheel install, fall back to
    installed package metadata.
    """
    if PACKAGE_DIR.parent.name == "src":
        pyproject = PACKAGE_DIR.parent.parent / "pyproject.toml"
        if pyproject.is_file():
            for line in pyproject.read_text().splitlines():
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"')
    return importlib.metadata.version("twicc")
