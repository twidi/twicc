# Tips system — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** Implement the tips system specified in `docs/superpowers/specs/2026-05-18-tips-system-design.md` — markdown tip files in `frontend/public/tips/` with YAML front-matter, manifest scanned by the backend at boot, sticky Notivue toast with cooldown-based scheduler, multi-device synced seen-state, and a `Tips` section in Settings.

**Architecture :** Mirror exactly the existing synced-JSON-file + WS-broadcast pattern (see `claude_settings_presets`, `terminal_config`, `message_snippets`, `workspaces`) for the seen-state ; add a separate read-only manifest module for the tip metadata, scanned at boot from the same directory that BlackNoise serves at `/static/tips/`.

**Tech stack :** Django ASGI + Channels + orjson + pyyaml (new dep) ; Vue 3 Composition API + Pinia + Notivue + markdown-it-async + DOMPurify ; Web Awesome 3.

**Project specifics :**
- No tests, no linting (per `CLAUDE.md` "Quality approach").
- All UI strings, comments, identifiers in **English** (per `CLAUDE.md`).
- Worktree-aware Bash commands — every command in this plan assumes the implementer is in `/home/twidi/dev/twicc-poc/.worktrees/feature-tips-system`. Prefix `cd <worktree> && ` if running from elsewhere.
- Backend changes require server restart by the user (devctl). The implementer does **not** run `devctl restart`.
- Frontend HMR picks up changes live, no restart needed.
- One commit per task, conventional-commits style (`feat(tips):`, `feat(tips-backend):`, etc.).

---

## File map

### Backend

| Path | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | modify | Add `pyyaml` dependency |
| `src/twicc/paths.py` | modify | Add `get_seen_tips_path()` + `get_tips_assets_dir()` |
| `src/twicc/seen_tips.py` | create | Atomic read/write of `<data_dir>/seen-tips.json` |
| `src/twicc/tips_manifest.py` | create | Scan tips dir + parse YAML front-matter + expose in-memory manifest |
| `src/twicc/cli/run.py` | modify | Call `init_manifest()` at boot |
| `src/twicc/views.py` | modify | Add `seen_tips` and `tips_manifest` keys to `/api/bootstrap/` |
| `src/twicc/asgi.py` | modify | On-connect push + `update_seen_tips` handler |

### Frontend

| Path | Action | Purpose |
|------|--------|---------|
| `frontend/src/stores/settings.js` | modify | Add `_isLinux`, `_isWindows`, `isLinux`, `isWindows`, `os` getter |
| `frontend/src/stores/tipsConstraints.js` | create | Pure `isTipAvailable(tip, env)` helper |
| `frontend/src/stores/tips.js` | create | Pinia store : manifest, seenTips, currentToastTipKey, nextEligibleTime, enabled |
| `frontend/src/utils/date.js` | modify | Add `formatRelative(timestampMs)` |
| `frontend/src/utils/frontMatter.js` | create | `stripFrontMatter(text)` |
| `frontend/src/composables/useWebSocket.js` | modify | Inbound handlers + `sendUpdateSeenTips()` outbound helper |
| `frontend/src/composables/useTipScheduler.js` | create | Poll loop + cooldown gate |
| `frontend/src/components/tips/TipToast.vue` | create | Toast content component |
| `frontend/src/components/tips/showTipToast.js` | create | Imperative entry point that pushes the toast and sets state |
| `frontend/src/components/settings/TipsSettings.vue` | create | Settings section |
| `frontend/src/components/app/SettingsPopover.vue` | modify | Register `tips` section + import `TipsSettings` |
| `frontend/src/main.js` | modify | Bootstrap hydration |
| `frontend/src/App.vue` | modify | Instantiate `useTipScheduler()` |

### Assets

| Path | Action | Purpose |
|------|--------|---------|
| `frontend/public/tips/welcome.md` | create | One initial tip for smoke testing |
| `hatch_build.py` | verify | Ensure the wheel glob includes `*.md` files in `static/frontend/tips/` (no edit if already covered) |

---

## Reference pointers

Useful pre-existing code for the implementer to read before starting :

- **End-to-end reference pattern** for synced-JSON + WS : the commit that introduced agent settings presets. `git show c19198fb` displays the full pattern (paths, module, bootstrap, asgi handlers, frontend store, useWebSocket plumbing).
- **Atomic write template** : `src/twicc/agent_settings_presets.py` (mirror exactly).
- **On-connect push site** : `src/twicc/asgi.py:410-429`.
- **Inbound dispatcher** : `src/twicc/asgi.py:478-515` (chain of `elif msg_type == "..."`).
- **Bootstrap response** : `src/twicc/views.py:2132-2168`.
- **Frontend dispatcher** : `frontend/src/composables/useWebSocket.js:920-963`.
- **Frontend outbound helpers** : `frontend/src/composables/useWebSocket.js:322-368`.
- **Bootstrap hydration** : `frontend/src/main.js:177-180`.
- **Settings popover sections** : `frontend/src/components/app/SettingsPopover.vue:54-64`.
- **Toast.custom flow** : `frontend/src/composables/useToast.js:131-166` (component receives `item` as a prop automatically — see `CustomNotification.vue:99-104`). Note : the design spec calls this prop `notivueItem` but the actual prop name injected by `CustomNotification` is **`item`** ; the plan code uses `item`.

---

## Phase A — Backend

### Task A1 : Add `pyyaml` dependency

**Files :**
- Modify : `pyproject.toml`

- [ ] **Step 1 : Add dependency**

```bash
uv add pyyaml
```

This updates `pyproject.toml` and `uv.lock`.

- [ ] **Step 2 : Verify**

```bash
uv run python -c "import yaml; print(yaml.__version__)"
```

Expected : prints a version, e.g., `6.0.2`.

- [ ] **Step 3 : Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add pyyaml for tips front-matter parsing"
```

User action : **None required for this task** — `uv add` already installed it.

---

### Task A2 : `paths.py` — add tips path helpers

**Files :**
- Modify : `src/twicc/paths.py` (add after `get_workspaces_path()` at line 120)

- [ ] **Step 1 : Edit `paths.py`**

Add at the bottom of the existing helpers section :

```python
def get_seen_tips_path() -> Path:
    """Path to the synced seen-tips state file."""
    return get_data_dir() / "seen-tips.json"


def get_tips_assets_dir() -> Path:
    """Directory holding tip .md files and their image assets.

    In dev (``settings.DEV_MODE``), this points to ``frontend/public/tips/``
    in the repo so Vite is the source of truth for live editing.

    In an installed wheel, the tips folder is bundled inside
    ``FRONTEND_DIST_DIR / "tips"`` by ``hatch_build.py`` (it copies the
    whole ``frontend/public/`` tree).
    """
    from django.conf import settings as django_settings
    if django_settings.DEV_MODE:
        return django_settings.PACKAGE_DIR.parent.parent / "frontend" / "public" / "tips"
    return django_settings.FRONTEND_DIST_DIR / "tips"
