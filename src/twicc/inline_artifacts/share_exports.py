"""Publish confined copies atomically and keep response leases alive.

Copy I/O never holds the database writer. Generations invalidate queued work;
manifest revisions guard durable state. Only selected entries grant access.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import threading
import uuid
import weakref
from copy import copy, deepcopy
from pathlib import Path
from typing import BinaryIO, Callable, NamedTuple

import orjson
from asgiref.sync import sync_to_async
from django.db import transaction

from twicc.core.models import SessionType, Share
from twicc.inline_artifacts.files import (
    MAX_INLINE_EXPORT_BYTES,
    InlineArtifactUnavailable,
    copy_source_artifact,
    copy_export_with_source_data,
    open_export_asset,
)
from twicc.inline_artifacts.share_selection import (
    SelectionNotReady,
    check_selection_ready,
    include_inline_artifacts,
    prepare_share_selection,
)
from twicc.paths import get_share_snapshot_dir, get_shares_dir
from twicc.providers.db_writer import run_under_db_write_lock

_COPY_ID = re.compile(r'[0-9a-f]{32}\Z')
_guard = threading.RLock()
_generations: dict[str, int] = {}
_removed: set[str] = set()
_leases: dict[tuple[str, str], int] = {}
_retired: set[tuple[str, str]] = set()
_preparing: set[tuple[str, str]] = set()
_loop_locks = weakref.WeakKeyDictionary()


class InlineExportFailure(Exception):
    """Stable export failure with no private source paths."""

    def __init__(self, code='export_failed', *, generation=None):
        self.code = code
        self.generation = generation
        super().__init__(code)


class PreparedInlineExports(NamedTuple):
    ready_metadata: dict
    new_copy_ids: tuple[str, ...]
    retained_copy_ids: tuple[str, ...]
    copied_bytes: int
    retained_bytes: int
    generation: int
    base_revision: int
    base_options: dict
    recapture: bool


class InlineAssetLease(NamedTuple):
    file: BinaryIO
    release: Callable[[], None]
    copy_id: str


def inline_export_root(share_id: str) -> Path:
    """Server-only export root; replacement IDs never appear in public URLs."""
    return get_share_snapshot_dir(share_id) / 'inline-artifacts'


def _root_key(share_id):
    return str(inline_export_root(share_id))


def share_export_lock(share_id: str) -> asyncio.Lock:
    """Serialize owner and background preparation for a share on its loop.

    Access-removal callers invalidate generations before waiting for this lock.
    They need not wait for any copy work before committing access removal.
    """
    loop = asyncio.get_running_loop()
    locks = _loop_locks.setdefault(loop, {})
    return locks.setdefault(_root_key(share_id), asyncio.Lock())


def invalidate_inline_exports(share_id: str) -> int:
    """Invalidate already queued copies without changing public state."""
    root = _root_key(share_id)
    with _guard:
        generation = _generations.get(root, 0) + 1
        _generations[root] = generation
        return generation


def reserve_inline_exports(share_id: str) -> int:
    """Reserve one generation before an operation's first asynchronous work."""
    return invalidate_inline_exports(share_id)


def _generation_current(share_id, generation):
    root = _root_key(share_id)
    with _guard:
        return root not in _removed and _generations.get(root, 0) == generation


def _check_generation(share_id, generation):
    if not _generation_current(share_id, generation):
        raise InlineExportFailure('export_interrupted', generation=generation)


def _copy_ids(state):
    return {entry['copy_id'] for entry in state.get('artifacts', {}).values()
            if isinstance(entry, dict) and isinstance(entry.get('copy_id'), str)
            and _COPY_ID.fullmatch(entry['copy_id'])}


def _load_share(share_id):
    return Share.objects.select_related('session').filter(id=share_id).first()


def _tree_bytes(path):
    # These trees only contain files produced by our confined copy operation.
    return sum(entry.stat().st_size for entry in path.rglob('*') if entry.is_file())


