"""Pytest configuration for Django tests."""

import asyncio
import inspect
import itertools
import os
import re
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import django
import pytest
from django.conf import settings

from twicc import provider_homes

from tests.codex_agent_run_fixtures import T0

pytest_plugins = ["tests.content_storage_helpers", "tests.migration_templates"]

# The clock of ``frozen_now``: one day after the agent-run fixture lines, so
# their sessions stay hot (the cold policy starts at 30 days).
NOW = T0 + timedelta(days=1)


# One counter per directory name prefix, for the whole run (see ``tmp_path``).
_tmp_path_numbers = defaultdict(itertools.count)


@pytest.fixture
def tmp_path(request, tmp_path_factory) -> Path:
    """pytest's ``tmp_path``, minus its cost that grows with the run.

    pytest numbers each test's directory by listing the whole base temp dir,
    which by then holds a directory and a ``*current`` symlink for every test
    that ran before: quadratic over the suite, about ten seconds of the full
    run. Here the number comes from a counter and the directory sits in one
    folder per test module, so nothing is listed. Same name prefix (the test
    name, sanitized, cut to 30 characters, then a dash before the number) and
    same base temp dir, hence the same retention across runs. The
    ``tmp_path_retention_policy`` option is not honoured: this project keeps
    pytest's default (keep everything).
    """
    module = re.sub(r"\W", "_", request.node.path.stem)
    name = re.sub(r"\W", "_", request.node.name)[:30]
    # The dash cannot occur in the sanitized name: "test_x1" + 0 and
    # "test_x" + 10 stay distinct.
    path = tmp_path_factory.getbasetemp() / module / f"{name}-{next(_tmp_path_numbers[(module, name)])}"
    path.mkdir(mode=0o700, parents=True)
    return path


@pytest.fixture
def compute_executor_started():
    """Give direct watcher and compute-adapter tests their runtime worker."""
    from twicc.providers.compute_executor import start_compute_executor, stop_compute_executor

    start_compute_executor()
    try:
        yield
    finally:
        asyncio.run(stop_compute_executor())


def pytest_configure(config):
    """Configure Django settings before tests run."""
    config.addinivalue_line(
        "markers", "real_retry_delays: keep the compute retry waits at their real length (see short_compute_retries)",
    )
    config.addinivalue_line(
        "markers", "frozen_now_at(instant): the instant ``frozen_now`` freezes instead of NOW (see frozen_now)",
    )
    if not settings.configured:
        settings.configure()
    django.setup()


@pytest.fixture(autouse=True, scope="session")
def warm_compute_workers():
    """Start the compute workers from the preloaded forkserver (see :mod:`tests.warm_spawn`)."""
    from tests.warm_spawn import WarmSpawnProcess
    from twicc.providers import background_compute_task

    context = background_compute_task._mp_ctx
    context.Process = WarmSpawnProcess
    yield
    del context.Process


@pytest.fixture(autouse=True, scope="session")
def no_post_migrate_after_flush():
    """Skip the ``post_migrate`` signal of the flush that ends each ``transaction=True`` test.

    Its only receiver recreates the ``contenttypes`` rows (one query per model
    and app), about 20 ms per flush; neither TwiCC nor a test reads them. A
    real ``migrate`` still sends the signal.
    """
    from unittest.mock import patch

    with patch("django.core.management.commands.flush.emit_post_migrate_signal"):
        yield


@pytest.fixture(autouse=True, scope="session")
def compact_flushed_test_database():
    """Keep large fixtures' free pages out of later workers' SQLite copies.

    A flush deletes rows but retains the database's allocated pages. Scale
    tests leave tens of megabytes of empty pages, copied by every subsequent
    WAL fixture. Compact only the shared memory test database, after the real
    flush completes, and only when at least half its pages are free.
    """
    from unittest.mock import patch

    from django.core.management.commands.flush import Command
    from django.db import connections

    original = Command.handle

    def flush_and_compact(command, *args, **options):
        result = original(command, *args, **options)
        database = connections[options["database"]]
        if database.vendor == "sqlite" and database.creation.is_in_memory_db(database.settings_dict["NAME"]):
            with database.cursor() as cursor:
                cursor.execute("PRAGMA freelist_count")
                free = cursor.fetchone()[0]
                if free >= 256:
                    cursor.execute("PRAGMA page_count")
                    if free * 2 >= cursor.fetchone()[0]:
                        cursor.execute("VACUUM")
        return result

    with patch.object(Command, "handle", flush_and_compact):
        yield


@pytest.fixture
def monotonic_clock():
    """Advance deterministic CLI polling without waiting on wall time."""
    from types import SimpleNamespace

    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    return SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)


