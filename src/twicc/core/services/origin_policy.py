"""Pure origin-routing policy for the common public-origin gate.

Three layers, all pure and independently testable (design §9):

- request-authority parsing (§8): the raw ``Host`` header value → a canonical
  :class:`RequestAuthority`, under the strict ASCII hostname contract (§5.1);
- recognition (§11): best-effort extraction of the authority inside an INVALID
  stored setting, so a broken setting can still quarantine its surface;
- policy building + request classification (§10-§11): the three live settings
  → an :class:`OriginPolicy`; one request's authority/path/protocol → a
  routing surface.

The ASGI executor lives in ``twicc.origin_gate``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from twicc.core.services.public_origin import _TRIM_CHARS, canonicalize_hostname

# Served on the share host. /_twicc/artifact-shell/, the broker shim and the
# artifact theme stylesheets are shared with the working app's own artifact
# preview, so they are allowed on the share host but must NOT be hidden on the
# working origin — only /share/ and /_twicc/share/ are share-exclusive.
SHARE_ONLY_PREFIXES = (
    "/share/",
    "/_twicc/share/",
    "/_twicc/artifact-shell/",
    "/_twicc/artifact-broker-shim.js",
    "/_twicc/artifact-theme/",
)
# Share-exclusive: hidden (404) on any non-share routing authority.
SHARE_EXCLUSIVE_PREFIXES = ("/share/", "/_twicc/share/")

_SCHEME_PREFIX_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BRACKETED_AUTHORITY_RE = re.compile(r"^\[([^\]]*)\](?::([0-9]+))?$")
_PLAIN_AUTHORITY_RE = re.compile(r"^([^:\[\]]+)(?::([0-9]+))?$")


class RequestAuthority(NamedTuple):
    hostname: str
    authority: str


def parse_request_authority(value: str) -> RequestAuthority | None:
    """Parse one raw authority token per §8. ``None`` for any invalid input.

    Strict on purpose: no trimming, no percent decoding, no IDNA conversion,
    IPv6 requires brackets, a trailing colon is malformed, and the explicit
    port is preserved (the request side cannot know the original scheme, so it
    never strips a default port).
    """
    match = _BRACKETED_AUTHORITY_RE.fullmatch(value)
    bracketed = match is not None
    if match is None:
        match = _PLAIN_AUTHORITY_RE.fullmatch(value)
    if match is None:
        return None
    host_token, port_token = match.group(1), match.group(2)
    canonical = canonicalize_hostname(host_token, bracketed=bracketed)
    if canonical.hostname is None:
        return None
    port = None
    if port_token is not None:
        # Bound conversion by the significant decimal spelling. Leading zeroes
        # stay valid, but int() receives at most five digits.
        significant = port_token.lstrip("0") or "0"
        if len(significant) > 5 or (len(significant) == 5 and significant > "65535"):
            return None
        port = int(significant)
    serialized = f"[{canonical.hostname}]" if canonical.is_ipv6 else canonical.hostname
    authority = f"{serialized}:{port}" if port is not None else serialized
    return RequestAuthority(canonical.hostname, authority)


def request_authority_from_scope(scope) -> RequestAuthority | None:
    """The scope's routing authority, or ``None`` unless EXACTLY one ``Host``
    header is present and valid (§8)."""
    values = [value for name, value in scope.get("headers") or () if name == b"host"]
    if len(values) != 1:
        return None
    return parse_request_authority(values[0].decode("latin1"))


def recognize_authority(value) -> RequestAuthority | None:
    """§11 recognition: extract the authority a broken setting still names.

    Mechanical only — strip the whitespace the settings parser strips, drop one
    explicit ``scheme://`` prefix (any scheme: an unsupported scheme can leave
    the authority recognizable), cut at the first ``/``, ``?`` or ``#``, refuse
    userinfo, then apply the SAME strict token parsing as a ``Host`` header.
    Recognition never decodes percent escapes, never converts Unicode, and
    never guesses missing host syntax.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip(_TRIM_CHARS)
    raw = _SCHEME_PREFIX_RE.sub("", raw, count=1)
    raw = re.split(r"[/?#]", raw, maxsplit=1)[0]
    if not raw or "@" in raw:
        return None
    return parse_request_authority(raw)


class OriginPolicy(NamedTuple):
    """Routing policy computed from the three live origin settings (§10-§11).

    ``share_hostname is None`` means the Share surface is disabled;
    ``dedicated_peer_authority`` and ``shared_peer_authority`` both ``None``
    mean the Peer surface is disabled (at most one is ever set). The quarantine
    sets fail closed: a hostname entry matches EVERY port, an authority entry
    matches exactly. The exact valid External authority takes precedence over
    both sets — enforced by ``classify_request`` order for hostnames and by a
    builder-side discard for authorities.
    """

    external_authority: str | None
    share_hostname: str | None
    dedicated_peer_authority: str | None
    shared_peer_authority: str | None
    quarantined_hostnames: frozenset[str]
    quarantined_authorities: frozenset[str]


