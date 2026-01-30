"""Test settings for pytest-django."""

from twicc.settings import *  # noqa: F401, F403

# Use in-memory SQLite for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {},
    "loggers": {},
}

# Test compute version
CURRENT_COMPUTE_VERSION = 99
