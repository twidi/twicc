# External Notifications via Apprise — Design

**Date:** 2026-06-11
**Status:** Designed, not implemented

## 1. Goal and context

On mobile, browser notifications die as soon as the browser tab is backgrounded: the page
is frozen, the WebSocket drops, and the two notification-worthy events TwiCC produces
("agent finished working", "agent needs your attention") are lost. A Service Worker +
Web Push architecture was surveyed and rejected: too many external dependencies (secure
context on the client, Apple/Google push relays, iOS home-screen install requirement,
silent-push subscription revocation) for a single use case.

Instead, TwiCC will send **outbound notifications from the backend** via
[Apprise](https://github.com/caronc/apprise), a Python library that talks to 137
notification services (ntfy, Pushover, Bark, Telegram, Discord, generic JSON webhook,
email, …) through a single API. The user configures destinations as Apprise URLs; TwiCC
fires the same two events that currently drive browser notifications. This also opens a
generic automation channel (any HTTP receiver via `json://`, home automation, etc.).

Key properties:

- 100% server-side: no browser/OS/platform constraints, works identically whatever the
  client browser.
- The user composes destination URLs himself and manages his own credentials/accounts
  on the target services. TwiCC has no per-service code, no per-service form.
- The notification payload is the lowest common denominator across all services:
  **title + body**. A deep link to the session is appended to the body (all notification
  apps render URLs in the body as clickable).

## 2. Scope

Exactly the two events that drive browser notifications today
(`notifyProcessStateChange()` in `frontend/src/composables/useWebSocket.js`):

| Event | Backend trigger | Title |
|---|---|---|
| Agent finished working | `AgentInfo.state` transitions to `USER_TURN` (`previous_state != USER_TURN`) | `{provider label} finished working` |
| Agent needs attention | `pending_requests` count grew | `{provider label} has a question for you` (request_type `ask_user_question`) / `{provider label} needs your approval` (other) |

Body (mirrors `buildNotificationBody()`, same 50-char truncation of names):

```
Project: {project_name}            # "{parent} › {worktree}" for worktree projects
Session: {session_title}

{public_base_url}/project/{project_id}/session/{session_id}
```

The URL line is included only when the user configured a public base URL (see §4.3).
Hidden sessions never notify (the hook lives after the existing hidden-session check).
Dead/error/timeout states do NOT notify (they don't trigger browser notifications today
either — toasts only). Out of scope for v1: per-project filtering, per-target event
filtering, retries, delivery history.

## 3. Dependency

Add `apprise` (~=1.11, actively maintained, native asyncio via `async_notify()`) to
`pyproject.toml`. Direct dependency, not optional: an optional dependency would force
"not installed" states in the UI for a few MB saved. Note: Apprise pulls `requests`
(sync); its `async_notify()` wraps sync plugins in threads — acceptable for
fire-and-forget sends.

## 4. Backend

### 4.1 New module: `src/twicc/external_notifications.py`

- `notify_agent_event(info: AgentInfo) -> None` — entry point called from
  `broadcast_process_state()`. Detects the two events, builds title/body, and
  fire-and-forgets the send (`asyncio.create_task`). Sends iterate the configured
  targets and `add()` only the `enabled` ones.
- Transition detection:
  - *finished*: `info.state == USER_TURN and info.previous_state != USER_TURN` —
    `AgentInfo` natively carries `previous_state` (`src/twicc/agent/states.py`), no
    extra bookkeeping needed.
  - *needs attention*: requires comparing pending counts across broadcasts (the
    frontend does the same with its `previousState` store). Keep a module-level
    `dict[session_id, int]` of last seen pending counts; entry dropped when the
    session reaches `DEAD`. The newest pending request (`request_type`) picks the
    title variant.
- Provider label from `get_provider_helpers(info.provider).LABEL`.
- Session title / project name reuse `get_session_and_project_display()` — already
  computed in `broadcast_process_state()`; pass the values down rather than re-querying.
- Send: build one `apprise.Apprise()` from the configured URL lines, `await
  apobj.async_notify(title=..., body=...)`. Wrap in try/except + log; a notification
  failure must never affect the broadcast path. Invalid URL lines (rejected by
  `Apprise.add()`) are logged and skipped.

### 4.2 Hook point

`broadcast_process_state()` in `src/twicc/asgi.py` (line ~205), after the hidden-session
early-return and after `get_session_and_project_display()`:

```python
asyncio.create_task(notify_agent_event(info, session_title, project_name, project_parent_name))
```

One single hook for both events — same single source of truth as the WS broadcast that
drives the in-browser notifications.

### 4.3 Settings (synced)

Three new keys in `_GENERIC_SYNCED_SETTINGS_DEFAULTS` (`src/twicc/synced_settings.py`),
stored in `<data_dir>/settings.json`, served/synced through the existing
bootstrap + `synced_settings_updated` machinery:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `externalNotificationTargets` | list of objects | `[]` | Notification targets: `[{"id": "<generated>", "name": "<optional label>", "url": "<apprise url>", "enabled": true, "tested": null, "notifyUserTurn": true, "notifyPendingRequest": true}, ...]`. Disabled targets are kept but skipped at send time |
| `publicBaseUrl` | string | `""` | Base URL where the user reaches TwiCC (e.g. tunnel hostname). Used only to build the deep link; empty = no link line in the body |

A structured list (not a delimited string) so per-target state lives as real fields,
and any future per-target field is an additive change with no format migration.

Each target carries a **required** stable `id` and an optional human `name`. The
backend send path ignores both — they exist for the UI (a `name` reads better than
a masked URL) and as the fixed handle a future per-project scoping would reference
instead of list position (see §8). The `id` is generated at row creation via the
`generateUUID()` helper (`frontend/src/utils/crypto.js`), which falls back to
`crypto.getRandomValues()` in non-secure contexts (plain-HTTP LAN/tunnel access) —
never `crypto.randomUUID()` directly. The frontend validator requires `id` to be a
string, so an entry without one is rejected: with no back-compat shim, any
pre-`id` settings entry is simply dropped at load and re-created.

Event selection is **per target**: `notifyUserTurn` / `notifyPendingRequest` pick which
of the two events the target receives (absent = opted in, so hand-written or older
entries default to everything). Two short-lived *global* toggles
(`externalNotifyUserTurn` / `externalNotifyPendingRequest`) existed pre-release and are
listed in `_GENERIC_OBSOLETE_SYNCED_SETTINGS_KEYS` to be scrubbed from settings.json.

`tested` is tri-state: `true` = last test succeeded, `false` = last test failed,
`null`/absent = never tested. Semantics of "succeeded": the URL parsed and the target
service **accepted** the HTTP request (`async_notify()` returned `True` without
exception). This proves the pipeline, not end-to-end delivery — e.g. a typoed ntfy
topic is still accepted with a 200 — so the UI helper text states that the user should
confirm receipt on the device. The field is written by the **frontend** from the test
endpoint's response (then saved through the normal synced-settings flow; no extra
backend write path), and is **reset to `null` whenever the URL field is edited** so a
stale checkmark never vouches for a URL it didn't test.

