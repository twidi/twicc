"""
Centralized path resolution for TwiCC data directories.

All data (database, logs, config) lives in a single "data directory":
- Default: ~/.twicc/
- Override: TWICC_DATA_DIR environment variable

Structure:
    <data_dir>/
    ├── .env          # Configuration (ports, password hash, etc.)
    ├── db/
    │   └── data.sqlite (+shm, +wal)
    └── logs/
        ├── backend.log              # Backend application logs
        ├── frontend.log             # Frontend (Vite) process output
        └── sdk/
            └── {session_id}.jsonl   # Raw SDK message logs

In development with worktrees, devctl.py sets TWICC_DATA_DIR to the
worktree root so each worktree gets its own DB, logs, and .env.
"""

import os
from pathlib import Path

# Environment variable name to override the data directory
TWICC_DATA_DIR_ENV = "TWICC_DATA_DIR"

# Default data directory (same pattern as ~/.claude/)
DEFAULT_DATA_DIR = Path.home() / ".twicc"


def get_data_dir() -> Path:
    """Return the resolved data directory.

    Priority:
    1. TWICC_DATA_DIR environment variable (if set and non-empty)
    2. ~/.twicc/ (default)
    """
    env_value = os.environ.get(TWICC_DATA_DIR_ENV, "").strip()
    if env_value:
        return Path(env_value).resolve()
    return DEFAULT_DATA_DIR


def get_db_dir() -> Path:
    """Return the database directory (<data_dir>/db/)."""
    return get_data_dir() / "db"


def get_db_path() -> Path:
    """Return the database file path (<data_dir>/db/data.sqlite)."""
    return get_db_dir() / "data.sqlite"


def get_logs_dir() -> Path:
    """Return the logs directory (<data_dir>/logs/)."""
    return get_data_dir() / "logs"


def get_sdk_logs_dir() -> Path:
    """Return the SDK logs directory (<data_dir>/logs/sdk/)."""
    return get_logs_dir() / "sdk"


def get_backend_log_path() -> Path:
    """Return the backend log file path (<data_dir>/logs/backend.log)."""
    return get_logs_dir() / "backend.log"


def get_frontend_log_path() -> Path:
    """Return the frontend log file path (<data_dir>/logs/frontend.log)."""
    return get_logs_dir() / "frontend.log"


def get_env_path() -> Path:
    """Return the .env file path (<data_dir>/.env)."""
    return get_data_dir() / ".env"


def ensure_data_dirs() -> None:
    """Create the data directory structure if it doesn't exist."""
    get_db_dir().mkdir(parents=True, exist_ok=True)
    get_sdk_logs_dir().mkdir(parents=True, exist_ok=True)
