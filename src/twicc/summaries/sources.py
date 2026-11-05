"""Resolve immutable native source snapshots without starting generation."""

import hashlib
from typing import NamedTuple

import orjson
from markdown_it import MarkdownIt

from twicc.agent.completion_types import NativeMessageRef
from twicc.context_injection import _CONTEXT_BLOCK_RE, _INSTRUCTION_BLOCK_RE
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, Session, SessionItem, TimelineEvent, TimelineKind
from twicc.core.services.message_sources import _canonical, _groups, _read
from twicc.inline_artifacts.publications import (
    _code_ranges, _colon_block, _comment_block, _comment_inline, _line_offsets, parse_inline_artifact_blocks,
)
from twicc.providers.helpers import get_provider_helpers


def _content_colon_block(state, start_line, end_line, silent):
    """Reuse TwiCC's block boundaries and retain the visible Markdown label."""
    index = len(state.tokens)
    if not _colon_block(state, start_line, end_line, silent):
        return False
    if not silent:
        label = state.src[state.bMarks[start_line] + state.tShift[start_line]:state.eMarks[start_line]]
        token = state.tokens[index]
        token.children = []
        state.md.inline.parse(label.lstrip(':').strip(), state.md, state.env, token.children)
    return True


_MARKDOWN = MarkdownIt('default', {'html': False})
_MARKDOWN.block.ruler.before('fence', 'colon_block', _content_colon_block,
                             {'alt': ['paragraph', 'reference', 'blockquote', 'list']})
_MARKDOWN.block.ruler.before('paragraph', 'html_comment', _comment_block)
_MARKDOWN.inline.ruler.before('html_inline', 'html_comment', _comment_inline)
MIN_CONTENT_CHARS = 500


class SummarySource(NamedTuple):
    """Only immutable values cross the synchronous source-reading boundary."""

    source_key: str | None
    source_hash: str
    canonical_text: str
    input_text: str
    session_id: str | None
    first_line: int | None
    text_lines: tuple[int, ...]
    content_chars: int
    eligible: bool
    unavailable_reason: str | None
    provider: str | None
    purpose: str | None


def _input_text(text: str) -> str:
    for block in reversed(parse_inline_artifact_blocks(text)):
        text = text[:block.start] + text[block.end:]
    # Match controls against a same-length view that hides literal code tags.
    # Keep non-whitespace masks so code cannot become a leading context prefix.
    parts, cursor = [], 0
    for start, end in _code_ranges(text, _MARKDOWN.parse(text), _line_offsets(text)):
        parts.extend((text[cursor:start], '\x00' * (end - start)))
        cursor = end
    parts.append(text[cursor:])
    masked = ''.join(parts)
    for pattern in (_CONTEXT_BLOCK_RE, _INSTRUCTION_BLOCK_RE):
        for match in reversed(list(pattern.finditer(masked))):
            start, end = match.span()
            text = text[:start] + text[end:]
            masked = masked[:start] + masked[end:]
    return text


def _token_text(tokens):
    for token in tokens:
        if token.type in {'text', 'code_inline', 'code_block', 'fence'}:
            yield token.content
        elif token.type in {'softbreak', 'hardbreak'} or token.block:
            yield ' '
        if token.children:
            yield from _token_text(token.children)


def content_text(text: str) -> str:
    """Count visible Markdown text, code and labels, excluding publications."""
    return ' '.join(''.join(_token_text(_MARKDOWN.parse(_input_text(text)))).split())


def content_length(text: str) -> int:
    """Return normalized Unicode characters, including single internal spaces."""
    return len(content_text(text))


def normalize_summary_model(value: object) -> str:
    """Normalize a stored preference at use time without rewriting settings."""
    return value if isinstance(value, str) and value in {'auto', 'haiku', 'luna', 'openrouter'} else 'auto'


def _hash(value) -> str:
    return hashlib.sha256(orjson.dumps(value)).hexdigest()


def _message_key(provider, session_id, incarnation, epoch, identity) -> str:
    return 'message:' + _hash([provider, session_id, incarnation, epoch, identity.kind, identity.native_id])


def _assembled(session, group):
    helpers = get_provider_helpers(session.provider)
    rows = [(item.line_num, helpers.extract_timeline_text_from_parsed(parsed))
            for item, parsed, _ in group if item.kind == ItemKind.ASSISTANT_MESSAGE]
    # Match timeline assembly exactly, including whitespace-only nonempty parts.
    text = '\n'.join(part for _, part in rows if part)
    lines = tuple(line for line, part in rows if part and part.strip())
    return text, lines


def _same_scope(value, session):
    return (value.provider == session.provider
            and value.storage_incarnation in (None, session.storage_incarnation)
            and value.history_epoch in (None, session.history_epoch))