```

- [ ] **Step 2 : Verify**

```bash
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.paths import get_seen_tips_path, get_tips_assets_dir
print('seen_tips:', get_seen_tips_path())
print('tips_assets:', get_tips_assets_dir())
"
```

Expected : `seen_tips` ends in `seen-tips.json` inside the worktree ; `tips_assets` resolves to `<repo>/frontend/public/tips`.

- [ ] **Step 3 : Commit**

```bash
git add src/twicc/paths.py
git commit -m "feat(tips-backend): add path helpers for seen-tips and tips assets dir"
```

---

### Task A3 : `seen_tips.py` — atomic read/write

**Files :**
- Create : `src/twicc/seen_tips.py`

- [ ] **Step 1 : Create the file**

```python
"""Atomic read/write of <data_dir>/seen-tips.json.

Mirrors src/twicc/agent_settings_presets.py for the file pattern, simplified
because we don't have a provider dimension : there's exactly one file.

The on-disk format is a flat dict ``{<tip_key>: <ISO timestamp UTC>}``.
"""

from __future__ import annotations

import logging
import os
import tempfile

import orjson

from twicc.paths import get_seen_tips_path

logger = logging.getLogger(__name__)


def read_seen_tips() -> dict[str, str]:
    """Read the seen-tips file.

    Returns an empty dict when the file is missing, invalid JSON, or not a
    dict at the top level. Keys and values that are not strings are dropped
    silently — this is a defensive read.
    """
    path = get_seen_tips_path()
    try:
        data = orjson.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except orjson.JSONDecodeError:
        logger.warning("seen-tips.json is invalid JSON, returning empty state")
        return {}
    if not isinstance(data, dict):
        logger.warning("seen-tips.json is not a dict, returning empty state")
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def write_seen_tips(state: dict[str, str]) -> None:
    """Persist the seen-tips state atomically (tempfile + os.replace)."""
    path = get_seen_tips_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = orjson.dumps(state, option=orjson.OPT_INDENT_2)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 2 : Verify**

```bash
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.seen_tips import read_seen_tips, write_seen_tips
print('initial:', read_seen_tips())
write_seen_tips({'demo-tip': '2026-05-18T10:00:00.000Z'})
print('after write:', read_seen_tips())
write_seen_tips({})
print('after reset:', read_seen_tips())
"
```

Expected : prints `initial: {}`, then `after write: {'demo-tip': ...}`, then `after reset: {}`. The file is created at `<worktree>/seen-tips.json`.

- [ ] **Step 3 : Commit**

```bash
git add src/twicc/seen_tips.py
git commit -m "feat(tips-backend): add atomic read/write of seen-tips.json"
```

---

### Task A4 : `tips_manifest.py` — scan + parse + manifest

**Files :**
- Create : `src/twicc/tips_manifest.py`

- [ ] **Step 1 : Create the file**

```python
"""In-memory manifest of available tips.

Built once at boot by scanning the tips assets dir and parsing each .md
file's YAML front-matter. Read-only after init — adding / removing tip
files requires a restart of the backend.

The body of each .md is **not** read here. The frontend fetches it
directly via HTTP from /static/tips/<key>.md.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import NamedTuple

import yaml

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[a-z0-9-]+$")
PLATFORM_VALUES = frozenset({"mobile", "desktop"})
OS_VALUES = frozenset({"mac", "linux", "windows"})
FRONT_MATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class TipMeta(NamedTuple):
    key: str
    title: str
    platform: list[str] | None
    os: list[str] | None
    providers_any: list[str] | None
    providers_all: list[str] | None


_manifest: dict[str, TipMeta] = {}


def init_manifest() -> None:
    """Called once at startup. Scans the tips dir and fills the in-memory manifest."""
    global _manifest
    from twicc.paths import get_tips_assets_dir
    _manifest = scan_tips_dir(get_tips_assets_dir())
    logger.info("Tips manifest: %d tips loaded", len(_manifest))


def get_manifest() -> dict[str, TipMeta]:
    return _manifest


def manifest_to_dict() -> dict[str, dict]:
    """JSON-serializable form for the WS / bootstrap wire payload."""
    return {
        key: {
            "title": tip.title,
            "platform": tip.platform,
            "os": tip.os,
            "providers_any": tip.providers_any,
            "providers_all": tip.providers_all,
        }
        for key, tip in _manifest.items()
    }


def scan_tips_dir(directory: Path) -> dict[str, TipMeta]:
    """Pure scan + parse + validate. Invalid tips are logged and excluded."""
    result: dict[str, TipMeta] = {}
    if not directory.is_dir():
        logger.warning("Tips directory not found: %s", directory)
        return result

    for path in sorted(directory.glob("*.md")):
        key = path.stem
        if not KEY_PATTERN.match(key):
            logger.warning("Tip %s: key does not match [a-z0-9-]+, skipped", key)
            continue
        try:
            meta = _parse_tip_file(key, path)
        except ValueError as exc:
            logger.warning("Tip %s: %s, skipped", key, exc)
            continue
        result[key] = meta
    return result


def _parse_tip_file(key: str, path: Path) -> TipMeta:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise ValueError("missing or malformed front-matter")
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front-matter: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError("front-matter must be a YAML mapping")

    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing or invalid 'title'")

    return TipMeta(
        key=key,
        title=title.strip(),
        platform=_validate_array(fm, "platform", PLATFORM_VALUES),
        os=_validate_array(fm, "os", OS_VALUES),
        providers_any=_validate_array(fm, "providers_any", allowed=None),
        providers_all=_validate_array(fm, "providers_all", allowed=None),
    )


def _validate_array(
    fm: dict, field: str, allowed: frozenset[str] | None
) -> list[str] | None:
    """Validate the optional ``field``. Returns None if absent, otherwise a list."""
    if field not in fm:
        return None
    value = fm[field]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"'{field}' must be an array of strings")
    if allowed is not None:
        bad = [x for x in value if x not in allowed]
        if bad:
            raise ValueError(f"'{field}' has invalid values: {bad}")
    return value
```

- [ ] **Step 2 : Verify (no tips yet, manifest should be empty without crashing)**

```bash
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.tips_manifest import init_manifest, get_manifest, manifest_to_dict
init_manifest()
print('manifest count:', len(get_manifest()))
print('dict form:', manifest_to_dict())
"
```

Expected : `manifest count: 0` (dossier vide ou inexistant), `dict form: {}`. No crash.

- [ ] **Step 3 : Commit**

```bash
git add src/twicc/tips_manifest.py
git commit -m "feat(tips-backend): add tips manifest module (scan + YAML front-matter)"
```

---

### Task A5 : Wire `init_manifest()` at boot

**Files :**
- Modify : `src/twicc/cli/run.py` (`run_server` function at line 129, init right after `sync_all_providers()` at line 144)

