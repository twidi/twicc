import os
from pathlib import Path

from dotenv import load_dotenv

from twicc.paths import ensure_data_dirs, get_backend_log_path, get_db_path, get_env_path

PACKAGE_DIR = Path(__file__).resolve().parent  # src/twicc/

# Load .env from the data directory (~/.twicc/.env or $TWICC_DATA_DIR/.env)
# Idempotent: no-op if already loaded by run.py
load_dotenv(get_env_path())

# Ensure data directories exist (db/, logs/)
ensure_data_dirs()

SECRET_KEY = "dev-insecure-key-do-not-use-in-production"

# TWICC_DEBUG is set by devctl when launching the backend process.
# It controls Django's DEBUG mode and the twicc logger level.
DEBUG = os.environ.get("TWICC_DEBUG", "").strip().lower() in ("1", "true", "yes")

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "channels",
    "twicc.core.apps.CoreConfig",
]

# In debug mode, try to load django-extensions (dev dependency, not required at runtime)
if DEBUG:
    try:
        import django_extensions  # noqa: F401

        INSTALLED_APPS.insert(-1, "django_extensions")
    except ImportError:
        pass

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "twicc.auth.middleware.PasswordAuthMiddleware",
]

# Password protection
# Set TWICC_PASSWORD_HASH in .env to enable password protection.
# Generate a hash with: python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
# If not set or empty, the app is accessible without authentication.
TWICC_PASSWORD_HASH = os.environ.get("TWICC_PASSWORD_HASH", "")

# Session settings
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 days
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = True  # Refresh expiry on each request

ROOT_URLCONF = "twicc.urls"

ASGI_APPLICATION = "twicc.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": get_db_path(),
        "OPTIONS": {
            "timeout": 30,
            "init_command": """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=30000;
                PRAGMA mmap_size=134217728;
                PRAGMA journal_size_limit=27103364;
                PRAGMA cache_size=2000;
            """,
        },
    }
}

# Static files
# Built frontend assets live inside the package: src/twicc/static/frontend/
# This path works both in dev (after npm run build) and when installed via pip/uvx.
STATIC_URL = "/static/"
FRONTEND_DIST_DIR = PACKAGE_DIR / "static" / "frontend"
STATICFILES_DIRS = [FRONTEND_DIST_DIR]
STATIC_ROOT = PACKAGE_DIR / "staticfiles"

# Source des donnees Claude
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Logging configuration
# All logs go to file (<data_dir>/logs/backend.log)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{asctime} - {levelname:>6} - {name}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": str(get_backend_log_path()),
            "formatter": "standard",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "twicc": {
            "handlers": ["file"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Display levels computation
CURRENT_COMPUTE_VERSION = 47  # Bump when display rules change to trigger recomputation

# Process auto-stop timeouts (in seconds)
# Processes are automatically stopped if they remain in a state for too long
PROCESS_TIMEOUT_STARTING = 60  # 1 minute - process stuck during startup
PROCESS_TIMEOUT_USER_TURN = 15 * 60  # 15 minutes - idle, waiting for user input
PROCESS_TIMEOUT_ASSISTANT_TURN = 2 * 60 * 60  # 2 hours - no activity from Claude
PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE = 6 * 60 * 60  # 6 hours - max total duration for a turn
