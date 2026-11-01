# Sharing — Design

**Status:** design complete, all decisions resolved 2026-07-05 (§18); implementation not started
**Date:** 2026-07-05
**Scope:** Public, read-only sharing of (a) a single session transcript and (b) a bookmarked artifact, via opaque capability URLs under `/share/…`, with per-link options (snapshot/live, per-link password, expiration, revocation), view tracking, a management UI, and an optional dedicated "share-only" origin so the owner's working URL stays private.

> *How to read this doc:* self-contained for a cold-start implementing agent, but assumes general TwiCC codebase familiarity. Read §19 (file map) early. The companion implementation plan is `2026-07-05-sharing-implementation-plan.md`. All decisions are resolved in §18; the plan doc is consolidated accordingly.

---

## 1. Background — what exists today

- **Whole-instance auth.** `PasswordAuthMiddleware` (`src/twicc/auth/middleware.py:57`) gates `/api/*` and `PROTECTED_NON_API_PREFIXES = ("/artifacts/",)` behind one session cookie bound to `TWICC_PASSWORD_HASH` (fingerprint check — rotating the password invalidates all sessions). `PUBLIC_PATHS = ("/api/auth/", "/static/", "/artifacts/auth")`. Everything else falls to the SPA catch-all (`views.spa_index`), which carries no data.
- **Local-only gate.** With no password set, `remote_access_blocked()` (`src/twicc/auth/local_access.py`) refuses every data path for non-local callers (raw TCP peer must be loopback and no forwarding header) unless `TWICC_ALLOW_INSECURE_REMOTE`. Access is all-or-nothing: anyone with the password sees *everything*.
- **No sharing prior art.** Grep over code and `docs/plans/` finds none. The multi-host design (`.worktrees/multi-hosts/docs/plans/2026-07-02-multi-host-design.md`) is federation between *authenticated* instances a user owns; its useful precedent is extending the auth middleware with a narrower credential type, not public sharing.
- **Artifact serving is already share-shaped.** `/artifacts/<bookmark_id>/` (`views.artifact_serve`, `src/twicc/views.py:3581`) serves a bookmarked file + sibling assets with path confinement (`confined_artifact_path`), wrapping top-level HTML in a trusted shell (`artifact_shell_response`, `src/twicc/artifacts/broker_html.py`) that injects the broker shim + strict CSP (`connect-src 'none'`). Only the bookmark id (a guessable sequential int) and the instance password stand between the internet and an artifact today.
- **Transcript rendering is reuse-friendly.** The renderer tree (`SessionItemsList.vue` → `VirtualScroller` → `SessionItem.vue` → provider items → shared `ToolUseContent.vue` / `MarkdownContent.vue`) is provider-agnostic and gates nearly every "navigate elsewhere" affordance behind `provide()`/`inject()` with `inject(..., null)` fallbacks — a host that doesn't provide them gets those buttons hidden for free (inventory in §8.4).
- **Item loading contract.** `GET …/items/metadata/` (all lines, `.defer("content")`) then `GET …/items/?range=A:B` (repeatable; the endpoint 400s without a range) — deliberate guard against whole-transcript dumps. Live updates arrive over one global WS firehose (Channels group `"updates"`, `WSConsumer` in `src/twicc/asgi.py:410`): every authenticated client receives every message, filtered only by message *type*. A public viewer can never join that.
- **Token precedent.** RPC PATs: `secrets.token_urlsafe(32)` prefixed `twicc_pat_`, SHA-256 digest stored, `hmac.compare_digest` verify (`src/twicc/auth/tokens.py`). Short non-secret handles: `tok_<hex4>`.
- **Standalone bundle precedent.** `frontend/src/artifact-shell/` + `vite.config.shell.js` (lib mode, fixed filenames, output into `src/twicc/static/artifact-shell/`), served by `views.artifact_shell_asset` under the public `/_twicc/artifact-shell/` prefix; the page itself is built server-side with a JSON data island (`artifact_shell_response`).
- **Mutation conventions.** One service module per domain in `src/twicc/core/services/` (e.g. `artifact_bookmark_mutation.py`) called by both REST views and CLI drop-request handlers (`drop_requests_watcher.py` `_KIND_HANDLERS`), writing under `run_under_db_write_lock`, broadcasting over the `updates` group. Every CLI command is auto-exposed at `/rpc/<command>`.

## 2. Goals and non-goals

**Goals**
1. Share one session as a read-only transcript: all items and tool renderings, faithful to the real UI, with **zero** navigation out of the transcript (no Files/Artifacts/Git/Terminal tabs, no other sessions, no file-viewer links).
2. Share one bookmarked artifact: the same rendering as `/artifacts/<id>/` (HTML shell + CSP, or direct file) behind a share token instead of the instance password.
3. Opaque URLs: `/share/<token>/…` where the token is a high-entropy secret minted per share, never the real session id or bookmark id. Real ids must not be derivable from the URL (they may still appear *inside* transcript content — see §17).
4. Multiple independent links per shared object (one per recipient if desired), each with its own label, optional password, expiration, and individual revocation.
5. Optional per-link password, distinct from the instance password.
6. Snapshot vs live: a session share can be frozen at its creation point or follow the session as it grows (live). An artifact share always serves a frozen copy taken at creation; the owner propagates updates explicitly (§9.2), so viewers can never catch a half-edited artifact.
7. Owner-side management: create/edit/revoke from the session header and from artifact bookmarks, a global list, view counters, last-viewed timestamps, optional "someone viewed your share" external notification.
8. A mandatory dedicated share **origin**: sharing works only through a separate hostname (configured in Settings → Sharing, `shareBaseUrl`) that serves *only* `/share/…`. The working origin never serves `/share/`. No share host configured ⇒ sharing is disabled (§12).
9. Full CLI/RPC story consistent with house conventions (full lifecycle for humans; sharing is not exposed to agents — no skill, no MCP tools — §14).

