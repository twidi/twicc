# Peer Origin Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `/peer/` only through the configured Peer address, behind one common `PublicOriginGate` that routes the External, Share, and Peer addresses with a strict ASCII hostname contract and atomic settings validation.

**Architecture:** A strict Python canonicalizer owns origin validation. A small JavaScript subset rejects only inputs that Python also rejects. A separate JavaScript guard recognizes canonical stored origins for browser consumers. A pure policy builder converts the three live settings into routing policy; a pure request classifier compares the parsed `Host` authority and path with that policy; a thin ASGI gate executes the result. Each Settings Apply sends one raw field without an optimistic store mutation. The backend validates and canonicalizes it, then broadcasts the authoritative value.

**Tech Stack:** Django 6 ASGI (Uvicorn), Channels, Python ≥ 3.13 (`ipaddress`, `idna`), orjson, pytest; Vue 3 + Pinia, node:test, WHATWG `URL`.

**Spec:** `docs/plans/2026-08-13-peer-origin-routing-design.md` (base spec commit `29938aa3`, amended by owner rulings D1 through D6 in this worktree). Common origin syntax: `docs/superpowers/specs/2026-08-13-public-origin-settings-design.md`.

**Execution worktree:** `/home/twidi/dev/twicc-poc/.worktrees/peer-system` (branch `peer-system`). Every shell command below runs from this directory. Backend tests use `TWICC_DATA_DIR=$PWD uv run --active pytest …`. `--active` is required here: the inherited main-checkout environment must run the worktree's editable source. `TWICC_DATA_DIR=$PWD` prevents `twicc.settings` import-time setup from reading or writing the production data directory before `twicc.settings_test` replaces the database. Frontend tests use `cd frontend && npm test` (or `node --test <file>` for one file).

## Global Constraints

- **Hard compatibility boundary (spec §1.1):** the Peer System has never shipped. Add NO negotiation, capability detection, fallback endpoints/formats, dual-read/dual-write, legacy parsers, schema adapters, repair/backfill/merge of Peer state, or rolling-deployment compatibility. No Peer settings migration (spec §4). Reviews must not require any of these.
- **Strict hostname contract (spec §5.1):** Python settings and request parsing own the complete contract. Hostnames are unescaped ASCII only. Percent escapes and Unicode input are invalid. Classes: `localhost`; canonical dotted-decimal IPv4; bracketed RFC 5952 IPv6; and lower-case DNS hostnames with strict LDH labels. Python validates `xn--` labels as IDNA2008 A-labels. The JavaScript form check uses only the safe rejection subset in the spec. It never rejects an input that Python accepts.
- **Routing authority (spec §5.2):** normalized hostname + remaining non-default port, never the scheme. Share routing compares hostname only (spec §5.3); Peer routing compares the exact authority.
- **Gate responses (spec §8, §11):** a rejected HTTP request gets the plain `404 Not found` response without reaching the inner application; a rejected WebSocket closes with code `4404`. A missing, duplicate, or malformed `Host` header rejects the whole request. An unreadable, malformed, or non-object settings source at cache initialization also rejects every request. A missing file and an empty JSON object remain valid defaults. The process does not observe later manual file edits until restart. A successful settings write updates the cache immediately.
- **Peer surface (spec §10):** every HTTP path starting with `/peer/`; Peer has no WebSocket surface. An empty Peer address hides `/peer/` on every authority.
- **Settings atomicity (spec §7):** an origin-setting patch validates only its changed origin fields. It validates every relationship that contains a changed field. An unchanged invalid stored origin does not block the patch and supplies no relationship operand. One patch remains atomic: rejection changes no setting and no version; acceptance increments `_version` once. The Settings UI shows only the applied field's errors in its active section and discards symmetric copies for other sections. Each visible conflict message names the other participating address.
- **The gate never repairs or writes settings and never changes Peer rows (spec §11).**
- **Language:** all code, comments, tests, strings, and docs in English.
- **Python:** ruff line-length 120; `orjson` for JSON; `NamedTuple` for immutable data; no cosmetic import aliases.
- **Tooling:** run an absent tool with `uvx <tool>`; do not skip the check or add the tool to project dependencies. A library imported by production code remains a declared project dependency.
- **Do not touch `CHANGELOG.md`** (repository rule: only on explicit user request).
- **Do not edit historical documents** under `docs/plans/` / `docs/superpowers/specs/` (including `2026-07-24-peer-messaging-design.md`). Ignore `docs/plans/2026-08-13-peer-revocation-reconnection-design.md` entirely.
- Commit messages follow the repository conventions in `CLAUDE.md` / `AGENTS.md` (Conventional Commit subject, descriptive body, co-author trailer for the running model).

## Verified runtime facts (used by the tasks below)

These were verified against Python 3.13 / Node 22 on the target machine. Later fixture cases pin the contract behavior that depends on them. The historical differential count is supporting probe evidence, not a fixture assertion:

- `str(ipaddress.IPv6Address('0:0:0:0:0:0:0:1'))` → `::1`; `'2001:DB8::1'` → `2001:db8::1`; `'::ffff:1.2.3.4'` → `::ffff:1.2.3.4` (mixed notation); `'::1.2.3.4'` → `::102:304`; `'::ffff:0:0'` → `::ffff:0.0.0.0`.
- WHATWG `URL` serializes the host in `[::ffff:1.2.3.4]` as `[::ffff:102:304]`. Task 3 removes the brackets only from its optional browser hostname hint. The frontend does not use that hint to canonicalize a submitted setting.
- Browser URL parsing uses UTS #46, not IDNA2008. Node 22 accepts and preserves `xn--e28h.example`. Its A-label decodes to U+1F600. Python `idna==3.11` rejects it with `InvalidCodepoint`. JavaScript therefore makes no A-label verdict.
- Python `urlsplit` and WHATWG `URL` have different normalization and error-order pipelines. A 68,438-input differential probe found 98 verdict, value, or code differences. The JavaScript subset does not reproduce either pipeline. It sends every deferred input to Python unchanged after the normative outer trim.
- The `idna` package (3.11, already a transitive dependency via httpx) implements IDNA2008: `idna.ulabel('xn--fa-hia')` → `faß`, `idna.alabel('faß')` → `b'xn--fa-hia'`; `idna.ulabel('xn--')` raises `IDNAError`, `idna.ulabel('xn--a-ecp')` and `idna.ulabel('xn--e28h')` raise `InvalidCodepoint` (an `IDNAError` subclass).

## Plan-level decisions (invisible to the user; recorded for reviewers)

- The pure policy/classifier code lives in a new `src/twicc/core/services/origin_policy.py`; the ASGI executor lives in a new `src/twicc/origin_gate.py`; `src/twicc/share/asgi_filter.py` is deleted (its `ShareOnlyApp` moves to `origin_gate.py` unchanged in behavior). The gate test file keeps its spec-mandated name `tests/test_share_host_gate.py`.
- The valid-External precedence (spec §11) is implemented as classifier order (the exact External authority is checked before the quarantine sets) plus, for *authority* candidates only, a builder-side discard of the External authority. Hostname quarantine candidates match every port, so they cannot be discarded as a set operation; the classifier-order rule provides the exact-authority exception for them.
- The settings cache separately latches whether the source observation that initialized it was valid. All public cache reads and writes use one reentrant lock. General settings callers can use defaults after an initial load failure. The routing reader exposes that same observation until a successful atomic write or process restart. A later manual file edit does not change the active cache or routing policy until restart. The gate also rejects a settings-read or policy-build exception. It never converts either failure into the empty/default routing policy.
- Conflict error copy restates the spec's relationship rules verbatim in sentence form (exact strings in Task 6). Structural errors keep the existing stable message.
- The JavaScript form check has one safe direction. It never rejects an input that Python accepts. It can defer any Python rejection until Apply. The backend write path and Python runtime gate prevent a deferred invalid value from entering routing.
- `usablePublicOrigin` is not form validation. It recognizes only the lexical shape of canonical backend output. Browser and Share URL consumers treat every other stored value as unset. The Python gate remains the routing authority.
- The three existing Settings sections keep their per-field Apply buttons. Each Apply validates and sends only its field. It leaves inputs in other sections untouched.
- Each Apply creates one correlation ID with the existing `generateUUID()` helper. It sends the ID with one trimmed raw origin field. The backend echoes the ID in one `synced_settings_result` frame after its accepted broadcast or rejected resync. Broadcast frames only update the store. They never resolve an Apply. A small ID-keyed map holds `{ field, input }` until the matching result arrives. `input` is always the visible text snapshot at Apply time. It is not the transmitted value. The map is not a Promise queue and does not stage fields together.
- The result frame makes every outcome independent of frame order. An accepted correction adopts the backend value. An accepted no-change result confirms the submitted value. A rejection carries its field errors. A stale rejection carries no field error and leaves the typed value available for another Apply. Back-to-back results resolve only their matching entries. An input edit discards older entries for that field, so a late result cannot replace new text or show an obsolete error.
- Recognition of an invalid setting's authority (spec §11) preserves an explicit port component, including a scheme-default port. It canonicalizes the hostname and decimal port with the same token parser used for `Host`. It never removes a port based on the invalid setting's scheme.
- The Python origin parser and JavaScript form check accept port `0`. Both reject a trailing colon with no port as malformed settings input. The request `Host` parser also rejects it. Fixture cases pin the Python settings parser, JavaScript check, and request `Host` parser.

---

### Task 1: Strict hostname canonicalization and routing authority (Python)

**Files:**
- Modify: `src/twicc/core/services/public_origin.py`
- Modify: `src/twicc/core/services/share_url.py` (remove the stale cross-language claim)
- Modify: `src/twicc/cli/share.py` (remove the stale cross-language claim)
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `frontend/src/utils/shareUrl.test.js` (temporary green-boundary bridge; Task 3 replaces it)
- Delete: `tests/fixtures/share_url_parity.json`
- Create: `tests/fixtures/share_url_backend_cases.json` (renamed backend-only fixture)
- Test: `tests/test_public_origin.py`
- Test: `tests/test_share_url_parity.py`

**Interfaces:**
- Produces: `CanonicalHostname(hostname: str | None, is_ipv6: bool = False)` NamedTuple and `canonicalize_hostname(token: str, *, bracketed: bool) -> CanonicalHostname` in `twicc.core.services.public_origin`.
- Produces: `PublicOriginResult(value: str | None, error: str | None, scheme: str | None = None, hostname: str | None = None, port: int | None = None, authority: str | None = None)` NamedTuple. `authority` is the serialized `hostname` — bracketed for IPv6 — plus `:port` when a non-default port remains.
- Produces: `normalize_public_origin(value: str | None) -> PublicOriginResult` keeps its existing signature and returns the extended `PublicOriginResult`.
- Produces: `_TRIM_CHARS: str` remains the shared settings-whitespace constant.
- Produces: temporary frontend bridge `frontend/src/utils/shareUrl.test.js` reads `tests/fixtures/share_url_backend_cases.json`, applies exactly one `LEGACY_FRONTEND_OVERRIDES` entry mapping `"U+FEFF is invalid Unicode input"` to `"https://share.example.com/share/tok123/"`, and uses each row's `expected` value otherwise.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Declare the direct `idna` dependency**

In `pyproject.toml`, replace:

```toml
    "httpx~=0.28.1",
    "json-repair~=0.59.5",
```

with:

```toml
    "httpx~=0.28.1",
    "idna~=3.11",
    "json-repair~=0.59.5",
```

(`idna` is already installed transitively via httpx; declaring it makes the new direct import explicit. `uv run` re-syncs the environment automatically; at the end of the plan, remind the user that a dependency was declared.)

Run: `uv lock`
Expected: `uv.lock` keeps the resolved `idna==3.11` package. It adds `idna` to the editable `twicc` dependency list and adds `{ name = "idna", specifier = "~=3.11" }` to `twicc` `package.metadata.requires-dist`.

Run: `uv tree --package twicc --depth 1 | rg '^[├└]── idna v3\.11(\.0)?$'`
Expected: one depth-one `idna` dependency under `twicc`. No match means the direct requirement or its lock metadata is missing.

The lock update records the direct requirement without changing the resolved package. At the end of the plan, remind the user that a dependency was declared.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_public_origin.py`:

```python
def test_canonicalize_hostname_strict_contract():
    from twicc.core.services.public_origin import canonicalize_hostname

    # localhost is case-insensitive with a fixed canonical value.
    assert canonicalize_hostname("LocalHost", bracketed=False) == ("localhost", False)
    # Canonical IPv4 only.
    assert canonicalize_hostname("192.168.1.42", bracketed=False) == ("192.168.1.42", False)
    assert canonicalize_hostname("192.168.001.1", bracketed=False).hostname is None
    assert canonicalize_hostname("1.2.3", bracketed=False).hostname is None
    # Bracketed IPv6 canonicalizes to lower-case compressed RFC 5952.
    assert canonicalize_hostname("0:0:0:0:0:0:0:1", bracketed=True) == ("::1", True)
    assert canonicalize_hostname("2001:DB8::1", bracketed=True) == ("2001:db8::1", True)
    assert canonicalize_hostname("::ffff:1.2.3.4", bracketed=True) == ("::ffff:1.2.3.4", True)
    assert canonicalize_hostname("1.2.3.4", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%eth0", bracketed=True).hostname is None
    assert canonicalize_hostname("fe80::1%25eth0", bracketed=True).hostname is None
    # DNS grammar: LDH labels, alphanumeric edges, 1-63 chars per label.
    assert canonicalize_hostname("Example.COM", bracketed=False) == ("example.com", False)
    assert canonicalize_hostname("devbox", bracketed=False) == ("devbox", False)
    assert canonicalize_hostname("a..example", bracketed=False).hostname is None
    assert canonicalize_hostname("example.com.", bracketed=False).hostname is None
    assert canonicalize_hostname("-a.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a-.example", bracketed=False).hostname is None
    assert canonicalize_hostname("my_host.example", bracketed=False).hostname is None
    assert canonicalize_hostname("a" * 63 + ".example", bracketed=False).hostname == "a" * 63 + ".example"
    assert canonicalize_hostname("a" * 64 + ".example", bracketed=False).hostname is None
    long_253 = ".".join(["a" * 63] * 3 + ["a" * 61])
    assert canonicalize_hostname(long_253, bracketed=False).hostname == long_253
    assert canonicalize_hostname(long_253 + "a", bracketed=False).hostname is None
    # ASCII only: Unicode and percent escapes are invalid, never converted.
    assert canonicalize_hostname("exämple.com", bracketed=False).hostname is None
    assert canonicalize_hostname("%65xample.com", bracketed=False).hostname is None
    # A-labels: valid IDNA2008 round-trips survive, malformed ones are invalid.
    assert canonicalize_hostname("XN--FA-HIA.de", bracketed=False) == ("xn--fa-hia.de", False)
    assert canonicalize_hostname("xn--a-ecp.example", bracketed=False).hostname is None
    assert canonicalize_hostname("xn--e28h.example", bracketed=False).hostname is None


def test_normalize_public_origin_strict_hostnames():
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").value == "https://[::1]:8443"
    assert normalize_public_origin("HTTPS://XN--FA-HIA.DE").value == "https://xn--fa-hia.de"
    assert normalize_public_origin("https://xn--.example").error == "host"
    assert normalize_public_origin("https://exämple.com").error == "host"
    assert normalize_public_origin("https://%65xample.com").error == "host"
    assert normalize_public_origin("https://[1.2.3.4]").error == "host"
    assert normalize_public_origin("https://example.com:").error == "port"
    assert normalize_public_origin("https://exa\tmple.com").error == "host"
    assert normalize_public_origin("https://example.com:8\t0").error == "port"
    assert normalize_public_origin("https://example.com?").error == "query"
    assert normalize_public_origin("https://example.com#").error == "fragment"
    assert normalize_public_origin("https://example.com?\t#").error == "query"
    assert normalize_public_origin("https://example.com#x?y").error == "fragment"


def test_normalized_origin_exposes_routing_authority():
    assert normalize_public_origin("https://Example.com:443").authority == "example.com"
    assert normalize_public_origin("https://Example.com:8443").authority == "example.com:8443"
    assert normalize_public_origin("http://example.com").authority == "example.com"
    assert normalize_public_origin("https://[0:0:0:0:0:0:0:1]:8443").authority == "[::1]:8443"
    assert normalize_public_origin("").authority is None
    assert normalize_public_origin("ftp://x.example").authority is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_public_origin.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'canonicalize_hostname'`. If this passes, the test was not added or the module already changed; stop and re-check.

- [ ] **Step 4: Implement the canonicalizer and authority**

In `src/twicc/core/services/public_origin.py`, replace the import block:

```python
from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple
from urllib.parse import SplitResult, urlsplit
```

with:

```python
from __future__ import annotations

import ipaddress
import re
from typing import NamedTuple
from urllib.parse import SplitResult, urlsplit

import idna
```

Replace the `PublicOriginResult` class:

```python
class PublicOriginResult(NamedTuple):
    value: str | None
    error: str | None
    scheme: str | None = None
    hostname: str | None = None
    port: int | None = None
```

with:

```python
class PublicOriginResult(NamedTuple):
    value: str | None
    error: str | None
    scheme: str | None = None
    hostname: str | None = None
    port: int | None = None
    authority: str | None = None


class CanonicalHostname(NamedTuple):
    hostname: str | None
    is_ipv6: bool = False


_DNS_HOSTNAME_MAX_LENGTH = 253
_DNS_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def _valid_alabel(label: str) -> bool:
    """True when ``label`` (lower-case, ``xn--``-prefixed) is a valid IDNA2008 A-label."""
    try:
        return idna.alabel(idna.ulabel(label)).decode("ascii") == label
    except (idna.IDNAError, UnicodeError):
        return False


def canonicalize_hostname(token: str, *, bracketed: bool) -> CanonicalHostname:
    """Canonicalize one raw hostname token per the strict ASCII contract (design §5.1).

    ``bracketed`` says whether the source spelled the token inside ``[…]``:
    brackets require a valid IPv6 literal, canonicalized to the lower-case
    compressed RFC 5952 form. No percent decoding, no Unicode-to-IDNA
    conversion — an invalid raw token stays invalid.
    """
    if not token or not token.isascii() or "%" in token or not all(0x21 <= ord(char) <= 0x7e for char in token):
        return CanonicalHostname(None)
    lowered = token.lower()
    if bracketed:
        try:
            return CanonicalHostname(str(ipaddress.IPv6Address(lowered)), True)
        except ValueError:
            return CanonicalHostname(None)
    if lowered == "localhost":
        return CanonicalHostname("localhost")
    if re.fullmatch(r"[0-9.]+", lowered):
        try:
            ipaddress.IPv4Address(lowered)
        except ValueError:
            return CanonicalHostname(None)
        return CanonicalHostname(lowered)
    if len(lowered) > _DNS_HOSTNAME_MAX_LENGTH:
        return CanonicalHostname(None)
    for label in lowered.split("."):
        if not _DNS_LABEL_RE.fullmatch(label):
            return CanonicalHostname(None)
        if label.startswith("xn--") and not _valid_alabel(label):
            return CanonicalHostname(None)
    return CanonicalHostname(lowered)
```

Replace the whole `_origin` function:

```python
def _origin(parsed: SplitResult) -> PublicOriginResult:
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if re.fullmatch(r"[0-9.]+", hostname):
        try:
            hostname = str(ipaddress.IPv4Address(hostname))
        except ipaddress.AddressValueError:
            return PublicOriginResult(None, "host")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return PublicOriginResult(None, "host")
    serialized_host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    suffix = f":{port}" if port is not None else ""
    return PublicOriginResult(
        f"{scheme}://{serialized_host}{suffix}",
        None,
        scheme,
        ascii_hostname,
        port,
    )
```

with:

```python
def _origin(parsed: SplitResult, canonical: CanonicalHostname) -> PublicOriginResult:
    scheme = parsed.scheme.lower()
    serialized_host = f"[{canonical.hostname}]" if canonical.is_ipv6 else canonical.hostname
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    suffix = f":{port}" if port is not None else ""
    authority = f"{serialized_host}{suffix}"
    return PublicOriginResult(
        f"{scheme}://{authority}",
        None,
        scheme,
        canonical.hostname,
        port,
        authority,
    )
```

Replace the whole current `_parse` function:

```python
def _parse(value: str | None) -> tuple[str, SplitResult | None, str | None]:
    raw = (value or "").strip(_TRIM_CHARS)
    if not raw:
        return raw, None, None
    candidate, error = _candidate(raw)
    if error:
        return raw, None, error
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        error = "port" if "port" in str(exc).lower() else "host"
        return raw, None, error
    if parsed.scheme.lower() not in ("http", "https"):
        return raw, None, "scheme"
    if not hostname or any(char.isspace() for char in hostname):
        return raw, None, "host"
    try:
        parsed.port
    except ValueError:
        return raw, None, "port"
    if parsed.username is not None or parsed.password is not None:
        return raw, None, "credentials"
    return raw, parsed, None
```

with these raw-authority and parse helpers:

```python
class _RawAuthority(NamedTuple):
    hostname: str
    port: str | None
    bracketed: bool


def _raw_authority(candidate: str) -> tuple[_RawAuthority | None, str | None]:
    """Extract raw tokens before ``urlsplit`` can remove control characters."""
    authority = re.match(r"^https?://([^/?#]*)", candidate, re.IGNORECASE).group(1)
    host_port = authority.rsplit("@", 1)[-1]
    if host_port.startswith("["):
        match = re.fullmatch(r"\[([^\]]*)\](?::(.*))?", host_port)
        if match is None:
            return None, "host"
        hostname, port = match.group(1), match.group(2)
        bracketed = True
    else:
        if host_port.count(":") > 1:
            return None, "host"
        hostname, separator, port = host_port.partition(":")
        port = port if separator else None
        bracketed = False
    if not hostname:
        return None, "host"
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f for char in hostname):
        return None, "host"
    if port == "" or (port is not None and re.fullmatch(r"[0-9]+", port) is None):
        return None, "port"
    return _RawAuthority(hostname, port, bracketed), None


def _parse(
    value: str | None,
) -> tuple[str, SplitResult | None, CanonicalHostname | None, str | None]:
    raw = (value or "").strip(_TRIM_CHARS)
    if not raw:
        return raw, None, None, None
    candidate, error = _candidate(raw)
    if error:
        return raw, None, None, error
    authority, error = _raw_authority(candidate)
    if error:
        return raw, None, None, error
    canonical = canonicalize_hostname(authority.hostname, bracketed=authority.bracketed)
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        error = "port" if "port" in str(exc).lower() else "host"
        return raw, None, None, error
    if parsed.scheme.lower() not in ("http", "https"):
        return raw, None, None, "scheme"
    if not hostname:
        return raw, None, None, "host"
    try:
        parsed.port
    except ValueError:
        return raw, None, None, "port"
    if parsed.username is not None or parsed.password is not None:
        return raw, None, None, "credentials"
    return raw, parsed, canonical, None
```

The raw helper rejects an explicit empty port and an unbracketed IPv6 spelling. It also stops `urlsplit` from laundering authority control characters. It keeps port `0` valid. `_parse` still calls `urlsplit` before it returns a strict-host error. This preserves Python's early authority errors before the suffix and strict raw-host checks.

In `normalize_public_origin`, unpack `raw, parsed, canonical, error`. Then replace this exact suffix block:

```python
    if parsed.path not in ("", "/"):
        return PublicOriginResult(None, "path")
    if parsed.query:
        return PublicOriginResult(None, "query")
    if parsed.fragment:
        return PublicOriginResult(None, "fragment")
    return _origin(parsed)
```

with:

```python
    if parsed.path not in ("", "/"):
        return PublicOriginResult(None, "path")
    query_index = raw.find("?")
    fragment_index = raw.find("#")
    if query_index >= 0 and (fragment_index < 0 or query_index < fragment_index):
        return PublicOriginResult(None, "query")
    if fragment_index >= 0:
        return PublicOriginResult(None, "fragment")
    if canonical.hostname is None:
        return PublicOriginResult(None, "host")
    return _origin(parsed, canonical)
