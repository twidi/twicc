"""Derive root-only export selections without changing published share state.

The export coordinator commits prepared metadata with confined copies. Captured
maps retain excluded identities, so visibility changes never replace snapshots.
"""

import re
from copy import deepcopy

import orjson

from twicc.core.models import SessionType
from twicc.core.serializers import session_compute_ready
from twicc.share.display import filtered_items_qs

_PUBLICATION_FIELDS = ('artifact_id', 'line_num', 'text_block_index', 'tag_offset', 'src', 'title', 'height')
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_STATUSES = frozenset({'pending', 'ready', 'error', 'not_included'})
_ERROR_CODES = frozenset({'artifact_unavailable', 'export_failed', 'export_too_large'})


class SelectionNotReady(Exception):
    """Retriable root compute failure. Callers must not publish prepared state."""

    def __init__(self, session_ids: tuple[str, ...]):
        self.session_ids = session_ids
        super().__init__('session_not_ready')


def include_inline_artifacts(options: dict) -> bool:
    return options.get('include_inline_artifacts', True) is not False


def _root(share):
    if share.kind != 'session' or not share.session_id:
        return None
    session = share.session
    return session if session and session.type == SessionType.SESSION else None


def check_selection_ready(share) -> None:
    """Check the loaded root. Commit gates must reload it under the write lock."""
    session = _root(share)
    if session is not None and not session_compute_ready(session):
        raise SelectionNotReady((session.id,))


def _publication(record):
    """Accept only the stored Publication contract and strip unknown fields."""
    if not isinstance(record, dict) or any(key not in record for key in _PUBLICATION_FIELDS):
        return None
    artifact_id = record['artifact_id']
    if not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id):
        return None
    for key in ('line_num', 'text_block_index', 'tag_offset', 'height'):
        if type(record[key]) is not int:
            return None
    if record['line_num'] < 1 or min(record['text_block_index'], record['tag_offset']) < 0:
        return None
    if not 160 <= record['height'] <= 900 or not isinstance(record['title'], str) or len(record['title']) > 200:
        return None
    src = record['src']
    if not isinstance(src, str) or any(char in src for char in '\\?#\x00'):
        return None
    parts = src.split('/')
    if len(parts) != 3 or parts[:2] != ['inline-artifacts', artifact_id] or not re.fullmatch(r'.+\.(html|htm)', parts[2]):
        return None
    return {key: record[key] for key in _PUBLICATION_FIELDS}


def _artifact_key(session_id, artifact_id):
    return orjson.dumps([session_id, artifact_id]).decode()


def _visible_lines(share, session):
    options = share.options or {}
    max_line = None
    if options.get('mode') == 'snapshot':
        max_line = options.get('frozen_at_line')
        if max_line is None:
            max_line = session.last_line
    return set(filtered_items_qs(session, max_display_mode=options.get('max_display_mode', 'normal'),
                                 max_line=max_line).values_list('line_num', flat=True))


def select_share_publications(share, *, captured: dict | None = None, recapture: bool = False) -> dict[str, dict]:
    """Select latest eligible root records, or preserve eligible snapshot captures.

    ``captured`` maps artifact keys to Publications, including excluded captures.
    A present identity never falls back or captures a replacement until recapture.
    Omitted captures use the initialized share's server-owned captured map.
    The include option changes executable access, not placement selection.
    """
    session = _root(share)
    if session is None:
        return {}
    check_selection_ready(share)
    if captured is None and (share.inline_artifact_exports or {}).get("initialized"):
        captured = share.inline_artifact_exports.get("captured", {})
    lines = _visible_lines(share, session)
    records = [_publication(record) for record in (session.inline_artifacts or {}).get('publications', [])]
    records = [record for record in records if record is not None]
    latest = {}
    for record in sorted(records, key=lambda p: (p['line_num'], p['text_block_index'], p['tag_offset'])):
        if record['line_num'] in lines:
            latest[_artifact_key(session.id, record['artifact_id'])] = record
    if (share.options or {}).get('mode') != 'snapshot' or captured is None or recapture:
        return latest
    selected = {key: record for key, record in latest.items() if key not in captured}
    for key, record in captured.items():
        record = _publication(record)
        if record is not None and key == _artifact_key(session.id, record['artifact_id']) and record['line_num'] in lines:
            selected[key] = record
    return selected


