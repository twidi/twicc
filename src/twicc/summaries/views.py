"""Authenticated summary resources; NDJSON carries complete small text snapshots."""
from contextlib import aclosing

import orjson
from asgiref.sync import sync_to_async
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse

from twicc.core.models import MessageSummary
from twicc.summaries import service


def _error(message, status):
    return JsonResponse({'error': message}, status=status)


def _method(allowed):
    response = _error('Method not allowed', 405)
    response['Allow'] = allowed
    return response


def _body(request):
    data = orjson.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError('The body must be a JSON object')  # noqa: TRY004 - HTTP validation contract
    return data


async def summaries_resolve(request):
    if request.method != 'POST':
        return _method('POST')
    try:
        data = _body(request)
        if set(data) != {'sources'}:
            raise ValueError("The body must contain 'sources'")
        result = await sync_to_async(service.resolve_sources)(data['sources'])
        return JsonResponse({'sources': result})
    except RequestDataTooBig:
        return _error('The request is too long to send', 413)
    except (ValueError, orjson.JSONDecodeError) as error:
        return _error(str(error), 400)


async def summaries_open(request):
    if request.method != 'POST':
        return _method('POST')
    try:
        data = _body(request)
        if 'source' not in data or set(data) - {'source', 'regenerate', 'start'}:
            raise ValueError("The body must contain 'source' and optional 'regenerate' and 'start'")
        if not isinstance(data['source'], dict):
            raise ValueError("'source' must be an object")  # noqa: TRY004 - HTTP validation contract
        for field in ('regenerate', 'start'):
            if field in data and not isinstance(data[field], bool):
                raise ValueError(f"'{field}' must be a boolean")
        handle = await service.acquire_summary(data['source'], regenerate=data.get('regenerate', False),
                                               start=data.get('start', True))
        return JsonResponse({'id': handle.id, 'descriptor': handle.descriptor, 'state': handle.state})
    except RequestDataTooBig:
        return _error('The request is too long to send', 413)
    except service.SummaryUnavailable as error:
        return _error(str(error), 409)
    except ValueError as error:
        return _error(str(error), 400)


async def summary_detail(request, summary_id):
    if request.method == 'DELETE':
        await service.release_summary(summary_id)
        return HttpResponse(status=204)
    if request.method != 'GET':
        return _method('GET, DELETE')
    try:
        service._handle(summary_id)
    except service.SummaryGone as error:
        return _error(str(error), 410)

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
        return _error('The saved summary does not exist', 404)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response
