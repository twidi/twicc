"""Best-effort, live-only message snapshots from complete native source groups."""

import hashlib
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from uuid import uuid4

import orjson
from django.utils import timezone

from twicc.agent.completion_types import NativeMessageRef, VerifiedMessageRef
from twicc.agent.semantic_types import DecisionRequest, SourceScope
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, Session, SessionItem, SessionType, TimelineDecision, TimelineEvent, TimelineKind
from twicc.core.services.message_sources import _canonical, _dedupe_claude_records, _groups, _read
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
        if (entry.get('ref') not in wanted or first.line_num <= entry['source_floor']
                or entry['storage_incarnation'] not in (None, session.storage_incarnation)
                or entry['history_epoch'] not in (None, session.history_epoch)):
            continue
        raw = entry['origin']
        # These proofs come from TwiCC's own request capture, not a drop payload.
        origin = SYSTEM_ORIGIN if raw == SYSTEM_ORIGIN._asdict() else origin_from_payload(raw)
        if origin is not None:
            return origin
    return None


def _capture(session, records, *, timeline_since, verified=(), eligible=False, input_origins=(), fulfill=True):
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
    data = {'text': text, 'source_refs': [ref._asdict() for ref in aliases],
            'source_line': first.line_num, 'storage_incarnation': session.storage_incarnation,
            'history_epoch': session.history_epoch}
    dedupe_key = 'msg:' + digest
    if not user and session.provider == 'claude_code':
        from twicc.core.services.timeline_decisions import admit_decision, publish_message_if_ready
        # Published history is immutable and never enters semantic analysis.
        if TimelineEvent.objects.filter(session_id=session.id, dedupe_key=dedupe_key).exists():
            return
        scope = SourceScope(session.provider, session.storage_incarnation, session.history_epoch)
        row = _canonical_decision(session, identity_key=dedupe_key, observed_ref=identity)
        if row is None:
            now = timezone.now()
            payload = {'target': {'text': text}, 'message_refs': data['source_refs'],
                       'dedupe_key': dedupe_key, 'event_data': data,
                       'source_floor': first.line_num - 1, 'source_boundary': max(item.line_num for item, _, _ in records),
                       'previous_messages': capture_message_context(session, before=first.line_num,
                            occurred_at=first.timestamp, input_origins=input_origins, verified=verified)}
            decision_id = admit_decision(DecisionRequest(str(uuid4()), 'message', session.id, scope,
                payload, now + timedelta(seconds=45), 'provider'), identity_key=dedupe_key,
                occurred_at=first.timestamp, not_before=now + timedelta(seconds=1))
        else:
            decision_id = str(row.pk)
        publish_message_if_ready(decision_id=decision_id)
    else:
        record_event_sync(kind, session_id=session.id, project_id=session.project_id, provider=session.provider,
                          occurred_at=first.timestamp, origin=origin or RequestOrigin('external' if user else 'agent'),
                          dedupe_key=dedupe_key, data=data)
    if fulfill and not user:
        _fulfill_pending(session.id)


def _canonical_decision(session, *, identity_key, observed_ref):
    """Select proven current scope before binding an older unknown-scope row."""
    from twicc.core.services.timeline_decisions import bind_decision_source
    scope = SourceScope(session.provider, session.storage_incarnation, session.history_epoch)
    rows = list(TimelineDecision.objects.filter(session_id=session.id, provider=session.provider,
        kind='message', identity_key=identity_key))
    compatible = [row for row in rows if _same_scope(row, session)]
    known = next((row for row in compatible if row.storage_incarnation == scope.storage_incarnation
                  and row.history_epoch == scope.history_epoch), None)
    unknown = [row for row in compatible if row.pk != getattr(known, 'pk', None)]
    if known is not None:
        for row in unknown:
            if row.payload.get('canonical_decision_id') == str(known.pk):
                continue
            if row.forced:
                _transfer_forced_message(session, duplicate=row, canonical=known)
            elif row.force_pending or row.published_event_id:
                raise ValueError('Decision source scope binding collision')
        known.refresh_from_db()
        return known
    for row in unknown:
        if bind_decision_source(decision_id=str(row.pk), scope=scope, observed_ref=observed_ref):
            row.storage_incarnation, row.history_epoch = scope.storage_incarnation, scope.history_epoch
            return row
    return None


