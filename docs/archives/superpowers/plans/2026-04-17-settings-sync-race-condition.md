# Settings Sync Race Condition Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix settings synchronization between multiple TwiCC clients by adding a nextTick guard (prevents watcher echo) and a version counter (rejects stale writes).

**Architecture:** Backend adds `_version` field to `settings.json` and a `threading.Lock` for write safety. Frontend fixes the broken `_isApplyingRemoteSettings` guard via `nextTick` and tracks `_settingsVersion` to send/receive version info. The WebSocket protocol gains a `version`/`baseVersion` field.

**Tech Stack:** Django (ASGI, Channels), Python 3.13, Vue 3 (Composition API, Pinia)

**Spec:** `docs/superpowers/specs/2026-04-17-settings-sync-race-condition-design.md`

**Note:** This project uses "no tests, no linting" policy (see CLAUDE.md). Verification is manual via dev servers.

---

### Task 1: Backend — Add `_version` support and lock to `synced_settings.py`

**Files:**
- Modify: `src/twicc/synced_settings.py`

- [ ] **Step 1: Add threading import and `_settings_lock`**

At the top of `src/twicc/synced_settings.py`, add `threading` to imports (after `import os`) and create the lock after the `_cache` declaration (after line 94):

```python
import threading
```

```python
_settings_lock = threading.Lock()
```

- [ ] **Step 2: Update `read_synced_settings` to handle `_version`**

In `read_synced_settings()` (line 97-113), after the merge at line 112, ensure `_version` defaults to `0` if not present in file data. Add after line 112:

```python
_cache.setdefault("_version", 0)
```

- [ ] **Step 3: Add `prepare_settings_for_client` helper**

Add a new function after `write_synced_settings` (after line 139):

```python
def prepare_settings_for_client(settings: dict) -> tuple[dict, int]:
    """Strip _version from settings and return (clean_settings, version).

    Used by all code paths that send settings to the frontend to avoid
    repeating the _version stripping logic.
    """
    clean = settings.copy()
    version = clean.pop("_version", 0)
    return clean, version
```

- [ ] **Step 4: Export new symbols**

Verify that `_settings_lock` and `prepare_settings_for_client` are importable. No `__all__` in this module, so they are already accessible. Just ensure the import lines in `asgi.py` and `views.py` will be updated in subsequent tasks.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/synced_settings.py
git commit -m "feat: add _version support, threading lock, and prepare_settings_for_client to synced_settings"
```

---

### Task 2: Backend — Version-aware `_handle_update_synced_settings` in `asgi.py`

**Files:**
- Modify: `src/twicc/asgi.py:1197-1223` (`_handle_update_synced_settings`)
- Modify: `src/twicc/asgi.py:28` (imports)

- [ ] **Step 1: Update imports**

At line 28 of `asgi.py`, add `_settings_lock` and `prepare_settings_for_client` to the existing import:

```python
from twicc.synced_settings import read_synced_settings, write_synced_settings, _settings_lock, prepare_settings_for_client
```

- [ ] **Step 2: Rewrite `_handle_update_synced_settings`**

Replace lines 1197-1223 with:

```python
    async def _handle_update_synced_settings(self, content: dict) -> None:
        """Handle update_synced_settings request from client.

        Uses optimistic concurrency: if the client's baseVersion is behind
        the current version, the write is rejected and the client is resynced.
        """
        synced_settings = content.get("settings")
        if not isinstance(synced_settings, dict):
            return
        base_version = content.get("baseVersion")  # None for old clients

        def _merge_and_write():
            with _settings_lock:
                existing = read_synced_settings()
                current_version = existing.get("_version", 0)

                # Reject stale writes (accept if baseVersion is None — safety for rolling upgrades)
                if base_version is not None and base_version < current_version:
                    clean, ver = prepare_settings_for_client(existing)
                    return None, clean, ver  # rejected

                # Accepted — merge, increment version, write
                existing.update(synced_settings)
                existing["_version"] = current_version + 1
                write_synced_settings(existing)
                return current_version + 1, None, None  # accepted

        new_version, reject_settings, reject_version = await sync_to_async(_merge_and_write)()

        if new_version is not None:
            # Accepted — broadcast to all clients
            await self.channel_layer.group_send(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "synced_settings_updated",
                        "settings": synced_settings,
                        "version": new_version,
                    },
                },
            )
        else:
            # Rejected — resync only this client
            await self.send_json({
                "type": "synced_settings_updated",
                "settings": reject_settings,
                "version": reject_version,
            })
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: add version-based optimistic concurrency to settings update handler"
```

---

### Task 3: Backend — Send `version` on WebSocket connect

**Files:**
- Modify: `src/twicc/asgi.py:594-597` (connect handler settings block)

- [ ] **Step 1: Update the connect handler**

Replace lines 594-597:

```python
        # Send synced settings to the connecting client
        if self._should_send("synced_settings_updated"):
            synced_settings = await sync_to_async(read_synced_settings)()
            await self.send_json({"type": "synced_settings_updated", "settings": synced_settings})
