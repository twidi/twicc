# Update Notification Design

## Goal

Notify users when a new version of TwiCC is available on PyPI. Show a persistent toast with upgrade instructions and a link to release notes. Each version is notified only once per browser (deduplicated via localStorage).

## Architecture

### Backend — `version_check_task.py`

New periodic task following the `pricing_task.py` / `usage_task.py` pattern:

- **Interval:** 15 minutes, with an immediate check at startup
- **API:** `GET https://pypi.org/pypi/twicc/json` via `httpx`
- **Version parsing:** Extract all versions from the `releases` dict, filter out pre-releases using `packaging.version.Version.is_prerelease`, take `max()` of stable versions
- **Comparison:** If `latest_stable > Version(settings.APP_VERSION)`, broadcast via channel layer
- **Lifecycle:** `start_version_check_task()` / `stop_version_check_task()` called from `cli.py`, same as other periodic tasks

WebSocket broadcast message:

```json
{
    "type": "update_available",
    "current_version": "1.1.0",
    "latest_version": "1.2.0",
    "release_url": "https://github.com/twidi/twicc/releases/tag/v1.2.0"
}
```

The backend re-broadcasts on every check cycle as long as the newer version exists. This ensures newly connected clients receive the notification. Deduplication is handled by the frontend.

### Frontend — WebSocket handler + toast

**In `useWebSocket.js`:** New handler for `update_available` message type.

**Deduplication:** Before showing the toast, check `localStorage` key `twicc-last-notified-version`. If the stored version matches or exceeds the received version, skip. Otherwise, store the new version and show the toast.

**Toast:** Persistent (`duration: Infinity`), must be dismissed manually. Content:

- **Title/message:** "A new version of TwiCC is available: v1.2.0"
- **Instructions:** "Stop and re-run: `uvx twicc@latest`"
- **Link:** "View release notes" → `https://github.com/twidi/twicc/releases/tag/v{version}`

### `pyproject.toml` — `project_urls`

Add a `Releases` entry pointing to `https://github.com/twidi/twicc/releases`.

## Data Flow

```
Backend (every 15 min)
  → httpx GET https://pypi.org/pypi/twicc/json
  → parse releases, filter pre-releases, get max stable version
  → if latest > current: broadcast WS "update_available"

Frontend (on WS message)
  → check localStorage "twicc-last-notified-version"
  → if already notified for this version: ignore
  → else: store version in localStorage + show persistent toast
```

## Dependencies

- `httpx` — already in project deps
- `packaging` — available as transitive dependency (via pip/setuptools)
- No new dependencies required
