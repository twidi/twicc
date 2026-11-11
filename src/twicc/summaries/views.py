"""Authenticated summary resources; NDJSON carries complete small text snapshots."""

from contextlib import aclosing

import orjson
from asgiref.sync import sync_to_async
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from twicc.core.models import MessageSummary
from twicc.summaries import service
from twicc.core.ui_messages import UiMessageError, ui_message_text


def _error(message, status):
    metadata = getattr(message, 'ui_message', None)
    return JsonResponse({'error': str(message), **({'ui_message': metadata} if metadata else {})}, status=status)


def _method(allowed):
    response = _error(ui_message_text('Method not allowed', 'backend.common.methodNotAllowed'), 405)
    response['Allow'] = allowed
    return response


def _body(request):
    data = orjson.loads(request.body)
    if not isinstance(data, dict):
        raise UiMessageError('The body must be a JSON object', 'backend.common.bodyObject')  # noqa: TRY004 - HTTP validation contract
    return data


async def summaries_resolve(request):
    if request.method != 'POST':
        return _method('POST')
    try:
        data = _body(request)
        if set(data) != {'sources'}:
            raise UiMessageError("The body must contain 'sources'", 'backend.summaries.sourcesRequired')
        result = await sync_to_async(service.resolve_sources)(data['sources'])
        return JsonResponse({'sources': result})
    except RequestDataTooBig:
        return _error(ui_message_text('The request is too long to send', 'backend.summaries.requestTooLong'), 413)
    except (ValueError, orjson.JSONDecodeError) as error:
        return _error(ui_message_text(str(error), error.ui_message["key"], error.ui_message.get("params")) if getattr(error, "ui_message", None) else str(error), 400)


async def summaries_open(request):
    if request.method != 'POST':
        return _method('POST')
    try:
        data = _body(request)
        if ('source' not in data or set(data) - {'source', 'regenerate', 'start', 'usage_origin'}
                or data.get('usage_origin', 'unknown') not in ('unknown', 'manual', 'timeline', 'test')):
            raise UiMessageError("The body must contain 'source' and optional 'regenerate' and 'start'", 'backend.summaries.openFields')
        if not isinstance(data['source'], dict):
            raise UiMessageError("'source' must be an object", 'backend.summaries.sourceObject')  # noqa: TRY004 - HTTP validation contract
        for field in ('regenerate', 'start'):
            if field in data and not isinstance(data[field], bool):
                raise UiMessageError(f"'{field}' must be a boolean", 'backend.summaries.booleanField', {"field": field})
        handle = await service.acquire_summary(data['source'], regenerate=data.get('regenerate', False),
                                               start=data.get('start', True), usage_origin=data.get('usage_origin', 'unknown'))
        return JsonResponse({'id': handle.id, 'descriptor': handle.descriptor, 'state': handle.state})
    except RequestDataTooBig:
        return _error(ui_message_text('The request is too long to send', 'backend.summaries.requestTooLong'), 413)
    except service.SummaryUnavailable as error:
        return _error(ui_message_text(str(error), error.ui_message["key"], error.ui_message.get("params")) if getattr(error, "ui_message", None) else str(error), 409)
    except ValueError as error:
        return _error(ui_message_text(str(error), error.ui_message["key"], error.ui_message.get("params")) if getattr(error, "ui_message", None) else str(error), 400)


async def summary_detail(request, summary_id):
    if request.method == 'DELETE':
        await service.release_summary(summary_id)
        return HttpResponse(status=204)
    if request.method != 'GET':
        return _method('GET, DELETE')
    try:
        service._handle(summary_id)
    except service.SummaryGone as error:
        return _error(ui_message_text(str(error), error.ui_message["key"], error.ui_message.get("params")) if getattr(error, "ui_message", None) else str(error), 410)

    async def body():
        try:
            async with aclosing(service.summary_snapshots(summary_id)) as snapshots:
                async for snapshot in snapshots:
                    yield orjson.dumps(snapshot) + b'\n'
        except service.SummaryGone:
            return

    response = StreamingHttpResponse(body(), content_type='application/x-ndjson')
    response['Cache-Control'] = 'no-store'
    response['X-Accel-Buffering'] = 'no'
    return response


async def summary_saved(request, saved_id):
    if request.method != 'GET':
        return _method('GET')
    try:
        result = await sync_to_async(service.load_saved_summary)(saved_id)
    except MessageSummary.DoesNotExist:
        return _error(ui_message_text('The saved summary does not exist', 'backend.summaries.savedMissing'), 404)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response