def capture_message_context(session, *, before, occurred_at, input_origins=(), verified=()):
    """Read source pages only before the captured candidate, then freeze the window."""
    from twicc.cli._drop_request.sender_header import SHELL_NOTICE_HEADER
    helpers = get_provider_helpers(session.provider)
    entries = []
    cursor = before
    seen = set()
    # Pages bound decompression and never include an item after the cutoff.
    while True:
        lines = list(SessionItem.objects.filter(session_id=session.id, line_num__lt=cursor)
                     .order_by('-line_num').values_list('line_num', flat=True)[:128])
        if not lines:
            break
        records = list(_read(session, SessionItem.objects.filter(session_id=session.id, line_num__in=lines)))
        for item, parsed, refs in reversed(records):
            if parsed.get('isSidechain') or parsed.get('isCompactSummary'):
                continue
            identity = _canonical(session, (item, parsed, refs))
            if identity in seen:
                continue
            if item.kind == ItemKind.ASSISTANT_MESSAGE:
                group = (_dedupe_claude_records(_read(session, SessionItem.objects.filter(session_id=session.id,
                    line_num__lt=before, message_id=item.message_id)))
                    if session.provider == 'claude_code' else [(item, parsed, refs)])
                data = _eligible_group(session, group, verified)
                if data is None:
                    continue
                text = data['text']
                role = 'assistant'
                line = group[0][0].line_num if group else item.line_num
            elif item.kind == ItemKind.USER_MESSAGE:
                text = helpers.extract_timeline_text_from_parsed(parsed)
                origin = _input_origin(session, item, refs, input_origins) or origin_from_payload(parsed.get('_origin'))
                notice = text and text.lstrip().startswith(SHELL_NOTICE_HEADER)
                if origin is not None and origin.actor not in ('user', 'system'):
                    continue
                role = 'system_notice' if notice or origin is not None and origin.actor == 'system' else 'user'
                line = item.line_num
            else:
                continue
            if text and text.strip():
                seen.add(identity)
                entries.append({'role': role, 'text': text, 'line': line,
                                '_refs': [ref._asdict() for ref in refs]})
        entries.sort(key=lambda entry: entry['line'])
        if len(entries) >= 4 and any(entry['role'] == 'user' for entry in entries):
            break
        cursor = min(lines)
    # Merge only actual ordinary inputs captured before this native candidate.
    snapshots = [entry['input_snapshot'] for entry in input_origins if entry.get('input_snapshot')]
    for snapshot in snapshots:
        if (snapshot['origin']['actor'] != 'user' or snapshot['captured_at'] > occurred_at.isoformat()
                or snapshot['source_floor'] >= before
                or snapshot['storage_incarnation'] not in (None, session.storage_incarnation)
                or snapshot['history_epoch'] not in (None, session.history_epoch)):
            continue
        native_ref = snapshot.get('ref')
        if native_ref and any(native_ref in entry['_refs'] for entry in entries):
            continue
        entries.append({'role': 'user', 'text': snapshot['text'],
                        'line': snapshot['source_floor'] + 0.5, '_refs': []})
    entries.sort(key=lambda entry: entry['line'])
    selected = entries[-4:]
    latest_user = next((entry for entry in reversed(entries) if entry['role'] == 'user'), None)
    if latest_user is not None and latest_user not in selected:
        selected.insert(0, latest_user)
    return [{'role': entry['role'], 'text': entry['text'],
             **({'line': entry['line']} if isinstance(entry['line'], int) else {})} for entry in selected]


