import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env file (idempotent: no-op if already loaded by run.py)
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "dev-insecure-key-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django_extensions",
    "channels",
    "twicc.core.apps.CoreConfig",
]

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
        "NAME": BASE_DIR / "data.sqlite",
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
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "dist"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Source des donnees Claude
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Logging configuration
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "[{asctime} - {levelname:>6} - {name}] {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "loggers": {
        "twicc": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# Display levels computation
CURRENT_COMPUTE_VERSION = 43  # Bump when display rules change to trigger recomputation

# Process auto-stop timeouts (in seconds)
# Processes are automatically stopped if they remain in a state for too long
PROCESS_TIMEOUT_STARTING = 60  # 1 minute - process stuck during startup
PROCESS_TIMEOUT_USER_TURN = 15 * 60  # 15 minutes - idle, waiting for user input
PROCESS_TIMEOUT_ASSISTANT_TURN = 2 * 60 * 60  # 2 hours - no activity from Claude
PROCESS_TIMEOUT_ASSISTANT_TURN_ABSOLUTE = 6 * 60 * 60  # 6 hours - max total duration for a turn