async def prepare_inline_exports(
    share_id: str, selection: dict, *, retain: dict | None = None, expected_generation: int | None = None,
) -> PreparedInlineExports:
    """Prepare complete replacement copies off-thread, without DB writes.

    Pending selected entries require copies. Ready snapshot tombstones retain
    their bytes. Inclusion re-enable replaces selected files within the capture.
    Pass the operation-start generation across loading and selection. If omitted,
    reservation occurs at API entry, before loading. A stale reservation fails.
    Any failure discards all new copies and leaves published state unchanged.
    """
    generation = reserve_inline_exports(share_id) if expected_generation is None else expected_generation
    _check_generation(share_id, generation)
    base = await sync_to_async(_load_share)(share_id)
    _check_generation(share_id, generation)
    previous = deepcopy(retain if retain is not None else (base.inline_artifact_exports if base else {}))
    state = deepcopy(selection)
    new_ids = []
    retained = set()
    copied = 0
    retained_bytes = 0
    enabled = state.get('enabled', False)
    reenable = enabled and previous.get('initialized') and not previous.get('enabled', True)
    mode_changed = (previous.get('initialized') and state.get('selection_options', {}).get('mode')
                    != previous.get('selection_options', {}).get('mode'))
    replace_selected = reenable or mode_changed
    recapture = (
        bool(previous.get('initialized'))
        and state.get('selection_options', {}).get('mode') == 'snapshot'
        and (
            bool(set(previous.get('captured', {})) - set(state.get('captured', {})))
            or any(state.get('artifacts', {}).get(key, {}).get('status') == 'pending'
                   and previous.get('artifacts', {}).get(key, {}).get('copy_id')
                   for key in state.get('selected', {}))
        )
    )
    root = inline_export_root(share_id)
    try:
        for key, entry in state.get('artifacts', {}).items():
            cid = entry.get('copy_id')
            needs_copy = enabled and key in state.get('selected', {}) and (replace_selected or entry.get('status') != 'ready')
            if cid and not needs_copy:
                if not _COPY_ID.fullmatch(cid):
                    raise InlineExportFailure()
                size = entry.get('byte_size')
                if type(size) is not int or size < 0:
                    size = await asyncio.to_thread(_tree_bytes, root / cid)
                    entry['byte_size'] = size
                retained_bytes += size
                retained.add(cid)
        if retained_bytes > MAX_INLINE_EXPORT_BYTES:
            raise InlineExportFailure('export_too_large')
        if enabled:
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            for key, record in state.get('selected', {}).items():
                entry = state['artifacts'][key]
                if entry.get('status') == 'ready' and not replace_selected:
                    continue
                session_id, artifact_id = orjson.loads(key)
                if artifact_id != record['artifact_id'] or (base is not None and session_id != base.session_id):
                    raise InlineExportFailure()
                cid = uuid.uuid4().hex
                new_ids.append(cid)
                with _guard:
                    _preparing.add((_root_key(share_id), cid))
                task = asyncio.create_task(asyncio.to_thread(
                    copy_source_artifact, session_id, record, root / cid,
                    MAX_INLINE_EXPORT_BYTES - retained_bytes - copied,
                ))
                try:
                    # Cancellation cannot stop a filesystem worker. Let it finish
                    # before discarding its folder, or it can recreate an orphan.
                    size = await asyncio.shield(task)
                except asyncio.CancelledError:
                    try:
                        await task
                    except Exception:
                        pass
                    raise
                except InlineArtifactUnavailable:
                    raise InlineExportFailure() from None
                copied += size
                if retained_bytes + copied > MAX_INLINE_EXPORT_BYTES:
                    raise InlineExportFailure('export_too_large')
                prior_revision = previous.get('artifacts', {}).get(key, {}).get('code_revision')
                revision = prior_revision + 1 if type(prior_revision) is int else 1
                state['artifacts'][key] = dict(status='ready', copy_id=cid, code_revision=revision, byte_size=size)
        if new_ids:
            state['revision'] = max(state.get('revision', 0), previous.get('revision', 0) + 1)
        return PreparedInlineExports(state, tuple(new_ids), tuple(sorted(retained)), copied, retained_bytes,
                                     generation, previous.get('revision', 0), deepcopy(base.options) if base else {}, recapture)
    except BaseException as error:
        if isinstance(error, InlineExportFailure):
            error.generation = generation
        await discard_inline_exports(share_id, new_ids)
        raise


def _cleanup_root_if_removed(root):
    if root in _removed and not any(key[0] == root for key in _leases) and not any(key[0] == root for key in _preparing):
        shutil.rmtree(root, ignore_errors=True)
        parent = Path(root).parent
        try:
            parent.rmdir()
        except OSError:
            pass


def _discard_copies(share_id, copy_ids):
    root = _root_key(share_id)
    with _guard:
        for cid in copy_ids:
            if not isinstance(cid, str) or not _COPY_ID.fullmatch(cid):
                continue
            key = (root, cid)
            _preparing.discard(key)
            _retired.add(key)
            if not _leases.get(key):
                shutil.rmtree(Path(root) / cid, ignore_errors=True)
                _retired.discard(key)
        _cleanup_root_if_removed(root)


async def discard_inline_exports(share_id: str, copy_ids) -> None:
    """Discard only the caller's unpublished replacement IDs."""
    await asyncio.to_thread(_discard_copies, share_id, copy_ids)


async def retire_inline_exports(share_id: str, copy_ids: list[str]) -> None:
    """Remove unreferenced copies after all response-lifetime leases finish."""
    def retire():
        with _guard:
            share = _load_share(share_id)
            referenced = _copy_ids(share.inline_artifact_exports or {}) if share else set()
            _discard_copies(share_id, set(copy_ids) - referenced)
    await sync_to_async(retire)()