`tested: true` is also the **send gate**: real notifications go only to enabled
targets whose last test succeeded. An untested or failing URL is never sent to (and
since editing a URL resets `tested`, a modified target stays silent until re-tested).

Targets with an empty URL are never persisted: the settings UI keeps just-added or
cleared rows as local drafts and only writes rows with a non-empty URL to the store.

Synced (not local) because the sender is the backend: there is exactly one configuration
for the whole instance, whichever device edits it. Note: destination URLs embed secrets
and will live in `settings.json` and be visible to any authenticated client — acceptable
for TwiCC's single-user model (same posture as the rest of the data dir).

The backend reads these via the existing in-memory synced-settings cache at send time —
no restart needed after edits.

### 4.4 Test endpoint

`POST /api/external-notifications/test/` (plain async view, auth via
`PasswordAuthMiddleware` like every `/api/` route):

- Body: `{"urls": ["..."]}` — the URL(s) to test as currently present in the form (not
  the saved settings), so the user can test before saving. The UI sends a single URL
  (per-target Test button); the endpoint accepts a list for flexibility.
- Behavior: one `Apprise` object **per URL** (to get per-URL results), send a test
  notification shaped like a real TwiCC one (same `Project:`/`Session:` body format,
  including the deep link when `publicBaseUrl` is configured), return
  `{"results": [{"url_masked": ..., "ok": bool, "error": str|null}, ...]}`. Invalid
  (unparseable) URLs are reported as failures with a distinct error.
- Responses echo a masked form of each URL (Apprise provides privacy-masked URL
  rendering) so secrets don't round-trip in clear.

## 5. Frontend

