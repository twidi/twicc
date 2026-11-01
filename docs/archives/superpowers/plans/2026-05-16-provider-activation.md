# Provider Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the user to enable/disable individual providers (Claude Code, Codex) via a `disabledProviders` synced setting; gate all runtime machinery (orchestrators, watchers, periodic tasks, API endpoints, UI surfaces) on that state; show a non-dismissable initial dialog on first launch or when no provider is enabled.

**Architecture:** Negative list (`disabledProviders`) stored in `settings.json`. Backend reads it to gate `OrchestratorRegistry.start_all()` at boot and to hot start/shutdown orchestrators when the setting changes. A new `enabled_providers` module exposes `is_provider_enabled` / `ensure_provider_enabled` used by every runtime endpoint and WS handler. Frontend derives `getEnabledProviders()` from the store, restricts runtime UI surfaces accordingly, and renders a non-dismissable dialog in `App.vue` when the key is absent or all providers are disabled.

**Tech Stack:** Python 3.13 (Django ASGI + Channels), Vue 3 Composition API + Pinia, Web Awesome 3.

**Spec:** `docs/superpowers/specs/2026-05-16-provider-activation-design.md`

**Note:** This project follows a "no tests, no linting" policy (see CLAUDE.md). Verification is manual: read backend logs via `devctl.py logs`, exercise the UI in the browser, and inspect WS frames in DevTools. Never restart the dev servers yourself — that's a user-reserved operation (see `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/feedback_never_restart_servers.md`).

**Worktree:** All work happens in `/home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider`. Every `Bash` command must `cd` into the worktree first — the editable install otherwise resolves to the main repo (see CLAUDE.md "Worktree Support").

---

## File Structure

### New backend files

| File | Responsibility |
|------|----------------|
| `src/twicc/providers/enabled.py` | Runtime source of truth: `is_provider_enabled`, `ensure_provider_enabled`, `ProviderDisabledError`, plus helpers to read the disabled list from the synced settings cache. |

### Modified backend files

| File | What changes |
|------|--------------|
| `src/twicc/synced_settings.py` | Distinguish "key absent" vs "empty list". Add `disabledProviders` to the list of recognized keys (without putting it in `_GENERIC_SYNCED_SETTINGS_DEFAULTS`). |
| `src/twicc/views.py` | Bootstrap exposes `disabledProviders` as a top-level field (so the front can use it for the dialog gate independently of `settings`). The rename branch in `session_detail` PATCH gets gated by `ensure_provider_enabled`. |
| `src/twicc/asgi.py` | `_handle_update_synced_settings` triggers hot start/shutdown when `disabledProviders` changes, applies the self-healing rule (refuse disabling a provider with live agents) and the default-provider rebind. Runtime WS handlers (`_handle_send_message`, `_handle_kill_process`, `_handle_stop_subagent`) call `ensure_provider_enabled`. |
| `src/twicc/orchestrator.py` | `OrchestratorRegistry.start_all` only starts orchestrators of currently enabled providers. New helpers `start_one(provider)` / `shutdown_one(provider)` for hot toggling. |
| `src/twicc/providers/codex/ws.py` | `_handle_pending_request_response` calls `ensure_provider_enabled(Provider.CODEX)`. |
| `src/twicc/providers/claude_code/ws.py` | Same, for `Provider.CLAUDE_CODE`. |

### New frontend files

| File | Responsibility |
|------|----------------|
| `frontend/src/components/app/ProviderActivationDialog.vue` | Non-dismissable modal asking the user which providers to activate. Hosted in `App.vue`. |

### Modified frontend files

| File | What changes |
|------|--------------|
| `frontend/src/providers/index.js` | Add `getEnabledProviders()` (derived from the settings store) alongside existing `getRegisteredProviders()`. |
| `frontend/src/constants.js` | Add `disabledProviders` to `SYNCED_SETTINGS_KEYS`. |
| `frontend/src/stores/settings.js` | Add `disabledProviders` state + validator + watcher to send updates. |
| `frontend/src/stores/data.js` | Add `hasActiveSessionForProvider(provider)` getter. |
| `frontend/src/App.vue` | Render `<ProviderActivationDialog>` and gate the visibility on the dialog conditions. |
| `frontend/src/components/app/SettingsPopover.vue` | New "Activated providers" block at the top of the `providers` section; default-provider `<wa-select>` filtered to enabled; provider-specific section entries hidden when disabled; `_statusAwareProviders` made reactive. |
| `frontend/src/components/session/detail/SessionItemsList.vue` | New "provider disabled" callout in the `.session-footer` chain (between stale and pending), shown when the session's provider is disabled. Rename action conditionally disabled. |
| `frontend/src/components/message/AgentSettingsPopover.vue` | Provider picker dropdown filtered to enabled providers; entire picker hidden when only one provider is enabled. |
| `frontend/src/views/ProjectView.vue` | `usageProviders` now filters on `getEnabledProviders()`. |

---

## Tasks

### Task 1: Backend — `enabled_providers` module (helpers + error type)

**Files:**
- Create: `src/twicc/providers/enabled.py`

- [ ] **Step 1: Create the module with the error type and helpers**

Write the full file content:

```python
"""Runtime source of truth for which providers are currently enabled.

The state is derived from the `disabledProviders` key in the synced settings.
A provider is *enabled* if it is registered (compiled in) AND not in the
`disabledProviders` list.

If the `disabledProviders` key is **absent** from the synced settings, this
module returns "no provider is enabled" so the rest of the backend stays
idle until the user makes a choice via the initial dialog (cf. spec §2/§3).

Callers that perform runtime actions on a specific provider MUST call
`ensure_provider_enabled(provider)` first. Read-only paths (DB queries,
parsing of historical session content) do NOT need the gate — see spec §6.3.
"""

from __future__ import annotations

from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.synced_settings import read_synced_settings


class ProviderDisabledError(Exception):
    """Raised when an operation targets a provider that is currently disabled."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        super().__init__(f"Provider {provider.value} is disabled")


def _has_disabled_providers_key() -> bool:
    """Return True if the `disabledProviders` key is physically present in
    the synced settings file. The mere absence of the key is the sentinel
    used to trigger the initial dialog (cf. spec §2.1)."""
    return "disabledProviders" in read_synced_settings()


def get_disabled_providers() -> set[Provider]:
    """Return the set of providers explicitly disabled by the user.

    Returns an empty set if the key is absent. Unknown provider names are
    silently dropped (forward-compat with futures that ship a renamed
    provider — never raises on stale strings)."""
    raw = read_synced_settings().get("disabledProviders") or []
    if not isinstance(raw, list):
        return set()
    valid = {p.value for p in Provider}
    return {Provider(v) for v in raw if v in valid}


def get_enabled_providers() -> set[Provider]:
    """Return the set of providers that are both registered AND enabled.

    If `disabledProviders` is absent from settings (= no choice made yet),
    returns an empty set — the back stays idle until the user validates the
    initial dialog."""
    if not _has_disabled_providers_key():
        return set()
    registered = set(get_provider_helpers_registry().keys())
    disabled = get_disabled_providers()
    return registered - disabled


def is_provider_enabled(provider: Provider) -> bool:
    return provider in get_enabled_providers()


def ensure_provider_enabled(provider: Provider) -> None:
    """Raise `ProviderDisabledError` if `provider` is not currently enabled."""
    if not is_provider_enabled(provider):
        raise ProviderDisabledError(provider)
```