```

The raw delimiter positions distinguish an absent suffix from an empty suffix. They also stop `urlsplit` from laundering TAB, LF, or CR inside that suffix. The first suffix delimiter selects the error. A `?` inside a fragment remains part of that fragment.

In `repair_legacy_public_origin`, unpack `raw, parsed, canonical, error`. Keep its deliberate suffix removal. Before its final return, reject `canonical.hostname is None`. Replace `return _origin(parsed)` with `return _origin(parsed, canonical)`.

Replace the complete current module docstring. It starts with this exact unique line:

```python
"""Normalize the three synced public-origin settings.
```

with:

```python
"""Authoritative normalization for the three synced public-origin settings.

The frontend performs only the safe subset defined by the public-origin
design. Backend and frontend fixture sections have explicit separate scopes.
"""
```

- [ ] **Step 5: Rename the Share fixture and bridge the existing frontend reader**

Rename `tests/fixtures/share_url_parity.json` to `tests/fixtures/share_url_backend_cases.json`.

In `tests/fixtures/share_url_backend_cases.json`, replace this exact line:

```json
    "_comment": "Parity cases for the mirrored share URL builders (agent-sharing design §7.4). Consumed by tests/test_share_url_parity.py AND frontend/src/utils/shareUrl.test.js — same input, byte-identical expected output on both surfaces.",
```

with:

```json
    "_comment": "Backend legacy-input and Share URL regression cases (agent-sharing design §7.4). Python owns every verdict. Frontend stored consumers use direct canonical-shape tests after Task 3.",
```

Replace this exact row in the renamed fixture:

```json
        {"name": "U+FEFF is parser-normalized", "stored": "\ufeffshare.example.com", "expected": "https://share.example.com/share/tok123/"},
```

with:

```json
        {"name": "U+FEFF is invalid Unicode input", "stored": "\ufeffshare.example.com", "expected": null},
```

The strict Python parser rejects U+FEFF. The pre-Task-3 JavaScript normalizer still accepts it. The temporary frontend bridge below records that one difference. It prevents a false cross-language claim and keeps the Task 1 and Task 2 commit boundaries green. Task 3 removes the bridge when it replaces the JavaScript normalizer and this test.

In `tests/test_share_url_parity.py`, replace this exact docstring:

```python
"""The §7.4 parity fixture, Python side. The SAME file drives
frontend/src/utils/shareUrl.test.js — never edit one side's expectations."""
```

with:

```python
"""Backend legacy-input and Share URL regression cases from design §7.4.

Frontend stored consumers have a narrower fail-closed contract and use direct
tests. The historical filename remains unchanged.
"""
```

Replace this exact function line:

```python
def test_build_share_url_parity(case):
```

with:

```python
def test_build_share_url_backend_legacy_cases(case):
```

Replace this exact fixture load:

```python
FIXTURE = orjson.loads(
    (Path(__file__).parent / "fixtures" / "share_url_parity.json").read_bytes()
)
```

with:

```python
FIXTURE = orjson.loads(
    (Path(__file__).parent / "fixtures" / "share_url_backend_cases.json").read_bytes()
)
```

In `src/twicc/core/services/share_url.py`, replace this exact module docstring:

```python
"""Build fail-closed Share URLs from the common public-origin contract.

Mirrored with ``frontend/src/utils/shareUrlCore.js`` and covered by
``tests/fixtures/share_url_parity.json``.
"""
```

with:

```python
"""Build fail-closed Share URLs from backend-normalized public origins.

The Python regression fixture covers legacy repair and backend URL construction.
Frontend stored consumers use their own narrower canonical-shape tests.
"""
```

In `src/twicc/cli/share.py`, replace this exact module docstring:

```python
"""``twicc share`` (list) / ``show`` — read-only, direct DB (works with the server
down). ``url`` follows the §7.4 parity contract of the agent-sharing design:
byte-identical to the URL the owner UI shows for the same share (mirrored
builder ``core/services/share_url.py`` ↔ ``frontend/src/utils/shareUrlCore.js``).
With ``shareBaseUrl`` unset, prints the relative ``/share/<token>/`` path
(links only resolve on the dedicated share origin)."""
```

with:

```python
"""``twicc share`` (list) / ``show`` — read-only, direct DB (works with the server
down). ``url`` uses the backend Share URL builder. With ``shareBaseUrl`` unset
or unusable, unredacted rows use the relative ``/share/<token>/`` path. Links
only resolve on the dedicated Share origin."""
```

Replace the ENTIRE current content of `frontend/src/utils/shareUrl.test.js`:

```js
// The §7.4 parity fixture, JS side — driven by the SAME file as
// tests/test_share_url_parity.py. Never edit one side's expectations.
// Imports the dependency-free core module: shareUrl.js pulls the Pinia
// store and is not importable under node --test.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const fixture = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/share_url_parity.json', import.meta.url), 'utf8'),
)

for (const c of fixture.cases) {
    test(`parity: ${c.name}`, () => {
        assert.equal(buildShareUrl(c.stored, fixture.url_path), c.expected)
    })
}

test('empty base stays empty after normalization', () => {
    assert.equal(normalizeShareBase(''), '')
    assert.equal(normalizeShareBase('   '), '')
})

test('invalid share base fails closed after normalization', () => {
    assert.equal(normalizeShareBase('ftp://share.example.com'), '')
})
```

with this temporary bridge:

```js
// Temporary Task 1 boundary bridge. The renamed fixture now records backend
// verdicts. Task 3 replaces this legacy frontend-normalization suite.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const fixture = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/share_url_backend_cases.json', import.meta.url), 'utf8'),
)

const LEGACY_FRONTEND_OVERRIDES = new Map([
    ['U+FEFF is invalid Unicode input', 'https://share.example.com/share/tok123/'],
])

for (const c of fixture.cases) {
    test(`legacy frontend normalization before Task 3: ${c.name}`, () => {
        const expected = LEGACY_FRONTEND_OVERRIDES.get(c.name) ?? c.expected
        assert.equal(buildShareUrl(c.stored, fixture.url_path), expected)
    })
}

test('empty base stays empty after normalization', () => {
    assert.equal(normalizeShareBase(''), '')
    assert.equal(normalizeShareBase('   '), '')
})

test('invalid share base fails closed after normalization', () => {
    assert.equal(normalizeShareBase('ftp://share.example.com'), '')
})
```

The override is temporary and exact. It does not weaken the backend fixture. It keeps the current frontend behavior under test until Task 3 changes that behavior.

- [ ] **Step 6: Run the parser, its consumers, and the full frontend suite**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_public_origin.py tests/test_settings_mutation.py tests/test_share_host_gate.py tests/test_share_url_parity.py -q`
Expected: PASS. The strict parser and every current backend consumer use the narrowed backend fixture. A fixture mismatch fails this command.

Run: `cd frontend && npm test`
Expected: PASS with zero failures. This proves the temporary bridge keeps the frontend suite green after the rename. Its U+FEFF override fails if the bridge accidentally applies the backend-only verdict to the old frontend normalizer.

- [ ] **Step 7: Verify the rename has no operational survivor**

Run: `test ! -e tests/fixtures/share_url_parity.json && test -e tests/fixtures/share_url_backend_cases.json && ! rg -n "share_url_parity\.json" src frontend/src tests`
Expected: no output and exit 0. The old fixture name has no active reader or stale source comment. The historical Python test filename remains unchanged.

Run:

```bash
! rg -ni '\bparity\b|mirrored' \
    src/twicc/cli/share.py \
    src/twicc/core/services/public_origin.py \
    src/twicc/core/services/share_url.py \
    frontend/src/utils/shareUrl.test.js \
    tests/fixtures/share_url_backend_cases.json \
    tests/test_share_url_parity.py
```

Expected: no output and exit 0. This meaning-based check covers every origin or Share identity claim that Task 1 changes. Task 3 owns the remaining frontend claims and repeats the complete final sweep after its rewrite.

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_share_url_parity.py -q`
Expected: PASS. This suite continues to pin Python legacy repair and backend URL construction. It makes no cross-language identity claim.

- [ ] **Step 8: Commit**

Commit the changes produced by this task.
Subject: `feat(origin): enforce the strict ascii hostname contract in the python origin parser`

---

### Task 2: Cross-address classification and merged-settings validation helpers (Python)

**Files:**
- Modify: `src/twicc/core/services/public_origin.py`
- Test: `tests/test_public_origin.py`

**Interfaces:**
- Consumes: `PublicOriginResult(value: str | None, error: str | None, scheme: str | None = None, hostname: str | None = None, port: int | None = None, authority: str | None = None)` NamedTuple and `normalize_public_origin(value: str | None) -> PublicOriginResult` (Task 1).
- Produces: in `twicc.core.services.public_origin`:
  - `classify_peer_external(peer: PublicOriginResult, external: PublicOriginResult) -> str | None` — `None` when peer is not a valid non-empty origin; else `"shared"` (equal normalized origins), `"ambiguous"` (different origins, one authority), `"dedicated"` (different authority, or empty/invalid external).
  - `OriginFieldError(field: str, code: str)` NamedTuple.
  - Constants `ORIGIN_CONFLICT_SHARE_EXTERNAL = "origin_conflict_share_external_hostname"`, `ORIGIN_CONFLICT_SHARE_PEER = "origin_conflict_share_peer_hostname"`, `ORIGIN_CONFLICT_AMBIGUOUS = "origin_conflict_ambiguous_authority"`.
  - `validate_origin_settings(public_value, share_value, peer_value, *, changed_fields) -> tuple[OriginFieldError, ...]` — structural errors (`invalid_origin_<code>`) for invalid changed fields, in field order. It then checks each relationship that contains a changed field. Invalid operands do not participate. Errors follow rule order share/external, share/peer, peer/external-ambiguity and name every participating field (share first, then the other operand). A structural error does not hide an independent conflict between valid changed fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_public_origin.py`:

```python
def test_classify_peer_external():
    from twicc.core.services.public_origin import classify_peer_external

    def parse(value):
        return normalize_public_origin(value)

    assert classify_peer_external(parse("https://x.example"), parse("https://x.example")) == "shared"
    assert classify_peer_external(parse("http://x.example"), parse("https://x.example")) == "ambiguous"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example")) == "dedicated"
    assert classify_peer_external(parse("https://x.example:8443"), parse("https://x.example:7443")) == "dedicated"
    assert classify_peer_external(parse("https://x.example"), parse("")) == "dedicated"
    assert classify_peer_external(parse(""), parse("https://x.example")) is None
    assert classify_peer_external(parse("ftp://x.example"), parse("https://x.example")) is None
    # IPv6 spellings normalize before comparison: expanded peer == compressed external.
    assert classify_peer_external(parse("https://[0:0:0:0:0:0:0:1]:8443"), parse("https://[::1]:8443")) == "shared"


def test_validate_origin_settings():
    from twicc.core.services.public_origin import validate_origin_settings

    all_fields = {"publicBaseUrl", "shareBaseUrl", "peerBaseUrl"}
    assert validate_origin_settings("", "", "", changed_fields=all_fields) == ()
    assert validate_origin_settings(
        "https://app.example", "https://share.example", "https://peer.example", changed_fields=all_fields,
    ) == ()
    # Structural errors name invalid changed fields only.
    errors = validate_origin_settings(
        "ftp://app.example", "https://share.example", 42, changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("peerBaseUrl", "invalid_origin_type"),
    ]
    # An unchanged invalid field does not block another field's repair.
    assert validate_origin_settings(
        "ftp://app.example", "https://share.example", "https://peer.example",
        changed_fields={"peerBaseUrl"},
    ) == ()
    # Share/External hostname conflict names both fields; a port does not help.
    errors = validate_origin_settings(
        "https://x.example", "https://x.example:9443", "", changed_fields={"shareBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
    # Share/Peer hostname conflict.
    errors = validate_origin_settings(
        "", "https://x.example", "http://x.example:8443", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
    # Ambiguous Peer/External: same authority, different origins.
    errors = validate_origin_settings(
        "https://x.example", "", "http://x.example", changed_fields={"peerBaseUrl"},
    )
    assert [(e.field, e.code) for e in errors] == [
        ("peerBaseUrl", "origin_conflict_ambiguous_authority"),
        ("publicBaseUrl", "origin_conflict_ambiguous_authority"),
    ]
    # Shared Peer/External is valid.
    assert validate_origin_settings(
        "https://x.example", "", "https://x.example", changed_fields={"peerBaseUrl"},
    ) == ()
    # A structural error does not hide a conflict between other changed fields.
    errors = validate_origin_settings(
        "ftp://app.example", "https://x.example", "http://x.example:8443",
        changed_fields=all_fields,
    )
    assert [(e.field, e.code) for e in errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_public_origin.py -q -k "classify_peer or validate_origin"`
Expected: FAIL — `ImportError: cannot import name 'classify_peer_external'`.

- [ ] **Step 3: Implement the helpers**

Append to `src/twicc/core/services/public_origin.py` (after `usable_public_origin`):

```python
ORIGIN_CONFLICT_SHARE_EXTERNAL = "origin_conflict_share_external_hostname"
ORIGIN_CONFLICT_SHARE_PEER = "origin_conflict_share_peer_hostname"
ORIGIN_CONFLICT_AMBIGUOUS = "origin_conflict_ambiguous_authority"


class OriginFieldError(NamedTuple):
    field: str
    code: str


def classify_peer_external(peer: PublicOriginResult, external: PublicOriginResult) -> str | None:
    """Peer/External routing class (design §6.2-§6.4) for parsed settings.

    ``None`` when peer is not a valid non-empty origin. An empty or invalid
    external makes every valid peer address dedicated at this (write-path)
    level; the runtime policy layer handles invalid-external quarantine.
    """
    if not peer.value:
        return None
    if not external.value:
        return "dedicated"
    if peer.value == external.value:
        return "shared"
    if peer.authority == external.authority:
        return "ambiguous"
    return "dedicated"


def validate_origin_settings(
    public_value, share_value, peer_value, *, changed_fields,
) -> tuple[OriginFieldError, ...]:
    """Validate changed origins and their relationships (design §7).

    Unchanged invalid values do not block a patch and do not become conflict
    operands. A structural error does not hide conflicts among other valid
    changed operands. Relationship errors name every participating field.
    """
    values = {"publicBaseUrl": public_value, "shareBaseUrl": share_value, "peerBaseUrl": peer_value}
    errors: list[OriginFieldError] = []
    results: dict[str, PublicOriginResult] = {}
    for field, value in values.items():
        result = normalize_public_origin(value)
        results[field] = result
        if field in changed_fields and result.error:
            errors.append(OriginFieldError(field, f"invalid_origin_{result.error}"))
    public, share, peer = results["publicBaseUrl"], results["shareBaseUrl"], results["peerBaseUrl"]
    if (
        changed_fields & {"shareBaseUrl", "publicBaseUrl"}
        and share.value and public.value and share.hostname == public.hostname
    ):
        errors.append(OriginFieldError("shareBaseUrl", ORIGIN_CONFLICT_SHARE_EXTERNAL))
        errors.append(OriginFieldError("publicBaseUrl", ORIGIN_CONFLICT_SHARE_EXTERNAL))
    if (
        changed_fields & {"shareBaseUrl", "peerBaseUrl"}
        and share.value and peer.value and share.hostname == peer.hostname
    ):
        errors.append(OriginFieldError("shareBaseUrl", ORIGIN_CONFLICT_SHARE_PEER))
        errors.append(OriginFieldError("peerBaseUrl", ORIGIN_CONFLICT_SHARE_PEER))
    if (
        changed_fields & {"peerBaseUrl", "publicBaseUrl"}
        and classify_peer_external(peer, public) == "ambiguous"
    ):
        errors.append(OriginFieldError("peerBaseUrl", ORIGIN_CONFLICT_AMBIGUOUS))
        errors.append(OriginFieldError("publicBaseUrl", ORIGIN_CONFLICT_AMBIGUOUS))
    return tuple(errors)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_public_origin.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `feat(origin): add cross-address origin classification helpers`

---

### Task 3: Minimal origin input check and stored-value guard (JavaScript)

**Files:**
- Modify: `frontend/src/utils/publicOrigin.js` (full-file rewrite)
- Modify: `frontend/src/utils/shareUrl.js` (remove the superseded identity claim)
- Test: `frontend/src/utils/publicOrigin.test.js`
- Test: `frontend/src/utils/shareUrl.test.js` (rework in place)

**Interfaces:**
- Consumes: temporary frontend bridge `frontend/src/utils/shareUrl.test.js` reads `tests/fixtures/share_url_backend_cases.json`, applies exactly one `LEGACY_FRONTEND_OVERRIDES` entry mapping `"U+FEFF is invalid Unicode input"` to `"https://share.example.com/share/tok123/"`, and uses each row's `expected` value otherwise. (Task 1)
- Removes: the temporary frontend bridge when Task 3 replaces the whole `frontend/src/utils/shareUrl.test.js` file.
- Produces: `checkPublicOriginInput(value) -> { value: string | null, error: string | null, scheme: string | null, hostname: string | null, port: null, authority: null }`. A successful non-empty `value` is the trimmed raw input. `hostname` is an optional browser hint. It is not a validation verdict.
- Produces: `usablePublicOrigin(value) -> string`. It returns the stored value only when it has the recognizable lexical shape of canonical backend output. Otherwise it returns `""`.
- Produces: temporary compatibility export `normalizePublicOrigin = checkPublicOriginInput`. Task 11 removes it after all current callers move to the new name.
- Removes: `repairLegacyPublicOrigin`. It has no production caller. Python owns the deployed External and Share migration.

The hard rejection set is intentionally small:

1. a non-string value;
2. a protocol-relative value or unsupported explicit scheme;
3. a missing apparent authority;
4. credentials;
5. non-ASCII data, a control character, or a percent sign in the raw authority;
6. a trailing colon without a port.

Each rejection is also a Python rejection. The check defers every other verdict. It does not validate DNS labels, A-labels, a port range, suffixes, or IPv6 canonical form. It never uses `new URL()` failure as a validation error.

The browser hostname is a hint only. The Share active-location rule uses it when the browser can parse the candidate. A missing hint does not reject the input.

The stored-value guard has a different role. Backend writes already canonicalize stored values. The guard checks canonical shape without normalizing arbitrary input:

- exact lower-case `http://` or `https://`;
- no credentials, suffix, outer whitespace, Unicode, or percent escape;
- a canonical decimal port, with scheme-default ports absent;
- a lower-case LDH hostname, canonical IPv4, or bracketed compressed IPv6;
- the Python mixed IPv4-mapped IPv6 form as an explicit stored-output case.

It uses WHATWG origin identity only as a canonical-shape check. It does not return a form verdict. The explicit mixed IPv6 case covers the one known backend spelling that WHATWG rewrites. An A-label can satisfy lexical canonical shape. The guard does not decide its IDNA2008 validity.

- [ ] **Step 1: Write the failing subset and consumer tests**

Replace the ENTIRE content of `frontend/src/utils/publicOrigin.test.js` with:

```js
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    checkPublicOriginInput,
    usablePublicOrigin,
} from './publicOrigin.js'

test('the form check keeps only the safe hard rejections', () => {
    for (const [input, error] of [
        [42, 'type'],
        ['//example.com', 'scheme'],
        ['ftp://example.com', 'scheme'],
        ['https://', 'host'],
        ['https://user:secret@example.com', 'credentials'],
        ['https://exämple.com', 'host'],
        ['https://%65xample.com', 'host'],
        ['https://exa\tmple.com', 'host'],
        ['https://exa\nmple.com', 'host'],
        ['https://exa\rmple.com', 'host'],
        ['https://example.com:', 'port'],
    ]) {
        assert.equal(checkPublicOriginInput(input).error, error, String(input))
    }
})

test('the form check defers backend-only verdicts without rewriting input', () => {
    for (const input of [
        'https://a..example',
        'https://xn--e28h.example',
        'https://[xyz]',
        'https://example.com:bad',
        'https://example.com/base',
        'https://example.com?x=1',
        'https://example.com#part',
        `https://example.com:${'0'.repeat(5000)}`,
    ]) {
        assert.deepEqual(
            { value: checkPublicOriginInput(input).value, error: checkPublicOriginInput(input).error },
            { value: input, error: null },
            input,
        )
    }
})

test('port zero and the normative outer trim remain valid', () => {
    assert.equal(checkPublicOriginInput('  https://example.com:0\r\n').value, 'https://example.com:0')
})

test('the browser hostname is only an optional hint', () => {
    assert.equal(checkPublicOriginInput('HTTPS://APP.EXAMPLE').hostname, 'app.example')
    assert.equal(checkPublicOriginInput('https://[xyz]').hostname, null)
})

test('stored consumers accept recognizable canonical backend output', () => {
    for (const value of [
        'https://example.com',
        'http://localhost:3501',
        'https://192.168.1.42:8443',
        'https://[::1]:8443',
        'https://[::ffff:1.2.3.4]',
        'https://xn--fa-hia.de',
        'https://example.com:0',
    ]) {
        assert.equal(usablePublicOrigin(value), value, value)
    }
})

test('stored consumers fail closed for non-canonical or malformed text', () => {
    for (const value of [
        'HTTPS://EXAMPLE.COM',
        'https://example.com/',
        'https://example.com:443',
        'https://example.com/base',
        'https://example.com.',
        'https://a..example',
        'https://my_host.example',
        'https://192.168.001.1',
        'https://[0:0:0:0:0:0:0:1]',
        'https://%65xample.com',
        'ftp://example.com',
    ]) {
        assert.equal(usablePublicOrigin(value), '', value)
    }
})
```

Replace the ENTIRE content of `frontend/src/utils/shareUrl.test.js` with:

```js
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const URL_PATH = '/share/tok123/'

test('canonical stored HTTP and HTTPS origins build Share URLs', () => {
    for (const [stored, expected] of [
        ['https://share.example.com', 'https://share.example.com/share/tok123/'],
        ['http://share.example.com:3500', 'http://share.example.com:3500/share/tok123/'],
    ]) {
        assert.equal(buildShareUrl(stored, URL_PATH), expected, stored)
    }
})

test('non-canonical stored origins fail closed', () => {
    for (const stored of [
        'share.example.com',
        'share.example.com:8443',
        'share.example.com/',
        'https://share.example.com///',
        '\t share.example.com \r\n',
        '  share.example.com  ',
        '\ufeffshare.example.com',
        '\u001cshare.example.com',
        'Share.Example.COM',
        'https://share.example.com/',
        'HTTPS://SHARE.EXAMPLE.COM',
        'https://share.example.com?x=1',
    ]) {
        assert.equal(normalizeShareBase(stored), '', stored)
        assert.equal(buildShareUrl(stored, URL_PATH), null, stored)
    }
})

test('empty and malformed stored origins keep sharing disabled', () => {
    for (const stored of [
        '',
        '   ',
        'ftp://share.example.com',
        'https://share.example.com/base',
        'https://u:p@share.example.com',
        '://x',
    ]) {
        assert.equal(normalizeShareBase(stored), '', stored)
        assert.equal(buildShareUrl(stored, URL_PATH), null, stored)
    }
})
```

The old shared-fixture loop tested Python-style legacy normalization in JavaScript. Task 1 keeps that fixture as backend-only coverage. These tests pin the frontend stored-value guard directly.

Before implementation, sweep the complete existing frontend test tree:

Run: `cd frontend && rg -l "publicOrigin|PublicOrigin|usablePublicOrigin|normalizePublicOrigin|shareUrlCore|originSettingsForm" src --glob '*.test.js' | sort`
Expected: exactly `src/stores/publicOriginSettings.test.js`, `src/utils/publicOrigin.test.js`, and `src/utils/shareUrl.test.js`. Task 3 declares the two utility tests. Task 11 declares and rewrites the Settings source-contract test. Stop if another existing test appears, and declare its required update in the task that changes its expectation.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && node --test src/utils/publicOrigin.test.js`
Expected: FAIL — `checkPublicOriginInput` is not exported. If the import succeeds, stop and inspect the current file.

- [ ] **Step 3: Rewrite the utility**

Replace the ENTIRE content of `frontend/src/utils/publicOrigin.js` with:

```js
// The backend owns public-origin validation and canonicalization.
//
// The form check below is intentionally a small subset. It rejects only raw
// shapes that the Python parser also rejects. It submits every other value
// unchanged after the normative outer trim.
//
// Stored-value consumers use a separate canonical-shape guard. A stored value
// normally came from the backend. A hand-edited non-canonical value fails
// closed instead of becoming a Browser or Share URL.

const TRIM_RE = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g
const HTTP_SCHEME_RE = /^https?:\/\//i
const EXPLICIT_SCHEME_RE = /^[a-z][a-z0-9+.-]*:(?!\d)/i
const LOCAL_HOST_RE = /^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|(\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(local|test|localhost)|[^./:\s]+(?=:\d))(:\d+)?([/?#]|$)/i
const RAW_AUTHORITY_RE = /^[\x21-\x7e]+$/
const DNS_LABEL_RE = /^(?!-)(?!.*-$)[a-z0-9-]{1,63}$/
const MIXED_MAPPED_RE = /^(https?):\/\/\[::ffff:(\d+\.\d+\.\d+\.\d+)\](?::(0|[1-9]\d{0,4}))?$/

function failure(error) {
    return { value: null, error, scheme: null, hostname: null, port: null, authority: null }
}

function candidate(raw) {
    if (raw.startsWith('//')) return { error: 'scheme' }
    if (HTTP_SCHEME_RE.test(raw)) {
        return { value: raw, scheme: raw.slice(0, raw.indexOf(':')).toLowerCase() }
    }
    if (EXPLICIT_SCHEME_RE.test(raw)) return { error: 'scheme' }
    const scheme = LOCAL_HOST_RE.test(raw) ? 'http' : 'https'
    return { value: `${scheme}://${raw}`, scheme }
}

