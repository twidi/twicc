"""Locale-neutral Android push presentation. No catalog loading or text rendering."""

import math
import re
from decimal import Decimal

import orjson

from twicc.core.ui_messages import ui_message
from twicc.providers.helpers import get_provider_helpers

# These producer schemas deliberately contain no translations. Tests compare every
# emitted key and required parameter with the manifest and all packaged catalogs.
_PARAMETERS = {
    'session.notifications.finished': ('provider',),
    'session.notifications.turnFinished': ('provider',),
    'session.notifications.question': ('provider',),
    'session.notifications.approval': ('provider',),
    'session.notifications.shells': ('count',),
    'session.notifications.body': ('project', 'session'),
    'session.notifications.extraUsageTitle': ('provider',),
    'session.notifications.extraUsageSentence': ('provider',),
    'session.notifications.creditsRemaining': ('count',),
    'session.notifications.creditsUsed': ('used',),
    'session.notifications.creditsUsedOf': ('used', 'limit'),
    'session.notifications.moneyUsed': ('used', 'currency'),
    'session.notifications.moneyUsedOf': ('used', 'limit', 'currency'),
    'session.notifications.peerMessage': ('peer',),
    'session.notifications.peerReply': ('peer',),
    'session.notifications.peerRequestFrom': ('peer',),
    'session.notifications.instance': (),
    'common.labels.unknown': (),
    'android.push.genericTitle': (),
    'android.push.genericBody': (),
    'android.push.finishedWithShells': ('finished', 'shells'),
    'android.push.extraUsageBody': ('sentence', 'usage'),
    'android.push.peerBody': ('title', 'preview'),
    'android.push.peerBodyWithProject': ('message', 'project'),
    'android.push.peerBodyWithSession': ('message', 'session'),
    'android.push.peerRequestBody': ('peer', 'address'),
    'android.push.projectWithParent': ('parent', 'project'),
}
# Only user-controlled presentation text can shrink. Keys, counts, financial
# figures, provider labels, and navigation fields always keep their meaning.
_USER_CONTENT = {
    'session.notifications.body': ('project', 'session'),
    'session.notifications.peerMessage': ('peer',),
    'session.notifications.peerReply': ('peer',),
    'session.notifications.peerRequestFrom': ('peer',),
    'android.push.peerBody': ('title', 'preview'),
    'android.push.peerBodyWithProject': ('project',),
    'android.push.peerBodyWithSession': ('session',),
    'android.push.peerRequestBody': ('peer', 'address'),
    'android.push.projectWithParent': ('parent', 'project'),
}
_DECIMAL_PARAMETERS = {
    'session.notifications.shells': ('count',),
    'session.notifications.creditsRemaining': ('count',),
    'session.notifications.creditsUsed': ('used',),
    'session.notifications.creditsUsedOf': ('used', 'limit'),
    'session.notifications.moneyUsed': ('used',),
    'session.notifications.moneyUsedOf': ('used', 'limit'),
}
_DECIMAL = re.compile(r'-?[0-9]+(?:\.[0-9]+)?')
_SESSION_PATH = re.compile(r'/projects?/[A-Za-z0-9_-]{1,255}/session/[A-Za-z0-9_-]{1,255}/?')
_DETAIL = re.compile(r'[A-Za-z0-9_-]{1,96}')


def _provider(provider):
    return get_provider_helpers(provider).LABEL or str(provider)


def _name(value):
    return value or ui_message('common.labels.unknown')


def _project(name, parent):
    if parent:
        return ui_message('android.push.projectWithParent', {'parent': parent, 'project': _name(name)})
    return _name(name)


def agent_messages(event: str, info, session_title, project_name, project_parent_name):
    """Choose the detected event, independently of the current aggregate state."""
    provider = {'provider': _provider(info.provider)}
    title = ui_message({
        'user_turn': 'session.notifications.finished',
        'question': 'session.notifications.question',
        'approval': 'session.notifications.approval',
    }[event], provider)
    shells = (info.background_work_in_progress or {}).get('shells') or 0
    if event == 'user_turn' and shells:
        title = ui_message('android.push.finishedWithShells', {
            'finished': ui_message('session.notifications.turnFinished', provider),
            'shells': ui_message('session.notifications.shells', {'count': shells}, count=shells),
        })
    return title, ui_message('session.notifications.body', {
        'project': _project(project_name, project_parent_name), 'session': _name(session_title),
    })


