"""Materialize removed-history dates before source mutation admission."""
from typing import NamedTuple

from django.db import transaction

from twicc.core.models import Session, SessionItem
from twicc.providers.contribution_revisions import SourceKey, observe_revisions, revisions_match
from twicc.providers.live_aggregates import ItemContribution, session_contribution


class RemovalEvidence(NamedTuple):
    states: tuple
    items: tuple
    revisions: object


def prepare_removal(session_ids):
    with transaction.atomic():
        rows = tuple(Session.objects.filter(pk__in=session_ids))
        states = tuple(session_contribution(row) for row in rows)
        revisions = observe_revisions(sources=[SourceKey(row.id, row.storage_incarnation) for row in rows])
        scopes = {row.id: (row.project_id, row.provider) for row in rows}
        items = tuple(ItemContribution(sid, *scopes[sid], stamp, None, None)
            for sid, stamp in SessionItem.objects.filter(session_id__in=scopes).order_by().values_list(
                'session_id', 'timestamp').distinct())
    if not revisions_match(revisions):
        raise RuntimeError('Source changed during removal preparation')
    return RemovalEvidence(states, items, revisions)


def require_removal(evidence):
    current = {row.id: session_contribution(row) for row in Session.objects.filter(
        pk__in=[state.session_id for state in evidence.states])}
    if (not revisions_match(evidence.revisions)
            or current != {state.session_id: state for state in evidence.states}):
        raise RuntimeError('Source changed before removal admission')
