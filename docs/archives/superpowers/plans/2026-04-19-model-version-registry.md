# Model Version Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support multiple Claude model versions (latest + older) with auto-retirement, 1M context compatibility, and a versioned model selector in both global and per-session settings.

**Architecture:** A backend `NamedTuple` registry defines available model versions with metadata (full SDK name, retirement date, 1M support, latest flag). The registry is served to the frontend via the bootstrap API. A daily async task auto-retires expired versions by updating settings and broadcasting changes. The frontend model dropdowns show latest models first, then older versions grouped below a divider with retirement dates.

**Tech Stack:** Python (NamedTuple registry, asyncio periodic task), Django (model field, views, WebSocket broadcasts), Vue.js 3 (updated dropdowns, forced-setting logic), existing synced settings + process manager infrastructure.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/twicc/model_registry.py` | **Create** | NamedTuple definition, registry list, lookup/resolution/upgrade functions |
| `src/twicc/model_retirement_task.py` | **Create** | Daily async task: detect retired models, update settings + sessions, broadcast |
| `src/twicc/agent/process.py` | Modify | `_build_sdk_model()` delegates to `resolve_sdk_model()` |
| `src/twicc/agent/manager.py` | Modify | `_apply_pending_settings()` enforces 1M consistency via registry |
| `src/twicc/cron_restart.py` | Modify | `_collect_restart_data()` enforces 1M consistency via registry |
| `src/twicc/asgi.py` | Modify | Enforce 1M consistency on settings changes from WS |
| `src/twicc/views.py` | Modify | Bootstrap API includes serialized model registry |
| `src/twicc/cli/run.py` | Modify | Start/stop the retirement task in the server lifecycle |
| `frontend/src/constants.js` | Modify | Replace static `MODEL`/`MODEL_LABELS` with `getModelLabel()` function |
| `frontend/src/stores/settings.js` | Modify | Store model registry, update `defaultModel` validation, expose helpers |
| `frontend/src/components/MessageInput.vue` | Modify | Dynamic model dropdown, 1M forcing logic, setting-help |
| `frontend/src/components/SettingsPopover.vue` | Modify | Dynamic default model dropdown, 1M consistency |
| `frontend/src/commands/staticCommands.js` | Modify | Update command palette model items to use registry |
| `frontend/src/composables/useWebSocket.js` | Modify | Handle `model_retirement` message type |
| `frontend/src/main.js` | Modify | Pass `model_registry` from bootstrap to store |

---

## Task 1: Backend Model Registry Module

**Files:**
- Create: `src/twicc/model_registry.py`

This is the core data structure and all pure-function logic around model versions.

- [ ] **Step 1: Create the `ModelVersion` NamedTuple and registry list**

```python
# src/twicc/model_registry.py
"""
Registry of supported Claude model versions.

Each model family (opus, sonnet) has one ``latest`` version and zero or more
older versions with a retirement date.  The ``selected_model`` value stored in
settings and session DB fields uses:
- bare alias for latest: ``"opus"``, ``"sonnet"``
- versioned alias for non-latest: ``"opus-4.5"``, ``"sonnet-4.5"``

When communicating with the SDK, latest aliases are passed as-is (the CLI
resolves them), while versioned aliases are resolved to their ``full_name``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import NamedTuple

logger = logging.getLogger(__name__)


class ModelVersion(NamedTuple):
    """A single supported model version."""
    model: str               # Family alias: "opus", "sonnet"
    version: str             # Short version: "4.6", "4.5"
    full_name: str           # Full SDK model ID: "claude-opus-4-6"
    retirement_date: date | None  # Date after which this version is retired (None for latest)
    latest: bool             # True = current default for this family (unique per model)
    supports_1m: bool        # Whether extended 1M context is available


MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion("opus",   "4.6", "claude-opus-4-6",               retirement_date=None,             latest=True,  supports_1m=True),
    ModelVersion("opus",   "4.5", "claude-opus-4-5-20251101",      retirement_date=date(2026, 11, 24), latest=False, supports_1m=False),
    ModelVersion("sonnet", "4.6", "claude-sonnet-4-6",             retirement_date=None,             latest=True,  supports_1m=True),
    ModelVersion("sonnet", "4.5", "claude-sonnet-4-5-20250929",    retirement_date=date(2026, 9, 29),  latest=False, supports_1m=False),
]
```

- [ ] **Step 2: Add lookup and resolution functions**

```python
# Continue in src/twicc/model_registry.py


def _parse_version(version_str: str) -> list[int]:
    """Parse a version string like "4.5" into a list of ints [4, 5]."""
    return [int(p) for p in version_str.split(".")]


def get_model_version(selected_model: str) -> ModelVersion | None:
    """Look up a ModelVersion by its selected_model value.

    Accepts both bare aliases ("opus") and versioned aliases ("opus-4.5").
    For bare aliases, returns the latest version of that family.
    """
    # Try versioned alias first: "opus-4.5" → model="opus", version="4.5"
    if "-" in selected_model:
        model, version = selected_model.split("-", 1)
        for mv in MODEL_VERSIONS:
            if mv.model == model and mv.version == version:
                return mv
        return None

    # Bare alias: "opus" → latest for that family
    for mv in MODEL_VERSIONS:
        if mv.model == selected_model and mv.latest:
            return mv
    return None


def resolve_sdk_model(selected_model: str | None, context_max: int) -> str | None:
    """Resolve a selected_model + context_max to the string to pass to the SDK.

    - Latest models ("opus", "sonnet"): pass the bare alias (CLI resolves it).
    - Versioned models ("opus-4.5"): pass the full_name from the registry.
    - Appends "[1m]" suffix when context_max is 1M and the model supports it.

    Returns None if selected_model is None or empty.
    """
    if not selected_model:
        return None

    mv = get_model_version(selected_model)
    if mv is None:
        # Unknown model — pass through as-is (backward compat)
        logger.warning("Unknown model '%s', passing through to SDK", selected_model)
        base = selected_model
        supports_1m = True  # assume it does
    elif mv.latest:
        base = mv.model  # bare alias
        supports_1m = mv.supports_1m
    else:
        base = mv.full_name  # full SDK model ID
        supports_1m = mv.supports_1m

    if context_max == 1_000_000 and supports_1m:
        return f"{base}[1m]"
    return base


def is_model_retired(selected_model: str) -> bool:
    """Check if a selected_model value refers to a retired version.

    Latest versions (retirement_date=None) are never considered retired.
    """
    mv = get_model_version(selected_model)
    if mv is None or mv.retirement_date is None:
        return False
    return date.today() > mv.retirement_date


def get_upgrade_target(selected_model: str) -> str | None:
    """Find the next version up for a retired model.

    Returns the selected_model value of the closest higher version in the same
    family.  If the next higher version is the latest, returns the bare alias.
    Returns None if no upgrade is possible (shouldn't happen if registry is
    well-maintained).
    """
    mv = get_model_version(selected_model)
    if mv is None:
        return None

    # Collect all versions of the same family, sorted by version ascending
    family = sorted(
        [v for v in MODEL_VERSIONS if v.model == mv.model],
        key=lambda v: _parse_version(v.version),
    )

    # Find the next version higher than the current one
    current_parts = _parse_version(mv.version)
    for candidate in family:
        if _parse_version(candidate.version) > current_parts:
            return candidate.model if candidate.latest else f"{candidate.model}-{candidate.version}"

    return None


def get_all_selected_model_values() -> list[str]:
    """Return all valid selected_model values (for validation)."""
    result = []
    for mv in MODEL_VERSIONS:
        if mv.latest:
            result.append(mv.model)
        else:
            result.append(f"{mv.model}-{mv.version}")
    return result


def selected_model_supports_1m(selected_model: str | None) -> bool:
    """Check if a selected_model value supports 1M context.

    None means "use default" — caller should resolve to effective model first.
    Unknown models are assumed to support 1M (backward compat).
    """
    if not selected_model:
        return True
    mv = get_model_version(selected_model)
    if mv is None:
        return True
    return mv.supports_1m


def enforce_1m_consistency(selected_model: str | None, context_max: int) -> int:
    """If the model doesn't support 1M, cap context_max to 200K.

    Returns the (possibly adjusted) context_max value.
    """
    if context_max == 1_000_000 and not selected_model_supports_1m(selected_model):
        return 200_000
    return context_max


def serialize_model_registry() -> list[dict]:
    """Serialize the registry for the frontend bootstrap API.

    Returns a list of dicts sorted for dropdown display:
    latest versions first (sorted by model name), then non-latest sorted by
    version descending then model name.
    """
    latest = []
    non_latest = []
    for mv in MODEL_VERSIONS:
        entry = {
            "model": mv.model,
            "version": mv.version,
            "selectedModel": mv.model if mv.latest else f"{mv.model}-{mv.version}",
            "retirementDate": mv.retirement_date.isoformat() if mv.retirement_date else None,
            "latest": mv.latest,
            "supports1m": mv.supports_1m,
        }
        if mv.latest:
            latest.append(entry)
        else:
            non_latest.append(entry)

    # Latest: sort by model name for consistent order
    latest.sort(key=lambda e: e["model"])
    # Non-latest: sort by version descending (highest first), then model name
    non_latest.sort(key=lambda e: ([-int(p) for p in e["version"].split(".")], e["model"]))

    return latest + non_latest
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/model_registry.py
git commit -m "feat: add model version registry with lookup and resolution functions"
```

---

## Task 2: Update `_build_sdk_model` in Process

**Files:**
- Modify: `src/twicc/agent/process.py:990-1005`

Replace the inline `[1m]` suffix logic with the registry's `resolve_sdk_model()`.

- [ ] **Step 1: Update `_build_sdk_model` and `sdk_model`**

In `src/twicc/agent/process.py`, replace lines 990-1005:

```python
def _build_sdk_model(self, selected_model: str | None = None, context_max: int | None = None) -> str | None:
    """Build the full SDK model string from model shorthand and context_max.

    Uses the model registry to resolve versioned models to their full SDK
    name, and appends "[1m]" only when the model supports extended context.
    """
    from twicc.model_registry import resolve_sdk_model

    model = selected_model if selected_model is not None else self.selected_model
    ctx = context_max if context_max is not None else self.context_max
    return resolve_sdk_model(model, ctx)

@property
def sdk_model(self) -> str | None:
    """The current full SDK model string (including context suffix)."""
    return self._build_sdk_model()
```

- [ ] **Step 2: Commit**

```bash
git add src/twicc/agent/process.py
git commit -m "feat: use model registry to resolve SDK model in process"
```

---

## Task 3: Enforce 1M Consistency at All Backend Resolution Points

**Files:**
- Modify: `src/twicc/asgi.py` (in `_handle_send_message` and `_handle_update_synced_settings`)
- Modify: `src/twicc/agent/manager.py:1017-1026` (`_apply_pending_settings`)
- Modify: `src/twicc/cron_restart.py:68-73` (`_collect_restart_data`)

Every place that resolves `session.selected_model` → effective value must also enforce 1M consistency. Additionally, as a safety net, auto-upgrade retired models received from the frontend.

- [ ] **Step 1: Enforce in `_handle_send_message` (asgi.py) — both resolution blocks**

There are TWO places in `_handle_send_message` that resolve null → global default and build an `effective` dict:
- **Existing session path** (~line 866): resolves settings then calls `manager.send_to_session()`
- **New session path** (~line 915): resolves settings then calls `manager.create_session()`

In BOTH `effective` dicts, after building them, add retired model upgrade + 1M enforcement:

```python
from twicc.model_registry import enforce_1m_consistency, get_upgrade_target, is_model_retired

# After building the `effective` dict:
# Safety net: auto-upgrade retired models (frontend should have corrected, but just in case)
if is_model_retired(effective["selected_model"]):
    target = get_upgrade_target(effective["selected_model"])
    if target:
        effective["selected_model"] = target
# Enforce 1M consistency
effective["context_max"] = enforce_1m_consistency(effective["selected_model"], effective["context_max"])
```

This must be done in both blocks (existing session ~line 873 and new session ~line 922).

- [ ] **Step 2: Enforce in `_handle_update_synced_settings` (asgi.py)**

Inside the `_merge_and_write()` function, after merging `synced_settings` into `existing`, if `defaultModel` changed to a model that doesn't support 1M and `defaultContextMax` is 1M, force it to 200K:

```python
from twicc.model_registry import selected_model_supports_1m
from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

if "defaultModel" in synced_settings:
    new_model = existing.get("defaultModel", SYNCED_SETTINGS_DEFAULTS["defaultModel"])
    if not selected_model_supports_1m(new_model) and existing.get("defaultContextMax", 200_000) == 1_000_000:
        existing["defaultContextMax"] = 200_000
```

- [ ] **Step 3: Enforce in `_apply_pending_settings` (manager.py)**

After building the `requested` dict (lines 1019-1026), add:

```python
from twicc.model_registry import enforce_1m_consistency

requested["context_max"] = enforce_1m_consistency(requested["selected_model"], requested["context_max"])
```

- [ ] **Step 4: Enforce in `_collect_restart_data` (cron_restart.py)**

After building the restart data dict (lines 60-74), before `return`:

```python
from twicc.model_registry import enforce_1m_consistency

data["context_max"] = enforce_1m_consistency(data["selected_model"], data["context_max"])
```

(Note: `_collect_restart_data` returns a dict or None — the variable holding the dict must be named. Currently it's built inline in the return statement, so restructure slightly: build into a local var `data`, enforce, then `return data`.)

- [ ] **Step 5: Commit**

```bash
git add src/twicc/asgi.py src/twicc/agent/manager.py src/twicc/cron_restart.py
git commit -m "feat: enforce 1M context consistency at all backend resolution points"
```

---

## Task 4: Bootstrap API — Serve Model Registry

**Files:**
- Modify: `src/twicc/views.py:1961-1985`

- [ ] **Step 1: Add model registry to bootstrap response**

In the `bootstrap()` view, add the import and include the registry in the response:

```python
from twicc.model_registry import serialize_model_registry

# Add to the JsonResponse dict:
"model_registry": serialize_model_registry(),
```

- [ ] **Step 2: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat: include model registry in bootstrap API response"
```

---

## Task 5: Model Retirement Periodic Task

**Files:**
- Create: `src/twicc/model_retirement_task.py`
- Modify: `src/twicc/cli/run.py` (start/stop lifecycle)

A simple async task that runs once per day. Checks for retired model versions and:
- Updates the global default setting if affected
- Updates active processes (running sessions) only
- Broadcasts to frontends so they handle the rest (display, send-time correction)
- Does NOT mass-update sessions in the database

- [ ] **Step 1: Create the retirement task module**

```python
# src/twicc/model_retirement_task.py
"""
Daily async task that detects retired model versions and auto-upgrades.

