"""Best-effort, live-only message snapshots from complete native source groups."""

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime

import orjson

from twicc.agent.completion_types import NativeMessageRef
from twicc.core.content_storage.readers import iter_current_items
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, Session, SessionItem, SessionType, TimelineEvent, TimelineKind
from twicc.core.services.session_timeline import record_event_sync
from twicc.providers.helpers import get_provider_helpers
from twicc.request_origin import RequestOrigin, origin_from_payload

logger = logging.getLogger(__name__)
_MESSAGE_KINDS = (TimelineKind.USER_MESSAGE_SENT, TimelineKind.ASSISTANT_MESSAGE_SENT)


def _refs(helpers, parsed):
    refs = list(helpers.completion_record_refs(parsed))
    if helpers.provider == 'codex':
        from twicc.providers.codex.canonical import completed_item
        item = completed_item(parsed)
        if item and item.get('type') == 'UserMessage' and isinstance(item.get('clientId'), str) and item['clientId']:
            refs.append(NativeMessageRef('codex_client', item['clientId'], (parsed.get('payload') or {}).get('turn_id')))
    return [ref for ref in refs if isinstance(ref.native_id, str) and ref.native_id]


def _read(session, query):
    helpers = get_provider_helpers(session.provider)
    for item in iter_current_items(query.order_by('line_num'), 128, 8 * 1024 * 1024):
        parsed = helpers.parse_item_content(item)
        if not parsed:
            continue
        if session.provider == 'claude_code' and not isinstance(parsed.get('message'), dict):
            continue
        if session.provider == 'codex':
            if not isinstance(parsed.get('payload'), dict):
                continue
            thread = (parsed.get('payload') or {}).get('thread_id')
            if thread is not None and thread != session.id:
                continue
        refs = _refs(helpers, parsed)
        if refs:
            yield item, parsed, refs


def _canonical(session, record):
    item, parsed, refs = record
    kind = 'claude_message' if session.provider == 'claude_code' and parsed.get('type') == 'assistant' else (
        'claude_uuid' if session.provider == 'claude_code' else 'codex_item')
    return next((ref for ref in refs if ref.kind == kind), None)


def _capture(session, records, *, timeline_since, verified=(), eligible=False):
    if not records or session.type != SessionType.SESSION:
        return
    records = sorted(records, key=lambda record: record[0].line_num)
    helpers = get_provider_helpers(session.provider)
    first = records[0][0]
    if not eligible and (timeline_since is None or first.timestamp is None or first.timestamp < timeline_since):
        return
    user = first.kind == ItemKind.USER_MESSAGE
    if not user:
        if not any(item.kind == ItemKind.ASSISTANT_MESSAGE for item, _, _ in records):
            return
        verdicts = [helpers.is_final_assistant_message(parsed) for _, parsed, _ in records]
        if False in verdicts:
            return
        # A group ID cannot prove its terminal block is indexed. Only an exact
        # UUID (or a completed canonical Codex item) supplies that evidence.
        exact_proof = any(ref in verified and ref.kind != 'claude_message'
                          for _, _, refs in records for ref in refs)
        if True not in verdicts and not exact_proof:
            return
    selected = [(item, parsed, refs) for item, parsed, refs in records
                if item.kind == (ItemKind.USER_MESSAGE if user else ItemKind.ASSISTANT_MESSAGE)]
    parts = [helpers.extract_timeline_text_from_parsed(parsed) for _, parsed, _ in selected]
    text = '\n'.join(part for part in parts if part)
    if not text.strip():
        return
    identity = _canonical(session, records[0])
    if identity is None:
        return
    aliases = list(dict.fromkeys(ref for _, _, refs in records for ref in refs))
    kind = TimelineKind.USER_MESSAGE_SENT if user else TimelineKind.ASSISTANT_MESSAGE_SENT
    digest = hashlib.sha256(orjson.dumps([session.provider, kind, identity.kind, identity.native_id])).hexdigest()
    # Source files do not prove that a user-role message came from the human UI.
    # An unrecognized actor/via pair is the timeline's existing unknown convention.
    origin = origin_from_payload(selected[0][1].get('_origin')) if user else None
    record_event_sync(kind, session_id=session.id, project_id=session.project_id, provider=session.provider,
                      occurred_at=first.timestamp, origin=origin or RequestOrigin('external' if user else 'agent'),
                      dedupe_key='msg:' + digest,
                      data={'text': text, 'source_refs': [ref._asdict() for ref in aliases],
                            'source_line': first.line_num})