async def _settle_inline_exports(share_id, copy_ids):
    """Finish reference-aware retirement even after repeated cancellation."""
    task = asyncio.create_task(retire_inline_exports(share_id, list(copy_ids)))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError()


def _commit_prepared(share_id, expected_revision, prepared, options, *, owner=None, save_kwargs=None):
    """Run under the DB writer, with no filesystem copying."""
    with _guard, transaction.atomic():
        root = _root_key(share_id)
        fresh = _load_share(share_id)
        creating = bool(save_kwargs and save_kwargs.get('force_insert'))
        if (_generations.get(root, 0) != prepared.generation or root in _removed
                or (fresh is None and not creating) or (creating and fresh is not None)):
            return False
        previous = (fresh.inline_artifact_exports or {}) if fresh else {}
        if previous.get('revision', 0) != expected_revision or expected_revision != prepared.base_revision:
            return False
        if fresh and (not fresh.is_active() or fresh.options != prepared.base_options):
            return False
        candidate = copy(owner if owner is not None else fresh)
        if creating:
            from twicc.core.models import Session
            candidate.session = Session.objects.get(id=candidate.session_id)
        if fresh:
            candidate.session = fresh.session
            if owner is not None and owner.session_id != fresh.session_id:
                return False
        candidate.options = deepcopy(options)
        candidate.inline_artifact_exports = deepcopy(previous)
        state = prepared.ready_metadata
        if not candidate.session or candidate.session.type != SessionType.SESSION or candidate.kind != 'session':
            return False
        if state.get('enabled'):
            check_selection_ready(candidate)
            expected = prepare_share_selection(candidate, recapture=prepared.recapture)
            for field in ('selected', 'captured', 'enabled', 'selection_options'):
                if state.get(field) != expected.get(field):
                    return False
        elif include_inline_artifacts(options):
            return False
        candidate.inline_artifact_exports = deepcopy(state)
        kwargs = dict(save_kwargs or {'update_fields': ['options', 'inline_artifact_exports', 'updated_at']})
        if 'update_fields' in kwargs:
            kwargs['update_fields'] = list(dict.fromkeys([*kwargs['update_fields'], 'inline_artifact_exports']))
        candidate.save(**kwargs)
        for cid in prepared.new_copy_ids:
            _preparing.discard((root, cid))
        if owner is not None:
            owner.__dict__.update(candidate.__dict__)
        return True


async def commit_inline_exports(share_id: str, expected_revision: int, prepared: PreparedInlineExports, options: dict) -> bool:
    """Atomically publish metadata/options after fresh activity and source checks.

    A rejected commit discards only this preparation's new folders. The caller
    can retry from fresh state. Readiness failures remain typed and retriable.
    """
    previous = None
    async def commit():
        return await sync_to_async(_commit_prepared)(share_id, expected_revision, prepared, options)
    try:
        previous = await sync_to_async(_load_share)(share_id)
        committed = await run_under_db_write_lock(commit)
        if committed:
            await broadcast_inline_exports(share_id)
        return committed
    finally:
        # The shielded writer can commit before raising outer cancellation.
        # Durable references decide which new and old trees can retire.
        old_ids = _copy_ids(previous.inline_artifact_exports or {}) if previous else set()
        await _settle_inline_exports(share_id, set(prepared.new_copy_ids) | old_ids)


async def save_inline_share(
    share, *, writer, recapture=False, expected_generation: int | None = None, **save_kwargs,
) -> bool:
    """Owner mutation integration. Explicit mutations are all-or-nothing."""
    disabling = not include_inline_artifacts(share.options or {})
    generation = reserve_inline_exports(share.id) if expected_generation is None else expected_generation
    async def save():
        if not _generation_current(share.id, generation):
            return False
        fresh = await sync_to_async(_load_share)(share.id)
        previous = deepcopy(fresh.inline_artifact_exports or {}) if fresh else {}
        share.inline_artifact_exports = previous
        if fresh:
            share.session = fresh.session
        else:
            from twicc.core.models import Session
            share.session = await sync_to_async(Session.objects.get)(id=share.session_id)
        if share.session.type != SessionType.SESSION:
            async def native_commit():
                await share.asave(**save_kwargs)
            await writer(native_commit)
            return True
        if disabling:
            selection = deepcopy(previous)
            if previous:
                selection.update(enabled=False, revision=previous.get('revision', 0) + int(previous.get('enabled', True)))
        else:
            selection = await sync_to_async(prepare_share_selection)(share, recapture=recapture)
        prepared = await prepare_inline_exports(share.id, selection, retain=previous, expected_generation=generation)
        prepared = prepared._replace(recapture=recapture)
        async def commit():
            return await sync_to_async(_commit_prepared)(share.id, previous.get('revision', 0), prepared, share.options,
                                                         owner=share, save_kwargs=save_kwargs)
        try:
            return await writer(commit)
        finally:
            await _settle_inline_exports(share.id, set(prepared.new_copy_ids) | _copy_ids(previous))

    if disabling:
        return await save()
    async with share_export_lock(share.id):
        return await save()


