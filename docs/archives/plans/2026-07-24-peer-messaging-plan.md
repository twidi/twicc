# Peer Messaging — Implementation Plan

**Status:** plan written 2026-07-24, then adversarially reviewed the same day (3 independent passes: backend accuracy, frontend accuracy, design conformance) and corrected — 1 blocker + 11 majors fixed in place; out-of-band identity verification integrated the same day (design §2.7/§4.2 → phases 1, 3, 4, 10) and itself adversarially reviewed (2 passes: protocol soundness, plan integration) — 1 blocker (brute-forceable code) + 1 deadlock (held-accept recovery) + hardening fixed in place; implementation not started
**Design:** `2026-07-24-peer-messaging-design.md` (read it first — this plan implements it decision-for-decision; all §10 questions are resolved there)
**Branch/worktree:** `peer-system` (`.worktrees/peer-system`)

This plan is written to be executed by an agent that follows instructions literally. File paths are exact; line numbers are anchors valid at the time of writing — always locate the named symbol, not the raw line. Work phase by phase, in order; each phase ends with its own tests green.

---

## 0. Ground rules for the implementing agent

- `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && ` prefixes EVERY Bash command. Python/Django ad-hoc commands additionally need `TWICC_DATA_DIR=$PWD` (see CLAUDE.md "Worktrees").
- Run tests with `uv run --active pytest` from the worktree (never plain `uv run` — the main repo's venv would win).
- Never run `migrate` by hand against a running instance, never restart dev servers, never touch CHANGELOG.md — all reserved to the user. Creating migration FILES is your job; applying them is not.
- All code, comments, UI strings, docs: English.
- Do not commit unless the user explicitly asks.
- The word **peer** is the only vocabulary for this feature. Never use host/remote/hub/federation (reserved by multi-host).
- Tokens (`token_ours`, `token_theirs`) must NEVER appear in serializers, WS payloads, REST responses, CLI output, or logs.

## 1. Deliverables map

New files:

| Path | Content |
|---|---|
| `src/twicc/core/migrations/0131_peer.py` | `Peer` + `PeerMessage` models |
| `src/twicc/core/services/peer_tokens.py` | id/token minting + inbound token resolution |
| `src/twicc/core/services/peer_mutation.py` | relationship mutations (create/accept/refuse/rename/delete) + peer broadcasts |
| `src/twicc/core/services/peer_messages.py` | message send/receive/deliver/refuse/status + envelope + message broadcasts |
| `src/twicc/peer/__init__.py` | empty package marker |
| `src/twicc/peer/outbound.py` | httpx client for the 4 outbound calls |
| `src/twicc/peer/inbound_views.py` | the 4 `/peer/` endpoints (Bearer-auth) |
| `src/twicc/peer/owner_views.py` | `/api/peers/…` + `/api/peer-messages/…` (UI management, cookie-gated) |
| `src/twicc/peer_purge_task.py` | periodic attachment-bytes purge |
| `src/twicc/cli/peers.py` | `twicc peers` (read) |
| `src/twicc/cli/peer_message.py` | `twicc peer-message` (read) |
| `src/twicc/cli/peer_send.py` | `twicc peer-send` (write, drop-request) |
| `src/twicc/agent/plugin/twicc/skills/twicc-peers/SKILL.md` | skill |
| `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md` | skill |
| `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md` | skill |
| `frontend/src/stores/peers.js` | Pinia store |
| `frontend/src/components/peer/PeersManagerDialog.vue` | Settings-launched management dialog |
| `frontend/src/components/peer/PeerInboxButton.vue` | sidebar badge button |
| `frontend/src/components/peer/PeerInboxDialog.vue` | inbox panel |
| `frontend/src/components/peer/PeerMessageReviewDialog.vue` | read & route dialog |
| `frontend/src/components/peer/PeerToastContent.vue` | actionable toast body |
| `tests/test_peer_handshake.py`, `tests/test_peer_messages.py`, `tests/test_peer_cli.py` | backend tests |

Modified files (detailed per phase): `core/models.py`, `core/serializers.py`, `synced_settings.py`, `cli/settings/_keys.py`, `auth/middleware.py`, `urls.py`, `asgi.py`, `drop_requests_watcher.py`, `cli/__init__.py`, `rpc/permissions.py`, `project_agent_defaults.py`, `cli/create_session/command.py`, `cli/run.py`, plugin `plugin.json`, `SKILLS-AND-CLI.md`, and on the frontend `useWebSocket.js`, `stores/settings.js`, `constants.js`, `SettingsPopover.vue`, `ProjectView.vue`, `HomeView.vue`, `App.vue`.

---

## Phase 1 — Data model, migration, serializers

### 1.1 Models (`src/twicc/core/models.py`)

Add near the Share section (after `ShareAccess`). For the choices classes use `models.TextChoices` subclasses like the existing `SessionType`/`PinMode` (top of `models.py`) — note `Share.kind` uses a different pattern (a `StrEnum` from `core/enums.py`); do not copy that one. Explicit `Meta` like `Share`.

```python
def generate_peer_id() -> str:
    """Non-secret admin handle for a Peer (UI, CLI, logs): ``peer_<hex8>``."""
    import secrets
    return "peer_" + secrets.token_hex(4)
```

**`Peer`:**

- `id = CharField(max_length=16, primary_key=True, default=generate_peer_id)`
- `name = CharField(max_length=255, blank=True, default="")` — local alias; empty until the acceptor names it (acceptor side) / set at creation (requester side)
- `remote_display_name = CharField(max_length=255, blank=True, default="")`
- `base_url = URLField(max_length=500)`
- `state = CharField(max_length=20, choices=…)` — choices `pending_sent`, `pending_received`, `active`, `broken` (class `PeerState(models.TextChoices)`)
- `token_ours = CharField(max_length=64, null=True, blank=True, unique=True)` — secret THEY present to call us. Requester side: minted at creation. Acceptor side: null until accept. `unique=True` with `null=True` (multiple NULLs allowed in SQLite)
- `token_theirs = CharField(max_length=64, null=True, blank=True, db_index=True)` — secret WE present to call them. Acceptor side: set at handshake-request receipt. Requester side: null until the accept callback. Indexed: `handshake_verify` looks rows up by it
- `verification_code = CharField(max_length=6, blank=True, default="")` — 6-digit code shown to the local user on `pending_received` rows (design §4.2 step 2); the SOLE barrier against an attacker-requester (see 4.3's rate limit + regen cap), NEVER on the agent surface
- `verification_attempts = PositiveSmallIntegerField(default=0)`
- `verification_regens = PositiveSmallIntegerField(default=0)` — code regenerations; the 3rd drops the pending request (hard guess ceiling)
- `verified_at = DateTimeField(null=True, blank=True)` — the requester echoed our code; unlocks Accept
- `code_confirmed_at = DateTimeField(null=True, blank=True)` — requester side: our own code submission was confirmed by the peer; required before the row may activate
- `remote_accepted_at = DateTimeField(null=True, blank=True)` — requester side: accept callback held because `code_confirmed_at` was not yet set
- `created_at = DateTimeField(auto_now_add=True)`, `accepted_at = DateTimeField(null=True, blank=True)`, `last_contact_at = DateTimeField(null=True, blank=True)`
- `Meta`: `ordering = ["name", "-created_at"]`

**`PeerMessage`:**

- auto pk (int) — the REST/admin handle; `message_id` is the wire handle
- `peer = ForeignKey(Peer, on_delete=CASCADE, related_name="messages")`
- `direction = CharField(max_length=3, choices=…)` — `in` / `out` (class `PeerMessageDirection`)
- `message_id = CharField(max_length=40)` — minted by the SENDER (`pm_` + `secrets.token_hex(8)`)
- `payload = JSONField(default=dict)` — `{text: str, images: list, documents: list}`, images/documents in the SDK block shape produced by `cli/_drop_request/attachments.py`
- `attachments_meta = JSONField(default=list)` — computed at row creation: `[{kind: "image"|"document", media_type, bytes, name?}]`; survives the purge
- `origin = JSONField(default=dict)` — wire provenance: `{session_title: str|null, sent_at: iso8601}`. **No session id on the wire** (design §3.2/§8)
- `origin_session = ForeignKey(Session, null=True, blank=True, on_delete=SET_NULL, related_name="peer_messages_sent")` — LOCAL only, outbound rows: which local session sent it (kept for the deferred threading, design §8)
- `status = CharField(max_length=12, choices=…)` — `pending` / `delivered` / `refused` / `failed` (class `PeerMessageStatus`)
- `error = CharField(max_length=255, blank=True, default="")` — detail for `failed`
- `recipient_note = TextField(blank=True, default="")` — inbound rows: the note added at delivery time (design §6.2)
- `delivered_to_session = ForeignKey(Session, null=True, blank=True, on_delete=SET_NULL, related_name="peer_messages_received")`
- `created_at = DateTimeField(auto_now_add=True)`, `resolved_at = DateTimeField(null=True, blank=True)`, `purged_at = DateTimeField(null=True, blank=True)`
- `Meta`: `ordering = ["-created_at"]`; `UniqueConstraint(fields=["peer", "direction", "message_id"], name="uniq_peermessage_peer_direction_msgid")`; index `idx_peermessage_status` on `["status", "direction"]`

### 1.2 Migration

Generate `0131_peer.py` with `cd <worktree> && TWICC_DATA_DIR=$PWD uv run --active python -m django makemigrations core --settings=twicc.settings`. Before running, sanity-check the resolved DB path (CLAUDE.md snippet). Do NOT apply it — remind the user at the end.

### 1.3 Serializers (`src/twicc/core/serializers.py`)

Query-free, like the rest of the module (load with `select_related` in callers).

- `serialize_peer(peer)` → `{id, name, remote_display_name, base_url, state, verification_code, verified_at, code_confirmed_at, remote_accepted_at, created_at, accepted_at, last_contact_at}` (ISO strings for datetimes, matching `serialize_share`'s style). **No tokens.** `verification_code` is deliberately included for the owner UI, but ONLY on `pending_received` rows — serialize `""` for every other state (shrinks exposure); it never reaches the agent surface (the CLI's `peers` output is a separate hand-built dict, 6.1). **SECURITY INVARIANT:** peer broadcasts ride the shared `"updates"` channel group, which share viewers also join; today `ShareConsumer.broadcast` (`share/consumer.py`) is a strict WHITELIST that silently drops all `peer_*` types — that is what keeps codes away from share links. Never add a peer type to that whitelist, and add the phase-4 regression test asserting `ShareConsumer` emits nothing for `peer_updated`/`peer_request_received`/`peers_updated`.
- `serialize_peer_message(message, *, include_payload=False)` → `{id, message_id, peer_id, direction, status, error, text_preview, attachments_meta, origin, recipient_note, delivered_to_session_id, created_at, resolved_at, purged}` where `text_preview` = first 300 chars of `payload["text"]` and `purged = message.purged_at is not None`. With `include_payload=True`, add `payload` (full). Broadcasts and lists always use the summary form (base64 blobs must never transit the channel layer); only the REST detail endpoint uses `include_payload=True`.

**Tests (phase 1):** none dedicated (covered by later phases).

---

## Phase 2 — `peerBaseUrl` setting

1. `src/twicc/synced_settings.py`, `_GENERIC_SYNCED_SETTINGS_DEFAULTS` (~line 107, next to `shareBaseUrl`): add
   ```python
   # Public base URL advertised to peer instances (peer messaging). Empty
   # disables the feature entirely: /peer/ endpoints answer 404 and no
   # outbound handshake can be sent. Unlike shareBaseUrl it MAY be the
   # working origin — /peer/ is a same-origin carve-out, not a dedicated host.
   "peerBaseUrl": "",
   ```
2. `src/twicc/cli/settings/_keys.py`, `GENERIC_KEY_DESCRIPTIONS`: add a one-line description for `peerBaseUrl` (mirroring the `shareBaseUrl` entry, ~line 31). The consistency test in `tests/test_settings_cli.py` enforces this.
3. Add a tiny live-read helper — in `src/twicc/core/services/peer_tokens.py` (created next phase) or directly where needed: `peer_base_url() -> str` returning `read_synced_settings().get("peerBaseUrl") or ""` (import `read_synced_settings` from `twicc.synced_settings`).

---

## Phase 3 — Ingress carve-out + token plumbing + inbound scaffolding

### 3.1 `src/twicc/core/services/peer_tokens.py`

Mirror `core/services/share_tokens.py`:

- `mint_token() -> str` — `secrets.token_urlsafe(32)`
- `mint_message_id() -> str` — `"pm_" + secrets.token_hex(8)`
- `mint_verification_code() -> str` — `f"{secrets.randbelow(1_000_000):06d}"`
- `resolve_peer(token: str) -> Peer | None` — `Peer.objects.filter(token_ours=token).select_related(None).first()`, then constant-time re-check `hmac.compare_digest(peer.token_ours, token)` (exact pattern of `share_tokens.resolve_share`, lines 36–50). Returns the peer regardless of state — callers apply state policy.
- `aresolve_peer(token)` — `sync_to_async` wrapper (like `aresolve_share`)
- `peer_base_url()` helper from phase 2.

### 3.2 Auth carve-out

- `src/twicc/auth/middleware.py`: add `"/peer/"` to `PUBLIC_PATHS` (lines 31–50). Effects (verified against `PasswordAuthMiddleware.__call__`): password-ON → passes without session auth; password-OFF → `_is_data_path` returns False so the local-only remote refusal is skipped. `/peer/` carries its own Bearer auth (this phase). Add a comment on the entry: `# peer messaging inbound API — Bearer-auth in views (design 2026-07-24)`.
- `src/twicc/urls.py`: add `peer/` to the SPA catch-all negative lookahead (line ~187): `^(?!api/|rpc/|static/|ws/|artifacts/|project-icons/|share/|_twicc/|peer/).*$`.
- No change to `share/asgi_filter.py`: `/peer/` is not in `_SHARE_ONLY_PREFIXES`, so it is already unreachable on the share host — leave as is.

### 3.3 Request body size — per-view, NO global change

Do NOT raise `DATA_UPLOAD_MAX_MEMORY_SIZE` (a global bump would let every endpoint — including the unauthenticated handshake — buffer tens of MB pre-auth). Instead, in `inbound_views.py`:

- `PEER_MESSAGE_MAX_REQUEST_BYTES = 48 * 1024 * 1024` (32 MB binary ≈ 43 MB base64 + JSON headroom).
- In `message_receive` ONLY: reject a missing or oversized `Content-Length` with 413 `{"error": "too_large"}` BEFORE reading; then read the body via `request.read(PEER_MESSAGE_MAX_REQUEST_BYTES + 1)` — NOT `request.body` — and 413 if the read exceeds the cap. (Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` check fires only in the `body` property; the ASGI handler has already buffered the stream, so `request.read()` deliberately bypasses the global 2.5 MB cap for this one endpoint. Put this explanation in a comment.) Parse the bytes with `orjson.loads`.
- The FOUR other `/peer/` views — `handshake_request`, `handshake_verify`, `handshake_accept`, `message_status`: reject `Content-Length` > 64 KB with 413, then use `request.body` normally (the global default additionally protects them).

### 3.4 `src/twicc/peer/inbound_views.py` — skeleton

Plain async Django views returning `JsonResponse`, `orjson.loads(request.body)` for parsing (house style, see `views.py`). Shared helpers at top of file:

- `_feature_disabled() -> bool`: `not peer_base_url()`. EVERY view starts with `if _feature_disabled(): return JsonResponse({"error": "not_found"}, status=404)` — uniform 404, checked BEFORE auth (design §4.3).
- `_bearer(request) -> str | None`: parse `Authorization: Bearer …` (copy the shape of `auth/middleware.py::_bearer`, lines 244–249).
- `async def _authenticated_peer(request) -> Peer | None`: `_bearer` → `aresolve_peer`.
- Rate limiting for the unauthenticated endpoint: module-level `_handshake_attempts: dict[str, list[float]]` with the pattern of `auth/views.py::_check_rate_limit` (~line 47; 5 attempts / 300 s window / 60 s lockout, monotonic clock) — but key it on **`request.META["REMOTE_ADDR"]` only**, NOT `_get_client_ip`: that helper trusts the leftmost `X-Forwarded-For`, which is client-controlled, and this endpoint is public and unauthenticated — a spoofed header must not bypass its only guard. Prune expired-window entries on each check so spoofed IPs can't grow the dict unboundedly. Note: the pending-rows cap (20, §4.3) means junk requests can crowd out legitimate ones — acceptable because the manager UI lets the user refuse/clear pending rows at any time.
- The SAME machinery (separate attempts dict) also guards `handshake_verify`: ~10 calls/min per `REMOTE_ADDR`, lockout on excess — the 6-digit code is the sole barrier against an attacker-requester and must not be online-guessable (see 4.3).
- Deployment caveat (put in a comment): behind a reverse proxy, `REMOTE_ADDR` is the proxy for every caller, so these caps become global for inbound handshake traffic. Acceptable for v1; revisit if a trusted-proxy configuration is ever added.

Routes (`src/twicc/urls.py`, before the catch-all — all WITH trailing slash, and the outbound client in 4.1 builds them the same way; wire format is ours on both sides):

```python
path("peer/handshake/request/", peer_inbound_views.handshake_request),
path("peer/handshake/verify/", peer_inbound_views.handshake_verify),
path("peer/handshake/accept/", peer_inbound_views.handshake_accept),
path("peer/messages/", peer_inbound_views.message_receive),
path("peer/messages/<str:message_id>/status/", peer_inbound_views.message_status),
```

Import in `urls.py` as `from .peer import inbound_views as peer_inbound_views` (matching the `share_*` alias style, lines 8–13).

Implement the four views in phases 4–5 (this phase may stub them at 404/405 to keep the app importable).

**Tests (phase 3):** in `tests/test_peer_handshake.py` — with `peerBaseUrl` empty, all four endpoints return 404; with it set, wrong/absent Bearer on the authed ones returns 403.

---

## Phase 4 — Handshake: outbound client, relationship service, inbound endpoints, owner REST

### 4.1 `src/twicc/peer/outbound.py`

Async httpx, modeled on `artifacts/proxy.py` (client per call, explicit timeout):

```python
OUTBOUND_TIMEOUT_SECONDS = 30.0

class PeerOutboundError(Exception):
    """Network-level failure reaching the peer (detail in str())."""

async def _post(base_url: str, path: str, json_body: dict, *, bearer: str | None) -> tuple[int, dict]:
    # url = base_url.rstrip("/") + path   (path like "/peer/messages/")
    # headers: {"Authorization": f"Bearer {bearer}"} when bearer is not None
    # async with httpx.AsyncClient(timeout=OUTBOUND_TIMEOUT_SECONDS) as client: post json=
    # on httpx.HTTPError -> raise PeerOutboundError(type(exc).__name__)
    # return (response.status_code, parsed json dict or {})
```

Public functions (thin wrappers over `_post`):

- `post_handshake_request(base_url, *, display_name, own_base_url, token)` → body `{display_name, base_url: own_base_url, token}`, no bearer
- `post_handshake_verify(base_url, *, bearer, code)` → body `{code}` to `/peer/handshake/verify/`
- `post_handshake_accept(base_url, *, bearer, token, display_name)` → body `{token, display_name}`
- `post_message(base_url, *, bearer, message_id, payload, origin)` → body `{message_id, payload, origin}`
- `post_status(base_url, *, bearer, message_id, status)` → body `{status}` to `/peer/messages/{message_id}/status/`

### 4.2 `src/twicc/core/services/peer_mutation.py`

Mirror `share_mutation.py`'s structure: `PeerError(field, code, message)` NamedTuple, `PeerMutationResult(success, peer_id, errors)`, all writes under `run_under_db_write_lock` (same import as `share_mutation.py` uses), broadcasts OUTSIDE the lock, `*_from_payload` never raise.

Broadcast helpers (exact pattern of `broadcast_share_updated` / `_removed`, `share_mutation.py` lines 184–206):

- `broadcast_peer_updated(peer)` → `data = {"type": "peer_updated", "peer": serialize_peer(peer)}`
- `broadcast_peer_removed(peer_id)` → `{"type": "peer_removed", "peer_id": …}`
- `broadcast_peer_request_received(peer)` → `{"type": "peer_request_received", "peer": …}`
- `broadcast_peer_accepted(peer)` → `{"type": "peer_accepted", "peer": …}`

Mutations:

- `async def create_peer_and_request(*, name, base_url) -> PeerMutationResult`
  - errors: `peer_host_unset` if `peer_base_url()` empty (mirror `share_host_unset`, `owner_views.py:48`); `invalid_url` unless `base_url` parses as absolute http(s); `duplicate` if a non-broken Peer with same normalized `base_url` exists.
  - mint `token_ours`; create row `state=pending_sent`; then `await post_handshake_request(...)` with `display_name=` the local instance's own name — use the hostname of `peer_base_url()` as display name v1 (no instance-name setting exists; keep it simple and note it in a comment).
  - On outbound failure (`PeerOutboundError` or status ≥ 400): DELETE the row and return `PeerMutationResult(False, None, [PeerError("base_url", "unreachable", str(detail))])` — the user retries the whole add.
  - On 2xx: keep row, `broadcast_peer_updated`.
- `async def accept_peer(peer, *, name) -> PeerMutationResult` — if `state == active`, return success (idempotent no-op, no outbound call — happens after a crossed handshake resolved from the other side). Otherwise requires `state == pending_received` (else `bad_state`) AND `verified_at` non-null (else error `not_verified` — the Accept gate of design §4.2 step 2; the UI disables the button, the service re-checks). Token: mint `token_ours` ONLY if `peer.token_ours` is null — after a crossed handshake (§4.3 dedup) the row already carries the token we minted for our own outbound request, and the other side knows that one; reuse it, never re-mint. Do NOT clear `verification_code` on accept — `handshake_verify` stays idempotent on `active` rows and the held-accept recovery depends on it (4.3). `await post_handshake_accept(peer.base_url, bearer=peer.token_theirs, token=<token_ours>, display_name=<own hostname>)`. On failure: do NOT persist a fresh mint, return `unreachable` (row stays `pending_received`, user retries). On 2xx: save `name`, `token_ours`, `state=active`, `accepted_at=now`; broadcast `peer_updated`.
- `async def submit_verification_code(peer, code) -> PeerMutationResult` — the requester-side action (the user types the code received out-of-band). Allowed states: `pending_sent`, or `pending_received` with non-null `token_ours` (crossed row acting as requester for its outbound leg). `await post_handshake_verify(peer.base_url, bearer=peer.token_ours, code=code)`:
  - 200 → set `code_confirmed_at=now`; if `remote_accepted_at` is set (held accept), also flip `state=active`, `accepted_at=now`; broadcast `peer_updated` (and `peer_accepted` when it just activated).
  - 403 `bad_code` → error `bad_code` ("Wrong code — check with your peer").
  - 403 `too_many_attempts` → error `code_regenerated` ("Too many attempts — the peer's code was regenerated, ask them for the new one").
  - 403 `unknown_token` → error `relationship_gone` ("The peer no longer has this pending request — ask them to check their side, or remove and re-add the peer").
  - any other status (400/404/5xx) → error `verify_failed` ("Verification could not be completed — check the relationship with your peer").
  - `PeerOutboundError` → error `unreachable`.
- `async def refuse_peer(peer)` — requires `pending_received`; silent local delete (design: silence works); `broadcast_peer_removed`.
- `async def rename_peer(peer, name)` / `async def update_peer_base_url(peer, base_url)` — trivial field updates + `peer_updated`.
- `async def delete_peer(peer)` — silent revocation, any state: `adelete()` + `broadcast_peer_removed`. The CASCADE deletes the peer's message history with it — a recorded decision (design §3.2): "history forever" holds for the lifetime of the relationship; revoking removes the relationship AND its messages. The manager's confirm dialog must say so (see 10.3).
- `def mark_peer_broken(peer)` (sync body): `state=broken`, save, return; caller broadcasts. Async callers (the send path, 5.1) MUST wrap it in `sync_to_async` under the write lock like every other mutation — a bare `.save()` from async raises `SynchronousOnlyOperation`.

### 4.3 Inbound handshake endpoints (`inbound_views.py`)

- `handshake_request` (POST, unauthenticated):
  1. feature gate; 2. rate limit by IP (429 with `Retry-After`); 3. parse `{display_name, base_url, token}` — all required non-empty strings, `base_url` absolute http(s), else 400 `{"error": "invalid_payload"}`.
  4. Dedup by normalized `base_url` against existing rows (every branch that creates or refreshes a `pending_received` row also mints a fresh `verification_code`, resets `verification_attempts=0` and `verified_at=None`):
     - `pending_received` with `verified_at IS NULL` → update its `remote_display_name` + `token_theirs` (re-request), re-mint the code, reset attempts/regens, broadcast `peer_updated`, return 200. **If `verified_at` IS set → return 200 WITHOUT mutating anything**: this endpoint is unauthenticated and dedups by `base_url` alone — a forged re-request must not be able to strip a completed verification or swap the bound token.
     - `pending_sent` → **crossed handshake** (both users added each other near-simultaneously): keep our minted `token_ours`, set `token_theirs` = body token and `remote_display_name`, flip `state=pending_received`, mint a code, broadcast `peer_request_received`, return 200. The user then verifies and accepts exactly like any incoming request — **no crossed exemption from the code** (a locally-typed URL can be stale or wrong; an attacker controlling it could fabricate the crossed case to dodge verification). `accept_peer` reuses the existing `token_ours` (see 4.2) and the `handshake_accept` endpoint tolerates this state (below). Without the merge rule the two-colleagues case deadlocks in two `pending_sent` rows.
     - `active` → 409 `{"error": "already_related"}`.
  5. Cap: ≥ 20 rows in `pending_received` → 429 `{"error": "too_many_pending"}`.
  6. Create `Peer(state=pending_received, name="", remote_display_name=…, base_url=…, token_theirs=token, token_ours=None, verification_code=mint_verification_code())`; `broadcast_peer_request_received`; return 201 `{}`.
- `handshake_verify` (POST, Bearer — but NOT via `_authenticated_peer`): the caller is the REQUESTER presenting the token it minted, which we stored as `token_theirs`. Dedicated lookup: `Peer.objects.filter(token_theirs=bearer, state__in=("pending_received", "active")).first()` + `hmac.compare_digest` re-check → 403 `{"error": "unknown_token"}` if no match. **`active` is included on purpose**: verify must be idempotent across the accept transition — the held-accept recovery (a requester whose earlier verify 200 was lost retries after the acceptor already went active) depends on it, which is also why `accept_peer` never clears `verification_code`. (The distinct `unknown_token`/`bad_code` responses are an accepted micro-oracle: only the holder of a valid 256-bit token sees the difference, and only about its own relationship.)
  1. feature gate; 2. rate limit per `REMOTE_ADDR` (~10/min with lockout, see 3.4) — the code is the sole barrier against an attacker-requester, this limit is not optional; 3. body `{code}` — a string of exactly 6 digits, else 400 `{"error": "invalid_payload"}`.
  4. Delegate the write to a dedicated `record_verification_attempt(peer_id, code)` in `peer_mutation.py` (its OWN function — do not reuse `register_incoming_request`), which re-fetches the Peer INSIDE `run_under_db_write_lock` and returns the outcome so the view broadcasts outside the lock:
     - `hmac.compare_digest(peer.verification_code, code)` matches → set `verified_at=now` (if null), `verification_attempts=0`; broadcast `peer_updated` (the owner's Accept unlocks live); return 200 `{}`. On an `active` row this is a pure 200 no-op.
     - mismatch → `verification_attempts += 1`; at 5 → re-mint `verification_code`, reset attempts, `verification_regens += 1`, broadcast `peer_updated` (the owner sees the new code); **if `verification_regens` reaches 3 (≤ 15 total guesses), DELETE the row instead** (silent-refusal semantics) + broadcast `peer_removed` — "5-then-regenerate" alone is an unbounded guessing loop, not a brute-force control; return 403 `{"error": "too_many_attempts"}`. Below 5: save and return 403 `{"error": "bad_code"}`.
  - All writes via `sync_to_async` under `run_under_db_write_lock` (delegate to a small function in `peer_mutation.py`, e.g. `register_incoming_request(...)`, keeping views thin like `share/owner_views.py`).
- `handshake_accept` (POST, Bearer):
  1. feature gate; 2. `_authenticated_peer` → 403 `{"error": "unknown_token"}` if None; 3. parse `{token, display_name}`; then by state:
     - `pending_sent` with `code_confirmed_at` set (the honest flow — the acceptor cannot accept before our code submission succeeded): set `token_theirs=token`, `remote_display_name=display_name`, `state=active`, `accepted_at=now`, `last_contact_at=now`; save; `broadcast_peer_accepted`; return 200 `{}`.
     - `pending_sent` WITHOUT `code_confirmed_at`: store `token_theirs=token`, `remote_display_name=display_name`, `remote_accepted_at=now` but KEEP `pending_sent` — do NOT activate (an accept from a stale/hijacked URL must not silently open the channel; design §4.2 step 4). Broadcast `peer_updated` (the UI warns: "accepted remotely, but your code verification hasn't completed — contact your peer"); return 200 `{}`. Activation happens later inside `submit_verification_code` (4.2).
     - crossed `pending_received` (non-null `token_ours`): 200 no-op — the data is already present; activation only ever comes from the LOCAL verify + accept path on this side.
     - already `active` and `hmac.compare_digest(peer.token_theirs or "", body_token)`: 200 (idempotent). Other states → 409.

  **Activation race note** (applies to `handshake_accept`, `record_verification_attempt`, `submit_verification_code`): on requester-side rows, `active ⇔ code_confirmed_at AND remote_accepted_at` is split across two writers. EVERY mutation must re-fetch the Peer INSIDE `run_under_db_write_lock` and evaluate activation from the fresh fields — whichever write lands second flips the row to `active` when both halves are present. Never decide from a Peer instance fetched before the lock (TOCTOU: two stale reads would leave both flags set and the row stuck pending forever).

### 4.4 Owner REST (`src/twicc/peer/owner_views.py` + `urls.py`)

Model on `share/owner_views.py` (async views, `_err_response` helper returning `{"errors": [e._asdict() …]}` status 400). Under `/api/` → automatically cookie-gated by `PasswordAuthMiddleware`. Routes (in `urls.py`, near the shares block, lines ~146–151):

```python
path("api/peers/", peer_owner_views.peers_list),                      # GET list, POST create+request
path("api/peers/<str:peer_id>/", peer_owner_views.peer_detail),      # GET, PATCH {name?, base_url?}, DELETE (silent revoke)
path("api/peers/<str:peer_id>/verify/", peer_owner_views.peer_verify),   # POST {code} — requester side submits the out-of-band code
path("api/peers/<str:peer_id>/accept/", peer_owner_views.peer_accept),   # POST {name}
path("api/peers/<str:peer_id>/refuse/", peer_owner_views.peer_refuse),   # POST
```

`peers_list` GET returns `{"peers": [serialize_peer(p) …]}` (all states — the UI shows pending ones). POST validates then calls `create_peer_and_request`.

### 4.5 WS connect snapshot

`src/twicc/asgi.py`, `WSConsumer.connect`, next to the `shares_updated` block (lines 572–581): push

```python
{"type": "peers_updated", "peers": [serialize_peer(p) for p in <all peers>]}
{"type": "peer_messages_updated", "messages": [serialize_peer_message(m) for m in <pending inbound + 50 most recent resolved, both directions>]}
```

both gated by `_should_send`, querying via `sync_to_async` like the neighbors.

**Tests (phase 4):** `tests/test_peer_handshake.py` — request endpoint (created row with a 6-digit code + broadcast; dedup path re-mints the code; 409 on active; cap; rate limit; body-size 413); **verification**: `handshake_verify` (403 unknown token; wrong code increments attempts; 5th mismatch regenerates the code + `too_many_attempts`; **3rd regeneration deletes the row** + `peer_removed`; rate limit 429; correct code sets `verified_at` and unlocks accept; **200 no-op on an `active` row** — exercise the idempotent path against a real active row, NOT a monkeypatch; constant-time compares), **dedup no-reset**: a re-request against a row with `verified_at` set mutates nothing, `accept_peer` rejected with `not_verified` before verification, `submit_verification_code` service with outbound monkeypatched (success sets `code_confirmed_at`; success with `remote_accepted_at` set activates the row; `bad_code`/`too_many_attempts`/`relationship_gone`/`verify_failed`/network mapped to their error codes); **held accept**: `handshake_accept` on a `pending_sent` row without `code_confirmed_at` stores `remote_accepted_at` and does NOT activate, then the full recovery: the acceptor row being `active`, a retried verify succeeds and `submit_verification_code` flips the held row to `active`; **ShareConsumer regression**: `ShareConsumer.broadcast` emits nothing for `peer_updated`/`peer_request_received`/`peers_updated` (the whitelist keeps verification codes away from share viewers); **crossed handshake** (incoming request over a `pending_sent` row flips it to `pending_received`, keeps `token_ours`, mints a code; `accept_peer` requires verification then reuses the token; `handshake_accept` is a no-op on that state); accept endpoint (honest flip to active, idempotency, 403 unknown token, 409 wrong state); `create_peer_and_request` and `accept_peer` with `twicc.peer.outbound` functions monkeypatched (success and `PeerOutboundError` paths — row deleted / kept-pending respectively); owner REST happy paths incl. `peer_verify`. Use `pytest-django` DB fixtures like neighboring test files.

---

## Phase 5 — Messaging core: send service, inbound message endpoints

### 5.1 `src/twicc/core/services/peer_messages.py`

Constants:

```python
PEER_ATTACHMENT_MAX_BYTES_PER_FILE = 5 * 1024 * 1024
PEER_ATTACHMENT_MAX_TOTAL_BYTES = 32 * 1024 * 1024
PEER_ATTACHMENT_MAX_FILES = 100
# The three size/count caps match both providers' ATTACHMENT_SUPPORT
# (providers/*/helpers.py); mime/document acceptance differs per provider
# (codex has documents: False) — the peer wire payload reuses the common
# SDK block shape with claude_code's wider acceptance.
```

Broadcast helpers (same pattern as phase 4):

- `broadcast_peer_message_received(message)` → `{"type": "peer_message_received", "message": serialize_peer_message(message)}` (summary — never the payload)
- `broadcast_peer_message_updated(message)` → `{"type": "peer_message_updated", "message": …}`

Helpers:

- `_attachments_meta(payload) -> list` — walk `payload["images"]`/`payload["documents"]`, emit `{kind, media_type, bytes(len of decoded base64), name?}` per block.
- `_validate_inbound_payload(payload) -> list[PeerError]` — `text` non-empty str; `images`/`documents` lists (default `[]`) of dicts in the SDK block shape; enforce the three caps on decoded sizes; unknown top-level keys rejected.

Service functions:

- `async def send_peer_message_from_payload(payload: dict) -> PeerSendResult` — the drop-request handler (phase 6). Payload: `{peer: <peer_id or exact local name>, text, images, documents, origin_session_id?}`.
  `PeerSendResult = NamedTuple(success, message_id, peer_id, errors, status_extra)` — `status_extra` is a dict the drop watcher merges into the final status JSON (precedent: `SettingsDropResult.status_extra`, `core/services/settings_mutation.py` ~lines 54/309/327). Here `status_extra = {"peer_status": "pending" | "failed"}`. **Never put a `status` key in `status_extra`** — it would overwrite the transport-level status (`"sent"`) and break the CLI exit mapping (see 6.4).
  1. Resolve peer by `id` then by exact `name`; not found → error `peer("not_found")`. State `broken` → `peer("peer_broken", "This peer no longer accepts messages (revoked or unreachable). Ask your user to check the relationship in Settings › Peers.")`. State pending → `peer("not_active")`.
  2. Mint `message_id`; resolve origin: if `origin_session_id` given, load the `Session` for FK + its title for the wire; build `origin = {"session_title": title_or_None, "sent_at": datetime.now(timezone.utc).isoformat()}` — timezone-aware UTC ISO-8601, the receiver renders it with `wa-relative-time` and in the envelope.
  3. Create `PeerMessage(direction=out, status=pending, payload=…, origin=…, origin_session=…, attachments_meta=…)` under the write lock.
  4. `await post_message(peer.base_url, bearer=peer.token_theirs, …)`:
     - 202 → keep `pending`; update `peer.last_contact_at`; result success with `status_extra={"peer_status": "pending"}`.
     - 403 → row `status=failed, error="peer_rejected_token", resolved_at=now`; `mark_peer_broken(peer)`; broadcast `peer_updated`; result error `peer("peer_broken", …)` (success=False).
     - other 4xx/5xx → row `failed`, `error=f"http_{code}"`; success=False, error `peer("send_failed", …)`.
     - `PeerOutboundError` → row `failed`, `error=detail`; success=False, `peer("unreachable", …)`.
  5. Broadcast `peer_message_updated` in every branch.
- `async def receive_peer_message(peer, body) -> tuple[int, dict]` — called by the inbound view:
  1. peer state must be `active` → else `(403, {"error": "unknown_token"})` (same response as bad token — no state oracle).
  2. `{message_id, payload, origin}` — validate `message_id` (str ≤ 40), `_validate_inbound_payload`, `origin` dict with optional `session_title` str / `sent_at` str; else `(400, {"error": "invalid_payload"})`.
  3. Idempotency: existing `(peer, "in", message_id)` → `(202, {"status": existing.status})` untouched.
  4. Create row (`direction=in, status=pending`), update `peer.last_contact_at`; broadcast `peer_message_received`; `(202, {"status": "pending"})`.
- `async def apply_status_callback(peer, message_id, status) -> tuple[int, dict]` — sender side:
  1. `status` must be `delivered` or `refused` else 400.
  2. Find `(peer, "out", message_id)`; missing → `(404, {"error": "unknown_message"})`. Already resolved → `(200, {})` idempotent.
  3. Set status + `resolved_at=now`; broadcast `peer_message_updated`; `(200, {})`.

### 5.2 Inbound message endpoints (`inbound_views.py`)

- `message_receive` (POST): feature gate → `_authenticated_peer` (403) → delegate to `receive_peer_message`, return its `(status, body)`.
- `message_status` (POST, `<message_id>` from URL): feature gate → auth → delegate to `apply_status_callback`.

**Tests (phase 5):** `tests/test_peer_messages.py` — inbound receive: 403 unknown token / non-active state, 400 invalid payloads (empty text, oversized attachment metadata using small fake blocks with inflated base64), 202 + row + broadcast, idempotent replay returns stored status; status callback: transitions, idempotency, 404; `send_peer_message_from_payload` with `outbound.post_message` monkeypatched: 202/403/500/network branches with exact row+peer state assertions.

---

## Phase 6 — Agent surface: CLI × 3, watcher kind, RPC/MCP

### 6.1 `twicc peers` (read) — `src/twicc/cli/peers.py`

Model on `src/twicc/cli/sessions.py` (lazy `django.setup()`, ORM query, `emit_json`). `def main() -> None` (no options in v1): list peers with `state in ("active", "broken")`, output `{"peers": [{id, name, state, last_contact_at} …]}` — the design's fields (name/state/last contact) plus the `id` needed by `peer-send`. No `base_url` (a network address the agent has no use for), no tokens ever. Including `broken` alongside `active` is a recorded decision: it lets the agent explain a failing send instead of "peer unknown".

Register in `src/twicc/cli/__init__.py` next to the other imported callbacks (lines ~1373–1465): `app.command(name="peers")(peers_main)` — with a docstring-derived help line matching neighbors' style.

### 6.2 `twicc peer-message <message_id>` (read) — `src/twicc/cli/peer_message.py`

`def main(message_id: str) -> None`: load `PeerMessage.objects.filter(direction="out", message_id=message_id).select_related("peer").first()`; not found → `emit_error("unknown message_id", code=1)`; else `emit_json(serialize_peer_message(m))` (summary — no payload). Register as `app.command(name="peer-message")`.

### 6.3 `twicc peer-send` (write) — `src/twicc/cli/peer_send.py`

Model on `src/twicc/cli/send_message/command.py` step by step:

1. Typer signature: `def peer_send_cmd(peer: str, text: str, attach: list[str] = [], timeout: int = 30)` — `peer` accepts the peer id or its exact local name; `text` may be inline text or a path to a file (reuse the same `resolve_prompt` helper `send_message/command.py` uses).
2. `django.setup()`; `transport.ensure_server_available()`.
3. Pre-check: peer exists and is `active` (direct ORM read; friendly `emit_error` exit 1 otherwise, mirroring `lookup_session` pre-checks).
4. Attachments: reuse `twicc.cli._drop_request.attachments.validate_and_encode(specs, support, helpers, model)`. `peer-send` has no target session, so unlike `send_message/command.py` (which derives these from the resolved session, ~lines 167–184) use fixed values:
   - `helpers` must be the **`ClaudeCodeHelpers` instance**, NOT the module — `validate_and_encode` calls `helpers.get_effective_image_dimension(model, n)`, an *instance* method (`providers/claude_code/helpers.py` ~line 615; no module-level function exists). Follow `send_message/command.py`'s session pre-check to the provider-helpers accessor it ends up using and call it for the claude_code provider.
   - `support` = that instance's attachment support (`get_attachment_support()` / the `ATTACHMENT_SUPPORT` constant, `providers/claude_code/helpers.py` ~lines 100–110). Comment why claude_code stands in: the peer wire format uses the provider-common SDK block shape; the three size/count caps are identical across providers.
   - `model=None` — safe: `get_effective_image_dimension(None, n)` returns the default dimension (`helpers.py` ~lines 638–643).
   On validation errors: `emit_validation_errors` + exit 1.
5. Origin: resolve the calling session with `resolve_current_session` from `twicc.cli._drop_request.whoami`. This is deterministic: it reads the `forced_session_id` ContextVar first (`whoami.py` ~lines 70–72), which the MCP dispatcher sets on every tool call, and falls back to PID ancestry for a real CLI subprocess. If it resolves, set `origin_session_id` in the payload; if not, send without it (best-effort — the wire `session_title` will be null).
6. Payload `{peer, text, images, documents, origin_session_id}` → `transport.submit(payload, kind="peer:send")` → `transport.wait(sub, timeout)` → `emit_final`. Exit mapping copied from `send_message/command.py` lines 224–230 (0 sent / 3 rejected / 4 failed / 5 timeout).

### 6.4 Watcher + result plumbing

- `src/twicc/drop_requests_watcher.py`, `_KIND_HANDLERS` (lines 44+): add `"peer:send": ("twicc.core.services.peer_messages", "send_peer_message_from_payload", "sent")`.
- `_RESULT_ID_FIELDS` (~lines 183–190): add `"message_id"` and `"peer_id"` so the final status JSON carries them. The remote delivery state travels via `status_extra={"peer_status": …}` (defined in 5.1): `execute_drop_payload` merges `result.status_extra` into the status dict last (`drop_requests_watcher.py` ~lines 234–241; precedent `SettingsDropResult`). The transport-level `status` stays `"sent"` on success — that is the key the exit mapping reads. **Never add `"status"` to `_RESULT_ID_FIELDS` and never emit a `"status"` key inside `status_extra`** — either would clobber the transport status and turn every successful send into a bogus exit code. Final CLI JSON on success: `{"status": "sent", "message_id": …, "peer_id": …, "peer_status": "pending"}`. Note: every failure branch (including network `unreachable`) surfaces as watcher status `rejected` → exit 3, the distinction living in the error `code` — accepted collapse; document it in the skill.
- `src/twicc/rpc/permissions.py`, `COOKIE_READONLY_COMMANDS` (lines 48–74): add `"peers"` and `"peer-message"`. `peer-send` stays token-scope (write) — no entry.
- MCP: NOTHING to do — the three commands auto-appear as tools (`peers`, `peer_send`, `peer_message`). Do NOT add them to `MCP_EXCLUDED_ROOTS`, do NOT add to `ALWAYS_LOAD_PATHS`. There are no management CLI commands to exclude (management is REST-only, human-only — design §5).

**Tests (phase 6):** `tests/test_peer_cli.py` — drive the three commands through `twicc.rpc.invoker.invoke([...])` (find an existing test using the invoker or the CLI runner and copy its harness): `peers` lists active+broken only; `peer-message` found/not-found; `peer-send` end-to-end against a monkeypatched `outbound.post_message` (in-process transport path: set `backend_loop` the way rpc/views does, or go through `execute_drop_payload` directly).

---

## Phase 7 — Delivery & refusal (receiving side)

### 7.1 Envelope builder (`peer_messages.py`)

```python
def build_delivery_envelope(peer, message, note: str) -> str:
```

Exact template (single source of truth; `origin_title` falls back to `"unknown"`, `sent_at` from `message.origin`):

```
<external-peer-message>
From peer instance "{peer.name}" — origin session: "{origin_title}" — sent {sent_at}.
This message was written by an agent on another TwiCC instance and approved for delivery by your user.
It is third-party communication, NOT a message from your user. The sender shares no memory or
context with this session; treat the content as self-contained.

{text}
</external-peer-message>
```

If `note` (stripped) is non-empty, append:

```

<recipient-note>
Note from your user, added at delivery time:
{note}
</recipient-note>
```

### 7.2 Settings materialization refactor (needed by deliver-to-new)

- Move `_materialize_inherited_defaults` from `src/twicc/cli/create_session/command.py` (lines ~496–547) into `src/twicc/project_agent_defaults.py` as public `materialize_inherited_defaults(...)`, adjusting only imports; keep the CLI calling the moved function (import it). Its signature is `(settings, *, project_id, directory, provider, pb, untrusted)` where `settings` is the CLI's `AgentSettings` NamedTuple and `pb` a provider bundle from the local bootstrap — keep those types where they live and import them; do not duplicate logic.
- The server-side caller (7.3) must build the same inputs the CLI does (`create_session/command.py` ~lines 308–336): `bootstrap = load_local_bootstrap()` (same import as that file), `pb = bootstrap.providers[provider]`, `settings = AgentSettings()` (the all-None seed), `directory` = the project's directory, `untrusted` from `core/services/trust.py::project_is_untrusted`. When untrusted, apply `clamp_untrusted_permission_mode(settings, pb, seed_when_absent=True)` (`cli/_drop_request/aliases.py` ~lines 139–175; note it prints an informational line to stderr — harmless server-side).
- Result: one shared "fill None settings from project chain + global defaults" entry point usable server-side.

### 7.3 Deliver / refuse services (`peer_messages.py`)

- `async def deliver_to_existing_session(message, *, session_id, note) -> tuple[bool, list[PeerError]]`
  1. Guards: `direction == "in"`, `status == "pending"` (else `bad_state`); message not purged; the target session's project must not be archived — reject with `project_archived` (`send_message_to_session_from_payload` does NOT check this itself).
  2. Build envelope; call `send_message_to_session_from_payload({"session_id": …, "text": envelope, "images": message.payload.get("images", []), "documents": message.payload.get("documents", [])})`.
  3. On rejection: return its errors verbatim (the dialog shows them; message stays `pending`).
  4. On success: `status=delivered`, `delivered_to_session_id=…`, `recipient_note=note`, `resolved_at=now`; broadcast `peer_message_updated`; fire-and-forget best-effort status callback: `await post_status(peer.base_url, bearer=peer.token_theirs, message_id=…, status="delivered")` inside `try/except Exception: pass` (design §4.1 — failure never blocks local resolution).
- `async def deliver_to_new_session(message, *, project_id, provider=None, note) -> tuple[bool, str | None, list[PeerError]]` (returns new session_id)
  1. Same guards.
  2. The project must exist and not be archived (`project_archived`). `provider = provider or resolve_project_default_provider(project_id)` (`project_agent_defaults.py`); settings materialization + untrusted clamp exactly as specced in 7.2 (bootstrap `pb`, `AgentSettings()` seed).
  3. Call `create_session_from_payload({"session_id": str(uuid.uuid4()), "project_id": …, "provider": …, "text": envelope, "images": …, "documents": …, **settings._asdict()})` (`core/services/session_creation.py`, documented payload keys ~lines 61–83). Two traps a literal reading would miss: **`session_id` is REQUIRED** (`session_creation.py` ~lines 118–119 rejects without it — the CLI path gets it injected by the transport, `transport.py` ~lines 122–123; a direct service call must mint its own UUID), and **`settings` is a NamedTuple** — spread `**settings._asdict()`, never `**settings` (TypeError; the CLI does the same at `command.py` ~line 462).
  4. Rejection/success handling identical to 7.3.1, storing `delivered_to_session` on success.
- `async def refuse_peer_message(message) -> tuple[bool, list[PeerError]]` — guards; `status=refused`, `resolved_at=now`; broadcast; best-effort `post_status(..., status="refused")`.

### 7.4 Owner REST (`owner_views.py` + `urls.py`)

```python
path("api/peer-messages/", peer_owner_views.peer_messages_list),                     # GET ?limit=50 — summaries, pending first
path("api/peer-messages/<int:pk>/", peer_owner_views.peer_message_detail),           # GET — include_payload=True
path("api/peer-messages/<int:pk>/deliver/", peer_owner_views.peer_message_deliver),  # POST {session_id} XOR {project_id, provider?}, + {note?}
path("api/peer-messages/<int:pk>/refuse/", peer_owner_views.peer_message_refuse),    # POST
```

`peer_message_deliver` dispatches to 7.3's two functions based on body keys; 400 with `errors` on failure; 200 `{"session_id": …}` on success.

**Tests (phase 7):** deliver-to-existing with a real test Session (envelope exact-match assertion incl. note variant; rejection surfacing via a session in a rejecting state); deliver-to-new (session created, settings materialized from a project with `default_agent_settings`, untrusted project gets clamped mode); refuse (+ callback monkeypatched, and callback failure not breaking resolution); purged/pending guards.

---

## Phase 8 — Attachment purge task

`src/twicc/peer_purge_task.py`:

- Loop structure modeled on `src/twicc/session_dirs_cleanup_task.py` (~lines 178–208) — wait-loop on the stop event, enable-flag early return, try/except keeping the loop alive. That task is the LOOP model only: it does no DB writes.
- For the writes, follow the periodic-task convention — the **db_writer job queue** (precedents: `pricing_task.py` ~line 74 `submit_async_job(...)`, `search_indexing_task.py` `_MarkSessionsIndexedJob`): define a `_PurgePeerAttachmentsJob` submitted via `submit_async_job`. The job selects `PeerMessage` where `resolved_at < now - PEER_ATTACHMENT_RETENTION`, `purged_at IS NULL`, and payload has a non-empty `images` or `documents`; for each: set `payload["images"] = []`, `payload["documents"] = []` (keep `text`), `purged_at = now`, save `update_fields=["payload", "purged_at"]`.
- `PEER_PURGE_INTERVAL = 6 * 60 * 60`; `PEER_ATTACHMENT_RETENTION = timedelta(days=7)`.
- No broadcast needed (history views read `purged` from REST on open; optional per-row `peer_message_updated` — skip in v1, note in a comment).
- Wire in `src/twicc/cli/run.py`: import next to the other task imports (lines 86–103), `peer_purge_task = asyncio.create_task(start_peer_purge_task(shutdown_event))` next to `session_dirs_cleanup_task` (~line 281), and `await _cancel_task(peer_purge_task, "peer purge")` in the shutdown block (358–458).

**Tests (phase 8):** purge function directly (not the loop): resolved-old purged (bytes gone, text+meta kept, `purged_at` set), pending/recent untouched.

---

## Phase 9 — Frontend: store, WS, toasts

### 9.1 `frontend/src/stores/peers.js`

Model on `stores/workspaces.js`:

```js
state: () => ({ peers: [], messages: [], loaded: false })
getters:
  pendingInboundMessages: (s) => s.messages.filter(m => m.direction === 'in' && m.status === 'pending'),
  pendingRequests: (s) => s.peers.filter(p => p.state === 'pending_received'),
  inboxCount() { return this.pendingInboundMessages.length + this.pendingRequests.length },
actions:
  applyPeers(peers), applyMessages(messages),          // wholesale replace (WS connect snapshot)
  upsertPeer(peer), removePeer(peerId), upsertMessage(message),  // incremental
```

Include `acceptHMRUpdate` like `settings.js` (line ~1259). Hydration comes exclusively from the WS connect snapshot (share precedent) — no bootstrap change.

### 9.2 `useWebSocket.js` — new cases in `handleMessage` switch (starts line 987)

All lazy-import the store (`import('../stores/peers')`), matching the `workspaces_updated` idiom (line 1462):

- `'peers_updated'` → `applyPeers(msg.peers)`
- `'peer_messages_updated'` → `applyMessages(msg.messages)`
- `'peer_updated'` → before upserting, detect the held-accept transition (stored copy was `pending_sent` without `remote_accepted_at`; incoming has `remote_accepted_at` set and `code_confirmed_at` null) → warning toast via `PeerToastContent` mode `request`: `"${peerName}" accepted your request — complete the code verification to activate` (this is the security-relevant case the user must notice; without it a held accept is invisible outside the manager); then `upsertPeer(msg.peer)`
- `'peer_removed'` → `removePeer(msg.peer_id)`
- `'peer_request_received'` → `upsertPeer(msg.peer)` + toast: `toast.custom(PeerToastContent, { type: 'info', title: 'Peer request', duration: 15000, props: { mode: 'request', peer: msg.peer } })`
- `'peer_accepted'` → `upsertPeer(msg.peer)` + `toast.info(\`"${msg.peer.name}" accepted your peer request\`)`
- `'peer_message_received'` → `upsertMessage(msg.message)` + `toast.custom(PeerToastContent, { type: 'info', title: \`Message from ${peerName}\`, duration: Infinity, props: { mode: 'message', message: msg.message } })` (resolve `peerName` from the store)
- `'peer_message_updated'` → before upserting, if the stored copy is `direction === 'out' && status === 'pending'` and the new one is `delivered`/`refused`, `toast.info(\`Your message to "${peerName}" was ${status}\`)`; then `upsertMessage`.

`PeerToastContent` must be lazy: add `const PeerToastContent = defineAsyncComponent(() => import('../components/peer/PeerToastContent.vue'))` at the top of `useWebSocket.js`. There is NO component-toast precedent inside `useWebSocket.js` today (its `toast.custom` calls all use `html:`); the `defineAsyncComponent` precedents are `useToast.js` line ~40 (`SessionToastContent`) and `App.vue` line ~135 (`ProviderAuthToastContent`). `toast.custom` detects a component via its `setup` key, which an async wrapper has (`useToast.js` ~line 135).

### 9.3 `PeerToastContent.vue`

Clone the skeleton of `components/session/SessionToastContent.vue` (props incl. `item` for `.clear()`, `.wa-light` action row). Props: `mode` (`'request' | 'message'`), `peer?`, `message?`, `item`. Buttons:

- mode `request`: **Review** → `window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))`; **Later** → dismiss.
- mode `message`: **Read** → `window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox', { detail: { messageId: message.id } }))`; **Later** → dismiss.

Every action ends with `props.item?.clear?.()` — the guarded form, exactly as `SessionToastContent.vue` does (~lines 86/110/128): `item` is injected by `CustomNotification` and may be absent.

---

## Phase 10 — Frontend: settings section + Peers manager

### 10.1 Synced setting `peerBaseUrl`

- `frontend/src/constants.js`: add `'peerBaseUrl'` to `SYNCED_SETTINGS_KEYS` (~line 211).
- `frontend/src/stores/settings.js`, FOUR spots (a literal clone of only the getter/setter will throw):
  1. `SETTINGS_SCHEMA`: synced entry with `null` placeholder default (copy the `shareBaseUrl` entry).
  2. `SETTINGS_VALIDATORS` (~line 167): add `peerBaseUrl: (v) => typeof v === 'string'` next to `shareBaseUrl` — the setter calls `SETTINGS_VALIDATORS.peerBaseUrl(...)`; without this entry it TypeErrors on first Apply.
  3. Getter `getPeerBaseUrl` (next to `getShareBaseUrl`, ~line 348) and setter `setPeerBaseUrl` (clone `setShareBaseUrl`, ~line 910 — trim + strip trailing slashes) **without** any "hostname must differ from the app" restriction: `peerBaseUrl` typically IS the working origin. Validate absolute http(s) URL in the SettingsPopover apply handler; non-fatal warning when scheme is `http:` (design §4.3).
  4. `collectAllSyncedSettings` (~line 1140): add `peerBaseUrl: store.peerBaseUrl` (localStorage snapshot parity with `shareBaseUrl`).

### 10.2 `SettingsPopover.vue`

- `sections` computed (line 92): add `{ id: 'peers', label: 'Peers', synced: true }` after `sharing`.
- `selectSection(id)` (line 158): seed `peerBaseUrlInput` like the sharing case (line 165).
- New `<section v-if="activeSection === 'peers'">` cloned from the Sharing section (template lines 1462–1504): `wa-input` + Apply button + `wa-callout` error + hint ("Your address, advertised to peers. Empty disables peer messaging. HTTPS strongly recommended."), plus a **Manage peers** `wa-button` whose click handler runs `window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))`. NOTE: this deliberately does NOT mirror the "Shared links" button — that one toggles a LOCAL ref (`showShareManager`, ~line 624) and mounts `ShareManagerDialog` inside `SettingsPopover` itself (~line 2016). `PeersManagerDialog` is instead mounted once in `App.vue` (10.3) so toasts can open it too; do not mount it in `SettingsPopover`.

### 10.3 `PeersManagerDialog.vue`

Mounted in `App.vue` next to `ShareManagerDialog`, opened by the `twicc:open-peers-manager` CustomEvent (idiom: `openShareManager`, `App.vue` lines ~583/625). Form patterns from `ProjectEditDialog.vue` (form id, submit button `form` attr via `setAttribute`, `@wa-after-show` focus, `.self`/target guards on dialog events — CLAUDE.md "Dialog forms" + "Bubbling custom events").

Content (REST via `apiFetch`, store updated by WS broadcasts — no manual refetch):

- Own address recap (from `getPeerBaseUrl`) + disabled-state callout when empty.
- **Pending requests** (`state === 'pending_received'`): claimed name + URL, then the **verification code displayed prominently** (`peer.verification_code`, large monospace) with the hint "Share this code with the requester over a channel you trust — accepting unlocks once they confirm it." Below: a local-name `wa-input`, **Accept** — DISABLED until `peer.verified_at` is set (it flips live via the `peer_updated` broadcast; show a "Verified ✓" tag once set) — (`POST /api/peers/<id>/accept/ {name}` — surface `unreachable`/`not_verified` errors) and **Refuse** (`POST …/refuse/`).
- **Pending sent** (`state === 'pending_sent'`): URL + a **code entry** (6-digit `wa-input` + "Verify" button → `POST /api/peers/<id>/verify/ {code}`, hint "Enter the code your peer reads to you") with error display (`bad_code`, `code_regenerated`, `unreachable`); a "Code confirmed ✓" tag once `code_confirmed_at` is set; if `remote_accepted_at` is set while unconfirmed, a `wa-callout variant="warning"`: "Accepted remotely, but your code verification hasn't completed — contact your peer before trusting this relationship."
- **Peer list**: name, URL, state chip (`active`/`broken`), `last_contact_at` via `wa-relative-time`; actions rename + edit URL (PATCH), **Remove** (DELETE, with a confirm step: "Removes the relationship and its message history silently — the peer is not notified and will be rejected on its next send.").
- **Add peer** form: name + URL → `POST /api/peers/` — on `unreachable`/`peer_host_unset`/`duplicate` errors show `wa-callout variant="danger"`.

Rendering rule: handshake-supplied strings (`remote_display_name`, `base_url`, any claimed name) come from an unauthenticated endpoint and are attacker-controlled — render them ONLY via Vue text interpolation, never `v-html`.

---

## Phase 11 — Frontend: inbox + read-and-route dialog

### 11.1 `PeerInboxButton.vue` + placement

Small `wa-button` + `wa-icon` (envelope) + `wa-badge` (already imported in `main.js` line 14) showing `peersStore.inboxCount` when > 0; template model: `components/app/CommandPaletteButton.vue`. Click → dispatch `twicc:open-peer-inbox`. Mount in `views/ProjectView.vue` `.sidebar-footer-buttons` (line ~2547), next to `<SettingsPopover />`, AND in `views/HomeView.vue` next to its `<SettingsPopover />` (~line 157 — HomeView has no footer button group), so the badge is visible outside ProjectView too. (Recorded deviation from the design's "header" wording: TwiCC has no top app bar; the sidebar/footer chrome is the persistent surface.) Render nothing when `getPeerBaseUrl` is empty AND the store has no peers/messages (feature dormant).

### 11.2 `PeerInboxDialog.vue`

Mounted in `App.vue`, opened by `twicc:open-peer-inbox` (optionally with `detail.messageId` → directly open the review dialog on that message). Sections:

- **Pending requests** (shortcut list, each row linking to the manager dialog).
- **Pending messages** (`pendingInboundMessages`): peer alias, `text_preview`, attachment count from `attachments_meta`, relative time → click opens `PeerMessageReviewDialog`.
- **History**: recent resolved messages both directions (status chips; purged ones show "N attachments (purged)").

### 11.3 `PeerMessageReviewDialog.vue`

On open: `apiFetch('/api/peer-messages/<id>/')` (full payload). Renders:

- Provenance header: `"{peer.name}" · session "{origin.session_title}" · <wa-relative-time origin.sent_at>`.
- Message text as markdown: `renderMarkdown` from `utils/markdown.js` is **async** — `await` it into a ref and bind that ref with `v-html` (HelpDialog pattern — `components/help/HelpDialog.vue` ~lines 15/57/106). A direct `v-html="renderMarkdown(text)"` renders `[object Promise]`.
- Images/documents: map `payload.images`/`payload.documents` through `sdkBlockToMediaItem` (`utils/fileUtils.js` ~line 482 — one function handling image and document/text blocks; drop nulls), thumbnails via `components/media/MediaThumbnailGroup.vue` (`items` prop of MediaItems; full-size preview is built into it via its embedded `MediaPreviewDialog`).
- **Recipient note** `wa-textarea` (optional, hint: "Injected alongside the message, attributed to you").
- Three actions (design §6.2):
  1. **Deliver to existing session** — two-step picker: `wa-select` (or filterable list) over `store.projects` (always loaded; **exclude archived projects**) → on project pick, `dataStore.loadSessions(projectId)` (`stores/data.js` line ~2181 — loads one page of most-recent sessions; fine for the picker, note the limit in a comment) → filterable session list (title + relative time; exclude drafts/subagents). Confirm → `POST /api/peer-messages/<id>/deliver/ {session_id, note}`. Errors (the send-message rejection codes + `project_archived`) render in a `wa-callout` verbatim.
  2. **Deliver to new session** — project picker (same filterable project list, archived excluded); before POSTing call `ensureProjectTrust(projectId)` (`composables/useTrustGate.js` — the helper `create.session-in` uses, `commands/staticCommands.js` line ~1026) → `POST … {project_id, note}`. On success, offer "Go to session" (`router.push(sessionRouteLocation(...))` — see `SessionToastContent.vue` `goToSession`).
  3. **Refuse** — `POST …/refuse/` with a confirm step.

WA components used are all already imported in `main.js` (§10 of the frontend report); if you add one that isn't, import it there.

---

## Phase 12 — Skills, docs, plugin bump

1. Read `src/twicc/agent/plugin/README.md` (structure/wording spec) and calibrate on `twicc-send-message/SKILL.md` + one read skill (`twicc-sessions`).
2. Create three skills, each `SKILL.md` following the README's exact section order and the verbatim "How to invoke" block:
   - `twicc-peers` — list peer instances the user has approved for cross-instance messaging; use before `peer-send` to resolve a name.
   - `twicc-peer-send` — send a message (text + attachments) to a peer instance; document: no confirmation on our side, but delivery requires the REMOTE user's approval — status stays `pending` until then; `peer_broken` error meaning; attachments like `send-message`; messages must be self-contained (the receiving agent shares no memory).
   - `twicc-peer-message` — re-check an outbound message's status (`pending`/`delivered`/`refused`/`failed`).
3. `SKILLS-AND-CLI.md` (repo root): new `## Peers` group with three `###` sections, matching the existing per-command format and brevity.
4. `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: bump `version` **minor** (new skills): `0.61.2` → `0.62.0` (re-check the current value first; another change may have bumped it).
5. Do NOT touch CHANGELOG.md. Do not update AGENTS.md unless CLAUDE.md changed (it doesn't need to for this feature).

---

## Phase 13 — Final verification

1. Full test run: `cd <worktree> && uv run --active pytest` — everything green, not only the new files.
2. `cd <worktree>/frontend && npm run build` is NOT required (no shim/shell/companion bundle touched) — skip unless you edited `artifact-broker/*`, `artifact-shell/*`, `browser-companion/*`, `share-*/*`, `element-select/*`.
3. Grep sweeps:
   - `grep -rn "token_ours\|token_theirs" src/twicc/core/serializers.py frontend/src` → must be empty.
   - `grep -rni "host\b" <new peer files>` → no multi-host vocabulary leaked into peer code/strings.
4. Manual smoke script (document in your report, run only if the user asks for a live test): two worktree instances, set each other's `peerBaseUrl`, add → read the 6-digit code on the receiver, enter it on the requester (verify) → accept → `twicc peer-send` from a session → toast/badge → deliver to a new session → check envelope + `peer-message` status flip.

## End-of-task reminders to give the user

- `migrate` needed on their running instance(s) (migration `0131_peer`).
- Backend restart needed (new routes, middleware entry, periodic task); frontend picks up via Vite.
- CHANGELOG entry: proposed only, on their explicit request.

## Explicitly out of scope (v1)

- `reply_to` threading (design §8 — deferred; `origin_session` FK and `message_id` uniqueness already lay the groundwork).
- Apprise/external notifications and OS notifications for peer events (design §6.1 "optionally" — deferred).
- Automatic retry of failed outbound sends; a "resend request" action on a failed peer add.
- Any peer-relationship management on the CLI/MCP surface (human-only, forever by design §4.4).