- [ ] **Step 1 : Import**

Add at the top of `src/twicc/cli/run.py`, alongside other cross-provider imports (around line 69-72) :

```python
from twicc.tips_manifest import init_manifest
```

- [ ] **Step 2 : Call init**

In `run_server()`, immediately after `await sync_all_providers()` at line 144 and before any `asyncio.create_task(...)` calls, add :

```python
init_manifest()
```

It's synchronous, fast (just file scanning), and we want the manifest ready before the first WS connection.

- [ ] **Step 3 : Verify**

After the user restarts the backend (operation reserved to user), check the backend log :

```bash
tail -n 20 logs/backend.log | grep "Tips manifest"
```

Expected : a line like `Tips manifest: 0 tips loaded`.

> User action : **Restart the backend** (`uv run ./devctl.py restart back`) before checking the log.

- [ ] **Step 4 : Commit**

```bash
git add src/twicc/cli/run.py
git commit -m "feat(tips-backend): init tips manifest at startup"
```

---

### Task A6 : `views.py` — add `seen_tips` and `tips_manifest` to bootstrap

**Files :**
- Modify : `src/twicc/views.py` (in `bootstrap()`, around line 2151)

- [ ] **Step 1 : Imports**

At the top of `views.py`, alongside existing imports for `read_terminal_config`, `read_message_snippets_config`, etc. :

```python
from twicc.seen_tips import read_seen_tips
from twicc.tips_manifest import manifest_to_dict
```

- [ ] **Step 2 : Add keys to the response**

In `bootstrap()`, inside the `JsonResponse` dict literal, add (after `message_snippets`, before `providers`) :

```python
"seen_tips": read_seen_tips(),
"tips_manifest": manifest_to_dict(),
```

- [ ] **Step 3 : Verify (after backend restart)**

```bash
# Port depends on devctl status. Assuming 3500 for prod / 3501 in this worktree.
curl -s http://localhost:3501/api/bootstrap/ | uv run python -c "
import sys, json
data = json.load(sys.stdin)
print('seen_tips:', data.get('seen_tips'))
print('tips_manifest:', data.get('tips_manifest'))
"
```

Expected : both keys present, both `{}` initially.

> User action : **Restart backend** to pick up the change.

- [ ] **Step 4 : Commit**

```bash
git add src/twicc/views.py
git commit -m "feat(tips-backend): expose seen_tips and tips_manifest via /api/bootstrap/"
```

---

### Task A7 : `asgi.py` — WS on-connect push + inbound handler

**Files :**
- Modify : `src/twicc/asgi.py`

- [ ] **Step 1 : Imports**

Near the top of `asgi.py`, alongside `from twicc.agent_settings_presets import ...`, add :

```python
from twicc.seen_tips import read_seen_tips, write_seen_tips
from twicc.tips_manifest import manifest_to_dict
```

- [ ] **Step 2 : On-connect push**

In `UpdatesConsumer.connect`, after the existing `workspaces_updated` block (line 427-429), add :

```python
        if self._should_send("tips_manifest_pushed"):
            await self.send_json({
                "type": "tips_manifest_pushed",
                "manifest": manifest_to_dict(),
            })

        if self._should_send("seen_tips_updated"):
            seen_tips = await sync_to_async(read_seen_tips)()
            await self.send_json({"type": "seen_tips_updated", "seen_tips": seen_tips})
```

- [ ] **Step 3 : Inbound dispatcher**

In `UpdatesConsumer.receive_json`, after the `update_agent_settings_presets` branch (line 514-515), add :

```python
        elif msg_type == "update_seen_tips":
            await self._handle_update_seen_tips(content)
```

- [ ] **Step 4 : Handler method**

After `_handle_update_agent_settings_presets` (line 1421), add :

```python
    async def _handle_update_seen_tips(self, content: dict) -> None:
        """Persist the seen-tips state and broadcast the change.

        Expected payload:
        ``{"type": "update_seen_tips", "seen_tips": {<key>: <iso_timestamp>, ...}}``

        Last-write-wins : no version / clock. Acceptable given the rarity
        of concurrent updates for this state.
        """
        state = content.get("seen_tips", {})
        if not isinstance(state, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in state.items()
        ):
            logger.warning("Invalid update_seen_tips payload, ignoring: %r", state)
            return

        await sync_to_async(write_seen_tips)(state)

        await self.channel_layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "seen_tips_updated",
                    "seen_tips": state,
                },
            },
        )
```

- [ ] **Step 5 : Verify (after backend restart)**

The full WS roundtrip is exercised at Task D2. For now, the verification is simply that the backend restarts without import errors and that the `/api/bootstrap/` response includes `seen_tips: {}` (verified in Task A6 already).

```bash
tail -n 20 logs/backend.log | grep -E "ERROR|Traceback" && echo "ERRORS FOUND" || echo "OK"
```

Expected : `OK`.

- [ ] **Step 6 : Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat(tips-backend): WS on-connect push + update_seen_tips handler"
```

> User action : **Restart backend.**

---

### Task A8 : Verify `hatch_build.py` ships `tips/` in the wheel

**Files :**
- Inspect : `hatch_build.py`, `pyproject.toml`

- [ ] **Step 1 : Read `hatch_build.py`** and `pyproject.toml`'s wheel artifact glob

Look for a glob like `src/twicc/static/frontend/**`. Confirm it picks up the entire `frontend/public/` tree after `npm run build`, including `*.md` files.

- [ ] **Step 2 : If the glob is restrictive (e.g. excludes `*.md`)**

Either widen the glob in `pyproject.toml` `[tool.hatch.build.targets.wheel]` `include`, or add an explicit pattern for `static/frontend/tips/*.md`. **Only edit if the audit reveals a gap.**

- [ ] **Step 3 : Smoke check (optional, slow)**

If unsure, build a local wheel and grep its contents :

```bash
uv build --wheel
unzip -l dist/twicc-*.whl | grep tips
```

Expected : the `tips/welcome.md` (created later in task D1) appears in the listing.

- [ ] **Step 4 : Commit if any change was made**

```bash
git add pyproject.toml hatch_build.py
git commit -m "build(tips): ensure tips/*.md ship in the wheel"
```

If no change, no commit.

---

## Phase B — Frontend infrastructure

### Task B1 : `settings.js` — OS detection extension

**Files :**
- Modify : `frontend/src/stores/settings.js`

- [ ] **Step 1 : State**

Find the state init block where `_isTouchDevice` and `_isMac` are set (around line 836-840). Add right after `_isMac` :

```js
const ua = navigator.userAgent || ''
const plat = navigator.platform || ''

state._isMac     = plat.startsWith('Mac') || /Macintosh/.test(ua)
state._isLinux   = /Linux/i.test(plat) && !/Android/i.test(ua)
state._isWindows = /Win/i.test(plat)
```

(Replace the existing `_isMac` line ; keep `_isTouchDevice` untouched.)

- [ ] **Step 2 : State declarations**

Find the state object schema (around line 64-65 where `_isTouchDevice` and `_isMac` are declared). Add :

```js
_isLinux: false,
_isWindows: false,
```

- [ ] **Step 3 : Getters**

Find the existing `isMac` getter (around line 297). Add right after :

```js
isLinux: (state) => state._isLinux,
isWindows: (state) => state._isWindows,
os: (state) => state._isMac ? 'mac' : state._isLinux ? 'linux' : state._isWindows ? 'windows' : null,
```

- [ ] **Step 4 : Verify (via browser console)**

With the dev server running, open the app, open devtools console :

```js
const s = window.__pinia__.state.value.settings  // or similar; or just inspect a component
// Better : open Vue devtools and look at the settings store getters.
```

Expected : `isMac`, `isLinux`, `isWindows`, `os` are exposed and one of them is true (matching the current OS).

- [ ] **Step 5 : Commit**

```bash
git add frontend/src/stores/settings.js
git commit -m "feat(tips-frontend): detect Linux and Windows in settings store"
```

---

### Task B2 : `tipsConstraints.js`

**Files :**
- Create : `frontend/src/stores/tipsConstraints.js`

- [ ] **Step 1 : Create the file**

```js
/**
 * Pure constraint evaluation, isolated from the tips store so it can be
 * imported from anywhere (scheduler composable, settings panel) without
 * loading the full store machinery.
 *
 * `env` shape :
 *   { platform: 'mobile'|'desktop', os: 'mac'|'linux'|'windows'|null,
 *     enabledProviders: string[] }
 */
