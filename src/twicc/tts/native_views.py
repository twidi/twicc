"""Instance-authenticated wrappers for native-owned audio resources."""

import secrets

from django.conf import settings
from django.http import JsonResponse

from twicc.auth.access import request_allowed
from twicc.auth.local_access import remote_access_blocked
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint
from twicc.tts.native_sessions import registry
from twicc.tts.views import _method_not_allowed, tts_audio, tts_audio_ending


async def _binding(request):
    if remote_access_blocked(request):
        return None
    session = request.session
    authenticated = await session.aget(SESSION_AUTH_KEY)
    fingerprint = await session.aget(SESSION_FINGERPRINT_KEY)
    if settings.TWICC_PASSWORD_HASH and not request_allowed(request, authenticated, fingerprint):
        return None
    return (session.session_key or '', compute_fingerprint(settings.TWICC_PASSWORD_HASH))


async def native_capabilities(request):
    if request.method != 'GET':
        return _method_not_allowed('GET')
    if await _binding(request) is None:
        return JsonResponse({'error': 'Authentication required'}, status=401)
    return JsonResponse({'protocol': 1, 'available': True})


async def _owner(request, session_id, audio_id):
    binding = await _binding(request)
    session = registry.get(str(session_id))
    secret = request.headers.get('X-TwiCC-TTS-Session', '')
    if (binding is None or session is None or session.closed or session.auth_binding != binding
            or not secrets.compare_digest(session.secret.encode(), secret.encode())):
        return None
    if audio_id not in session.owned_audio and not (
        request.method == 'DELETE' and audio_id in session.released_audio
    ):
        return None
    return session


async def native_audio(request, session_id: str, audio_id: str):
    session = await _owner(request, session_id, audio_id)
    if session is None:
        return JsonResponse({'error': 'The audio is no longer available'}, status=404)
    if request.method == 'DELETE':
        await session.release_audio(audio_id)
        # Delegate the existing idempotent ending and response semantics.
        return await tts_audio(request, audio_id)
    return await tts_audio(request, audio_id)


async def native_audio_ending(request, session_id: str, audio_id: str):
    if await _owner(request, session_id, audio_id) is None:
        return JsonResponse({'error': 'The audio is no longer available'}, status=404)
    return await tts_audio_ending(request, audio_id)
