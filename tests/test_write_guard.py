"""SQL writes issued outside the DB writer lock are reported once per call site."""
import asyncio
import logging

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.models import Session as DjangoSession
from django.db import connection
from django.utils import timezone

from twicc.core.models import Project
from twicc.projects import update_project_metadata
from twicc.providers import db_writer, write_guard

GUARD_LOGGER = 'twicc.providers.write_guard'


@pytest.fixture(autouse=True)
def _enabled_guard_logger(monkeypatch):
    """``settings_test`` disables the loggers that exist when it configures logging."""
    monkeypatch.setattr(write_guard.logger, 'disabled', False)


@pytest.mark.parametrize('sql,table', [
    ('INSERT INTO "core_session" ("id") VALUES (%s)', 'core_session'),
    ('INSERT OR IGNORE INTO "core_session" ("id") VALUES (%s)', 'core_session'),
    ('REPLACE INTO core_session (id) VALUES (%s)', 'core_session'),
    ('UPDATE "core_session" SET "title" = %s', 'core_session'),
    ('  update core_session set title = %s', 'core_session'),
    ('DELETE FROM "core_session" WHERE "id" = %s', 'core_session'),
    ('SELECT "core_session"."id" FROM "core_session"', None),
    ('INSERT OR IGNORE INTO temp.history_stage (id) VALUES (%s)', None),
    ('DELETE FROM "temp"."history_stage"', None),
    ('INSERT INTO "content_reference_counts_0a1b" (content_id) VALUES (%s)', None),
    ('UPDATE "django_session" SET "session_data" = %s', None),
    ('DELETE FROM "django_session" WHERE "session_key" = %s', None),
])
def test_written_table(sql, table):
    assert write_guard.written_table(sql) == table


def reports(caplog):
    return [record for record in caplog.records if record.name == GUARD_LOGGER]


def run_with_writer(scenario):
    async def wrapper():
        db_writer.start_db_writer()
        try:
            await scenario()
        finally:
            await db_writer.stop_db_writer()
    asyncio.run(wrapper())


@pytest.mark.django_db(transaction=True)
def test_unlocked_write_is_reported_once_per_call_site(caplog):
    project = Project.objects.create(id='guarded-project')

    async def scenario():
        await sync_to_async(update_project_metadata)(project.id)
        await sync_to_async(update_project_metadata)(project.id)

    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        run_with_writer(scenario)
    assert len(reports(caplog)) == 1
    message = reports(caplog)[0].getMessage()
    assert 'table=core_project' in message and 'site=projects.py:' in message
    assert reports(caplog)[0].stack_info


@pytest.mark.django_db(transaction=True)
def test_write_under_the_lock_is_not_reported(caplog):
    project = Project.objects.create(id='guarded-project')

    async def scenario():
        await db_writer.run_under_db_write_lock(lambda: sync_to_async(update_project_metadata)(project.id))

    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        run_with_writer(scenario)
    assert reports(caplog) == []


@pytest.mark.django_db(transaction=True)
def test_write_on_the_compute_worker_inherits_the_lease(caplog):
    project = Project.objects.create(id='guarded-project')

    async def scenario():
        await db_writer.run_under_db_write_lock(lambda: db_writer.run_compute_sync(update_project_metadata, project.id))

    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        run_with_writer(scenario)
    assert reports(caplog) == []


@pytest.mark.django_db(transaction=True)
def test_write_after_the_lock_release_is_reported(caplog):
    project = Project.objects.create(id='guarded-project')

    async def scenario():
        await db_writer.run_under_db_write_lock(lambda: sync_to_async(update_project_metadata)(project.id))
        await sync_to_async(update_project_metadata)(project.id)

    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        run_with_writer(scenario)
    assert len(reports(caplog)) == 1


@pytest.mark.django_db(transaction=True)
def test_django_session_write_is_not_reported(caplog):
    def write():
        DjangoSession.objects.create(session_key='guarded', session_data='', expire_date=timezone.now())

    async def scenario():
        await sync_to_async(write)()

    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        run_with_writer(scenario)
    assert reports(caplog) == []


@pytest.mark.django_db(transaction=True)
def test_nothing_is_reported_outside_the_db_writer_lifetime(caplog):
    project = Project.objects.create(id='guarded-project')
    with caplog.at_level(logging.ERROR, logger=GUARD_LOGGER):
        update_project_metadata(project.id)
    assert reports(caplog) == []


@pytest.mark.django_db
def test_wrapper_survives_a_connection_opened_inside_execute_wrapper(monkeypatch):
    def record(execute, sql, params, many, context):
        return execute(sql, params, many, context)

    monkeypatch.setattr(connection, 'execute_wrappers', [])
    with connection.execute_wrapper(record):
        write_guard.install(connection=connection)
    assert connection.execute_wrappers == [write_guard.report_unlocked_writes]
