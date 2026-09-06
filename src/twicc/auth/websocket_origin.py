"""Reject browser WebSockets opened by a different site's page.

The UI, terminals, and live shares all connect to location.host. Proxies must
preserve Host, as required by PublicOriginGate. Never use forwarding headers
to turn a foreign Origin into an allowed one.
"""

import re

from twicc.core.services.origin_policy import RequestAuthority, parse_request_authority

_BROWSER_ORIGIN = re.compile(r"(https?)://([^/?#]+)", re.IGNORECASE)


def websocket_origin_allowed(scope, authority: RequestAuthority) -> bool:
    origins = [value for name, value in scope.get("headers", ()) if name == b"origin"]
    if not origins:
        # RFC 6455 §4.1: browsers send Origin; native clients may omit it.
        # This grants no authentication: the consumers still enforce it.
        return True
    if len(origins) != 1:
        return False
    match = _BROWSER_ORIGIN.fullmatch(origins[0].decode("latin1"))
    if not match:
        return False
    scheme, raw_authority = match.groups()
    scheme = scheme.lower()
    origin = parse_request_authority(raw_authority)
    if origin is None:
        return False
    if scope.get("scheme") == "wss" and scheme != "https":
        return False
    # An HTTPS page may reach plain ASGI ws after TLS termination at a proxy.
    # Compare the public authorities using the browser's effective default port.
    default_port = ":443" if scheme == "https" else ":80"
    return origin.authority.removesuffix(default_port) == authority.authority.removesuffix(default_port)