export function isTipAvailable(tip, env) {
    if (tip.platform && !tip.platform.includes(env.platform)) return false
    if (tip.os) {
        if (env.os === null) return false
        if (!tip.os.includes(env.os)) return false
    }
    if (tip.providers_any && tip.providers_any.length > 0) {
        const any = tip.providers_any.some((p) => env.enabledProviders.includes(p))
        if (!any) return false
    }
    if (tip.providers_all && tip.providers_all.length > 0) {
        const all = tip.providers_all.every((p) => env.enabledProviders.includes(p))
        if (!all) return false
    }
    return true
}
```

- [ ] **Step 2 : Verify (in a Vue component or via Vite eval)**

Trivial pure function. No standalone test ; will be exercised by the store and scheduler.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/stores/tipsConstraints.js
git commit -m "feat(tips-frontend): add isTipAvailable constraint evaluator"
```

---

### Task B3 : `tips.js` Pinia store

**Files :**
- Create : `frontend/src/stores/tips.js`

- [ ] **Step 1 : Create the file**

```js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { isTipAvailable } from './tipsConstraints'

const LS_ENABLED_KEY = 'twicc.tips.enabled'

export const useTipsStore = defineStore('tips', () => {
    // Read-only manifest pushed by the backend at boot / WS connect.
    // Shape : { <key>: { title, platform, os, providers_any, providers_all } }
    const manifest = ref({})

    // Synced seen state : { <key>: ISO timestamp }
    const seenTips = ref({})

    // Currently displayed toast tip key (in-memory, per-tab).
    // Watched by TipToast.vue to swap content in-place via the "Next tip" button.
    const currentToastTipKey = ref(null)

    // Epoch ms. The scheduler refuses to show a tip while Date.now() < nextEligibleTime.
    // Set at scheduler init to (now + FIRST_TIP_DELAY_MS), then on every voluntary
    // dismiss to (now + TIP_COOLDOWN_MS). In-memory, per-tab.
    const nextEligibleTime = ref(0)

    // Per-device on/off, persisted in localStorage. Default ON.
    const lsEnabled = localStorage.getItem(LS_ENABLED_KEY)
    const enabled = ref(lsEnabled === null ? true : lsEnabled === 'true')

    function applyManifest(remote) {
        manifest.value = remote || {}
    }

    function applySeenTips(remote) {
        seenTips.value = remote || {}
    }

    function setEnabled(value) {
        enabled.value = !!value
        localStorage.setItem(LS_ENABLED_KEY, String(enabled.value))
    }

    function _sendSeenTips() {
        // Lazy import to avoid circular dependency (store → composable → store).
        import('../composables/useWebSocket').then(({ sendUpdateSeenTips }) => {
            sendUpdateSeenTips(seenTips.value)
        })
    }

    function markSeen(key) {
        if (!manifest.value[key]) return
        // Refresh timestamp on every call : a user re-opening an already-seen tip
        // with checkbox unchecked legitimately updates the "Seen X ago" ordering.
        seenTips.value = { ...seenTips.value, [key]: new Date().toISOString() }
        _sendSeenTips()
    }

    function unmarkSeen(key) {
        if (!(key in seenTips.value)) return
        const next = { ...seenTips.value }
        delete next[key]
        seenTips.value = next
        _sendSeenTips()
    }

    function resetAllSeen() {
        if (Object.keys(seenTips.value).length === 0) return
        seenTips.value = {}
        _sendSeenTips()
    }

    function getAvailableTips(env) {
        return Object.entries(manifest.value)
            .filter(([_, tip]) => isTipAvailable(tip, env))
            .map(([key, tip]) => ({ key, ...tip }))
    }

    function getCandidates(env) {
        return getAvailableTips(env).filter((t) => !(t.key in seenTips.value))
    }

    function pickRandom(candidates, exclude = []) {
        const pool = candidates.filter((t) => !exclude.includes(t.key))
        if (pool.length === 0) return null
        return pool[Math.floor(Math.random() * pool.length)]
    }

    return {
        manifest, seenTips, currentToastTipKey, nextEligibleTime, enabled,
        applyManifest, applySeenTips, setEnabled,
        markSeen, unmarkSeen, resetAllSeen,
        getAvailableTips, getCandidates, pickRandom,
    }
})
```

- [ ] **Step 2 : Verify**

No standalone test ; will be exercised by the scheduler + UI later.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/stores/tips.js
git commit -m "feat(tips-frontend): add Pinia store for tips manifest and seen state"
```

---

### Task B4 : `useWebSocket.js` — inbound handlers + outbound helper

**Files :**
- Modify : `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1 : Inbound handlers**

In the message dispatcher `switch`, after the `agent_settings_presets_updated` case (line 963), add :

```js
            case 'tips_manifest_pushed':
                // Read-only manifest pushed by the backend on connect.
                import('../stores/tips').then(({ useTipsStore }) => {
                    useTipsStore().applyManifest(msg.manifest)
                })
                break
            case 'seen_tips_updated':
                // Multi-device sync : another client (or this one's last write)
                // updated the seen-state.
                import('../stores/tips').then(({ useTipsStore }) => {
                    useTipsStore().applySeenTips(msg.seen_tips)
                })
                break
```

