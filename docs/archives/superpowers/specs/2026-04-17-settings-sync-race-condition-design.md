# Fix Settings Sync Race Condition Between Multiple Clients

**Date:** 2026-04-17
**Status:** Draft
**Scope:** Frontend + Backend

## Context

When multiple browser tabs or devices have TwiCC open, synced settings (default model, permission mode, etc.) are synchronized via WebSocket. Two bugs cause stale settings to overwrite newer ones:

1. **Broken guard flag:** `applySyncedSettings()` sets `_isApplyingRemoteSettings = true` and clears it synchronously. But Vue watchers run asynchronously (next flush). By the time the outgoing watcher fires, the flag is already `false`, so the guard never blocks. Every incoming settings update is echoed back to the backend.

2. **No versioning:** The backend does a blind `existing.update(synced_settings)` with no conflict detection. Last-write-wins means stale values from a reconnecting client (or from the HTTP fetch / WebSocket race on page reload) can overwrite newer settings.

**Concrete failure scenario:**
1. Tab A and Tab B connected, `defaultModel = "opus"`
2. Tab B disconnects (network blip, laptop lid)
3. Tab A changes `defaultModel` to `"sonnet"`, backend writes it
4. Tab B reloads → HTTP fetch returns `"sonnet"` at T0
5. Tab A changes `defaultModel` to `"haiku"` between T0 and T1
6. Tab B's `initSettings()` applies the stale `"sonnet"` from the HTTP fetch
7. The broken watcher sends `"sonnet"` back to the backend → overwrites `"haiku"`
8. WebSocket pushes `"sonnet"` to all clients → Tab A's change is lost

## Fix 1: nextTick Guard (Frontend)

**File:** `frontend/src/stores/settings.js`

### Change

Replace the synchronous flag clear with a `nextTick` deferred clear:

```js
import { nextTick } from 'vue'

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
    nextTick(() => { this._isApplyingRemoteSettings = false })
}
```

### Why it works

In Vue 3, `nextTick` chains onto the `currentFlushPromise`. The execution order is:

1. `applySyncedSettings`: flag = `true`, mutations happen, Vue schedules the watcher
2. `nextTick` callback is queued after the flush
3. Synchronous code returns
4. Vue flush: watcher runs, sees flag `true` → returns immediately (no echo)
5. Post-flush: `nextTick` callback runs → flag = `false`

The watcher's flag check (`if (store._isApplyingRemoteSettings) return`) is synchronous at the top of the callback, before the `await import(...)`, so the async nature of the callback doesn't affect the guard.

**Note on the async call site:** In `useWebSocket.js`, `applySyncedSettings` is called inside a `.then()` after a dynamic import (`import('../stores/settings').then(...)`). Since the module is already cached after the first import, the `.then()` resolves on the next microtask, which still precedes Vue's flush. The timing analysis holds regardless of the async entry point.

## Fix 2: Version Counter (Backend + Frontend)

### Settings file format

A `_version` field (integer) is added to `settings.json`:

```json
{
    "_version": 5,
    "defaultModel": "sonnet",
    "titleGenerationEnabled": true
}
```

**Backward compatibility:** Existing files without `_version` are treated as version 0. The first write creates `_version: 1`.

### Backend — `synced_settings.py`

- `read_synced_settings()`: Returns settings dict including `_version`. If absent from file, defaults to `0`.
- `write_synced_settings()`: Writes the dict as-is (caller manages `_version`). The cache is updated as before.
- `_version` is NOT added to `SYNCED_SETTINGS_DEFAULTS` (it is metadata, not a setting).
- New helper: `prepare_settings_for_client(settings) -> (clean_settings, version)`: pops `_version` from a settings dict copy and returns both. Used by all code that sends settings to the frontend to avoid repeating the stripping logic.

### Backend — Thread safety

`_handle_update_synced_settings` performs a read-check-write cycle inside `sync_to_async`, which dispatches to a thread pool. Two concurrent settings updates from different clients could interleave and corrupt the version counter.

**Solution:** Add a `threading.Lock` in `synced_settings.py` that wraps the read-check-write cycle. All write paths must acquire this lock:

```python
import threading

_settings_lock = threading.Lock()
```

Callers use it like:
```python
with _settings_lock:
    existing = read_synced_settings()
    # ... check version, merge, increment ...
    write_synced_settings(existing)
```

The lock is in `synced_settings.py` because that module owns the file and cache. All write paths in `asgi.py` acquire it via a helper or directly.

### Backend — `asgi.py`

**WebSocket protocol changes:**

Outgoing (`synced_settings_updated`):
```json
{ "type": "synced_settings_updated", "settings": { ... }, "version": 5 }
```
The `version` is a top-level field, not inside `settings`. The `_version` key is stripped from the `settings` dict before sending (via `prepare_settings_for_client`).

Incoming (`update_synced_settings`):
```json
{ "type": "update_synced_settings", "settings": { ... }, "baseVersion": 5 }
```

