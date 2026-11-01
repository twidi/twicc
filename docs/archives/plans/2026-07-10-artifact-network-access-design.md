# Artifact Network Access Management — Design

**Status:** design complete, decisions resolved with the user 2026-07-10; implementation not started
**Date:** 2026-07-10
**Scope:** owner-side management of an artifact bookmark's network access — a unified per-host list (allowed / explicitly denied / pending) in the bookmark edit dialog, fed by server-recorded denial events (viewer fetches refused by the share proxy, plus owner "Deny" clicks in preview), with per-host and bulk allow, explicit deny, and full provenance (which shares, which viewers by ip/user-agent, owner-side cases).
**Companions:** `2026-07-05-sharing-design.md` (sharing, esp. §9.3/D6), `2026-06-18-artifact-network-broker-design.md` (broker, esp. §6.4/§9/§10).

---

## 1. Background — what exists today

- `ArtifactBookmark.allowed_hosts` (JSON dict `{scheme://host:port: {kind}}`) is the broker's persisted "Forever" allowlist. For **shares** it is enforced server-side and **read live on every request** (`share_artifact_proxy`, `src/twicc/share/artifact_views.py:124` → `artifact_proxy(enforced_allowlist=…)`, `src/twicc/artifacts/proxy.py:274`). Consequence this design exploits: adding/removing a host applies **instantly to every existing link** — no propagation, no re-creation.
- In `artifact_proxy`'s `mode == "fetch"` branch the order is: DNS resolve + classify (`kind` known) → metadata hard-block → allowlist check → `{"error":"blocked","reason":"not_allowed"}` (~line 340). At the refusal point the server holds everything: normalized `host_key` (`normalize_host_key`), fresh `kind`, request ip/user-agent, and (via the share wrapper) the `Share` + `ArtifactBookmark`. Nothing is recorded today.
- Owner-side, a prompt "Deny" (`frontend/src/artifact-broker/host.js`, `gate()`) throws `"denied by user"` and persists nothing.
- `POST /api/artifact-bookmarks/<id>/allowed-hosts/` is wired in the UI (prompt "Forever", ShareDialog promotion); the **`DELETE` twin exists but is called nowhere** (`src/twicc/views.py::artifact_bookmark_allowed_hosts`, service `remove_artifact_allowed_host`).
- Precedents reused: `ShareAccess` (ip/ua rows, pruned to 500/share) and the batched write pattern of `src/twicc/share/view_tracking.py` (in-memory accumulator, 30 s flush task, prune, broadcast).

## 2. Requirements (user-stated)

1. In `ArtifactBookmarkDialog` (edit mode), a **"Network access"** section: one unified list of hosts.
2. Allowed hosts listed, each removable (wire the sleeping `DELETE`).
3. Denied-and-not-preauthorized hosts **recorded server-side** when a share fetch is refused; the owner can allow them one by one or in bulk.
4. Full provenance per denied host for abuse detection: which share(s), which viewers (ip / user-agent — same capture as `ShareAccess`), and owner-side preview denials distinguished.
5. An explicit **"Deny"** decision per host: keeps the recorded events, remembers the choice, and (decided) also auto-denies without prompting in owner preview.
6. UI drill-down: by default host + total count; the count expands to per-provenance rows (share label + count); that count expands to the detailed date/ip/ua list, rendered by the **same component** as the "Recent views" panel (extracted, shared, same cap and scroll).
7. Human-only, like all of sharing: REST for the browser UI only — no CLI verb, no MCP tool, no drop-request kind.

## 3. Data model

### 3.1 `ArtifactNetworkDenial` (new model, migration after `0124_share`)

One row per distinct `(bookmark, share, host_key, ip, user_agent)` combination — an **application-level upsert** with a counter, not one row per event (a non-allowed host can be hammered; a fetch loop must not evict other provenances):