**Non-goals**
- Multi-user accounts, per-viewer identity, or write access for viewers. TwiCC remains single-user; viewers are anonymous bearers of a capability URL.
- Public *discovery* (no server-side index of shares; each link is secret). The share host's `/share/` homepage lists only links *this browser already opened* (from its own `localStorage`), so it reveals nothing the viewer didn't already hold — not discovery.
- Editing/commenting by viewers.
- Sharing a whole project or a live-follow of *future* sessions (a share targets one existing object; nothing prevents creating many).

## 3. Threat model and access model

### 3.1 What a share link is

A **capability URL**: possession of the token grants exactly the scoped read access of that share, nothing else. This is the industry-standard model (Google Docs "anyone with the link", GitHub secret gists). Entropy: `secrets.token_urlsafe(32)` = 256 bits — brute force is not a realistic vector; the realistic vectors are *leakage* (forwarded email, chat logs, browser history, referer) and *over-sharing* (recipient passes it on). Mitigations offered per link: password, expiration, revocation, per-recipient links (leak attribution + selective revocation).

### 3.2 What a viewer must never be able to do

- Reach any non-`/share/` route with any ambient credential. The share pages never receive the owner's session cookie semantics; nothing on them calls `/api/…` or `/rpc/…`.
- Enumerate or derive other shares, sessions, projects, bookmarks.
- Mutate anything — with one deliberate exception under O5 (owner-side CLI create), which is owner-side, not viewer-side.
- Grant network-broker consent for a shared artifact (§9.3): the "clicker is the authority" premise of the broker design (`2026-06-18-artifact-network-broker-design.md` §3.3) is *invalid* for anonymous viewers. Viewer-side consent does not exist in any variant of this design.

### 3.3 What the owner accepts when creating a share

The transcript is shown *as is*: file paths, commands, code, env values echoed by tools, the machine's directory layout. No redaction layer (rejected, §16). The creation dialog states this plainly. `max_display_mode` (§5.2) bounds *which items* are visible (debug mode, which exposes raw JSON, is opt-in), not what visible items contain.

### 3.4 Relationship to the instance password and the local-only gate

Share routes authenticate by token, so they are independent of the instance password. **Resolved (O1):** `/share/…` is exempt from `remote_access_blocked()` — share links work even when no instance password is set. Rationale: the gate exists because *nothing* authenticates an unprotected instance; a share token *is* an authenticator, deliberately minted. An owner who tunnels a passwordless instance exposes only what they explicitly shared.

## 4. Concepts

- **Share** — one row = one capability URL targeting one object (session or artifact bookmark), with its own options, protection, lifecycle, and counters.
- **Per-recipient links** — not a separate concept: create several shares of the same target, label them ("Alice", "team channel"), revoke individually. The model imposes no uniqueness on targets.
- **Snapshot vs live** — sessions have a per-share `mode`: snapshot pins `frozen_at_line`; live follows growth. Artifacts are always snapshots: the served directory is copied into the data dir at creation, and the owner explicitly propagates updates (atomic re-copy) when ready (§9.2).
- **Share origin** — the base URL the owner distributes, **required** (`shareBaseUrl` synced setting). It must be a hostname distinct from the working origin; the share surface is served *only* there and the working origin 404s `/share/` (§12). No share origin configured ⇒ no sharing.

## 5. Data model

New models in `src/twicc/core/models.py`, migration `0122_share.py`.

### 5.1 `Share`