- [ ] **Step 2 : Outbound helper**

After `sendUpdateAgentSettingsPresets` (line 366-368), add :

```js
/**
 * Push the seen-tips state to the backend for persistence and broadcast.
 * @param {Object} seenTips - { <tip_key>: <ISO timestamp> }
 * @returns {boolean} True if message was sent, false if not connected.
 */
export function sendUpdateSeenTips(seenTips) {
    return sendWsMessage({ type: 'update_seen_tips', seen_tips: seenTips })
}
```

- [ ] **Step 3 : Verify (in browser console, after backend has the changes and frontend is up)**

```js
const { sendUpdateSeenTips } = await import('/src/composables/useWebSocket.js')
sendUpdateSeenTips({ 'fake-key': '2026-05-18T10:00:00.000Z' })
// Inspect the Network/WS tab — you should see the outbound message,
// and then an inbound seen_tips_updated message broadcast back.
```

Also check that the seen-tips file was written :

```bash
cat seen-tips.json
```

Expected : `{"fake-key":"2026-05-18T10:00:00.000Z"}`. Clean up after :

```bash
rm seen-tips.json
```

- [ ] **Step 4 : Commit**

```bash
git add frontend/src/composables/useWebSocket.js
git commit -m "feat(tips-frontend): WS bindings for tips_manifest and seen_tips"
```

---

### Task B5 : `main.js` — bootstrap hydration

**Files :**
- Modify : `frontend/src/main.js` (around line 177-180)

- [ ] **Step 1 : Add hydration**

After the existing block :

```js
useMessageSnippetsStore().applyConfig(bootstrapData.message_snippets)
```

add :

```js
const { useTipsStore } = await import('./stores/tips')
const tipsStore = useTipsStore()
tipsStore.applyManifest(bootstrapData.tips_manifest)
tipsStore.applySeenTips(bootstrapData.seen_tips)
```

(Use dynamic import to keep the cold-load order similar to the other stores, all of which import dynamically in this section. If the existing block uses static top-level imports, prefer the same — match the local style.)

- [ ] **Step 2 : Verify (after frontend HMR)**

Open the app, then in devtools console :

```js
const { useTipsStore } = await import('/src/stores/tips.js')
const s = useTipsStore()
console.log('manifest', s.manifest, 'seenTips', s.seenTips)
```

Expected : `manifest: {}` (empty until task D1 adds a tip), `seenTips: {}`.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/main.js
git commit -m "feat(tips-frontend): hydrate tips store from /api/bootstrap/"
```

---

## Phase C — Frontend UI

### Task C1 : `date.js` — add `formatRelative`

**Files :**
- Modify : `frontend/src/utils/date.js`

- [ ] **Step 1 : Add the function**

Append to the existing file :

```js
/**
 * Format an epoch ms timestamp as a relative time string.
 *
 * - < 1 minute : "just now"
 * - < 1 hour   : "Nm ago"
 * - < 24 hours : "Nh ago"
 * - < 7 days   : "Nd ago"
 * - older      : falls back to formatDate(timestamp_seconds, { smart: true })
 *
 * @param {number} timestampMs - Epoch milliseconds (use Date.parse(iso) for ISO strings).
 * @returns {string}
 */
export function formatRelative(timestampMs) {
    if (!timestampMs) return '-'
    const deltaSec = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000))
    if (deltaSec < 60) return 'just now'
    if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`
    if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
    if (deltaSec < 7 * 86400) return `${Math.floor(deltaSec / 86400)}d ago`
    return formatDate(Math.floor(timestampMs / 1000), { smart: true })
}
```

- [ ] **Step 2 : Verify**

In devtools :

```js
const { formatRelative } = await import('/src/utils/date.js')
console.log(formatRelative(Date.now() - 5_000))           // "just now"
console.log(formatRelative(Date.now() - 5 * 60_000))      // "5m ago"
console.log(formatRelative(Date.now() - 3 * 86400_000))   // "3d ago"
console.log(formatRelative(Date.now() - 30 * 86400_000))  // a calendar date
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/utils/date.js
git commit -m "feat(date): add formatRelative for human-friendly elapsed times"
```

---

### Task C2 : `frontMatter.js` utility

**Files :**
- Create : `frontend/src/utils/frontMatter.js`

- [ ] **Step 1 : Create the file**

```js
/**
 * Strip the YAML front-matter block from the start of a markdown string.
 * Used by TipToast.vue : the backend already parsed the front-matter into
 * the manifest, so the frontend just needs the body.
 */
const FM_RE = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/

export function stripFrontMatter(text) {
    return (text || '').replace(FM_RE, '')
}
```

- [ ] **Step 2 : Verify**

In devtools :

```js
const { stripFrontMatter } = await import('/src/utils/frontMatter.js')
console.log(stripFrontMatter('---\ntitle: x\n---\nbody'))  // "body"
console.log(stripFrontMatter('no front matter here'))      // unchanged
```

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/utils/frontMatter.js
git commit -m "feat(tips-frontend): add stripFrontMatter utility"
```

---

### Task C3 : `useTipScheduler.js`

**Files :**
- Create : `frontend/src/composables/useTipScheduler.js`

- [ ] **Step 1 : Create the file**

```js
import { onMounted, onBeforeUnmount } from 'vue'
import { useTipsStore } from '../stores/tips'
import { useSettingsStore } from '../stores/settings'
import { hasBlockingOverlay } from '../utils/focusGuard'
import { showTipToast } from '../components/tips/showTipToast'

// --- Tunable constants ---------------------------------------------------
// Cooldown measured from the moment the user voluntarily dismisses the
// previous tip (Close, Escape, or "Next tip" returning no further
// candidate). Bump as needed — this is the variable to tune first.
export const TIP_COOLDOWN_MS = 2 * 60 * 60_000   // 2 hours

// Delay between app mount and the first tip attempt.
export const FIRST_TIP_DELAY_MS = 60_000         // 60 seconds

// How often the scheduler wakes up to check whether it can show a tip.
// Short enough to feel responsive once the cooldown is over, long enough
// to be invisible. NOT the inter-tip delay.
export const SCHEDULER_POLL_MS = 60_000          // 1 minute
// -------------------------------------------------------------------------