@pytest.fixture
def small_compute_pages(monkeypatch):
    """Cross the real publication phase boundaries with small page budgets.

    Continuation tests need several pages and a nested tree, rather than
    hundreds of interchangeable rows. The dedicated lifecycle, validation,
    and history-fact tests retain the production budgets and scale fixtures.
    """
    from functools import partial
    from types import SimpleNamespace

    from twicc.core.content_storage import extraction, validation
    from twicc.providers import compute_lifecycle, history_facts

    monkeypatch.setattr(extraction, "ROW_BUDGET", 2)
    monkeypatch.setattr(compute_lifecycle, "STOP_AGENT_BUDGET", 2)
    monkeypatch.setattr(history_facts, "_FACT_PAGE_SIZE", 2)
    monkeypatch.setattr(validation, "prepare_validation_page", partial(validation.prepare_validation_page, row_budget=2))
    for name in ("prepare_fact_delete_page", "prepare_fact_insert_page"):
        monkeypatch.setattr(history_facts, name, partial(getattr(history_facts, name), row_budget=2))
    return SimpleNamespace(items=5, agents=3, facts=5, page_size=2)


@pytest.fixture
def metadata_publication(monkeypatch):
    """Publish producer metadata without running the content-storage protocol.

    Agent links, run ends, plans, groups, and catalogs need the real compute
    producer, revision guard, and final publisher. Their legacy seed rows do
    not need a complete content extraction and certification on each replay.
    Live ingestion stays real. Storage/publication integration tests do not
    request this fixture and retain the complete protocol.
    """
    from twicc.core.content_storage import extraction

    monkeypatch.setattr(extraction, "prepare_compute_result", lambda message: message)
    monkeypatch.setattr(extraction, "finish_storage_result", lambda message: None)


