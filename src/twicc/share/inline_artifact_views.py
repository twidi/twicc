"""Public root-only inline manifests, copied assets, and read-only broker routes."""

from __future__ import annotations

from urllib.parse import unquote

import orjson
from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.artifacts.broker_html import is_artifact_document_request
from twicc.core.models import ArtifactBookmark, SessionType
from twicc.inline_artifacts.files import InlineArtifactUnavailable, export_artifact_directory, _directory_at, _parts
from twicc.inline_artifacts.share_exports import (
    get_inline_export_coordinator, inline_export_root, lease_inline_asset, retry_inline_export,
)
from twicc.inline_artifacts.share_selection import (
    SelectionNotReady, include_inline_artifacts, prepare_share_selection, public_inline_manifest,
)
from twicc.inline_artifacts.views import _data_entries, inline_asset_response
from twicc.share.headers import apply_share_headers
from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404
from twicc.views import _guess_raw_content_type


def _json(payload, status=200):
    return apply_share_headers(JsonResponse(payload, status=status))


async def _context(request, token):
    try:
        context = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return None, password_required_response(request, token)
    if context.share.kind != 'session':
        raise Http404('Inline artifact unavailable')
    return context, None


async def _selected(context, source_session_id, artifact_id):
    if (context.session.type != SessionType.SESSION or source_session_id != context.share.session_id
            or not include_inline_artifacts(context.options)):
        raise Http404('Inline artifact unavailable')
    state = await sync_to_async(prepare_share_selection)(context.share)
    key = orjson.dumps([source_session_id, artifact_id]).decode()
    record = state.get('selected', {}).get(key)
    if record is None:
        raise Http404('Inline artifact unavailable')
    return key, record


async def inline_manifest(request, token):
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    context, response = await _context(request, token)
    if response is not None:
        return response
    try:
        state = context.share.inline_artifact_exports or {}
        needs_work = (context.options.get('mode', 'live') == 'live' or not state.get('initialized')
                      or any(entry.get('status') == 'pending' for entry in state.get('artifacts', {}).values()))
        if context.session.type == SessionType.SESSION and include_inline_artifacts(context.options) and needs_work:
            await get_inline_export_coordinator().schedule_public_reconcile(context.share.id)
            # Resolve again after capture: access can change while waiting for its lock.
            context, response = await _context(request, token)
            if response is not None:
                return response
        result = _json(await sync_to_async(public_inline_manifest)(context.share))
        if request.method == 'HEAD':
            result['Content-Length'] = str(len(result.content))
            result.content = b''
        return result
    except SelectionNotReady:
        return _json({'error': 'session_not_ready'}, 409)


async def inline_retry(request, token, source_session_id, artifact_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    context, response = await _context(request, token)
    if response is not None:
        return response
    try:
        key, _ = await _selected(context, source_session_id, artifact_id)
        entry = (context.share.inline_artifact_exports or {}).get('artifacts', {}).get(key, {})
        completed_snapshot = context.options.get('mode') == 'snapshot' and entry.get('status') == 'ready'
        if not completed_snapshot:
            await retry_inline_export(context.share.id, key)
        context, response = await _context(request, token)
        if response is not None:
            return response
        await _selected(context, source_session_id, artifact_id)
        return _json(await sync_to_async(public_inline_manifest)(context.share))
    except SelectionNotReady:
        return _json({'error': 'session_not_ready'}, 409)


def _listing(share_id, key, entry_filename, asset_path):
    parts = _parts(asset_path, directory=True)
    if parts[0] != 'data':
        return None
    lease = lease_inline_asset(share_id, key, entry_filename)
    try:
        with export_artifact_directory(inline_export_root(share_id) / lease.copy_id) as root:
            try:
                with _directory_at(root, parts) as directory:
                    return {'files': _data_entries(directory)}
            except FileNotFoundError:
                if parts == ['data']:
                    return {'files': []}
                return None
            except NotADirectoryError:
                return None
    finally:
        lease.file.close()
        lease.release()


async def inline_asset(request, token, source_session_id, artifact_id, asset_path):
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    context, response = await _context(request, token)
    if response is not None:
        return response
    try:
        key, record = await _selected(context, source_session_id, artifact_id)
        entry = record['src'].rsplit('/', 1)[-1]
        document_path = f'/share/{token}/inline-artifacts/{source_session_id}/{artifact_id}/{entry}'
        if unquote(request.headers.get('X-Twicc-Artifact-Doc', '')) == document_path:
            payload = await sync_to_async(_listing)(context.share.id, key, entry, asset_path)
            if payload is not None:
                result = _json(payload)
                if request.method == 'HEAD':
                    result['Content-Length'] = str(len(result.content))
                    result.content = b''
                return result
        lease = await sync_to_async(lease_inline_asset)(context.share.id, key, asset_path)
        response = await sync_to_async(inline_asset_response)(
            lease.file, _guess_raw_content_type(asset_path),
            as_document=is_artifact_document_request(request.headers.get('Sec-Fetch-Dest')),
            head=request.method == 'HEAD', release=lease.release,
        )
        return apply_share_headers(response)
    except SelectionNotReady:
        return _json({'error': 'session_not_ready'}, 409)
    except (InlineArtifactUnavailable, OSError, ValueError):
        raise Http404('Inline artifact unavailable') from None


async def inline_proxy(request, token, source_session_id, artifact_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    context, response = await _context(request, token)
    if response is not None:
        return response
    try:
        key, record = await _selected(context, source_session_id, artifact_id)
        # Require the same selected ready copy as document/data requests.
        lease = await sync_to_async(lease_inline_asset)(context.share.id, key, record['src'].rsplit('/', 1)[-1])
        try:
            bookmark = await ArtifactBookmark.objects.filter(
                session_id=source_session_id, relative_path=record['src'],
            ).afirst()
            from twicc.artifacts.proxy import artifact_proxy
            response = await artifact_proxy(request, enforced_allowlist=set((bookmark.allowed_hosts or {}).keys())
                                            if bookmark else set())
            return apply_share_headers(response)
        finally:
            lease.file.close()
            lease.release()
    except SelectionNotReady:
        return _json({'error': 'session_not_ready'}, 409)
    except InlineArtifactUnavailable:
        raise Http404('Inline artifact unavailable') from None
