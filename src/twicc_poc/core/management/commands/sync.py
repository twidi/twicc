"""Django management command for synchronizing Claude projects."""

from django.core.management.base import BaseCommand

from twicc_poc.core.models import Project
from twicc_poc.sync import sync_all_with_progress


class Command(BaseCommand):
    help = "Synchronize projects and sessions from Claude projects directory"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all data and perform a full resynchronization from scratch",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self.stdout.write("Resetting database...")
            # Delete Projects first - cascades to Session and SessionItem
            Project.objects.all().delete()
            self.stdout.write("Database cleared.")
            self.stdout.write("")

        self.stdout.write("Starting synchronization...")
        self.stdout.write("")
        sync_all_with_progress(stream=self.stdout)