- [ ] **Step 2: Smoke-import the module**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.enabled import is_provider_enabled, ensure_provider_enabled, ProviderDisabledError, get_enabled_providers; print('ok')"
```

Expected output: `ok`.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/enabled.py
git commit -m "feat(providers): add enabled_providers module"
```

---

### Task 2: Backend — Synced settings reads `disabledProviders` (and bootstrap exposes it)

**Files:**
- Modify: `src/twicc/synced_settings.py` (no behavior change, just docstring + a passthrough)
- Modify: `src/twicc/views.py` (bootstrap exposes `disabledProviders`)

The synced settings file already passes unknown keys through (cf. exploration A5). So no whitelist change is needed — `disabledProviders` written by the front via `update_synced_settings` will land in the file naturally. What we DO need:

1. Make sure the key, when present, survives `read_synced_settings()` (it does — `_cache.update(file_data)`).
2. Expose it explicitly in `/api/bootstrap/` as a top-level field so the front can detect the "key absent" state before `_settingsVersion` is initialised (the `settings` snapshot loses the distinction once defaults are merged).

- [ ] **Step 1: Add a small docstring note in `synced_settings.py`**

Open `src/twicc/synced_settings.py`. After `_GENERIC_SYNCED_SETTINGS_DEFAULTS` (around line 57), add a comment:

```python
# Note: `disabledProviders` (list[str]) is intentionally NOT listed here.
# Its absence in the settings file is the sentinel that triggers the initial
# provider activation dialog (see `twicc.providers.enabled` and spec §2).
```

This is documentation only; no code change.

- [ ] **Step 2: Add `disabledProvidersPresent` and `disabledProviders` to the bootstrap payload**

Open `src/twicc/views.py`, locate the `bootstrap()` function (around line 2124). The current payload includes `settings` and `settings_version`. Add two new fields next to them:

```python
from twicc.synced_settings import read_synced_settings  # likely already imported

# inside bootstrap(), before returning the JsonResponse:
raw_settings = read_synced_settings()
disabled_providers_present = "disabledProviders" in raw_settings
disabled_providers = raw_settings.get("disabledProviders") or [] if disabled_providers_present else []
```

Then add `"disabledProvidersPresent": disabled_providers_present` and `"disabledProviders": disabled_providers` to the response dict.

(Read the exact return shape around line 2148 first; preserve the existing keys and ordering.)

- [ ] **Step 3: Smoke test bootstrap**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
curl -s http://localhost:3500/api/bootstrap/ | python -m json.tool | head -40
```

(Adapt the port to your worktree's `.env`.)

Verify the response now contains `disabledProvidersPresent` and `disabledProviders`. Expected for a fresh DB: `disabledProvidersPresent: false`, `disabledProviders: []`.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/synced_settings.py src/twicc/views.py
git commit -m "feat(bootstrap): expose disabledProviders key presence and value"
```