export function useTipScheduler() {
    const tipsStore = useTipsStore()
    const settings = useSettingsStore()

    let pollHandle = null

    function tryShowTip() {
        if (!tipsStore.enabled) return
        if (tipsStore.currentToastTipKey !== null) return    // already showing
        if (Date.now() < tipsStore.nextEligibleTime) return  // cooldown
        if (hasBlockingOverlay()) return
        if (document.visibilityState !== 'visible') return

        const env = {
            platform: settings._isTouchDevice ? 'mobile' : 'desktop',
            os: settings.os,
            enabledProviders: settings.enabledProviders,
        }
        const candidates = tipsStore.getCandidates(env)
        if (candidates.length === 0) return

        const tip = tipsStore.pickRandom(candidates)
        if (tip) showTipToast(tip.key)
        // nextEligibleTime is NOT updated here; it gets bumped on dismiss
        // by TipToast.vue.
    }

    onMounted(() => {
        tipsStore.nextEligibleTime = Date.now() + FIRST_TIP_DELAY_MS
        pollHandle = setInterval(tryShowTip, SCHEDULER_POLL_MS)
    })

    onBeforeUnmount(() => {
        if (pollHandle) {
            clearInterval(pollHandle)
            pollHandle = null
        }
    })
}
```

- [ ] **Step 2 : Verify**

Smoke-tested in Task C5 once the scheduler is wired into `App.vue`.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/composables/useTipScheduler.js
git commit -m "feat(tips-frontend): scheduler with post-dismiss cooldown"
```

---

### Task C4 : `TipToast.vue` + `showTipToast.js`

**Files :**
- Create : `frontend/src/components/tips/TipToast.vue`
- Create : `frontend/src/components/tips/showTipToast.js`

- [ ] **Step 1 : Create `showTipToast.js`**

```js
import { useToast } from '../../composables/useToast'
import { useTipsStore } from '../../stores/tips'
import TipToast from './TipToast.vue'

/**
 * Show (or swap to) a tip toast.
 * If a toast is already displayed, this just swaps the current key — the
 * TipToast component watches `currentToastTipKey` to re-render in place.
 */
export function showTipToast(key) {
    const tipsStore = useTipsStore()
    if (!tipsStore.manifest[key]) return

    if (tipsStore.currentToastTipKey !== null) {
        tipsStore.currentToastTipKey = key
        return
    }

    tipsStore.currentToastTipKey = key
    useToast().custom(TipToast, {
        type: 'info',
        duration: Number.POSITIVE_INFINITY,   // sticky : user must dismiss
        style: { '--nv-width': 'min(480px, calc(100vw - 2rem))' },
    })
}
```

- [ ] **Step 2 : Create `TipToast.vue`**

```vue
<script setup>
import { ref, computed, watch } from 'vue'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { renderMarkdown } from '../../utils/markdown'
import { stripFrontMatter } from '../../utils/frontMatter'
import { TIP_COOLDOWN_MS } from '../../composables/useTipScheduler'

// CustomNotification automatically injects the Notivue item as the `item` prop.
// (Spec calls it `notivueItem` for narrative clarity, but the actual prop name
// from CustomNotification.vue is `item` — see CustomNotification.vue line 103.)
const props = defineProps({
    item: { type: Object, required: true },
})

const tipsStore = useTipsStore()
const settings = useSettingsStore()

const tip = computed(() => {
    const k = tipsStore.currentToastTipKey
    if (!k) return null
    return { key: k, ...tipsStore.manifest[k] }
})

const bodyHtml = ref('')
const loading = ref(false)
const errored = ref(false)
const showAgainLater = ref(false)
const bodyCache = new Map()   // key → rendered html, per-toast-instance cache

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const hasMoreCandidates = computed(() => {
    if (!tip.value) return false
    const candidates = tipsStore.getCandidates(env.value)
    return candidates.filter((c) => c.key !== tip.value.key).length > 0
})

async function loadBody(key) {
    if (bodyCache.has(key)) {
        bodyHtml.value = bodyCache.get(key)
        loading.value = false
        errored.value = false
        return
    }
    loading.value = true
    errored.value = false
    try {
        const r = await fetch(`/static/tips/${key}.md`)
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const raw = await r.text()
        const body = stripFrontMatter(raw)
        const html = await renderMarkdown(body)
        bodyCache.set(key, html)
        bodyHtml.value = html
    } catch (e) {
        console.error('Failed to load tip', key, e)
        errored.value = true
    } finally {
        loading.value = false
    }
}

// Commit the current tip's seen-state based on the checkbox.
// Called only on voluntary close / Next tip, never on display.
function commitState(key) {
    if (!key) return
    if (showAgainLater.value) {
        tipsStore.unmarkSeen(key)
    } else {
        tipsStore.markSeen(key)
    }
}

// React to currentToastTipKey changes : load new body, reset checkbox.
// No mark/unmark here — commit happens only on voluntary close / Next.
watch(() => tipsStore.currentToastTipKey, async (newKey) => {
    if (!newKey) return
    showAgainLater.value = false
    await loadBody(newKey)
}, { immediate: true })

// Tear down without re-committing (used after commitState has already run).
function teardown() {
    tipsStore.nextEligibleTime = Date.now() + TIP_COOLDOWN_MS
    tipsStore.currentToastTipKey = null
    props.item.clear()
}

function onClose() {
    commitState(tipsStore.currentToastTipKey)
    teardown()
}

function onNextTip() {
    const key = tipsStore.currentToastTipKey
    commitState(key)
    showAgainLater.value = false
    const candidates = tipsStore.getCandidates(env.value)
    const next = tipsStore.pickRandom(candidates, [key])
    if (!next) {
        teardown()
        return
    }
    tipsStore.currentToastTipKey = next.key   // triggers watch above
}
</script>

<template>
    <div class="tip-toast" tabindex="-1" @keydown.esc="onClose">
        <header class="tip-header">
            <wa-icon name="lightbulb" />
            <span class="tip-title">{{ tip?.title }}</span>
        </header>

        <div v-if="loading" class="tip-loading">Loading…</div>
        <div v-else-if="errored" class="tip-error">Failed to load tip content.</div>
        <div v-else class="tip-body" v-html="bodyHtml" />

        <footer class="tip-footer">
            <label class="tip-show-again">
                <input type="checkbox" v-model="showAgainLater" />
                <span>Show again later</span>
            </label>
            <wa-button v-if="hasMoreCandidates" size="small" @click="onNextTip">
                Next tip
                <wa-icon slot="end" name="chevron-right" />
            </wa-button>
        </footer>
    </div>
</template>

<style scoped>
.tip-toast {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 0;
}

.tip-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
}

.tip-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tip-body {
    max-height: 60vh;
    overflow-y: auto;
}

.tip-body :deep(img) {
    max-width: 100%;
    height: auto;
}

.tip-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 0.5rem;
}

.tip-show-again {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    cursor: pointer;
    user-select: none;
}

.tip-loading,
.tip-error {
    padding: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
```

- [ ] **Step 3 : Verify**

Will be smoke-tested at Task D2 once everything is wired.

- [ ] **Step 4 : Commit**

```bash
git add frontend/src/components/tips/TipToast.vue frontend/src/components/tips/showTipToast.js
git commit -m "feat(tips-frontend): TipToast component + showTipToast helper"
```

---

### Task C5 : `App.vue` — instantiate scheduler

**Files :**
- Modify : `frontend/src/App.vue` (`<script setup>` around line 1-45)

