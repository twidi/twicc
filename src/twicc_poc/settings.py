from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = "poc-insecure-key-do-not-use-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "channels",
    "twicc_poc.core",
]

MIDDLEWARE = []  # Aucun middleware necessaire pour le POC

ROOT_URLCONF = "twicc_poc.urls"

ASGI_APPLICATION = "twicc_poc.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "data.sqlite",
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
        "simple": {
            "format": "[{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "twicc_poc": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