**`connect()` handler:** Sends `version` alongside settings (extracted via `prepare_settings_for_client`).

**`_handle_update_synced_settings()` logic:**

```
acquire _settings_lock:
    read existing settings → current_version = existing["_version"] (default 0)

    if baseVersion is not None AND baseVersion < current_version:
        → REJECTED (stale write)
        → release lock
        → send synced_settings_updated to THIS client only (with current settings + version)
        → do NOT broadcast, do NOT write to file
    else:
        → ACCEPTED
        → merge: existing.update(incoming_settings)
        → set existing["_version"] = current_version + 1
        → write to file
        → release lock
        → broadcast synced_settings_updated to ALL clients (with incoming settings + new version)
```

The broadcast sends the **full synced settings dict** received from the client (all `SYNCED_SETTINGS_KEYS`) plus the new `version`. This matches the existing behavior: the frontend watcher always collects all synced keys into the outgoing payload, so the broadcast is always a complete snapshot of synced settings.

When `baseVersion` is `None` (defensive safety during rolling upgrades), the write is always accepted.

**Other write paths that must be version-aware:**

These paths write backend-only keys (changelog tracking) and must preserve and increment `_version`:

1. **`_resolve_changelog_versions()`** (lines 430-484): Called at startup. Reads settings, updates changelog version keys, writes back. Must acquire `_settings_lock`, preserve `_version`, and increment it.

2. **`_handle_changelog_seen()`** (lines 1419-1432): Called when user dismisses changelog dialog. Reads settings, updates `lastChangelogVersionSeen`, writes back. Must acquire `_settings_lock`, preserve `_version`, and increment it.

These writes don't broadcast to clients (they write backend-only keys that the frontend doesn't track). The version increment ensures that any concurrent frontend write will be correctly ordered.

**HTTP endpoint `/api/settings/`:** Includes `version` in the response (via `prepare_settings_for_client`), alongside `settings` and `default_settings`.

### Frontend — `settings.js`

- New **module-level variable**: `let _settingsVersion = 0` (parallels `_pendingSyncedSettings`). NOT in the store's `state()` — this avoids making it reactive, which would be unnecessary and could interfere with the deep watcher on synced settings.
- `applySyncedSettings(remoteSettings, version)`: accepts an optional `version` parameter. Rejects the update if the incoming `version` is older than `_settingsVersion` (guards against HTTP/WS ordering races). If accepted and `version` is provided, updates `_settingsVersion`.
- The outgoing watcher includes `baseVersion: _settingsVersion` in the payload sent via `sendSyncedSettings()`.

### Frontend — `useWebSocket.js`

- `synced_settings_updated` handler: passes `msg.version` to `applySyncedSettings()`:
  ```js
  import('../stores/settings').then(({ useSettingsStore }) => {
      useSettingsStore().applySyncedSettings(msg.settings, msg.version)
  })
  ```
- `sendSyncedSettings(synced, baseVersion)`: accepts `baseVersion` and includes it in the WebSocket message.

### Frontend — `main.js`

- The HTTP fetch of `/api/settings/` now receives `version` in the response.
- `applyDefaultSettings` gets a new parameter: `version`. It stores it in a module-level `_pendingSettingsVersion` alongside `_pendingSyncedSettings`.
- `initSettings()` passes `_pendingSettingsVersion` to `store.applySyncedSettings(_pendingSyncedSettings, _pendingSettingsVersion)`.

## Interaction of the Two Fixes

The fixes are complementary:

| Scenario | nextTick alone | Version alone | Both |
|----------|---------------|---------------|------|
| Echo on incoming settings | Blocked | Echo accepted (same values, harmless round-trip) | Blocked |
| Stale write from HTTP race | Not protected | Rejected by version check | Rejected + no echo |
| Stale write from old localStorage | Not protected | Rejected by version check | Rejected + no echo |

Without nextTick, the version counter still prevents data loss but generates unnecessary round-trips (echo → accept with same values → broadcast). With both, there is zero superfluous traffic and a guarantee of consistency.

## Files Modified

| File | Changes |
|------|---------|
| `frontend/src/stores/settings.js` | `nextTick` in `applySyncedSettings`, `_settingsVersion` state, version param, watcher sends `baseVersion` |
| `frontend/src/composables/useWebSocket.js` | Pass `version` to `applySyncedSettings`, include `baseVersion` in outgoing messages |
| `frontend/src/main.js` | Store `version` from HTTP fetch, pass to `initSettings` via `applyDefaultSettings` |
| `src/twicc/synced_settings.py` | Handle `_version` field in read/write, `_settings_lock`, `prepare_settings_for_client` helper |
| `src/twicc/asgi.py` | Version check in `_handle_update_synced_settings`, send `version` on connect, version-aware changelog writes, use lock on all write paths |
| `src/twicc/views.py` | Include `version` in `/api/settings/` response via `prepare_settings_for_client` |