def lease_inline_asset(share_id: str, artifact_key: str, asset_path: str) -> InlineAssetLease:
    """Open a selected current file with one response-lifetime read lease.

    Call via sync_to_async from async routes. The fresh DB check and file open
    share a guard with pointer commits/deletion. The release callback is sync,
    idempotent, and suitable for inline_asset_response's FileResponse closer.
    """
    root = _root_key(share_id)
    with _guard:
        share = _load_share(share_id)
        if root in _removed or share is None or not share.is_active() or share.kind != 'session':
            raise InlineArtifactUnavailable()
        state = share.inline_artifact_exports or {}
        if (not include_inline_artifacts(share.options or {}) or not state.get('enabled')
                or artifact_key not in state.get('selected', {}) or share.session.type != SessionType.SESSION):
            raise InlineArtifactUnavailable()
        record = state['selected'][artifact_key]
        try:
            current = prepare_share_selection(share)
        except SelectionNotReady:
            raise InlineArtifactUnavailable() from None
        if current.get('selected', {}).get(artifact_key) != record:
            raise InlineArtifactUnavailable()
        if artifact_key != orjson.dumps([share.session_id, record['artifact_id']]).decode():
            raise InlineArtifactUnavailable()
        entry = state.get('artifacts', {}).get(artifact_key, {})
        cid = entry.get('copy_id')
        if entry.get('status') != 'ready' or not isinstance(cid, str) or not _COPY_ID.fullmatch(cid):
            raise InlineArtifactUnavailable()
        key = (root, cid)
        if key in _retired:
            raise InlineArtifactUnavailable()
        file = open_export_asset(Path(root) / cid, asset_path)
        _leases[key] = _leases.get(key, 0) + 1
        released = False
        def release():
            nonlocal released
            with _guard:
                if released:
                    return
                released = True
                remaining = _leases[key] - 1
                if remaining:
                    _leases[key] = remaining
                else:
                    del _leases[key]
                    if key in _retired or root in _removed:
                        shutil.rmtree(Path(root) / cid, ignore_errors=True)
                        _retired.discard(key)
                _cleanup_root_if_removed(root)
        return InlineAssetLease(file, release, cid)


async def remove_inline_share_exports(share_id: str) -> None:
    """Deny new leases and clean copies after existing responses complete."""
    root = _root_key(share_id)
    invalidate_inline_exports(share_id)
    def remove():
        with _guard:
            _removed.add(root)
            path = Path(root)
            if path.exists():
                ids = [entry.name for entry in path.iterdir() if _COPY_ID.fullmatch(entry.name)]
                # Active workers finish before they own their staging cleanup.
                _retired.update((root, cid) for cid in ids)
                _discard_copies(share_id, [cid for cid in ids if (root, cid) not in _preparing])
            _cleanup_root_if_removed(root)
    await asyncio.to_thread(remove)


async def ensure_inline_exports(share_id: str) -> None:
    """Capture legacy metadata before copying; failures are per artifact.

    Initialized snapshot captures and successful copies never recapture.
    Later authorized ensure/retry calls recover interrupted pending captures.
    """
    generation = reserve_inline_exports(share_id)
    get_inline_export_coordinator().preserve_unfinished_data(share_id, generation)
    async with share_export_lock(share_id):
        if not _generation_current(share_id, generation):
            return
        async def initialize():
            def commit():
                with _guard, transaction.atomic():
                    share = _load_share(share_id)
                    if (share is None or not share.is_active() or share.kind != 'session'
                            or not include_inline_artifacts(share.options or {})
                            or share.session.type != SessionType.SESSION):
                        return []
                    if (share.inline_artifact_exports or {}).get('initialized'):
                        return None
                    state = prepare_share_selection(share)
                    if _generations.get(_root_key(share_id), 0) != generation:
                        return []
                    share.inline_artifact_exports = state
                    share.save(update_fields=['inline_artifact_exports', 'updated_at'])
                    return list(state['selected'])
            return await sync_to_async(commit)()
        keys = await run_under_db_write_lock(initialize)
        if keys is None:
            await recover_snapshot_initialization(share_id, expected_generation=generation)
            return
        for key in keys:
            await _retry_locked(share_id, key, initial=True, expected_generation=generation)


