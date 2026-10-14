"""Exact final repair inventory and raw SQLite precision regressions."""

from datetime import UTC, datetime

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem
from twicc.core.content_storage.extraction import (
    begin_compute_application, close_compute_application, final_compute_message,
    prepare_compute_batch, publish_compute_batch,
)
from twicc.providers.compute_base import BaseSessionCompute
from twicc.providers.helpers import get_provider_helpers

pytestmark = pytest.mark.django_db(transaction=True)
STAMP = datetime(2026, 9, 29, tzinfo=UTC)


def make_session(**fields):
    fields.setdefault('compute_version', get_provider_helpers(Provider.CLAUDE_CODE).current_compute_version)
    project = Project.objects.create(id='obligation-project')
    return Session.objects.create(id='obligation-session', project=project, provider=Provider.CLAUDE_CODE,
                                  file_path='unused', **fields)


def complete(operation):
    while operation.phase != 'final':
        batch = prepare_compute_batch(operation)
        publish_compute_batch(operation, batch, frozenset())
    return final_compute_message(operation, frozenset())


def test_failed_metadata_transaction_does_not_publish_repair_obligations(monkeypatch):
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='{}', timestamp=STAMP)
    operation = begin_compute_application({'session_id': session.id, 'history_facts': [],
        'item_fields': ['timestamp'], 'item_updates': [{'id': item.id, 'timestamp': None}]})
    try:
        while operation.phase != 'metadata':
            publish_compute_batch(operation, prepare_compute_batch(operation), frozenset())
        batch = prepare_compute_batch(operation)
        def fail(*args):
            raise RuntimeError('rollback inventory')
        monkeypatch.setattr(BaseSessionCompute, 'apply_item_updates', fail)
        with pytest.raises(RuntimeError, match='rollback inventory'):
            publish_compute_batch(operation, batch, frozenset())
        item.refresh_from_db()
        assert item.timestamp == STAMP
    finally:
        close_compute_application(operation)


def test_metadata_and_final_subphase_diagnostics_are_payload_free(caplog, monkeypatch):
    import logging
    from twicc import sync_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics.logger, 'disabled', False)
    monkeypatch.setattr(diagnostics.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(diagnostics.logger, 'propagate', False)
    session = make_session()
    item = SessionItem.objects.create(session=session, line_num=1, content='SECRET-CONTENT', timestamp=STAMP)
    with caplog.at_level(logging.DEBUG, logger='twicc.sync_diagnostics'):
        assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': [],
            'item_fields': ['cost'], 'item_updates': [{'id': item.id, 'cost': '1.000001'}]}).outcome == 'applied'
    logs = '\n'.join(record.getMessage() for record in caplog.records if record.name == 'twicc.sync_diagnostics')
    for phase in ('compute_metadata_source', 'compute_final_links',
                  'compute_final_facts', 'compute_final_source', 'compute_final_readiness'):
        assert f'operation={phase} ' in logs
    assert 'SECRET-CONTENT' not in logs
    assert 'UPDATE ' not in logs and 'SELECT ' not in logs


def test_empty_metadata_keeps_source_and_aggregate_diagnostics_absent(monkeypatch, caplog):
    import logging
    from twicc import sync_diagnostics
    monkeypatch.setattr(sync_diagnostics.logger, 'disabled', False)
    monkeypatch.setattr(sync_diagnostics.logger, 'handlers', [caplog.handler])
    monkeypatch.setattr(sync_diagnostics.logger, 'propagate', False)
    session = make_session()
    with caplog.at_level(logging.DEBUG, logger='twicc.sync_diagnostics'):
        assert BaseSessionCompute.apply_session_complete({'session_id': session.id, 'history_facts': []}).outcome == 'applied'
    assert not any('operation=compute_metadata_' in record.getMessage() for record in caplog.records)