def _amount(value, decimals):
    if value is None or decimals is None or decimals < 0:
        return None
    amount = Decimal(str(value)).scaleb(-decimals)
    text = format(amount, f'.{decimals}f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def extra_usage_messages(provider, snapshot):
    """Keep decimal amounts canonical; the receiving device formats them."""
    label = {'provider': _provider(provider)}
    title = ui_message('session.notifications.extraUsageTitle', label)
    sentence = ui_message('session.notifications.extraUsageSentence', label)
    usage = None
    if snapshot.extra_usage_utilization is not None:
        used = snapshot.extra_usage_used_credits or 0
        limit = snapshot.extra_usage_monthly_limit
        currency = snapshot.extra_usage_currency
        amount = _amount(used, snapshot.extra_usage_decimal_places) if currency else None
        if amount is not None:
            used = amount
            limit = _amount(limit, snapshot.extra_usage_decimal_places)
            key = 'moneyUsedOf' if limit is not None else 'moneyUsed'
            params = {'used': used, 'currency': currency}
            count = None
        else:
            key = 'creditsUsedOf' if limit is not None else 'creditsUsed'
            params = {'used': used}
            count = limit if limit is not None else used
        if limit is not None:
            params['limit'] = limit
        usage = ui_message('session.notifications.' + key, params, count=count)
    elif snapshot.extra_usage_remaining_credits is not None:
        remaining = round(snapshot.extra_usage_remaining_credits)
        usage = ui_message('session.notifications.creditsRemaining', {'count': remaining}, count=remaining)
    body = ui_message('android.push.extraUsageBody', {'sentence': sentence, 'usage': usage}) if usage else sentence
    return title, body


def peer_message_messages(message, routing):
    peer = message.peer
    label = peer.name or peer.remote_display_name or peer.base_url or ui_message('session.notifications.instance')
    title = ui_message('session.notifications.peerReply' if message.reply_to_message_id else 'session.notifications.peerMessage', {'peer': label})
    body = ui_message('android.push.peerBody', {'title': message.title or '', 'preview': (message.payload or {}).get('text') or ''})
    if routing is not None:
        if routing.project_name:
            body = ui_message('android.push.peerBodyWithProject', {
                'message': body, 'project': _project(routing.project_name, routing.project_parent_name),
            })
        if routing.session_id:
            body = ui_message('android.push.peerBodyWithSession', {'message': body, 'session': _name(routing.session_title)})
    return title, body


def peer_request_messages(peer):
    label = peer.name or peer.remote_display_name or ui_message('session.notifications.instance')
    return (
        ui_message('session.notifications.peerRequestFrom', {'peer': label}),
        ui_message('android.push.peerRequestBody', {'peer': label, 'address': peer.base_url}),
    )


def _copy_descriptor(value, nodes, *, depth=1):
    """Validate and copy before serialization, with bounded recursion and nodes."""
    nodes[0] += 1
    if depth > 4 or nodes[0] > 64 or not isinstance(value, dict):
        raise ValueError('Invalid descriptor bounds')
    if set(value) - {'key', 'params', 'count'}:
        raise ValueError('Invalid descriptor fields')
    key, params = value.get('key'), value.get('params')
    if not isinstance(key, str) or key not in _PARAMETERS or not isinstance(params, dict) or set(params) != set(_PARAMETERS[key]):
        raise ValueError('Invalid descriptor schema')
    count = value.get('count')
    if 'count' in value and (type(count) not in (int, float) or not math.isfinite(count)):
        raise ValueError('Invalid plural count')
    copied = {}
    for name, item in params.items():
        if name in _DECIMAL_PARAMETERS.get(key, ()):
            if not (type(item) in (int, float) or isinstance(item, str) and _DECIMAL.fullmatch(item)):
                raise ValueError('Invalid decimal parameter')
        if isinstance(item, dict):
            copied[name] = _copy_descriptor(item, nodes, depth=depth + 1)
        elif type(item) in (str, bool, int, float):
            if type(item) in (int, float) and not math.isfinite(item):
                raise ValueError('Invalid parameter number')
            copied[name] = item
        else:
            raise ValueError('Invalid parameter type')
    return ui_message(key, copied, count=count)


def _content_fields(descriptor):
    for name, value in descriptor['params'].items():
        if isinstance(value, dict):
            yield from _content_fields(value)
        elif isinstance(value, str) and name in _USER_CONTENT.get(descriptor['key'], ()):
            yield descriptor['params'], name


def bounded_payload(payload: dict) -> dict | None:
    """Fit one v2 plaintext record, preserving all safe navigation and structure.

    Shorten only designated presentation parameters on Unicode code-point
    boundaries. If presentation cannot fit, use known generic descriptors.
    Invalid or oversized routing/envelopes cannot produce a deliverable record.
    """
    allowed = {'type', 'version', 'id', 'sent_at', 'kind', 'title_ui_message', 'body_ui_message', 'path', 'action', 'detail'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        return None
    if payload.get('type') != 'twicc.notification' or type(payload.get('version')) is not int or payload['version'] != 2:
        return None
    if payload.get('kind') not in ('user_turn', 'pending_request', 'extra_usage', 'peer'):
        return None
    for name, maximum in (('id', 36), ('sent_at', 64)):
        value = payload.get(name)
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            return None
    if 'path' in payload and (not isinstance(payload['path'], str) or _SESSION_PATH.fullmatch(payload['path']) is None):
        return None
    if 'action' in payload and payload['action'] not in ('home', 'peerInbox', 'peerRequests'):
        return None
    if 'detail' in payload and (not isinstance(payload['detail'], str) or _DETAIL.fullmatch(payload['detail']) is None):
        return None
    result = payload.copy()
    fields = ('title_ui_message', 'body_ui_message')
    try:
        nodes = [0]
        for name in fields:
            result[name] = _copy_descriptor(payload.get(name), nodes)
        # Halving the largest parameter bounds work logarithmically, including
        # escape-heavy strings whose JSON bytes exceed their UTF-8 byte length.
        candidates = [item for name in fields for item in _content_fields(result[name])]
        while len(orjson.dumps(result)) > 3900:
            if not candidates:
                break
            params, name = max(candidates, key=lambda item: len(item[0][item[1]]))
            text = params[name]
            if not text:
                break
            params[name] = text[:len(text) // 2]
        if len(orjson.dumps(result)) <= 3900:
            return result
    except (ValueError, TypeError, OverflowError, RecursionError):
        pass
    result['title_ui_message'] = ui_message('android.push.genericTitle')
    result['body_ui_message'] = ui_message('android.push.genericBody')
    try:
        return result if len(orjson.dumps(result)) <= 3900 else None
    except (TypeError, ValueError):
        return None