def build_origin_policy(public_raw, share_raw, peer_raw) -> OriginPolicy:
    """Pure §11 policy: normalize the three settings, derive conflict operands
    (a valid setting contributes its own hostname/authority, a recognizable
    invalid one its recognized ones), union every disable/quarantine rule, then
    apply the valid-External precedence to the authority set."""
    from twicc.core.services.public_origin import normalize_public_origin

    external = normalize_public_origin(public_raw)
    share = normalize_public_origin(share_raw)
    peer = normalize_public_origin(peer_raw)

    external_valid = bool(external.value)
    share_valid = bool(share.value)
    peer_valid = bool(peer.value)
    external_invalid = external.value is None
    share_invalid = share.value is None
    peer_invalid = peer.value is None

    def _operand(result, raw, valid):
        if valid:
            return RequestAuthority(result.hostname, result.authority)
        if result.value == "":
            return None
        return recognize_authority(raw)

    external_op = _operand(external, public_raw, external_valid)
    share_op = _operand(share, share_raw, share_valid)
    peer_op = _operand(peer, peer_raw, peer_valid)

    share_enabled = share_valid
    peer_enabled = peer_valid
    quarantined_hostnames: set[str] = set()
    quarantined_authorities: set[str] = set()

    if share_invalid:
        share_enabled = False
        if share_op:
            quarantined_hostnames.add(share_op.hostname)
    if peer_invalid:
        peer_enabled = False
        if peer_op:
            quarantined_authorities.add(peer_op.authority)
    if external_invalid:
        # An invalid non-empty External leaves Peer unclassifiable (§11).
        peer_enabled = False
        if peer_op:
            quarantined_authorities.add(peer_op.authority)
    if share_op and external_op and share_op.hostname == external_op.hostname:
        share_enabled = False
        quarantined_hostnames.add(share_op.hostname)
    if share_op and peer_op and share_op.hostname == peer_op.hostname:
        share_enabled = False
        peer_enabled = False
        quarantined_hostnames.add(share_op.hostname)
        quarantined_authorities.add(peer_op.authority)
    if (
        peer_op
        and external_op
        and peer_op.authority == external_op.authority
        and not (peer_valid and external_valid and peer.value == external.value)
    ):
        # Same routing authority without two equal valid origins: ambiguous.
        peer_enabled = False
        quarantined_authorities.add(peer_op.authority)

    external_authority = external.authority if external_valid else None
    if external_authority is not None:
        # Valid-External precedence (§11): the exact External authority keeps
        # serving the app. Hostname candidates keep matching other ports, so
        # they stay in the set; classify_request checks External first.
        quarantined_authorities.discard(external_authority)

    shared_peer_authority = None
    dedicated_peer_authority = None
    if peer_enabled:
        if external_valid and peer.value == external.value:
            shared_peer_authority = peer.authority
        else:
            dedicated_peer_authority = peer.authority

    return OriginPolicy(
        external_authority=external_authority,
        share_hostname=share.hostname if share_enabled else None,
        dedicated_peer_authority=dedicated_peer_authority,
        shared_peer_authority=shared_peer_authority,
        quarantined_hostnames=frozenset(quarantined_hostnames),
        quarantined_authorities=frozenset(quarantined_authorities),
    )


_policy_cache: tuple[tuple, OriginPolicy] | None = None


def get_origin_policy(settings: dict) -> OriginPolicy:
    """Memoized policy for the current settings; rebuilt whenever any of the
    three raw values changes, so a successful Apply routes the next request."""
    global _policy_cache
    key = (settings.get("publicBaseUrl"), settings.get("shareBaseUrl"), settings.get("peerBaseUrl"))
    if _policy_cache is not None and _policy_cache[0] == key:
        return _policy_cache[1]
    policy = build_origin_policy(*key)
    _policy_cache = (key, policy)
    return policy


def _app_surface(policy: OriginPolicy, authority: RequestAuthority, path: str, scope_type: str) -> str:
    """Path rules for the full-application surface: hide the share-exclusive
    surface everywhere, serve /peer/ only on the exact shared authority."""
    if scope_type == "websocket":
        return "reject" if path.startswith("/ws/share/") else "inner_app"
    if any(path.startswith(prefix) for prefix in SHARE_EXCLUSIVE_PREFIXES):
        return "reject"
    if path.startswith("/peer/"):
        return "inner_app" if policy.shared_peer_authority == authority.authority else "reject"
    return "inner_app"


def classify_request(
    policy: OriginPolicy, authority: RequestAuthority | None, path: str, scope_type: str,
) -> str:
    """Route one request: ``"inner_app"`` | ``"share_surface"`` | ``"reject"``.

    ``"reject"`` means the plain HTTP 404 or the WebSocket 4404 close, without
    calling the inner application (§8, §11).
    """
    if authority is None:
        return "reject"
    if policy.external_authority is not None and authority.authority == policy.external_authority:
        # Valid-External precedence: checked BEFORE the quarantine sets (§11).
        return _app_surface(policy, authority, path, scope_type)
    if authority.authority in policy.quarantined_authorities or authority.hostname in policy.quarantined_hostnames:
        return "reject"
    if policy.share_hostname is not None and authority.hostname == policy.share_hostname:
        return "share_surface"
    if policy.dedicated_peer_authority is not None and authority.authority == policy.dedicated_peer_authority:
        if scope_type == "websocket":
            return "reject"
        return "inner_app" if path.startswith("/peer/") else "reject"
    return _app_surface(policy, authority, path, scope_type)