def prepare_share_selection(share, *, recapture: bool = False) -> dict:
    """Prepare server-owned metadata. This function never writes or copies files.

    ``initialized`` means a ready root supplied a capture, including an empty one.
    Revision changes cover initialization, selection, enabled state and recapture.
    Snapshot exclusions retain copy/code entries as captured tombstones. Routes
    must require selected membership. Live exclusions discard their entries.
    """
    if _root(share) is None:
        return {}
    previous = share.inline_artifact_exports or {}
    captured = previous.get('captured') if previous.get('initialized') else None
    selected = select_share_publications(share, captured=captured, recapture=recapture)
    state = deepcopy(previous)
    snapshot = (share.options or {}).get('mode') == 'snapshot'
    captures = {} if recapture or captured is None else deepcopy(captured)
    if snapshot:
        captures.update(selected)
    else:
        captures = {}
    options = share.options or {}
    enabled = include_inline_artifacts(options)
    selection_options = {
        "mode": options.get("mode", "live"),
        "max_display_mode": options.get("max_display_mode", "normal"),
        "frozen_at_line": options.get("frozen_at_line") if snapshot else None,
    }
    changed = (not previous.get('initialized') or previous.get('selected') != selected
               or previous.get('enabled') != enabled or previous.get('selection_options') != selection_options or recapture)
    state.update(schema=1, initialized=True, captured=captures, selected=selected, enabled=enabled,
                 selection_options=selection_options, revision=max(0, previous.get('revision', 0)) + int(changed))
    entries = previous.get('artifacts', {})
    state['artifacts'] = {
        key: deepcopy(entries[key]) for key in captures
        if snapshot and not recapture and key in entries
    }
    for key, record in selected.items():
        prior_record = previous.get('selected', {}).get(key)
        if snapshot and not recapture and captured is not None:
            prior_record = captured.get(key, prior_record)
        if prior_record == record and key in entries:
            state['artifacts'][key] = deepcopy(entries[key])
        else:
            state['artifacts'][key] = {'status': 'pending', 'code_revision': None, 'copy_id': None}
    return state


def public_inline_manifest(share) -> dict:
    """Return sanitized positions and export status, without capturing metadata.

    Public ``publication`` is [source_session_id, line_num, text_block_index,
    tag_offset]. The adapter converts it to its occurrence object in one place.
    """
    session = _root(share)
    if session is None:
        return {'enabled': False, 'revision': 0, 'artifacts': []}
    state = prepare_share_selection(share)
    enabled = state['enabled']
    artifacts = []
    for key, record in sorted(state['selected'].items()):
        entry = state['artifacts'].get(key, {})
        status = entry.get('status', 'pending')
        if status not in _STATUSES:
            status = 'error'
        if not enabled:
            status = 'not_included'
        revision = entry.get('code_revision')
        if type(revision) is not int or revision < 0 or status != 'ready':
            revision = None
        descriptor = {
            'source_session_id': session.id, 'artifact_id': record['artifact_id'],
            'publication': [session.id, record['line_num'], record['text_block_index'], record['tag_offset']],
            'title': record['title'], 'height': record['height'], 'entry_filename': record['src'].rsplit('/', 1)[1],
            'status': status, 'code_revision': revision,
        }
        if status == 'error':
            code = entry.get('error')
            descriptor['error'] = code if isinstance(code, str) and code in _ERROR_CODES else 'export_failed'
        artifacts.append(descriptor)
    return {'enabled': enabled, 'revision': state['revision'], 'artifacts': artifacts}