def capture_ending_context(session, *, refs, floor, occurred_at, input_origins=(), tool_refs=()):
    """Capture indexed root evidence only through an exact frozen terminal."""
    if not refs or refs[-1].kind not in ('claude_uuid', 'codex_item'):
        return {}
    terminal = refs[-1]
    groups = _resolve_groups(session, [terminal], floor=floor)
    if not any(_eligible_group(session, group, refs) for group in groups):
        return {}
    lines = [item.line_num for group in groups for item, _, aliases in group if terminal in aliases]
    if not lines:
        return {}
    boundary = min(lines)
    tool_lines = set()
    for ref in tool_refs:
        matching = [item.line_num for group in _resolve_groups(session, [ref], floor=floor)
                    for item, _, aliases in group if ref in aliases]
        if not matching:
            return {}
        tool_lines.add(min(matching))
    previous = capture_message_context(session, before=boundary + 1, occurred_at=occurred_at,
        input_origins=input_origins, verified=refs)
    source_boundary = max(boundary, *tool_lines) if tool_lines else boundary
    return {'previous_messages': previous,
            'tool_evidence': _capture_tool_observations(session, source_boundary,
                conversation_boundary=boundary, captured_lines=tool_lines),
            'source_boundary': source_boundary, 'source_ceiling': boundary}


def _observation_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(part.get('text', '') for part in value
                         if isinstance(part, dict) and isinstance(part.get('text'), str))
    return orjson.dumps(value).decode() if value is not None else ''


def _capture_tool_observations(session, boundary, *, conversation_boundary, captured_lines):
    """Read existing completed tools, excluding child conversations and later lines."""
    from twicc.agent.semantic_types import ToolObservation
    from twicc.core.content_storage.readers import iter_current_items
    from twicc.providers.codex.canonical import completed_item

    helpers = get_provider_helpers(session.provider)
    commands = {}
    observations = []
    query = SessionItem.objects.filter(session_id=session.id, line_num__lte=boundary).order_by('line_num')
    for row in iter_current_items(query, 128, 8 * 1024 * 1024):
        parsed = helpers.parse_item_content(row)
        if not parsed or parsed.get('isSidechain') or parsed.get('parent_tool_use_id'):
            continue
        if session.provider == 'claude_code':
            blocks = (parsed.get('message') or {}).get('content')
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    arguments = block.get('input') or {}
                    command = arguments.get('command') if isinstance(arguments, dict) else None
                    commands[block.get('id')] = command if isinstance(command, str) else (
                        f"{block.get('name', '')} {orjson.dumps(arguments).decode()}")
                elif block.get('type') == 'tool_result' and block.get('tool_use_id') in commands:
                    tool_id = block['tool_use_id']
                    observations.append(ToolObservation(tool_id, row.line_num, commands[tool_id],
                        _observation_text(block.get('content')))._asdict())
            continue
        payload = parsed.get('payload') or {}
        if payload.get('thread_id') not in (None, session.id):
            continue
        kind = payload.get('type')
        if parsed.get('type') == 'response_item':
            tool_id = payload.get('call_id')
            if kind in ('function_call', 'custom_tool_call'):
                arguments = payload.get('arguments', payload.get('input', ''))
                commands[tool_id] = f"{payload.get('name', '')} {_observation_text(arguments)}"
            elif kind in ('function_call_output', 'custom_tool_call_output') and tool_id in commands:
                observations.append(ToolObservation(tool_id, row.line_num, commands[tool_id],
                    _observation_text(payload.get('output')))._asdict())
        item = completed_item(parsed)
        if item and item.get('type') == 'CommandExecution' and item.get('status') not in ('in_progress', 'inProgress'):
            command = item.get('command')
            if isinstance(command, list):
                command = orjson.dumps(command).decode()
            if isinstance(command, str):
                output = item.get('aggregated_output', item.get('aggregatedOutput'))
                if output is None:
                    output = f"Process exited with code {item['exit_code']}" if item.get('exit_code') is not None else ''
                observations.append(ToolObservation(item.get('id'), row.line_num, command,
                    _observation_text(output))._asdict())
        elif item and item.get('type') in ('McpToolCall', 'DynamicToolCall', 'FileChange'):
            if item.get('status') in ('in_progress', 'inProgress'):
                continue
            if item['type'] == 'FileChange':
                command = f"apply_patch {orjson.dumps(item.get('changes', {})).decode()}"
                output = '\n'.join(text for text in (item.get('stdout'), item.get('stderr')) if isinstance(text, str))
            else:
                command = f"{item.get('tool', item.get('name', ''))} {_observation_text(item.get('arguments', {}))}"
                output = _observation_text(item.get('result', item.get('error')))
            observations.append(ToolObservation(item.get('id'), row.line_num, command, output)._asdict())
    return [entry for entry in observations
            if entry['line'] <= conversation_boundary or entry['line'] in captured_lines]


