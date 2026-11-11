"""Best-effort daily aggregates. Never retain individual activity or client keys."""

import hashlib
import logging
import re
import secrets
from collections import Counter
from contextlib import contextmanager

from twicc.telemetry.state import state_txn, utc_today, _prune
from twicc.telemetry.task import is_telemetry_active

logger = logging.getLogger(__name__)
METRICS = frozenset({'manual_summaries', 'manual_tts_messages', 'timeline_tts_messages', 'timeline_tts_activations'})
BROWSERS = frozenset({'chrome', 'firefox', 'safari', 'edge', 'opera', 'samsung_internet', 'brave', 'vivaldi', 'other', 'android_app'})
LOCALES = frozenset({'en', 'de', 'es', 'fr', 'pt-BR'})
_VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?\Z')


def _token(data):
    return data['instance_id'], data.get('active_since')


def note_collection_setting(active: bool) -> None:
    """A telemetry storage failure must never prevent a settings update."""
    from twicc.telemetry.state import note_active_transition
    try:
        note_active_transition(active)
    except Exception:
        logger.debug('Cannot update telemetry collection state', exc_info=True)


@contextmanager
def _collection_txn(token=None):
    """Keep opt-out checks and writes together. Lock order matches bootstrap."""
    from twicc.synced_settings import _settings_lock
    if not is_telemetry_active():
        yield None
        return
    with state_txn() as txn, _settings_lock:
        if (txn.data.get('was_active') is False or not is_telemetry_active()
                or token is not None and token != _token(txn.data)):
            yield None
        else:
            yield txn


def collection_token():
    """Capture collection state when an operation starts; None means disabled."""
    try:
        with _collection_txn() as txn:
            if txn is not None:
                return _token(txn.data)
    except Exception:
        logger.debug('Cannot read telemetry collection state', exc_info=True)
    return None


def client_epoch() -> str | None:
    token = collection_token()
    return hashlib.sha256(repr(token).encode()).hexdigest() if token else None


def record_report(report: dict) -> None:
    """Only current, unbuffered authenticated Web reports may contribute."""
    token = collection_token()
    if token is None or report.get('epoch') != hashlib.sha256(repr(token).encode()).hexdigest():
        return
    metric = report.get('metric')
    if isinstance(metric, str) and metric in METRICS - {'manual_summaries'}:
        record_activity(metric, token=token)
    if 'client' in report:
        record_client(report.get('client_id'), report['client'], token=token)


def record_activity(metric: str, *, token=None) -> None:
    """Increment one counter, capped at the final bucket. Never affect the feature."""
    if metric not in METRICS:
        return
    try:
        with _collection_txn(token) as txn:
            if txn is None:
                return
            entry = txn.data['days'].setdefault(utc_today().isoformat(), {})
            counts = entry.setdefault('usage', {})
            if counts.get(metric, 0) >= 101:
                return
            counts[metric] = min(101, counts.get(metric, 0) + 1)
            _prune(txn.data)
            txn.write()
    except Exception:
        logger.debug('Cannot record telemetry activity', exc_info=True)


def normalize_client(info: object) -> dict | None:
    if not isinstance(info, dict):
        return None
    browser, device, locale = info.get('browser'), info.get('device_type'), info.get('locale')
    if not isinstance(browser, str) or browser not in BROWSERS:
        return None
    if device not in ('desktop', 'mobile') or not isinstance(locale, str) or locale not in LOCALES:
        return None
    result = {'browser': browser, 'device_type': device, 'locale': locale}
    if browser == 'android_app':
        version, engine = info.get('app_version'), info.get('engine')
        if not isinstance(version, str) or len(version) > 64 or not _VERSION.fullmatch(version):
            return None
        if engine not in ('gecko', 'webview'):
            return None
        result.update(device_type='mobile', app_version=version, engine=engine)
    return result


def record_client(client_id: object, info: object, *, token=None) -> None:
    """Count profiles, not tabs. Only a daily salted digest stays on disk."""
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 96:
        return
    normalized = normalize_client(info)
    if normalized is None:
        return
    try:
        with _collection_txn(token) as txn:
            if txn is None:
                return
            entry = txn.data['days'].setdefault(utc_today().isoformat(), {})
            salt = entry.setdefault('client_salt', secrets.token_hex(16))
            digest = hashlib.sha256(f'{salt}:{client_id}'.encode()).hexdigest()
            clients = entry.setdefault('clients', {})
            locales = set(clients.get(digest, {}).get('locales', []))
            locales.add(normalized.pop('locale'))
            updated = {**normalized, 'locales': sorted(locales)}
            if clients.get(digest) == updated:
                return
            clients[digest] = updated
            _prune(txn.data)
            txn.write()
    except Exception:
        logger.debug('Cannot record telemetry client', exc_info=True)


def client_payload(day_state: dict) -> dict:
    """Explicit projection: no state keys, digests, salt, or raw identifiers escape."""
    counts = Counter()
    locales = set()
    for client in day_state.get('clients', {}).values():
        locales.update(value for value in client.get('locales', []) if value in LOCALES)
        fields = ('browser', 'device_type', 'app_version', 'engine')
        description = tuple((key, client[key]) for key in fields if key in client)
        counts[description] += 1
    return {'devices_count': sum(counts.values()), 'ui_locales': sorted(locales),
            'devices': [{**dict(description), 'count': count} for description, count in sorted(counts.items())]}