async def _retry_locked(share_id, artifact_key, *, initial=False, expected_generation):
    if not _generation_current(share_id, expected_generation):
        return
    share = await sync_to_async(_load_share)(share_id)
    if (share is None or not share.is_active() or not include_inline_artifacts(share.options or {})
            or share.kind != 'session' or share.session.type != SessionType.SESSION):
        return
    previous = deepcopy(share.inline_artifact_exports or {})
    entry = previous.get('artifacts', {}).get(artifact_key, {})
    if artifact_key not in previous.get('selected', {}) or entry.get('status') not in ({'pending'} if initial else {'error'}):
        return
    check_selection_ready(share)
    selection = (await sync_to_async(prepare_share_selection)(share)
                 if (share.options or {}).get('mode', 'live') == 'live' else deepcopy(previous))
    if artifact_key not in selection.get('selected', {}):
        return
    # Copy only this failed identity; retained ready and other pending entries
    # stay in the durable capture and do not receive new source bytes.
    complete_selection = deepcopy(selection)
    selected = selection['selected']
    selection['selected'] = {artifact_key: selected[artifact_key]}
    try:
        prepared = await prepare_inline_exports(share_id, selection, retain=previous,
                                                expected_generation=expected_generation)
    except InlineExportFailure as error:
        await publish_inline_export_error(share_id, previous, artifact_key, error.code, complete_selection,
                                          expected_generation=error.generation)
        return
    state = prepared.ready_metadata
    state['selected'] = selected
    # Partial preparation is not a recapture, even if another capture is pending.
    prepared = prepared._replace(recapture=False)
    await commit_inline_exports(share_id, previous['revision'], prepared, share.options)


async def publish_inline_export_error(
    share_id: str, previous: dict, artifact_key: str, code: str, selection: dict, *, expected_generation: int,
) -> bool:
    """Publish a background per-artifact error only for its original generation.

    ``previous`` is the loaded durable state; ``selection`` is the full desired
    selection from before copying. InlineExportFailure carries the generation.
    Explicit owner operations return failure instead of calling this helper.
    """
    generation = expected_generation
    async def publish():
        def commit():
            with _guard, transaction.atomic():
                share = _load_share(share_id)
                if (share is None or not share.is_active() or not include_inline_artifacts(share.options or {})
                        or _generations.get(_root_key(share_id), 0) != generation
                        or share.inline_artifact_exports != previous):
                    return False
                expected = prepare_share_selection(share)
                if any(selection.get(field) != expected.get(field)
                       for field in ('selected', 'captured', 'enabled', 'selection_options')):
                    return False
                state = deepcopy(selection)
                prior_revision = previous.get('artifacts', {}).get(artifact_key, {}).get('code_revision')
                state['artifacts'][artifact_key] = dict(status='error', code_revision=prior_revision, copy_id=None, error=code)
                state['revision'] = max(state.get('revision', 0), previous.get('revision', 0)) + 1
                share.inline_artifact_exports = state
                share.save(update_fields=['inline_artifact_exports', 'updated_at'])
                return True
        return await sync_to_async(commit)()
    try:
        committed = await run_under_db_write_lock(publish)
        if committed:
            await broadcast_inline_exports(share_id)
        return committed
    finally:
        await _settle_inline_exports(share_id, _copy_ids(previous))


async def retry_inline_export(share_id: str, artifact_key: str) -> None:
    """Retry failed initial snapshot entries or current live errors only."""
    generation = reserve_inline_exports(share_id)
    get_inline_export_coordinator().preserve_unfinished_data(share_id, generation)
    async with share_export_lock(share_id):
        if not _generation_current(share_id, generation):
            return
        await recover_snapshot_initialization(share_id, expected_generation=generation)
        await _retry_locked(share_id, artifact_key, expected_generation=generation)


async def recover_snapshot_initialization(share_id: str, *, expected_generation: int | None = None) -> None:
    """Recover pending captures under a new or existing authorized generation."""
    generation = reserve_inline_exports(share_id) if expected_generation is None else expected_generation
    async def recover():
        def commit():
            with _guard, transaction.atomic():
                share = _load_share(share_id)
                if (share is None or (share.options or {}).get('mode') != 'snapshot'
                        or not _generation_current(share_id, generation)):
                    return
                state = deepcopy(share.inline_artifact_exports or {})
                changed = False
                for entry in state.get('artifacts', {}).values():
                    if entry.get('status') == 'pending':
                        entry.update(status='error', error='export_interrupted')
                        changed = True
                if changed:
                    state['revision'] = state.get('revision', 0) + 1
                    share.inline_artifact_exports = state
                    share.save(update_fields=['inline_artifact_exports', 'updated_at'])
        await sync_to_async(commit)()
    await run_under_db_write_lock(recover)