| Field | Type | Notes |
|---|---|---|
| `bookmark` | FK `ArtifactBookmark`, CASCADE | the anchor; deleting the bookmark drops its denials |
| `share` | FK `Share`, **null**, CASCADE | `NULL` = owner-side preview denial. Deleting a share drops its rows (a deleted link 404s and produces no further traffic) |
| `host_key` | `CharField(255)` | normalized `scheme://host:port` (`normalize_host_key`), never taken raw from a client |
| `kind` | `CharField(16)` | `public` / `loopback` / `lan`, server-resolved at denial time; feeds the later allow/deny POSTs |
| `ip` | `CharField(64)`, blank | empty for owner rows |
| `user_agent` | `CharField(255)`, blank, truncated | empty for owner rows |
| `count` | `PositiveIntegerField` default 1 | incremented per repeated event |
| `first_at` / `last_at` | `DateTimeField` | |

- **No DB unique constraint** on the quintuple: SQLite treats `NULL` `share` values as distinct, so uniqueness is enforced by the upsert in Python — which already runs under the DB write lock (flush task / service). Index on `(bookmark, -last_at)` for the pruned read.
- **Pruning:** cap **500 rows per bookmark**, evicting oldest `last_at` (same total-order robustness care as `view_tracking._persist`).
- `metadata` and `unresolved` refusals are **not** recorded (never allowable / no kind).

### 3.2 `ArtifactBookmark.denied_hosts` (new JSON field)

Symmetric to `allowed_hosts`: `{host_key: {kind}}`, `JSONField(default=dict, blank=True)`. It stores the owner's **explicit "deny" decision** per host. The decision is per host; the event rows in `ArtifactNetworkDenial` are provenance and keep accumulating for a denied host (abuse stays visible).

### 3.3 Host states

A host appearing anywhere in the system is in exactly one state:

| State | Meaning | Source | Actions |
|---|---|---|---|
| **allowed** | reachable by viewers and (no prompt) by the owner | key in `allowed_hosts` | Remove |
| **denied** | explicitly refused by the owner; auto-denied in preview, refused for viewers (as any non-allowed host) | key in `denied_hosts` | Allow (with non-public confirm), Un-deny |
| **pending** | denial events recorded, no owner decision yet — the inbox | denial rows whose key is in neither dict | Allow (single or bulk, with non-public confirm), Deny |

Transitions (all through `core/services/artifact_bookmark_mutation.py`, lock + broadcast):

- **Allow** (`add_artifact_allowed_host`, extended): add to `allowed_hosts`, remove from `denied_hosts` if present, **purge all `ArtifactNetworkDenial` rows of the bookmark for that `host_key`**. Single point — covers the dialog, the ShareDialog promotion loop, and the prompt's "Forever".
- **Deny** (new `add_artifact_denied_host`): add to `denied_hosts`, remove from `allowed_hosts` if present (defensive; the UI only offers Deny on pending/denied hosts). Rows are kept.
- **Un-deny** (new `remove_artifact_denied_host`): remove from `denied_hosts`; the host reverts to pending (its rows are still there) or disappears if it has no rows.
- **Remove allowed** (`remove_artifact_allowed_host`, existing): the host leaves the list; future refusals re-enter as pending.

The two dicts are mutually exclusive by construction (each add removes from the other).

## 4. Recording denial events

### 4.1 Viewer path (server-side, authoritative)

`artifact_proxy` gains an optional `on_not_allowed: Callable[[str, str], None]` (host_key, kind) parameter, invoked in the `not_allowed` branch. `share_artifact_proxy` passes a closure capturing `ctx.share` / `ctx.bookmark` and the request's ip / user-agent (same extraction as `view_tracking`: `_get_client_ip`, truncated UA) that calls `note_denial(...)` in a **new module `src/twicc/artifacts/denial_tracking.py`**, mirroring `view_tracking.py`:

