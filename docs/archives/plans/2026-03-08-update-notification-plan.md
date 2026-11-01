# Update Notification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Notify users when a new TwiCC version is available on PyPI, with a persistent toast containing upgrade instructions and a link to the GitHub release notes.

**Architecture:** A new backend periodic task checks PyPI every 15 minutes via httpx. When a newer stable version is found, it broadcasts an `update_available` WebSocket message. The frontend deduplicates notifications via localStorage and shows a persistent toast with version info, `uvx twicc@latest` instructions, and a release notes link.

**Tech Stack:** httpx (existing dep), `packaging.version` (transitive dep), Django Channels broadcast, Notivue toast, localStorage

---

### Task 1: Add `Releases` to `project_urls` in `pyproject.toml`

**Files:**
- Modify: `pyproject.toml:48-51`

**Step 1: Add the Releases URL**

In `pyproject.toml`, add a `Releases` entry to `[project.urls]`:

```toml
[project.urls]
Homepage = "https://github.com/twidi/twicc"
Repository = "https://github.com/twidi/twicc"
Releases = "https://github.com/twidi/twicc/releases"
"Bug Tracker" = "https://github.com/twidi/twicc/issues"
```

**Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add Releases URL to project metadata"
```

---

### Task 2: Create the version check task (backend)

**Files:**
- Create: `src/twicc/version_check_task.py`

**Step 1: Create `version_check_task.py`**

Follow the exact same pattern as `usage_task.py` and `pricing_task.py`:

```python
"""
Background task for checking PyPI for new TwiCC versions.

Periodically queries the PyPI JSON API to detect when a newer stable
version of TwiCC is available, and broadcasts an update_available
message to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from channels.layers import get_channel_layer
from django.conf import settings
from packaging.version import Version

logger = logging.getLogger(__name__)

# Stop event for version check task
_version_check_stop_event: asyncio.Event | None = None

# Interval for version check: 15 minutes in seconds
VERSION_CHECK_INTERVAL = 15 * 60

# PyPI JSON API endpoint
PYPI_URL = "https://pypi.org/pypi/twicc/json"

# GitHub releases URL template
GITHUB_RELEASE_URL = "https://github.com/twidi/twicc/releases/tag/v{version}"


def get_version_check_stop_event() -> asyncio.Event:
    """Get or create the stop event for the version check task."""
    global _version_check_stop_event
    if _version_check_stop_event is None:
        _version_check_stop_event = asyncio.Event()
    return _version_check_stop_event


def stop_version_check_task() -> None:
    """Signal the version check task to stop."""
    global _version_check_stop_event
    if _version_check_stop_event is not None:
        _version_check_stop_event.set()


def _get_latest_stable_version(releases: dict) -> Version | None:
    """Extract the latest stable version from PyPI releases dict.

    Filters out pre-releases (alpha, beta, rc, dev) and returns
    the highest stable version, or None if no stable versions exist.
    """
    stable_versions = []
    for version_str in releases:
        try:
            v = Version(version_str)
            if not v.is_prerelease and not v.is_devrelease:
                stable_versions.append(v)
        except Exception:
            # Skip unparseable version strings
            continue
    return max(stable_versions) if stable_versions else None


async def _check_pypi_version() -> Version | None:
    """Query PyPI for the latest stable version of twicc.

    Returns the latest stable Version, or None if the check fails.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(PYPI_URL)
        response.raise_for_status()
        data = response.json()
        return _get_latest_stable_version(data.get("releases", {}))


async def _broadcast_update_available(latest_version: Version) -> None:
    """Broadcast update_available message to all connected WebSocket clients."""
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "update_available",
                "current_version": settings.APP_VERSION,
                "latest_version": str(latest_version),
                "release_url": GITHUB_RELEASE_URL.format(version=latest_version),
            },
        },
    )


async def start_version_check_task() -> None:
    """
    Background task that periodically checks PyPI for new TwiCC versions.

    Runs until stop event is set:
    - Checks PyPI immediately on startup
    - Then waits VERSION_CHECK_INTERVAL before the next check
    - Broadcasts update_available if a newer stable version is found
    - Handles graceful shutdown via stop event
    """
    stop_event = get_version_check_stop_event()
    current_version = Version(settings.APP_VERSION)

    logger.info("Version check task started (current: %s)", current_version)

    while not stop_event.is_set():
        try:
            latest = await _check_pypi_version()
            if latest and latest > current_version:
                logger.info("New version available: %s (current: %s)", latest, current_version)
                await _broadcast_update_available(latest)
            else:
                logger.debug("Version check: up to date (current: %s, latest: %s)", current_version, latest)
        except Exception as e:
            logger.warning("Version check failed: %s", e)

        # Wait for the next check interval (or until stop event is set)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=VERSION_CHECK_INTERVAL)
        except asyncio.TimeoutError:
            pass

    logger.info("Version check task stopped")