def _fulfill_pending(session_id):
    from twicc.core.services.timeline_decisions import fulfill_final_message_obligation
    for decision_id in TimelineDecision.objects.filter(session_id=session_id, kind='ending',
            status='accepted', force_pending=True).values_list('id', flat=True):
        fulfill_final_message_obligation(ending_id=str(decision_id))


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
            _fulfill_pending(session_id)
            return
        session = Session.objects.get(pk=session_id)
        rows = SessionItem.objects.filter(session_id=session_id, line_num__in=new_line_nums)
        groups = list(_groups(session, _read(session, rows)))
        origins = [*(get_input_origins() if get_input_origins is not None else []), *_saved_input_origins(session)]
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
        _fulfill_pending(session_id)
    except Exception:
        logger.exception('Timeline message capture failed for %s', session_id)


def _record_refs(session, refs, *, timeline_since, floor=0, eligible=False, verified=None, saved=(), input_origins=()):
    wanted = set(refs)
    if not wanted:
        return
    # IDs live in current provider JSON, which can be compressed. Read bounded
    # pages and match exact aliases; the source floor is only a read boundary.
    query = SessionItem.objects.filter(session_id=session.id, line_num__gt=floor)
    if all(ref.kind == 'claude_message' for ref in refs):
        query = query.filter(message_id__in=[ref.native_id for ref in refs])
    records = (record for record in _read(session, query) if wanted.intersection(record[2]))
    origins = [*input_origins, *_saved_input_origins(session)]
    for group in _groups(session, records):
        if group[0][0].line_num <= floor:
            continue
        _capture(session, group, timeline_since=timeline_since,
                 verified=refs if verified is None else verified,
                 eligible=eligible or any(ref in saved for _, _, aliases in group for ref in aliases),
                 input_origins=origins)


def record_verified_messages(session_id: str, *, refs: Sequence[VerifiedMessageRef], timeline_since: datetime,
                             input_origins: Sequence[dict] = ()) -> None:
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
            _record_refs(session, aliases, timeline_since=timeline_since, floor=floor, saved=saved, input_origins=input_origins)
        _fulfill_pending(session_id)
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


def recover_referenced_messages(session_id: str, *, input_origins: Sequence[dict] = ()) -> None:
    """Resolve only saved live-turn aliases, including after journal retention ends."""
    try:
        _fulfill_pending(session_id)
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
                         verified=[NativeMessageRef(**ref) for ref in turn.final_refs], input_origins=input_origins)
        if pending:
            _record_refs(session, pending, timeline_since=None, eligible=True, input_origins=input_origins)
    except Exception:
        logger.exception('Referenced timeline message recovery failed for %s', session_id)


def _eligible_group(session, group, verified=()):
    """Return complete assistant source facts using the existing terminal rules."""
    if not group or group[0][0].kind != ItemKind.ASSISTANT_MESSAGE:
        return None
    helpers = get_provider_helpers(session.provider)
    verdicts = [helpers.is_final_assistant_message(parsed) for _, parsed, _ in group]
    exact = any(ref in verified and ref.kind != 'claude_message' for _, _, aliases in group for ref in aliases)
    if False in verdicts or True not in verdicts and not exact:
        return None
    text = '\n'.join(part for item, parsed, _ in group if item.kind == ItemKind.ASSISTANT_MESSAGE
                     and (part := helpers.extract_timeline_text_from_parsed(parsed)))
    if not text.strip():
        return None
    return {'text': text, 'source_refs': [ref._asdict() for ref in dict.fromkeys(
        ref for _, _, aliases in group for ref in aliases)], 'source_line': group[0][0].line_num,
        'storage_incarnation': session.storage_incarnation, 'history_epoch': session.history_epoch}