- [ ] **Step 1 : Import + call**

In `<script setup>` of `App.vue`, alongside the existing `useWebSocket()` / `useFavicon()` calls (around line 40-43), add :

```js
import { useTipScheduler } from './composables/useTipScheduler'
// …
useTipScheduler()
```

- [ ] **Step 2 : Verify**

Open the app. The scheduler initializes : after 60s (or however long the dev tab has been open), in devtools console :

```js
const { useTipsStore } = await import('/src/stores/tips.js')
const s = useTipsStore()
console.log('nextEligible :', new Date(s.nextEligibleTime))  // should be near "now + 60s" then "now + 2h" once a tip is shown
```

No tip is displayed yet (no tips files), but no error either.

- [ ] **Step 3 : Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat(tips-frontend): start the tip scheduler at app mount"
```

---

### Task C6 : `TipsSettings.vue` + `SettingsPopover.vue` integration

**Files :**
- Create : `frontend/src/components/settings/TipsSettings.vue`
- Modify : `frontend/src/components/app/SettingsPopover.vue`

- [ ] **Step 1 : Create `TipsSettings.vue`**

```vue
<script setup>
import { computed, ref } from 'vue'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { formatRelative } from '../../utils/date'
import { showTipToast } from '../tips/showTipToast'

const tipsStore = useTipsStore()
const settings = useSettingsStore()
const confirmingReset = ref(false)

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const availableTips = computed(() => {
    return tipsStore.getAvailableTips(env.value).sort((a, b) => a.title.localeCompare(b.title))
})

const seenCount = computed(() => Object.keys(tipsStore.seenTips).length)

function isSeen(key) {
    return key in tipsStore.seenTips
}

function seenLabel(key) {
    const iso = tipsStore.seenTips[key]
    if (!iso) return 'Not yet seen'
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return 'Seen'
    return `Seen ${formatRelative(ms)}`
}

function onToggle(value) {
    tipsStore.setEnabled(value)
}

function onClickTip(key) {
    // Close the Settings popover before showing the toast — see spec §6.6.
    // SettingsPopover.vue listens for this event and calls `hide()` on its
    // wa-popover.
    window.dispatchEvent(new CustomEvent('twicc:close-settings-popover'))
    // Defer one tick so the popover starts closing before the toast pushes.
    setTimeout(() => showTipToast(key), 0)
}

function onResetClick() {
    confirmingReset.value = true
}

function onResetConfirm() {
    tipsStore.resetAllSeen()
    confirmingReset.value = false
}

function onResetCancel() {
    confirmingReset.value = false
}
</script>

<template>
    <div class="tips-settings">
        <label class="tips-toggle">
            <input
                type="checkbox"
                :checked="tipsStore.enabled"
                @change="onToggle($event.target.checked)"
            />
            <span>Display tips automatically</span>
        </label>
        <p v-if="!tipsStore.enabled" class="tips-hint">
            Tips will only appear when you click them from the list below.
        </p>

        <div class="tips-reset">
            <wa-button
                size="small"
                :disabled="seenCount === 0"
                @click="onResetClick"
            >
                Reset all seen tips
            </wa-button>
            <wa-dialog v-if="confirmingReset" open @wa-after-hide="onResetCancel">
                <span slot="label">Reset seen tips</span>
                <p>
                    This will mark all tips as unseen. They may appear again
                    on the next tick.
                </p>
                <wa-button slot="footer" @click="onResetCancel">Cancel</wa-button>
                <wa-button slot="footer" variant="brand" @click="onResetConfirm">
                    Reset
                </wa-button>
            </wa-dialog>
        </div>

        <h4 class="tips-list-title">All tips</h4>

        <p v-if="availableTips.length === 0" class="tips-empty">
            No tips available yet.
        </p>

        <ul v-else class="tips-list">
            <li
                v-for="tip in availableTips"
                :key="tip.key"
                class="tips-row"
                :class="{ seen: isSeen(tip.key) }"
                @click="onClickTip(tip.key)"
                tabindex="0"
                @keydown.enter="onClickTip(tip.key)"
            >
                <wa-icon :name="isSeen(tip.key) ? 'check' : 'circle'" class="tips-status" />
                <div class="tips-content">
                    <div class="tips-row-title">{{ tip.title }}</div>
                    <div class="tips-row-sub">{{ seenLabel(tip.key) }}</div>
                </div>
            </li>
        </ul>
    </div>
</template>

<style scoped>
.tips-settings {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.tips-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    cursor: pointer;
    user-select: none;
}

.tips-hint {
    margin: 0;
    font-size: 0.85em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-list-title {
    margin: 0.5rem 0 0;
    font-size: 0.95em;
    font-weight: 600;
}

.tips-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    padding: 0;
    margin: 0;
}

.tips-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
    background-color: var(--wa-color-surface-lowered, transparent);
}

