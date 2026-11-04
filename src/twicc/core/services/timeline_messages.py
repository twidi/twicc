"""Best-effort, live-only message snapshots from complete native source groups."""

import hashlib
import logging
from collections.abc import Callable, Sequence
from datetime import datetime

import orjson

from twicc.agent.completion_types import NativeMessageRef, VerifiedMessageRef
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, Session, SessionItem, SessionType, TimelineEvent, TimelineKind
from twicc.core.services.message_sources import _canonical, _groups, _read
from twicc.core.services.session_timeline import record_event_sync
from twicc.providers.helpers import get_provider_helpers
from twicc.request_origin import RequestOrigin, SYSTEM_ORIGIN, origin_from_payload

logger = logging.getLogger(__name__)
_MESSAGE_KINDS = (TimelineKind.USER_MESSAGE_SENT, TimelineKind.ASSISTANT_MESSAGE_SENT)


def _saved_input_origins(session):
    return [entry for entries in TimelineEvent.objects.filter(
        session_id=session.id, provider=session.provider,
        kind__in=[TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED],
    ).values_list('data__input_origins', flat=True) for entry in entries or []]


def _input_origin(session, first, aliases, origins):
    wanted = [ref._asdict() for ref in aliases]
    for entry in origins:
        if (entry['ref'] not in wanted or first.line_num <= entry['source_floor']
                or entry['storage_incarnation'] not in (None, session.storage_incarnation)
                or entry['history_epoch'] not in (None, session.history_epoch)):
            continue
        raw = entry['origin']
        # These proofs come from TwiCC's own request capture, not a drop payload.
        origin = SYSTEM_ORIGIN if raw == SYSTEM_ORIGIN._asdict() else origin_from_payload(raw)
        if origin is not None:
            return origin
    return None


def _capture(session, records, *, timeline_since, verified=(), eligible=False, input_origins=()):
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
    if user:
        from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
        if text.lstrip().startswith(SHELL_NOTICE_HEADER):
            return
    identity = _canonical(session, records[0])
    if identity is None:
        return
    aliases = list(dict.fromkeys(ref for _, _, refs in records for ref in refs))
    kind = TimelineKind.USER_MESSAGE_SENT if user else TimelineKind.ASSISTANT_MESSAGE_SENT
    digest = hashlib.sha256(orjson.dumps([session.provider, kind, identity.kind, identity.native_id])).hexdigest()
    # A user role alone proves no sender. Match the exact outgoing identity to
    # its captured request, including when indexing follows turn publication.
    origin = (_input_origin(session, first, aliases, input_origins)
              or origin_from_payload(selected[0][1].get('_origin'))) if user else None
    record_event_sync(kind, session_id=session.id, project_id=session.project_id, provider=session.provider,
                      occurred_at=first.timestamp, origin=origin or RequestOrigin('external' if user else 'agent'),
                      dedupe_key='msg:' + digest,
                      data={'text': text, 'source_refs': [ref._asdict() for ref in aliases],
                            'source_line': first.line_num, 'storage_incarnation': session.storage_incarnation,
                            'history_epoch': session.history_epoch})