def _resolve_groups(session, refs, *, floor=0, ceiling=None):
    wanted = set(refs)
    query = SessionItem.objects.filter(session_id=session.id, line_num__gt=floor)
    if ceiling is not None:
        query = query.filter(line_num__lte=ceiling)
    if all(ref.kind == 'claude_message' for ref in refs):
        query = query.filter(message_id__in=[ref.native_id for ref in refs])
    records = [record for record in _read(session, query) if wanted.intersection(record[2])]
    bounded = []
    for group in _groups(session, records):
        # An exact frozen terminal also bounds hydration of its split group.
        # A later block sharing that group ID cannot enrich an old ending.
        terminals = [item.line_num for item, _, aliases in group
                     if any(ref in wanted and ref.kind == 'claude_uuid' for ref in aliases)]
        upper = min(ceiling, max(terminals)) if ceiling is not None and terminals else (
            max(terminals) if terminals else ceiling)
        if upper is not None:
            group = [record for record in group if record[0].line_num <= upper]
        if group and group[0][0].line_num > floor:
            bounded.append(group)
    return bounded


def _matching_event(session, *, dedupe_key, data, occurred_at):
    event = TimelineEvent.objects.filter(session_id=session.id, dedupe_key=dedupe_key).first()
    if (event is not None and event.kind == TimelineKind.ASSISTANT_MESSAGE_SENT
            and event.provider == session.provider and event.occurred_at == occurred_at
            and event.data == data and event.actor == 'agent'):
        return event
    return None


def _publish_decided_message(row):
    session = Session.objects.get(pk=row.session_id)
    data = {**row.payload['event_data'], 'storage_incarnation': session.storage_incarnation,
            'history_epoch': session.history_epoch}
    refs = [NativeMessageRef(**ref) for ref in row.payload['message_refs']]
    groups = _resolve_groups(session, refs, floor=row.payload.get('source_floor', 0),
                             ceiling=row.payload.get('source_boundary'))
    if not any(_eligible_group(session, group, refs) == data and group[0][0].timestamp == row.occurred_at
               for group in groups):
        return None
    event = record_event_sync(TimelineKind.ASSISTANT_MESSAGE_SENT, session_id=session.id,
        project_id=session.project_id, provider=session.provider, actor='agent',
        occurred_at=row.occurred_at, dedupe_key=row.payload['dedupe_key'], data=data)
    if event is None:
        event = _matching_event(session, dedupe_key=row.payload['dedupe_key'], data=data, occurred_at=row.occurred_at)
    if event is None:
        return None
    TimelineDecision.objects.filter(pk=row.pk).update(published_event_id=event.pk, applied_at=timezone.now())
    return event.pk


def _fulfill_final_message(ending):
    from twicc.core.services.timeline_decisions import force_message, publish_message_if_ready
    session = Session.objects.get(pk=ending.session_id)
    refs = [NativeMessageRef(**ref) for ref in ending.payload['snapshot']['final_refs']]
    if not refs:
        return True
    groups = _resolve_groups(session, refs, floor=ending.payload['source_floor'],
                             ceiling=ending.payload.get('source_ceiling'))
    helpers = get_provider_helpers(session.provider)
    selected = None
    # Frozen final-reference order owns selection, even when indexing arrives late.
    # Resolve backwards until eligibility is proven; a missing ref still matters.
    for final_ref in reversed(refs):
        group = next((group for group in groups
                      if any(final_ref in aliases for _, _, aliases in group)), None)
        if group is None:
            return False
        if (all(helpers.is_final_assistant_message(raw) is None for _, raw, _ in group)
                and not any(ref in refs and ref.kind != 'claude_message'
                            for _, _, aliases in group for ref in aliases)):
            return False
        data = _eligible_group(session, group, refs)
        if data is not None:
            selected = group, data
            break
    if selected is None:
        return True
    group, data = selected
    identity = _canonical(session, group[0])
    digest = hashlib.sha256(orjson.dumps([session.provider, TimelineKind.ASSISTANT_MESSAGE_SENT,
                                        identity.kind, identity.native_id])).hexdigest()
    key = 'msg:' + digest
    if session.provider == 'codex':
        _capture(session, group, timeline_since=None, eligible=True, verified=refs, fulfill=False)
        return _matching_event(session, dedupe_key=key, data=data, occurred_at=group[0][0].timestamp) is not None
    row = _canonical_decision(session, identity_key=key, observed_ref=identity)
    if row is None:
        # A previously published message needs no new semantic candidate.
        if TimelineEvent.objects.filter(session_id=session.id, dedupe_key=key).exists():
            return _matching_event(session, dedupe_key=key, data=data, occurred_at=group[0][0].timestamp) is not None
        _capture(session, group, timeline_since=None, eligible=True, verified=refs, fulfill=False)
        row = _canonical_decision(session, identity_key=key, observed_ref=identity)
    if row is None or row.occurred_at != group[0][0].timestamp or {
            **row.payload['event_data'], 'storage_incarnation': session.storage_incarnation,
            'history_epoch': session.history_epoch} != data:
        return False
    force_message(decision_id=str(row.pk), reason='final_message')
    return publish_message_if_ready(decision_id=str(row.pk)) is not None