async def reconcile_inline_exports() -> None:
    """Startup cleanup preserves referenced trees and removes crash leftovers."""
    # Initialization commits pending capture before creating any directory.
    # Recover durable rows independently from filesystem orphan cleanup.
    snapshot_ids = await sync_to_async(list)(Share.objects.filter(
        kind='session', options__mode='snapshot', inline_artifact_exports__initialized=True,
    ).values_list('id', flat=True))
    for share_id in snapshot_ids:
        await recover_snapshot_initialization(share_id)
    root = get_shares_dir()
    if not root.exists():
        return
    for directory in await asyncio.to_thread(lambda: list(root.iterdir())):
        inline = directory / 'inline-artifacts'
        if not inline.is_dir():
            continue
        share = await sync_to_async(_load_share)(directory.name)
        if share is None:
            await remove_inline_share_exports(directory.name)
            continue
        if share.kind != 'session':
            continue
        with _guard:
            active = {cid for path, cid in _preparing if path == str(inline)}
        await retire_inline_exports(share.id, [entry.name for entry in inline.iterdir()
                                             if entry.name not in active])


async def broadcast_inline_exports(share_id: str) -> None:
    """Send committed owner metadata and sanitized public manifest events."""
    from twicc.core.services.share_mutation import broadcast_share_updated

    share = await sync_to_async(_load_share)(share_id)
    if share is not None and share.is_active():
        await broadcast_share_updated(share)


def _metadata_preparation(share, state, generation):
    previous = share.inline_artifact_exports or {}
    return PreparedInlineExports(state, (), tuple(_copy_ids(state)), 0, 0, generation,
                                 previous.get('revision', 0), deepcopy(share.options), False)


async def refresh_export_data(share_id: str, artifact_key: str, paths: list[str]) -> None:
    """Replace complete saved data without reading unpublished source code.

    Failure keeps the ready copy runnable and records an owner-only data_error.
    Relevant paths are an enqueue hint; copying always reconciles the data tree.
    A new copy pointer changes the manifest revision, never the code revision.
    """
    generation = reserve_inline_exports(share_id)
    async with share_export_lock(share_id):
        await _refresh_data_locked(share_id, artifact_key, generation)


async def _refresh_data_locked(share_id, artifact_key, generation):
    if not _generation_current(share_id, generation):
        return
    share = await sync_to_async(_load_share)(share_id)
    if (share is None or not share.is_active() or share.kind != 'session'
            or (share.options or {}).get('mode', 'live') != 'live'
            or not include_inline_artifacts(share.options or {})):
        return
    previous = deepcopy(share.inline_artifact_exports or {})
    record = previous.get('selected', {}).get(artifact_key)
    entry = previous.get('artifacts', {}).get(artifact_key, {})
    if not record or entry.get('status') != 'ready' or not entry.get('copy_id'):
        return
    # The lease pins the complete old tree, including assets not yet opened.
    try:
        lease = await sync_to_async(lease_inline_asset)(share_id, artifact_key, record['src'].rsplit('/', 1)[1])
    except InlineArtifactUnavailable:
        return
    lease.file.close()
    cid = uuid.uuid4().hex
    root = inline_export_root(share_id)
    with _guard:
        _preparing.add((_root_key(share_id), cid))
    try:
        retained_ids = _copy_ids(previous) - {lease.copy_id}
        retained_bytes = await asyncio.to_thread(lambda: sum(_tree_bytes(root / item) for item in retained_ids))
        worker = asyncio.create_task(asyncio.to_thread(
            copy_export_with_source_data, root / lease.copy_id, share.session_id, record['artifact_id'],
            root / cid, MAX_INLINE_EXPORT_BYTES - retained_bytes,
        ))
        try:
            cancelled = False
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                try:
                    worker.result()
                except Exception:
                    pass
                raise asyncio.CancelledError()
            size = worker.result()
        except InlineArtifactUnavailable:
            state = deepcopy(previous)
            state['artifacts'][artifact_key]['data_error'] = 'export_failed'
            state['revision'] = previous.get('revision', 0) + 1
            prepared = _metadata_preparation(share, state, generation)
        else:
            state = deepcopy(previous)
            state['artifacts'][artifact_key].update(copy_id=cid, byte_size=size)
            state['artifacts'][artifact_key].pop('data_error', None)
            state['revision'] = previous.get('revision', 0) + 1
            prepared = _metadata_preparation(share, state, generation)._replace(
                new_copy_ids=(cid,), retained_copy_ids=tuple(retained_ids), copied_bytes=size,
                retained_bytes=retained_bytes,
            )
        await commit_inline_exports(share_id, prepared.base_revision, prepared, share.options)
    finally:
        lease.release()
        await _settle_inline_exports(share_id, [cid])