def record_indexed_messages(session_id: str, *, new_line_nums: Sequence[int],
                            timeline_since: datetime | None,
                            verified_final_refs: Sequence[VerifiedMessageRef] | None = None,
                            get_input_origins: Callable[[], list] | None = None) -> None:
    """Capture only new live candidates and their exact Claude message groups."""
    if not new_line_nums:
        return
    try:
        saved_groups = _saved_ref_groups(session_id)
        if timeline_since is None and not saved_groups:
            return
        session = Session.objects.get(pk=session_id)
        rows = SessionItem.objects.filter(session_id=session_id, line_num__in=new_line_nums)
        groups = list(_groups(session, _read(session, rows)))
        origins = [*(get_input_origins() if get_input_origins is not None else []), *_saved_input_origins(session)] if any(
            group[0][0].kind == ItemKind.USER_MESSAGE for group in groups) else []
        helpers = get_provider_helpers(session.provider)
        needs_proof = (session.provider == 'claude_code' or verified_final_refs) and any(
            group[0][0].kind == ItemKind.ASSISTANT_MESSAGE
            and all(helpers.is_final_assistant_message(parsed) is None for _, parsed, _ in group)
            for group in groups)
        turns = list(LogicalTurn.objects.filter(session_id=session.id)) if saved_groups or needs_proof else []
        saved = _current_saved_refs(session, saved_groups, turns=turns)
        runtime_proof = [proof for proof in verified_final_refs or () if _same_scope(proof, session)]
        runtime_refs = {proof.ref for proof in runtime_proof}
        # Opening scope travels with runtime proof after journal cleanup.
        # Open Codex journal refs include unverified replacement candidates.
        # Saved event refs preserve verified proof after runtime cleanup.
        proof = [NativeMessageRef(**ref) for turn in turns
                 if _same_scope(turn, session) and turn.outcome in (None, 'normal') for ref in turn.final_refs
                 if (session.provider != 'codex' or NativeMessageRef(**ref) in runtime_refs
                     or turn.outcome == 'normal' and NativeMessageRef(**ref) in saved)]
        for group in groups:
            _capture(session, group, timeline_since=timeline_since,
                     verified=(*saved, *proof, *(item.ref for item in runtime_proof
                                                if group[0][0].line_num > item.source_floor)),
                     eligible=any(ref in saved for _, _, aliases in group for ref in aliases), input_origins=origins)
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
    origins = _saved_input_origins(session)
    for group in _groups(session, records):
        if group[0][0].line_num <= floor:
            continue
        _capture(session, group, timeline_since=timeline_since,
                 verified=refs if verified is None else verified,
                 eligible=eligible or any(ref in saved for _, _, aliases in group for ref in aliases),
                 input_origins=origins)


def record_verified_messages(session_id: str, *, refs: Sequence[VerifiedMessageRef], timeline_since: datetime) -> None:
    """Resolve final proof on an already-admitted writer; never guess adjacent text."""
    try:
        session = Session.objects.get(pk=session_id)
        turns = list(LogicalTurn.objects.filter(session_id=session_id))
        saved = _current_saved_refs(session, _saved_ref_groups(session_id), turns=turns)
        invalid = {NativeMessageRef(**ref) for turn in turns if not _same_scope(turn, session)
                   for ref in ([turn.first_ref] if turn.first_ref else []) + turn.final_refs}
        by_floor = {}
        for proof in refs:
            if _same_scope(proof, session) and proof.ref not in invalid:
                by_floor.setdefault(proof.source_floor, []).append(proof.ref)
        for floor, aliases in by_floor.items():
            _record_refs(session, aliases, timeline_since=timeline_since, floor=floor, saved=saved)
    except Exception:
        logger.exception('Verified timeline message capture failed for %s', session_id)


def _saved_ref_groups(session_id):
    return [{NativeMessageRef(**ref) for ref in refs} for refs in TimelineEvent.objects.filter(
        session_id=session_id, kind__in=[TimelineKind.TURN_STARTED, TimelineKind.TURN_ENDED],
    ).values_list('data__message_refs', flat=True) if refs]


def _same_scope(turn, session):
    return (turn.provider == session.provider
            and (turn.storage_incarnation is None or turn.storage_incarnation == session.storage_incarnation)
            and (turn.history_epoch is None or turn.history_epoch == session.history_epoch))


def _current_saved_refs(session, groups, *, turns=None):
    if not groups:
        return set()
    turns = LogicalTurn.objects.filter(session_id=session.id) if turns is None else turns
    invalid = {NativeMessageRef(**ref) for turn in turns if not _same_scope(turn, session)
               for ref in ([turn.first_ref] if turn.first_ref else []) + turn.final_refs}
    # An end event also owns inputs absent from the journal's first/final refs.
    # A stale journal alias invalidates that event's complete reference group.
    return {ref for group in groups if not group.intersection(invalid) for ref in group}


def recover_referenced_messages(session_id: str) -> None:
    """Resolve only saved live-turn aliases, including after journal retention ends."""
    try:
        groups = _saved_ref_groups(session_id)
        pending = {ref for group in groups for ref in group}
        if not pending:
            return
        resolved = {NativeMessageRef(**ref) for refs in TimelineEvent.objects.filter(
            session_id=session_id, kind__in=_MESSAGE_KINDS,
        ).values_list('data__source_refs', flat=True) for ref in refs or []}
        pending -= resolved
        if not pending:
            return
        session = Session.objects.get(pk=session_id)
        turns = list(LogicalTurn.objects.filter(session_id=session_id))
        pending &= _current_saved_refs(session, groups, turns=turns)
        for turn in turns:
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
