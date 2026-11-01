# Auto-Show Changelog on Update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically open the changelog dialog when a user updates TwiCC to a new version, without showing it on first install.

**Architecture:** The backend determines whether to show the changelog by comparing `APP_VERSION` against `lastChangelogVersionSeen` stored in `settings.json`. The decision is sent alongside the existing `server_version` WebSocket message. The frontend opens the changelog dialog when instructed, then acknowledges back via WebSocket so the backend persists the new version.

**Tech Stack:** Python (`packaging.version.Version`), Django Channels (WebSocket), Vue 3 (Pinia store + ChangelogDialog)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/twicc/asgi.py` | Modify | Add changelog check logic in `connect()`, add `changelog_seen` handler in `receive_json()` |
| `src/twicc/synced_settings.py` | Read only | Used as-is for `read_synced_settings()` / `write_synced_settings()` |
| `frontend/src/composables/useWebSocket.js` | Modify | Handle `show_changelog_for_version` field, add `sendChangelogSeen()` export |
| `frontend/src/stores/data.js` | Modify | Add `pendingChangelogVersion` state field + setter/clear |
| `frontend/src/components/SettingsPopover.vue` | Modify | Watch `pendingChangelogVersion`, open dialog, send ack on close |

---

### Task 1: Backend — Add changelog version check in `connect()`

**Files:**
- Modify: `src/twicc/asgi.py:508-510` (the `server_version` send block)

**Context:** The `connect()` method currently sends `{"type": "server_version", "version": APP_VERSION}` right after accepting the WebSocket. We add logic to optionally include `show_changelog_for_version`.

- [ ] **Step 1: Add import for `packaging.version.Version`**

At the top of `asgi.py`, add:
```python
from packaging.version import Version, InvalidVersion
```

- [ ] **Step 2: Add helper function to determine changelog display**

Add a module-level function before the `UpdatesConsumer` class:

```python
def _get_changelog_version_to_show() -> str | None:
    """Determine if the changelog should be shown for the current version.

    Returns the version string to show, or None if no changelog should be displayed.

    Rules:
    - No settings.json or empty file → first install → save current version, don't show
    - lastChangelogVersionSeen missing → existing user updating → show current version
    - lastChangelogVersionSeen < APP_VERSION → update → show current version
    - lastChangelogVersionSeen >= APP_VERSION → already seen or downgrade → don't show
    """
    from twicc.synced_settings import read_synced_settings, write_synced_settings

    all_settings = read_synced_settings()
    last_seen = all_settings.get("lastChangelogVersionSeen")

    if last_seen is None:
        if not all_settings:
            # Empty or missing settings.json → first install
            # Save current version so future updates can detect the upgrade
            all_settings["lastChangelogVersionSeen"] = settings.APP_VERSION
            write_synced_settings(all_settings)
            return None
        else:
            # Settings exist but no lastChangelogVersionSeen → existing user, first update
            # Don't save yet — wait for frontend acknowledgment
            return settings.APP_VERSION

    # Compare versions using packaging
    try:
        if Version(settings.APP_VERSION) > Version(last_seen):
            return settings.APP_VERSION
    except InvalidVersion:
        pass

    return None
```

- [ ] **Step 3: Modify the `server_version` message to include changelog info**

Replace the existing block (lines 508-510):
```python
# Send server version to the client (used for auto-reload on version change)
if self._should_send("server_version"):
    await self.send_json({"type": "server_version", "version": settings.APP_VERSION})
```

With:
```python
# Send server version to the client (used for auto-reload on version change)
if self._should_send("server_version"):
    msg = {"type": "server_version", "version": settings.APP_VERSION}
    changelog_version = await sync_to_async(_get_changelog_version_to_show)()
    if changelog_version:
        msg["show_changelog_for_version"] = changelog_version
    await self.send_json(msg)
```

Note: `_get_changelog_version_to_show` does file I/O (read/write `settings.json`), so it must be wrapped with `sync_to_async` since we're in an async context. `sync_to_async` is already imported in this file.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: detect version upgrade and include show_changelog_for_version in server_version WS message"
```

---

### Task 2: Backend — Add `changelog_seen` handler

**Files:**
- Modify: `src/twicc/asgi.py:580-645` (the `receive_json` method)

- [ ] **Step 1: Add the message type to `receive_json` dispatch**

In the `receive_json` method, add a new `elif` branch after the `update_keep_settings` handler (around line 645):

```python
elif msg_type == "changelog_seen":
    await self._handle_changelog_seen(content)
```

Also update the docstring of `receive_json` to include:
```
- changelog_seen: acknowledge that the user has seen the changelog for a version
```

- [ ] **Step 2: Implement `_handle_changelog_seen`**

Add the handler method to `UpdatesConsumer`:

```python
async def _handle_changelog_seen(self, content: dict) -> None:
    """Persist that the user has seen the changelog for the given version."""
    version = content.get("version")
    if not version:
        return

    def _persist():
        from twicc.synced_settings import read_synced_settings, write_synced_settings
        all_settings = read_synced_settings()
        all_settings["lastChangelogVersionSeen"] = version
        write_synced_settings(all_settings)

    await sync_to_async(_persist)()
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat: add changelog_seen WS handler to persist last seen changelog version"
```

---

### Task 3: Frontend — Add `pendingChangelogVersion` to data store

**Files:**
- Modify: `frontend/src/stores/data.js`

**Context:** `data.js` already has `currentVersion` (set by `setCurrentVersion()`). We add a sibling field for the pending changelog.

- [ ] **Step 1: Add state field and actions**

In the `state` of the data store, add:
```javascript
pendingChangelogVersion: null,
```

Add two actions:
```javascript
setPendingChangelogVersion(version) {
    this.pendingChangelogVersion = version
},
clearPendingChangelogVersion() {
    this.pendingChangelogVersion = null
},
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat: add pendingChangelogVersion state to data store"
```

---

### Task 4: Frontend — Handle `show_changelog_for_version` in WebSocket handler

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Update `server_version` case in `handleMessage`**

In the `server_version` case (lines 572-581), after `store.setCurrentVersion(msg.version)`, add:

```javascript
// Auto-show changelog on version update (backend decides when)
if (msg.show_changelog_for_version) {
    store.setPendingChangelogVersion(msg.show_changelog_for_version)
}
```

- [ ] **Step 2: Add `sendChangelogSeen` export function**

Near the other `send*` exports (around line 264), add:

```javascript
/**
 * Acknowledge that the user has seen the forced changelog for a version.
 * @param {string} version - The version that was displayed
 * @returns {boolean} - True if message was sent
 */
export function sendChangelogSeen(version) {
    return sendWsMessage({
        type: 'changelog_seen',
        version
    })
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat: handle show_changelog_for_version and add sendChangelogSeen"
```

---

### Task 5: Frontend — Wire up auto-open in SettingsPopover

**Files:**
- Modify: `frontend/src/components/SettingsPopover.vue`

**Context:** SettingsPopover is always mounted when authenticated (present in both HomeView and ProjectView). It already holds `<ChangelogDialog ref="changelogDialogRef" />` and has the `openChangelog()` function.

- [ ] **Step 1: Add import and reactive tracking**

Add import:
```javascript
import { sendChangelogSeen } from '../composables/useWebSocket'
```

Add a local ref to track forced opens:
```javascript
const forcedChangelogOpen = ref(false)
```

- [ ] **Step 2: Add watcher on `pendingChangelogVersion`**

```javascript
watch(() => dataStore.pendingChangelogVersion, (version) => {
    if (version) {
        forcedChangelogOpen.value = true
        changelogDialogRef.value?.open()
    }
})
```

- [ ] **Step 3: Add close handler for changelog dialog**

```javascript
function onChangelogClose() {
    if (forcedChangelogOpen.value) {
        forcedChangelogOpen.value = false
        const version = dataStore.pendingChangelogVersion
        if (version) {
            sendChangelogSeen(version)
        }
        dataStore.clearPendingChangelogVersion()
    }
}
```

- [ ] **Step 4: Bind the close handler on the ChangelogDialog**

Update the template — the ChangelogDialog wraps a `<wa-dialog>`, so we need to listen for its hide event. Since ChangelogDialog's root is a `<wa-dialog>`, we can listen on the component itself:

```html
<ChangelogDialog ref="changelogDialogRef" @wa-after-hide="onChangelogClose" />
```

If `@wa-after-hide` doesn't bubble through the component boundary (Web Awesome custom events on shadow DOM), an alternative is to add an `@close` emit from ChangelogDialog. Check which approach works — `wa-after-hide` should work since `wa-dialog` emits it and Vue captures it on the component wrapper element.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SettingsPopover.vue
git commit -m "feat: auto-open changelog dialog on version update with WS acknowledgment"
```

---

## Edge Cases Handled

- **First install:** Empty `settings.json` → backend writes `lastChangelogVersionSeen`, no `show_changelog_for_version` sent.
- **Existing user, first update to 1.3+:** `settings.json` has keys but no `lastChangelogVersionSeen` → show changelog.
- **Subsequent updates:** `Version(APP_VERSION) > Version(lastChangelogVersionSeen)` → show changelog.
- **Downgrade:** `Version` comparison returns false → no changelog.
- **User closes without ack (page reload):** `lastChangelogVersionSeen` not updated → backend sends `show_changelog_for_version` again on next connect. This is correct.
- **Second tab after ack:** `lastChangelogVersionSeen` already updated → no `show_changelog_for_version`.
- **Invalid version strings:** Caught by `InvalidVersion` exception → silently skipped.
- **HMR reconnect:** `__hmrState.serverVersion` comparison triggers reload dialog before changelog logic would re-run, so no conflict.