class InlineExportCoordinator:
    """One process-local serial worker with bounded, coalesced source events.

    Overflow collapses into a Share-row sweep, never a transcript scan. Dirty
    events invalidate current preparations synchronously and queue another pass.
    The artifact watcher does not query ORM or wait for compute readiness.
    """

    MAX_PENDING_SESSIONS = 256
    MAX_PENDING_PATHS = 256

    def __init__(self):
        self._pending = {}
        self._active_paths = {}
        self._shares = {}
        self._wake = asyncio.Event()
        self._worker = None
        self._accepting = True
        self._sweep = False
        self._reservations = {}
        self._signatures = {}
        self._missed_data = set()

    async def start(self):
        if self._worker is not None:
            return
        self._accepting = True
        # Frozen initialization recovery and orphan cleanup never refresh data.
        await reconcile_inline_exports()
        await self._load_live_shares()
        self._sweep = True
        for ids in self._shares.values():
            for share_id in ids:
                self._reservations[share_id] = reserve_inline_exports(share_id)
        self._wake.set()
        self._worker = asyncio.create_task(self._run(), name='inline-export-coordinator')

    async def stop(self):
        self._accepting = False
        self._wake.set()
        if self._worker is not None:
            await self._worker
            self._worker = None

    def publication_changed(self, session_id: str):
        self._enqueue(session_id, None if session_id in self._missed_data else set())

    def files_changed(self, session_id: str, paths: list[str]):
        relevant = {path for path in paths if path == 'inline-artifacts' or path.startswith('inline-artifacts/')}
        if relevant:
            self._enqueue(session_id, relevant)

    def _enqueue(self, session_id, paths):
        if not self._accepting:
            return
        for share_id in self._shares.get(session_id, ()):
            self._reservations[share_id] = reserve_inline_exports(share_id)
        self._merge_pending_paths(session_id, paths)

    def _merge_pending_paths(self, session_id, paths):
        if session_id not in self._pending and len(self._pending) >= self.MAX_PENDING_SESSIONS:
            self._sweep = True
        else:
            previous = self._pending.get(session_id, set())
            active = self._active_paths.get(session_id, set())
            # Superseding a consumed batch must retain its unfinished data.
            # HTML and duplicate-publication events can invalidate its token.
            combined = None if paths is None or previous is None or active is None else previous | active | paths
            self._pending[session_id] = None if combined is None or len(combined) > self.MAX_PENDING_PATHS else combined
        self._wake.set()

    def preserve_unfinished_data(self, share_id: str, expected_generation: int) -> None:
        """Replay queued, active, or sweep data under a direct API's reservation.

        Call synchronously after ensure/retry reserves, before its first await.
        No ORM, new reservation, or snapshot work occurs in this callback.
        The share lock lets the authorized API settle before its replay loads.
        """
        if self._worker is None or not self._accepting or not _generation_current(share_id, expected_generation):
            return
        for session_id, share_ids in self._shares.items():
            if share_id not in share_ids:
                continue
            active = self._active_paths.get(session_id, set())
            pending = self._pending.get(session_id, set())
            # Startup and overflow sweeps retain whole-data obligations even
            # when no active or pending path entry exists for this source.
            paths = None if self._sweep or active is None or pending is None else active | pending
            if paths is not None:
                paths = {path for path in paths if path == 'inline-artifacts'
                         or len(path.split('/')) == 2 or path.split('/')[2:3] == ['data']}
                if not paths:
                    return
            # Keep this operation's token unchanged. Other shares retain the
            # token captured for their original queued work, when available.
            with _guard:
                self._reservations[share_id] = expected_generation
                for other_id in share_ids - {share_id}:
                    self._reservations.setdefault(other_id, _generations.get(_root_key(other_id), 0))
            self._merge_pending_paths(session_id, paths)
            return

    async def _load_live_shares(self):
        rows = await sync_to_async(list)(Share.objects.filter(
            kind='session', session__type=SessionType.SESSION,
        ).values_list('id', 'session_id', 'options', 'revoked_at', 'expires_at'))
        shares = {}
        from django.utils import timezone
        for share_id, session_id, options, revoked, expires in rows:
            active = not revoked and (expires is None or expires > timezone.now())
            signature = self._signature(session_id, options, active)
            self._signatures[share_id] = signature
            if active and (options or {}).get('mode', 'live') == 'live' and include_inline_artifacts(options or {}):
                shares.setdefault(session_id, set()).add(share_id)
        self._shares = shares

    @staticmethod
    def _signature(session_id, options, active):
        options = options or {}
        return (session_id, active, options.get('mode', 'live'), include_inline_artifacts(options),
                options.get('max_display_mode', 'normal'), options.get('frozen_at_line'))

    def register_share(self, share_id, session_id, options, *, supported, active):
        """Register committed effective options without ORM or filesystem work."""
        previous = self._signatures.get(share_id)
        signature = self._signature(session_id, options, active and supported)
        self._signatures[share_id] = signature
        for ids in self._shares.values():
            ids.discard(share_id)
        eligible = supported and active and signature[2] == 'live' and signature[3]
        if eligible:
            self._shares.setdefault(session_id, set()).add(share_id)
        if previous == signature or self._worker is None or not self._accepting:
            return
        # Initial registration reconciles source events that predate the row.
        # Only effective inline option/access changes supersede queued work.
        invalidate_inline_exports(share_id)
        if eligible:
            self._enqueue(session_id, None)

    def request_reconcile(self, session_id):
        """Reconnect queues whole-data reconciliation only in the live server."""
        if self._worker is not None:
            self._enqueue(session_id, None)

    async def _run(self):
        import logging
        logger = logging.getLogger(__name__)
        while self._accepting or self._pending or self._sweep:
            await self._wake.wait()
            self._wake.clear()
            await self._load_live_shares()
            pending, self._pending = self._pending, {}
            reservations, self._reservations = self._reservations, {}
            if self._sweep:
                self._sweep = False
                pending.update({sid: None for sid in self._shares})
            # Keep all consumed hints until the batch finishes, including work
            # queued behind another share. Superseding events merge these hints.
            self._active_paths = pending
            for session_id, paths in pending.items():
                for share_id in tuple(self._shares.get(session_id, ())):
                    try:
                        generation = reservations.get(share_id)
                        if generation is None:
                            generation = reserve_inline_exports(share_id)
                        await self._reconcile(share_id, paths, expected_generation=generation)
                        self._missed_data.discard(session_id)
                    except SelectionNotReady:
                        # An accepted compute/live apply retries missed boot data.
                        self._missed_data.add(session_id)
                    except InlineExportFailure:
                        # Source/access invalidation supersedes this operation.
                        pass
                    except Exception:
                        logger.exception('Inline export reconciliation failed for share %s', share_id)
            self._active_paths = {}

    async def reconcile(self, share_id: str):
        """Reconcile current live publications and ready data after reconnect."""
        await self._reconcile(share_id, None)

    async def _reconcile(self, share_id, paths, *, expected_generation=None):
        generation = reserve_inline_exports(share_id) if expected_generation is None else expected_generation
        async with share_export_lock(share_id):
            share = await sync_to_async(_load_share)(share_id)
            if (share is None or not share.is_active() or share.kind != 'session'
                    or not share.session or share.session.type != SessionType.SESSION
                    or (share.options or {}).get('mode', 'live') != 'live'
                    or not include_inline_artifacts(share.options or {})):
                return
            if not _generation_current(share_id, generation):
                return
            previous = deepcopy(share.inline_artifact_exports or {})
            selection = await sync_to_async(prepare_share_selection)(share)
            if selection != previous:
                # Publish placement as pending before the copy. Existing code
                # revisions remain monotonic across new finalized publications.
                for key, entry in selection.get('artifacts', {}).items():
                    if entry.get('status') == 'pending':
                        entry['code_revision'] = previous.get('artifacts', {}).get(key, {}).get('code_revision')
                prepared = _metadata_preparation(share, selection, generation)
                if not await commit_inline_exports(share_id, prepared.base_revision, prepared, share.options):
                    return
            fresh = await sync_to_async(_load_share)(share_id)
            for key, entry in fresh.inline_artifact_exports.get('artifacts', {}).items():
                if not _generation_current(share_id, generation):
                    return
                if entry.get('status') in {'pending', 'error'}:
                    artifact_id = fresh.inline_artifact_exports.get('selected', {}).get(key, {}).get('artifact_id')
                    # A selected pending placement requires its first complete
                    # publication even when coalesced paths refer elsewhere.
                    relevant = entry['status'] == 'pending' or paths is None or not paths or any(
                        path == 'inline-artifacts' or path == f'inline-artifacts/{artifact_id}'
                        or path.startswith(f'inline-artifacts/{artifact_id}/') for path in paths)
                    if relevant:
                        await _retry_locked(share_id, key, initial=entry['status'] == 'pending',
                                            expected_generation=generation)
                elif entry.get('status') == 'ready':
                    artifact_id = fresh.inline_artifact_exports.get('selected', {}).get(key, {}).get('artifact_id')
                    relevant = paths is None or any(
                        path in ('inline-artifacts', f'inline-artifacts/{artifact_id}',
                                 f'inline-artifacts/{artifact_id}/data')
                        or path.startswith(f'inline-artifacts/{artifact_id}/data/') for path in paths)
                    if relevant:
                        await _refresh_data_locked(share_id, key, generation)


_coordinator = None


def get_inline_export_coordinator() -> InlineExportCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = InlineExportCoordinator()
    return _coordinator
