"""Native message grouping shared by timeline capture and summary resolution."""

from twicc.agent.completion_types import NativeMessageRef
from twicc.core.content_storage.readers import iter_current_items
from twicc.core.models import SessionItem
from twicc.providers.helpers import get_provider_helpers


def _refs(helpers, parsed):
    refs = list(helpers.completion_record_refs(parsed))
    if helpers.provider == 'codex':
        from twicc.providers.codex.canonical import completed_item
        item = completed_item(parsed)
        if item and item.get('type') == 'UserMessage' and isinstance(item.get('clientId'), str) and item['clientId']:
            refs.append(NativeMessageRef('codex_client', item['clientId']))
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


def _groups(session, records):
    groups = {}
    for record in records:
        identity = _canonical(session, record)
        if identity is not None:
            groups.setdefault((identity.kind, identity.native_id), []).append(record)
    hydrated = []
    for (kind, identity), group in groups.items():
        if kind != 'claude_message':
            hydrated.append(group[:1])
            continue
        seen = set()
        complete = []
        for record in _read(session, SessionItem.objects.filter(session_id=session.id, message_id=identity)):
            uuid = next((ref.native_id for ref in record[2] if ref.kind == 'claude_uuid'), None)
            if uuid is None or uuid not in seen:
                complete.append(record)
            if uuid is not None:
                seen.add(uuid)
        if complete:
            hydrated.append(complete)
    yield from sorted(hydrated, key=lambda group: group[0][0].line_num)