def _complete(session, group) -> bool:
    if session.provider == 'codex':
        # Native item_completed is independent of final_answer/commentary phase.
        return True
    if any(parsed.get('isAbortedMidStream') for _, parsed, _ in group):
        return False
    # A tool-use stop closes a message even though its turn continues.
    helpers = get_provider_helpers(session.provider)
    if any(helpers.is_final_assistant_message(parsed) is not None for _, parsed, _ in group):
        return True
    aliases = {ref for _, _, refs in group for ref in refs}
    first, last = group[0][0].line_num, group[-1][0].line_num
    active_floors = []
    for turn in LogicalTurn.objects.filter(session_id=session.id, provider=session.provider):
        if not _same_scope(turn, session) or first <= turn.source_floor:
            continue
        if any(NativeMessageRef(**ref) in aliases and ref['kind'] != 'claude_message' for ref in turn.final_refs):
            return True
        if turn.closed_at is None:
            active_floors.append(turn.source_floor)
    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(session.provider)
    agent = manager._agents.get(session.id)
    if agent is not None:
        proofs = manager.timeline_verified_message_refs(session.id) or ()
        if any(_same_scope(proof, session) and first > proof.source_floor
               and proof.ref in aliases and proof.ref.kind != 'claude_message' for proof in proofs):
            return True
        if (agent.state == 'assistant_turn'
                and agent._completion_storage_incarnation in (None, session.storage_incarnation)
                and agent._completion_history_epoch in (None, session.history_epoch)):
            active_floors.append(agent._completion_source_floor)
    if not any(first > floor for floor in active_floors):
        return True
    # A later native assistant message proves this older source is no longer growing.
    identity = _canonical(session, group[0])
    later = SessionItem.objects.filter(session_id=session.id, line_num__gt=last, kind=ItemKind.ASSISTANT_MESSAGE)
    for record in _read(session, later):
        later_identity = _canonical(session, record)
        if later_identity is not None and later_identity != identity:
            return True
    return False


def _resolved(*, key, text, session_id=None, lines=(), provider=None, purpose=None, complete=True):
    input_text = _input_text(text)
    count = content_length(text)
    empty = not input_text.strip() if purpose is not None else not count
    reason = 'empty' if empty else ('incomplete' if not complete else (
        'too_short' if purpose is None and count < MIN_CONTENT_CHARS else None))
    return SummarySource(key, hashlib.sha256(text.encode()).hexdigest(), text, input_text,
                         session_id, lines[0] if lines else None, lines, count, reason is None, reason,
                         provider, purpose)


def _chat(session, line):
    group = next(_groups(session, _read(session, SessionItem.objects.filter(session_id=session.id, line_num=line))), None)
    if group is None:
        raise ValueError('Source is not a native assistant message')
    text, lines = _assembled(session, group)
    if not any(item.kind == ItemKind.ASSISTANT_MESSAGE for item, _, _ in group):
        raise ValueError('Source is not an assistant message')
    identity = _canonical(session, group[0])
    return _resolved(key=_message_key(session.provider, session.id, session.storage_incarnation,
                                    session.history_epoch, identity),
                     text=text, session_id=session.id, lines=lines, provider=session.provider,
                     complete=_complete(session, group))


def _snapshot(event):
    if event.kind != TimelineKind.ASSISTANT_MESSAGE_SENT or not isinstance(event.data.get('text'), str):
        raise ValueError('Source is not an assistant message snapshot')
    data = event.data
    text = data['text']
    refs = tuple(NativeMessageRef(**ref) for ref in data.get('source_refs', ()))
    canonical_kind = 'claude_message' if event.provider == 'claude_code' else 'codex_item'
    identity = next((ref for ref in refs if ref.kind == canonical_kind), None)
    key = 'snapshot:' + _hash([event.id, event.provider, event.session_id])
    scoped = data.get('history_epoch') is not None
    if identity is not None and scoped:
        key = _message_key(event.provider, event.session_id, data.get('storage_incarnation'),
                           data['history_epoch'], identity)
    session = Session.objects.filter(pk=event.session_id, provider=event.provider).first()
    lines = ()
    if session is not None and refs:
        query = SessionItem.objects.filter(session_id=session.id)
        if identity is not None and session.provider == 'claude_code':
            query = query.filter(message_id=identity.native_id)
        records = (record for record in _read(session, query) if set(refs).intersection(record[2]))
        for group in _groups(session, records):
            current_text, current_lines = _assembled(session, group)
            if current_text != text:
                continue
            current_identity = _canonical(session, group[0])
            if identity is not None and current_identity != identity:
                continue
            current_key = _message_key(session.provider, session.id, session.storage_incarnation,
                                       session.history_epoch, current_identity)
            if not scoped or key == current_key:
                key, lines = current_key, current_lines
            break
    return _resolved(key=key, text=text, session_id=event.session_id, lines=lines, provider=event.provider)


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def resolve_source(source: dict) -> SummarySource:
    """Resolve exactly one supported source shape; never trust client source text for saved messages."""
    if not isinstance(source, dict):
        raise ValueError('Source must be an object')  # noqa: TRY004 — one request-validation error contract
    if set(source) == {'text', 'purpose'}:
        text, purpose = source['text'], source['purpose']
        if not isinstance(text, str) or not text.strip() or purpose not in ('selection', 'test'):
            raise ValueError('Temporary source requires text and a supported purpose')
        return _resolved(key=None, text=text, purpose=purpose)
    if set(source) == {'session_id', 'line_num'}:
        if not isinstance(source['session_id'], str) or not source['session_id'] or not _positive_int(source['line_num']):
            raise ValueError('Chat source requires session_id and a positive line_num')
        try:
            session = Session.objects.get(pk=source['session_id'])
        except Session.DoesNotExist:
            raise ValueError('Source session does not exist') from None
        return _chat(session, source['line_num'])
    if set(source) == {'event_id'} and _positive_int(source['event_id']):
        try:
            event = TimelineEvent.objects.get(pk=source['event_id'])
        except TimelineEvent.DoesNotExist:
            raise ValueError('Source event does not exist') from None
        return _snapshot(event)
    raise ValueError('Source must specify a chat message, timeline snapshot, or temporary text')