When a retirement is detected:
1. Global default is updated in synced settings (if affected)
2. Active processes are updated via the existing apply_live_settings machinery
3. A ``model_retirement`` broadcast notifies all frontends (retired model mapping)
   → Frontends handle display correction for non-running sessions on their own
   → No database mass-update of sessions (corrected at render/send time)
"""

import asyncio
import logging
from datetime import date as date_type

logger = logging.getLogger(__name__)

RETIREMENT_CHECK_INTERVAL = 24 * 60 * 60  # 24 hours

_retirement_stop_event: asyncio.Event | None = None


def get_retirement_stop_event() -> asyncio.Event:
    global _retirement_stop_event
    if _retirement_stop_event is None:
        _retirement_stop_event = asyncio.Event()
    return _retirement_stop_event


def stop_model_retirement_task() -> None:
    if _retirement_stop_event is not None:
        _retirement_stop_event.set()


async def start_model_retirement_task() -> None:
    """Run the retirement check loop: once at startup, then every 24 hours."""
    stop_event = get_retirement_stop_event()

    _log_upcoming_retirements()

    # Initial check on startup
    try:
        await _check_and_retire()
    except Exception:
        logger.exception("Error in initial retirement check")

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETIREMENT_CHECK_INTERVAL)
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # Time to check again

        try:
            await _check_and_retire()
        except Exception:
            logger.exception("Error in retirement check cycle")


async def _check_and_retire() -> None:
    """Perform one retirement check cycle.

    1. Build the map of retired selected_model → upgrade target
    2. Update global defaultModel if affected
    3. Update active processes (running sessions) via apply_live_settings
    4. Broadcast ``model_retirement`` to frontends (they handle non-running sessions)
    """
    from channels.layers import get_channel_layer

    from twicc.model_registry import MODEL_VERSIONS, get_upgrade_target, is_model_retired

    # Identify all retired non-latest versions
    retired_models: dict[str, str] = {}  # old selected_model → new selected_model
    for mv in MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        selected = f"{mv.model}-{mv.version}"
        if is_model_retired(selected):
            target = get_upgrade_target(selected)
            if target:
                retired_models[selected] = target

    if not retired_models:
        return

    logger.info("Retired models detected: %s", retired_models)

    # 1. Update global default if affected
    settings_changed = False
    from twicc.synced_settings import _settings_lock, prepare_settings_for_client, read_synced_settings, write_synced_settings

    with _settings_lock:
        current = read_synced_settings()
        default_model = current.get("defaultModel", SYNCED_SETTINGS_DEFAULTS["defaultModel"])
        if default_model in retired_models:
            current["defaultModel"] = retired_models[default_model]
            current["_version"] = current.get("_version", 0) + 1
            write_synced_settings(current)
            settings_changed = True
            logger.info("Updated global defaultModel: %s → %s", default_model, retired_models[default_model])

    # Broadcast global settings update if changed
    if settings_changed:
        channel_layer = get_channel_layer()
        clean, version = prepare_settings_for_client(read_synced_settings())
        await channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "synced_settings_updated",
                    "settings": clean,
                    "version": version,
                },
            },
        )

    # 2. Update active processes (running sessions)
    # Model change is an "idle" setting: apply_live_settings() calls set_model()
    # on the SDK — no process restart needed.
    # - USER_TURN: applied immediately via set_model()
    # - ASSISTANT_TURN: apply_live_settings skips idle changes, so we also
    #   update the session DB row; _apply_pending_settings will pick it up
    #   at the next USER_TURN transition.
    from twicc.agent.manager import get_process_manager
    from twicc.model_registry import selected_model_supports_1m

    manager = get_process_manager()
    for old_model, new_model in retired_models.items():
        for process in manager.get_all_processes():
            if process.selected_model != old_model:
                continue
            ctx = process.context_max
            if ctx == 1_000_000 and not selected_model_supports_1m(new_model):
                ctx = 200_000
            # Update session DB so _apply_pending_settings picks it up if in ASSISTANT_TURN
            from twicc.core.models import Session
            await asyncio.to_thread(
                lambda sid=process.session_id, nm=new_model: (
                    Session.objects.filter(id=sid).update(selected_model=nm)
                )
            )
            try:
                await process.apply_live_settings(process.permission_mode, new_model, ctx)
                logger.info("Upgraded active process %s: %s → %s", process.session_id, old_model, new_model)
            except Exception:
                logger.exception("Failed to apply retirement upgrade to process %s", process.session_id)

    # 3. Broadcast model_retirement to frontends
    # Frontends use this to correct display/settings of non-running sessions
    # at render time (no DB update needed for those)
    channel_layer = get_channel_layer()
    await channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "model_retirement",
                "retired_models": retired_models,
                "default_changed": settings_changed,
            },
        },
    )


def _log_upcoming_retirements() -> None:
    """Log a summary of model versions and upcoming retirements at startup."""
    from twicc.model_registry import MODEL_VERSIONS

    today = date_type.today()
    for mv in MODEL_VERSIONS:
        if mv.retirement_date is None:
            continue
        days_left = (mv.retirement_date - today).days
        if days_left <= 0:
            logger.warning("Model %s-%s is RETIRED (since %s)", mv.model, mv.version, mv.retirement_date)
        elif days_left <= 30:
            logger.warning("Model %s-%s retires in %d days (%s)", mv.model, mv.version, days_left, mv.retirement_date)
        else:
            logger.info("Model %s-%s retires on %s (%d days)", mv.model, mv.version, mv.retirement_date, days_left)
```

- [ ] **Step 2: Integrate into server lifecycle in `run.py`**

In `src/twicc/cli/run.py`, add the import alongside other task imports:
```python
from twicc.model_retirement_task import start_model_retirement_task, stop_model_retirement_task
```

In the task launch section (after line 247), add:
```python
retirement_task = asyncio.create_task(start_model_retirement_task())
```

In the shutdown `finally` block (with the other task shutdowns), add:
```python
logger.info("Stopping model retirement task...")
stop_model_retirement_task()
await _cancel_task(retirement_task, "Model retirement task")
```

- [ ] **Step 3: Commit**

```bash
git add src/twicc/model_retirement_task.py src/twicc/cli/run.py
git commit -m "feat: add daily model retirement task with auto-upgrade"
```

---

## Task 6: Frontend — Constants and Store Updates

**Files:**
- Modify: `frontend/src/constants.js:207-220`
- Modify: `frontend/src/stores/settings.js:6,99`
- Modify: `frontend/src/main.js`

- [ ] **Step 1: Replace static MODEL constants in constants.js**

Remove `MODEL` and `MODEL_LABELS`:
```javascript
export const MODEL = {
    OPUS: 'opus',
    SONNET: 'sonnet',
}

export const MODEL_LABELS = {
    [MODEL.OPUS]: 'Opus',
    [MODEL.SONNET]: 'Sonnet',
}
```

Replace with:
```javascript
/**
 * Build a human-friendly label for a selected_model value.
 * "opus" → "Opus", "opus-4.5" → "Opus 4.5", "sonnet" → "Sonnet"
 */
export function getModelLabel(selectedModel) {
    if (!selectedModel) return ''
    if (selectedModel.includes('-')) {
        const [model, version] = selectedModel.split('-', 2)
        return `${model.charAt(0).toUpperCase() + model.slice(1)} ${version}`
    }
    return selectedModel.charAt(0).toUpperCase() + selectedModel.slice(1)
}
```

- [ ] **Step 2: Update settings store — model registry storage and validation**

In `frontend/src/stores/settings.js`:

Add module-level variable for the registry (near other module-level state):
```javascript
let _modelRegistry = []
```

Add exported functions:
```javascript
export function setModelRegistry(registry) {
    _modelRegistry = registry
}

export function getModelRegistry() {
    return _modelRegistry
}

export function modelSupports1m(selectedModel) {
    if (!selectedModel) return true  // null = default = latest = supports 1M
    const entry = _modelRegistry.find(e => e.selectedModel === selectedModel)
    return entry ? entry.supports1m : true
}

/**
 * If selectedModel is retired, return the upgrade target. Otherwise null.
 * Used by frontend to correct stale session settings at render/send time.
 */
export function getRetiredModelUpgrade(selectedModel) {
    if (!selectedModel) return null
    const entry = _modelRegistry.find(e => e.selectedModel === selectedModel)
    if (!entry || entry.latest || !entry.retirementDate) return null
    if (new Date(entry.retirementDate + 'T00:00:00') >= new Date()) return null
    // Find next higher version in same family
    const family = _modelRegistry
        .filter(e => e.model === entry.model)
        .sort((a, b) => {
            const av = a.version.split('.').map(Number)
            const bv = b.version.split('.').map(Number)
            return av[0] - bv[0] || (av[1] ?? 0) - (bv[1] ?? 0)
        })
    const currentParts = entry.version.split('.').map(Number)
    for (const candidate of family) {
        const cp = candidate.version.split('.').map(Number)
        if (cp[0] > currentParts[0] || (cp[0] === currentParts[0] && (cp[1] ?? 0) > (currentParts[1] ?? 0))) {
            return candidate.selectedModel
        }
    }
    return null
}
```

Update the `defaultModel` validator (line 99). Replace:
```javascript
defaultModel: (v) => Object.values(MODEL).includes(v),
```
With:
```javascript
defaultModel: (v) => typeof v === 'string' && v.length > 0,
```

Remove `MODEL` from the import line (line 6). The `MODEL` constant is no longer used in this file.

- [ ] **Step 3: Load registry from bootstrap in main.js**

In `frontend/src/main.js`, after the existing `applyDefaultSettings(...)` call:

```javascript
import { setModelRegistry } from './stores/settings.js'
// ...
setModelRegistry(bootstrapData.model_registry || [])
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/constants.js frontend/src/stores/settings.js frontend/src/main.js
git commit -m "feat: replace static MODEL constants with registry-based getModelLabel and store"
```

---

## Task 7: Frontend — Update Global Settings Model Dropdown (SettingsPopover)

**Files:**
- Modify: `frontend/src/components/SettingsPopover.vue`

- [ ] **Step 1: Update imports**

Replace `MODEL, MODEL_LABELS` in the import (line 8) with nothing (remove them). Add new imports:
```javascript
import { getModelLabel } from '../constants'
import { getModelRegistry, modelSupports1m } from '../stores/settings'
```

- [ ] **Step 2: Replace static modelOptions with registry-based computed**

Replace lines 261-264:
```javascript
const modelOptions = Object.values(MODEL).map(value => ({
    value,
    label: MODEL_LABELS[value],
}))
```
With:
```javascript
const modelRegistryOptions = computed(() => {
    const registry = getModelRegistry()
    return {
        latest: registry.filter(e => e.latest),
        older: registry.filter(e => !e.latest),
    }
})
```

- [ ] **Step 3: Add date formatter and 1M support computed**

```javascript
function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}

const defaultModelSupports1m = computed(() => modelSupports1m(defaultModel.value))
```

Where `defaultModel` is the existing reactive ref for the global default model.

- [ ] **Step 4: Update the model select template**

Replace lines 694-706 (the `wa-select` for "Default model"):

```html
<wa-select
    :value.prop="defaultModel"
    @change="onDefaultModelChange"
    size="small"
>
    <wa-option
        v-for="entry in modelRegistryOptions.latest"
        :key="entry.selectedModel"
        :value="entry.selectedModel"
    >
        {{ getModelLabel(entry.selectedModel) }} (latest: {{ entry.version }})
    </wa-option>
    <wa-divider v-if="modelRegistryOptions.older.length"></wa-divider>
    <wa-option
        v-for="entry in modelRegistryOptions.older"
        :key="entry.selectedModel"
        :value="entry.selectedModel"
    >
        {{ getModelLabel(entry.selectedModel) }} (until {{ formatRetirementDate(entry.retirementDate) }})
    </wa-option>
</wa-select>
```

- [ ] **Step 5: Enforce 1M consistency on model change**

Update `onDefaultModelChange`:
```javascript
function onDefaultModelChange(event) {
    const newModel = event.target.value
    store.setDefaultModel(newModel)
    if (!modelSupports1m(newModel) && store.getDefaultContextMax === CONTEXT_MAX.EXTENDED) {
        store.setDefaultContextMax(CONTEXT_MAX.DEFAULT)
    }
}
```

In the context max select, disable the 1M option when the model doesn't support it:
```html
<wa-option
    v-for="option in contextMaxOptions"
    :key="option.value"
    :value="option.value"
    :disabled="option.value === String(CONTEXT_MAX.EXTENDED) && !defaultModelSupports1m"
>
    {{ option.label }}{{ option.value === String(CONTEXT_MAX.EXTENDED) && !defaultModelSupports1m ? ' (not available)' : '' }}
</wa-option>
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SettingsPopover.vue
git commit -m "feat: dynamic model dropdown with versions and 1M consistency in global settings"
```

---

## Task 8: Frontend — Update Per-Session Model Dropdown (MessageInput)

**Files:**
- Modify: `frontend/src/components/MessageInput.vue`

- [ ] **Step 1: Update imports**

Remove `MODEL, MODEL_LABELS` from the import (line 11). Add:
```javascript
import { getModelLabel } from '../constants'
import { getModelRegistry, modelSupports1m } from '../stores/settings'
```

- [ ] **Step 2: Replace static modelOptions**

Replace lines 132-135:
```javascript
const modelOptions = Object.values(MODEL).map(value => ({
    value,
    label: MODEL_LABELS[value],
}))
```
With:
```javascript
const modelRegistryOptions = computed(() => {
    const registry = getModelRegistry()
    return {
        latest: registry.filter(e => e.latest),
        older: registry.filter(e => !e.latest),
    }
})
```

Add date formatter (same as SettingsPopover):
```javascript
function formatRetirementDate(isoDate) {
    return new Date(isoDate + 'T00:00:00').toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
    })
}
```

- [ ] **Step 3: Update `defaultModelLabel` and `settingsSummaryParts`**

Replace line 162:
```javascript
const defaultModelLabel = computed(() => MODEL_LABELS[settingsStore.getDefaultModel])
```
With:
```javascript
const defaultModelLabel = computed(() => getModelLabel(settingsStore.getDefaultModel))
```

In `settingsSummaryParts` (line 212), replace:
```javascript
const modelLabel = MODEL_LABELS[effectiveModel]
```
With:
```javascript
const modelLabel = getModelLabel(effectiveModel)
```

- [ ] **Step 4: Add 1M forcing logic based on model**

```javascript
const isContextMaxForcedByModel = computed(() => {
    const effectiveModel = selectedModel.value ?? settingsStore.getDefaultModel
    return !modelSupports1m(effectiveModel)
})

// Watch: auto-reset to 200K when model doesn't support 1M
watch(isContextMaxForcedByModel, (forced) => {
    if (forced) {
        const effectiveCtx = selectedContextMax.value ?? settingsStore.getDefaultContextMax
        if (effectiveCtx === CONTEXT_MAX.EXTENDED) {
            selectedContextMax.value = CONTEXT_MAX.DEFAULT
            activeContextMax.value = CONTEXT_MAX.DEFAULT
        }
    }
})
```

- [ ] **Step 5: Update the model select template**

Replace lines 1436-1448:
```html
<wa-select
    :value.prop="selectedModel === null ? DEFAULT_SENTINEL : selectedModel"
    @change="selectedModel = $event.target.value === DEFAULT_SENTINEL ? null : $event.target.value"
    size="small"
    :disabled="isStarting"
>
    <wa-option :value="DEFAULT_SENTINEL">Default: {{ defaultModelLabel }}</wa-option>
    <small class="select-group-label">Force to:</small>
    <wa-option
        v-for="entry in modelRegistryOptions.latest"
        :key="entry.selectedModel"
        :value="entry.selectedModel"
    >
        {{ getModelLabel(entry.selectedModel) }} (latest: {{ entry.version }})
    </wa-option>
    <wa-divider v-if="modelRegistryOptions.older.length"></wa-divider>
    <wa-option
        v-for="entry in modelRegistryOptions.older"
        :key="entry.selectedModel"
        :value="entry.selectedModel"
    >
        {{ getModelLabel(entry.selectedModel) }} (until {{ formatRetirementDate(entry.retirementDate) }})
    </wa-option>
</wa-select>
<a v-if="selectedModel !== null" class="reset-setting-link" @click.prevent="selectedModel = null">Reset to default: {{ defaultModelLabel }}</a>
```

- [ ] **Step 6: Update the context select with model-based forcing**

Update `:disabled` on the context `wa-select` (line 1458):
```html
:disabled="isStarting || isContextMaxForced || isContextMaxForcedByModel"
```

Add a new `setting-help` span below the existing ones (after line 1466):
```html
<span v-if="isContextMaxForced" class="setting-help">Forced to 1M: context usage exceeds 85% of 200K.</span>
<span v-else-if="isContextMaxForcedByModel" class="setting-help">1M not available for this model version.</span>
<a v-else-if="selectedContextMax !== null" class="reset-setting-link" @click.prevent="selectedContextMax = null">Reset to default: {{ defaultContextMaxLabel }}</a>
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/MessageInput.vue
git commit -m "feat: registry-based model dropdown with 1M forcing in per-session settings"
```

---

## Task 9: Frontend — Update Command Palette (staticCommands.js)

**Files:**
- Modify: `frontend/src/commands/staticCommands.js:20-21,412-417`

- [ ] **Step 1: Update imports and command items**

Replace the imports (lines 20-21):
```javascript
    MODEL,
    MODEL_LABELS,
```
With:
```javascript
    getModelLabel,
```

Add a top-level import for `getModelRegistry`:
```javascript
import { getModelRegistry } from '../stores/settings'
```

Replace the model command items (lines 412-417):
```javascript
items: () => Object.values(MODEL).map(value => ({
    id: value,
    label: MODEL_LABELS[value],
    action: () => settings.setDefaultModel(value),
    active: settings.defaultModel === value,
})),
```
With:
```javascript
items: () => getModelRegistry().map(entry => ({
    id: entry.selectedModel,
    label: entry.latest
        ? `${getModelLabel(entry.selectedModel)} (latest: ${entry.version})`
        : `${getModelLabel(entry.selectedModel)} (until ${entry.retirementDate})`,
    action: () => settings.setDefaultModel(entry.selectedModel),
    active: settings.defaultModel === entry.selectedModel,
})),
```

Note: `items` is a lazy function evaluated at call time, so the registry is available. The file uses ES modules (Vite), so the import must be a static ES import at the top level.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/commands/staticCommands.js
git commit -m "feat: update command palette model items to use registry"
```

---

## Task 10: Frontend — Handle Retirement Broadcast and Session-Level Correction

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js`
- Modify: `frontend/src/components/MessageInput.vue` (add retired model correction)

The backend only updates active processes. For all other sessions, the frontend handles it:
- On `model_retirement` broadcast: show a toast
- At session render time (MessageInput): check if the session's `selected_model` is retired, auto-correct it

- [ ] **Step 1: Handle `model_retirement` message in WebSocket**

Add a static import at the top of `useWebSocket.js`:
```javascript
import { getModelLabel } from '../constants'
```

Then in the message handler switch/case:
```javascript
case 'model_retirement': {
    const { retired_models } = msg

    // Show persistent warning toast
    const retiredList = Object.entries(retired_models)
        .map(([old, newM]) => `${getModelLabel(old)} → ${getModelLabel(newM)}`)
        .join(', ')

    toast.warning(
        `Model version retired: ${retiredList}. Settings updated automatically.`,
        { duration: Infinity }
    )
    break
}
```

Note: `handleMessage` is NOT async, so no `await import(...)`. `getModelLabel` is a pure utility from `constants.js` — safe static import. `toast` is already available in this file.

- [ ] **Step 2: Auto-correct retired model at session render time in MessageInput**

In `MessageInput.vue`, add a watcher (or computed) that detects if the session's `selected_model` is retired and auto-corrects it:

```javascript
import { getRetiredModelUpgrade } from '../stores/settings'

// When the session loads or selectedModel changes, check if it's retired
watch(
    () => selectedModel.value,
    (model) => {
        if (!model) return
        const upgrade = getRetiredModelUpgrade(model)
        if (upgrade) {
            selectedModel.value = upgrade
            activeModel.value = upgrade
        }
    },
    { immediate: true }
)
```

This ensures that when a user opens any session with a retired model, the setting is corrected immediately. The corrected value is then sent to the backend on the next message send. Also show a `setting-help` message:

```html
<span v-if="isModelRetired" class="setting-help">
    Model version retired, upgraded to {{ getModelLabel(selectedModel) }}.
</span>
```

With the computed:
```javascript
const isModelRetired = computed(() => {
    const model = selectedModel.value
    return model ? !!getRetiredModelUpgrade(model) : false
})
```

Note: the watcher auto-corrects immediately, so `isModelRetired` will only flash briefly. But it's still useful to show the help text after auto-correction (comparing the original session DB value to the corrected one). Alternatively, the watcher can set a flag that the help text reads. Implementer should decide the best UX — the key requirement is that the value is corrected before send.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useWebSocket.js frontend/src/components/MessageInput.vue
git commit -m "feat: handle model retirement broadcast and auto-correct retired models at session render"
```

---

## Task 11: Clean Up Remaining Old MODEL References

**Files:**
- Modify: any files still importing `MODEL` or `MODEL_LABELS`

- [ ] **Step 1: Search for remaining references**

```bash
rg "import.*\b(MODEL|MODEL_LABELS)\b" frontend/src/ --glob="*.{js,vue}"
```

After Tasks 6-9, the only remaining references should be in files already modified. Verify no stragglers. Known files that need cleanup:
- `frontend/src/stores/settings.js` — remove `MODEL` from import line 6
- Any other file found by the grep

- [ ] **Step 2: Fix any remaining imports**

For each file, replace `MODEL_LABELS[x]` with `getModelLabel(x)`, and remove `MODEL`/`MODEL_LABELS` from imports. If a file used `MODEL.OPUS` or `MODEL.SONNET` as a value (not just label), replace with the string literal `'opus'` or `'sonnet'`.

- [ ] **Step 3: Verify no build errors**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 || true
npm run build
```

(The project may not have TypeScript checking, so `npm run build` is the key verification.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/
git commit -m "refactor: remove all static MODEL/MODEL_LABELS references in favor of registry"
```

---

## Task 12: End-to-End Verification

- [ ] **Step 1: Start dev servers**

Ask the user to restart dev servers: `uv run ./devctl.py restart all`

- [ ] **Step 2: Verify bootstrap response**

Open browser dev tools → Network tab. Check `GET /api/bootstrap/` response contains `model_registry` with 4 entries (opus 4.6, opus 4.5, sonnet 4.6, sonnet 4.5), sorted with latest first.

- [ ] **Step 3: Test global settings model dropdown**

Open Settings → Claude section. Verify:
- Dropdown shows: `Opus (latest: 4.6)`, `Sonnet (latest: 4.6)`, then divider, then `Opus 4.5 (until Nov 24, 2026)`, `Sonnet 4.5 (until Sep 29, 2026)`
- Selecting `Sonnet 4.5` → 1M context option becomes disabled/unavailable
- Switching back to `Opus` → 1M option re-enabled
- If 1M was selected when switching to Sonnet 4.5, it auto-resets to 200K

- [ ] **Step 4: Test per-session model dropdown**

Open a session's gear icon. Verify:
- "Default: Opus" sentinel option at top
- Same registry-based options below with "Force to:" label
- Selecting an older model: setting-help "1M not available for this model version." appears, context dropdown disabled
- Settings summary shows "Opus" for latest, "Sonnet 4.5" for versioned, with `[1m]` suffix when applicable

- [ ] **Step 5: Test command palette**

Open command palette (Ctrl+K or equivalent). Navigate to "Change Default Model". Verify all 4 model options appear with correct labels.

- [ ] **Step 6: Test model resolution with SDK**

Start a session with `Sonnet 4.5` selected. Check backend logs for the model passed to the SDK — should be `claude-sonnet-4-5-20250929` (full name), not `sonnet-4.5`. Start a session with `Opus` (latest) — should pass `opus` or `opus[1m]` to the SDK.

- [ ] **Step 7: Commit any fixes from verification**

```bash
git add -u
git commit -m "fix: adjustments from end-to-end verification"
```

---

## Summary of Changes

| Area | What changes |
|------|-------------|
| **New module** | `model_registry.py` — NamedTuple registry + lookup/resolve/upgrade/serialize |
| **New module** | `model_retirement_task.py` — daily task: update global default + active processes + broadcast |
| **Backend process** | `_build_sdk_model()` delegates to `resolve_sdk_model()` |
| **Backend settings** | 1M consistency + retired model safety net at all resolution points |
| **Bootstrap API** | Includes serialized model registry |
| **Server lifecycle** | Retirement task started/stopped alongside other periodic tasks |
| **Frontend constants** | Static `MODEL`/`MODEL_LABELS` → `getModelLabel()` function |
| **Frontend store** | Registry from bootstrap, `modelSupports1m()`, `getRetiredModelUpgrade()` |
| **Frontend dropdowns** | Dynamic options from registry with divider and retirement dates |
| **Frontend 1M logic** | `isContextMaxForcedByModel` + watcher + setting-help message |
| **Frontend session** | Auto-correct retired model at render time (no DB update needed) |
| **Frontend commands** | Command palette model items from registry |
| **Frontend toast** | `model_retirement` WS message → persistent warning toast |
