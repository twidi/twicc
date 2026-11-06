"""Validate the selected direct FCM Web Push transport."""

import base64
from urllib.parse import urlsplit


def _key(value, length):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Invalid push encryption keys.")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if len(decoded) != length or (length == 65 and decoded[0] != 4):
        raise ValueError("Invalid push encryption keys.")
    return value


def subscription(value):
    if not isinstance(value, dict):
        raise TypeError("Invalid push registration.")
    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise ValueError("Invalid push address.")
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or url.netloc != "fcm.googleapis.com"
        or not url.path.startswith("/fcm/send/")
        or url.path == "/fcm/send/"
        or url.query
        or url.fragment
    ):
        raise ValueError("Android notifications require a Google FCM push address.")
    keys = value.get("keys")
    if not isinstance(keys, dict):
        raise TypeError("Invalid push encryption keys.")
    return {"endpoint": endpoint, "keys": {"p256dh": _key(keys.get("p256dh"), 65), "auth": _key(keys.get("auth"), 16)}}