.tips-row:hover,
.tips-row:focus-visible {
    background-color: var(--wa-color-surface-default, #eee);
    outline: none;
}

.tips-row.seen .tips-row-title {
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-content {
    flex: 1;
    min-width: 0;
}

.tips-row-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tips-row-sub {
    font-size: 0.8em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-empty {
    margin: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
```

- [ ] **Step 2 : Register the section in `SettingsPopover.vue`**

In `frontend/src/components/app/SettingsPopover.vue` :

1. Add import at the top of `<script setup>` :

```js
import TipsSettings from '../settings/TipsSettings.vue'
```

2. In the `sections` computed (around lines 54-64), add `{ id: 'tips', label: 'Tips' }`. Place it between `terminal` and `usage` to group it with display-oriented sections. The resulting array should look like :

```js
const sections = computed(() => [
    { id: 'global',        label: 'Global' },
    { id: 'providers',     label: 'Providers', synced: true },
    ...providerSections.value.filter(s => s.enabled),
    { id: 'notifications', label: 'Notifications' },
    { id: 'sessions',      label: 'Sessions' },
    { id: 'title',         label: 'Title suggestion', navLabel: 'Titles', synced: true },
    { id: 'editor',        label: 'Editor' },
    { id: 'terminal',      label: 'Terminal' },
    { id: 'tips',          label: 'Tips' },
    { id: 'usage',         label: 'Providers quotas/usage', navLabel: 'Usage' },
])
```

3. Add the section block in the template, mirroring the way `NotificationSettings` is rendered (line 982 or similar). Place it close to the other component-backed sections :

```html
<TipsSettings v-if="activeSection === 'tips'" />
```

4. Add an event listener for closing the popover when a tip row is clicked. Near the `<wa-popover>` root, expose a method or wire a listener :

```js
import { onMounted, onBeforeUnmount } from 'vue'

const popoverRef = ref(null)

function handleCloseRequest() {
    popoverRef.value?.hide?.()
}

onMounted(() => {
    window.addEventListener('twicc:close-settings-popover', handleCloseRequest)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:close-settings-popover', handleCloseRequest)
})
```

And bind the ref on the existing `<wa-popover>` element (whichever it is) :

```html
<wa-popover ref="popoverRef" …>
```

If the popover doesn't expose `hide()`, fall back to toggling `open` on the popover element :

```js
function handleCloseRequest() {
    const el = popoverRef.value
    if (el && typeof el.removeAttribute === 'function') {
        el.removeAttribute('open')
    }
}
```

(Check the Web Awesome 3 docs locally at `frontend/node_modules/@awesome.me/webawesome/dist/skills/references/components/popover.md` for the canonical close API. Adjust if Web Awesome exposes a cleaner imperative method.)

- [ ] **Step 3 : Verify**

1. Open the app, open Settings popover.
2. Confirm a `Tips` entry shows in the left nav.
3. Click it. The right panel renders the layout : toggle, reset button (disabled), list with "No tips available yet" (we'll add one in Task D1).
4. Wire smoke test : after Task D1 is done, the list will show the demo tip ; clicking it should close the popover and show the toast.

- [ ] **Step 4 : Commit**

```bash
git add frontend/src/components/settings/TipsSettings.vue frontend/src/components/app/SettingsPopover.vue
git commit -m "feat(tips-frontend): Tips section in Settings popover"
```

---

## Phase D — Validation & seed content

### Task D1 : Add the first demo tip

**Files :**
- Create : `frontend/public/tips/welcome.md`

- [ ] **Step 1 : Create the tip**

```markdown
---
title: "Welcome to TwiCC tips"
---

You'll see occasional tips like this one to help you discover TwiCC's
features. Use **Show again later** to keep a tip in the rotation,
otherwise it gets marked as seen on close.

You can browse all available tips from **Settings → Tips**.
```

- [ ] **Step 2 : Restart the backend** so it scans the new file into the manifest

> User action : **Restart backend** (`uv run ./devctl.py restart back`).

- [ ] **Step 3 : Verify**

```bash
tail -n 20 logs/backend.log | grep "Tips manifest"
```

Expected : `Tips manifest: 1 tips loaded`.

Refresh the frontend, open Settings → Tips :
- The list shows `Welcome to TwiCC tips`, with `Not yet seen` underneath.

Click the row :
- Settings popover closes.
- A sticky toast appears at the bottom (or wherever Notivue is mounted) with the title and the markdown body.
- The toast has a `Show again later` checkbox and (if there were more tips) a `Next tip` button.

Close the toast :
- The row in Settings now shows `Seen just now`.
- A check icon replaces the circle.

- [ ] **Step 4 : Commit**

```bash
git add frontend/public/tips/welcome.md
git commit -m "feat(tips): add welcome tip as initial seed"
```

---

### Task D2 : End-to-end smoke test

This task does not produce a commit — it validates that the whole pipeline works. **Do not skip.**

- [ ] **Constraint test (mobile-only tip)**

Add a second tip with a `platform: [mobile]` constraint, restart back, and confirm it does **not** appear in the Settings list on desktop.

```bash
cat > frontend/public/tips/mobile-only-demo.md <<'EOF'
---
title: "Mobile-only demo"
platform: [mobile]
---

This tip should be filtered out on desktop.
EOF
```

> User action : restart backend.

Reload the frontend. In Settings → Tips, you should still see only `Welcome to TwiCC tips`. Then clean up :

```bash
rm frontend/public/tips/mobile-only-demo.md
```

> User action : restart backend.

- [ ] **Multi-device sync test**

Open the app in two browser tabs side-by-side. In tab A, click the `Welcome` tip from Settings, close it (checkbox unchecked). Within ~1s the row in tab B should update to `Seen just now` as well.

Click `Reset all seen tips` in tab A : tab B's row reverts to `Not yet seen` within ~1s.

- [ ] **Cooldown test**

Manually override `TIP_COOLDOWN_MS` to e.g. `30_000` (30s) in `useTipScheduler.js` temporarily. Reload the page, wait 60s (first tick), see the welcome tip appear, close it. Wait another ~30s : a second tip should NOT appear (only one in the manifest), but the eligibility gate should be visible :

```js
const { useTipsStore } = await import('/src/stores/tips.js')
console.log(new Date(useTipsStore().nextEligibleTime))   // ~30s in the future after close
```

Revert the override before committing :

```bash
git checkout frontend/src/composables/useTipScheduler.js
```

- [ ] **Show-again-later test**

Open the tip toast, **check** "Show again later", close. Verify in Settings → Tips that the row shows `Not yet seen` (the tip was put back in the rotation).

- [ ] **Scheduler-guard test**

While a `wa-popover` or `wa-dialog` is open elsewhere in the app, force the eligibility to be in the past :

```js
const { useTipsStore } = await import('/src/stores/tips.js')
useTipsStore().nextEligibleTime = 0
```

Wait one minute (next poll tick). Verify the tip does **not** appear (the blocking-overlay guard kicked in). Close the popover, wait again : the tip appears.

- [ ] **Constraint runtime change test**

Add a tip with `providers_any: [codex]`. Disable Codex from Settings → Providers ; the tip should disappear from Settings → Tips. Re-enable Codex ; it reappears.

- [ ] **Browser tab close = no marking**

Open the welcome tip (after a reset). Close the browser tab without dismissing. Reopen the app : the tip should still be unseen in Settings → Tips, and the scheduler will pick it up again at the next eligibility window.

---

### Task D3 : Final clean-up & branch readiness

- [ ] Run `git status` ; only intended files are committed, no leftovers.
- [ ] Open Settings → Tips one more time, verify everything looks right.
- [ ] Update `CHANGELOG.md` if the project's convention requires it (check `git log` for the convention).
- [ ] If a CHANGELOG entry is added, commit it :

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note tips system addition"
```

The feature is now ready for the user to test the wheel build (release flow handled separately per `CLAUDE.md` "Release Process").

---

## Out-of-scope reminders (see spec §0.2)

- No weighting / priority — uniform random pick.
- No telemetry / view counts.
- No localization — English only.
- No hot-reload of the tips manifest in dev — restart backend.
- No `min_app_version` constraint.
- No auto-reset when all tips have been seen.
- No multi-tab synchronization of `nextEligibleTime` (only the seen-state is synced).
- No initial set of tips beyond the welcome seed.

---

## Operations reserved to the user

These will come up during implementation. **Do not run these yourself :**

- **Backend restart** (`uv run ./devctl.py restart back`) — required after every backend code change in this plan (Tasks A5, A6, A7, A8, D1, D2).
- **Package install** — `uv add pyyaml` in Task A1 is the implementer's action ; if any other dependency comes up, defer to the user.
- **Frontend restart** is **not** required — Vite HMR handles all frontend changes live.

At the end of each backend task, the implementer should remind the user :

> 🛠️ Backend changes ready : please restart with `uv run ./devctl.py restart back` to pick them up.