```

**Step 2: Commit**

```bash
git add src/twicc/version_check_task.py
git commit -m "feat: add PyPI version check background task"
```

---

### Task 3: Wire the version check task into the server lifecycle

**Files:**
- Modify: `src/twicc/cli.py`

**Step 1: Add import**

Add to the imports in `cli.py` (after the `usage_task` import, line 60):

```python
from twicc.version_check_task import start_version_check_task, stop_version_check_task
```

**Step 2: Start the task in `run_server()`**

Add right after the `usage_sync_task` creation (after line 208):

```python
version_check_task = asyncio.create_task(start_version_check_task())
```

**Step 3: Stop the task in the `finally` block**

Add in the finally block, before the process manager shutdown (before the "Stopping process manager..." line):

```python
# Clean shutdown of version check task
logger.info("Stopping version check task...")
stop_version_check_task()
await _cancel_task(version_check_task, "Version check task")
```

**Step 4: Commit**

```bash
git add src/twicc/cli.py
git commit -m "feat: wire version check task into server lifecycle"
```

---

### Task 4: Send latest known version on WebSocket connect

**Files:**
- Modify: `src/twicc/version_check_task.py`
- Modify: `src/twicc/asgi.py`

**Step 1: Add a module-level cache in `version_check_task.py`**

Add a module-level variable to cache the latest known version, and a getter function:

```python
# Cache the latest known version (set by the check task, read on WS connect)
_latest_known_version: Version | None = None


def get_update_available_message() -> dict | None:
    """Build an update_available message if a newer version is known.

    Returns the message dict, or None if no update is available.
    Called by the WebSocket consumer on client connect.
    """
    if _latest_known_version is None:
        return None
    current = Version(settings.APP_VERSION)
    if _latest_known_version <= current:
        return None
    return {
        "type": "update_available",
        "current_version": settings.APP_VERSION,
        "latest_version": str(_latest_known_version),
        "release_url": GITHUB_RELEASE_URL.format(version=_latest_known_version),
    }
```

Update the check loop to set `_latest_known_version` when a newer version is found:

```python
# In start_version_check_task, after detecting latest > current_version:
global _latest_known_version
_latest_known_version = latest
```

**Step 2: Send on WebSocket connect in `asgi.py`**

Add to `UpdatesConsumer.connect()`, after the startup_progress block:

```python
# Send update available notification if a newer version is known
if self._should_send("update_available"):
    from twicc.version_check_task import get_update_available_message
    update_msg = get_update_available_message()
    if update_msg:
        await self.send_json(update_msg)
```

**Step 3: Commit**

```bash
git add src/twicc/version_check_task.py src/twicc/asgi.py
git commit -m "feat: send update_available to new WebSocket clients on connect"
```

---

### Task 5: Handle `update_available` in frontend WebSocket handler

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`

**Step 1: Add localStorage helper constants**

At the top of the file (after existing imports):

```javascript
// localStorage key for tracking the last version the user was notified about
const UPDATE_NOTIFIED_VERSION_KEY = 'twicc-update-notified-version'
```

**Step 2: Add the `update_available` case in `handleMessage`**

In the `switch (msg.type)` block, add a new case:

```javascript
case 'update_available':
    handleUpdateAvailable(msg)
    break
```

**Step 3: Implement `handleUpdateAvailable`**

Add the function before the `useWebSocket()` export:

```javascript
/**
 * Handle update_available message from the backend.
 * Shows a persistent toast if the user hasn't been notified for this version yet.
 * Deduplication is done via localStorage.
 */
function handleUpdateAvailable(msg) {
    const { latest_version, release_url } = msg
    if (!latest_version) return

    // Check localStorage: skip if already notified for this version (or newer)
    const lastNotified = localStorage.getItem(UPDATE_NOTIFIED_VERSION_KEY)
    if (lastNotified && lastNotified >= latest_version) return

    // Store the version so we don't notify again
    localStorage.setItem(UPDATE_NOTIFIED_VERSION_KEY, latest_version)

    // Show persistent toast with upgrade instructions
    toast.custom({
        type: 'info',
        title: `TwiCC v${latest_version} is available`,
        duration: Infinity,
        html: `
            <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.25rem;">
                <span>Stop and re-run: <code style="background: var(--wa-color-neutral-100); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em;">uvx twicc@latest</code></span>
                <a href="${release_url}" target="_blank" rel="noopener" style="color: var(--wa-color-primary-600); text-decoration: underline;">View release notes</a>
            </div>
        `,
    })
}
```

**Step 4: Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat: show persistent toast when a new TwiCC version is available"
```
