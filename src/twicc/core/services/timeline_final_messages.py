"""Batched reads of the final-message proof saved on official endings.

Project IDs are already expanded by the caller. Reads use at most three
primary-key batches: endings, current sessions, and exact linked messages.
"""

from collections.abc import Mapping, Sequence

from twicc.core.models import Session, TimelineEvent, TimelineKind
from twicc.core.services.timeline_feed import (
    MAX_EVENT_ID, FeedError, _integer, _items, format_occurred_at, serialize_event, visible_sessions_q,
)
from twicc.core.ui_messages import ui_message_text
from twicc.pending_titles import get_pending_title


def validate_ending_ids(ending_ids: Sequence[int]) -> tuple[int, ...]:
    """Reject invalid IDs before any database work; keep distinct request order."""
    if any(type(value) is not int or not 1 <= value <= MAX_EVENT_ID for value in ending_ids):
        raise FeedError(ui_message_text('ending_ids must contain positive IDs up to 2**63 - 1.',
                                      'backend.timeline.ending_ids_range'))
    ids = tuple(dict.fromkeys(ending_ids))
    if len(ids) > 100:
        raise FeedError(ui_message_text('ending_ids must contain at most 100 distinct IDs.',
                                      'backend.timeline.ending_ids_limit'))
    return ids


def parse_request(params: Mapping[str, str]) -> tuple[tuple[int, ...], tuple[str, ...], bool]:
    """Parse the small read endpoint using the feed's existing error conventions."""
    ids = validate_ending_ids([_integer('ending_ids', value) for value in _items(params, 'ending_ids')])
    include_muted = params.get('include_muted', '0')
    if include_muted not in ('0', '1'):
        raise FeedError(ui_message_text('include_muted must be 0 or 1.', 'backend.timeline.include_muted'))
    return ids, _items(params, 'projects'), include_muted == '1'


def _metadata(ending, session):
    final = ending.data.get('final_message')
    if not isinstance(final, dict) or final.get('state') not in ('ready', 'pending', 'none'):
        return None
    state = final['state']
    keys = {'state', 'storage_incarnation', 'history_epoch'} | ({'event_id'} if state == 'ready' else set())
    if set(final) != keys:
        return None
    for field in ('storage_incarnation', 'history_epoch'):
        saved = final[field]
        if saved is None:
            if state != 'pending':
                return None
        elif ((field == 'history_epoch' and (type(saved) is not int or saved < 0))
              or (field == 'storage_incarnation' and (not isinstance(saved, str) or not saved))
              or saved != getattr(session, field)):
            return None
    if state == 'ready' and (type(final['event_id']) is not int or not 1 <= final['event_id'] <= MAX_EVENT_ID):
        return None
    return final


def resolve_final_messages(ending_ids: Sequence[int], *, project_ids: Sequence[str] = (),
                           include_muted: bool = False) -> dict:
    """Resolve only exact saved event IDs, with current visibility and source scope."""
    ids = validate_ending_ids(ending_ids)
    if not ids:
        return {'items': [], 'sessions': {}}
    rows = TimelineEvent.objects.filter(visible_sessions_q(include_muted=include_muted),
        pk__in=ids, kind=TimelineKind.TURN_ENDED, actor='agent')
    if project_ids:
        rows = rows.filter(project_id__in=project_ids)
    endings = {row.pk: row for row in rows}
    current = {row.pk: row for row in Session.objects.filter(
        pk__in={row.session_id for row in endings.values() if row.session_id}).only(
            'id', 'title', 'project_id', 'provider', 'hidden', 'archived', 'mute_on_user_turn', 'pinned',
            'storage_incarnation', 'history_epoch')}
    candidates = {}
    for ending in endings.values():
        session = current.get(ending.session_id)
        if (session is None or session.hidden or (session.mute_on_user_turn and not include_muted)
                or (session.provider, session.project_id) != (ending.provider, ending.project_id)
                or (project_ids and session.project_id not in project_ids)):
            continue
        final = _metadata(ending, session)
        if final is not None:
            candidates[ending.pk] = final
    message_ids = {final['event_id'] for final in candidates.values() if final['state'] == 'ready'}
    messages = {row.pk: row for row in TimelineEvent.objects.filter(pk__in=message_ids)} if message_ids else {}
    items, sessions = [], {}
    for ending_id in ids:
        item = {'ending_id': ending_id, 'state': 'unavailable', 'occurred_at': None, 'message': None}
        final = candidates.get(ending_id)
        if final is not None:
            ending = endings[ending_id]
            message = messages.get(final.get('event_id'))
            if final['state'] == 'ready':
                if (message is None or message.kind != TimelineKind.ASSISTANT_MESSAGE_SENT or message.actor != 'agent'
                        or (message.provider, message.session_id, message.project_id)
                        != (ending.provider, ending.session_id, ending.project_id)
                        or any(message.data.get(field) != final[field]
                               for field in ('storage_incarnation', 'history_epoch'))
                        or not isinstance(message.data.get('text'), str) or not message.data['text'].strip()):
                    items.append(item)
                    continue
                item['message'] = serialize_event(message, include_ui_messages=True)
            item.update(state=final['state'], occurred_at=format_occurred_at(ending.occurred_at))
            session = current[ending.session_id]
            sessions[session.pk] = {
                'title': get_pending_title(session.pk) or session.title or None,
                'project_id': session.project_id, 'provider': session.provider, 'hidden': bool(session.hidden),
                'archived': bool(session.archived), 'mute_on_user_turn': bool(session.mute_on_user_turn),
                'pinned': session.pinned,
            }
        items.append(item)
    return {'items': items, 'sessions': sessions}