Remind the user to restart the backend dev server after this commit (it's a Python change, no HMR).

---

### Task 3: Backend — `start_all` only starts enabled providers

**Files:**
- Modify: `src/twicc/orchestrator.py`

- [ ] **Step 1: Read the current `start_all` implementation**

Open `src/twicc/orchestrator.py` and re-read `OrchestratorRegistry.start_all()` (around lines 148-284). It currently uses `asyncio.gather(*[orch.start(...) for orch in self._orchestrators.values()], return_exceptions=True)`.

- [ ] **Step 2: Gate `start_all` on enabled providers**

Replace the loop in `start_all()` so it iterates only on enabled providers. Pseudo-code:

```python
async def start_all(self, shutdown_event, search_index_ready):
    from twicc.providers.enabled import get_enabled_providers
    enabled = get_enabled_providers()
    # if no provider is enabled (either no choice made yet OR everything
    # disabled), do not start anything — the user-facing app keeps serving
    # the initial dialog
    if not enabled:
        return
    coros = [
        orch.start(shutdown_event, search_index_ready)
        for provider, orch in self._orchestrators.items()
        if provider in enabled
    ]
    await asyncio.gather(*coros, return_exceptions=True)
```

Apply the same filter to `wait_initial_sync_done()` and `wait_compute_done()` so the parent task does not hang waiting for events that will never be set (those `asyncio.Event`s are owned by the orchestrator and remain in their initial state if `start()` was never called).

- [ ] **Step 3: Add `start_one(provider)` and `shutdown_one(provider)` helpers**

Add two new public methods on `OrchestratorRegistry`. They will be called by the hot-toggle path in Task 4:

```python
async def start_one(self, provider: Provider) -> None:
    """Start a single orchestrator (used by hot-toggle on activation)."""
    orch = self._orchestrators.get(provider)
    if orch is None:
        return
    # Reuse the shutdown_event/search_index_ready from the active run
    await orch.start(self._shutdown_event, self._search_index_ready)

async def shutdown_one(self, provider: Provider) -> None:
    """Shutdown a single orchestrator (used by hot-toggle on deactivation)."""
    orch = self._orchestrators.get(provider)
    if orch is None:
        return
    await orch.shutdown()
```

You will need to stash `shutdown_event` and `search_index_ready` on `self` during `start_all()` so the hot-toggle calls can reuse them. Add `self._shutdown_event = shutdown_event` and `self._search_index_ready = search_index_ready` at the top of `start_all()`.

- [ ] **Step 4: Smoke test that boot is clean when no providers are enabled**

Manual: temporarily edit your worktree's `db/data.sqlite`-adjacent `settings.json` to have no `disabledProviders` key (or remove it). Restart the backend (user action). Check the logs:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py logs back --lines=80
```

Expected: no "Initial sync done" / "Watcher started" lines from any provider (no orchestrator started). The HTTP / WS server is up; bootstrap returns `disabledProvidersPresent: false`.

(Once verified, restore your `settings.json` for normal dev.)

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/orchestrator.py
git commit -m "feat(orchestrator): only start enabled providers + add start_one/shutdown_one"
```

---

### Task 4: Backend — Hot toggle on `disabledProviders` change (with self-healing + default rebind)

**Files:**
- Modify: `src/twicc/asgi.py` (`_handle_update_synced_settings` around lines 966-1022)

This task wires the runtime side of the toggle: when the user changes `disabledProviders` from the front, the backend starts/stops the right orchestrators, refuses to disable providers with live agents (self-healing), and rebinds `defaultProvider` if the user disabled the current default.

**Important — async/sync boundary:**
`_handle_update_synced_settings` runs in async context but its merge+write happens inside a sync closure (`_merge_and_write`) dispatched via `sync_to_async(...)` so the write happens under `_settings_lock`. The two consequences:

1. **All file writes (self-healing rewrite, default rebind, version bump) must happen inside the sync closure**, while the lock is held. Doing them outside would race other clients writing to the same file.
2. **`shutdown_one` / `start_one` calls are async — they MUST happen in the outer scope, AFTER `sync_to_async(_merge_and_write)()` returns.**

So the closure returns the data the outer scope needs (old + new disabled sets, the final dict to broadcast), and the outer scope only applies the orchestrator transitions and the broadcast.

- [ ] **Step 1: Read `_handle_update_synced_settings`**

Open `src/twicc/asgi.py` around lines 966-1022. Note the structure: outer async function → inner sync `_merge_and_write()` closure under `with _settings_lock:` → call via `await sync_to_async(_merge_and_write)()` → broadcast or reject.

The closure currently returns `(new_version, reject_settings, reject_version)`. We're going to extend that tuple.

- [ ] **Step 2: Extend `_merge_and_write` to handle `disabledProviders` corrections inside the lock**

Rewrite the closure body. Pseudo-code (adapt names to what's already there — keep `existing_settings` as the local name):

```python
def _merge_and_write():
    from twicc.agent.registry import get_agent_manager_registry
    from twicc.core.enums import Provider

    with _settings_lock:
        existing_settings = read_synced_settings()
        current_version = existing_settings.get("_version", 0)

        # Stale write — reject as today
        if base_version is not None and base_version < current_version:
            clean, ver = prepare_settings_for_client(existing_settings)
            return {"status": "rejected", "clean": clean, "version": ver}

        # Snapshot the pre-merge disabled set BEFORE we touch existing_settings
        old_disabled = set(existing_settings.get("disabledProviders") or [])

        # Apply the client's merge
        existing_settings.update(synced_settings)

        # Run the existing provider consistency hook
        get_provider_helpers_registry().enforce_synced_settings_consistency(
            existing_settings, synced_settings,
        )

        # === Self-healing: refuse to disable a provider with live agents ===
        new_disabled_raw = existing_settings.get("disabledProviders")
        corrections: dict = {}  # keys we want to surface in the broadcast
        if isinstance(new_disabled_raw, list):
            new_disabled = set(new_disabled_raw)
            just_disabled = new_disabled - old_disabled
            registry = get_agent_manager_registry()
            refused: set[str] = set()
            for value in just_disabled:
                try:
                    provider = Provider(value)
                except ValueError:
                    continue
                manager = registry.get(provider)
                if manager and manager.get_active_agents():
                    refused.add(value)
            if refused:
                new_disabled -= refused
                existing_settings["disabledProviders"] = sorted(new_disabled)
                corrections["disabledProviders"] = sorted(new_disabled)

        # === Default-provider rebind ===
        # Recompute enabled set from the (possibly corrected) existing_settings
        registered = {p.value for p in get_provider_helpers_registry().keys()}
        final_disabled_set = set(existing_settings.get("disabledProviders") or [])
        enabled_after = registered - final_disabled_set
        current_default = existing_settings.get("defaultProvider")
        if enabled_after and current_default not in enabled_after:
            new_default = next(p.value for p in Provider if p.value in enabled_after)
            existing_settings["defaultProvider"] = new_default
            corrections["defaultProvider"] = new_default

        # === Version bump + single write ===
        existing_settings["_version"] = current_version + 1
        write_synced_settings(existing_settings)

        return {
            "status": "accepted",
            "version": current_version + 1,
            "old_disabled": old_disabled,
            "new_disabled": final_disabled_set,
            "corrections": corrections,
        }
```

Notes:
- One `write_synced_settings(existing_settings)` call at the end — keeps the file write atomic and the version increment consistent.
- `corrections` carries the keys the back rewrote on top of what the client sent. These get merged into the broadcast payload below.
- `get_provider_helpers_registry().keys()` returns the registered providers — adapt if the accessor is named differently in this version of the code.

- [ ] **Step 3: Drive the orchestrator hot toggle in the outer async scope**

Replace the existing `new_version, reject_settings, reject_version = await sync_to_async(_merge_and_write)()` + broadcast section with:

```python
result = await sync_to_async(_merge_and_write)()

if result["status"] == "rejected":
    await self.send_json({
        "type": "synced_settings_updated",
        "settings": result["clean"],
        "version": result["version"],
    })
    return

# Accepted — apply orchestrator transitions if the disabled set actually moved
from twicc.orchestrator import get_orchestrator_registry
old_disabled = result["old_disabled"]
new_disabled = result["new_disabled"]
to_disable = new_disabled - old_disabled
to_enable = old_disabled - new_disabled
orchestrators = get_orchestrator_registry()
for value in to_disable:
    try:
        provider = Provider(value)
    except ValueError:
        continue
    await orchestrators.shutdown_one(provider)
for value in to_enable:
    try:
        provider = Provider(value)
    except ValueError:
        continue
    # don't await — the front is ultra-optimistic and won't wait (cf. spec §4.1)
    asyncio.create_task(orchestrators.start_one(provider))
```

- [ ] **Step 4: Broadcast the corrected payload to all clients**

Right after the orchestrator transitions, build a broadcast payload that includes the client's submitted keys PLUS the back-side corrections (so other tabs / devices see the rebind and the self-healing):

```python
broadcast_settings = dict(synced_settings)
broadcast_settings.update(result["corrections"])
await self.channel_layer.group_send(
    "updates",
    {
        "type": "broadcast",
        "data": {
            "type": "synced_settings_updated",
            "settings": broadcast_settings,
            "version": result["version"],
        },
    },
)
```

This is the only point that diverges from the previous logic — instead of broadcasting `synced_settings` verbatim, we overlay back corrections. The client that originally sent the change sees the corrected values too, so its local optimistic state reconciles with the server.

- [ ] **Step 5: Smoke test hot toggle (manual, after backend restart)**

1. Open two browser tabs on TwiCC.
2. In one tab, Settings → toggle Codex OFF. The WS frame `synced_settings_updated` should arrive with `disabledProviders: ["codex"]` in both tabs.
3. Check `devctl logs back` — should show the Codex orchestrator shutdown messages.
4. Toggle Codex ON. Should show the Codex orchestrator startup (initial sync, watcher, plugin install).
5. **Self-healing test:** start a Codex session, send a long message, then while the agent is generating, try to disable Codex from another tab. The WS broadcast should come back with `disabledProviders: []` (correction applied) — the switch snaps back to ON in both tabs.
6. **Default rebind test:** with both providers enabled and `defaultProvider = "codex"`, disable Codex. The broadcast should include `defaultProvider: "claude_code"`.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/asgi.py
git commit -m "feat(asgi): hot start/shutdown providers on disabledProviders change

Includes self-healing (refuse to disable provider with live agents) and
auto-rebind of defaultProvider when the current default becomes disabled."
```

---

### Task 5: Backend — `ensure_provider_enabled` on runtime endpoints

**Files:**
- Modify: `src/twicc/asgi.py` (`_handle_send_message`, `_handle_kill_process`, `_handle_stop_subagent`)
- Modify: `src/twicc/providers/claude_code/ws.py` (`_handle_pending_request_response`)
- Modify: `src/twicc/providers/codex/ws.py` (`_handle_pending_request_response`)
- Modify: `src/twicc/views.py` (the rename branch of `session_detail` PATCH around line 508)

- [ ] **Step 1: Wrap `_handle_send_message`**

Open `src/twicc/asgi.py` and find `_handle_send_message` (around line 575). The function resolves the provider via `get_session_provider(session_id)` (existing session) or `content.get("provider")` (new draft). At the top, after the provider resolution:

```python
from twicc.providers.enabled import ensure_provider_enabled, ProviderDisabledError

# ... existing provider resolution ...

try:
    ensure_provider_enabled(provider)
except ProviderDisabledError as e:
    await self.send_json({
        "type": "error",
        "code": "provider_disabled",
        "provider": e.provider.value,
        "message": str(e),
    })
    return
```

- [ ] **Step 2: Wrap `_handle_kill_process` and `_handle_stop_subagent`**

Same pattern. Both handlers already resolve the target provider — add the gate at the same point in each function.

- [ ] **Step 3: Wrap Codex `_handle_pending_request_response`**

Open `src/twicc/providers/codex/ws.py`. At the top of the handler, before any SDK call:

```python
from twicc.providers.enabled import ensure_provider_enabled, ProviderDisabledError

try:
    ensure_provider_enabled(Provider.CODEX)
except ProviderDisabledError as e:
    await self.send_json({"type": "error", "code": "provider_disabled", "provider": e.provider.value, "message": str(e)})
    return
```

(Adapt the response shape to the existing convention in `codex/ws.py`. The point is: bail out before reaching the SDK.)

- [ ] **Step 4: Wrap Claude Code `_handle_pending_request_response`**

Same in `src/twicc/providers/claude_code/ws.py`, with `Provider.CLAUDE_CODE`.

- [ ] **Step 5: Gate the rename branch of `session_detail` PATCH**

Open `src/twicc/views.py` around line 446 (`session_detail`). Find the `title` branch (the block that calls `provider_helpers.rename_session` around line 508). Before mutating anything, add:

```python
from twicc.providers.enabled import ensure_provider_enabled, ProviderDisabledError

if "title" in data:
    try:
        ensure_provider_enabled(session.provider)
    except ProviderDisabledError as e:
        return JsonResponse(
            {"error": "provider_disabled", "provider": e.provider.value, "message": str(e)},
            status=409,
        )
    # ... existing title validation and update ...
```

The other PATCH branches (`archived`, `pinned`) are purely TwiCC DB updates — leave them ungated.

- [ ] **Step 6: Front-side handler for `provider_disabled` errors**

Check that `frontend/src/composables/useWebSocket.js` already has a generic handler for `{type: "error", ...}` messages — most TwiCC error responses go through one. Grep for `case 'error'` in that file.

If a generic handler exists (toasts, notivue notification, etc.), make sure `code: "provider_disabled"` payloads surface meaningfully — e.g. *"Cannot send to {provider}: it is disabled. Enable it from Settings → Providers."*.

If there is no generic error handler, add one branch:

```js
case 'error':
    if (msg.code === 'provider_disabled') {
        notivue.push({
            message: `Cannot reach ${msg.provider}: provider is disabled.`,
            type: 'error',
        })
        return
    }
    // fall through to existing handling (if any) or log
    console.warn('WS error:', msg)
    break
```

(Adapt to whatever the existing notification system is — `notivue` is mentioned in `App.vue`.)

This is defence in depth: the UI should already prevent these calls (callouts, hidden buttons), but a race condition between a toggle and an in-flight send must surface to the user instead of silently failing.

- [ ] **Step 7: Smoke test (manual)**

Once the user has restarted the backend, with Codex disabled in your settings:

1. Try to rename a Codex session from the UI. Backend should respond `409` with `provider_disabled` and the front shows a notification.
2. Try to send a message to a Codex session. WS should send back `error/provider_disabled` and the front shows a notification.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/asgi.py src/twicc/views.py src/twicc/providers/claude_code/ws.py src/twicc/providers/codex/ws.py frontend/src/composables/useWebSocket.js
git commit -m "feat(api): gate runtime endpoints with ensure_provider_enabled + front handler"
```

---

### Task 6: Frontend — settings store: `disabledProviders` state + validator + `getEnabledProviders` helper

**Files:**
- Modify: `frontend/src/constants.js` (add to `SYNCED_SETTINGS_KEYS`)
- Modify: `frontend/src/stores/settings.js` (state + validator + bootstrap snapshot)
- Modify: `frontend/src/providers/index.js` (add `getEnabledProviders()`)

- [ ] **Step 1: Add `disabledProviders` to `SYNCED_SETTINGS_KEYS`**

Open `frontend/src/constants.js` around line 204. Add the key:

```js
export const SYNCED_SETTINGS_KEYS = new Set([
    'defaultProvider', 'disabledProviders', 'titleGenerationEnabled', 'titleAutoApply',
    'titleSystemPrompt', 'autoUnpinOnArchive', 'terminalUseTmux', 'terminalTmuxConfigPath',
    'waTheme', 'waBrand',
])
```

- [ ] **Step 2: Add the validator and the state**

Open `frontend/src/stores/settings.js`. In the `SETTINGS_VALIDATORS` map (around line 73-106), add:

```js
disabledProviders: (v) =>
    Array.isArray(v) && v.every(item => typeof item === 'string' && getRegisteredProviders().includes(item)),
```

In the store `state()` (find the surrounding declarations of other synced fields), add:

```js
disabledProviders: [],
```

The default `[]` only applies until the bootstrap snapshot arrives; the `disabledProvidersPresent` flag (read by App.vue, cf. Task 8) is the actual gate for the initial dialog.

- [ ] **Step 3: Capture `disabledProvidersPresent` from bootstrap**

The bootstrap fetch path lives in `frontend/src/stores/settings.js` (probably an `initSettings()` action). Add a top-level field on the store:

```js
disabledProvidersPresent: false,
```

When the bootstrap response arrives (find the fetch handler that consumes `/api/bootstrap/`), store the boolean and the array:

```js
this.disabledProvidersPresent = data.disabledProvidersPresent === true
if (Array.isArray(data.disabledProviders)) {
    this.disabledProviders = data.disabledProviders
}
```

When a `synced_settings_updated` WS message arrives carrying `disabledProviders`, set `disabledProvidersPresent = true` because the back only sets the key after the dialog validation.

Find `applySyncedSettings()` in the same file (around line 621-656). After the value is applied via the existing `SYNCED_SETTINGS_KEYS` loop, add right after the loop:

```js
if ('disabledProviders' in remoteSettings) {
    this.disabledProvidersPresent = true
}
```

- [ ] **Step 4: Add `getEnabledProviders()` in `providers/index.js`**

Open `frontend/src/providers/index.js`. Add at the bottom:

```js
export function getEnabledProviders() {
    // Lazy import to avoid a static cycle settings <-> providers.
    const settings = useSettingsStore()
    const disabled = new Set(settings.disabledProviders || [])
    return getRegisteredProviders().filter(p => !disabled.has(p))
}
```

You'll need a static import of `useSettingsStore` at the top OR a lazy import inside the function depending on existing patterns. Check sibling helpers (`getProviderHelpers`, `getProviderOptions`) for the established convention.

**Pinia-active scope:** `useSettingsStore()` must be invoked inside a Pinia-active context — i.e. during render of a setup component, inside a `computed`, or inside an action of another store. Calling `getEnabledProviders()` at module load time (e.g. as a top-level constant in a helper file) will throw because the active Pinia instance isn't installed yet. All callers in this plan invoke it from inside `computed(...)` or render functions, so it's fine — but keep this constraint in mind if a new caller is introduced.

(If the static import creates a cycle that breaks HMR per CLAUDE.md "Avoiding Circular Imports", switch to lazy import via dynamic `await import('../stores/settings')` and rework callers to use the async form, or expose `disabledProviders` on a smaller dedicated module imported here.)

- [ ] **Step 5: Smoke test in the browser console**

```js
import('./src/providers/index.js').then(m => console.log(m.getEnabledProviders()))
```

Expected: returns `["claude_code", "codex"]` when no provider is disabled. Toggle `disabledProviders` manually in the store (`useSettingsStore().disabledProviders = ['codex']`) and re-check.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/constants.js frontend/src/stores/settings.js frontend/src/providers/index.js
git commit -m "feat(stores): add disabledProviders state and getEnabledProviders helper"
```

---

### Task 7: Frontend — data store: `hasActiveSessionForProvider` getter

**Files:**
- Modify: `frontend/src/stores/data.js`

- [ ] **Step 1: Locate the right place to add the getter**

Open `frontend/src/stores/data.js`. Find the existing getters (Pinia getters block, often under `getters: { ... }` in the options-style API or as `computed`/exported functions in setup-style). Skim how `processStates` is keyed — the keys are session IDs, the values include the current state.

- [ ] **Step 2: Add the getter**

Add the getter inline next to the other ones:

```js
hasActiveSessionForProvider(state) {
    return (provider) => {
        for (const [sessionId, ps] of Object.entries(state.processStates || {})) {
            if (!ps || ps.state === 'DEAD') continue
            const session = state.sessions?.[sessionId]
            if (session?.provider === provider) return true
        }
        return false
    }
}
```

(Adapt `'DEAD'` to the canonical constant if there is one — check imports in the file for a `PROCESS_STATE` enum.)

- [ ] **Step 3: Smoke test in the browser console**

```js
const store = window.__pinia_data__  // or useDataStore() from a setup component
store.hasActiveSessionForProvider('claude_code')
```

Expected: returns `true` while a Claude Code session is running; `false` otherwise.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/stores/data.js
git commit -m "feat(stores): add hasActiveSessionForProvider getter on data store"
```

---

### Task 8: Frontend — `ProviderActivationDialog.vue` + App.vue host

**Files:**
- Create: `frontend/src/components/app/ProviderActivationDialog.vue`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Re-read the existing non-dismissable dialog in App.vue**

Open `frontend/src/App.vue` around lines 282-287 (version mismatch dialog). Use the same `<wa-dialog>` pattern: `:open`, `@wa-hide.prevent` (block close on Esc / outside click), no `closable` attribute.

- [ ] **Step 2: Create `ProviderActivationDialog.vue`**

Full file content:

```vue
<script setup>
import { ref, computed, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { getRegisteredProviders, getProviderHelpers } from '../../providers'

const settings = useSettingsStore()

const choices = ref({})

// Initialise choices from the current store state: a provider is checked
// if it's NOT in disabledProviders (or if the key is absent — empty disabled
// list means all checked).
function syncChoicesFromStore() {
    const disabled = new Set(settings.disabledProviders || [])
    const next = {}
    for (const p of getRegisteredProviders()) {
        next[p] = !disabled.has(p)
    }
    choices.value = next
}

// Re-sync every time the dialog opens (in case the back rewrote the value
// while it was hidden).
const open = computed(() => !settings.disabledProvidersPresent || (settings.disabledProviders || []).length >= getRegisteredProviders().length)
watch(open, (now) => { if (now) syncChoicesFromStore() }, { immediate: true })

const atLeastOneChecked = computed(() => Object.values(choices.value).some(v => v === true))

function providerLabel(p) {
    const helpers = getProviderHelpers(p)
    return helpers.getProviderLabel?.() ?? p
}

async function save() {
    if (!atLeastOneChecked.value) return
    const disabled = getRegisteredProviders().filter(p => !choices.value[p])
    // Send via the standard synced-settings update channel (which already
    // handles version + broadcast on the back). The store's existing watcher
    // will pick this up if disabledProviders is in SYNCED_SETTINGS_KEYS.
    settings.disabledProviders = disabled
}
</script>

<template>
    <wa-dialog
        label="Choose your providers"
        :open="open"
        @wa-hide.prevent
        no-header-actions
    >
        <p>
            TwiCC supports multiple AI coding providers. Pick the ones you want
            to enable. You can change this anytime from <strong>Settings → Providers</strong>.
        </p>
        <div class="provider-choices">
            <label v-for="p in getRegisteredProviders()" :key="p" class="provider-row">
                <wa-switch
                    :checked="choices[p] === true"
                    @change="(e) => choices[p] = e.target.checked"
                ></wa-switch>
                <span>{{ providerLabel(p) }}</span>
            </label>
        </div>
        <wa-button
            slot="footer"
            variant="brand"
            :disabled="!atLeastOneChecked"
            @click="save"
        >
            Save
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
.provider-choices {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    margin: var(--wa-space-m) 0;
}
.provider-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    cursor: pointer;
}
</style>
```

Notes for the implementer:
- Verify `wa-switch` and `wa-dialog` are both registered in `frontend/src/main.js` (per CLAUDE.md "Web Awesome Components"). If `wa-switch` is missing, add `import '@awesome.me/webawesome/dist/components/switch/switch.js'`.
- The `save()` action assigns to `settings.disabledProviders`. The store's existing watcher (added implicitly by listing the key in `SYNCED_SETTINGS_KEYS`) sends the `update_synced_settings` WS message. Verify in the store code that this is how other synced fields are persisted — if there's an explicit `saveSettings()` call, use it instead.

- [ ] **Step 3: Mount the dialog in `App.vue`**

In `frontend/src/App.vue`:

1. Add the import next to the existing component imports:

```js
import ProviderActivationDialog from './components/app/ProviderActivationDialog.vue'
```

2. In the template, place `<ProviderActivationDialog />` right next to the version-mismatch dialog (lines 282-287). No props — it self-gates via the store.

- [ ] **Step 4: Smoke test (browser)**

1. Make sure `disabledProviders` key is absent in `<worktree>/settings.json` (delete the key manually if needed).
2. User restarts backend, refreshes the frontend.
3. The dialog should appear, Save disabled.
4. Toggle one provider on → Save becomes enabled. Click Save → dialog closes, back receives the message, settings file is updated.
5. Reload page → no dialog (key is now present with a valid value).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/app/ProviderActivationDialog.vue frontend/src/App.vue
git commit -m "feat(app): add ProviderActivationDialog hosted in App.vue"
```

---

### Task 9: Frontend — Settings panel: "Activated providers" block + default-provider filter + section masking

**Files:**
- Modify: `frontend/src/components/app/SettingsPopover.vue`

- [ ] **Step 1: Add a computed `enabledProviders` in the setup**

Open `frontend/src/components/app/SettingsPopover.vue`. Near the existing computed properties (`providerSections`, `providerOptions`, around line 37-50), add:

```js
import { getEnabledProviders } from '../../providers'
import { useDataStore } from '../../stores/data'

const dataStore = useDataStore()
const enabledProviders = computed(() => new Set(getEnabledProviders()))
```

- [ ] **Step 2: Update `providerSections` to mark each entry**

Where `providerSections` is computed (lines 37-50), expand each entry with `enabled`:

```js
const providerSections = computed(() =>
    getRegisteredProviders().map(provider => ({
        id: `provider_${provider}`,
        provider,
        label: ...,
        navLabel: ...,
        icon: ...,
        synced: true,
        enabled: enabledProviders.value.has(provider),
    }))
)
```

- [ ] **Step 3: Hide disabled provider entries in the sidebar**

The `sections` computed (line 52-62) concatenates `providerSections`. Filter on `enabled`:

```js
const sections = computed(() => [
    { id: 'global', ... },
    { id: 'providers', ... },
    ...providerSections.value.filter(s => s.enabled),
    { id: 'notifications', ... },
    ...
])
```

- [ ] **Step 4: Add the "Activated providers" block at the top of the `providers` section**

Locate the rendering of the `providers` section (around line 812-846 according to the exploration). Above the `<wa-select>` for `defaultProvider`, add:

```html
<div class="activated-providers-block">
    <h4>Activated providers</h4>
    <p class="hint">
        Disabling a provider stops all of its background tasks, prevents
        creating new sessions or renaming existing ones, and hides its
        settings section below. Existing sessions remain readable.
    </p>
    <div class="provider-switches">
        <div v-for="p in getRegisteredProviders()" :key="p" class="provider-switch-row">
            <wa-switch
                :checked="enabledProviders.has(p)"
                :disabled="isSwitchDisabled(p)"
                @change="onToggleProvider(p, $event)"
            ></wa-switch>
            <span>{{ providerLabelFor(p) }}</span>
            <span v-if="reasonFor(p)" class="hint danger">{{ reasonFor(p) }}</span>
        </div>
    </div>
</div>
```

With the supporting setup code:

```js
function providerLabelFor(p) {
    return getProviderHelpers(p).getProviderLabel?.() ?? p
}
function isLastEnabled(p) {
    return enabledProviders.value.size === 1 && enabledProviders.value.has(p)
}
function isSwitchDisabled(p) {
    if (!enabledProviders.value.has(p)) return false
    return isLastEnabled(p) || dataStore.hasActiveSessionForProvider(p)
}
function reasonFor(p) {
    if (!enabledProviders.value.has(p)) return null
    if (isLastEnabled(p)) return 'Cannot disable: at least one provider must remain active.'
    if (dataStore.hasActiveSessionForProvider(p)) return 'Cannot disable: active sessions in progress.'
    return null
}
function onToggleProvider(p, event) {
    const checked = event.target.checked
    const settings = useSettingsStore()
    const current = new Set(settings.disabledProviders || [])
    if (checked) current.delete(p)
    else current.add(p)
    settings.disabledProviders = [...current]
}
```

Place the corresponding CSS in the `<style scoped>` block:

```css
.activated-providers-block { margin-bottom: var(--wa-space-l); }
.activated-providers-block h4 { margin: 0 0 var(--wa-space-2xs); }
.provider-switches { display: flex; flex-direction: column; gap: var(--wa-space-s); }
.provider-switch-row { display: flex; align-items: center; gap: var(--wa-space-s); }
.hint { font-size: var(--wa-font-size-s); color: var(--wa-color-neutral-fill-loud); }
.hint.danger { color: var(--wa-color-danger-fill-loud); }
```

- [ ] **Step 5: Filter the `defaultProvider` select**

Around line 817-844, the `<wa-option>` loop iterates on `providerOptions`. Replace it (or filter it) so only enabled providers appear:

```js
const enabledProviderOptions = computed(() => providerOptions.value.filter(opt => enabledProviders.value.has(opt.value)))
```

Use `enabledProviderOptions` in the template.

(The back already rebinds the stored value to a valid one — cf. Task 4 — so no defensive logic on the front.)

- [ ] **Step 6: Make `_statusAwareProviders` reactive (footer indicators)**

Around lines 344-398 of the same file. Currently `_statusAwareProviders` is a plain constant computed once at setup; the rotation `setInterval` (called by `_scheduleStatusRotation()`) captures `_statusAwareProviders.length` at that point and never re-reads it. Converting to a naive `computed` is not enough: the scheduled callback would still use a stale length, leaving the rotation pointing at indices that no longer exist after a provider is disabled.

Convert in two steps:

1. Replace the constant with a `computed`:

```js
import { computed, watch } from 'vue'
import { getEnabledProviders } from '../../providers'

const _statusAwareProviders = computed(() =>
    getEnabledProviders()
        .map(provider => ({ provider, helpers: getProviderHelpers(provider), getter: getProviderHelpers(provider).getServiceStatus() }))
        .filter(({ getter }) => getter !== null)
)
```

2. Adjust all callers to read `.value`. Notably `currentStatusProvider` becomes:

```js
const currentStatusProvider = computed(() => {
    const list = _statusAwareProviders.value
    if (list.length === 0) return null
    return list[currentStatusIdx.value % list.length]
})
```

3. Watch the length and reschedule the rotation when it changes. Add **after** the existing `_scheduleStatusRotation()` setup:

```js
watch(
    () => _statusAwareProviders.value.length,
    (newLen, oldLen) => {
        // Reset the index if the old position is now out of bounds.
        if (currentStatusIdx.value >= newLen) {
            currentStatusIdx.value = 0
        }
        // Re-schedule: cancel the previous interval and create a new one
        // — the new interval will close over the fresh `.value.length`.
        _cancelStatusRotation()
        if (newLen > 1) {
            _scheduleStatusRotation()
        }
    }
)
```

(Names `_cancelStatusRotation` / `_scheduleStatusRotation` reflect what's already in the file — adapt to the actual identifiers. If the rotation closes its `setInterval` over a captured `_statusAwareProviders` reference, the cancel+reschedule is mandatory; if it reads `_statusAwareProviders.value.length` inside the callback, the watch is optional but the index reset is still needed.)

When `newLen === 0` (no provider with a status getter is enabled), the rotation stays cancelled and the footer indicator hides itself naturally (template already guards on the array being non-empty via `currentStatusProvider`).

- [ ] **Step 7: Smoke test (browser)**

1. Settings → Providers section shows the new "Activated providers" block.
2. Toggle Codex OFF: the section under "Providers" for Codex disappears from the sidebar. Default provider select now only shows Claude Code. Footer status indicator for OpenAI disappears.
3. Confirm the switch row hint says "Cannot disable: at least one provider must remain active." when only Claude Code is left, and the Claude Code switch is grayed.
4. Re-enable Codex: section comes back, default select shows both, status indicator returns.
5. Start a Codex session, send a message, while it's running, try to disable Codex: the switch should be grayed with "Cannot disable: active sessions in progress." hint.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/app/SettingsPopover.vue
git commit -m "feat(settings): provider activation block + filter default select + reactive status rotation"
```

---

### Task 10: Frontend — Session view: disabled-provider callout + rename guard

**Files:**
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue`

- [ ] **Step 1: Compute `isProviderEnabled` for the current session**

Open `frontend/src/components/session/detail/SessionItemsList.vue` around line 117-118 (where `session` and `providerLabel` are computed). Add:

```js
import { getEnabledProviders } from '../../../providers'
import { computed } from 'vue'

const enabledProviders = computed(() => new Set(getEnabledProviders()))
const isProviderEnabled = computed(() => {
    const p = session.value?.provider
    return p ? enabledProviders.value.has(p) : true
})
```

- [ ] **Step 2: Add the callout in the `.session-footer` chain**

The footer chain is the `v-if isStale / v-else-if hasPendingRequest / v-else-if !parentSession` group around line 1550-1556. Insert a new branch:

```html
<div v-if="isStale && !parentSessionId" class="stale-banner">
    <!-- existing stale-banner ... -->
</div>
<div v-else-if="!isProviderEnabled && !parentSessionId" class="provider-disabled-banner">
    <wa-callout variant="warning" appearance="outlined">
        <wa-icon slot="icon" name="circle-pause"></wa-icon>
        <div class="provider-disabled-content">
            <strong>{{ providerLabel }} is disabled</strong>
            <span>
                Re-enable {{ providerLabel }} from
                <strong>Settings → Providers</strong> to resume this session.
            </span>
        </div>
    </wa-callout>
</div>
<!-- existing pending request branch and MessageInput follow -->
```

Add the CSS by reusing the stale-banner classes (just copy the rule):

```css
.provider-disabled-banner { padding: var(--wa-space-s); }
.provider-disabled-content { display: flex; flex-direction: column; gap: var(--wa-space-2xs); }
```

- [ ] **Step 3: Guard the rename action**

Locate where the rename UI is rendered (it's likely in the session header / dropdown — grep `rename` inside `frontend/src/components/session/`). Common location: a `<wa-menu-item>` inside a session actions menu, or an inline button. Two options:

- **Disable** the menu item when `!isProviderEnabled`: add `:disabled="!isProviderEnabled"` to the `<wa-menu-item>`, plus a `wa-tooltip` saying *"Cannot rename: provider is disabled."*.
- **Hide** the menu item entirely: `v-if="isProviderEnabled"`.

Prefer **disabling with a tooltip** — the user sees the action exists but understands why it's locked.

If `isProviderEnabled` is not in scope of the rename component, import / pass it the same way.

- [ ] **Step 4: Smoke test (browser)**

1. Disable Codex.
2. Open a Codex session → "Codex is disabled" callout instead of MessageInput.
3. Try to rename → action disabled, tooltip shown.
4. Re-enable Codex → callout disappears, rename works.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/SessionItemsList.vue
git commit -m "feat(session): callout for disabled provider + rename guard"
```

(If the rename UI lives in a separate file, include it in the same commit with a richer commit body.)

---

### Task 11: Frontend — MessageInput / AgentSettingsPopover: picker filter + hide if single

**Files:**
- Modify: `frontend/src/components/message/AgentSettingsPopover.vue`

- [ ] **Step 1: Filter `providerSwitcherOptions`**

Open `frontend/src/components/message/AgentSettingsPopover.vue` around lines 69-77. Replace the existing computed:

```js
const providerSwitcherOptions = computed(() => {
    const current = props.session?.provider
    const enabled = new Set(getEnabledProviders())
    return getProviderOptions()
        .filter(opt => enabled.has(opt.value))
        .map(opt => ({
            value: opt.value,
            label: opt.label,
            icon: opt.icon,
            active: opt.value === current,
        }))
})
```

Don't forget the import:

```js
import { getEnabledProviders, getProviderOptions } from '../../providers'
```

- [ ] **Step 2: Hide the picker entirely when only one provider is enabled**

Find the `<wa-dropdown v-if="isDraft">` for the provider switcher (around lines 280-307). Tighten the condition:

```html
<wa-dropdown v-if="isDraft && providerSwitcherOptions.length > 1">
    <!-- existing dropdown content -->
</wa-dropdown>
```

The single enabled provider becomes the implicit choice for new sessions. The existing default-provider logic in `data.js` already handles new session creation with the right provider (the back has rebound it if needed — cf. Task 4).

- [ ] **Step 3: Smoke test (browser)**

1. Both providers enabled: open a new draft → picker shows both.
2. Disable Codex: open a new draft → picker disappears (only Claude Code possible).
3. Existing "Codex doesn't support documents" callout (lines 257-273) is independent and remains for sessions that already are Codex — no change there.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/message/AgentSettingsPopover.vue
git commit -m "feat(message): filter provider picker on enabled providers + hide if single"
```

---

### Task 12: Frontend — Usage rotation filter

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Filter `usageProviders` on enabled**

Open `frontend/src/views/ProjectView.vue` around line 111. Replace:

```js
const usageProviders = computed(() =>
    getRegisteredProviders().filter(provider => getProviderHelpers(provider).tracksUsage())
)
```

with:

```js
const usageProviders = computed(() =>
    getEnabledProviders().filter(provider => getProviderHelpers(provider).tracksUsage())
)
```

Update the import line at the top to include `getEnabledProviders`.

- [ ] **Step 2: Smoke test (browser)**

1. Project view: usage block rotates between providers that track usage AND are enabled.
2. Disable one: it drops out of the rotation immediately.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/views/ProjectView.vue
git commit -m "feat(project-view): filter usage rotation on enabled providers"
```

---

## End-of-plan verification checklist

After all tasks are merged and the user has restarted the backend + reloaded the frontend, run through this manual checklist:

- [ ] Fresh `settings.json` (no `disabledProviders` key) → dialog appears in `App.vue`, blocking, Save disabled until at least one provider checked.
- [ ] Save with both checked → settings file now contains `"disabledProviders": []`, no dialog on reload.
- [ ] Settings → Providers shows the "Activated providers" block with both switches ON.
- [ ] Toggle Codex OFF: Codex section disappears from settings sidebar; default select restricted to Claude Code; footer OpenAI status indicator disappears; project view usage rotation drops Codex.
- [ ] Open any existing Codex session: callout replaces MessageInput; rename action is disabled with tooltip.
- [ ] WS frame trace shows `synced_settings_updated` after each toggle.
- [ ] Backend logs show Codex orchestrator shutdown on disable, full start (with plugin install) on re-enable.
- [ ] Start a Claude Code session → only "Cannot disable: at least one provider must remain active." (since Codex is enabled). Disable Codex, then the Claude Code switch turns grey because it's now the last one.
- [ ] Re-enable Codex. Start a Codex session and message → while it's running, the Codex switch is grey with "active sessions in progress." hint.
- [ ] Edit `settings.json` manually to put both providers in `disabledProviders`. Reload → dialog reappears (key present but no enabled provider).

---

## Notes for the implementer

1. **Worktree discipline:** prefix every `Bash` command with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider`. Forgetting this hits the main repo's editable install — already burned three times historically.
2. **Never restart dev servers** — that's the user's responsibility (see memory `feedback_never_restart_servers.md`). At the end of each task that requires a backend restart, remind the user in your status update.
3. **Web Awesome 3 imports:** verify every new `wa-*` element used in `ProviderActivationDialog.vue` and the Settings additions (`wa-switch`, `wa-callout`, `wa-icon`, `wa-tooltip`, `wa-dropdown`, `wa-menu-item`) is imported in `frontend/src/main.js`. Missing imports cause unstyled components in production.
4. **Avoid circular imports:** `settings.js ↔ providers/index.js` is a likely cycle. Use lazy dynamic `await import()` if static imports break HMR. See CLAUDE.md "Avoiding Circular Imports".
5. **`disabledProviders` channel:** prefer using the existing synced-settings update mechanism (set the store field, the existing watcher does the WS send) rather than introducing a new WS message type. Verify by tailing the WS frames in DevTools.
6. **Default provider rebind happens on the back:** Task 4 ensures the front never sees an invalid `defaultProvider`. No defensive logic on the front.