- `note_denial(bookmark_id, share_id, host_key, kind, ip, user_agent)` accumulates in memory, keyed by the full quintuple with a per-key counter — the upsert absorbs hammering **in RAM** before any DB write.
- A 30 s flush task (started in `run_server()` next to the share-view flush) persists via `asyncio.to_thread` (no write lock — exactly like `view_tracking._persist`): upsert rows (`count += n`, `last_at`, refresh `kind` to the latest resolved value), prune to 500/bookmark, then broadcast (§7). Failed flushes re-queue, same as views.
- **Flush-time guard against the allow race:** entries whose `host_key` is in the bookmark's *current* `allowed_hosts` are dropped at flush, not persisted — a viewer denial noted just before the owner allowed the host must not resurrect rows the allow purge already removed.

The owner path of `artifact_proxy` (`enforced_allowlist is None`) passes no callback — nothing changes there.

### 4.2 Owner path (preview "Deny" click)

`createBrokerHost` gains an optional `onDenied(url, kind)` callback (symmetric to `persistAllow`), called in `gate()` when the decision is `deny`. Both owner mounts supply it when a bookmark exists — `FilePane.vue` and `ArtifactShellApp.vue` (owner mode) — as a **fire-and-forget** `POST /api/artifact-bookmarks/<id>/network-denials/` `{url, kind}`; a failure never breaks the deny itself. The server re-normalizes the URL (`normalize_host_key`) and validates `kind` — the client's values are provenance data, not an authorization. Rows are written directly through the service (no batching: one event per human click; the DB upsert coalesces repeats). Auto-denied fetches (§5) do **not** POST — the decision is already persisted; only viewer traffic keeps counting on a denied host.

## 5. Broker behavior change — persisted deny, live dicts (decided)

Today `createBrokerHost` **snapshots** `allowedHosts` at mount into a local dict (host.js ~127) and is deliberately not re-created on bookmark changes — only `getBookmarkId` is a live getter. Implemented naively, Deny/Un-deny/Remove from the dialog would have no effect on an already-open preview. So the host's contract changes: the persisted dicts become **live getters**, `getAllowedHosts()` / `getDeniedHosts()` (the `getBookmarkId` precedent), evaluated per request. Per-request precedence in `proxyFetch`:

1. Own-dir asset → host-direct, no prompt (unchanged, unconditional: the artifact's own files are not network egress — a deny on the TwiCC origin's host key must not break the artifact loading its own bundle).
2. **Denied** (`key in getDeniedHosts()`) → reject immediately (`"denied by owner"`), **no prompt** — checked first among egress paths, so an explicit persisted deny also overrides an earlier "This session" grant (and clears it from the module cache).
3. Session grant or persisted allow, kind-matched (`getAllowedHosts()` replaces the mount snapshot) → proceed (unchanged otherwise, incl. rebind re-prompt).
4. Otherwise → `gate()` / prompt (unchanged).

Notes:

- Deny is per host, kind-insensitive (a rebind doesn't resurrect a denied host — unlike allow, where a kind change re-prompts for security).
- **SPA preview is live**: `FilePane.vue` passes getters over the store's reactive bookmark (`() => artifactBookmark.value?.allowed_hosts ?? {}`, same for `denied_hosts`); the `artifact_bookmark_updated` broadcast keeps them current, so a dialog action applies to the open preview on its next request.
- **Dedicated page is reload-only** (accepted): its data island is static server-rendered state; the getters close over it. The shell's island (`broker_html.py::artifact_shell_response` → `artifact-shell/main.js`) gains `deniedHosts`; `useArtifactBroker` forwards both getters.
- **Remove-allowed vs session grant** (accepted): removing a "Forever" host doesn't revoke a same-tab "This session" grant — that grant's own contract is "kept until you reload or close the tab". Only an explicit Deny overrides it (rule 1).
- Non-bookmarked previews have no `denied_hosts` (nothing to read) — unchanged.
- Share mode is unaffected server-side (not-allowed is already refused); `denied_hosts` is never sent to the share shell.

## 6. Endpoints (owner REST, password-gated; human-only — no CLI/MCP/drop-request)

| Route | Method | Purpose |
|---|---|---|
| `/api/artifact-bookmarks/<id>/allowed-hosts/` | POST / DELETE | existing — allow (now also purges denials + un-denies) / remove |
| `/api/artifact-bookmarks/<id>/denied-hosts/` | POST / DELETE | new, mirrors allowed-hosts — mark / unmark the explicit deny decision. Body `{"url": …, "kind": …}` on POST (kind validated), `{"url": …}` on DELETE. The dialog passes the stored `host_key` as `url` — `normalize_host_key` is idempotent on keys, no URL round-tripping needed (same for the existing allowed-hosts DELETE). Returns the updated serialized bookmark |
| `/api/artifact-bookmarks/<id>/network-denials/` | GET | list denial rows: `{denials: [{host_key, kind, share: {id, label, status} | null, ip, user_agent, count, first_at, last_at}]}`, newest `last_at` first. Grouping (host → share → detail) is client-side |
| `/api/artifact-bookmarks/<id>/network-denials/` | POST | record one owner-side denial event `{url, kind}` (§4.2) |

`serialize_artifact_bookmark` gains `denied_hosts`. Bulk allow = the front loops over the existing single-host POST (same pattern as ShareDialog's promotion loop).

## 7. Live refresh

Denial writes (flush and owner-event POST) broadcast a lightweight `artifact_bookmark_denials_updated {bookmark_id}` on the `updates` group. The bookmark dialog, when open on that bookmark, refetches the denial list. Allow / deny / un-deny / remove already flow through the existing `artifact_bookmark_updated` broadcast (the dicts live on the bookmark), which keeps `FilePane`'s reactive `allowed_hosts` / `denied_hosts` props fresh for the broker host too.

## 8. UI

### 8.1 `ArtifactBookmarkDialog` — "Network access" section (edit mode only)

Fetches `network-denials` on open. One unified list, ordered **pending → denied → allowed** (action-needed first), each row: status tag, `host_key` (code), kind tag (`loopback`/`lan` visually distinct from `public`), then per state:

- **pending**: checkbox (for bulk), total count (clickable), **Allow** and **Deny** buttons. A header **"Allow selected"** button appears when ≥1 checked.
- **denied**: total count (clickable, still accumulating from viewers), **Allow** and **Un-deny**.
- **allowed**: **Remove** button (wires the existing DELETE). No count (allow purged the rows).

**Drill-down** (two levels, per the user's spec): clicking a host's count expands the per-provenance list — one row per share (`label`, falling back to the share id) or "You, in preview" for owner rows — each with its own count; clicking that count expands the detailed list (date, ip, truncated-expandable user-agent) rendered by **`AccessLogList.vue`**, a new shared component extracted from `ShareListPanel.vue`'s "Recent views" panel (same row layout, same `max-height` scroll, same UA expansion) and used by both.

**Non-public honesty preserved**: allowing from this list bypasses the consent prompt, so Allow on a `loopback`/`lan` host (single or within a bulk selection) first shows the same adaptive warning as `ArtifactBrokerPrompt` (where the name resolves; TwiCC's server makes the request; anonymous viewers of any share will reach it) and requires an explicit confirm. `public` hosts allow without friction.

The `kind` used for allow/deny POSTs is the host's **most recent** denial row's kind.

### 8.2 `ShareDialog` bridge

In the artifact network callouts of `ShareDialog`, a short line + **"Manage network access…"** button opening `ArtifactBookmarkDialog` in edit mode **on top of the share dialog, without closing it** (WA dialogs stack; ShareDialog is already nested inside the bookmark dialog today). Circular-import care (HMR rule): `ArtifactBookmarkDialog` statically imports `ShareDialog`, so the back-reference is `defineAsyncComponent(() => import('../artifacts/ArtifactBookmarkDialog.vue'))`.

### 8.3 Build note

`artifact-broker/host.js`, `artifact-shell/*` and `broker_html.py`'s island are **not HMR'd** — `cd frontend && npm run build` after touching them. The SPA-side SFCs (`ArtifactBookmarkDialog`, `ShareDialog`, `FilePane`, `AccessLogList`) are HMR'd normally.

## 9. Decisions (resolved with the user, 2026-07-10)

| # | Question | Decision |
|---|---|---|
| N1 | Event aggregation | Upsert per `(bookmark, share, host_key, ip, user_agent)` with counter + first/last timestamps; UI shows host + count with two-level drill-down (share-level counts, then date/ip/ua detail via the shared extracted component) |
| N2 | Denial rows when a host is allowed | **Purged** — the list is an inbox, not an audit log |
| N3 | Surface | Bookmark edit dialog only; ShareDialog gets a pointer + button opening it without closing itself |
| N4 | Owner-side denial rows | Recorded via client POST on the prompt's Deny; `share NULL`, no ip/ua ("You, in preview") |
| N5 | Explicit Deny decision | New `denied_hosts` dict on the bookmark, symmetric to `allowed_hosts`; keeps event rows; third state in the list |
| N6 | Deny effect on owner preview | **Auto-deny without prompt** (the broker host reads `denied_hosts` like `allowed_hosts`); un-deny from the dialog |
| N7 | Non-public allow from the list | Adaptive warning + explicit confirm (prompt-equivalent honesty); public = frictionless |
| N8 | Pruning | 500 denial rows per bookmark, evict oldest `last_at` |

## 10. File map

| Concern | Location |
|---|---|
| Refusal point + callback | `src/twicc/artifacts/proxy.py` (~340, `artifact_proxy`) |
| Share wrapper (context capture) | `src/twicc/share/artifact_views.py::share_artifact_proxy` |
| New tracking module | `src/twicc/artifacts/denial_tracking.py` (pattern: `src/twicc/share/view_tracking.py`) |
| Flush task start | `src/twicc/cli/run.py::run_server` |
| Model + migration | `src/twicc/core/models.py` (`ArtifactNetworkDenial`, `ArtifactBookmark.denied_hosts`), migration after `0124_share` |
| Services | `src/twicc/core/services/artifact_bookmark_mutation.py` (extend allow, new deny/un-deny, purge) |
| Serializer | `src/twicc/core/serializers.py::serialize_artifact_bookmark` |
| Endpoints | `src/twicc/views.py` (`artifact_bookmark_allowed_hosts` neighbourhood), `src/twicc/urls.py` |
| Broker host (deny gate + onDenied) | `frontend/src/artifact-broker/host.js`, `frontend/src/composables/useArtifactBroker.js` |
| Owner mounts | `frontend/src/components/files/FilePane.vue` (~347, ~363), `frontend/src/artifact-shell/ArtifactShellApp.vue` + `main.js`, `src/twicc/artifacts/broker_html.py` |
| Dialog UI | `frontend/src/components/artifacts/ArtifactBookmarkDialog.vue` |
| Shared log-list component | new `frontend/src/components/share/AccessLogList.vue`, extracted from `ShareListPanel.vue` |
| ShareDialog bridge | `frontend/src/components/share/ShareDialog.vue` |
| Adaptive non-public warning copy | `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` (reference) |

## 11. Tests (pytest)

- `denial_tracking`: in-memory coalescing, upsert (new row / count increment / kind refresh), pruning at 500 per bookmark, re-queue on failed flush, flush-time drop of entries whose host is now allowed.
- Proxy callback: share fetch to a non-allowed host records a denial with share + ip/ua; allowed host records nothing; metadata/unresolved record nothing; owner path (no allowlist) records nothing.
- Endpoints: GET list shape (share label join, owner rows), POST owner event (URL normalization, kind validation, upsert), denied-hosts POST/DELETE (dict mutations, mutual exclusion with allowed), 404s.
- Allow purge: allowing a host removes its denial rows and its `denied_hosts` entry.
- CASCADE: deleting a share / bookmark removes the expected rows.
- Serializer: `denied_hosts` present.