All changes live in the existing **Settings → Notifications** section
(`frontend/src/components/app/NotificationSettings.vue`), below the two
browser-notification groups, as a new "Push to your devices" subsection (separated by a
`wa-divider`, labeled with the cloud `synced-icon` marker since everything in it is
synced):

- Short explanation + links to the Apprise URL documentation (appriseit.com) and its
  URL builder (appriseit.com/url-builder/), with an inline example.
- `wa-input` for the public base URL first, with helper text as a regular
  `.setting-group-hint` paragraph (not the wa-input `hint` attribute, to match the
  section's hint styling).
- A **"Targets" list**: one block per target with a `wa-input` for the URL (monospace),
  an enable/disable `wa-switch`, a per-row "Test" button, and a remove button, plus a
  second line with the two per-target event switches ("Finished working" / "Needs
  attention"); and an "Add target" button. Per-row Test calls the test endpoint with
  that row's current (possibly unsaved, tracked on `input` events) URL and renders the
  result inline on the row — so one target can be tried without spamming the others.
- The Test button reflects the target's `tested` state: green check (`true`), red cross
  (`false`), neutral (`null`, never tested). Editing the URL resets the state to
  neutral. Helper text notes that a green check means the service accepted the test
  message — the user should confirm it actually arrived on the device.
- Row layout: `space-between` with three children — the (URL input + Test) group, the
  enable switch (visually centered in the leftover space), and the remove button. The
  per-target event switches sit on an indented second line.
- Narrow layout (container query on the target list, < 23rem): the URL + Test group
  takes its own line and the switch + remove button wrap below it, right-aligned.

The two values are synced settings: added to `SYNCED_SETTINGS_KEYS` +
`SETTINGS_SCHEMA` in `frontend/src/stores/settings.js` (plus
`collectAllSyncedSettings()`) with getters/setters following the existing pattern. The
existing `notif*` keys stay local-only (they gate per-device sounds/browser
notifications); the new keys gate the single server-side sender. The store syncs on the
input `change` event (blur/Enter), not on every keystroke.

## 6. Delivery semantics (v1)

- Fire-and-forget `asyncio.create_task`; Apprise's own per-plugin HTTP handling applies.
- No retries, no delivery history, no auto-disable in v1 — failures are logged to
  `backend.log`. The Test button is the debugging tool. (Industry-standard retry/backoff
  machinery is deliberately skipped: single user, low volume, and ntfy/Pushover/Telegram
  are reliable; revisit only if real-world flakiness shows up.)
- No HMAC signing: the user controls both ends; secrets belong in the destination URL.
- Ordering/dedup: not needed — events are rare and the per-session transition guards
  prevent duplicates at the source.

## 7. Edge cases

- **Hidden sessions**: never notify (hook is after the existing early-return).
- **Subagent/spawned sessions**: they go through the same `broadcast_process_state()`
  and therefore notify like the frontend does today (visible spawned sessions toast/notify
  too). No special casing in v1.
- **Empty config**: `externalNotificationTargets` empty or all-disabled → the hook
  returns immediately (cheap check before any Apprise construction).
- **Apprise import/construction errors**: caught and logged; never propagate.
- **Settings edited mid-flight**: each send reads the current cache; no process state.

## 8. Future extensions (explicitly not v1)

- ~~Per-target labels~~: done — the optional `name` field (see §4.3).
- Per-target last-delivery status display.
- More events (session died on error, long-running session reminders).
- **Per-project filtering** (discussed, deferred until a real need appears). The
  agreed direction, if built, is to keep a **single global pool** of targets (the
  only place URLs — and their `tested` gate — are defined) and add a `scope` to each
  target: `all` (default) / `only` these projects / `all except` these. Matching at
  send time uses `info.project_id`, also accepting the project's `worktree_of` parent
  so a target scoped on a main repo covers its worktrees (consistent with cost/activity
  aggregation). This avoids a second config surface and the duplicated Test/tri-state
  form a per-project "add target" UI would require. A read-only mirror inside the
  project edit dialog (toggle each target on/off for *this* project, writing back to
  the same `scope` lists) can be layered on later — two views, one source of truth.
  The rejected alternative was project-owned targets (two classes of target with
  divergent lifecycles, duplicated forms). Multi-project selection would reuse the
  workspace-edit project picker (extract/share it). Workspace-level scoping is a
  natural further step, not planned. The per-target `id` shipped now is the stable
  handle this would reference.
