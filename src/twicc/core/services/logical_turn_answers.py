"""Native source association, with a separate admitted-writer binding helper."""

from collections import OrderedDict
from typing import NamedTuple

from twicc.core.content_storage.readers import iter_current_items
from twicc.core.enums import ItemKind
from twicc.core.models import LogicalTurn, Session, SessionItem
from twicc.providers.helpers import IndexableMessage, get_provider_helpers


class LogicalAnswer(NamedTuple):
    status: str
    message: IndexableMessage | None


class WaitProgress(NamedTuple):
    consumed_up_to: int
    last_logical_turn_id: str | None


def _key(ref):
    return ref['kind'], ref['native_id'], ref.get('turn_id')


class NativeAnswerCache:
    """Observe newly inserted items, including late indexing below an observed line.

    The PK watermark measures insertion, never logical consumption. Source scope
    changes discard all observed identities. No reader mutates the journal.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.scope = None
        self.provider = None
        self.watermark = 0
        self.records = {}
        self.messages = {}
        self.all_messages = {}
        self.errors = {}

    def fork(self):
        """Stage observations without modifying a cache from an earlier commit."""
        staged = NativeAnswerCache(self.session_id)
        staged.scope = self.scope
        staged.provider = self.provider
        staged.watermark = self.watermark
        staged.records = {ref: dict(lines) for ref, lines in self.records.items()}
        staged.messages = {ref: dict(messages) for ref, messages in self.messages.items()}
        staged.all_messages = dict(self.all_messages)
        staged.errors = dict(self.errors)
        return staged

    def refresh(self):
        session = Session.objects.filter(pk=self.session_id).only(
            'provider', 'storage_incarnation', 'history_epoch',
        ).first()
        if session is None:
            return None
        scope = session.provider, session.storage_incarnation, session.history_epoch
        if scope != self.scope:
            self.scope = scope
            self.provider = session.provider
            self.watermark = 0
            self.records.clear()
            self.messages.clear()
            self.all_messages.clear()
            self.errors.clear()
        helpers = get_provider_helpers(session.provider)
        rows = SessionItem.objects.filter(session_id=self.session_id, pk__gt=self.watermark).order_by('pk')
        for item in iter_current_items(rows, 128, 8 * 1024 * 1024):
            self.watermark = max(self.watermark, item.pk)
            parsed = helpers.parse_item_content(item)
            if parsed is None:
                continue
            if session.provider == 'codex':
                thread = (parsed.get('payload') or {}).get('thread_id')
                if thread is not None and thread != session.id:
                    continue
            if session.provider == 'claude_code' and (parsed.get('parent_tool_use_id') or parsed.get('isSidechain')):
                continue
            if item.kind == ItemKind.API_ERROR and parsed.get('isApiErrorMessage'):
                error = parsed.get('error') or {}
                text = error.get('message') if isinstance(error, dict) else None
                self.errors[item.line_num] = text or helpers.extract_indexable_text_from_parsed(parsed)
            refs = helpers.completion_record_refs(parsed)
            for ref in refs:
                self.records.setdefault(tuple(ref), {})[item.line_num] = item.pk
            if item.kind != ItemKind.ASSISTANT_MESSAGE:
                continue
            text = helpers.extract_indexable_text_from_parsed(parsed)
            if text:
                message = IndexableMessage(item.line_num, text, 'assistant', item.timestamp,
                                           helpers.is_final_assistant_message(parsed))
                self.all_messages[item.line_num] = message
                for ref in refs:
                    self.messages.setdefault(tuple(ref), {})[item.line_num] = message
        return session

    def lines(self, ref):
        return sorted(self.records.get(_key(ref), {})) if ref else []

    def answer(self, ref, *, cursor):
        messages = sorted(self.messages.get(_key(ref), {}).values(), key=lambda message: message.line_num)
        messages = [message for message in messages if message.line_num > cursor]
        if not messages:
            return None
        return messages[-1]._replace(text='\n'.join(message.text for message in messages))


def resolve_logical_answer(turn: LogicalTurn, *, cursor: int,
                           cache: NativeAnswerCache | None = None) -> LogicalAnswer:
    cache = cache or NativeAnswerCache(turn.session_id)
    session = cache.refresh()
    if session is None:
        if turn.outcome in {'ended', 'provider_error'}:
            return LogicalAnswer(turn.outcome, None)
        if turn.outcome == 'normal' and not turn.final_refs:
            return LogicalAnswer('ended', None)
        return LogicalAnswer('pending', None)
    if (turn.provider != session.provider
            or turn.storage_incarnation is not None and turn.storage_incarnation != session.storage_incarnation
            or turn.history_epoch is not None and turn.history_epoch != session.history_epoch):
        return LogicalAnswer('invalidated', None)
    if turn.outcome is None:
        return LogicalAnswer('pending', None)
    if turn.outcome in {'ended', 'provider_error'}:
        return LogicalAnswer(turn.outcome, None)
    if not turn.final_refs:
        return LogicalAnswer('ended', None)
    if turn.storage_incarnation is None or turn.history_epoch is None:
        return LogicalAnswer('pending', None)
    ref = turn.final_refs[-1]
    if ref['kind'] == 'unresolved' or not ref['native_id']:
        return LogicalAnswer('pending', None)
    lines = cache.lines(ref)
    if not lines:
        return LogicalAnswer('pending', None)
    if max(lines) <= cursor:
        return LogicalAnswer('ended', None)
    message = cache.answer(ref, cursor=cursor)
    return LogicalAnswer('replied', message) if message else LogicalAnswer('pending', None)


def select_logical_turn(*, session_id: str, cursor: int, wait_token: str | None,
                        after_turn_id: str | None = None,
                        cache: NativeAnswerCache | None = None) -> LogicalTurn | None:
    if wait_token is not None:
        from twicc.core.services.logical_turns import input_for_token

        association = input_for_token(session_id=session_id, token=wait_token)
        if association is None or association.status != 'accepted':
            raise ValueError('Invalid or unaccepted wait token for this session')
        return association.turn
    turns = LogicalTurn.objects.filter(session_id=session_id).order_by('position')
    if after_turn_id is not None:
        try:
            previous = turns.get(pk=after_turn_id)
        except (LogicalTurn.DoesNotExist, ValueError) as exc:
            raise ValueError('Invalid logical progress for this session') from exc
        turns = turns.filter(position__gt=previous.position)
    cache = cache or NativeAnswerCache(session_id)
    cache.refresh()
    for turn in turns:
        lines = cache.lines(turn.final_refs[-1]) if turn.final_refs else []
        if lines and max(lines) <= cursor and turn.outcome is not None:
            continue
        # A floor is only an admission lower bound. Unresolved records cannot
        # prove that the interval precedes the caller's strict answer boundary.
        return turn
    return None


_BINDING_CACHES: OrderedDict[str, NativeAnswerCache] = OrderedDict()
_BINDING_CACHE_LIMIT = 128


def bind_indexed_turn_sources(session_id: str) -> None:
    """Bind native proof only on the caller's existing admitted writer path.

    Live ingestion and journal snapshot publication call this in their existing
    transactions. Reader-side waits never call it. The bounded cache retains
    incremental observations only while a session has bindable unbound rows.
    """
    from django.db import transaction
    from django.db.models import Q
    from twicc.agent.completion_types import NativeMessageRef
    from twicc.core.services.logical_turns import bind_turn_source

    turns = list(LogicalTurn.objects.filter(session_id=session_id).filter(
        Q(storage_incarnation__isnull=True) | Q(history_epoch__isnull=True)))
    if not turns:
        _BINDING_CACHES.pop(session_id, None)
        return
    if not any(turn.first_ref or turn.final_refs for turn in turns):
        return
    previous = _BINDING_CACHES.get(session_id)
    cache = previous.fork() if previous is not None else NativeAnswerCache(session_id)
    session = cache.refresh()
    if session is None or not session.storage_incarnation:
        return
    remaining = False
    for turn in turns:
        refs = ([turn.first_ref] if turn.first_ref else []) + turn.final_refs
        proof = next((ref for ref in refs if ref['kind'] != 'unresolved' and cache.lines(ref)), None)
        if proof is None or not bind_turn_source(
            turn_id=str(turn.pk), session_id=session_id, provider=session.provider,
            storage_incarnation=session.storage_incarnation, history_epoch=session.history_epoch,
            observed_ref=NativeMessageRef(**proof),
        ):
            remaining = True
    def publish_cache():
        _BINDING_CACHES.pop(session_id, None)
        if remaining:
            _BINDING_CACHES[session_id] = cache
            while len(_BINDING_CACHES) > _BINDING_CACHE_LIMIT:
                _BINDING_CACHES.popitem(last=False)
    transaction.on_commit(publish_cache)