```

With:

```python
        # Send synced settings to the connecting client
        if self._should_send("synced_settings_updated"):
            raw_settings = await sync_to_async(read_synced_settings)()
            clean_settings, version = prepare_settings_for_client(raw_settings)
            await self.send_json({"type": "synced_settings_updated", "settings": clean_settings, "version": version})
```

- [ ] **Step 2: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: include settings version in WebSocket connect message"
```

---

### Task 4: Backend — Make changelog write paths version-aware

**Files:**
- Modify: `src/twicc/asgi.py:430-480` (`_resolve_changelog_versions`)
- Modify: `src/twicc/asgi.py:1419-1432` (`_handle_changelog_seen`)

- [ ] **Step 1: Update `_resolve_changelog_versions`**

This function has two write paths (line 450 and line 480). Both must acquire the lock and increment `_version`.

At line 440, wrap the read in the lock. Replace the body of the function from line 440 to line 480 (the two `write_synced_settings` calls) to use `_settings_lock`:

```python
    with _settings_lock:
        all_settings = read_synced_settings()
        last = all_settings.get("lastChangelogVersionSeen")
        previous = all_settings.get("previousLastChangelogVersionSeen")

        # --- Step 1: Normalize / initialize the two variables ---

        if not all_settings:
            # No settings or empty → first install
            all_settings["lastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["previousLastChangelogVersionSeen"] = settings.APP_VERSION
            all_settings["_version"] = all_settings.get("_version", 0) + 1
            write_synced_settings(all_settings)
            return settings.APP_VERSION, settings.APP_VERSION, False

        if last is None and previous is None:
            last = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
            previous = VERSION_BEFORE_LAST_CHANGELOG_VERSION_SEEN
        elif last is not None and previous is None:
            previous = VERSION_BEFORE_PREVIOUS_LAST_CHANGELOG_VERSION_SEEN
        elif previous is not None and last is None:
            last = previous

        # --- Step 2: Update previous based on upgrade detection ---

        if last == previous:
            pass
        elif last == settings.APP_VERSION:
            pass
        else:
            previous = last

        # --- Persist ---

        all_settings["lastChangelogVersionSeen"] = last
        all_settings["previousLastChangelogVersionSeen"] = previous
        all_settings["_version"] = all_settings.get("_version", 0) + 1
        write_synced_settings(all_settings)
```

Note: the rest of the function (lines 482-489, the `show_forced` logic) stays outside the lock — it only reads local variables, not shared state.

- [ ] **Step 2: Update `_handle_changelog_seen`**

Replace the `_persist` inner function (lines 1425-1430) with:

```python
        def _persist():
            from twicc.synced_settings import read_synced_settings, write_synced_settings, _settings_lock

            with _settings_lock:
                all_settings = read_synced_settings()
                all_settings["lastChangelogVersionSeen"] = version
                all_settings["_version"] = all_settings.get("_version", 0) + 1
                write_synced_settings(all_settings)
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: make changelog write paths version-aware with settings lock"
```

---

### Task 5: Backend — Include `version` in HTTP `/api/settings/` response

**Files:**
- Modify: `src/twicc/views.py:1929-1938`

- [ ] **Step 1: Update the endpoint**

Replace lines 1929-1938:

```python
def synced_settings(request):
    """GET /api/settings/ - Current synced settings, defaults, and Claude settings categories."""
    from twicc.synced_settings import CLAUDE_SETTINGS_CATEGORIES, SYNCED_SETTINGS_DEFAULTS, read_synced_settings

    return JsonResponse({
        "settings": read_synced_settings(),
        "default_settings": SYNCED_SETTINGS_DEFAULTS,
        "claude_settings_categories": CLAUDE_SETTINGS_CATEGORIES,
        "dev_mode": settings.DEV_MODE,
    })
```

With:

```python
def synced_settings(request):
    """GET /api/settings/ - Current synced settings, defaults, and Claude settings categories."""
    from twicc.synced_settings import CLAUDE_SETTINGS_CATEGORIES, SYNCED_SETTINGS_DEFAULTS, prepare_settings_for_client, read_synced_settings

    clean_settings, version = prepare_settings_for_client(read_synced_settings())
    return JsonResponse({
        "settings": clean_settings,
        "version": version,
        "default_settings": SYNCED_SETTINGS_DEFAULTS,
        "claude_settings_categories": CLAUDE_SETTINGS_CATEGORIES,
        "dev_mode": settings.DEV_MODE,
    })
```