| Field | Type | Notes |
|---|---|---|
| `id` | `CharField(16)` PK | `"shr_" + secrets.token_hex(4)` — non-secret admin handle (CLI, logs, UI) |
| `token` | `CharField(64)` unique, indexed | URL secret, `secrets.token_urlsafe(32)`. Stored in plaintext (O2 resolved: URLs must stay re-copyable from the UI; the DB sits on the owner's disk where an attacker already has everything) |
| `kind` | `CharField(16)` | `ShareKind`: `session` \| `artifact` |
| `session` | FK `Session`, null, CASCADE | set iff `kind=session` |
| `artifact_bookmark` | FK `ArtifactBookmark`, null, CASCADE | set iff `kind=artifact` |
| `label` | `CharField(255)` | owner-private ("for Alice"); never sent to viewers |
| `password_hash` | `CharField(255)`, blank | per-link password, same PBKDF2 format as `auth/hashers.py`; empty = none |
| `expires_at` | `DateTimeField`, null | checked at request time; no cleanup task needed |
| `revoked_at` | `DateTimeField`, null | revoke = keep row + counters; delete = remove row (both offered) |
| `options` | `JSONField` | kind-specific, §5.2 |
| `view_count` | `IntegerField` default 0 | page views only (not assets/API ranges), flushed in batches (§13) |
| `last_viewed_at` | `DateTimeField`, null | |
| `notify_on_view` | `BooleanField` default False | external notification via the existing Apprise integration (§13) |
| `created_at` / `updated_at` | | |

`CheckConstraint`: exactly one of `session` / `artifact_bookmark` set, matching `kind`. A DB constraint, not just serializer validation.

CASCADE means deleting a bookmark (or a session row disappearing) kills its shares — correct: the capability must not outlive its target. (Sessions are never deleted by TwiCC; bookmarks are user-deletable and the bookmark dialog must warn when shares exist.)

### 5.2 `options` per kind

**session**: `mode` (`"snapshot"`|`"live"`), `frozen_at_line` (int; set to `session.last_line` at creation when snapshot), `max_display_mode` (`"conversation"`|`"simplified"`|`"normal"`|`"debug"`; viewer may pick any mode ≤ this; default `"normal"` — `debug` exposes raw JSON and is opt-in), `include_subagents` (bool, default true; gates the in-page subagent overlay §8.6 and the subagent API routes), `show_costs` (bool, default false), `show_timestamps` (bool, default true), `show_title` (bool, default true; false ⇒ viewer sees "Shared session"), `display_title` (str, optional; owner-set public title — **overrides** the real session title, and shows even when `show_title` is off; empty ⇒ use the real title per `show_title`).

**artifact**: `snapshot_at` (ISO timestamp of the served copy — set at creation and on every explicit propagation; the copy lives at `<data_dir>/shares/<share_id>/`, removed on share delete), `display_title` (str, optional; owner-set public title — overrides the real bookmark name, the default title for the share page + recent-views list). Network policy is not an option: every artifact share uses the owner-allowlist broker behaviour (§9.3, decision D6).

Options are owner-editable after creation (except `frozen_at_line`, which can be "re-frozen to now" via a dedicated action; `snapshot_at` changes only through the propagate action, §9.2).

### 5.3 `ShareAccess`

Lightweight access log: `share` FK CASCADE, `at`, `ip` (`CharField(64)`), `user_agent` (`CharField(255)`, truncated). One row per *page view* (§13 defines a view). Opportunistic pruning on insert: keep the newest 500 rows per share. Powers the per-share "recent visits" list in the management UI; `view_count`/`last_viewed_at` stay denormalised on `Share` for cheap list display.

### 5.4 Serializers

`serialize_share(share)` (owner-facing, in `core/serializers.py`): all fields + computed `url_path` (`/share/<token>/`) + target summary (session title/project or bookmark name) + `status` (`active`/`revoked`/`expired`). For artifact shares it also computes `source_updated_at` (max mtime under the bookmark's live directory) so the UI can flag the share as outdated vs `snapshot_at` (§9.2).
`serialize_share_public_meta(share)` (viewer-facing, §6.2): strictly the fields the share page needs — never label, never counters, never real ids… with one caveat: media URL rewriting (§8.5) requires the viewer bundle to recognise the shared session's artifacts paths, so `meta` includes the session id when `kind=session`. The session id also appears throughout raw item content; it is not treated as a secret (it grants nothing — every real route is password-gated). Real *bookmark* ids and project ids are never sent.

## 6. URL space and backend routes

All viewer-facing routes live under one flat prefix keyed by token — the kind is resolved by lookup, keeping URLs short and shape-uniform (`/share/session/<t>` vs `/share/artifacts/<t>` rejected, §16):

```
/share/<token>/                     page (session viewer HTML, or artifact shell/file)
/share/<token>/…                    kind-specific sub-paths below
/_twicc/share/<asset>               built share-viewer JS/CSS (public, no data)
```

`token` matches `[A-Za-z0-9_-]{20,}`; the literal next segments (`api`, `media`, assets) can never collide with it.

### 6.1 Common behaviour

- Token lookup → constant-time compare (fetch by indexed token then `hmac.compare_digest`). Unknown, revoked, or expired ⇒ **the same** minimal 404 page/JSON ("This link is not available.") — no oracle distinguishing never-existed from revoked.
- Per-link password (§7.2) checked before anything else when set.
- Every `/share/` response carries `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- No `/share/` response ever sets or reads the owner's auth markers; the Django session is used only for the per-link password grant (§7.2).

### 6.2 Session share routes

| Route | Returns |
|---|---|
| `GET /share/<t>/` | viewer HTML page (data island: `{tokenPath, meta}`) |
| `GET /share/<t>/api/meta/` | `serialize_share_public_meta`: provider, title (`display_title` override, else the real session title per `show_title`), created/last timestamps, costs (per `show_costs`), `last_line` (clamped to `frozen_at_line` when snapshot), options the viewer needs (`max_display_mode`, `include_subagents`, `show_timestamps`, `mode`), session id (§5.4 caveat) |
| `GET /share/<t>/api/items/metadata/` | same shape as `session_items_metadata`, **filtered**: `line_num ≤ frozen_at_line` when snapshot; items with `display_level > max_display_mode`'s ceiling excluded at the source (`DEBUG_ONLY` rows never leave the server unless `max_display_mode="debug"`) |
| `GET /share/<t>/api/items/?range=…` | same contract as `session_items` (range required, repeatable), same filters |
| `GET /share/<t>/api/items/<line>/tool-results/<tool_id>/` | tool_result rows via `ToolResultLink`, same filters |
| `GET /share/<t>/api/subagents/` | only if `include_subagents` |
| `GET /share/<t>/api/subagent/<sid>/items/metadata/` etc. | subagent variants of metadata/items/tool-results; `<sid>` must be a descendant of the shared session (walk `parent_session`/`spawn_root`, bounded hops) |
| `GET /share/<t>/media/<filename>` | inline artifact images referenced by the transcript (`![](/artifacts/<sid>/<file>)`): reuse `session_artifact`'s filename regex + extension allowlist, confined to the shared session's artifacts dir |

Server-side display-level filtering (not client-side) is a hard rule: content above the allowed ceiling must never reach the viewer's network tab.

### 6.3 Artifact share routes

| Route | Returns |
|---|---|
| `GET /share/<t>/` | HTML bookmark ⇒ share shell page (share variant of `artifact_shell_response`); other types ⇒ the file directly (`_serve_artifact_file`) |
| `GET /share/<t>/api/meta/` | `{snapshot_at}` — powers the viewer update banner (§9.2) |
| `GET /share/<t>/__twicc_doc__` | the artifact HTML wrapped with shim + CSP (`artifact_html_response`) |
| `GET /share/<t>/<path:asset>` | sibling assets, path-confined against the snapshot dir (§9.2) |
| `POST /share/<t>/api/proxy/` | broker proxy with **server-side enforcement of the owner's allowlist** (§9.3) |

### 6.4 Auth wiring

`PasswordAuthMiddleware`: add `"/share/"` and `"/_twicc/share/"` to `PUBLIC_PATHS`. No further instance-level gate (O1).

## 7. Per-link protection

### 7.1 Levels

1. Token only (default).
2. Token + per-link password: the owner sets a password on the share; the viewer lands on a minimal standalone password page (same pattern as `artifact_auth`: plain HTML form, no SPA, shared rate limiter `_login_attempts` keyed by IP).

### 7.2 Password grant persistence

On success, store in the *viewer's* Django session: `share_grants: {<share_id>: <fingerprint>}` where fingerprint = `sha256(share.password_hash)[:16]` — same rotate-to-invalidate trick as the instance auth. This session key is disjoint from `SESSION_AUTH_KEY`; a viewer session carrying grants has no instance authentication. Changing the share password invalidates all grants for it instantly.

## 8. Shared session page — frontend architecture

### 8.1 Delivery

New standalone bundle `frontend/src/share-session/` (entry `main.js`, root `ShareSessionApp.vue`), built by `vite.config.share.js` (lib mode, fixed `share-session.js`/`share-session.css`, outDir `src/twicc/static/share-session/`, `base: '/_twicc/share/'`), chained into the `package.json` `build` script. Served by a public `views.share_asset` mirroring `artifact_shell_asset`. The page HTML is produced server-side (like `artifact_shell_response`) with a JSON data island `{tokenPath, meta}` — no auth store, no router, no global WS.

### 8.2 Reused verbatim

`VirtualScroller.vue` + `useVirtualScroll.js`, `SessionItem.vue`, `ContentList.vue`, provider item components, `ToolUseContent.vue`, all per-tool renderers, `GroupToggle.vue`, `DaySeparator.vue`, `MarkdownContent.vue`, media thumbnails + `MediaPreviewDialog.vue`/`GlobalMediaPreview.vue`, `utils/{parsedContent,visualItems,markdown,mermaid}.js`, provider `toolHelpers`. Rendering must be pixel-identical to the real UI, including collapsible groups and the display-mode semantics — that is why `computeVisualItems` runs client-side in the share bundle too, over the (server-filtered) items.

### 8.3 Store shims via Vite aliases

The reused components import `stores/data.js` (≈5500 lines), `stores/settings.js`, `stores/codeComments.js` directly. The share Vite config aliases those module paths to shims in `frontend/src/share-session/shims/`:

- `dataStoreShim.js` — a Pinia store exposing exactly the read surface the transcript tree touches (`getSession`, `getProject` (stub), `getSessionItems`, `getSessionVisualItems`, `recomputeVisualItems`, `getExpandedGroups`/`toggleExpandedGroup`, detail-toggle state, `getToolState`, `getAgentLink`, `getWorkflowLink` (returns `undefined` ⇒ buttons hidden), `getProcessState` (null), `getPendingRequests` (empty), `loadSessionItemsRanges` (fetches `/share/<t>/api/items/`)). The exact surface is enumerated in the implementation plan; the shim throws on anything else so drift is caught in dev, not silently.
- `settingsStoreShim.js` — viewer-local reactive settings: `displayMode` (viewer-selectable ≤ `max_display_mode`), `showDiffs: false`, effective color scheme (viewer toggle, defaults to `prefers-color-scheme`), `waTheme`/`waBrand` fixed to defaults, `areMessageTimestampsShown` (from share options, viewer-toggleable off), `areCostsShown` (from share options).
- `codeCommentsShim.js` — empty counts, no-ops.

This keeps every reused component byte-identical. The alternative (prop-drilling a store interface) would fork the transcript tree and is rejected (§16).

### 8.4 Navigation neutralisation

All of these are disabled by *not providing* the injection (components already `inject(..., null)` and hide the affordance): `viewFileInFilesTab` (kills "View in Files" on Read/Write/Edit and Codex `ApplyPatchFileEntry`), `markdownFileLinks` (kills file-link classification/opening in `MarkdownContent`), `insertTextAtCursor`, `codeCommentToolContext`. "View Workflow" hides because `getWorkflowLink` returns `undefined`; "Stop Agent" hides because `getProcessState` is null and `sessionActive` is not provided. "View Agent" is the one affordance *kept*, rewired (§8.6).

Two real code changes in shared components (both benign for the SPA):
1. `MarkdownContent.vue` — guard the router fallback branch: `const router = useRouter()` is undefined in a router-less app; before `router.push(href)`, if no router: absolute `http(s)` hrefs open via `window.open(href, "_blank", "noopener,noreferrer")`, anything else is inert.
2. `ToolUseContent.vue` — "View Agent" navigation goes through a new injectable (`openSubagent`, default = current `router.push` behaviour) so the share host can supply the overlay behaviour instead. (Exact seam confirmed at implementation; if the current code already funnels through one method, the injectable replaces its body.)

### 8.5 Media and links inside content

- User-message images are base64 inside item content — render as-is.
- Markdown/artifact images `/artifacts/<sid>/<file>`: a share-mode hook in the markdown pipeline (and thumbnail URL resolution) rewrites `sid == shared session` URLs to `/share/<t>/media/<file>`; any other `/artifacts/…` URL renders as a broken-media placeholder (it is *not* shared).
- File-path links: plain inert text styling (no classification — no roots to classify against).
- External `http(s)` links: clickable, `target="_blank"` + `noopener noreferrer` (the page sends no referrer anyway).

### 8.6 Subagents (when `include_subagents`)

"View Agent" opens an in-page overlay (`SharedSubagentView`: full-height drawer) hosting a second instance of the same transcript stack pointed at `/share/<t>/api/subagent/<sid>/…`. No router, arbitrary nesting depth (a subagent's own "View Agent" buttons work the same way), breadcrumb trail in the drawer header. When `include_subagents=false`, the injectable is not provided ⇒ button hidden, API routes 404.

### 8.7 Page chrome

Static header: title (`meta.title` — the resolved display title: `display_title` override else the real title per `show_title`, §6.2), provider icon, created/updated dates, cost badge (per `show_costs`), "Read-only shared transcript" tag; viewer controls: display mode (bounded), timestamps toggle, light/dark toggle; discreet "Shared with TwiCC" footer. No composer, no pending-request forms, no goal/hybrid blocks (the whole `SessionItemsList` footer stack is simply not part of `ShareSessionApp`). A slim `ShareSessionItemsList` re-implements the thin data-loading/scroll glue of `SessionItemsList.vue` against the shim store (the heavy parts — scroller, items — are the reused components). Live-mode indicator when `mode="live"` (§10). If the share dies mid-view (revoked/expired ⇒ API 404), a banner replaces the live indicator: "This share is no longer available." with content left on screen but no further loads.

Print stylesheet: hide chrome, expand nothing automatically (WYSIWYG print of the current state).

### 8.8 Theming/CSS

Re-run `initTheme()`-equivalent minimal logic at bundle start (theme before CSS to avoid flashes). Import Web Awesome base + default theme CSS, `github-markdown.css` + theme overrides (come with `MarkdownContent`), and the custom properties the transcript CSS expects (`--user-card-base-color`, `--assistant-card-base-color`, `--main-shadow-size` — defined in `App.vue` (:790-791, :852); `--card-spacing`/`--max-card-width` live in `SessionItem.vue`'s unscoped block (:433-434) and ship with the component; extract the `App.vue` trio to a small shared `transcript-tokens.css` imported by both the SPA and the share bundle so they can't drift). `SessionItem.vue`'s unscoped CSS ships automatically with the component.

## 9. Shared artifact page

### 9.1 Serving

Mirrors `/artifacts/<id>/` exactly, with the share token as the key and the snapshot copy as the base directory (§9.2). Non-HTML bookmarks stream directly (images/PDF/audio/video/markdown get the browser's native or raw rendering — same as today's dedicated page behaviour). HTML gets the trusted shell + CSP-wrapped inner doc.

**Markdown/Mermaid bookmarks:** today's `/artifacts/<id>/` serves raw text for these. For sharing, that is a poor viewer experience; the share shell also handles `md`/`mmd` bookmarks by rendering them with `MarkdownContent`/`MermaidDiagram` inside the share-session bundle's machinery (a tiny `ShareDocView` mode of the same bundle — cheap, since markdown+mermaid utils are already in it). This is a deliberate *improvement over* the internal dedicated page.

### 9.2 Snapshot + explicit propagation (decision D7)

At creation: copy the bookmark's *directory* (the file's parent dir — the sibling-asset universe) to `<data_dir>/shares/<share_id>/`, recursively, with a size guard (sum > 200 MB ⇒ refuse with explicit error; the dialog shows computed size beforehand). Serving always confines paths to the snapshot dir; **the live files are never served to viewers**. The owner keeps editing the real artifact freely; viewers keep a consistent copy and can never catch a half-edited state mid-update.

**Propagation.** Updates are pushed explicitly by the owner: `serialize_share` computes `source_updated_at` (max mtime under the live directory); when it exceeds `snapshot_at`, the management UI shows an **outdated** badge and a **Propagate update** action → re-copy (copy to `<dir>.tmp`, atomic swap, set `snapshot_at = now`). The existing artifacts watcher signal (`artifact_files_changed`) lets the owner UI refresh the badge live while files change.

**Viewer refresh signal.** The share page (HTML shell and doc view alike) polls `GET /share/<t>/api/meta/` lightly (30 s, visible tab only); when `snapshot_at` changes, a banner offers "This artifact was updated — Reload".

Deleting the share deletes the copy. Revoking keeps it (revoke is reversible; delete is not).

### 9.3 Network broker policy (decision D6)

Anonymous viewers must never grant consent, and one viewer's grant must never affect others or the owner (`allowed_hosts` is a shared mutable row). Single behaviour, no per-share option — the goal is a *functional* shared artifact:

`POST /share/<t>/api/proxy/` accepts fetches whose normalised `scheme://host:port` is in `bookmark.allowed_hosts` **at request time, checked server-side** — unlike the owner-side proxy, which deliberately doesn't re-check (that decision, design §6.4 of the broker doc, is premised on the caller being the owner's trusted first-party page; a share viewer is neither, so the premise — not the decision — changes). Metadata IP hard-block and IP pinning reused as-is. No preflight/consent flow is exposed; unlisted hosts ⇒ 403, surfaced by the shim as a failed fetch. The share shell mounts a *prompt-less* host variant: forwards allowed calls, never renders `ArtifactBrokerPrompt`.

The owner's mental model: "every host I've already allowed *Forever* on this artifact keeps working for viewers, out of the box; nothing else does, and viewers can't grant anything." The creation dialog lists the artifact's current allowed hosts so the owner knows what viewers will reach.

## 10. Live updates (session shares, `mode="live"`)

The viewer needs: new items for the shared session, updated item metadata, session meta refresh (title/cost). **Resolved (O4): the dedicated WS consumer ships; polling was designed and rejected** (kept below for the record):

- **O4-a WS (retained):** new `ShareConsumer` at `ws/share/<token>/` (raw addition to `websocket_urlpatterns`). On connect: token check (+ password grant check via the scope's session), then join the global `updates` group but **filter server-side per message**: forward only `session_items_added` / `session_updated` whose `session_id` (or session id) matches the shared session or (if `include_subagents`) a cached descendant set; re-serialize `session_updated` through the *public* meta serializer and re-filter items against display-level/frozen-line rules before sending. The viewer never sees the firehose — filtering happens in the consumer, not the client. Cost: every live-share connection processes group traffic; fine at this scale (single user, few viewers), noted as a caveat.
- **O4-b polling (rejected):** `GET /share/<t>/api/updates/?since=<line>&meta_version=<hash>` returning `{last_line, meta?}`; the client then range-fetches new lines. 4 s interval while the tab is visible (Page Visibility API), no new WS surface, tunnel-proof. Latency and burst-cost slightly worse; no streaming granularity difference (streaming deltas are ephemeral non-DB messages in both variants — viewers see finalized lines only, which is accepted and documented).

Snapshot shares never poll/connect. Frozen line is enforced server-side either way.

## 11. Management UI (owner side)

- **Pinia store** `stores/shares.js`: list from `GET /api/shares/`, kept in sync via new WS broadcasts `share_updated` / `share_removed` (owner WS, existing firehose — fine). Seeded via bootstrap like bookmarks.
- **REST (owner, password-gated):** `GET/POST /api/shares/`, `GET/PATCH/DELETE /api/shares/<share_id>/`, `POST /api/shares/<share_id>/revoke/` (and `unrevoke`), `POST /api/shares/<share_id>/propagate/` (artifact re-snapshot / session re-freeze), `GET /api/shares/<share_id>/accesses/`. All mutations through `core/services/share_mutation.py`.
- **ShareDialog** (create/edit): target summary, label (private), public title (input placeholder = the real session title / bookmark name; empty ⇒ that default, else the owner's override → `options.display_title`), kind-specific options (§5.2), password set/clear, expiration picker, `notify_on_view`; on create shows the full URL with copy button (built from the required `shareBaseUrl` setting — §12). Share creation is gated on `shareBaseUrl` being set: the Share entry points are disabled with a hint linking to Settings → Sharing when it is empty, and the create endpoint refuses (400) as a backstop. Warns about transcript exposure (§3.3). Reference implementation pattern: `ProjectEditDialog.vue` (per CLAUDE.md).
- **ShareListPanel** (reusable list: URL copy, status chip active/revoked/expired, views count, last view, quick revoke/edit/delete; for artifact shares an **outdated** badge + **Propagate update** action, §9.2): embedded in three places — session sharing popover, artifact bookmark sharing popover, and the global manager.
- **Entry points:** a Share button in `SessionHeader.vue` (with a "shared" badge state when ≥1 active share); a Share action on artifact bookmarks (in `ArtifactBookmarkList.vue` rows, the bookmark dialog, and `FilePane`'s bookmark affordance — share requires a bookmark; the flow offers to create the bookmark first when missing); a global "Shares" manager listing everything (placement: a dedicated dialog opened from `SettingsPopover.vue`, plus the same component reachable from the command palette — decided, no question).
- **Badges:** session rows/header and bookmark rows show a small share indicator when actively shared (cheap: `shares.js` indexes by target).
- **Bookmark deletion guard:** deleting a bookmark with active shares prompts with the count (CASCADE kills them).
- New synced setting `shareBaseUrl` in a dedicated **Sharing** section of the Settings panel, with an Apply button (mirrors `publicBaseUrl`). Client-side validation rejects a value whose hostname equals the app's own hostname (§12).

## 12. Dedicated share origin (mandatory)

Sharing is served **only** on a dedicated hostname, distinct from the working origin. This is a hard precondition, not defence-in-depth: no share host configured ⇒ no sharing. The rationale is origin isolation — the public share surface must never share an origin with the authenticated working app. It must be a different **hostname**, not just a different port: cookies are not port-scoped (RFC 6265), so `host:appPort` and `host:sharePort` would still share the working session cookie; only a distinct hostname isolates it. Typical setup: a second tunnel hostname pointing at the **same** local port.

- **Source of truth — the `shareBaseUrl` synced setting** (Settings → Sharing, Apply button). Its hostname is both what builds share URLs and what the routing gate matches against. Client-side validation rejects a value whose hostname equals the app's own hostname.
- **Gate (`share/asgi_filter.py`, always installed):** reads `shareBaseUrl` **live** per request (the way `external_notifications.py` reads `publicBaseUrl`):
  - request `Host` == share host → `ShareOnlyApp`: only `/share/…` + the public share/artifact-shell assets (`/_twicc/share/…`, `/_twicc/artifact-shell/…`, the broker shim, `/favicon.ico`→204); everything else 404; WS allowed only for `ws/share/…` (O4-a).
  - request `Host` != share host (the working origin, or anything else) → the full app, but `/share/…` and `/_twicc/share/…` → 404, and `ws/share/…` closed.
  - `shareBaseUrl` empty → `/share/…` 404s everywhere (sharing disabled).
- Wrapped above BlackNoise so the share host never reaches the `/static/` mount it doesn't use. `/mcp` (pre-Django `http_router`) is never in the allow-list ⇒ unreachable on the share host. Host-header trust is safe: spoofing a Host gets you *less* (only `/share/`) or a 404, never more.
- No dedicated port and no env var: a single Host-header gate on the one listener. The operator points a second tunnel hostname at the same local port and sets it in Settings → Sharing.

**Share host homepage (`/share/`, no token).** The share host's root serves a small client-side page listing the shares *this browser* has opened — read from `localStorage` on the share origin (isolated by origin), never from the server (no per-viewer tracking exists, by design §13). Each share page records `{token, kind, title, lastAccess}` on view; the homepage renders them as links to `/share/<token>/`, with a per-row remove affordance (and lazily prunes entries that resolve to 404). The `/share/` prefix is kept (rather than root-level tokens) to keep `/_twicc/…` cleanly separable and leave room for other share-host surfaces later.

## 13. View tracking and notifications

- A **view** = a successful GET of the share *page* (`/share/<t>/` root), not assets/API/media. Counted after password check.
- Writes are batched: in-memory accumulator flushed to DB (update `view_count`/`last_viewed_at`, insert `ShareAccess` rows) every 30 s or on shutdown — same coalescing philosophy as `tokens.note_used`/`start_last_used_flush_task`, avoiding a DB write per hit. Flush task started in `run_server()`.
- `notify_on_view` uses the existing external-notifications (Apprise) pipeline: event "Share viewed" with the share label + target title, throttled per share (first view always; then at most one notification per hour, counting suppressed views in the next message). WS `share_updated` broadcast on flush keeps owner UI counters live.

## 14. CLI, RPC (human-only)

**Resolved (O5): full CLI lifecycle for humans; sharing is not exposed to agents at all — no skill, no MCP tools.**

- CLI ships the complete verb set: `twicc share list` / `show <shr_id>` (read-only, direct DB, print full URLs) plus `create session <id> [--live|--frozen] [--label] [--password] [--expires] …`, `create artifact <bookmark_id>`, `update`, `revoke`, `unrevoke`, `delete`, `propagate` via drop-requests (`share:*` kinds in `_KIND_HANDLERS` → `share_mutation.py`), auto-exposed over `/rpc/` like every command. Read-only-mode sessions can't reach the write path regardless.
- **No agent surface.** There is no `twicc-share` skill, and no MCP tool for sharing — the whole `share` command root is excluded from the MCP registry (`MCP_EXCLUDED_ROOTS`). Publishing owner content to the network is a human decision (same spirit as the broker's human-consent boundary): a human at a terminal has the full CLI; agents are never pointed at it.
- New `## Sharing` section in `SKILLS-AND-CLI.md` documenting the full CLI (repo doc for humans). No plugin/skill change.

## 15. Security hardening summary

- 256-bit tokens, constant-time comparison, uniform 404 (no revoked/never-existed oracle), `no-store`, `noindex`, `Referrer-Policy: no-referrer`.
- Server-side content filtering (display level ceiling, frozen line, subagent descendant check with bounded parent-chain walk).
- Per-link password on the shared IP rate limiter; grants fingerprint-bound to the password hash (rotation invalidates).
- Share pages carry zero owner credentials and call only `/share/<t>/…`.
- Broker: no viewer consent path exists; share proxy enforces the allowlist server-side; metadata IP block and IP pinning inherited.
- Artifact path confinement inherited (`confined_artifact_path`) for live and snapshot roots.
- Token brute force is not rate-limited (entropy makes it moot); noted as accepted (§17).

## 16. Alternatives considered and rejected

- **Kind in the URL** (`/share/session/<t>`): adds nothing (kind is derivable from the row), makes URLs longer, adds a mismatch failure mode. Rejected for a flat `/share/<t>/`.
- **Reusing real ids in share URLs**: rejected outright (user requirement; enumeration + linkability).
- **Signed URLs (HMAC of id+expiry, stateless)**: no revocation without a denylist table — which is just a worse share table. Rejected.
- **Redaction layer for transcripts** (masking paths/secrets): pattern-based redaction over arbitrary tool output is unwinnable and breeds false confidence; the honest contract is "you share the transcript, you share its contents". Rejected (creation-dialog warning instead).
- **Prop-drilling a store interface through the transcript tree** instead of Vite alias shims: forks ~30 components' props for one consumer. Rejected.
- **Reusing the owner WS consumer with a type filter for viewers**: the firehose carries instance-wide data; a subscription filter is a client-side courtesy, not an authorization boundary. Rejected in favour of a dedicated consumer (O4-a) or polling (O4-b).
- **Static HTML export as the snapshot mechanism** (bake items into the page): duplicates the rendering pipeline's data contract, breaks range-loading for huge sessions, diverges from live mode. Rejected — snapshot is a *server-side line clamp*, one code path for both modes. (A future "download as standalone HTML" could reuse the share bundle, noted as an idea, not designed.)
- **Per-viewer accounts/ACLs**: contradicts TwiCC's single-user model; per-recipient *links* deliver the useful part (attribution, selective revocation). Rejected.
- **iframe-embedding the SPA in read-only mode**: the SPA assumes full auth + full stores + router; guaranteeing non-navigation inside it is a negative-space proof. Rejected for the standalone bundle.

## 17. Caveats and accepted limitations

- **Content is the content**: real session ids, absolute paths, command output, and anything the agent saw appear in shared transcripts. Mitigation is informed consent (dialog warning), not redaction. The session id in `/share/…/api/meta/` grants no access by itself.
- **The share title is shown to viewers**: by default the real session title / bookmark name (owner-authored) is the public title — for artifacts this is a deliberate, small disclosure beyond the old kind-only meta. The owner can override it per share (`display_title`) or, for sessions, hide it entirely (`show_title: false`).
- **Live shares reveal activity timing** (viewer sees when the owner works). Snapshot avoids this.
- **In-flight streaming is invisible to viewers** (stream deltas are not DB rows); new content appears line-by-line as persisted. Accepted.
- **O4-a cost**: each live viewer connection filters the global group's traffic server-side. Single-user scale makes this negligible; a per-session group would be the fix if it ever isn't.
- **Snapshot artifact copies consume disk** (bounded by the 200 MB guard, surfaced in UI).
- **A shared HTML artifact lets anonymous viewers consume the owner's allowed upstreams** (e.g. hammer an API the artifact calls through the proxy): sharing a network-dependent artifact implies it (D6). The creation dialog lists the hosts viewers will reach. Accepted.
- **InMemoryChannelLayer / single process**: all of this (flush tasks, consumers, the share-host gate reading live settings) assumes the existing single-process model. Fine today; multi-process would revisit far more than sharing.
- **No token rotation on a live share** (rotate = revoke + create new link). Accepted; per-recipient links make this cheap.

## 18. Decisions (resolved 2026-07-05)

| # | Question | Decision |
|---|---|---|
| **O1** | Share links without an instance password? | **Yes** — the token is the credential; `/share/` is exempt from the local-only remote gate (§3.4) |
| **O2** | Token storage | **Plaintext** — URLs stay re-copyable from the UI; hash-only buys nothing on an owner-local DB (§5.1) |
| **O3** | Dedicated share origin | **Mandatory, host-only, settings-driven** (§12). Sharing is served only on the `shareBaseUrl` hostname (a distinct hostname pointing at the same port); the working origin never serves `/share/`; no share host ⇒ no sharing. A single Host-header gate that reads the setting live — no dedicated port, no env var |
| **O4** | Live updates transport | **Dedicated filtered WS consumer** (§10); polling variant designed and rejected |
| **O5** | Agent access to sharing | **Full CLI lifecycle for humans**; sharing is **not exposed to agents** — no skill, no MCP tool (the `share` root is excluded from the MCP registry) (§14) |
| **D6** | Shared-artifact network policy | Single behaviour, no option: never any viewer consent prompt; the owner's "Forever" grants are honored server-side so the artifact is functional out of the box; viewers can't grant anything (§9.3) |
| **D7** | Shared-artifact freshness | Always a snapshot copy + **explicit owner propagation** (outdated badge + Propagate button, atomic swap); viewers get a light "updated — reload" banner. No live serving of the owner's working files (§9.2) |

Everything else in this document is decided in place (flat URLs; data model; server-side filtering; snapshot semantics; management UI placement; view tracking; notifications; per-link passwords; per-recipient links as N rows).

## 19. File map

| Concern | Location |
|---|---|
| Auth middleware / public paths | `src/twicc/auth/middleware.py:31` |
| Local-only gate | `src/twicc/auth/local_access.py` |
| Password hashing / rate limiter / standalone auth page | `src/twicc/auth/hashers.py`, `src/twicc/auth/views.py` (`_login_attempts`, `artifact_auth`) |
| Token precedent | `src/twicc/auth/tokens.py` |
| Models / migrations | `src/twicc/core/models.py`, `src/twicc/core/migrations/` (latest: `0121_session_plan_paths.py`) |
| Serializers | `src/twicc/core/serializers.py` |
| Service-module convention | `src/twicc/core/services/artifact_bookmark_mutation.py` |
| Items endpoints (contract to mirror) | `src/twicc/views.py` (`session_items`, `session_items_metadata`, `tool_results`), `src/twicc/urls.py` |
| Artifact serving / shell / CSP / shim | `src/twicc/views.py:3581` (`artifact_serve`), `src/twicc/artifacts/broker_html.py`, `src/twicc/views.py:2037` (`_serve_artifact_file`), `session_artifact` (`views.py:3337`) |
| Broker proxy | `src/twicc/artifacts/proxy.py` |
| ASGI composition / WS consumer / firehose | `src/twicc/asgi.py` (`WSConsumer.connect` :410, `websocket_urlpatterns`, ProtocolTypeRouter ~:1991) |
| Server launch (view-flush task) | `src/twicc/cli/run.py:171` (`run_server`), `:243` (uvicorn Config) |
| Share-host gate wiring | `src/twicc/asgi.py` (after `application = BlackNoise(...)`) |
| Watcher broadcasts | `src/twicc/providers/sessions_watcher.py` |
| Drop-request dispatch | `src/twicc/drop_requests_watcher.py` (`_KIND_HANDLERS`) |
| Transcript component tree | `frontend/src/components/session/detail/` (`SessionItemsList.vue`, `SessionItem.vue`, `items/ToolUseContent.vue`, …), `frontend/src/components/virtual-scroller/` |
| Visual items / parsed content / markdown | `frontend/src/utils/{visualItems,parsedContent,markdown,mermaid}.js` |
| Markdown link handling | `frontend/src/components/ui/MarkdownContent.vue`, `frontend/src/utils/fileLinks.js` |
| Standalone bundle precedent | `frontend/src/artifact-shell/`, `frontend/vite.config.shell.js`, `views.artifact_shell_asset` |
| Broker host/shim/composable/prompt | `frontend/src/artifact-broker/`, `frontend/src/composables/useArtifactBroker.js`, `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` |
| Dialog form reference | `frontend/src/components/project/ProjectEditDialog.vue` |
| Settings panel (shortcuts section rule) | `frontend/src/components/app/SettingsPopover.vue` |
| External notifications | Apprise integration (`docs/plans/2026-06-11-external-notifications-apprise-design.md`, `publicBaseUrl` precedent) |
| CLI + skills conventions | `src/twicc/cli/artifacts.py`, `src/twicc/agent/plugin/twicc/skills/twicc-artifacts/SKILL.md`, `SKILLS-AND-CLI.md`, plugin `plugin.json` version rule |

## 20. Implementation

See `2026-07-05-sharing-implementation-plan.md` — phased, file-by-file, consolidated on the resolved decisions (§18).