def _ensure_decision_source(row):
    """Prove a captured alias before binding an unknown durable source scope."""
    from twicc.core.services.timeline_decisions import bind_decision_source, source_is_current
    scope = SourceScope(row.provider, row.storage_incarnation, row.history_epoch)
    if source_is_current(session_id=row.session_id, scope=scope):
        return True
    session = Session.objects.filter(pk=row.session_id, provider=row.provider).first()
    if session is None or not _same_scope(row, session):
        return False
    refs = [NativeMessageRef(**ref) for ref in row.payload.get('message_refs', [])]
    groups = _resolve_groups(session, refs, floor=row.payload.get('source_floor', 0),
                             ceiling=row.payload.get('source_boundary')) if refs else []
    observed = next((ref for group in groups for _, _, aliases in group for ref in aliases if ref in refs), None)
    if observed is None:
        return False
    current = SourceScope(session.provider, session.storage_incarnation, session.history_epoch)
    if not bind_decision_source(decision_id=str(row.pk), scope=current, observed_ref=observed):
        return False
    row.storage_incarnation, row.history_epoch = current.storage_incarnation, current.history_epoch
    return True


def _transfer_forced_message(session, *, duplicate, canonical):
    """Transfer only one proven identical message's final-forcing obligation."""
    from twicc.core.services.timeline_decisions import _redirect_forced_message
    expected = {**canonical.payload.get('event_data', {}), 'storage_incarnation': session.storage_incarnation,
                'history_epoch': session.history_epoch}
    original = {**duplicate.payload.get('event_data', {}), 'storage_incarnation': session.storage_incarnation,
                'history_epoch': session.history_epoch}
    refs = [NativeMessageRef(**ref) for ref in canonical.payload.get('message_refs', [])]
    if (canonical.status in ('canceled', 'invalidated') or duplicate.published_event_id is not None
            or canonical.occurred_at != duplicate.occurred_at or original != expected
            or duplicate.payload.get('message_refs') != canonical.payload.get('message_refs')
            or duplicate.payload.get('target') != canonical.payload.get('target')):
        raise ValueError('Decision source scope binding collision')
    groups = _resolve_groups(session, refs, floor=canonical.payload.get('source_floor', 0),
                             ceiling=canonical.payload.get('source_boundary')) if refs else []
    if not any(_eligible_group(session, group, refs) == expected
               and group[0][0].timestamp == canonical.occurred_at for group in groups):
        raise ValueError('Decision source scope binding collision')
    _redirect_forced_message(duplicate=duplicate, canonical=canonical)


def _message_application_candidate(row):
    """Follow a committed redirect or select current canonical source proof."""
    redirect = row.payload.get('canonical_decision_id')
    if redirect:
        row = TimelineDecision.objects.filter(pk=redirect, session_id=row.session_id, provider=row.provider,
            kind='message', identity_key=row.identity_key).first()
        if row is None:
            return None
    session = Session.objects.filter(pk=row.session_id, provider=row.provider).first()
    if session is None or not _same_scope(row, session):
        return None
    refs = [NativeMessageRef(**ref) for ref in row.payload.get('message_refs', [])]
    groups = _resolve_groups(session, refs, floor=row.payload.get('source_floor', 0),
                             ceiling=row.payload.get('source_boundary')) if refs else []
    expected = {**row.payload.get('event_data', {}), 'storage_incarnation': session.storage_incarnation,
                'history_epoch': session.history_epoch}
    proven = next((group for group in groups if _eligible_group(session, group, refs) == expected
                   and group[0][0].timestamp == row.occurred_at), None)
    if proven is None:
        return None
    return _canonical_decision(session, identity_key=row.identity_key, observed_ref=_canonical(session, proven[0]))