- [ ] **Step 2: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat: include settings version in /api/settings/ HTTP response"
```

---

### Task 6: Frontend — Fix nextTick guard and add version tracking in `settings.js`

**Files:**
- Modify: `frontend/src/stores/settings.js`

- [ ] **Step 1: Add `nextTick` import**

At line 5, add `nextTick` to the existing Vue import:

```javascript
import { watch, nextTick } from 'vue'
```

- [ ] **Step 2: Add module-level `_settingsVersion`**

After `let _pendingSyncedSettings = null` (line 679), add:

```javascript
// Current settings version from backend (for optimistic concurrency).
// Module-level (not in store state) to avoid unnecessary reactivity.
let _settingsVersion = 0
```

- [ ] **Step 3: Rewrite `applySyncedSettings`**

Replace lines 596-608:

```javascript
        applySyncedSettings(remoteSettings) {
            if (!remoteSettings || typeof remoteSettings !== 'object') return
            this._isApplyingRemoteSettings = true
            for (const key of SYNCED_SETTINGS_KEYS) {
                if (key in remoteSettings) {
                    const validator = SETTINGS_VALIDATORS[key]
                    if (!validator || validator(remoteSettings[key])) {
                        this[key] = remoteSettings[key]
                    }
                }
            }
            this._isApplyingRemoteSettings = false
        },
```

With:

```javascript
        applySyncedSettings(remoteSettings, version) {
            if (!remoteSettings || typeof remoteSettings !== 'object') return
            // Reject incoming settings with a version older than what we already have.
            // This closes the HTTP/WS ordering gap: if the WebSocket pushes version 5
            // before initSettings() applies the HTTP-fetched version 3, the stale
            // HTTP data is silently dropped.
            if (version !== undefined && version < _settingsVersion) return
            this._isApplyingRemoteSettings = true
            for (const key of SYNCED_SETTINGS_KEYS) {
                if (key in remoteSettings) {
                    const validator = SETTINGS_VALIDATORS[key]
                    if (!validator || validator(remoteSettings[key])) {
                        this[key] = remoteSettings[key]
                    }
                }
            }
            if (version !== undefined) {
                _settingsVersion = version
            }
            // Clear the guard AFTER Vue has flushed the watchers scheduled by the
            // mutations above. Vue's nextTick resolves after the current job flush,
            // so any watcher triggered by the mutations will still see the flag as
            // true and skip the outgoing send.
            nextTick(() => { this._isApplyingRemoteSettings = false })
        },
```

- [ ] **Step 4: Update the outgoing watcher to send `baseVersion`**

Replace lines 775-793:

```javascript
    // Watch synced settings and send to backend when changed by the user.
    // The guard flag (_isApplyingRemoteSettings) prevents re-sending when
    // changes come from the backend via WebSocket.
    // Lazy import of useWebSocket avoids circular dependency (settings.js ↔ useWebSocket.js).
    watch(
        () => {
            const synced = {}
            for (const key of SYNCED_SETTINGS_KEYS) {
                synced[key] = store[key]
            }
            return synced
        },
        async (newSynced) => {
            if (store._isApplyingRemoteSettings) return
            const { sendSyncedSettings } = await import('../composables/useWebSocket')
            sendSyncedSettings(newSynced)
        },
        { deep: true }
    )
```

With:

```javascript
    // Watch synced settings and send to backend when changed by the user.
    // The guard flag (_isApplyingRemoteSettings) prevents re-sending when
    // changes come from the backend via WebSocket (cleared via nextTick).
    // Lazy import of useWebSocket avoids circular dependency (settings.js ↔ useWebSocket.js).
    watch(
        () => {
            const synced = {}
            for (const key of SYNCED_SETTINGS_KEYS) {
                synced[key] = store[key]
            }
            return synced
        },
        async (newSynced) => {
            if (store._isApplyingRemoteSettings) return
            const { sendSyncedSettings } = await import('../composables/useWebSocket')
            sendSyncedSettings(newSynced, _settingsVersion)
        },
        { deep: true }
    )
