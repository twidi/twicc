"""Bounded historical agent-stop projection for compute publication."""

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from django.db import connection

from twicc.core.content_storage.lifecycle import apply_compute_lifecycle, lifecycle_change
from twicc.core.content_storage.state import TREE_PAGE_SIZE
from twicc.core.models import Session

STOP_AGENT_BUDGET = 64
STOP_BYTE_BUDGET = 256 * 1024


class StoppedAgentUpdate(NamedTuple):
    session_id: str
    stopped_at: datetime


def normalize_agent_stops(entries: Sequence[dict]) -> tuple[StoppedAgentUpdate, ...]:
    """Retain the latest stop, in first-agent order, without changing event history."""
    latest = {}
    for entry in entries:
        identity = entry['agent_session_id']
        stopped_at = datetime.fromisoformat(entry['stopped_at'])
        if identity not in latest or stopped_at > latest[identity]:
            latest[identity] = stopped_at
    return tuple(StoppedAgentUpdate(identity, stopped_at) for identity, stopped_at in latest.items())


def publish_agent_stops(msg: dict, updates: tuple[StoppedAgentUpdate, ...]) -> tuple[str, ...]:
    """Publish one slice under the caller's write-first source guard."""
    def mutate():
        metadata = {}
        identities = tuple(update.session_id for update in updates)
        for offset in range(0, len(identities), TREE_PAGE_SIZE):
            metadata.update({row['id']: row for row in Session.objects.filter(
                pk__in=identities[offset:offset + TREE_PAGE_SIZE],
            ).values('id', 'last_updated_at', 'last_stopped_at')})
        accepted = []
        for update in updates:
            row = metadata.get(update.session_id)
            if row is None or (row['last_updated_at'] is not None and row['last_updated_at'] > update.stopped_at):
                continue
            if row['last_updated_at'] == row['last_stopped_at'] == update.stopped_at:
                continue
            accepted.append(update)
        if not accepted:
            return ()
        identities = tuple(update.session_id for update in accepted)
        quote = connection.ops.quote_name
        table = quote(Session._meta.db_table)
        field = Session._meta.get_field('last_stopped_at')
        parameters = []
        for update in accepted:
            value = field.get_db_prep_save(update.stopped_at, connection)
            parameters.append((value, value, update.session_id, value))
        with lifecycle_change(identities, defer_plaintext_inspection=True), connection.cursor() as cursor:
            cursor.executemany(
                f'UPDATE {table} SET "last_stopped_at" = %s, "last_updated_at" = %s '
                'WHERE "id" = %s AND ("last_updated_at" IS NULL OR "last_updated_at" <= %s)',
                parameters,
            )
        return identities

    return apply_compute_lifecycle(msg, mutate)
