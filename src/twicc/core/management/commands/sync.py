"""Django management command for synchronizing Claude projects."""

import asyncio

from django.core.management.base import BaseCommand
from django.db import transaction

from twicc.core.content_storage.ingestion import initialize_ingestion_session
from twicc.core.content_storage.references import remove_history_memberships
from twicc.core.models import Project, Session
from twicc.providers.claude_code.initial_sync import sync_all_with_progress


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
            with transaction.atomic():
                for session in Session.objects.all().iterator(chunk_size=128):
                    initialize_ingestion_session(session, invalidate=True)
                    remove_history_memberships(session.id)
                Project.objects.all().delete()
            self.stdout.write("Database cleared.")
            self.stdout.write("")

        self.stdout.write("Starting synchronization...")
        self.stdout.write("")
        asyncio.run(self._synchronize())

    async def _synchronize(self):
        from twicc.providers.db_writer import get_thread_queue, start_db_writer, stop_db_writer

        start_db_writer()
        try:
            await asyncio.to_thread(sync_all_with_progress, get_thread_queue(), stream=self.stdout)
        finally:
            await stop_db_writer()