@pytest.fixture
def metadata_rows(monkeypatch, metadata_publication):
    """Seed normalized legacy rows for agent-run business scenarios.

    Batch scenarios already seed legacy rows. Live scenarios use the same
    representation here, while retaining real slicing, provider hooks,
    history facts, links, run ends, lifecycle fields, and revision guards.
    Content extraction, ownership, and certificates run in the dedicated
    storage, golden replay, and WebSocket integration tests.
    """
    from contextlib import nullcontext
    from importlib import import_module

    from twicc.core.content_storage import ingestion, lifecycle
    from twicc.core.content_storage.readers import effective_current
    from twicc.core.content_storage.state import ensure_storage_incarnation
    from twicc.core.models import SessionItem

    # Its lazy from-import must bind the real function before this fixture
    # replaces the source attribute. Otherwise later storage tests retain
    # the fixture's no-op after monkeypatch restores that source attribute.
    import_module('twicc.providers.compute_lifecycle')

    def initialize(session, **kwargs):
        session.storage_incarnation = ensure_storage_incarnation(session.id)
        if metadata := kwargs.get("metadata"):
            metadata[session.id]["storage_incarnation"] = session.storage_incarnation

    def insert(session, items, **kwargs):
        lines = [item.line_num for item, _ in items]
        rows = SessionItem.objects.filter(session_id=session.id, line_num__in=lines)
        existing = set(rows.values_list("line_num", flat=True))
        for item, _ in items:
            item.content = effective_current(item)
        SessionItem.objects.bulk_create([item for item, _ in items], ignore_conflicts=True)
        return set(rows.values_list("line_num", flat=True)) - existing

    monkeypatch.setattr(lifecycle, "lifecycle_change", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(ingestion, "initialize_ingestion_session", initialize)
    monkeypatch.setattr(ingestion, "ingestion_plaintext_change", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(ingestion, "insert_ingested_items", insert)
    monkeypatch.setattr(ingestion, "promote_ingested_items", lambda *args, **kwargs: None)


@pytest.fixture
def agent_run_metadata(monkeypatch, metadata_rows):
    """Keep agent-run scenarios independent of the statistics projection.

    Provider hooks, facts, links, run ends and lifecycle fields stay real.
    Statistics, message counts and cost publication have their own integration
    tests; these scenarios assert only agent-run evidence and run states.
    """
    from twicc.providers import compute_base

    monkeypatch.setattr(compute_base, "item_contributions", lambda queryset: [])
    monkeypatch.setattr(compute_base, "apply_contribution_changes", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def fresh_process_state(monkeypatch):
    """Isolate caches, title queues, and project directories between tests.

    Session ids recur with different providers. Settings, dictionaries, and
    content rows also change after rollbacks and database flushes. Reset
    these process caches together, while each title queue and directory map
    gets its own container. Keep the same reset at teardown.
    """
    from twicc import projects, synced_settings, title_auto_task
    from twicc.core.content_storage.cold_cache import reset_cold_cache
    from twicc.core.content_storage.dictionaries import reset_dictionary_cache
    from twicc.core.services.session_timeline import clear_provider_cache

    def clear_caches():
        synced_settings._cache.clear()
        clear_provider_cache()
        reset_dictionary_cache()
        reset_cold_cache()

    clear_caches()
    monkeypatch.setattr(title_auto_task, "_queued", deque())
    monkeypatch.setattr(title_auto_task, "_pending", {})
    monkeypatch.setattr(title_auto_task, "_running", set())
    monkeypatch.setattr(title_auto_task, "_wake", None)
    monkeypatch.setattr(projects, "_project_directories", {})
    yield
    clear_caches()


@pytest.fixture(autouse=True, scope="session")
def cheap_password_hashes():
    """Hash new passwords with 1 000 PBKDF2 rounds instead of 600 000.

    The real count costs ~0.3 s per hash and per check, and no test is about
    the work factor. The count is written in the hash itself, so
    ``verify_password`` still runs the real check on what was stored.
    """
    from twicc.auth import hashers
    from unittest.mock import patch

    with patch.object(hashers, "_PBKDF2_ITERATIONS", 1000):
        yield


class _ShortSleepAsyncio:
    """``asyncio`` as seen by ``db_writer``, with its sleeps cut to 1 ms."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(delay, result=None):
        return await asyncio.sleep(min(delay, 0.001), result)


@pytest.fixture(autouse=True, scope="session")
def short_db_writer_polls():
    """Cut the DB writer's 50 ms sleeps to 1 ms.

    The writer sleeps 50 ms whenever its queues are empty (so each job waits up
    to 50 ms) and between retries on a full queue. Both are pure latency, never
    an ordering the code relies on: jobs still go through the real queues, lock
    and loop, in the same order.
    """
    from twicc.providers import db_writer
    from unittest.mock import patch

    with patch.object(db_writer, "asyncio", _ShortSleepAsyncio()):
        yield


@pytest.fixture(scope="session")
def short_compute_retry_defaults():
    """Install stateless retry wrappers once, retaining the original callables."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from twicc.providers import background_compute_task, compute_selection
    from twicc.providers.codex import background_compute

    real_wait = background_compute_task._wait_for_retry_delay
    real_deadline = compute_selection.compute_retry_deadline
    real_read = background_compute_task.read_compute_state

    async def short_wait(stop_event, delay):
        return await real_wait(stop_event, min(delay, 0.01))

    def short_deadline(delay):
        return real_deadline(min(delay, 0.01))

    async def short_read(read, session_ids, stop_event, wait_for_delay=None, stopped=None):
        # The function's default captured the original wait at import time.
        # Explicit test waiters still observe the actual backoff sequence.
        return await real_read(read, session_ids, stop_event,
                               short_wait if wait_for_delay is None else wait_for_delay, stopped)

    originals = []
    with ExitStack() as stack:
        for module in (background_compute_task, background_compute):
            for name, value in (("_wait_for_retry_delay", short_wait), ("compute_retry_deadline", short_deadline),
                                ("read_compute_state", short_read)):
                originals.append((module, name, getattr(module, name)))
                stack.enter_context(patch.object(module, name, value))
        yield originals


@pytest.fixture(autouse=True)
def short_compute_retries(request, monkeypatch, short_compute_retry_defaults):
    """Cap retry waits at 10 ms except in explicit retry-delay tests.

    Keep the real stop-aware wait, retry selection, and backoff values.
    Only marked tests need per-test patches; ordinary tests share the
    stateless wrappers instead of installing retry patches each time.
    """
    if request.node.get_closest_marker("real_retry_delays"):
        for module, name, original in short_compute_retry_defaults:
            monkeypatch.setattr(module, name, original)


@pytest.fixture(autouse=True, scope="session")
def no_fsync():
    """Skip the disk flush of ``os.fsync``: tests never survive a power cut anyway.

    The attachment and upload stores fsync every file and directory they
    write, a few milliseconds each. Nothing a test observes depends on the
    data reaching the disk. The descriptor is still checked, so fsyncing a
    closed or invalid one keeps raising, as the real call does. A test that is
    about fsync (that it is called, or that its failure is handled) patches it
    again on top of this one.
    """
    def checked_fsync(fd):
        os.fstat(fd)

    from unittest.mock import patch

    with patch.object(os, "fsync", checked_fsync):
        yield


@pytest.fixture(autouse=True, scope="session")
def no_real_notification_delivery():
    """Cut the wire at Apprise: no test may reach a real notification service.

    ``settings_test`` now isolates the data dir too, so no test reads the
    developer's live targets any more. This stays as a second line: a test
    that writes a real target into the isolated ``settings.json`` must still
    not reach the service. On 2026-09-09, before that isolation, a full-suite
    run pushed several "Message from alice" events from
    ``test_peer_messages.py`` to the developer's ntfy phone.

    ``async_notify`` is the single network boundary — the only Apprise call
    either send path makes. Stubbing it there leaves every layer above it
    real: the target filters, the presence deferral, ``_spawn``/``_send``, URL
    validation and privacy masking all still run, so tests keep asserting that
    a notification *is* dispatched, and what it contains. Only the delivery is
    dropped, which is exactly what a real success returns (``True``).
    """
    import apprise
    from unittest.mock import patch

    async def _swallow(self, *args, **kwargs):
        return True

    with patch.object(apprise.Apprise, "async_notify", _swallow):
        yield


@pytest.fixture
def fresh_data_dir(tmp_path, monkeypatch) -> Path:
    """An empty data dir of the test's own, in a process pinned to UTC.

    For the code that lists or watches the data dir root (the data dir
    backups): the suite-wide isolated data dir is shared by every test. UTC
    makes "local calendar day" equal to the UTC day; a test that needs another
    zone sets ``TZ`` through ``monkeypatch`` and calls ``time.tzset()``, and
    this fixture's ``undo()`` restores both.
    """
    import time

    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield path
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def db_setup(db):
    """Fixture that provides database access and creates test data helpers."""
    return db


class ProviderHomeDirs(NamedTuple):
    """The two provider homes a test owns (see :func:`provider_home`)."""
    claude: Path
    codex: Path


@pytest.fixture
def provider_home(tmp_path, monkeypatch) -> ProviderHomeDirs:
    """Point both provider homes at fresh directories under ``tmp_path``.

    Sets ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` (``settings_test`` already
    isolates them under a per-process temp root; this narrows them to the
    test) and resets the resolver's cache so every accessor of
    ``twicc.provider_homes`` — ``claude_projects_dir()``, ``codex_sessions_dir()``,
    ``claude_plans_dir()``… — reads the new values. The test creates the
    subfolder it needs (``projects/``, ``sessions/``, ``plans/``).
    """
    dirs = ProviderHomeDirs(claude=tmp_path / "claude-home", codex=tmp_path / "codex-home")
    dirs.claude.mkdir()
    dirs.codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(dirs.claude))
    monkeypatch.setenv("CODEX_HOME", str(dirs.codex))
    provider_homes.reset_cache()
    yield dirs
    # Runs before monkeypatch restores the environment: the next resolution
    # (empty cache) reads the restored settings_test values.
    provider_homes.reset_cache()


@pytest.fixture
def frozen_now(request, monkeypatch):
    """Freeze ``django.utils.timezone.now`` at :data:`NOW`.

    The content-storage modules call ``timezone.now()`` through the module, so
    this one patch covers them. Without it, sessions dated around ``T0`` turn
    cold 30 days later and both the cold/hot decisions and the query counts of
    a live slice change. A module whose lines use another fixed date sets the
    ``frozen_now_at(instant)`` marker, one day after its own dates. A test that
    needs another clock patches ``timezone.now`` again after this fixture.
    """
    from django.utils import timezone

    marker = request.node.get_closest_marker("frozen_now_at")
    now = marker.args[0] if marker else NOW
    monkeypatch.setattr(timezone, "now", lambda: now)
    return now


class ContentStorageEffects(NamedTuple):
    """What the content storage scheduled: promotion wakeups and cold-cache invalidations, in call order."""
    wakeups: list
    cold: list


@pytest.fixture
def effects(monkeypatch) -> ContentStorageEffects:
    """Record every promotion wakeup and cold-cache invalidation, then run the real one.

    Both functions are imported lazily by their callers, so patching the module
    attribute catches every call. Invalidations are recorded when scheduled
    (inside the transaction), wakeups when they run (after commit).
    """
    from twicc.core.content_storage import cold_cache, lifecycle

    recorded = ContentStorageEffects([], [])
    request_root_promotion = lifecycle.request_root_promotion
    schedule_cold_invalidation = cold_cache.schedule_cold_invalidation

    def recording_request_root_promotion(session_id, *args, **kwargs):
        recorded.wakeups.append(session_id)
        return request_root_promotion(session_id, *args, **kwargs)

    signature = inspect.signature(schedule_cold_invalidation)

    def recording_schedule_cold_invalidation(*args, **kwargs):
        # Callers pass collections (never one-shot iterators): recording them leaves the call unchanged.
        call = signature.bind(*args, **kwargs)
        call.apply_defaults()
        lifecycle_session_ids = call.arguments["lifecycle_session_ids"]
        recorded.cold.append((
            sorted(call.arguments["session_ids"]), call.arguments["mappings"],
            sorted(lifecycle_session_ids) if lifecycle_session_ids is not None else None,
        ))
        return schedule_cold_invalidation(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "request_root_promotion", recording_request_root_promotion)
    monkeypatch.setattr(cold_cache, "schedule_cold_invalidation", recording_schedule_cold_invalidation)
    return recorded
