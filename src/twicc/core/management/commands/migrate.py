"""Add duration diagnostics while retaining Django's migration command."""

from time import perf_counter

from django.core.management.commands.migrate import Command as DjangoMigrateCommand

from twicc.db.migration_logging import current_migration, logger


class Command(DjangoMigrateCommand):
    def handle(self, *args, **options):
        token = current_migration.set(None)
        self._active_migration = None
        try:
            return super().handle(*args, **options)
        except BaseException:
            if self._active_migration is not None:
                self._log_progress("failure", self._active_fake, perf_counter() - self._migration_started)
            raise
        finally:
            self._active_migration = None
            current_migration.reset(token)

    def migration_progress_callback(self, action, migration=None, fake=False):
        if action in ("apply_start", "unapply_start"):
            direction = "forward" if action == "apply_start" else "backward"
            self._active_migration = (str(migration), direction)
            self._active_fake = fake
            self._migration_started = perf_counter()
            current_migration.set(self._active_migration)
            self._log_progress("start", fake)
        elif action in ("apply_success", "unapply_success"):
            # fake_initial can change fake=False at start to fake=True here.
            self._log_progress("success", fake, perf_counter() - self._migration_started)
            self._active_migration = None
            current_migration.set(None)
        return super().migration_progress_callback(action, migration, fake)

    def _log_progress(self, event, fake, duration=0):
        migration, direction = self._active_migration
        log = logger.error if event == "failure" else logger.info
        log(
            "Migration %s migration=%s direction=%s fake=%s duration_seconds=%.6f",
            event, migration, direction, fake, duration,
            extra={
                "migration_event": event, "migration": migration, "direction": direction,
                "fake": fake, "duration_seconds": duration,
            },
        )
