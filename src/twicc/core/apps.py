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

        from twicc.core import json_response

        json_response.install()

        from django.db.backends.signals import connection_created

        from twicc.providers import write_guard

        connection_created.connect(write_guard.install, dispatch_uid="twicc.write_guard")
