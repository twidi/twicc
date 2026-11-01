# Live Model & Permission Mode Update During Active Process

## Problem

Currently, the model and permission mode dropdowns are disabled when a Claude process is active. The Claude Agent SDK provides `set_model()` and `set_permission_mode()` methods that allow changing these settings on a live process. We want to expose this capability to the user.

## Behavior

### When a process IS active (`assistant_turn` or `user_turn`)

- Dropdowns remain enabled (disabled only during `starting`)
- Track "active" values (what the live SDK process is currently using)
- Compare selected dropdown values against active values to detect changes

**If text is present:** settings are sent alongside the message on send (existing flow), and `set_model`/`set_permission_mode` are called on the SDK client before forwarding the message.

**If text is empty and settings changed:** an "Update..." button appears after the dropdowns:
- "Update model" — only model changed
- "Update permissions" — only permission mode changed
- "Update model & permissions" — both changed

Clicking this button sends a `send_message` with empty text. The backend applies settings via SDK methods without calling `query()`.

### When no process is active

Behavior is unchanged. Dropdowns reflect session/default values, no "Update..." button.

## Frontend Changes (`MessageInput.vue`)

1. **Remove `isDropdownsDisabled`** — replace with `isStarting` (disabled only during `starting`)
2. **Add `activeModel` / `activePermissionMode` refs** — track what the live process uses
3. **Add computed `hasModelChanged`, `hasPermissionChanged`, `hasSettingsChanged`** — compare selected vs active, only when process is active
4. **Add `showUpdateButton` computed** — `hasSettingsChanged && processIsActive && !messageText.trim()`
5. **Add `updateButtonLabel` computed** — dynamic label based on what changed
6. **Add `handleUpdateSettings()` handler** — sends `send_message` with `text: ''`
7. **Modify `handleSend()`** — allow empty text when `hasSettingsChanged`; after successful send, sync active values
8. **Adjust watchers** — don't overwrite user's dropdown selection when process is active

## Backend Changes

### `process.py` — `ClaudeProcess`

Add two methods that call through to the SDK client:
- `set_permission_mode(mode)` — calls `self._client.set_permission_mode(mode)`, updates `self.permission_mode`
- `set_model(model)` — calls `self._client.set_model(model)`, updates `self.selected_model`

### `manager.py` — `ProcessManager.send_to_session`

When process is alive (`USER_TURN` or `ASSISTANT_TURN`):
1. Call `process.set_permission_mode()` if value differs from current
2. Call `process.set_model()` if value differs from current
3. If `text` is empty and no attachments, return (settings-only update)
4. Otherwise, call `process.send()` as before

### `asgi.py` — `_handle_send_message`

- Allow `text` to be empty (or missing) for settings-only updates on existing sessions with an active process
- Still require `text` for new sessions (drafts)
- Update DB values as before