def _groups(session, records):
    groups = {}
    for record in records:
        identity = _canonical(session, record)
        if identity is not None:
            groups.setdefault((identity.kind, identity.native_id), []).append(record)
    for (kind, identity), group in groups.items():
        if kind != 'claude_message':
            yield group[:1]
            continue
        seen = set()
        complete = []
        for record in _read(session, SessionItem.objects.filter(session_id=session.id, message_id=identity)):
            uuid = next((ref.native_id for ref in record[2] if ref.kind == 'claude_uuid'), None)
            if uuid is None or uuid not in seen:
                complete.append(record)
            if uuid is not None:
                seen.add(uuid)
        yield complete


def record_indexed_messages(session_id: str, *, new_line_nums: Sequence[int],
                            timeline_since: datetime | None,
                            verified_final_refs: Sequence[NativeMessageRef] = ()) -> None:
    """Capture only new live candidates and their exact Claude message groups."""
    if not new_line_nums:
        return
    try:
        saved = _saved_refs(session_id)
        if timeline_since is None and not saved:
            return
        session = Session.objects.get(pk=session_id)
        saved = _current_saved_refs(session, saved)
        rows = SessionItem.objects.filter(session_id=session_id, line_num__in=new_line_nums)
        for group in _groups(session, _read(session, rows)):
            _capture(session, group, timeline_since=timeline_since,
                     verified=(*verified_final_refs, *saved),
                     eligible=any(ref in saved for _, _, aliases in group for ref in aliases))
    except Exception:
        logger.exception('Timeline message capture failed for %s', session_id)


def _record_refs(session, refs, *, timeline_since, floor=0, eligible=False, verified=None, saved=()):
    wanted = set(refs)
    if not wanted:
        return
    # IDs live in current provider JSON, which can be compressed. Read bounded
    # pages and match exact aliases; the source floor is only a read boundary.
    query = SessionItem.objects.filter(session_id=session.id, line_num__gt=floor)
    if all(ref.kind == 'claude_message' for ref in refs):
        query = query.filter(message_id__in=[ref.native_id for ref in refs])
    records = (record for record in _read(session, query) if wanted.intersection(record[2]))
    for group in _groups(session, records):
        _capture(session, group, timeline_since=timeline_since,
                 verified=refs if verified is None else verified,
                 eligible=eligible or any(ref in saved for _, _, aliases in group for ref in aliases))


def record_verified_messages(session_id: str, *, refs: Sequence[NativeMessageRef], timeline_since: datetime) -> None:
    """Resolve final proof on an already-admitted writer; never guess adjacent text."""
    try:
        session = Session.objects.get(pk=session_id)
        saved = _current_saved_refs(session, _saved_refs(session_id))
        _record_refs(session, refs, timeline_since=timeline_since, saved=saved)
    except Exception:
        logger.exception('Verified timeline message capture failed for %s', session_id)


def _saved_refs(session_id):
    return {NativeMessageRef(**ref) for refs in TimelineEvent.objects.filter(
        session_id=session_id, kind__in=[TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED],
    ).values_list('data__message_refs', flat=True) for ref in refs or []}


def _same_scope(turn, session):
    return (turn.provider == session.provider
            and (turn.storage_incarnation is None or turn.storage_incarnation == session.storage_incarnation)
            and (turn.history_epoch is None or turn.history_epoch == session.history_epoch))


def _current_saved_refs(session, refs):
    if not refs:
        return refs
    refs = set(refs)
    for turn in LogicalTurn.objects.filter(session_id=session.id):
        if not _same_scope(turn, session):
            refs -= {NativeMessageRef(**ref) for ref in ([turn.first_ref] if turn.first_ref else []) + turn.final_refs}
    return refs


def recover_referenced_messages(session_id: str) -> None:
    """Resolve only saved live-turn aliases, including after journal retention ends."""
    try:
        pending = _saved_refs(session_id)
        if not pending:
            return
        resolved = {NativeMessageRef(**ref) for refs in TimelineEvent.objects.filter(
            session_id=session_id, kind__in=_MESSAGE_KINDS,
        ).values_list('data__source_refs', flat=True) for ref in refs or []}
        pending -= resolved
        if not pending:
            return
        session = Session.objects.get(pk=session_id)
        for turn in LogicalTurn.objects.filter(session_id=session_id):
            turn_refs = {NativeMessageRef(**ref) for ref in ([turn.first_ref] if turn.first_ref else []) + turn.final_refs}
            refs = pending & turn_refs
            if not refs:
                continue
            pending -= refs
            if not _same_scope(turn, session):
                continue
            _record_refs(session, refs, timeline_since=None, floor=turn.source_floor, eligible=True,
                         verified=[NativeMessageRef(**ref) for ref in turn.final_refs])
        if pending:
            _record_refs(session, pending, timeline_since=None, eligible=True)
    except Exception:
        logger.exception('Referenced timeline message recovery failed for %s', session_id)
