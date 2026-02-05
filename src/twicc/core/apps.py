import os
import time

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "twicc.core"

    def ready(self):
        # Restore system timezone after Django setup.
        # Django sets TZ to its TIME_ZONE setting (default: America/Chicago),
        # which overrides the system timezone. We remove it to use the actual
        # system timezone for logging and other time-related operations.
        if "TZ" in os.environ:
            del os.environ["TZ"]
            time.tzset()