```

- [ ] **Step 5: Update `applyDefaultSettings` to accept version**

Replace lines 666-676:

```javascript
export function applyDefaultSettings(defaultSettings, currentSettings, claudeSettingsCategories, devMode) {
    if (defaultSettings && typeof defaultSettings === 'object') {
        Object.assign(SETTINGS_SCHEMA, defaultSettings)
    }
    if (claudeSettingsCategories && typeof claudeSettingsCategories === 'object') {
        _claudeSettingsCategories = claudeSettingsCategories
    }
    SETTINGS_SCHEMA._devMode = !!devMode
    // Store current settings for applySyncedSettings() to use after store init
    _pendingSyncedSettings = currentSettings
}
```

With:

```javascript
export function applyDefaultSettings(defaultSettings, currentSettings, claudeSettingsCategories, devMode, version) {
    if (defaultSettings && typeof defaultSettings === 'object') {
        Object.assign(SETTINGS_SCHEMA, defaultSettings)
    }
    if (claudeSettingsCategories && typeof claudeSettingsCategories === 'object') {
        _claudeSettingsCategories = claudeSettingsCategories
    }
    SETTINGS_SCHEMA._devMode = !!devMode
    // Store current settings + version for applySyncedSettings() to use after store init
    _pendingSyncedSettings = currentSettings
    _pendingSettingsVersion = version
}
```

- [ ] **Step 6: Add `_pendingSettingsVersion` and update `initSettings`**

After the new `_settingsVersion` variable (added in step 2), add:

```javascript
let _pendingSettingsVersion = undefined
```

Then update `initSettings` (line 693-700). Replace:

```javascript
    // Apply synced settings fetched from the API before mount
    if (_pendingSyncedSettings) {
        store.applySyncedSettings(_pendingSyncedSettings)
        _pendingSyncedSettings = null
    }
```

With:

```javascript
    // Apply synced settings fetched from the API before mount
    if (_pendingSyncedSettings) {
        store.applySyncedSettings(_pendingSyncedSettings, _pendingSettingsVersion)
        _pendingSyncedSettings = null
        _pendingSettingsVersion = undefined
    }
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/stores/settings.js
git commit -m "feat: fix nextTick guard in applySyncedSettings and add version tracking"
```

---

### Task 7: Frontend — Update `useWebSocket.js` to pass version

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Update `sendSyncedSettings`**

Replace lines 269-277:

```javascript
/**
 * Send synced settings to the backend for persistence in settings.json.
 * The backend will broadcast the updated settings to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings) {
    return sendWsMessage({ type: 'update_synced_settings', settings })
}
```

With:

```javascript
/**
 * Send synced settings to the backend for persistence in settings.json.
 * The backend will broadcast the updated settings to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @param {number} baseVersion - The settings version this update is based on
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings, baseVersion) {
    return sendWsMessage({ type: 'update_synced_settings', settings, baseVersion })
}
```

- [ ] **Step 2: Update `synced_settings_updated` handler**

Replace lines 815-821:

```javascript
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings)
                })
                break
```

With:

```javascript
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings, msg.version)
                })
                break
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat: pass settings version through WebSocket send/receive"
```

---

### Task 8: Frontend — Pass version from HTTP fetch in `main.js`

**Files:**
- Modify: `frontend/src/main.js:100-103`

- [ ] **Step 1: Update the fetch destructuring and `applyDefaultSettings` call**

Replace lines 102-103:

```javascript
            const { settings, default_settings, claude_settings_categories, dev_mode } = await resp.json()
            applyDefaultSettings(default_settings, settings, claude_settings_categories, dev_mode)
```

With:

```javascript
            const { settings, version, default_settings, claude_settings_categories, dev_mode } = await resp.json()
            applyDefaultSettings(default_settings, settings, claude_settings_categories, dev_mode, version)
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/main.js
git commit -m "feat: pass settings version from HTTP fetch to applyDefaultSettings"
```

---

### Task 9: Manual verification

- [ ] **Step 1: Restart backend and frontend dev servers**

Ask the user to restart via `uv run ./devctl.py restart`.

- [ ] **Step 2: Verify basic settings sync**

Open two browser tabs. Change a setting (e.g., color scheme or default model) on tab A. Confirm it appears on tab B within ~1 second.

- [ ] **Step 3: Verify no echo in browser console**

Open browser devtools Network tab (WS filter) on tab B. When tab A changes a setting, tab B should receive `synced_settings_updated` but should NOT send `update_synced_settings` back (the nextTick guard should prevent the echo).

- [ ] **Step 4: Verify version in messages**

In the WS traffic, confirm:
- `synced_settings_updated` messages include a `version` field (integer)
- `update_synced_settings` messages from the client include a `baseVersion` field

- [ ] **Step 5: Verify stale write rejection**

Hard to reproduce exactly, but confirm `settings.json` in the data directory has a `_version` field that increments on each settings change.

- [ ] **Step 6: Commit the spec update (set status to Implemented)**

```bash
# Update spec status from Draft to Implemented
git add docs/superpowers/specs/2026-04-17-settings-sync-race-condition-design.md
git commit -m "docs: mark settings sync race condition spec as implemented"
```