function rawAuthority(candidateValue) {
    const withoutScheme = candidateValue.replace(/^https?:\/\//i, '')
    return withoutScheme.split(/[/?#]/, 1)[0]
}

function hostnameHint(candidateValue) {
    try {
        const hostname = new URL(candidateValue).hostname
        return hostname.startsWith('[') ? hostname.slice(1, -1).toLowerCase() : hostname.toLowerCase()
    } catch {
        return null
    }
}

export function checkPublicOriginInput(value) {
    if (value != null && typeof value !== 'string') return failure('type')
    const raw = String(value ?? '').replace(TRIM_RE, '')
    if (!raw) {
        return { value: '', error: null, scheme: null, hostname: null, port: null, authority: null }
    }
    const prepared = candidate(raw)
    if (prepared.error) return failure(prepared.error)
    const authority = rawAuthority(prepared.value)
    if (!authority) return failure('host')
    if (!RAW_AUTHORITY_RE.test(authority) || authority.includes('%')) return failure('host')
    if (authority.includes('@')) return failure('credentials')
    if (authority.endsWith(':')) return failure('port')
    return {
        value: raw,
        error: null,
        scheme: prepared.scheme,
        hostname: hostnameHint(prepared.value),
        port: null,
        authority: null,
    }
}

// Temporary compatibility for the callers replaced in Task 11. Despite the
// historical name, this is the subset check above. It does not normalize.
export const normalizePublicOrigin = checkPublicOriginInput

function canonicalIpv4(value) {
    const parts = value.split('.')
    return parts.length === 4 && parts.every(part => {
        const number = Number(part)
        return /^\d+$/.test(part) && number <= 255 && String(number) === part
    })
}

function canonicalPort(scheme, token) {
    if (token == null) return true
    if (!/^(0|[1-9]\d{0,4})$/.test(token) || Number(token) > 65535) return false
    return !((scheme === 'https' && token === '443') || (scheme === 'http' && token === '80'))
}

function recognizableCanonicalStoredOrigin(value) {
    if (typeof value !== 'string' || !value || value !== value.replace(TRIM_RE, '')) return false
    if (!/^[\x21-\x7e]+$/.test(value) || value.includes('%') || !/^https?:\/\//.test(value)) return false

    const mixed = value.match(MIXED_MAPPED_RE)
    if (mixed) {
        return canonicalIpv4(mixed[2]) && canonicalPort(mixed[1], mixed[3])
    }

    let url
    try {
        url = new URL(value)
    } catch {
        return false
    }
    if (url.origin !== value || !['http:', 'https:'].includes(url.protocol)) return false

    if (url.hostname.startsWith('[')) {
        return /^\[[0-9a-f:.]+\]$/.test(url.hostname)
    }
    const hostname = url.hostname
    if (hostname === 'localhost') return true
    if (/^[0-9.]+$/.test(hostname)) return canonicalIpv4(hostname)
    return hostname.length <= 253 && hostname.split('.').every(label => DNS_LABEL_RE.test(label))
}

export function usablePublicOrigin(value) {
    return recognizableCanonicalStoredOrigin(value) ? value : ''
}
```

In `frontend/src/utils/shareUrl.js`, replace this exact comment:

```js
// Re-export the parity pair so app code keeps one import point; the
// algorithm lives in shareUrlCore.js (dependency-free, node-testable).
```

with:

```js
// Re-export the core builders so app code keeps one import point. The
// implementation lives in shareUrlCore.js (dependency-free, node-testable).
```

The temporary alias keeps Tasks 3 through 10 green. It does not restore parser behavior. Task 11 removes the alias and every caller.

- [ ] **Step 4: Run the focused and full frontend suites**

Run: `cd frontend && node --test src/utils/publicOrigin.test.js src/utils/shareUrl.test.js && npm test`
Expected: PASS with zero failures. The focused tests fail if a hard rejection expands, if a deferred value is rewritten, or if a non-canonical stored value becomes usable. The full suite fails if any existing consumer expectation still assumes legacy frontend normalization.

Run: `test ! -e tests/fixtures/share_url_parity.json && ! rg -n "share_url_parity\.json" frontend/src`
Expected: no output and exit 0. The final Task 3 test rewrite has no old fixture reader. This check fails if the temporary Task 1 bridge or another frontend reference survives.

Run:

```bash
! rg -ni '\bparity\b|mirrored' \
    src/twicc/cli/share.py \
    src/twicc/core/services/public_origin.py \
    src/twicc/core/services/share_url.py \
    frontend/src/utils/publicOrigin.js \
    frontend/src/utils/shareUrl.js \
    frontend/src/utils/shareUrl.test.js \
    tests/fixtures/share_url_backend_cases.json \
    tests/test_share_url_parity.py
```

Expected: no output and exit 0. The final changed origin and Share construction has no claim that the backend and frontend implementations are identical.

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `refactor(origin): make frontend origin checks backend-safe`

---

### Task 4: Export and verify the stored-origin consumer boundary

**Files:**
- Modify: `frontend/src/utils/publicOrigin.js`
- Test: `frontend/src/utils/publicOrigin.test.js`

**Interfaces:**
- Consumes: `checkPublicOriginInput(value) -> { value: string | null, error: string | null, scheme: string | null, hostname: string | null, port: null, authority: null }` (Task 3).
- Produces: `isRecognizablyCanonicalPublicOrigin(value) -> boolean`. The existing Task 3 string-returning consumer wrapper remains unchanged.

- [ ] **Step 1: Write the failing boundary tests**

Replace the Task 3 import:

```js
import {
    checkPublicOriginInput,
    usablePublicOrigin,
} from './publicOrigin.js'
```

with:

```js
import {
    checkPublicOriginInput,
    isRecognizablyCanonicalPublicOrigin,
    usablePublicOrigin,
} from './publicOrigin.js'
```

Then append:

```js
test('the stored guard is separate from the permissive form check', () => {
    for (const value of [
        'https://a..example',
        'https://example.com/base',
        'https://example.com:bad',
        'https://[xyz]',
    ]) {
        assert.equal(checkPublicOriginInput(value).error, null, value)
        assert.equal(isRecognizablyCanonicalPublicOrigin(value), false, value)
    }
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && node --test src/utils/publicOrigin.test.js`
Expected: FAIL — `isRecognizablyCanonicalPublicOrigin` is not exported.

- [ ] **Step 3: Export the guard**

Replace this exact block produced by Task 3:

```js
export function usablePublicOrigin(value) {
    return recognizableCanonicalStoredOrigin(value) ? value : ''
}
```

with:

```js
export function isRecognizablyCanonicalPublicOrigin(value) {
    return recognizableCanonicalStoredOrigin(value)
}

export function usablePublicOrigin(value) {
    return isRecognizablyCanonicalPublicOrigin(value) ? value : ''
}
```

This export does not create a second parser. It exposes the same stored-value guard for direct tests.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && node --test src/utils/publicOrigin.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `test(origin): pin the stored-origin consumer boundary`

---

### Task 5: Re-scope the shared fixture to backend verdicts and frontend subsets

**Files:**
- Modify: `tests/fixtures/public_origin_cases.json`
- Test: `tests/test_public_origin.py`
- Test: `frontend/src/utils/publicOrigin.test.js`

**Interfaces:**
- Consumes: `normalize_public_origin(value: str | None) -> PublicOriginResult` (Task 1).
- Consumes: `classify_peer_external(peer: PublicOriginResult, external: PublicOriginResult) -> str | None` and `validate_origin_settings(public_value, share_value, peer_value, *, changed_fields) -> tuple[OriginFieldError, ...]` (Task 2).
- Consumes: `checkPublicOriginInput(value) -> { value: string | null, error: string | null, scheme: string | null, hostname: string | null, port: null, authority: null }` and `usablePublicOrigin(value) -> string` (Task 3).
- Consumes: `isRecognizablyCanonicalPublicOrigin(value) -> boolean` (Task 4).
- Produces: fixture sections `cases`, `repair_cases`, `authority_cases`, and `cross_cases` are explicitly backend-only. Python asserts every row.
- Produces: fixture sections `frontend_input_cases` and `frontend_stored_cases` are explicitly frontend-only. JavaScript asserts every row. It names but does not consume backend verdict sections.
- Produces: every JavaScript hard-rejection row also appears in the Python `cases` section with a rejection result. This proves the required safety direction without comparing codes or order.

- [ ] **Step 1: Extend the backend fixture sections**

In `tests/fixtures/public_origin_cases.json`, replace this exact `cases` tail:

```json
        {"name": "invalid port", "input": "https://public.example.com:bad", "value": null, "error": "port"}
    ],
```

with the same `invalid port` row, a comma, every row below, and the closing `],`:

```json
        {"name": "invalid port", "input": "https://public.example.com:bad", "value": null, "error": "port"},
{"name": "port zero kept", "input": "https://public.example.com:0", "value": "https://public.example.com:0", "error": null},
{"name": "trailing colon is malformed", "input": "https://public.example.com:", "value": null, "error": "port"},
{"name": "empty query suffix", "input": "https://example.com?", "value": null, "error": "query"},
{"name": "empty fragment suffix", "input": "https://example.com#", "value": null, "error": "fragment"},
{"name": "control in empty suffix", "input": "https://example.com?\t#", "value": null, "error": "query"},
{"name": "question mark inside fragment", "input": "https://example.com#x?y", "value": null, "error": "fragment"},
{"name": "ipv6 expanded loopback", "input": "https://[0:0:0:0:0:0:0:1]:8443", "value": "https://[::1]:8443", "error": null},
{"name": "ipv6 compressed loopback", "input": "https://[::1]:8443", "value": "https://[::1]:8443", "error": null},
{"name": "ipv6 uppercase", "input": "https://[2001:DB8::1]", "value": "https://[2001:db8::1]", "error": null},
{"name": "ipv6 ipv4 mapped", "input": "https://[::ffff:1.2.3.4]", "value": "https://[::ffff:1.2.3.4]", "error": null},
{"name": "ipv6 ipv4 mapped zero", "input": "https://[::ffff:0:0]", "value": "https://[::ffff:0.0.0.0]", "error": null},
{"name": "ipv6 ipv4 compatible", "input": "https://[::1.2.3.4]", "value": "https://[::102:304]", "error": null},
{"name": "brackets around ipv4", "input": "https://[1.2.3.4]", "value": null, "error": "host"},
{"name": "unbracketed ipv6", "input": "https://::1", "value": null, "error": "host"},
{"name": "valid a-label", "input": "https://xn--fa-hia.de", "value": "https://xn--fa-hia.de", "error": null},
{"name": "uppercase a-label", "input": "HTTPS://XN--FA-HIA.DE", "value": "https://xn--fa-hia.de", "error": null},
{"name": "malformed a-label", "input": "https://xn--.example", "value": null, "error": "host"},
{"name": "malformed uppercase a-label", "input": "https://XN--.example", "value": null, "error": "host"},
{"name": "malformed a-label payload", "input": "https://xn--a-ecp.example", "value": null, "error": "host"},
{"name": "idna2008-disallowed a-label", "input": "https://xn--e28h.example", "value": null, "error": "host"},
{"name": "unicode hostname", "input": "https://exämple.com", "value": null, "error": "host"},
{"name": "percent escape", "input": "https://%65xample.com", "value": null, "error": "host"},
{"name": "trailing dot", "input": "https://example.com.", "value": null, "error": "host"},
{"name": "empty label", "input": "https://a..example", "value": null, "error": "host"},
{"name": "leading hyphen label", "input": "https://-a.example", "value": null, "error": "host"},
{"name": "trailing hyphen label", "input": "https://a-.example", "value": null, "error": "host"},
{"name": "underscore label", "input": "https://my_host.example", "value": null, "error": "host"},
{"name": "label at 63 chars", "input": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example", "value": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example", "error": null},
{"name": "label over 63 chars", "input": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example", "value": null, "error": "host"},
{"name": "hostname at 253 chars", "input": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "value": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "error": null},
{"name": "hostname over 253 chars", "input": "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "value": null, "error": "host"},
{"name": "non-canonical ipv4 leading zero", "input": "https://192.168.001.1", "value": null, "error": "host"},
{"name": "digits and dots not ipv4", "input": "https://1.2.3", "value": null, "error": "host"},
{"name": "embedded tab in hostname", "input": "https://exa\tmple.com", "value": null, "error": "host"},
{"name": "embedded line feed in hostname", "input": "https://exa\nmple.com", "value": null, "error": "host"},
{"name": "embedded carriage return in hostname", "input": "https://exa\rmple.com", "value": null, "error": "host"},
{"name": "embedded tab in port", "input": "https://example.com:8\t0", "value": null, "error": "port"},
{"name": "embedded line feed in port", "input": "https://example.com:8\n0", "value": null, "error": "port"},
{"name": "embedded carriage return in port", "input": "https://example.com:8\r0", "value": null, "error": "port"}
    ],
```

These rows are backend-only by section. They contain no per-row `backend_only` property. The old suffix-precedence, credential-order, NFKC-order, and CPython-phase rows do not return. D6 removes those cross-language comparison tests.

After `repair_cases`, add these complete backend sections:

```json
"authority_cases": [
    {"name": "default https port stripped", "input": "https://Example.com:443", "hostname": "example.com", "authority": "example.com"},
    {"name": "default http port stripped", "input": "http://Example.com:80", "hostname": "example.com", "authority": "example.com"},
    {"name": "non-default port kept", "input": "https://example.com:8443", "hostname": "example.com", "authority": "example.com:8443"},
    {"name": "scheme is not part of the authority", "input": "http://example.com", "hostname": "example.com", "authority": "example.com"},
    {"name": "ipv6 expanded with port", "input": "https://[0:0:0:0:0:0:0:1]:8443", "hostname": "::1", "authority": "[::1]:8443"},
    {"name": "ipv6 compressed with port", "input": "https://[::1]:8443", "hostname": "::1", "authority": "[::1]:8443"},
    {"name": "a-label authority", "input": "HTTPS://XN--FA-HIA.DE", "hostname": "xn--fa-hia.de", "authority": "xn--fa-hia.de"},
    {"name": "ipv4 authority", "input": "192.168.1.42:3501", "hostname": "192.168.1.42", "authority": "192.168.1.42:3501"}
],
"cross_cases": [
    {"name": "all empty", "publicBaseUrl": "", "shareBaseUrl": "", "peerBaseUrl": "", "changed_fields": ["publicBaseUrl", "shareBaseUrl", "peerBaseUrl"], "errors": [], "peer_routing": null},
    {"name": "distinct valid trio", "publicBaseUrl": "https://app.example", "shareBaseUrl": "https://share.example", "peerBaseUrl": "https://peer.example", "changed_fields": ["publicBaseUrl", "shareBaseUrl", "peerBaseUrl"], "errors": [], "peer_routing": "dedicated"},
    {"name": "shared peer and external", "publicBaseUrl": "https://x.example", "shareBaseUrl": "https://share.example", "peerBaseUrl": "https://x.example", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "shared"},
    {"name": "shared via ipv6 spellings", "publicBaseUrl": "https://[::1]:8443", "shareBaseUrl": "", "peerBaseUrl": "https://[0:0:0:0:0:0:0:1]:8443", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "shared"},
    {"name": "dedicated by port", "publicBaseUrl": "https://x.example", "shareBaseUrl": "", "peerBaseUrl": "https://x.example:8443", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "dedicated"},
    {"name": "dedicated by two ports", "publicBaseUrl": "https://x.example:7443", "shareBaseUrl": "", "peerBaseUrl": "https://x.example:8443", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "dedicated"},
    {"name": "dedicated with empty external", "publicBaseUrl": "", "shareBaseUrl": "", "peerBaseUrl": "https://peer.example", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "dedicated"},
    {"name": "scheme-only difference is ambiguous", "publicBaseUrl": "https://x.example", "shareBaseUrl": "", "peerBaseUrl": "http://x.example", "changed_fields": ["peerBaseUrl"], "errors": [{"field": "peerBaseUrl", "code": "origin_conflict_ambiguous_authority"}, {"field": "publicBaseUrl", "code": "origin_conflict_ambiguous_authority"}], "peer_routing": "ambiguous"},
    {"name": "share and external hostname conflict ignores ports", "publicBaseUrl": "https://x.example", "shareBaseUrl": "https://x.example:9443", "peerBaseUrl": "", "changed_fields": ["shareBaseUrl"], "errors": [{"field": "shareBaseUrl", "code": "origin_conflict_share_external_hostname"}, {"field": "publicBaseUrl", "code": "origin_conflict_share_external_hostname"}], "peer_routing": null},
    {"name": "share and peer hostname conflict", "publicBaseUrl": "", "shareBaseUrl": "https://x.example", "peerBaseUrl": "http://x.example:8443", "changed_fields": ["peerBaseUrl"], "errors": [{"field": "shareBaseUrl", "code": "origin_conflict_share_peer_hostname"}, {"field": "peerBaseUrl", "code": "origin_conflict_share_peer_hostname"}], "peer_routing": "dedicated"},
    {"name": "invalid share is a structural error", "publicBaseUrl": "https://app.example", "shareBaseUrl": "ftp://share.example", "peerBaseUrl": "https://peer.example", "changed_fields": ["shareBaseUrl"], "errors": [{"field": "shareBaseUrl", "code": "invalid_origin_scheme"}], "peer_routing": null},
    {"name": "unicode peer is a structural error", "publicBaseUrl": "", "shareBaseUrl": "", "peerBaseUrl": "https://exämple.com", "changed_fields": ["peerBaseUrl"], "errors": [{"field": "peerBaseUrl", "code": "invalid_origin_host"}], "peer_routing": null},
    {"name": "unchanged invalid external is not a relationship operand", "publicBaseUrl": "ftp://peer.example", "shareBaseUrl": "", "peerBaseUrl": "https://peer.example", "changed_fields": ["peerBaseUrl"], "errors": [], "peer_routing": "dedicated"},
    {"name": "structural error keeps independent relationship errors", "publicBaseUrl": "ftp://app.example", "shareBaseUrl": "https://x.example", "peerBaseUrl": "http://x.example:8443", "changed_fields": ["publicBaseUrl", "shareBaseUrl", "peerBaseUrl"], "errors": [{"field": "publicBaseUrl", "code": "invalid_origin_scheme"}, {"field": "shareBaseUrl", "code": "origin_conflict_share_peer_hostname"}, {"field": "peerBaseUrl", "code": "origin_conflict_share_peer_hostname"}], "peer_routing": null}
]
```

Add the required commas between top-level sections. Task 6 adds a direct metadata-injection regression that makes each invalid-operand guard load-bearing.

- [ ] **Step 2: Add the explicitly scoped frontend fixture sections**

After `cross_cases`, add:

```json
"frontend_input_cases": [
    {"name": "empty input", "input": "", "value": "", "error": null},
    {"name": "non-string input", "input": 42, "value": null, "error": "type"},
    {"name": "trimmed input stays raw", "input": "  HTTPS://Example.COM:443/ \r\n", "value": "HTTPS://Example.COM:443/", "error": null},
    {"name": "port zero", "input": "https://example.com:0", "value": "https://example.com:0", "error": null},
    {"name": "protocol relative", "input": "//public.example.com", "value": null, "error": "scheme"},
    {"name": "unsupported scheme", "input": "ftp://public.example.com", "value": null, "error": "scheme"},
    {"name": "missing authority", "input": "https://", "value": null, "error": "host"},
    {"name": "credentials", "input": "https://user:secret@public.example.com", "value": null, "error": "credentials"},
    {"name": "unicode authority", "input": "https://exämple.com", "value": null, "error": "host"},
    {"name": "percent authority", "input": "https://%65xample.com", "value": null, "error": "host"},
    {"name": "control in authority", "input": "https://exa\tmple.com", "value": null, "error": "host"},
    {"name": "line feed in authority", "input": "https://exa\nmple.com", "value": null, "error": "host"},
    {"name": "carriage return in authority", "input": "https://exa\rmple.com", "value": null, "error": "host"},
    {"name": "trailing colon", "input": "https://public.example.com:", "value": null, "error": "port"},
    {"name": "dns grammar deferred", "input": "https://a..example", "value": "https://a..example", "error": null},
    {"name": "a-label verdict deferred", "input": "https://xn--e28h.example", "value": "https://xn--e28h.example", "error": null},
    {"name": "port verdict deferred", "input": "https://example.com:bad", "value": "https://example.com:bad", "error": null},
    {"name": "suffix verdict deferred", "input": "https://example.com/base", "value": "https://example.com/base", "error": null},
    {"name": "ipv6 verdict deferred", "input": "https://[xyz]", "value": "https://[xyz]", "error": null}
],
"frontend_stored_cases": [
    {"name": "canonical dns stored value", "input": "https://example.com", "usable": "https://example.com"},
    {"name": "canonical ipv4 stored value", "input": "http://192.168.1.42:3501", "usable": "http://192.168.1.42:3501"},
    {"name": "canonical ipv6 stored value", "input": "https://[::1]:8443", "usable": "https://[::1]:8443"},
    {"name": "python mixed mapped stored value", "input": "https://[::ffff:1.2.3.4]", "usable": "https://[::ffff:1.2.3.4]"},
    {"name": "canonical a-label stored shape", "input": "https://xn--fa-hia.de", "usable": "https://xn--fa-hia.de"},
    {"name": "idna2008-disallowed a-label stays lexically usable", "input": "https://xn--e28h.example", "usable": "https://xn--e28h.example"},
    {"name": "root slash is not canonical storage", "input": "https://example.com/", "usable": ""},
    {"name": "default port is not canonical storage", "input": "https://example.com:443", "usable": ""},
    {"name": "uppercase is not canonical storage", "input": "HTTPS://EXAMPLE.COM", "usable": ""},
    {"name": "expanded ipv6 is not canonical storage", "input": "https://[0:0:0:0:0:0:0:1]", "usable": ""},
    {"name": "malformed stored hostname", "input": "https://a..example", "usable": ""}
]
```

Also add this top-level list:

```json
"backend_only_sections": ["cases", "repair_cases", "authority_cases", "cross_cases"]
```

Add this metadata beside it. JavaScript can prove the A-label boundary without
reading or asserting any backend verdict:

```json
"backend_a_label_cases": [
    "valid a-label",
    "uppercase a-label",
    "malformed a-label",
    "malformed uppercase a-label",
    "malformed a-label payload",
    "idna2008-disallowed a-label",
    "a-label authority"
]
```

Use commas that keep the complete file valid JSON.

- [ ] **Step 3: Wire the backend sections into Python**

Keep the existing `cases` and `repair_cases` loops. Append:

```python
def test_authority_cases_match_backend_contract():
    for case in CASES["authority_cases"]:
        result = normalize_public_origin(case["input"])
        assert (result.hostname, result.authority) == (case["hostname"], case["authority"]), case["name"]


def test_cross_cases_match_backend_contract():
    from twicc.core.services.public_origin import classify_peer_external, validate_origin_settings

    for case in CASES["cross_cases"]:
        errors = validate_origin_settings(
            case["publicBaseUrl"], case["shareBaseUrl"], case["peerBaseUrl"],
            changed_fields=set(case["changed_fields"]),
        )
        assert [{"field": error.field, "code": error.code} for error in errors] == case["errors"], case["name"]
        if case["peer_routing"] is not None:
            routing = classify_peer_external(
                normalize_public_origin(case["peerBaseUrl"]),
                normalize_public_origin(case["publicBaseUrl"]),
            )
            assert routing == case["peer_routing"], case["name"]


def test_every_frontend_rejection_is_also_a_backend_rejection():
    backend = {case["input"]: case for case in CASES["cases"]}
    for case in CASES["frontend_input_cases"]:
        if case["error"] is None:
            continue
        assert case["input"] in backend, case["name"]
        assert backend[case["input"]]["error"] is not None, case["name"]
```

Add any missing safe-rejection input to `cases`. Do not compare the two error codes. The test enforces verdict direction only.

- [ ] **Step 4: Add the scoped JavaScript fixture tests**

Add this import after the existing Node imports:

```js
import { readFileSync } from 'node:fs'
```

Add this loader after the Task 4 public-origin import:

```js
const cases = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/public_origin_cases.json', import.meta.url), 'utf8'),
)
```

Then append:

```js
test('frontend input cases match the safe subset', () => {
    for (const item of cases.frontend_input_cases) {
        const result = checkPublicOriginInput(item.input)
        assert.deepEqual(
            { value: result.value, error: result.error },
            { value: item.value, error: item.error },
            item.name,
        )
    }
})

test('frontend stored cases fail closed outside canonical shape', () => {
    for (const item of cases.frontend_stored_cases) {
        assert.equal(usablePublicOrigin(item.input), item.usable, item.name)
        assert.equal(isRecognizablyCanonicalPublicOrigin(item.input), Boolean(item.usable), item.name)
    }
})

test('backend verdict sections stay explicitly backend-only', () => {
    assert.deepEqual(cases.backend_only_sections, [
        'cases',
        'repair_cases',
        'authority_cases',
        'cross_cases',
    ])
    assert.deepEqual(cases.backend_a_label_cases, [
        'valid a-label',
        'uppercase a-label',
        'malformed a-label',
        'malformed uppercase a-label',
        'malformed a-label payload',
        'idna2008-disallowed a-label',
        'a-label authority',
    ])
})
```

The JavaScript test must not call a frontend function inside a backend-only loop. Do not restore the deleted `classifyPeerExternal` or `validateOriginSettings` imports.

- [ ] **Step 5: Run both suites**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_public_origin.py -q && cd frontend && node --test src/utils/publicOrigin.test.js`
Expected: PASS. Python owns every backend verdict. JavaScript owns only the subset and consumer sections. `test_every_frontend_rejection_is_also_a_backend_rejection` fails if the frontend rejection set becomes unsafe. The stored A-label row fails if either JavaScript guard makes an IDNA2008 verdict.

- [ ] **Step 6: Commit**

Commit the changes produced by this task.
Subject: `test(origin): separate backend verdicts from frontend checks`

---

### Task 6: Changed-origin validation in the atomic settings write path

**Files:**
- Modify: `src/twicc/core/services/settings_mutation.py`
- Test: `tests/test_settings_mutation.py`
- Test: `tests/test_public_origin.py`

**Interfaces:**
- Consumes: `validate_origin_settings(public_value, share_value, peer_value, *, changed_fields) -> tuple[OriginFieldError, ...]` (Task 2).
- Consumes: `ORIGIN_CONFLICT_SHARE_EXTERNAL = "origin_conflict_share_external_hostname"`, `ORIGIN_CONFLICT_SHARE_PEER = "origin_conflict_share_peer_hostname"`, and `ORIGIN_CONFLICT_AMBIGUOUS = "origin_conflict_ambiguous_authority"` (Task 2).
- Produces: `_merge_and_write` detects changed origin fields by decoded JSON type and recursively compared value. Boolean is distinct from Number. Python `int` and `float` values form one JSON Number category. It validates changed fields and relationships that contain a changed field. An unchanged invalid origin neither blocks the patch nor becomes a relationship operand. One patch remains atomic. It returns one `SettingsDropError(field, code, message)` per finding. Conflict messages (exact strings): share/external → `"The Share host must use a different hostname from the External address."`; share/peer → `"The Share host must use a different hostname from the Peer address."`; ambiguity → `"The Peer and External addresses must be the same origin or use different authorities."`; structural codes keep the existing message `"Enter a hostname or an HTTP(S) origin without a path, query, or fragment."`.
- Produces: `SettingsDropError(field: str, code: str, message: str)` and `SettingsUpdateResult(status: str, version: int, corrections: dict, clean: dict, errors: tuple[SettingsDropError, ...] = ())` remain the service result shapes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_mutation.py`:

```python
def test_origin_patch_allows_unchanged_invalid_stored_origin(temp_settings):
    seeded = ss.read_synced_settings()
    seeded["shareBaseUrl"] = "ftp://peer.example"
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()
    # The patch changes Peer only. The unchanged invalid Share value does not
    # block this repair and does not become a relationship operand (design §7).
    r = _update({"peerBaseUrl": "https://peer.example"})
    assert r.status == "accepted"
    after = ss.read_synced_settings()
    assert after["peerBaseUrl"] == "https://peer.example"
    assert after["shareBaseUrl"] == "ftp://peer.example"
    assert after["_version"] == before["_version"] + 1


def test_non_origin_patch_stays_allowed_with_invalid_stored_origin(temp_settings):
    seeded = ss.read_synced_settings()
    seeded["shareBaseUrl"] = "ftp://legacy.example.com"
    ss.write_synced_settings(seeded)
    r = _update({"terminalUseTmux": False, "shareBaseUrl": "ftp://legacy.example.com"})
    assert r.status == "accepted"
    assert ss.read_synced_settings()["terminalUseTmux"] is False
    assert ss.read_synced_settings()["shareBaseUrl"] == "ftp://legacy.example.com"


@pytest.mark.parametrize(
    "stored, submitted, expected_status",
    [
        (0.0, 0, "accepted"),
        (1.0, 1, "accepted"),
        (0, False, "rejected"),
        (False, 0, "rejected"),
    ],
)
def test_json_scalar_number_round_trip_and_boolean_distinction(
    temp_settings, stored, submitted, expected_status,
):
    seeded = ss.read_synced_settings()
    seeded["peerBaseUrl"] = stored
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()

    result = _update({"terminalUseTmux": False, "peerBaseUrl": submitted})

    assert result.status == expected_status
    after = ss.read_synced_settings()
    if expected_status == "accepted":
        assert after["terminalUseTmux"] is False
        assert after["peerBaseUrl"] == submitted
        assert after["_version"] == before["_version"] + 1
    else:
        assert [(error.field, error.code) for error in result.errors] == [
            ("peerBaseUrl", "invalid_origin_type"),
        ]
        assert after == before


@pytest.mark.parametrize(
    "stored, submitted, expected_status",
    [
        ([0.0], [0], "accepted"),
        ({"value": 1.0}, {"value": 1}, "accepted"),
        ({"value": 0}, {"value": False}, "rejected"),
        ([0], [False], "rejected"),
    ],
)
def test_json_nested_number_round_trip_and_boolean_distinction(
    temp_settings, stored, submitted, expected_status,
):
    seeded = ss.read_synced_settings()
    seeded["peerBaseUrl"] = stored
    ss.write_synced_settings(seeded)
    before = ss.read_synced_settings()

    result = _update({"terminalUseTmux": False, "peerBaseUrl": submitted})

    assert result.status == expected_status
    after = ss.read_synced_settings()
    if expected_status == "accepted":
        assert after["terminalUseTmux"] is False
        assert after["peerBaseUrl"] == submitted
        assert after["_version"] == before["_version"] + 1
    else:
        assert [(error.field, error.code) for error in result.errors] == [
            ("peerBaseUrl", "invalid_origin_type"),
        ]
        assert after == before


def test_origin_patch_rejects_relationship_conflicts_atomically(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    before = ss.read_synced_settings()
    r = _update({"shareBaseUrl": "https://x.example:9443"})
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
    assert r.errors[0].message == "The Share host must use a different hostname from the External address."
    after = ss.read_synced_settings()
    assert after.get("shareBaseUrl", "") == before.get("shareBaseUrl", "")
    assert after["_version"] == before["_version"]


def test_origin_patch_rejects_ambiguous_peer_external(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    r = _update({"peerBaseUrl": "http://x.example"})
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("peerBaseUrl", "origin_conflict_ambiguous_authority"),
        ("publicBaseUrl", "origin_conflict_ambiguous_authority"),
    ]
    assert r.errors[0].message == (
        "The Peer and External addresses must be the same origin or use different authorities."
    )


def test_origin_patch_accepts_shared_peer_and_external(temp_settings):
    _update({"publicBaseUrl": "https://x.example"})
    r = _update({"peerBaseUrl": "https://x.example"})
    assert r.status == "accepted"
    assert ss.read_synced_settings()["peerBaseUrl"] == "https://x.example"


@pytest.mark.parametrize(
    "first_field,first_value,second_field,second_value,expected_public,expected_share",
    [
        (
            "publicBaseUrl", "https://share.example",
            "shareBaseUrl", "https://final-share.example",
            "https://share.example", "https://final-share.example",
        ),
        (
            "shareBaseUrl", "https://app.example",
            "publicBaseUrl", "https://final-app.example",
            "https://final-app.example", "https://app.example",
        ),
    ],
)
def test_two_invalid_origins_can_be_repaired_in_either_order(
    temp_settings, first_field, first_value, second_field, second_value, expected_public, expected_share,
):
    seeded = ss.read_synced_settings()
    seeded["publicBaseUrl"] = "ftp://app.example"
    seeded["shareBaseUrl"] = "ftp://share.example"
    ss.write_synced_settings(seeded)
    # Each repair order keeps the other invalid field unchanged.
    first = _update({first_field: first_value})
    assert first.status == "accepted"
    untouched = "shareBaseUrl" if first_field == "publicBaseUrl" else "publicBaseUrl"
    assert ss.read_synced_settings()[untouched].startswith("ftp://")
    second = _update({second_field: second_value})
    assert second.status == "accepted"
    settings = ss.read_synced_settings()
    assert settings["publicBaseUrl"] == expected_public
    assert settings["shareBaseUrl"] == expected_share


def test_multi_field_origin_patch_reports_all_errors_and_writes_nothing(temp_settings):
    before = ss.read_synced_settings()
    r = _update({
        "publicBaseUrl": "ftp://app.example",
        "shareBaseUrl": "https://x.example",
        "peerBaseUrl": "http://x.example:8443",
    })
    assert r.status == "rejected"
    assert [(e.field, e.code) for e in r.errors] == [
        ("publicBaseUrl", "invalid_origin_scheme"),
        ("shareBaseUrl", "origin_conflict_share_peer_hostname"),
        ("peerBaseUrl", "origin_conflict_share_peer_hostname"),
    ]
    assert r.errors[1].message == "The Share host must use a different hostname from the Peer address."
    assert ss.read_synced_settings() == before


def test_update_from_payload_returns_origin_relationship_errors(temp_settings):
    from asgiref.sync import async_to_sync
    from twicc.core.services.settings_mutation import update_synced_settings_from_payload

    _update({"publicBaseUrl": "https://x.example"})
    res = async_to_sync(update_synced_settings_from_payload)({
        "kind": "settings:update",
        "patch": {"shareBaseUrl": "https://x.example:9443"},
        "broadcast": False,
    })
    assert res.success is False
    assert [(error.field, error.code) for error in res.errors] == [
        ("shareBaseUrl", "origin_conflict_share_external_hostname"),
        ("publicBaseUrl", "origin_conflict_share_external_hostname"),
    ]
```

Append to `tests/test_public_origin.py`. The first test injects recognizable metadata into invalid parse results. It fails if any relationship admits an invalid operand. The second test covers spec §13.4: reads never migrate or repair `peerBaseUrl`, valid or not.

```python
@pytest.mark.parametrize(
    "public_value,share_value,peer_value,changed_fields",
    [
        ("", "ftp://peer.example", "https://peer.example", {"peerBaseUrl"}),
        ("ftp://share.example", "https://share.example", "", {"shareBaseUrl"}),
        ("ftp://peer.example", "", "http://peer.example", {"peerBaseUrl"}),
    ],
)
def test_invalid_origin_result_metadata_never_becomes_a_relationship_operand(
    monkeypatch, public_value, share_value, peer_value, changed_fields,
):
    from twicc.core.services import public_origin

    original = public_origin.normalize_public_origin

    def normalize(value):
        if isinstance(value, str) and value.startswith("ftp://"):
            hostname = value.removeprefix("ftp://")
            return public_origin.PublicOriginResult(
                None, "scheme", "ftp", hostname, None, hostname,
            )
        return original(value)

    monkeypatch.setattr(public_origin, "normalize_public_origin", normalize)
    assert public_origin.validate_origin_settings(
        public_value, share_value, peer_value, changed_fields=changed_fields,
    ) == ()


def test_settings_read_preserves_invalid_peer_base_url(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(orjson.dumps({"peerBaseUrl": "ftp://peer.example/forbidden"}))
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_synced_settings()["peerBaseUrl"] == "ftp://peer.example/forbidden"
        assert orjson.loads(path.read_bytes())["peerBaseUrl"] == "ftp://peer.example/forbidden"
    finally:
        ss._cache.clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_settings_mutation.py tests/test_public_origin.py -q`
Expected: the write-path relationship tests FAIL because `_merge_and_write` does not call `validate_origin_settings` yet. The invalid-metadata guard, unchanged-invalid, and §13.4 read tests can pass already. Keep these regression pins.

- [ ] **Step 3: Implement the merged validation**

Replace this import block in `src/twicc/core/services/settings_mutation.py`:

```python
from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
```

with:

```python
from twicc.agent.registry import get_agent_manager_registry
from twicc.core.enums import Provider
from twicc.core.services.public_origin import (
    ORIGIN_CONFLICT_AMBIGUOUS,
    ORIGIN_CONFLICT_SHARE_EXTERNAL,
    ORIGIN_CONFLICT_SHARE_PEER,
)
from twicc.providers.helpers import get_provider_helpers_registry
```

In `src/twicc/core/services/settings_mutation.py`, replace this block of `_merge_and_write`:

```python
        from twicc.core.services.public_origin import PUBLIC_ORIGIN_SETTING_KEYS, normalize_public_origin

        normalized_patch = dict(patch)
        corrections: dict = {}
        errors: list[SettingsDropError] = []
        for key in PUBLIC_ORIGIN_SETTING_KEYS:
            if key not in patch:
                continue
            value = patch[key]
            # The frontend sends a full settings snapshot. Keep an unchanged
            # malformed legacy value from blocking unrelated changes.
            if value == existing_settings.get(key):
                continue
            if not isinstance(value, str):
                errors.append(SettingsDropError(
                    key,
                    "invalid_origin_type",
                    "Enter a hostname or an HTTP(S) origin without a path, query, or fragment.",
                ))
                continue
            result = normalize_public_origin(value)
            if result.error:
                errors.append(SettingsDropError(
                    key,
                    f"invalid_origin_{result.error}",
                    "Enter a hostname or an HTTP(S) origin without a path, query, or fragment.",
                ))
                continue
            normalized_patch[key] = result.value
            if result.value != value:
                corrections[key] = result.value
        if errors:
            clean, version = prepare_settings_for_client(existing_settings)
            return {
                "status": "rejected",
                "clean": clean,
                "version": version,
                "errors": tuple(errors),
            }
```

with:

```python
        from twicc.core.services.public_origin import (
            PUBLIC_ORIGIN_SETTING_KEYS,
            normalize_public_origin,
            validate_origin_settings,
        )

        normalized_patch = dict(patch)
        corrections: dict = {}
        changed_origin_fields = {
            key for key in PUBLIC_ORIGIN_SETTING_KEYS
            if key in patch and not _same_json_value(patch[key], existing_settings.get(key))
        }
        for key in PUBLIC_ORIGIN_SETTING_KEYS:
            if key not in changed_origin_fields:
                continue
            value = patch[key]
            result = normalize_public_origin(value)
            if not result.error:
                normalized_patch[key] = result.value
            if not result.error and result.value != value:
                corrections[key] = result.value
        errors: list[SettingsDropError] = []
        if changed_origin_fields:
            merged = {
                key: normalized_patch.get(key, existing_settings.get(key, ""))
                for key in PUBLIC_ORIGIN_SETTING_KEYS
            }
            for field_error in validate_origin_settings(
                merged["publicBaseUrl"], merged["shareBaseUrl"], merged["peerBaseUrl"],
                changed_fields=changed_origin_fields,
            ):
                errors.append(SettingsDropError(
                    field_error.field,
                    field_error.code,
                    _ORIGIN_ERROR_MESSAGES.get(field_error.code, _ORIGIN_STRUCTURAL_MESSAGE),
                ))
        if errors:
            clean, version = prepare_settings_for_client(existing_settings)
            return {
                "status": "rejected",
                "clean": clean,
                "version": version,
                "errors": tuple(errors),
            }
```

Replace this exact module-level block:

```python
class SettingsDropResult(NamedTuple):
    success: bool
    errors: tuple = ()
    status_extra: dict = {}  # generic passthrough → status file; never mutate in place
```

with:

```python
class SettingsDropResult(NamedTuple):
    success: bool
    errors: tuple = ()
    status_extra: dict = {}  # generic passthrough → status file; never mutate in place


_ORIGIN_STRUCTURAL_MESSAGE = "Enter a hostname or an HTTP(S) origin without a path, query, or fragment."
_ORIGIN_ERROR_MESSAGES = {
    ORIGIN_CONFLICT_SHARE_EXTERNAL: "The Share host must use a different hostname from the External address.",
    ORIGIN_CONFLICT_SHARE_PEER: "The Share host must use a different hostname from the Peer address.",
    ORIGIN_CONFLICT_AMBIGUOUS: "The Peer and External addresses must be the same origin or use different authorities.",
}


def _same_json_value(left, right) -> bool:
    """Return true when decoded JSON values have the same JSON type and value."""
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    return left == right
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_settings_mutation.py tests/test_public_origin.py -q`
Expected: PASS. The scalar and nested JSON-category regressions fail if a Boolean/Number change skips validation or an `int`/`float` round trip triggers validation. The two-invalid test fails if per-field recovery deadlocks. The multi-field test fails on a partial write, missing error, hidden independent conflict, or version change after rejection.

- [ ] **Step 5: Verify the CLI payload adapter returns relationship errors**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_settings_mutation.py -q`
Expected: PASS. `test_update_from_payload_returns_origin_relationship_errors` fails if the `settings:update` adapter bypasses validation or drops its field errors. Fix only files declared by this task. Report any required fixture change outside this write set to the orchestrator.

- [ ] **Step 6: Commit**

Commit the changes produced by this task.
Subject: `feat(settings): validate changed origins and their relationships atomically`

---

### Task 7: Request-authority parsing and invalid-setting recognition

**Files:**
- Create: `src/twicc/core/services/origin_policy.py`
- Test: `tests/test_origin_policy.py` (new file)

**Interfaces:**
- Consumes: `canonicalize_hostname(token: str, *, bracketed: bool) -> CanonicalHostname` and `_TRIM_CHARS: str` (Task 1) from `twicc.core.services.public_origin`.
- Produces: in `twicc.core.services.origin_policy`:
  - `RequestAuthority(hostname: str, authority: str)` NamedTuple.
  - `parse_request_authority(value: str) -> RequestAuthority | None` — strict §8 parsing of one `Host` header value (also reused for §11 recognition).
  - `request_authority_from_scope(scope) -> RequestAuthority | None` — `None` unless the scope has exactly one `host` header whose value parses.
  - `recognize_authority(value) -> RequestAuthority | None` — §11 best-effort recognition inside an invalid setting value.
  - `SHARE_ONLY_PREFIXES: tuple[str, ...]` and `SHARE_EXCLUSIVE_PREFIXES: tuple[str, ...]` constants (moved here from `share/asgi_filter.py` so the pure classifier in Task 8 can use them; their values stay unchanged).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_origin_policy.py`:

```python
"""Pure origin-routing policy: request-authority parsing, recognition, policy
building, and request classification (peer-origin-routing design §8-§11)."""

from unittest.mock import patch

import pytest

from twicc.core.services.origin_policy import (
    RequestAuthority,
    parse_request_authority,
    recognize_authority,
    request_authority_from_scope,
)


@pytest.mark.parametrize("value,hostname,authority", [
    ("example.com", "example.com", "example.com"),
    ("example.com:0", "example.com", "example.com:0"),
    ("EXAMPLE.com", "example.com", "example.com"),
    ("example.com:8443", "example.com", "example.com:8443"),
    ("example.com:443", "example.com", "example.com:443"),  # the explicit port component is retained (§8)
    ("example.com:08080", "example.com", "example.com:8080"),  # canonical decimal, no leading zeros
    ("localhost:3501", "localhost", "localhost:3501"),
    ("192.168.1.42:3501", "192.168.1.42", "192.168.1.42:3501"),
    ("[::1]:8443", "::1", "[::1]:8443"),
    ("[0:0:0:0:0:0:0:1]:8443", "::1", "[::1]:8443"),
    ("[2001:DB8::1]", "2001:db8::1", "[2001:db8::1]"),
    ("XN--FA-HIA.DE", "xn--fa-hia.de", "xn--fa-hia.de"),
])
def test_parse_request_authority_accepts(value, hostname, authority):
    assert parse_request_authority(value) == RequestAuthority(hostname, authority)


@pytest.mark.parametrize("value", [
    "",
    "example.com:",          # trailing colon is malformed in a Host header
    "example.com:bad",
    "example.com:70000",
    "a..example",
    "example.com.",
    "-a.example",
    "a-.example",
    "my_host.example",
    "exämple.com",
    "%65xample.com",
    "xn--.example",
    "XN--.example",
    "xn--a-ecp.example",
    "xn--e28h.example",
    "999.1.2.3",
    "1.2.3",
    "192.168.001.1",
    "::1",                   # IPv6 requires brackets in Host (§8)
    "[::1",
    "[1.2.3.4]",
    "[fe80::1%eth0]",
    "[fe80::1%25eth0]",
    "example.com:" + ("9" * 4301),
    "a b.example",
    "exa\tmple.com",
    "example.com:8\t0",
    "example.com\n",
    "user@example.com",
])
def test_parse_request_authority_rejects(value):
    assert parse_request_authority(value) is None


def test_parse_request_authority_bounds_port_before_integer_conversion():
    with patch("builtins.int", side_effect=AssertionError("unbounded port reached int")) as conversion:
        assert parse_request_authority("example.com:" + ("9" * 5000)) is None
    conversion.assert_not_called()


def test_parse_request_authority_strips_leading_zeroes_before_integer_conversion():
    real_int = int
    with patch("builtins.int", side_effect=lambda token: real_int(token)) as conversion:
        assert parse_request_authority("example.com:" + ("0" * 5000)) == RequestAuthority(
            "example.com", "example.com:0",
        )
    conversion.assert_called_once_with("0")


def _scope(host_values):
    return {"type": "http", "headers": [(b"host", v.encode("latin1")) for v in host_values]}


def test_request_authority_from_scope_requires_exactly_one_host():
    assert request_authority_from_scope(_scope(["example.com"])) == RequestAuthority("example.com", "example.com")
    assert request_authority_from_scope(_scope([])) is None
    assert request_authority_from_scope(_scope(["a.example", "b.example"])) is None
    assert request_authority_from_scope(_scope(["a.example", "a.example"])) is None
    assert request_authority_from_scope({"type": "http"}) is None


def test_recognize_authority_extracts_from_invalid_settings():
    # An unsupported scheme or a forbidden path can leave the authority
    # recognizable (§11); hostname and decimal port spelling are canonicalized.
    assert recognize_authority("ftp://share.example") == RequestAuthority("share.example", "share.example")
    assert recognize_authority("https://peer.example/forbidden") == RequestAuthority("peer.example", "peer.example")
    assert recognize_authority("https://peer.example:8443/x?q=1") == RequestAuthority(
        "peer.example", "peer.example:8443",
    )
    assert recognize_authority("ftp://[::1]:8443/x") == RequestAuthority("::1", "[::1]:8443")
    assert recognize_authority("  ftp://Share.Example  ") == RequestAuthority("share.example", "share.example")
    # No percent decoding, no IDNA conversion, no host-syntax guessing.
    assert recognize_authority("https://") is None
    assert recognize_authority("https://%65xample.com/x") is None
    assert recognize_authority("https://exämple.com/x") is None
    assert recognize_authority("https://exa\tmple.com/x") is None
    assert recognize_authority("https://example.com:8\t0/x") is None
    assert recognize_authority("https://user:pw@x.example/x") is None
    assert recognize_authority(42) is None
    assert recognize_authority("") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_origin_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'twicc.core.services.origin_policy'`.

- [ ] **Step 3: Create the module**

Create `src/twicc/core/services/origin_policy.py`:

```python
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

# Served on the share host. /_twicc/artifact-shell/ and the broker shim are
# shared with the working app's own artifact preview, so they are allowed on
# the share host but must NOT be hidden on the working origin — only /share/
# and /_twicc/share/ are share-exclusive.
SHARE_ONLY_PREFIXES = (
    "/share/",
    "/_twicc/share/",
    "/_twicc/artifact-shell/",
    "/_twicc/artifact-broker-shim.js",
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_origin_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `feat(origin): add request-authority parsing and invalid-setting recognition`

---

### Task 8: Live routing policy builder and request classifier

**Files:**
- Modify: `src/twicc/core/services/origin_policy.py`
- Test: `tests/test_origin_policy.py`

**Interfaces:**
- Consumes: `normalize_public_origin(value: str | None) -> PublicOriginResult` (Task 1).
- Consumes: `recognize_authority(value) -> RequestAuthority | None`, `RequestAuthority(hostname: str, authority: str)`, and `SHARE_EXCLUSIVE_PREFIXES: tuple[str, ...]` (Task 7).
- Produces: in `twicc.core.services.origin_policy`:
  - `OriginPolicy(external_authority, share_hostname, dedicated_peer_authority, shared_peer_authority, quarantined_hostnames: frozenset[str], quarantined_authorities: frozenset[str])` NamedTuple. `share_hostname is None` ⇔ the Share surface is disabled; both peer fields `None` ⇔ the Peer surface is disabled.
  - `build_origin_policy(public_raw, share_raw, peer_raw) -> OriginPolicy` — pure §10/§11 policy from the three raw settings.
  - `get_origin_policy(settings: dict) -> OriginPolicy` — single-entry memo keyed on the raw 3-tuple (live changes rebuild on the next request).
  - `classify_request(policy: OriginPolicy, authority: RequestAuthority | None, path: str, scope_type: str) -> str` — one of `"inner_app"`, `"share_surface"`, `"reject"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_origin_policy.py`:

```python
from twicc.core.services.origin_policy import (  # noqa: E402  (grouped with the module under test)
    OriginPolicy,
    build_origin_policy,
    classify_request,
    get_origin_policy,
)


def test_policy_valid_trio():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://peer.example:8443")
    assert policy == OriginPolicy(
        external_authority="app.example",
        share_hostname="share.example",
        dedicated_peer_authority="peer.example:8443",
        shared_peer_authority=None,
        quarantined_hostnames=frozenset(),
        quarantined_authorities=frozenset(),
    )


def test_policy_shared_peer():
    policy = build_origin_policy("https://x.example", "", "https://x.example")
    assert policy.shared_peer_authority == "x.example"
    assert policy.dedicated_peer_authority is None


def test_policy_empty_settings_disable_their_surfaces():
    policy = build_origin_policy("", "", "")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset() and policy.quarantined_authorities == frozenset()


def test_policy_empty_external_makes_peer_dedicated():
    policy = build_origin_policy("", "", "https://peer.example")
    assert policy.dedicated_peer_authority == "peer.example"
    assert policy.external_authority is None


def test_policy_invalid_share_quarantines_recognizable_hostname():
    policy = build_origin_policy("", "ftp://share.example", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    # Unrecognizable: surface disabled, nothing to quarantine.
    policy = build_origin_policy("", "https://", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset()


def test_policy_invalid_peer_quarantines_recognizable_authority():
    policy = build_origin_policy("", "", "https://peer.example:8443/forbidden")
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_authorities == frozenset({"peer.example:8443"})


def test_policy_unrecognizable_invalid_peer_disables_without_quarantine():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://")
    assert policy.external_authority == "app.example"
    assert policy.share_hostname == "share.example"
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset()
    assert policy.quarantined_authorities == frozenset()


def test_policy_share_conflicts_use_recognized_operands():
    # Share/External conflict: Share disabled, its hostname quarantined; the
    # exact External authority survives via classifier precedence.
    policy = build_origin_policy("https://x.example", "https://x.example:9443", "")
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})
    assert policy.external_authority == "x.example"
    # Share/Peer conflict, with a recognizable invalid peer operand.
    policy = build_origin_policy("", "https://x.example", "https://x.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})
    assert policy.quarantined_authorities == frozenset({"x.example"})
    # A recognizable invalid External operand also disables conflicting Share.
    policy = build_origin_policy("ftp://x.example", "https://x.example", "")
    assert policy.external_authority is None
    assert policy.share_hostname is None
    assert policy.quarantined_hostnames == frozenset({"x.example"})


def test_policy_ambiguous_authority_disables_peer():
    policy = build_origin_policy("https://x.example", "", "http://x.example")
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    # The peer authority equals the valid External authority, so precedence
    # discards it from the authority-quarantine set (§11).
    assert policy.quarantined_authorities == frozenset()
    assert policy.external_authority == "x.example"


def test_policy_invalid_external_disables_peer_classification():
    policy = build_origin_policy("https://", "", "https://peer.example")
    assert policy.external_authority is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_authorities == frozenset({"peer.example"})


def test_policy_interaction_case_1():
    # Spec §13.2 interaction basis, case 1.
    policy = build_origin_policy("https://app.example", "ftp://share.example", "https://peer.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset({"peer.example"})
    assert policy.external_authority == "app.example"


def test_policy_interaction_case_2():
    # Spec §13.2 interaction basis, case 2: the recognizable invalid Peer
    # operand joins the Share-and-Peer conflict and takes valid Share down.
    policy = build_origin_policy("https://", "https://share.example", "https://share.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset({"share.example"})
    assert policy.external_authority is None


def test_policy_interaction_case_3():
    # Spec §13.2 interaction basis, case 3: External precedence removes only
    # app.example from the authority-quarantine set.
    policy = build_origin_policy("https://app.example", "ftp://share.example", "https://app.example/forbidden")
    assert policy.share_hostname is None
    assert policy.dedicated_peer_authority is None and policy.shared_peer_authority is None
    assert policy.quarantined_hostnames == frozenset({"share.example"})
    assert policy.quarantined_authorities == frozenset()
    assert policy.external_authority == "app.example"


def _authority(value):
    return parse_request_authority(value)


def test_classify_request_routing_table():
    policy = build_origin_policy("https://app.example", "https://share.example", "https://peer.example:8443")
    # Share hostname → share surface, any port, both protocols.
    assert classify_request(policy, _authority("share.example"), "/share/tok/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example:9999"), "/share/tok/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/ws/share/tok/", "websocket") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/peer/messages/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/api/sessions/", "http") == "share_surface"
    assert classify_request(policy, _authority("share.example"), "/ws/", "websocket") == "share_surface"
    # Dedicated Peer authority → only /peer/ HTTP; no WebSocket.
    assert classify_request(policy, _authority("peer.example:8443"), "/peer/messages/", "http") == "inner_app"
    assert classify_request(policy, _authority("peer.example:8443"), "/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/static/app.js", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/mcp", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example:8443"), "/ws/", "websocket") == "reject"
    # The peer hostname WITHOUT its port is just another authority.
    assert classify_request(policy, _authority("peer.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("peer.example"), "/api/sessions/", "http") == "inner_app"
    # External and every other authority → full app, hidden share, no /peer/.
    assert classify_request(policy, _authority("app.example"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("app.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("localhost:3501"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("localhost:3501"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/_twicc/share/x.js", "http") == "reject"
    assert classify_request(policy, _authority("app.example"), "/ws/share/tok/", "websocket") == "reject"
    assert classify_request(policy, _authority("app.example"), "/ws/", "websocket") == "inner_app"
    # No valid Host → reject.
    assert classify_request(policy, None, "/api/sessions/", "http") == "reject"


def test_classify_request_shared_peer():
    policy = build_origin_policy("https://x.example", "", "https://x.example")
    assert classify_request(policy, _authority("x.example"), "/peer/messages/", "http") == "inner_app"
    assert classify_request(policy, _authority("x.example"), "/api/sessions/", "http") == "inner_app"
    assert classify_request(policy, _authority("x.example"), "/share/tok/", "http") == "reject"
    assert classify_request(policy, _authority("x.example"), "/_twicc/share/x.js", "http") == "reject"
    assert classify_request(policy, _authority("x.example"), "/ws/share/tok/", "websocket") == "reject"
    assert classify_request(policy, _authority("x.example"), "/ws/", "websocket") == "inner_app"
    assert classify_request(policy, _authority("other.example"), "/peer/messages/", "http") == "reject"
    assert classify_request(policy, _authority("other.example"), "/api/sessions/", "http") == "inner_app"


def test_classify_request_quarantine_and_precedence():
    policy = build_origin_policy("https://x.example", "https://x.example:9443", "")
    # Hostname quarantine matches every port…
    assert classify_request(policy, _authority("x.example:9443"), "/api/sessions/", "http") == "reject"
    assert classify_request(policy, _authority("x.example:1234"), "/ws/", "websocket") == "reject"
    # …except the exact valid External authority (§11 precedence).
    assert classify_request(policy, _authority("x.example"), "/api/sessions/", "http") == "inner_app"
    # Peer stays hidden there: no valid shared Peer origin exists.
    assert classify_request(policy, _authority("x.example"), "/peer/messages/", "http") == "reject"


def test_get_origin_policy_memoizes_and_tracks_changes():
    settings = {"publicBaseUrl": "https://app.example", "shareBaseUrl": "", "peerBaseUrl": ""}
    first = get_origin_policy(settings)
    assert get_origin_policy(dict(settings)) is first
    changed = get_origin_policy({**settings, "peerBaseUrl": "https://peer.example"})
    assert changed is not first
    assert changed.dedicated_peer_authority == "peer.example"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_origin_policy.py -q`
Expected: FAIL — `ImportError: cannot import name 'OriginPolicy'`.

- [ ] **Step 3: Implement the builder and classifier**

Append to `src/twicc/core/services/origin_policy.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_origin_policy.py -q`
Expected: PASS. A failure in an interaction-case test means the union/precedence composition is wrong — the exact failure names the state (§11 rows compose cumulatively; rule order must never remove an earlier disable).

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `feat(origin): build the live origin routing policy and request classifier`

---

### Task 9: The common PublicOriginGate — ASGI executor, wiring, routing-table and Share-regression tests

**Files:**
- Create: `src/twicc/origin_gate.py`
- Delete: `src/twicc/share/asgi_filter.py`
- Modify: `src/twicc/asgi.py`
- Modify: `src/twicc/synced_settings.py`
- Test: `tests/test_share_host_gate.py` (rework in place — the spec mandates this file name)

**Interfaces:**
- Consumes: `request_authority_from_scope(scope) -> RequestAuthority | None` and `SHARE_ONLY_PREFIXES: tuple[str, ...]` (Task 7).
- Consumes: `get_origin_policy(settings: dict) -> OriginPolicy` and `classify_request(policy: OriginPolicy, authority: RequestAuthority | None, path: str, scope_type: str) -> str` (Task 8).
- Produces: `RoutingSettingsSnapshot(settings: dict, available: bool)` and `read_routing_settings() -> RoutingSettingsSnapshot` in `twicc.synced_settings`. The availability value describes the source observation that initialized the active cache. A missing file or empty object is available. An unreadable, malformed, or non-object source is unavailable until a successful atomic write or process restart loads a valid source. Later manual file edits do not change active settings until restart.
- Produces: `twicc.origin_gate` exporting `PublicOriginGate(full_app, share_only_app)` and `ShareOnlyApp(inner)` (it preserves all behavior from the old `share/asgi_filter.ShareOnlyApp`). `twicc.share.asgi_filter` no longer exists; nothing else imports it (verified: only `asgi.py`, the old tests, and one comment in `synced_settings.py` referenced it).
- Produces: module move `src/twicc/share/asgi_filter.py` → `src/twicc/origin_gate.py`.
- Produces: `_run(coro) -> object`.
- Produces: `_gate() -> tuple[PublicOriginGate, Recorder]`.
- Produces: `async _drive(app, scope) -> list[dict]`.
- Produces: `_http(path: str, host: str) -> dict` and `_ws(path: str, host: str) -> dict`.
- Produces: `_status(sent: list[dict]) -> int | None`, `_assert_plain_404(sent: list[dict], context=None) -> None`, and `_ws_close_code(sent: list[dict]) -> int | None`.
- Produces: `set_origins(public: str = "", share: str = "", peer: str = "") -> None` fixture.

- [ ] **Step 1: Create the gate module**

Create `src/twicc/origin_gate.py`:

```python
"""ASGI gate enforcing the common public-origin routing (peer-origin-routing
design §9-§11).

A single :class:`PublicOriginGate` replaces the former ``ShareHostGate``. It
wraps the application ABOVE BlackNoise, so it runs before static files, Django
and its SPA fallback, the raw ``/mcp`` endpoint, and application WebSockets.
Per request it reads the three origins from the active in-process cache, builds
the pure routing policy, classifies the request authority + path, and executes
the result:

  Share hostname                → ShareOnlyApp (existing Share-only policy)
  dedicated Peer authority      → only /peer/ HTTP; everything else 404/4404
  shared External+Peer authority→ full app, /peer/ included
  every other authority         → full app, but never /peer/
  quarantined / invalid Host    → plain 404, WebSocket close 4404
  unavailable routing settings → plain 404, WebSocket close 4404

Rejections answer the plain ``404 Not found`` (or close ``4404``) without
calling the inner application and without revealing the configured addresses.
The gate never repairs or writes settings.
"""

from __future__ import annotations

import logging

from twicc.core.services.origin_policy import (
    SHARE_ONLY_PREFIXES,
    classify_request,
    get_origin_policy,
    request_authority_from_scope,
)

logger = logging.getLogger(__name__)


def _share_only_allowed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SHARE_ONLY_PREFIXES) or path == "/favicon.ico"


async def _reply_404(send):
    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Not found"})


async def _reject_request(stype, send):
    if stype == "websocket":
        await send({"type": "websocket.close", "code": 4404})
        return
    await _reply_404(send)


async def _reply_204(send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _reply_redirect(send, location: str):
    # 302 Found — TEMPORARY on purpose: the share-host root points at /share/ for now,
    # but a real homepage could live there later, so it must not be cached permanently.
    await send({"type": "http.response.start", "status": 302,
                "headers": [(b"location", location.encode("latin1")),
                            (b"cache-control", b"no-store"),
                            (b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b""})


class ShareOnlyApp:
    """Wrap an ASGI app, exposing ONLY the share surface (used on the share host)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        path = scope.get("path", "")
        if stype == "http":
            if path == "/":
                # Share-host root → the recent-shares homepage (temporary redirect).
                return await _reply_redirect(send, "/share/")
            if path == "/favicon.ico":
                return await _reply_204(send)
            if not _share_only_allowed(path):
                return await _reply_404(send)
            return await self.inner(scope, receive, send)
        if stype == "websocket":
            if not path.startswith("/ws/share/"):
                await send({"type": "websocket.close", "code": 4404})
                return
            return await self.inner(scope, receive, send)
        # lifespan et al. pass through.
        return await self.inner(scope, receive, send)


class PublicOriginGate:
    """Route every HTTP request and WebSocket by its request authority against
    the live External / Share / Peer settings."""

    def __init__(self, full_app, share_only_app):
        self.full_app = full_app
        self.share_only_app = share_only_app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket"):
            return await self.full_app(scope, receive, send)
        authority = request_authority_from_scope(scope)
        if authority is None:
            return await _reject_request(stype, send)
        from twicc.synced_settings import read_routing_settings

        try:
            snapshot = read_routing_settings()
            if not snapshot.available:
                return await _reject_request(stype, send)
            policy = get_origin_policy(snapshot.settings)
        except Exception:
            logger.exception("Public-origin routing settings are unavailable")
            return await _reject_request(stype, send)
        surface = classify_request(policy, authority, scope.get("path", ""), stype)
        if surface == "share_surface":
            return await self.share_only_app(scope, receive, send)
        if surface == "inner_app":
            return await self.full_app(scope, receive, send)
        return await _reject_request(stype, send)
```

- [ ] **Step 2: Add the latched routing snapshot and rewire the application**

Delete `src/twicc/share/asgi_filter.py`.

In `src/twicc/synced_settings.py`, replace this exact import block:

```python
import tempfile
import threading

import orjson
```

with:

```python
import tempfile
import threading
from typing import NamedTuple

import orjson
```

Replace this exact cache block:

```python
# In-memory cache of the current synced settings (file content merged with defaults).
# Populated lazily on first read, then kept up-to-date by write_synced_settings().
# Empty dict means not yet initialized (initialized cache always has at least the defaults).
_cache: dict = {}

# Lock to serialize concurrent writes (and cache updates) to settings.json.
_settings_lock = threading.Lock()
```

with:

```python
class RoutingSettingsSnapshot(NamedTuple):
    settings: dict
    available: bool


# In-memory cache of the current synced settings (file content merged with defaults).
# Populated lazily on first read, then kept up-to-date by write_synced_settings().
# Empty dict means not yet initialized (initialized cache always has at least the defaults).
_cache: dict = {}

# False when the observation that initialized the active cache found an
# unreadable, malformed, or non-object source. General settings callers can
# use defaults, but public-origin routing must fail closed.
_routing_settings_available = True

# One reentrant lock serializes every public cache read and write. Existing
# read-modify-write callers already hold this lock, so nested calls must work.
_settings_lock = threading.RLock()
```

Replace the complete current `read_synced_settings` function:

```python
def read_synced_settings() -> dict:
    """Read synced settings, using the in-memory cache when available.

    On first call, reads settings.json, applies legacy migrations (rename/drop),
    merges with defaults, and populates the cache. If migrations changed
    anything, the cleaned data is written back to disk so the legacy keys
    disappear permanently.

    Returns a **copy** so callers can mutate freely without affecting the cache.
    """
    if not _cache:
        path = get_synced_settings_path()
        try:
            file_data = orjson.loads(path.read_bytes())
        except (FileNotFoundError, orjson.JSONDecodeError):
            file_data = {}
        migrated = _migrate_legacy_settings(file_data)
        _cache.update({**SYNCED_SETTINGS_DEFAULTS, **file_data})
        _cache.setdefault("_version", 0)
        if migrated:
            # Persist the cleaned data so old keys do not reappear next read.
            write_synced_settings(_cache.copy())
    return _cache.copy()
```

with:

```python
def read_synced_settings() -> dict:
    """Read settings and retain whether the cache-initializing load was valid.

    Missing settings are valid first-install defaults. Other read failures,
    malformed JSON, and non-object roots provide defaults to general callers
    but make public-origin routing unavailable. The active cache does not
    observe later manual file edits before a process restart.
    """
    global _routing_settings_available
    with _settings_lock:
        if not _cache:
            path = get_synced_settings_path()
            available = True
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                file_data = {}
            except OSError:
                logger.exception("Cannot read synced settings")
                file_data = {}
                available = False
            else:
                try:
                    file_data = orjson.loads(raw)
                except orjson.JSONDecodeError:
                    logger.exception("Cannot parse synced settings")
                    file_data = {}
                    available = False
                if available and not isinstance(file_data, dict):
                    logger.error("Synced settings JSON root is not an object")
                    file_data = {}
                    available = False
            migrated = available and _migrate_legacy_settings(file_data)
            _cache.update({**SYNCED_SETTINGS_DEFAULTS, **file_data})
            _cache.setdefault("_version", 0)
            _routing_settings_available = available
            if migrated:
                # Persist the cleaned data so old keys do not reappear next read.
                write_synced_settings(_cache.copy())
        return _cache.copy()


def read_routing_settings() -> RoutingSettingsSnapshot:
    """Return settings and availability from one active-cache observation."""
    with _settings_lock:
        settings = read_synced_settings()
        return RoutingSettingsSnapshot(settings, _routing_settings_available)
```

Replace the complete current `write_synced_settings` function:

```python
def write_synced_settings(data: dict) -> None:
    """Write synced settings to settings.json atomically and update the cache.

    Uses write-to-temp-then-rename to avoid partial writes.
    """
    path = get_synced_settings_path()
    content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Update the in-memory cache.
    _cache.clear()
    _cache.update({**SYNCED_SETTINGS_DEFAULTS, **data})
```

with:

```python
def write_synced_settings(data: dict) -> None:
    """Atomically write settings and publish one available cache snapshot."""
    global _routing_settings_available
    with _settings_lock:
        path = get_synced_settings_path()
        content = orjson.dumps(data, option=orjson.OPT_INDENT_2)

        # Publish neither cache nor availability before the atomic replacement.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        _cache.clear()
        _cache.update({**SYNCED_SETTINGS_DEFAULTS, **data})
        _routing_settings_available = True
```

All public reads and writes now acquire the same `RLock`. Existing callers can keep their outer read-modify-write lock. A first load cannot race another public read or write. The settings copy and availability flag always come from one cache publication.

The availability value describes the observation that initialized the active cache. A missing file and `{}` set it true. An initial unreadable, malformed, or non-object source sets it false. The cache intentionally does not re-read later manual file edits. Such edits do not change the active settings or policy until restart. A successful atomic write publishes new active settings and sets availability true immediately. A failed write changes neither value.

In `src/twicc/asgi.py`, replace:

```python
# Mandatory dedicated share origin (design §12): /share/ is served ONLY on the
# configured share host (the shareBaseUrl hostname) and NEVER on the working
# origin. The gate reads shareBaseUrl LIVE, so an Apply in Settings → Sharing takes
# effect on the next request with no restart. Wrapped ABOVE BlackNoise so the share
# host never reaches the /static/ mount it doesn't use.
from twicc.share.asgi_filter import ShareHostGate, ShareOnlyApp  # noqa: E402

application = ShareHostGate(application, ShareOnlyApp(application))
```

with:

```python
# Common public-origin gate (peer-origin-routing design §9-§11): routes the
# External, Share, and Peer addresses. /share/ is served ONLY on the configured
# Share hostname; /peer/ ONLY on the configured Peer authority; a dedicated Peer
# authority serves nothing else. The gate reads the active settings cache, so
# an Apply in Settings takes effect on the next request with no restart. Wrapped
# ABOVE BlackNoise so rejected authorities never reach the /static/ mount.
from twicc.origin_gate import PublicOriginGate, ShareOnlyApp  # noqa: E402

application = PublicOriginGate(application, ShareOnlyApp(application))
```

In `src/twicc/synced_settings.py`, replace the comment line:

```python
    # always requires it. The share host gate lives in share/asgi_filter.py,
```

with:

```python
    # always requires it. The origin gate lives in origin_gate.py,
```

- [ ] **Step 3: Rework the gate test suite**

Replace the ENTIRE content of `tests/test_share_host_gate.py` with the suite below. It keeps every pre-existing Share behavior check (spec §13.3), ports the harness to `PublicOriginGate`, and adds the §10 routing-table rows, routing-settings availability failures, cache-observation and first-load serialization cases, both recovery paths, the live-change case, and the bracketed-IPv6 Share case. (Task 10 appends the Host-boundary and runtime-invalid suites to this same file.)

```python
"""The common public-origin gate (peer-origin-routing design §9-§11): /share/
is served ONLY on the Share hostname, /peer/ ONLY on the Peer authority, and a
dedicated Peer authority serves nothing else."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import orjson
import pytest

from twicc.origin_gate import PublicOriginGate, ShareOnlyApp


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Recorder:
    """A stub inner app that answers 200 and records that it was reached."""
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"full-app"})


async def _drive(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(m):
        sent.append(m)

    await app(scope, receive, send)
    return sent


def _http(path, host):
    return {"type": "http", "path": path, "headers": [(b"host", host.encode("latin1"))]}


def _ws(path, host):
    return {"type": "websocket", "path": path, "headers": [(b"host", host.encode("latin1"))]}


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def _assert_plain_404(sent, context=None):
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1, context
    assert starts[0]["status"] == 404, context
    assert (b"content-type", b"text/plain") in starts[0]["headers"], context
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert body == b"Not found", context


def _ws_close_code(sent):
    for m in sent:
        if m["type"] == "websocket.close":
            return m.get("code")
    return None


def _location(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            for name, value in m["headers"]:
                if name == b"location":
                    return value.decode("latin1")
    return None


@pytest.fixture
def set_origins(monkeypatch):
    def _set(public="", share="", peer=""):
        monkeypatch.setattr(
            "twicc.synced_settings.read_routing_settings",
            lambda: SimpleNamespace(
                settings={"publicBaseUrl": public, "shareBaseUrl": share, "peerBaseUrl": peer},
                available=True,
            ),
        )
    return _set


def _gate():
    full = Recorder()
    return PublicOriginGate(full, ShareOnlyApp(full)), full


def test_real_asgi_application_has_the_gate_above_blacknoise():
    from blacknoise import BlackNoise
    from twicc.asgi import application

    assert isinstance(application, PublicOriginGate)
    assert isinstance(application.full_app, BlackNoise)
    assert isinstance(application.share_only_app, ShareOnlyApp)
    assert application.share_only_app.inner is application.full_app


# ── Share host unset → sharing disabled everywhere ──────────────────────────

def test_unset_host_404s_share_everywhere(set_origins):
    set_origins()
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_unset_host_serves_working_app(set_origins):
    set_origins()
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


@pytest.mark.parametrize("content", [None, b"{}"])
def test_missing_or_empty_settings_use_the_valid_default_policy(content, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    if content is not None:
        path.write_bytes(content)
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
        assert _status(sent) == 200
        assert full.called
        assert ss.read_routing_settings().available is True
    finally:
        ss._cache.clear()


def test_cached_valid_settings_ignore_later_manual_edits_until_restart(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(b'{"peerBaseUrl":"https://peer.example"}')
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        initial = ss.read_routing_settings()
        assert initial.available is True
        assert initial.settings["peerBaseUrl"] == "https://peer.example"

        path.write_bytes(b"{")
        unchanged = ss.read_routing_settings()
        assert unchanged == initial

        # Clearing the cache simulates process initialization after restart.
        ss._cache.clear()
        assert ss.read_routing_settings().available is False
    finally:
        ss._cache.clear()


def test_manual_repair_requires_cache_reinitialization(tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    path.write_bytes(b"{")
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        assert ss.read_routing_settings().available is False
        path.write_bytes(b'{"peerBaseUrl":"https://peer.example"}')
        assert ss.read_routing_settings().available is False

        # A restart creates a new cache and observes the repaired file.
        ss._cache.clear()
        repaired = ss.read_routing_settings()
        assert repaired.available is True
        assert repaired.settings["peerBaseUrl"] == "https://peer.example"
    finally:
        ss._cache.clear()


def test_general_and_routing_first_reads_share_one_source_observation(monkeypatch):
    import twicc.synced_settings as ss

    class CoordinatedPath:
        def __init__(self):
            self.calls = 0
            self._count_lock = threading.Lock()
            self._second_entered = threading.Event()

        def read_bytes(self):
            with self._count_lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                # An unlocked second read enters before this returns. A locked
                # second read waits and then sees the populated cache.
                self._second_entered.wait(timeout=0.25)
                return b"{"
            self._second_entered.set()
            return b'{"peerBaseUrl":"https://peer.example"}'

    path = CoordinatedPath()
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    ready = threading.Barrier(3)

    def read_general():
        ready.wait()
        return ss.read_synced_settings()

    def read_routing():
        ready.wait()
        return ss.read_routing_settings()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            general_future = pool.submit(read_general)
            routing_future = pool.submit(read_routing)
            ready.wait()
            general = general_future.result(timeout=2)
            snapshot = routing_future.result(timeout=2)
        assert path.calls == 1
        assert general == snapshot.settings
        assert snapshot.available is False
    finally:
        ss._cache.clear()


@pytest.mark.parametrize("failure", ["malformed", "non_object", "unreadable"])
def test_unavailable_routing_settings_never_reach_either_delegate(failure, tmp_path, monkeypatch):
    import twicc.synced_settings as ss

    path = tmp_path / "settings.json"
    if failure == "malformed":
        path.write_bytes(b"{")
    elif failure == "non_object":
        path.write_bytes(b"[]")
    else:
        class UnreadableSettingsPath:
            def read_bytes(self):
                raise PermissionError("unreadable settings")

        path = UnreadableSettingsPath()
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    try:
        # The unavailable state survives when a non-routing caller initializes
        # the shared cache before the gate.
        ss.read_synced_settings()
        assert ss.read_routing_settings().available is False
        for scope in (
            _http("/api/sessions/", "former-peer.example"),
            _http("/share/tok/", "former-share.example"),
            _ws("/ws/share/tok/", "former-share.example"),
        ):
            full = Recorder()
            share = Recorder()
            gate = PublicOriginGate(full, share)
            sent = _run(_drive(gate, scope))
            if scope["type"] == "http":
                _assert_plain_404(sent)
            else:
                assert _ws_close_code(sent) == 4404
            assert not full.called
            assert not share.called
    finally:
        ss._cache.clear()


@pytest.mark.parametrize("failure", ["read", "build"])
def test_routing_read_or_policy_build_exception_never_reaches_either_delegate(failure, monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_routing_settings",
        lambda: SimpleNamespace(settings={}, available=True),
    )
    if failure == "read":
        def fail_read():
            raise OSError("read failed")

        monkeypatch.setattr("twicc.synced_settings.read_routing_settings", fail_read)
    else:
        def fail_build(_settings):
            raise ValueError("build failed")

        monkeypatch.setattr("twicc.origin_gate.get_origin_policy", fail_build)
    for scope in (
        _http("/api/sessions/", "app.example"),
        _http("/share/tok/", "share.example"),
        _ws("/ws/share/tok/", "share.example"),
    ):
        full = Recorder()
        share = Recorder()
        gate = PublicOriginGate(full, share)
        sent = _run(_drive(gate, scope))
        if scope["type"] == "http":
            _assert_plain_404(sent)
        else:
            assert _ws_close_code(sent) == 4404
        assert not full.called
        assert not share.called


def test_settings_cli_envelope_restores_routing_after_invalid_load(tmp_path, monkeypatch):
    import twicc.synced_settings as ss
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.drop_requests_watcher import execute_drop_payload

    path = tmp_path / "settings.json"
    path.write_bytes(b"{")
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    drop_dir = tmp_path / "drop-requests"
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir",
        lambda: drop_dir,
    )
    ss._cache.clear()
    try:
        assert ss.read_routing_settings().available is False
        for scope in (
            _http("/peer/messages/", "peer.example"),
            _ws("/ws/", "peer.example"),
        ):
            full = Recorder()
            share = Recorder()
            gate = PublicOriginGate(full, share)
            sent = _run(_drive(gate, scope))
            if scope["type"] == "http":
                _assert_plain_404(sent)
            else:
                assert _ws_close_code(sent) == 4404
            assert not full.called
            assert not share.called

        dropped = write_drop_file(
            {
                "patch": {"peerBaseUrl": "https://peer.example"},
                "broadcast": False,
            },
            kind="settings:update",
        )
        assert dropped.path.exists()
        envelope = orjson.loads(dropped.path.read_bytes())
        assert envelope["payload"]["kind"] == "settings:update"
        result = _run(execute_drop_payload(
            envelope["payload"],
            envelope["payload"]["kind"],
        ))
        assert result["status"] == "updated"
        assert ss.read_routing_settings().available is True

        full = Recorder()
        share = Recorder()
        gate = PublicOriginGate(full, share)
        sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
        assert _status(sent) == 200
        assert full.called
        assert not share.called
    finally:
        ss._cache.clear()


def test_invalid_share_setting_disables_sharing_and_quarantines(set_origins):
    set_origins(share="ftp://share.example.com")
    gate, full = _gate()
    # The recognizable hostname is quarantined: nothing is served there.
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called
    sent = _run(_drive(gate, _http("/api/sessions/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


# ── On the share host → only the share surface ──────────────────────────────

def test_share_host_serves_share(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_share_host_404s_non_share_paths(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_share_host_404s_peer_paths(set_origins):
    set_origins(
        public="https://app.example",
        share="https://share.example.com",
        peer="https://peer.example:8443",
    )
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "share.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_share_host_allows_shared_assets(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/_twicc/artifact-shell/shell.js", "share.example.com")))
    assert _status(sent) == 200


def test_share_host_favicon_204(set_origins):
    set_origins(share="share.example.com")
    gate, _full = _gate()
    sent = _run(_drive(gate, _http("/favicon.ico", "share.example.com")))
    assert _status(sent) == 204


def test_share_host_root_redirects_to_share_temporarily(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/", "share.example.com")))
    # 302 (temporary), NOT 301/308 — a real homepage could live here later.
    assert _status(sent) == 302
    assert _status(sent) not in (301, 308)
    assert _location(sent) == "/share/"
    assert not full.called


def test_share_hostname_matches_any_request_port(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    # Share routing compares only the hostname (§5.3).
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com:9999")))
    assert _status(sent) == 200
    assert full.called


def test_bracketed_ipv6_share_host_selects_share_boundary(set_origins):
    set_origins(share="https://[::1]:8443")
    gate, full = _gate()
    # An expanded request spelling canonicalizes to the same Share hostname (§13.3).
    sent = _run(_drive(gate, _http("/share/tok/", "[0:0:0:0:0:0:0:1]:8443")))
    assert _status(sent) == 200
    assert full.called
    sent = _run(_drive(gate, _http("/api/sessions/", "[::1]:8443")))
    _assert_plain_404(sent)


def test_working_origin_root_serves_app(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    # On the working origin, / is the SPA — never redirected to /share/.
    sent = _run(_drive(gate, _http("/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── On the working origin → share surface invisible ─────────────────────────

def test_working_origin_404s_share(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "app.example.com")))
    _assert_plain_404(sent)
    assert not full.called


def test_working_origin_serves_app(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "app.example.com")))
    assert _status(sent) == 200
    assert full.called


def test_full_url_share_base_extracts_hostname(set_origins):
    set_origins(share="https://share.example.com/")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/share/tok/", "share.example.com")))
    assert _status(sent) == 200
    assert full.called


# ── WebSocket ───────────────────────────────────────────────────────────────

def test_ws_share_closed_on_working_origin(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "app.example.com")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_ws_non_share_closed_on_share_host(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "share.example.com")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_ws_share_reaches_inner_on_share_host(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "share.example.com")))
    assert _ws_close_code(sent) is None
    assert full.called


def test_ws_app_reaches_inner_on_working_origin(set_origins):
    set_origins(share="share.example.com")
    gate, full = _gate()
    _run(_drive(gate, _ws("/ws/", "app.example.com")))
    assert full.called


def test_lifespan_passes_through(set_origins):
    set_origins()
    gate, full = _gate()
    _run(_drive(gate, {"type": "lifespan"}))
    assert full.called


# ── Peer routing table (§10) ────────────────────────────────────────────────

def test_dedicated_peer_serves_only_peer_http(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    for path, expected_status, expect_inner in [
        ("/peer/messages/", 200, True),
        ("/peer/handshake/request/", 200, True),
        ("/", 404, False),
        ("/api/sessions/", 404, False),
        ("/static/assets/app.js", 404, False),
        ("/mcp", 404, False),
        ("/rpc/sessions/", 404, False),
        ("/artifacts/abc/", 404, False),
        ("/share/tok/", 404, False),
        ("/favicon.ico", 404, False),
    ]:
        gate, full = _gate()
        sent = _run(_drive(gate, _http(path, "peer.example:8443")))
        if expected_status == 404:
            _assert_plain_404(sent, path)
        else:
            assert _status(sent) == expected_status, path
        assert full.called is expect_inner, path


def test_dedicated_peer_closes_every_websocket(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    for path in ("/ws/", "/ws/share/tok/", "/ws/terminal/1/"):
        gate, full = _gate()
        sent = _run(_drive(gate, _ws(path, "peer.example:8443")))
        assert _ws_close_code(sent) == 4404, path
        assert not full.called, path


def test_shared_peer_routing_table(set_origins):
    set_origins(public="https://x.example", peer="https://x.example")
    for path, expected_status, expect_inner in [
        ("/peer/messages/", 200, True),
        ("/api/sessions/", 200, True),
        ("/share/tok/", 404, False),
    ]:
        gate, full = _gate()
        sent = _run(_drive(gate, _http(path, "x.example")))
        if expected_status == 404:
            _assert_plain_404(sent, path)
        else:
            assert _status(sent) == expected_status, path
        assert full.called is expect_inner, path

    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", "x.example")))
    assert _ws_close_code(sent) is None
    assert full.called

    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", "x.example")))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_other_authorities_never_serve_peer(set_origins):
    set_origins(public="https://app.example", share="share.example.com", peer="https://peer.example:8443")
    for host in ("app.example", "localhost:3501", "192.168.1.42:3501", "tunnel.example"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        _assert_plain_404(sent, host)
        assert not full.called, host
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/api/sessions/", host)))
        assert _status(sent) == 200, host
        assert full.called, host


def test_empty_peer_hides_peer_everywhere(set_origins):
    set_origins(public="https://app.example", share="share.example.com")
    for host in ("app.example", "share.example.com", "localhost:3501"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        _assert_plain_404(sent, host)
        assert not full.called, host


def test_peer_hostname_without_port_is_not_the_peer_authority(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    gate, full = _gate()
    # Same hostname, no port: just another authority — app yes, /peer/ no.
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "peer.example")))
    assert _status(sent) == 200


def test_default_port_host_does_not_match_portless_peer_authority(set_origins):
    set_origins(peer="https://peer.example")
    gate, full = _gate()
    # `Host: peer.example:443` matches only a configured `peer.example:443` (§8).
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:443")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    assert _status(sent) == 200
    assert full.called


def test_ambiguous_scheme_only_difference_disables_peer(set_origins):
    set_origins(public="https://x.example", peer="http://x.example")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "x.example")))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    # The exact External authority keeps the app (§11 precedence).
    sent = _run(_drive(gate, _http("/api/sessions/", "x.example")))
    assert _status(sent) == 200


# ── Live setting changes (§12) ──────────────────────────────────────────────

def test_live_setting_change_routes_next_request(monkeypatch):
    state = {"publicBaseUrl": "https://app.example", "shareBaseUrl": "", "peerBaseUrl": ""}
    monkeypatch.setattr(
        "twicc.synced_settings.read_routing_settings",
        lambda: SimpleNamespace(settings=dict(state), available=True),
    )
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    _assert_plain_404(sent)
    state["peerBaseUrl"] = "https://peer.example"
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example")))
    assert _status(sent) == 200
    assert full.called
```

- [ ] **Step 4: Run the suite and the wiring neighbors**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_share_host_gate.py -q`
Expected: PASS. The unavailable-settings tests fail if malformed, non-object, or unreadable settings reach either delegate. Their Share HTTP and Share WebSocket probes also fail if the gate wrongly delegates to the Share-only boundary. The default-policy test fails if a missing file or `{}` becomes unavailable. The observation tests fail if a manual edit changes the active cache without restart. The CLI-envelope recovery test fails if local submission cannot create the request, if `settings:update` is missing or stale in the watcher handler map, if dispatch rejects the patch, or if the next request remains unavailable. It does not clear the cache or simulate restart. The coordinated two-reader test fails if public reads can publish different first-load observations. Then run the share surface suites that exercise the app end to end:
`TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_share_public_routes.py tests/test_share_consumer.py tests/test_share_updates_consumer.py -q`
Expected: PASS. The gate suite proves that `twicc.asgi.application` is a `PublicOriginGate` directly above BlackNoise. The lower-layer Share suites remain regression checks below that gate.

- [ ] **Step 5: Check nothing still imports the deleted module**

Run: `rg -n "share\.asgi_filter|ShareHostGate" src tests -g "*.py"`
Expected: NO output. Any hit is a stale import to fix (this catches collateral damage the suites might miss).

- [ ] **Step 6: Commit**

Commit the changes produced by this task.
Subject: `feat(origin): route all origins through the common public-origin gate`

---

### Task 10: Gate-level Host boundaries and runtime-invalid interaction states

**Files:**
- Test: `tests/test_share_host_gate.py`

**Interfaces:**
- Consumes: `_run(coro) -> object` (Task 9).
- Consumes: `_gate() -> tuple[PublicOriginGate, Recorder]` (Task 9).
- Consumes: `async _drive(app, scope) -> list[dict]` (Task 9).
- Consumes: `_http(path: str, host: str) -> dict` and `_ws(path: str, host: str) -> dict` (Task 9).
- Consumes: `_status(sent: list[dict]) -> int | None`, `_assert_plain_404(sent: list[dict], context=None) -> None`, and `_ws_close_code(sent: list[dict]) -> int | None` (Task 9).
- Consumes: `set_origins(public: str = "", share: str = "", peer: str = "") -> None` fixture (Task 9).
- Produces: nothing new — test-only task covering spec §13.2's request-authority boundary list and the three runtime-invalid interaction cases at the ASGI level.

- [ ] **Step 1: Append the Host-boundary suite**

Append to `tests/test_share_host_gate.py`:

```python
# ── Request-authority boundaries (§8, §13.2) ────────────────────────────────

def test_missing_host_rejects_whole_request(set_origins):
    set_origins(public="https://app.example")
    gate, full = _gate()
    sent = _run(_drive(gate, {"type": "http", "path": "/api/sessions/", "headers": []}))
    _assert_plain_404(sent)
    assert not full.called
    gate, full = _gate()
    sent = _run(_drive(gate, {"type": "websocket", "path": "/ws/", "headers": []}))
    assert _ws_close_code(sent) == 4404
    assert not full.called


def test_duplicate_host_rejects_whole_request(set_origins):
    set_origins(public="https://app.example")
    scope = {"type": "http", "path": "/api/sessions/",
             "headers": [(b"host", b"app.example"), (b"host", b"app.example")]}
    gate, full = _gate()
    sent = _run(_drive(gate, scope))
    _assert_plain_404(sent)
    assert not full.called
    ws_scope = {"type": "websocket", "path": "/ws/",
                "headers": [(b"host", b"app.example"), (b"host", b"app.example")]}
    gate, full = _gate()
    sent = _run(_drive(gate, ws_scope))
    assert _ws_close_code(sent) == 4404
    assert not full.called


@pytest.mark.parametrize("host", [
    "",
    "app example",
    "exämple.com",
    "%65xample.com",
    "xn--.example",
    "XN--.example",
    "xn--a-ecp.example",
    "xn--e28h.example",
    "a..example",
    "app.example.",
    "-a.example",
    "a-.example",
    "my_host.example",
    "999.1.2.3",
    "1.2.3",
    "192.168.001.1",
    "::1",
    "[::1",
    "[1.2.3.4]",
    "[fe80::1%eth0]",
    "[fe80::1%25eth0]",
    "app.example:",
    "app.example:bad",
    "app.example:70000",
    "exa\tmple.com",
    "app.example:8\t0",
    "app.example\n",
    f"app.example:{'9' * 5000}",
])
def test_malformed_host_rejects_whole_request(set_origins, host):
    set_origins(public="https://app.example")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host)))
    _assert_plain_404(sent, host)
    assert not full.called, host
    gate, full = _gate()
    sent = _run(_drive(gate, _ws("/ws/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def test_uppercase_and_alabel_hosts_canonicalize(set_origins):
    set_origins(public="https://app.example", peer="https://xn--fa-hia.de")
    gate, full = _gate()
    # `Host: XN--FA-HIA.DE` is accepted as routing authority `xn--fa-hia.de`.
    sent = _run(_drive(gate, _http("/peer/messages/", "XN--FA-HIA.DE")))
    assert _status(sent) == 200
    assert full.called
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", "APP.example")))
    assert _status(sent) == 200
    assert full.called


def test_dns_length_boundaries_in_host(set_origins):
    set_origins(public="https://app.example")
    label63 = "a" * 63
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", f"{label63}.example")))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", f"{'a' * 64}.example")))
    _assert_plain_404(sent)
    host253 = ".".join([label63] * 3 + ["a" * 61])
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host253)))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/api/sessions/", host253 + "a")))
    _assert_plain_404(sent)


def test_ipv6_host_spellings_canonicalize_to_one_authority(set_origins):
    set_origins(public="https://app.example", peer="https://[::1]:8443")
    for host in ("[::1]:8443", "[0:0:0:0:0:0:0:1]:8443"):
        gate, full = _gate()
        sent = _run(_drive(gate, _http("/peer/messages/", host)))
        assert _status(sent) == 200, host
        assert full.called, host


def test_explicit_request_port_is_preserved(set_origins):
    set_origins(public="https://app.example", peer="https://peer.example:8443")
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:8443")))
    assert _status(sent) == 200
    gate, full = _gate()
    sent = _run(_drive(gate, _http("/peer/messages/", "peer.example:8444")))
    _assert_plain_404(sent)
```

- [ ] **Step 2: Append the runtime-invalid interaction suite**

Append to `tests/test_share_host_gate.py`:

```python
# ── Runtime-invalid interaction basis (§11, §13.2) ──────────────────────────

def _assert_quarantined(gate_factory, host):
    """Every HTTP request → plain 404, every WebSocket → 4404, no inner call."""
    for path in ("/", "/api/sessions/", "/share/tok/", "/peer/messages/", "/static/assets/app.js"):
        gate, full = gate_factory()
        sent = _run(_drive(gate, _http(path, host)))
        _assert_plain_404(sent, (host, path))
        assert not full.called, (host, path)
    gate, full = gate_factory()
    sent = _run(_drive(gate, _ws("/ws/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def _assert_serves_app(gate_factory, host):
    gate, full = gate_factory()
    sent = _run(_drive(gate, _http("/api/sessions/", host)))
    assert _status(sent) == 200, host
    assert full.called, host
    gate, full = gate_factory()
    _run(_drive(gate, _ws("/ws/", host)))
    assert full.called, host


def _assert_disabled_surfaces(gate_factory, host):
    """Share-exclusive and /peer/ paths answer their defined gate responses."""
    for path in ("/share/tok/", "/_twicc/share/x.js", "/peer/messages/"):
        gate, full = gate_factory()
        sent = _run(_drive(gate, _http(path, host)))
        _assert_plain_404(sent, (host, path))
        assert not full.called, (host, path)
    gate, full = gate_factory()
    sent = _run(_drive(gate, _ws("/ws/share/tok/", host)))
    assert _ws_close_code(sent) == 4404, host
    assert not full.called, host


def test_interaction_case_1_valid_external_invalid_share_invalid_peer(set_origins):
    # Disabled: Share and Peer. Surviving quarantine: share.example and
    # peer.example. Exact External exception: app.example.
    set_origins(public="https://app.example", share="ftp://share.example",
                peer="https://peer.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_quarantined(_gate, "peer.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_interaction_case_2_invalid_external_valid_share_conflicting_peer(set_origins):
    # The recognizable invalid Peer operand joins the Share-and-Peer conflict
    # and disables the otherwise valid Share. Disabled: Share and Peer
    # (including Peer classification). Surviving quarantine: share.example.
    # No External exception (External is invalid).
    set_origins(public="https://", share="https://share.example",
                peer="https://share.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_interaction_case_3_peer_conflicts_with_external(set_origins):
    # Before precedence the quarantine candidates are share.example and
    # app.example; valid External precedence removes only app.example.
    set_origins(public="https://app.example", share="ftp://share.example",
                peer="https://app.example/forbidden")
    _assert_quarantined(_gate, "share.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")
    _assert_serves_app(_gate, "other.example")
    _assert_disabled_surfaces(_gate, "other.example")


def test_runtime_share_external_conflict_disables_share_but_keeps_external(set_origins):
    # A manual conflict disables Share. Valid External precedence keeps the
    # application on the exact External authority.
    set_origins(public="https://app.example", share="https://app.example")
    _assert_serves_app(_gate, "app.example")
    _assert_disabled_surfaces(_gate, "app.example")


def test_invalid_external_quarantines_valid_peer_authority(set_origins):
    # §11 last row: invalid non-empty External disables Peer classification and
    # quarantines the configured Peer authority when recognizable.
    set_origins(public="https://", peer="https://peer.example")
    _assert_quarantined(_gate, "peer.example")
    _assert_serves_app(_gate, "other.example")


def test_unrecognizable_invalid_settings_disable_without_quarantine(set_origins):
    # A setting too malformed to name an authority disables its surface but
    # cannot quarantine an unknown host; other authorities keep the app.
    set_origins(public="https://app.example", share="https://", peer="https://")
    _assert_serves_app(_gate, "app.example")
    _assert_serves_app(_gate, "tunnel.example")
    _assert_disabled_surfaces(_gate, "tunnel.example")
```

- [ ] **Step 3: Run the full backend suite**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_share_host_gate.py tests/test_origin_policy.py -q`
Expected: PASS. Concrete failures this suite exists to catch (§13.2): an inner-app call on a surviving quarantine authority, an exposed disabled surface, a non-404 HTTP result, a WebSocket close other than 4404, or a lost application route at the exact External exception.

- [ ] **Step 4: Run the complete backend test suite once**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest -q`
Expected: PASS. This is the first task after the wiring change; a failure in an unrelated suite (e.g. peer handshake tests driving views directly) means the gate leaked into a path it must not touch — Django test clients bypass the ASGI gate, so such suites must be unaffected.

- [ ] **Step 5: Commit**

Commit the changes produced by this task.
Subject: `test(origin): cover host boundaries and runtime-invalid interaction states`

---

### Task 11: Per-field Settings Apply with backend-authoritative commits

**Files:**
- Modify: `src/twicc/asgi.py`
- Modify: `frontend/src/utils/publicOrigin.js`
- Create: `frontend/src/utils/originSettingsForm.js`
- Modify: `frontend/src/composables/useWebSocket.js`
- Modify: `frontend/src/stores/settings.js`
- Modify: `frontend/src/components/app/SettingsPopover.vue`
- Test: `tests/test_synced_settings_ws.py` (new file)
- Test: `frontend/src/utils/originSettingsForm.test.js` (new file)
- Test: `frontend/src/stores/publicOriginSettings.test.js` (rework in place)

**Interfaces:**
- Consumes: `checkPublicOriginInput(value) -> { value: string | null, error: string | null, scheme: string | null, hostname: string | null, port: null, authority: null }` and `usablePublicOrigin(value) -> string` (Task 3).
- Consumes: `ORIGIN_CONFLICT_SHARE_EXTERNAL = "origin_conflict_share_external_hostname"`, `ORIGIN_CONFLICT_SHARE_PEER = "origin_conflict_share_peer_hostname"`, and `ORIGIN_CONFLICT_AMBIGUOUS = "origin_conflict_ambiguous_authority"` (Task 2), plus `SettingsDropError(field: str, code: str, message: str)` values serialized by Task 6's existing validation-error WebSocket path.
- Produces: `ORIGIN_SETTING_KEYS`, `validateOriginSetting({ field, input, stored, locationHostname }) -> { errors, warning, patch }`, `originSettingErrorMessage(errors, field, messageFor) -> string`, `refreshOriginInput(currentInput, previousStored, nextStored) -> string`, `discardOriginSettingWrites(pendingWrites, field) -> void`, and `resolveOriginSettingResult(pendingWrites, payload, currentInput) -> { field: string, status: string, value: string | null, errors: Array } | null` in `frontend/src/utils/originSettingsForm.js`. A patch contains zero or one trimmed raw field. An exact non-empty no-op that fails the stored-value guard returns a `retained_stored_value` field error. This keeps the visible retained-value error stable. The backend supplies every relationship verdict and canonical value.
- Produces: `sendSyncedSettings(settings, baseVersion, requestId) -> boolean` and `settings.sendOriginSetting(field, value, requestId) -> Promise<boolean>`. The store sends one-field patches and never mutates an origin value.
- Produces: WebSocket request field `request_id: string` and direct frame `synced_settings_result { request_id, status, settings, version, errors }`. The handler sends this frame after the accepted broadcast or rejected full resync. The `settings` object contains the authoritative values for the submitted keys.
- Produces: browser event `twicc:origin-settings-result` with the direct result frame. The popover resolves only the matching ID. Each pending entry stores `{ field, input }`, where `input` is the visible text snapshot at Apply time. The transmitted field value is separate. Broadcasts update Pinia and typed inputs, but never settle an Apply. A WebSocket disconnect clears IDs whose results cannot arrive on the replacement connection.
- Removes: `setPublicBaseUrl`, `setShareBaseUrl`, `setPeerBaseUrl`, and the temporary `normalizePublicOrigin` alias. The construction adds no Promise queue, acknowledgement registry, or cross-field staging.

The identifier-free construction is not robust. The backend can send an accepted broadcast or a rejected resync before the verdict details. Payload shape cannot attribute two concurrent writes. It also cannot tell whether a later edit still represents the submitted value. Task 11 therefore uses the smallest correlation mechanism that covers every outcome:

1. `generateUUID()` supplies one ID per Apply. Its `getRandomValues` fallback works on plain-HTTP LAN origins.
2. The backend sends one direct result with that ID after its broadcast or resync. Frame order does not carry meaning.
3. An accepted result contains the authoritative value. This covers corrections and accepted no-change writes.
4. A rejected result contains the errors. A stale-version rejection has an empty error list and retains the typed value.
5. An ID-keyed map permits back-to-back verdicts without confusing fields or values. Its `input` property always means the visible text snapshot at Apply time. It never means the transmitted trimmed value. The map is only correlation state.
6. An input event discards older entries for that field. A late verdict then cannot replace new text or restore an obsolete error.
7. A WebSocket disconnect clears the map. Reliable in-connection delivery, matching results, input events, send failures, disconnects, and unmount bound every entry without a timer or Promise queue.

A transport loss can discard an undelivered rejection result. The original lost-result schedule, clear-before-result schedule, and rejected-Apply-across-reconnect schedule are this same failure. The map belongs to one socket, so the replacement connection cannot receive that result. Store reconciliation still preserves typed input. The user can Apply again after reconnect. The plan does not add retry, replay, a timer, or a general delivery protocol.

- [ ] **Step 1: Write the failing per-field form tests**

Create `frontend/src/utils/originSettingsForm.test.js`:

```js
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    discardOriginSettingWrites,
    originSettingErrorMessage,
    refreshOriginInput,
    resolveOriginSettingResult,
    validateOriginSetting,
} from './originSettingsForm.js'

const TWO_INVALID = {
    publicBaseUrl: 'ftp://app.example',
    shareBaseUrl: 'ftp://share.example',
    peerBaseUrl: '',
}

test('two invalid stored origins can be repaired in either order', () => {
    for (const [firstField, firstValue, secondField, secondValue] of [
        ['publicBaseUrl', 'https://app.example', 'shareBaseUrl', 'https://share.example'],
        ['shareBaseUrl', 'https://share.example', 'publicBaseUrl', 'https://app.example'],
    ]) {
        const first = validateOriginSetting({
            field: firstField, input: firstValue, stored: TWO_INVALID, locationHostname: 'localhost',
        })
        assert.deepEqual(first.errors, [])
        assert.deepEqual(first.patch, { [firstField]: firstValue })
        const second = validateOriginSetting({
            field: secondField,
            input: secondValue,
            stored: { ...TWO_INVALID, ...first.patch },
            locationHostname: 'localhost',
        })
        assert.deepEqual(second.errors, [])
        assert.deepEqual(second.patch, { [secondField]: secondValue })
    }
})

test('frontend defers relationship conflicts to the backend', () => {
    const result = validateOriginSetting({
        field: 'peerBaseUrl',
        input: 'http://x.example',
        stored: { publicBaseUrl: 'https://x.example', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.errors, [])
    assert.deepEqual(result.patch, { peerBaseUrl: 'http://x.example' })
})

test('frontend defers structural rules outside its safe subset', () => {
    for (const input of [
        'https://a..example',
        'https://example.com:bad',
        'https://example.com/base',
        'https://[xyz]',
        'https://xn--e28h.example',
    ]) {
        const result = validateOriginSetting({
            field: 'peerBaseUrl',
            input,
            stored: TWO_INVALID,
            locationHostname: 'localhost',
        })
        assert.deepEqual(result.errors, [], input)
        assert.deepEqual(result.patch, { peerBaseUrl: input }, input)
    }
})

test('an unchanged retained invalid origin keeps its visible error after Apply', () => {
    const result = validateOriginSetting({
        field: 'publicBaseUrl',
        input: 'https://a..example',
        stored: {
            publicBaseUrl: 'https://a..example',
            shareBaseUrl: '',
            peerBaseUrl: '',
        },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.patch, {})
    assert.deepEqual(result.errors, [{
        field: 'publicBaseUrl', code: 'retained_stored_value',
    }])
    assert.equal(originSettingErrorMessage(
        result.errors,
        'publicBaseUrl',
        () => 'Enter a hostname or an HTTP(S) origin without a path, query, or fragment.',
    ), 'Enter a hostname or an HTTP(S) origin without a path, query, or fragment.')
})

test('only the applied field errors appear in the active section', () => {
    const message = originSettingErrorMessage([
        { field: 'peerBaseUrl', code: 'first', message: 'First message.' },
        { field: 'peerBaseUrl', code: 'second', message: 'Second message.' },
        { field: 'publicBaseUrl', code: 'other', message: 'Hidden message.' },
    ], 'peerBaseUrl', code => code)
    assert.equal(message, 'First message. Second message.')
})

test('authoritative refresh preserves typed input after a stale resync', () => {
    assert.equal(refreshOriginInput('https://typed.example', 'https://old.example', 'https://remote.example'),
        'https://typed.example')
})

test('authoritative refresh follows remote state when the input is untouched', () => {
    assert.equal(refreshOriginInput('https://old.example', 'https://old.example', 'https://remote.example'),
        'https://remote.example')
})

test('correlated acceptances expose corrections and accepted no-change values', () => {
    const pending = new Map([
        ['corrected', { field: 'publicBaseUrl', input: 'HTTPS://APP.EXAMPLE:443/' }],
        ['unchanged', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'corrected',
        status: 'accepted',
        settings: { publicBaseUrl: 'https://app.example' },
        errors: [],
    }, 'HTTPS://APP.EXAMPLE:443/'), {
        field: 'publicBaseUrl', status: 'accepted', value: 'https://app.example', errors: [],
    })
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'unchanged',
        status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' },
        errors: [],
    }, 'https://share.example'), {
        field: 'shareBaseUrl', status: 'accepted', value: 'https://share.example', errors: [],
    })
    assert.equal(pending.size, 0)
})

test('outer-trimmed Apply correlates by visible text and adopts the canonical value', () => {
    const visibleInput = '  HTTPS://PEER.EXAMPLE:443/\r\n'
    const prepared = validateOriginSetting({
        field: 'peerBaseUrl',
        input: visibleInput,
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(prepared.patch, { peerBaseUrl: 'HTTPS://PEER.EXAMPLE:443/' })
    const pending = new Map([
        ['trimmed', { field: 'peerBaseUrl', input: visibleInput }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'trimmed',
        status: 'accepted',
        settings: { peerBaseUrl: 'https://peer.example' },
        errors: [],
    }, visibleInput), {
        field: 'peerBaseUrl', status: 'accepted', value: 'https://peer.example', errors: [],
    })
    assert.equal(pending.size, 0)
})

test('a rejection result still shows the applied-field error in either frame-handling order', () => {
    for (const resyncFirst of [true, false]) {
        const submitted = 'http://x.example'
        let input = submitted
        const pending = new Map([
            ['rejected', { field: 'peerBaseUrl', input: submitted }],
        ])
        const payload = {
            request_id: 'rejected',
            status: 'rejected',
            settings: { peerBaseUrl: 'https://old.example' },
            errors: [
                { field: 'peerBaseUrl', message: 'The Peer and External addresses must be the same origin or use different authorities.' },
                { field: 'publicBaseUrl', message: 'Hidden symmetric copy.' },
            ],
        }
        if (resyncFirst) {
            input = refreshOriginInput(input, 'https://old.example', 'https://old.example')
        }
        const result = resolveOriginSettingResult(pending, payload, input)
        if (!resyncFirst) {
            input = refreshOriginInput(input, 'https://old.example', 'https://old.example')
        }
        const message = originSettingErrorMessage(result.errors, result.field, code => code)
        assert.equal(message, 'The Peer and External addresses must be the same origin or use different authorities.')
        assert.equal(input, submitted)
    }
})

test('a stale-version result resolves its write without erasing typed text', () => {
    const pending = new Map([
        ['stale', { field: 'peerBaseUrl', input: 'https://typed.example' }],
    ])
    assert.deepEqual(resolveOriginSettingResult(pending, {
        request_id: 'stale',
        status: 'rejected',
        settings: { peerBaseUrl: 'https://remote.example' },
        errors: [],
    }, 'https://typed.example'), {
        field: 'peerBaseUrl', status: 'rejected', value: null, errors: [],
    })
    assert.equal(pending.size, 0)
})

test('back-to-back verdicts resolve only their matching writes', () => {
    const pending = new Map([
        ['public-write', { field: 'publicBaseUrl', input: 'https://app.example' }],
        ['share-write', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    const share = resolveOriginSettingResult(pending, {
        request_id: 'share-write', status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' }, errors: [],
    }, 'https://share.example')
    const external = resolveOriginSettingResult(pending, {
        request_id: 'public-write', status: 'accepted',
        settings: { publicBaseUrl: 'https://app.example' }, errors: [],
    }, 'https://app.example')
    assert.equal(share.field, 'shareBaseUrl')
    assert.equal(external.field, 'publicBaseUrl')
    assert.equal(pending.size, 0)
})

test('typing supersedes the same-field write without discarding another field', () => {
    const pending = new Map([
        ['old-peer', { field: 'peerBaseUrl', input: 'https://old.example' }],
        ['share', { field: 'shareBaseUrl', input: 'https://share.example' }],
    ])
    discardOriginSettingWrites(pending, 'peerBaseUrl')
    assert.equal(pending.has('old-peer'), false)
    assert.equal(pending.has('share'), true)
    assert.equal(pending.size, 1)
    pending.set('new-peer', { field: 'peerBaseUrl', input: 'https://new.example' })

    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'old-peer', status: 'accepted',
        settings: { peerBaseUrl: 'https://old.example' }, errors: [],
    }, 'https://new.example'), null)
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'share', status: 'accepted',
        settings: { shareBaseUrl: 'https://share.example' }, errors: [],
    }, 'https://share.example').field, 'shareBaseUrl')
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'new-peer', status: 'accepted',
        settings: { peerBaseUrl: 'https://new.example' }, errors: [],
    }, 'https://new.example').field, 'peerBaseUrl')
    assert.equal(pending.size, 0)
})

test('a verdict cannot affect text entered after its Apply', () => {
    const pending = new Map([
        ['old-write', { field: 'peerBaseUrl', input: 'https://old.example' }],
    ])
    discardOriginSettingWrites(pending, 'peerBaseUrl')
    assert.equal(resolveOriginSettingResult(pending, {
        request_id: 'old-write', status: 'accepted',
        settings: { peerBaseUrl: 'https://old.example' }, errors: [],
    }, 'https://new.example'), null)
})

test('the Share field retains its active-location rule', () => {
    const result = validateOriginSetting({
        field: 'shareBaseUrl',
        input: 'https://APP.example',
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'app.example',
    })
    assert.deepEqual(result.errors, [{ field: 'shareBaseUrl', code: 'location_hostname' }])
    assert.deepEqual(result.patch, {})
})

test('plain HTTP warns only for Peer and still creates the raw patch', () => {
    const result = validateOriginSetting({
        field: 'peerBaseUrl',
        input: '  http://Peer.Example/  ',
        stored: { publicBaseUrl: '', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.equal(result.warning, 'http')
    assert.deepEqual(result.patch, { peerBaseUrl: 'http://Peer.Example/' })
})

test('the backend owns canonical equality', () => {
    const result = validateOriginSetting({
        field: 'publicBaseUrl',
        input: 'HTTPS://APP.EXAMPLE:443/',
        stored: { publicBaseUrl: 'https://app.example', shareBaseUrl: '', peerBaseUrl: '' },
        locationHostname: 'localhost',
    })
    assert.deepEqual(result.errors, [])
    assert.deepEqual(result.patch, { publicBaseUrl: 'HTTPS://APP.EXAMPLE:443/' })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && node --test src/utils/originSettingsForm.test.js`
Expected: FAIL — the module does not exist.

- [ ] **Step 3: Create the per-field form helper**

Create `frontend/src/utils/originSettingsForm.js`:

```js
// Pure per-field preparation for the three origin settings.
// Python owns validation, relationships, and canonical output.

import { checkPublicOriginInput, usablePublicOrigin } from './publicOrigin.js'

export const ORIGIN_SETTING_KEYS = ['publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl']

export function originSettingErrorMessage(errors, field, messageFor) {
    return errors
        .filter(error => error.field === field)
        .map(error => error.message || messageFor(error.code))
        .join(' ')
}

export function refreshOriginInput(currentInput, previousStored, nextStored) {
    return currentInput === (previousStored || '') ? (nextStored || '') : currentInput
}

export function discardOriginSettingWrites(pendingWrites, field) {
    for (const [requestId, write] of pendingWrites) {
        if (write.field === field) pendingWrites.delete(requestId)
    }
}

export function resolveOriginSettingResult(pendingWrites, payload, currentInput) {
    if (!payload || !['accepted', 'rejected'].includes(payload.status)) return null
    const write = pendingWrites.get(payload.request_id)
    if (!write) return null
    pendingWrites.delete(payload.request_id)
    if (currentInput !== write.input) return null
    const value = payload.settings?.[write.field]
    if (payload.status === 'accepted' && typeof value !== 'string') return null
    return {
        field: write.field,
        status: payload.status,
        value: payload.status === 'accepted' ? value : null,
        errors: payload.status === 'rejected' && Array.isArray(payload.errors) ? payload.errors : [],
    }
}

export function validateOriginSetting({ field, input, stored, locationHostname }) {
    if (!ORIGIN_SETTING_KEYS.includes(field)) {
        return { errors: [{ field, code: 'unknown_field' }], warning: null, patch: {} }
    }
    const checked = checkPublicOriginInput(input)
    if (checked.error) {
        return { errors: [{ field, code: checked.error }], warning: null, patch: {} }
    }
    if (field === 'shareBaseUrl' && checked.hostname && locationHostname
            && checked.hostname === locationHostname.toLowerCase()) {
        return { errors: [{ field, code: 'location_hostname' }], warning: null, patch: {} }
    }
    const warning = field === 'peerBaseUrl' && checked.scheme === 'http' ? 'http' : null
    const patch = checked.value === (stored[field] || '') ? {} : { [field]: checked.value }
    const errors = !Object.keys(patch).length && checked.value && !usablePublicOrigin(checked.value)
        ? [{ field, code: 'retained_stored_value' }]
        : []
    return { errors, warning, patch }
}
```

This helper sends the trimmed raw value. It does not run a JavaScript relationship check. The stored-value guard runs only for an exact non-empty no-op. It keeps a retained malformed value's visible error stable when Apply sends no patch. Backend verdicts return through the correlated result frame added below.

- [ ] **Step 4: Remove optimistic setters and add the one-field send action**

In `frontend/src/stores/settings.js`, replace:

```js
import { normalizePublicOrigin, usablePublicOrigin } from '../utils/publicOrigin'
```

with:

```js
import { usablePublicOrigin } from '../utils/publicOrigin'
import { ORIGIN_SETTING_KEYS } from '../utils/originSettingsForm'
```

Replace this exact current block:

```js
        /**
         * Set the External address used for remote access and deep links.
         * @param {string} url
         */
        setPublicBaseUrl(url) {
            if (SETTINGS_VALIDATORS.publicBaseUrl(url)) {
                const result = normalizePublicOrigin(url)
                if (!result.error) this.publicBaseUrl = result.value
            }
        },

        /**
         * Set the dedicated Share address (design §12). Empty disables sharing.
         * @param {string} url
         */
        setShareBaseUrl(url) {
            if (SETTINGS_VALIDATORS.shareBaseUrl(url)) {
                const result = normalizePublicOrigin(url)
                if (!result.error) this.shareBaseUrl = result.value
            }
        },

        /**
         * Set the peer messaging base URL (the address advertised to peer
         * instances). Unlike shareBaseUrl it MAY be the working origin —
         * /peer/ is a same-origin carve-out, not a dedicated host. Empty
         * disables peer messaging.
         * @param {string} url
         */
        setPeerBaseUrl(url) {
            if (SETTINGS_VALIDATORS.peerBaseUrl(url)) {
                const result = normalizePublicOrigin(url)
                if (!result.error) this.peerBaseUrl = result.value
            }
        },
```

with:

```js
        /**
         * Send one raw origin field to the backend.
         * The authoritative synced_settings_updated broadcast performs the
         * store mutation. This action never commits optimistically.
         * @param {string} field - One origin setting key
         * @param {string} value - The trimmed raw field value
         * @param {string} requestId - The Apply correlation ID
         * @returns {Promise<boolean>} whether the WebSocket accepted the send
         */
        async sendOriginSetting(field, value, requestId) {
            if (!ORIGIN_SETTING_KEYS.includes(field)) return false
            // Lazy import avoids the settings.js ↔ useWebSocket.js cycle.
            const { sendSyncedSettings } = await import('../composables/useWebSocket')
            return sendSyncedSettings({ [field]: value }, _settingsVersion, requestId)
        },
```

Do not change `applySyncedSettings`. Its generic synced-key loop remains the only store mutation path.

- [ ] **Step 5: Write the failing correlated-result backend tests**

Create `tests/test_synced_settings_ws.py`:

```python
"""WebSocket result frames for correlated synced-settings writes."""

from asgiref.sync import async_to_sync

from twicc.asgi import WSConsumer
from twicc.core.services import settings_mutation
from twicc.core.services.settings_mutation import SettingsDropError, SettingsUpdateResult


def _frames(monkeypatch, result, *, request_id="origin-write", value="HTTPS://PEER.EXAMPLE:443/"):
    async def fake_update(_patch, *, base_version):
        assert base_version == 4
        return result

    frames = []

    async def send_json(frame):
        frames.append(frame)

    monkeypatch.setattr(settings_mutation, "update_synced_settings", fake_update)
    consumer = WSConsumer()
    consumer.send_json = send_json
    payload = {
        "settings": {"peerBaseUrl": value},
        "baseVersion": 4,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    async_to_sync(consumer._handle_update_synced_settings)(payload)
    return frames


def test_correlated_acceptance_returns_the_authoritative_submitted_value(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {"peerBaseUrl": "https://peer.example"},
        {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result) == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_acceptance_without_correction_still_returns_one_result(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {}, {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result, value="https://peer.example") == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_rejection_sends_resync_then_the_matching_error_result(monkeypatch):
    error = SettingsDropError(
        "peerBaseUrl",
        "origin_conflict_ambiguous_authority",
        "The Peer and External addresses must be the same origin or use different authorities.",
    )
    clean = {"peerBaseUrl": "https://stored.example"}
    result = SettingsUpdateResult("rejected", 4, {}, clean, (error,))
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 4},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://stored.example"},
            "version": 4,
            "errors": [error._asdict()],
        },
    ]


def test_correlated_stale_rejection_has_an_empty_error_list(monkeypatch):
    clean = {"peerBaseUrl": "https://remote.example"}
    result = SettingsUpdateResult("rejected", 8, {}, clean)
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 8},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://remote.example"},
            "version": 8,
            "errors": [],
        },
    ]


def test_idless_rejection_keeps_the_legacy_error_frame(monkeypatch):
    error = SettingsDropError("peerBaseUrl", "invalid_origin_host", "Invalid address.")
    result = SettingsUpdateResult("rejected", 4, {}, {"peerBaseUrl": ""}, (error,))
    frames = _frames(monkeypatch, result, request_id=None)
    assert [frame["type"] for frame in frames] == ["synced_settings_updated", "error"]
    assert frames[1]["code"] == "invalid_synced_settings"
```

- [ ] **Step 6: Run the backend test to verify it fails**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_synced_settings_ws.py -q`
Expected: FAIL — the handler does not emit `synced_settings_result`.

- [ ] **Step 7: Echo the correlation ID in one direct result frame**

In `src/twicc/asgi.py`, replace this exact docstring tail:

```python
        This wrapper only adds the WS-specific reject path: a stale
        ``baseVersion`` rejects the write, so this single client is resynced
        with the authoritative clean settings. An accepted write needs nothing
        here — the service already broadcast to all clients (including this one).
```

with:

```python
        This wrapper adds the WS-specific result path. A rejected write first
        resyncs this client. A request with a correlation ID then receives one
        direct result after the accepted broadcast or rejected resync.
```

In `src/twicc/asgi.py`, replace this exact line:

```python
        base_version = content.get("baseVersion")  # None for old clients
```

with:

```python
        base_version = content.get("baseVersion")  # None for old clients
        request_id = content.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = None
```

Replace this exact current comment:

```python
        # Delegate to the shared service (merge + orchestrator transitions +
        # all-clients broadcast). Only a rejection (stale ``baseVersion``) is
        # handled here: a targeted resync of this client.
```

with:

```python
        # The service broadcasts every accepted write. This wrapper sends a
        # rejected resync and one direct result for a correlated request.
```

Replace this exact current block:

```python
        result = await update_synced_settings(synced_settings, base_version=base_version)
        if result.status == "rejected":
            # Stale write rejected — resync only this client, then stop.
            await self.send_json({
                "type": "synced_settings_updated",
                "settings": result.clean,
                "version": result.version,
            })
            if result.errors:
                await self.send_json({
                    "type": "error",
                    "code": "invalid_synced_settings",
                    "message": result.errors[0].message,
                    "errors": [error._asdict() for error in result.errors],
                })
            return
```

with:

```python
        result = await update_synced_settings(synced_settings, base_version=base_version)
        if result.status == "rejected":
            # Resync this client before its direct verdict. The request ID makes
            # the result independent of this frame order.
            await self.send_json({
                "type": "synced_settings_updated",
                "settings": result.clean,
                "version": result.version,
            })
        if request_id:
            await self.send_json({
                "type": "synced_settings_result",
                "request_id": request_id,
                "status": result.status,
                "settings": {key: result.clean.get(key) for key in synced_settings},
                "version": result.version,
                "errors": [error._asdict() for error in result.errors],
            })
            return
        if result.errors:
            # Keep the existing fallback for old clients without correlation.
            await self.send_json({
                "type": "error",
                "code": "invalid_synced_settings",
                "message": result.errors[0].message,
                "errors": [error._asdict() for error in result.errors],
            })
```

An accepted service call already broadcasts before it returns. A rejected call sends its full resync before this result. Neither order affects attribution.

- [ ] **Step 8: Carry the ID through the frontend transport and expose results**

In `frontend/src/composables/useWebSocket.js`, replace this exact current block:

```js
/**
 * Send synced settings to the backend for persistence in settings.json.
 * The backend will broadcast the updated settings to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @param {number} baseVersion - The current settings version (for optimistic concurrency)
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings, baseVersion) {
    return sendWsMessage({ type: 'update_synced_settings', settings, baseVersion })
}
```

with:

```js
/**
 * Send synced settings to the backend for persistence in settings.json.
 * The backend broadcasts an accepted update to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @param {number} baseVersion - The current settings version
 * @param {string} [requestId] - Correlation ID for one direct result
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings, baseVersion, requestId) {
    return sendWsMessage({
        type: 'update_synced_settings', settings, baseVersion, request_id: requestId,
    })
}
```

Replace this exact current block:

```js
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings, msg.version)
                })
                break
```

with:

```js
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings, msg.version)
                })
                break
            case 'synced_settings_result':
                window.dispatchEvent(new CustomEvent('twicc:origin-settings-result', {
                    detail: msg,
                }))
                break
```

Keep the existing `invalid_synced_settings` toast. It remains the fallback for an old or ID-less caller. Correlated origin writes receive the direct result instead.

- [ ] **Step 9: Rework each popover Apply handler**

In `frontend/src/components/app/SettingsPopover.vue`, replace this current import:

```js
import { normalizePublicOrigin, usablePublicOrigin } from '../../utils/publicOrigin'
```

with:

```js
import { checkPublicOriginInput, usablePublicOrigin } from '../../utils/publicOrigin'
import { generateUUID } from '../../utils/crypto'
import {
    discardOriginSettingWrites,
    originSettingErrorMessage,
    refreshOriginInput,
    resolveOriginSettingResult,
    validateOriginSetting,
} from '../../utils/originSettingsForm'
```

Replace this exact current block:

```js
function normalizedInputValue(value) {
    return normalizePublicOrigin(value).value ?? value.trim()
}

function storedPublicOriginError(value) {
    const result = normalizePublicOrigin(value)
    return result.error ? publicOriginErrorMessage(result.error) : ''
}
```

with:

```js
function normalizedInputValue(value) {
    return checkPublicOriginInput(value).value ?? value.trim()
}

function storedPublicOriginError(value) {
    return usablePublicOrigin(value) || !value ? '' : PUBLIC_ORIGIN_ERROR
}
```

The stored error uses the fail-closed consumer guard. It does not ask the permissive input check to validate hand-edited storage.

Replace this exact block:

```js
function publicOriginErrorMessage(error) {
    if (error === 'scheme') return 'The address must use HTTP or HTTPS.'
    if (error === 'credentials') return 'The address must not contain a username or password.'
    return PUBLIC_ORIGIN_ERROR
}
```

with:

```js
function publicOriginErrorMessage(error) {
    const code = error?.replace(/^invalid_origin_/, '')
    if (code === 'scheme') return 'The address must use HTTP or HTTPS.'
    if (code === 'credentials') return 'The address must not contain a username or password.'
    if (code === 'location_hostname') return 'The share host must be a different hostname from this app.'
    if (code === 'origin_conflict_share_external_hostname') return 'The Share host must use a different hostname from the External address.'
    if (code === 'origin_conflict_share_peer_hostname') return 'The Share host must use a different hostname from the Peer address.'
    if (code === 'origin_conflict_ambiguous_authority') return 'The Peer and External addresses must be the same origin or use different authorities.'
    return PUBLIC_ORIGIN_ERROR
}
```

The three conflict sentences are the approved exact strings. Each names the other participating address.

Replace this exact current block:

```js
function onPublicBaseUrlInputChange(event) {
    publicBaseUrlInput.value = event.target.value
    publicBaseUrlError.value = ''
}

function onPublicBaseUrlApply() {
    publicBaseUrlError.value = ''
    const result = normalizePublicOrigin(publicBaseUrlInput.value)
    if (result.error) {
        publicBaseUrlError.value = publicOriginErrorMessage(result.error)
        return
    }
    store.setPublicBaseUrl(result.value)
    publicBaseUrlInput.value = store.getPublicBaseUrl || ''
}

function onShareBaseUrlInputChange(event) {
    shareBaseUrlInput.value = event.target.value
    shareBaseUrlError.value = ''
}

function onShareBaseUrlApply() {
    shareBaseUrlError.value = ''
    const result = normalizePublicOrigin(shareBaseUrlInput.value)
    if (result.error) {
        shareBaseUrlError.value = publicOriginErrorMessage(result.error)
        return
    }
    if (result.hostname) {
        if (result.hostname.toLowerCase() === window.location.hostname.toLowerCase()) {
            shareBaseUrlError.value = 'The share host must be a different hostname from this app.'
            return
        }
    }
    store.setShareBaseUrl(result.value)
    shareBaseUrlInput.value = store.getShareBaseUrl || ''
}

function onPeerBaseUrlInputChange(event) {
    peerBaseUrlInput.value = event.target.value
    peerBaseUrlError.value = ''
    peerBaseUrlWarning.value = ''
}

function onPeerBaseUrlApply() {
    peerBaseUrlError.value = ''
    peerBaseUrlWarning.value = ''
    const result = normalizePublicOrigin(peerBaseUrlInput.value)
    if (result.error) {
        peerBaseUrlError.value = publicOriginErrorMessage(result.error)
        return
    }
    if (result.scheme === 'http') {
        // Non-fatal (design §4.3): warn, still apply.
        peerBaseUrlWarning.value = 'Plain HTTP — tokens travel unencrypted. HTTPS is strongly recommended.'
    }
    store.setPeerBaseUrl(result.value)
    peerBaseUrlInput.value = store.getPeerBaseUrl || ''
}
```

with:

```js
const pendingOriginWrites = new Map()

function onPublicBaseUrlInputChange(event) {
    discardOriginSettingWrites(pendingOriginWrites, 'publicBaseUrl')
    publicBaseUrlInput.value = event.target.value
    publicBaseUrlError.value = ''
}

function onShareBaseUrlInputChange(event) {
    discardOriginSettingWrites(pendingOriginWrites, 'shareBaseUrl')
    shareBaseUrlInput.value = event.target.value
    shareBaseUrlError.value = ''
}

function onPeerBaseUrlInputChange(event) {
    discardOriginSettingWrites(pendingOriginWrites, 'peerBaseUrl')
    peerBaseUrlInput.value = event.target.value
    peerBaseUrlError.value = ''
    peerBaseUrlWarning.value = ''
}

const originErrorRefs = {
    publicBaseUrl: publicBaseUrlError,
    shareBaseUrl: shareBaseUrlError,
    peerBaseUrl: peerBaseUrlError,
}

const originInputRefs = {
    publicBaseUrl: publicBaseUrlInput,
    shareBaseUrl: shareBaseUrlInput,
    peerBaseUrl: peerBaseUrlInput,
}

function setOriginError(field, errors) {
    originErrorRefs[field].value = originSettingErrorMessage(errors, field, publicOriginErrorMessage)
}

async function applyOriginSetting(field, inputRef) {
    originErrorRefs[field].value = ''
    if (field === 'peerBaseUrl') peerBaseUrlWarning.value = ''
    const result = validateOriginSetting({
        field,
        input: inputRef.value,
        stored: {
            publicBaseUrl: store.getPublicBaseUrl || '',
            shareBaseUrl: store.getShareBaseUrl || '',
            peerBaseUrl: store.getPeerBaseUrl || '',
        },
        locationHostname: window.location.hostname,
    })
    setOriginError(field, result.errors)
    if (result.errors.length || !Object.keys(result.patch).length) return
    if (result.warning === 'http') {
        peerBaseUrlWarning.value = 'Plain HTTP — tokens travel unencrypted. HTTPS is strongly recommended.'
    }
    const value = result.patch[field]
    const requestId = generateUUID()
    pendingOriginWrites.set(requestId, { field, input: inputRef.value })
    if (!await store.sendOriginSetting(field, value, requestId)) {
        const pending = pendingOriginWrites.get(requestId)
        pendingOriginWrites.delete(requestId)
        if (pending && inputRef.value === pending.input) {
            originErrorRefs[field].value = 'Not connected to the server — try again.'
        }
    }
}

function onPublicBaseUrlApply() {
    applyOriginSetting('publicBaseUrl', publicBaseUrlInput)
}

function onShareBaseUrlApply() {
    applyOriginSetting('shareBaseUrl', shareBaseUrlInput)
}

function onPeerBaseUrlApply() {
    applyOriginSetting('peerBaseUrl', peerBaseUrlInput)
}
```

After that block, add:

```js
function onOriginSettingsResult(event) {
    const payload = event.detail
    const pending = pendingOriginWrites.get(payload?.request_id)
    if (!pending) return
    const result = resolveOriginSettingResult(
        pendingOriginWrites, payload, originInputRefs[pending.field].value,
    )
    if (!result) return
    if (result.status === 'accepted') {
        originErrorRefs[result.field].value = ''
        originInputRefs[result.field].value = result.value
        return
    }
    setOriginError(result.field, result.errors)
}

onMounted(() => {
    window.addEventListener('twicc:origin-settings-result', onOriginSettingsResult)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:origin-settings-result', onOriginSettingsResult)
    pendingOriginWrites.clear()
})

// Broadcasts update the store. They do not resolve correlated writes.
function refreshOriginField(inputRef, value, oldValue) {
    inputRef.value = refreshOriginInput(inputRef.value, oldValue, value)
}

watch(() => store.getPublicBaseUrl, (value, oldValue) => {
    refreshOriginField(publicBaseUrlInput, value, oldValue)
})
watch(() => store.getShareBaseUrl, (value, oldValue) => {
    refreshOriginField(shareBaseUrlInput, value, oldValue)
})
watch(() => store.getPeerBaseUrl, (value, oldValue) => {
    refreshOriginField(peerBaseUrlInput, value, oldValue)
})
watch(() => dataStore.wsConnected, connected => {
    if (!connected) pendingOriginWrites.clear()
})
```

A backend rejection keeps the visible text. The correlated result uses only its applied field. It discards every error for another section. An accepted result adopts the canonical value when the current text still equals the visible Apply-time snapshot. The separately named `value` is the trimmed raw field sent to the backend. An input event discards every older result for that field. A disconnect clears IDs whose direct results belonged to the closed socket. The existing `dataStore`, not the Settings store, owns `wsConnected`.

- [ ] **Step 10: Remove the temporary compatibility export**

In `frontend/src/utils/publicOrigin.js`, remove this exact Task 3 block:

```js
// Temporary compatibility for the callers replaced in Task 11. Despite the
// historical name, this is the subset check above. It does not normalize.
export const normalizePublicOrigin = checkPublicOriginInput
```

- [ ] **Step 11: Replace the frontend source-contract tests and run the survivor sweep**

Replace the ENTIRE content of `frontend/src/stores/publicOriginSettings.test.js` with:

```js
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')
const popoverSource = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
const websocketSource = readFileSync(new URL('../composables/useWebSocket.js', import.meta.url), 'utf8')
const browserSource = readFileSync(new URL('../components/browser/BrowserPane.vue', import.meta.url), 'utf8')
const backendSource = readFileSync(new URL('../../../src/twicc/asgi.py', import.meta.url), 'utf8')

const FIELDS = [
    ['Public', 'publicBaseUrl'],
    ['Share', 'shareBaseUrl'],
    ['Peer', 'peerBaseUrl'],
]

test('External, Share, and Peer origins expose a fail-closed getter', () => {
    for (const [name, key] of FIELDS) {
        assert.match(settingsSource, new RegExp(
            'getUsable' + name + 'BaseUrl:[^\\n]+usablePublicOrigin\\(state\\.' + key + '\\)',
        ))
    }
})

test('the store has one non-optimistic per-field send action', () => {
    for (const [name] of FIELDS) {
        assert.doesNotMatch(settingsSource, new RegExp('set' + name + 'BaseUrl\\('))
    }
    assert.match(settingsSource, /async sendOriginSetting\(field, value, requestId\)/)
    assert.match(settingsSource, /sendSyncedSettings\(\{ \[field\]: value \}, _settingsVersion, requestId\)/)
    assert.doesNotMatch(settingsSource, /this\.\w+BaseUrl = value/)
})

test('each Apply sends its trimmed field and snapshots its visible text', () => {
    assert.match(popoverSource, /applyOriginSetting\('publicBaseUrl', publicBaseUrlInput\)/)
    assert.match(popoverSource, /applyOriginSetting\('shareBaseUrl', shareBaseUrlInput\)/)
    assert.match(popoverSource, /applyOriginSetting\('peerBaseUrl', peerBaseUrlInput\)/)
    assert.match(popoverSource, /const value = result\.patch\[field\]/)
    assert.match(popoverSource, /pendingOriginWrites\.set\(requestId, \{ field, input: inputRef\.value \}\)/)
    assert.match(popoverSource, /store\.sendOriginSetting\(field, value, requestId\)/)
})

test('Apply renders field errors before it returns on an empty patch', () => {
    assert.match(
        popoverSource,
        /setOriginError\(field, result\.errors\)\s+if \(result\.errors\.length \|\| !Object\.keys\(result\.patch\)\.length\) return/,
    )
})

test('the Settings result protocol carries one correlation ID end to end', () => {
    assert.match(popoverSource, /import \{ generateUUID \} from '\.\.\/\.\.\/utils\/crypto'/)
    assert.match(popoverSource, /const requestId = generateUUID\(\)/)
    assert.doesNotMatch(popoverSource, /crypto\.randomUUID/)
    assert.match(websocketSource, /request_id: requestId/)
    assert.match(backendSource, /request_id = content\.get\("request_id"\)/)
    assert.match(backendSource, /"type": "synced_settings_result"/)
    assert.match(websocketSource, /twicc:origin-settings-result/)
    assert.match(popoverSource, /pendingOriginWrites\.get\(payload\?\.request_id\)/)
})

test('correlated results adopt accepted values and show rejected field errors', () => {
    assert.match(popoverSource, /resolveOriginSettingResult\(/)
    assert.match(popoverSource, /if \(result\.status === 'accepted'\)/)
    assert.match(popoverSource, /originInputRefs\[result\.field\]\.value = result\.value/)
    assert.match(popoverSource, /setOriginError\(result\.field, result\.errors\)/)
    assert.match(websocketSource, /applySyncedSettings\(msg\.settings, msg\.version\)/)
})

test('the popover subscribes and unsubscribes the correlated result handler', () => {
    assert.match(
        popoverSource,
        /window\.addEventListener\('twicc:origin-settings-result', onOriginSettingsResult\)/,
    )
    assert.match(
        popoverSource,
        /window\.removeEventListener\('twicc:origin-settings-result', onOriginSettingsResult\)/,
    )
})

test('broadcast resyncs preserve typed text without settling correlated writes', () => {
    assert.match(popoverSource, /watch\(\(\) => store\.getPublicBaseUrl/)
    assert.match(popoverSource, /watch\(\(\) => store\.getShareBaseUrl/)
    assert.match(popoverSource, /watch\(\(\) => store\.getPeerBaseUrl/)
    assert.match(popoverSource, /refreshOriginInput\(inputRef\.value, oldValue, value\)/)
    assert.doesNotMatch(popoverSource, /function refreshOriginField[^}]+pendingOriginWrites/s)
})

test('typing invalidates older results for only that field', () => {
    for (const field of ['publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl']) {
        assert.match(popoverSource, new RegExp(
            "discardOriginSettingWrites\\(pendingOriginWrites, '" + field + "'\\)",
        ))
    }
})

test('disconnect discards correlation IDs whose results cannot arrive', () => {
    assert.match(
        popoverSource,
        /watch\(\(\) => dataStore\.wsConnected,[\s\S]*?if \(!connected\) pendingOriginWrites\.clear\(\)/,
    )
})

test('the former External URL label is now External address', () => {
    assert.match(popoverSource, />External address <wa-icon/)
    assert.doesNotMatch(popoverSource, />External URL <wa-icon/)
})

test('the Browser companion falls back when the External address is invalid', () => {
    assert.match(
        browserSource,
        /usablePublicOrigin\(settingsStore\.getPublicBaseUrl\) \|\| window\.location\.origin/,
    )
})
```

Run: `rg -n "normalizePublicOrigin|validateOriginSettings|classifyPeerExternal" frontend/src`
Expected: NO output. The full-file test replacement now runs before this sweep. If a caller remains, update it only in a file declared by this task. Stop and report any caller outside this write set.

- [ ] **Step 12: Run the backend and frontend suites**

Run: `TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_synced_settings_ws.py -q && cd frontend && node --test src/utils/originSettingsForm.test.js src/stores/publicOriginSettings.test.js && npm test`
Expected: PASS. All seven correlation branches are load-bearing. The tests fail on an uncorrelated or duplicate verdict, a missing result-event subscription, a missing accepted no-change result, an outer-trimmed Apply that stores its transmitted value instead of its visible text snapshot, missing canonical UI adoption, a missing visible applied-field error in either effective frame order, direct `crypto.randomUUID()` use, frontend relationship enforcement, an optimistic setter, a multi-field send, same-field removal, cross-field preservation, lost typed input, a late-result overwrite, a disconnect leak, or a consumer leak.

- [ ] **Step 13: Commit**

Commit the changes produced by this task.
Subject: `feat(settings): apply origin settings one field at a time`

---

### Task 12: Architecture docs follow the gate move

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: module move `src/twicc/share/asgi_filter.py` → `src/twicc/origin_gate.py` (Task 9).
- Produces: nothing — documentation-only task (repository rule: `AGENTS.md` follows every `CLAUDE.md` change).

- [ ] **Step 1: Update the stale module reference in both docs**

In `CLAUDE.md`, in the `Share` model bullet, replace the fragment:

```
host gate `share/asgi_filter.py`, never the working origin
```

with:

```
host gate `origin_gate.py` (the common `PublicOriginGate`, design `docs/plans/2026-08-13-peer-origin-routing-design.md`), never the working origin
```

In `AGENTS.md`, the same fragment appears in its `Share` bullet; apply the replacement there too.

- [ ] **Step 2: Verify no stale doc reference remains**

Run: `grep -rn "asgi_filter" CLAUDE.md AGENTS.md docs/help/ 2>/dev/null; true`
Expected: NO output from `CLAUDE.md` / `AGENTS.md`. (Historical documents under `docs/plans/` and `docs/superpowers/specs/` keep their references — do not edit them.)

- [ ] **Step 3: Commit**

Commit the changes produced by this task.
Subject: `docs: point the architecture docs at the public-origin gate`

---

## Final verification (after the last task)

- [ ] Run the complete backend suite: `TWICC_DATA_DIR=$PWD uv run --active pytest -q` → all green.
- [ ] Run the complete frontend suite: `cd frontend && npm test` → all green.
- [ ] Remind the user at the end of implementation:
  - a dependency was declared (`idna~=3.11` in `pyproject.toml`) — `uv run`/devctl re-syncs it automatically, but a manual `uv sync` also works;
  - the dev backend must be restarted via `devctl.py` for the new gate to serve requests (user-reserved operation — do not restart it yourself).

## Spec-coverage map (self-review record)

| Spec section | Where |
|---|---|
| §1 scope | Goal, Architecture, and Global Constraints; Tasks 1 and 3 implement the referenced common syntax, Task 9 centralizes the three origin routes, and Task 11 preserves the three setting identities |
| §2 current behavior | Tasks 1, 3, 6, 9, and 11 replace the named current parser, write, gate, and UI behavior |
| §3 goals | Task 6 enforces changed-origin and relationship validation; Tasks 8–10 enforce routing; Tasks 9 and 11 cover live changes and per-field Apply |
| §5.1 strict hostname contract and frontend subset | Task 1 implements the Python parser; Tasks 3 and 4 implement the frontend subset and stored guard; Task 5 scopes the fixture; Task 7 parses `Host` |
| §5.2 routing authority | Task 1 produces configured authorities; Task 5 covers `authority_cases`; Task 7 produces request authorities |
| §5.3 Share hostname, port-blind | Task 8 classifier, Task 9 `test_share_hostname_matches_any_request_port` |
| §6 address relationships | Task 2 implements backend classifiers; Task 5 covers `cross_cases`; Task 6 enforces writes; Tasks 8–10 enforce routing |
| §7 changed-field settings validation, patch atomicity, per-field Apply | Tasks 2, 5, 6, and 11 |
| §8 request authority parsing | Tasks 7, 10 |
| §9 ASGI architecture (gate above BlackNoise, pure layers, lifespan pass-through) | Tasks 8, 9 |
| §10 routing table | Tasks 8 (classifier tests), 9 (gate tests) |
| §11 runtime invalid settings, routing availability, recognition, quarantine, precedence, cumulative composition | Tasks 7–10 |
| §12 live changes without restart | Task 8 memo, Task 9 `test_live_setting_change_routes_next_request`, Task 11 successful per-field Apply path |
| §13.1 origin/settings validation | Tasks 1–6 and 11 |
| §13.2 ASGI routing coverage | Tasks 8, 9, 10 |
| §13.3 Share regression | Task 9 |
| §13.4 Peer migration boundary | Task 6 (read-preservation test; no migration code anywhere) |
| §1.1 hard compatibility boundary | Global Constraints; no task adds Peer compatibility machinery |
| §4 out of scope | Task 6 (no Peer migration), Task 11 (plain HTTP warning retained; no address-change or relationship warning), Global Constraints (temporary WIP ignored) |
