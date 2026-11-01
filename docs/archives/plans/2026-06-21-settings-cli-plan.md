# Settings CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `twicc settings` command group to read and mutate the synced settings (`settings.json`), with a generic key/value backbone plus dedicated `provider` and `notifications` sub-commands, all sharing the backend merge logic the WS path already uses.

**Architecture:** Extract the rich merge/consistency/orchestrator logic from the WS handler in `asgi.py` into a shared async service (`core/services/settings_mutation.py`). The WS handler and two new drop-request kinds (`settings:update`, `settings:notification_test`) both call it. The CLI follows the established drop-request pattern (`check_heartbeat` → `write_drop_file` → `poll_status` → `emit_final`), with all command-specific transformation logic factored into **pure, unit-tested helpers**.

**Tech Stack:** Django 6 ASGI · Channels · Typer · orjson · pytest. No frontend changes (the WS refactor is behaviour-preserving). No model change → no migration.

**Spec:** `docs/plans/2026-06-21-settings-cli-design.md` — read it first.

---

## Pre-flight notes (read once)

- **Tests are not mandatory** (project rule). This plan adds **backend pytest** for the *pure logic* where the infra makes it cheap: the service merge, the value parsing/allowlist, the notification-target list edits, and the info schema. The full drop-request round-trip and the live orchestrator transitions need a running backend → those use **manual verification** steps. Do not stand up new integration infra for them.
- **No frontend work.** Task 2 changes `asgi.py` but must keep the Settings panel behaviour identical.
- **Test data-dir isolation:** synced-settings tests must point the file at a tmp path and clear the module cache. Use this fixture (put it in each new test file or `conftest.py`):

```python
import pytest
import twicc.synced_settings as ss

@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()
```

- **Run backend tests:** `uv run pytest tests/test_settings_mutation.py tests/test_settings_cli.py -v`.
- **Reuse, don't reinvent:** all drop/poll/emit boilerplate comes from `cli/_drop_request/*`; mirror `cli/update_workspace.py` for the command skeleton and exit codes (0 ok, 3 rejected, 4 failed, 5 timeout, 1 validation, 2 server-down).
- **`django.setup()` is required, even for reads.** `_keys.py` and `build_settings_dump()` import `SYNCED_SETTINGS_DEFAULTS`, which builds the provider registry (`get_provider_helpers_registry` → `from twicc.core.models import Session`) and raises `AppRegistryNotReady` without Django. Every `settings` command body must call `django.setup()` (lazily, after `--help`) before using `_keys`/`synced_settings`, exactly like `update_project_settings_cmd` (`settings_command.py:192-194`). So `_keys.py` is *not* Django-free — only `--help` stays cheap (its imports are lazy inside the functions). Tests are unaffected: pytest-django runs `django.setup()` before collecting `tests/`.
- **Status passthrough (do NOT use `emit_final` for settings):** `build_final` dispatches by id field, so a settings result (no id) would print a bogus `project_id: null`. Instead: (a) add a generic `status_extra: dict` passthrough to the watcher (see Task 3), (b) the service drop-results carry `status_extra` (e.g. `corrections`, `tested`, `test_results`), (c) settings write commands emit their own final JSON via a new `cli/settings/_output.py::emit_settings_final(outcome, …)` that reads `outcome.data` and maps the same exit codes. `PollOutcome.data` already carries the whole status file (`_drop_request/polling.py`).
- **After backend changes:** remind the user to restart the backend via `devctl.py` (no migration needed). Do not restart it yourself.
- **Commit per task.** Commit message footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

**Backend — create:**
- `src/twicc/core/services/settings_mutation.py` — shared async service: `update_synced_settings()`, `update_synced_settings_from_payload()`, `notification_test_from_payload()`.
- `src/twicc/cli/settings/__init__.py`
- `src/twicc/cli/settings/_keys.py` — pure: allowlist, key classification, type inference, value parsing.
- `src/twicc/cli/settings/_targets.py` — pure: notification-target list edits by id.
- `src/twicc/cli/settings/_output.py` — `emit_settings_final(outcome, …)`: settings-shaped final JSON (status + `corrections`/`tested`/`test_results` from `outcome.data`) + exit-code mapping. Used by every settings write command instead of `_drop_request/output.emit_final`.
- `src/twicc/cli/settings/command.py` — `settings` group (full dump) + `get` / `set` / `unset`.
- `src/twicc/cli/settings/provider.py` — `settings provider <p>` group (show + agent-defaults flags + sub-commands).
- `src/twicc/cli/settings/notifications.py` — `list` / `add` / `update` / `remove` / `test`.
- `src/twicc/cli/info/settings.py` — `info settings` schema builder.
- `tests/test_settings_mutation.py`, `tests/test_settings_cli.py`.

**Backend — modify:**
- `src/twicc/asgi.py` — `_handle_update_synced_settings` becomes a thin caller.
- `src/twicc/drop_requests_watcher.py` — register the two new kinds.
- `src/twicc/cli/__init__.py` — register the `settings` group.
- `src/twicc/cli/info/command.py` — add `"settings"` to `VALID_SECTIONS` + dispatch.
- `src/twicc/synced_settings.py` — fix the stale `externalNotificationTargets` docstring.

**Docs — modify:**
- `SKILLS-AND-CLI.md` (root) — document the new commands. **No agent skill, no `plugin.json` bump.**

---

## Phase 1 — Backend: shared service + drop kinds

### Task 1: Extract the shared settings service

**Files:**
- Create: `src/twicc/core/services/settings_mutation.py`
- Test: `tests/test_settings_mutation.py`

The goal is to lift the `_merge_and_write` closure + the orchestrator-transition tail out of `asgi.py::_handle_update_synced_settings` (lines ~1280–1475) into a service, **byte-for-byte preserving** the merge rules (consistency, disabledProviders live-agent refusal, transition guard, defaultProvider rebind, `_version` bump) and the running-set delta computation (`old_key_present` / `old_running` / `new_running` — NOT a disabled-set diff).

- [ ] **Step 1: Write the failing tests** (`tests/test_settings_mutation.py`)

```python
import pytest
import twicc.synced_settings as ss
from asgiref.sync import async_to_sync

@pytest.fixture
def temp_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(ss, "get_synced_settings_path", lambda: path)
    ss._cache.clear()
    yield path
    ss._cache.clear()


def _update(patch, **kw):
    from twicc.core.services.settings_mutation import update_synced_settings
    # broadcast=False keeps the test off the channel layer / orchestrator.
    return async_to_sync(update_synced_settings)(patch, broadcast=False, **kw)


def test_scalar_patch_is_merged_and_version_bumped(temp_settings):
    r1 = _update({"autoUnpinOnArchive": False})
    assert r1.status == "accepted"
    assert ss.read_synced_settings()["autoUnpinOnArchive"] is False
    v1 = r1.version
    r2 = _update({"publicBaseUrl": "https://x"})
    assert r2.version == v1 + 1


class _NoAgentsRegistry:
    """Stub: every provider resolves to a manager with no live agents."""
    def get(self, provider):
        class _M:
            def get_active_agents(self_inner):
                return []
        return _M()


def test_default_provider_rebind_when_disabled(temp_settings, monkeypatch):
    # The merge guards a disable: it only sticks if the provider is RUNNING and
    # has no live agents. In a serverless test both must be stubbed, else the
    # disable is reverted by the transition guard and no rebind fires.
    import twicc.providers.state as pstate
    from twicc.core.services import settings_mutation as sm
    monkeypatch.setattr(pstate, "get_provider_state",
                        lambda p: pstate.ProviderState.RUNNING)
    monkeypatch.setattr(sm, "get_agent_manager_registry", lambda: _NoAgentsRegistry())
    r = _update({"disabledProviders": ["claude_code"], "defaultProvider": "claude_code"})
    assert r.corrections.get("defaultProvider") == "codex"
    assert ss.read_synced_settings()["defaultProvider"] == "codex"


def test_base_version_stale_is_rejected(temp_settings):
    _update({"autoUnpinOnArchive": False})            # version -> 1
    r = _update({"autoUnpinOnArchive": True}, base_version=0)
    assert r.status == "rejected"
    assert ss.read_synced_settings()["autoUnpinOnArchive"] is False  # unchanged
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_settings_mutation.py -v`
Expected: FAIL (module `settings_mutation` does not exist).

- [ ] **Step 3: Implement the service**

Create `src/twicc/core/services/settings_mutation.py`. Move the `_merge_and_write` closure from `asgi.py` verbatim into a module-level sync function `_merge_and_write(patch, base_version)` returning a result dict (same keys: `status`, `version`, `to_start`, `to_stop`, `corrections`, and on reject `clean`). Then:

```python
import asyncio
import logging
from typing import NamedTuple

from asgiref.sync import sync_to_async

from twicc.core.enums import Provider
from twicc.providers.helpers import get_provider_helpers_registry
from twicc.agent.registry import get_agent_manager_registry  # real path (see asgi.py:31)
from twicc.synced_settings import (
    _settings_lock, prepare_settings_for_client,
    read_synced_settings, write_synced_settings,
)

logger = logging.getLogger(__name__)


class SettingsUpdateResult(NamedTuple):
    status: str            # "accepted" | "rejected"
    version: int
    corrections: dict
    clean: dict            # full clean settings (resync / CLI display)


def _merge_and_write(patch: dict, base_version: int | None) -> dict:
    # ... moved verbatim from asgi.py lines ~1281-1398, with `synced_settings`
    #     renamed to `patch`. KEEP the running-set delta logic intact.
    ...


async def _apply_transitions_and_broadcast(patch: dict, result: dict) -> None:
    from twicc.orchestrator import get_orchestrator_registry
    orchestrators = get_orchestrator_registry()
    to_stop = [Provider(v) for v in result["to_stop"] if _is_provider(v)]
    to_start = [Provider(v) for v in result["to_start"] if _is_provider(v)]
    await asyncio.gather(
        *(orchestrators.begin_shutdown(p) for p in to_stop),
        *(orchestrators.begin_start(p) for p in to_start),
    )
    for p in to_stop:
        orchestrators.schedule_finish_shutdown(p)
    for p in to_start:
        orchestrators.schedule_finish_start(p)
    # Broadcast to all clients (patch overlaid with corrections). A service has
    # no `self`, so use the channel layer directly — same group + envelope as
    # asgi.py:1465-1475 and workspaces.py:504-505.
    from channels.layers import get_channel_layer
    broadcast_settings = {**patch, **result["corrections"]}
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {
            "type": "synced_settings_updated",
            "settings": broadcast_settings,
            "version": result["version"],
        },
    })


async def update_synced_settings(
    patch: dict, *, base_version: int | None = None, broadcast: bool = True,
) -> SettingsUpdateResult:
    result = await sync_to_async(_merge_and_write)(patch, base_version)
    if result["status"] == "accepted" and broadcast:
        await _apply_transitions_and_broadcast(patch, result)
    clean = result.get("clean")
    if clean is None:
        clean, _ = prepare_settings_for_client(read_synced_settings())
    return SettingsUpdateResult(
        status=result["status"], version=result["version"],
        corrections=result.get("corrections", {}), clean=clean,
    )
```

> Implementation notes: resolve the exact import names for the agent-manager registry, orchestrator registry, and the broadcast helper by reading `asgi.py` (the handler already imports them). If `asgi.py` does the broadcast inline via `self.channel_layer.group_send("updates", {...})`, replicate that with `get_channel_layer()` in the service. `_is_provider(v)` is a tiny try/except on `Provider(v)`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_settings_mutation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/services/settings_mutation.py tests/test_settings_mutation.py
git commit -m "feat(settings): extract shared synced-settings merge service"
```

### Task 2: Make the WS handler call the service

**Files:**
- Modify: `src/twicc/asgi.py` (`_handle_update_synced_settings`, ~line 1247)

- [ ] **Step 1: Replace the body**

Keep the shape validation + `base_version` extraction. Replace the `_merge_and_write` closure + transition tail with:

```python
from twicc.core.services.settings_mutation import update_synced_settings
result = await update_synced_settings(synced_settings, base_version=base_version)
if result.status == "rejected":
    await self.send_json({
        "type": "synced_settings_updated",
        "settings": result.clean,
        "version": result.version,
    })
# accepted: the service already ran transitions + broadcast to all clients.
```

Delete the now-dead local closure and the duplicated transition/broadcast code.

- [ ] **Step 2: Manual verification** (needs a running backend)

Ask the user to restart the backend (devctl). Then in the Settings panel: toggle `autoUnpinOnArchive`, change the default provider, enable/disable a provider, and confirm: value persists, broadcast reaches other tabs, provider toggle shows the in-transition spinner before settling. Expected: identical to before.

- [ ] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "refactor(settings): WS handler delegates to shared service"
```

### Task 3: `settings:update` drop kind

**Files:**
- Modify: `src/twicc/core/services/settings_mutation.py` (add `update_synced_settings_from_payload`)
- Modify: `src/twicc/drop_requests_watcher.py` (`_KIND_HANDLERS`)
- Test: `tests/test_settings_mutation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_update_from_payload_applies_patch(temp_settings):
    from twicc.core.services.settings_mutation import update_synced_settings_from_payload
    from asgiref.sync import async_to_sync
    res = async_to_sync(update_synced_settings_from_payload)(
        {"kind": "settings:update", "patch": {"terminalUseTmux": False}, "broadcast": False},
    )
    assert res.success is True
    assert ss.read_synced_settings()["terminalUseTmux"] is False
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_settings_mutation.py::test_update_from_payload_applies_patch -v`

- [ ] **Step 3: Define the drop-result contract**

The watcher reads `result.success` (`drop_requests_watcher.py:267`) and
`[e._asdict() for e in (result.errors or [])]` (line 286) — `SettingsUpdateResult`
has neither, so the `*_from_payload` glue must return a **separate** type. Add to
`settings_mutation.py`:

```python
class SettingsDropError(NamedTuple):   # _asdict() comes free with NamedTuple
    field: str
    code: str
    message: str

class SettingsDropResult(NamedTuple):
    success: bool
    errors: tuple = ()
    status_extra: dict = {}   # generic passthrough -> status file (see Step 4)
```

- [ ] **Step 4: Generic `status_extra` passthrough in the watcher**

The watcher only copies `_RESULT_ID_FIELDS` from a result into the status file
(`drop_requests_watcher.py:269-272`), so `corrections` / `tested` would never
reach the CLI. Add one generic merge right after that loop:

```python
extra = getattr(result, "status_extra", None)
if isinstance(extra, dict):
    status_data.update(extra)
```

- [ ] **Step 5: Implement `update_synced_settings_from_payload`**

```python
async def update_synced_settings_from_payload(payload: dict) -> SettingsDropResult:
    patch = payload.get("patch") or {}
    r = await update_synced_settings(patch, base_version=None,
                                     broadcast=payload.get("broadcast", True))
    extra = {"corrections": r.corrections} if r.corrections else {}
    return SettingsDropResult(success=(r.status == "accepted"), status_extra=extra)
```

(No server-side validation needed here: `set-default`/`enable`/`disable` targets
are validated client-side in Task 9; the drop service trusts the patch.)

Register in `drop_requests_watcher.py::_KIND_HANDLERS`:

```python
"settings:update": ("twicc.core.services.settings_mutation",
                    "update_synced_settings_from_payload", "updated"),
```

- [ ] **Step 6: Run to verify it passes, then commit**

```bash
uv run pytest tests/test_settings_mutation.py -v
git add src/twicc/core/services/settings_mutation.py src/twicc/drop_requests_watcher.py tests/test_settings_mutation.py
git commit -m "feat(settings): add settings:update drop-request kind"
```

### Task 4: `settings:notification_test` drop kind

**Files:**
- Modify: `src/twicc/core/services/settings_mutation.py` (add `notification_test_from_payload`)
- Modify: `src/twicc/drop_requests_watcher.py`
- Test: `tests/test_settings_mutation.py`

- [ ] **Step 1: Write the failing test** (mock the Apprise send)

```python
def test_notification_test_persists_tested(temp_settings, monkeypatch):
    from twicc.core.services import settings_mutation as sm
    from asgiref.sync import async_to_sync
    # seed a target
    ss.write_synced_settings({**ss.read_synced_settings(),
        "externalNotificationTargets": [{"id": "t1", "url": "json://x", "tested": None}]})
    async def fake_test(urls): return [{"url_masked": "json://***", "ok": True, "error": None}]
    monkeypatch.setattr("twicc.external_notifications.test_notification_urls", fake_test)
    res = async_to_sync(sm.notification_test_from_payload)(
        {"kind": "settings:notification_test", "id": "t1", "broadcast": False})
    assert res.success is True
    target = ss.read_synced_settings()["externalNotificationTargets"][0]
    assert target["tested"] is True
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

```python
async def notification_test_from_payload(payload):
    target_id = payload.get("id")
    settings = read_synced_settings()
    targets = settings.get("externalNotificationTargets") or []
    target = next((t for t in targets if t.get("id") == target_id), None)
    if target is None:
        return SettingsDropResult(success=False, errors=(
            SettingsDropError("id", "not_found", f"No notification target {target_id!r}."),))
    url = target.get("url", "")
    from twicc.external_notifications import test_notification_urls
    results = await test_notification_urls([url])
    ok = bool(results and results[0].get("ok"))   # result dicts use key "ok"
    # Stale-url guard: re-read and only persist if the url is unchanged.
    patch_targets = [
        {**t, "tested": ok} if (t.get("id") == target_id and t.get("url") == url) else t
        for t in (read_synced_settings().get("externalNotificationTargets") or [])
    ]
    await update_synced_settings({"externalNotificationTargets": patch_targets},
                                 broadcast=payload.get("broadcast", True))
    return SettingsDropResult(success=True, status_extra={"tested": ok, "test_results": results})
```

(`SettingsDropResult` / `SettingsDropError` are defined in Task 3 Step 3.)

Register the kind with success status **`updated`** (NOT `tested` — `poll_status`/`build_final` only accept the fixed terminal set; a fresh status dead-ends at timeout):

```python
"settings:notification_test": ("twicc.core.services.settings_mutation",
                               "notification_test_from_payload", "updated"),
```

- [ ] **Step 4: Run to verify it passes** + **Step 5: Commit**

```bash
git add src/twicc/core/services/settings_mutation.py src/twicc/drop_requests_watcher.py tests/test_settings_mutation.py
git commit -m "feat(settings): add settings:notification_test drop-request kind"
```

---

## Phase 2 — CLI: scaffold, read, generic backbone

### Task 5: Pure key helpers (`_keys.py`)

**Files:**
- Create: `src/twicc/cli/settings/__init__.py` (empty), `src/twicc/cli/settings/_keys.py`
- Test: `tests/test_settings_cli.py`

`_keys.py` is Django-free where possible, but it imports `SYNCED_SETTINGS_DEFAULTS` (which pulls the provider registry). Keep imports lazy inside functions to protect `--help` speed.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

def test_generic_allowlist_excludes_visual_and_special():
    from twicc.cli.settings._keys import classify_key
    assert classify_key("autoUnpinOnArchive") == "generic"
    assert classify_key("waTheme") == "excluded"
    assert classify_key("defaultLayoutId") == "excluded"
    assert classify_key("disabledProviders") == "provider"
    assert classify_key("externalNotificationTargets") == "notifications"
    assert classify_key("claudeCodeDefaultModel") == "provider"
    assert classify_key("nope") == "unknown"

def test_value_type_inferred_from_default():
    from twicc.cli.settings._keys import parse_value
    assert parse_value("autoUnpinOnArchive", "false") is False
    assert parse_value("autoUnpinOnArchive", "true") is True
    assert parse_value("publicBaseUrl", "https://x") == "https://x"

def test_parse_value_rejects_bad_bool_and_int():
    from twicc.cli.settings._keys import parse_value, ValueParseError
    with pytest.raises(ValueParseError):
        parse_value("autoUnpinOnArchive", "maybe")
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement `_keys.py`**

```python
"""Pure helpers for the generic `twicc settings set/unset/get` backbone."""
from __future__ import annotations

# Keys excluded from CLI mutation (visual-only or internal).
EXCLUDED_KEYS = frozenset({"waTheme", "waBrand", "defaultLayoutId", "_version"})
# Keys owned by dedicated sub-commands.
PROVIDER_KEYS = frozenset({"defaultProvider", "disabledProviders",
                           "orchestrationDisabledProviders"})
NOTIFICATION_KEYS = frozenset({"externalNotificationTargets"})


class ValueParseError(ValueError):
    pass


def _provider_prefixed(key: str) -> bool:
    return key.startswith("claudeCode") or key.startswith("codex")


def classify_key(key: str) -> str:
    """One of: generic | provider | notifications | excluded | unknown."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    if key in EXCLUDED_KEYS:
        return "excluded"
    if key in PROVIDER_KEYS or _provider_prefixed(key):
        return "provider"
    if key in NOTIFICATION_KEYS:
        return "notifications"
    if key in SYNCED_SETTINGS_DEFAULTS:
        return "generic"
    return "unknown"


_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def parse_value(key: str, raw: str):
    """Parse `raw` into the type of the key's default."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS
    default = SYNCED_SETTINGS_DEFAULTS[key]
    if isinstance(default, bool):
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueParseError(f"{key} expects a boolean (true/false), got {raw!r}.")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            raise ValueParseError(f"{key} expects an integer, got {raw!r}.")
    return raw
```

- [ ] **Step 4: Run to verify it passes** + **Step 5: Commit**

```bash
git add src/twicc/cli/settings/__init__.py src/twicc/cli/settings/_keys.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): pure key classification + value parsing"
```

### Task 6: `settings` group + full dump + `get`

**Files:**
- Create: `src/twicc/cli/settings/command.py`
- Modify: `src/twicc/cli/__init__.py`
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Implement the read command + group**

In `command.py`, build a Typer group `settings_app` with `invoke_without_command=True`. The callback (no sub-command) prints the full settings JSON via `read_synced_settings()` with `_version` stripped (`prepare_settings_for_client`), using the project's JSON emit helper (`cli/_output.py`). Add a `get <KEY>` command printing one value (error if the key is unknown). Both bodies call `django.setup()` first (lazy, after `--help`) — `read_synced_settings` needs the app registry.

Register in `cli/__init__.py` (lazy import like the others):

```python
from twicc.cli.settings.command import settings_app  # noqa: E402
app.add_typer(settings_app)
```

- [ ] **Step 2: Write a test for the read function**

Factor the read into a pure `build_settings_dump()` returning the dict; test it against `temp_settings` (defaults present, `_version` absent).

- [ ] **Step 3: Run tests** + manual `twicc settings` / `twicc settings get autoUnpinOnArchive`.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/cli/settings/command.py src/twicc/cli/__init__.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): settings group with full dump and get"
```

### Task 7: `set` / `unset`

**Files:**
- Modify: `src/twicc/cli/settings/command.py`
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Create `cli/settings/_output.py`**

`emit_settings_final(outcome, *, request_uuid, timeout)` — like `_drop_request/output.build_final` but for the settings shape (no id dispatch): on a terminal `updated` status emit `{"status": "updated", "request_uuid", **{k: outcome.data[k] for k in ("corrections", "tested", "test_results") if k in outcome.data}}`; reuse the `rejected`/`failed`/`timeout` branches verbatim from `build_final`. Return the exit code too (or have the caller map: 0 updated, 3 rejected, 4 failed, 5 timeout) so every settings write command is consistent.

- [ ] **Step 2: Implement `set` / `unset`**

`set <KEY> <VALUE>`: `classify_key` → reject `excluded` ("UI-only visual preference"), `provider` ("use `twicc settings provider …`"), `notifications` ("use `twicc settings notifications …`"), `unknown` ("no such setting"); for `generic`, `parse_value` then drop a `settings:update` payload `{"patch": {key: value}}` via the standard flow (`check_heartbeat`, `write_drop_file(payload, kind="settings:update")`, `poll_status`, then **`emit_settings_final`** — not `emit_final`, see pre-flight). `unset <KEY>`: same allowlist; resolve `SYNCED_SETTINGS_DEFAULTS[key]` and send `{"patch": {key: default}}` — a revert-to-default (exactly what the UI "Reset to default" does: it writes the default value, it does **not** delete the key — `read/write_synced_settings` always re-spread the defaults, so a delete would be re-persisted anyway). No service change needed. Both bodies call `django.setup()` first.

- [ ] **Step 3: Test the validation paths** (pure): a helper `validate_set(key)` returns an error envelope or None; assert the four rejection categories + the accepted one.

- [ ] **Step 4: Manual verification** of a real write (`twicc settings set autoUnpinOnArchive false`; confirm in `twicc settings` and the UI), and `twicc settings unset titleSystemPrompt`.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/cli/settings/command.py src/twicc/cli/settings/_output.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): generic set/unset for global scalar settings"
```

---

## Phase 3 — CLI: `settings provider <p>`

### Task 8: provider group — custom TyperGroup, show, agent-defaults flags

**Files:**
- Create: `src/twicc/cli/settings/provider.py`
- Modify: `src/twicc/cli/settings/command.py` (mount the sub-group)
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Copy the flat-args group class**

Copy the `_FlatBackcompatGroup(TyperGroup)` from `cli/update_project/command.py:36-55` (flips `allow_interspersed_args = True` when the token after the positional is not a known sub-command). Without it, `settings provider claude_code --model opus` won't parse the flags onto the callback. **Apply `cls=_FlatBackcompatGroup` to the inner `provider` group only** — the outer `settings` group has no callback flags (just `invoke_without_command=True`), so it must NOT get the custom class. This is a 3-level nesting (`app → settings → provider`); the flat-args trick is per-group and only the innermost group needs it.

- [ ] **Step 2: Implement the callback (show + agent-defaults patch)**

The group callback takes the `<provider>` positional plus the agent-defaults options. With **no flags and no sub-command → show** the provider's slice (offline read): `enabled` (from `disabledProviders`), `is_default` (from `defaultProvider`), `orchestration_enabled` (from `orchestrationDisabledProviders`), the agent defaults, untrusted default, usage-file settings. With flags → build the agent-defaults patch:
- Map flags to `{provider}Default*` via the provider's `AGENT_SETTINGS_FIELDS_MAPPING`.
- **Reject** a flag the provider does not declare (Codex: `--thinking`/`--fast`/`--chrome`) with a validation error (deliberate divergence from silent-drop; decision #9).
- Resolve the bundle aliases via `cli/_drop_request/aliases.py` (`resolve_overrides`) against the provider's `ProviderBootstrap` (`bootstrap_local.py`).
- `--untrusted-permission-mode` is a **trap**: it is NOT in `AGENT_SETTINGS_FIELDS_MAPPING` (which ends at `context_max`); its synced key is `UNTRUSTED_PERMISSION_MODE_SYNCED_KEY` (`claudeCodeDefaultUntrustedPermissionMode` / `codexDefaultUntrustedPermissionMode`). Handle it **separately** from the mapping loop, mirroring `cli/update_project/settings_command.py:325-333`: resolve via the untrusted alias path (`resolve_alias("permission_mode", v, pb, untrusted=True)`), validate the result is in `pb.untrusted_permission_modes`, then write it under `UNTRUSTED_PERMISSION_MODE_SYNCED_KEY`.
- `--usage-read-file PATH` sets `{provider}UsageReadFilePath=PATH` + `…Enabled=True`; `--no-usage-read-file` sets `…Enabled=False`. Same for dump.
- Drop a `settings:update` payload with the assembled patch (standard flow).

- [ ] **Step 3: Tests** (pure): factor the patch assembly into `build_provider_patch(provider, flags) -> (patch, errors)`; assert Codex rejects `--fast`, alias `--model max` resolves, `--usage-read-file` sets both keys. Test the show projection from a seeded settings dict.

- [ ] **Step 4: Run tests** + manual `twicc settings provider claude_code` (show) and `twicc settings provider codex --model max --effort xhigh`.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/cli/settings/provider.py src/twicc/cli/settings/command.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): settings provider show + agent-defaults flags"
```

### Task 9: provider sub-commands (enable/disable/set-default/orchestration-*)

**Files:**
- Modify: `src/twicc/cli/settings/provider.py`
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Implement the five sub-commands**

Each reads current settings (offline), computes the new list/value (pure helper), and drops a `settings:update` patch:
- `enable` / `disable` → new `disabledProviders` (remove/add this provider; idempotent). Validate the provider exists in the registry; reject enabling/disabling an unknown provider.
- `set-default` → `defaultProvider = provider`; **reject client-side** if the provider is (or would be) disabled.
- `orchestration-enable` / `orchestration-disable` → new `orchestrationDisabledProviders` (remove/add; idempotent).

The server applies safety/transitions and returns any `corrections` in `status_extra`; `emit_settings_final` prints them so the user sees e.g. a refused disable ("live agents") or a `defaultProvider` rebind. Use `emit_settings_final` (not `emit_final`) like every settings write command.

- [ ] **Step 2: Tests** (pure): `compute_disabled(current, provider, enable: bool)` and `compute_orchestration_disabled(...)` idempotency; `set-default` rejects a disabled target.

- [ ] **Step 3: Manual verification** (live server, orchestrator): `twicc settings provider codex disable` then `enable`; confirm the provider transitions and the UI reflects it; try disabling a provider with a live agent and confirm the refusal/corrections.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/cli/settings/provider.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): provider enable/disable/set-default/orchestration"
```

---

## Phase 4 — CLI: `settings notifications`

### Task 10: Pure target list edits (`_targets.py`)

**Files:**
- Create: `src/twicc/cli/settings/_targets.py`
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_add_target_generates_id_and_defaults():
    from twicc.cli.settings._targets import add_target
    new = add_target([], url="json://x", name="n", flags={})
    assert new[-1]["url"] == "json://x" and new[-1]["id"] and new[-1]["tested"] is None
    assert new[-1]["enabled"] is True

def test_update_target_resets_tested_on_url_change():
    from twicc.cli.settings._targets import update_target
    targets = [{"id": "a", "url": "json://x", "tested": True}]
    out = update_target(targets, "a", {"url": "json://y"})
    assert out[0]["url"] == "json://y" and out[0]["tested"] is None

def test_remove_and_missing_id_errors():
    from twicc.cli.settings._targets import remove_target, find_target, TargetNotFound
    import pytest
    targets = [{"id": "a"}]
    assert remove_target(targets, "a") == []
    with pytest.raises(TargetNotFound):
        find_target(targets, "zzz")
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement `_targets.py`** — `add_target` (uuid via `twicc`'s existing UUID helper or `uuid4().hex`; defaults: `enabled=True, tested=None, notifyUserTurn=True, notifyPendingRequest=True, notifyExtraUsageStart=True, awayOnly=True`), `update_target` (patch fields; reset `tested=None` when `url` changes), `remove_target`, `find_target`/`TargetNotFound`. All pure, return new lists.

- [ ] **Step 4: Run to verify it passes** + **Step 5: Commit**

```bash
git add src/twicc/cli/settings/_targets.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): pure notification-target list edits"
```

### Task 11: notifications list/add/update/remove

**Files:**
- Create: `src/twicc/cli/settings/notifications.py`
- Modify: `src/twicc/cli/settings/command.py` (mount sub-group)
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Implement**

`notifications` group, `invoke_without_command=True` → **list** (offline read: print targets with id/url/flags + the global `publicBaseUrl`/`notifyOnExtraUsageStart`). `add <url>` / `update <id>` / `remove <id>`: read current targets, apply the `_targets.py` op, drop a `settings:update` patch with the **whole** new `externalNotificationTargets` list (whole-list overwrite, matching the WS path), then `emit_settings_final`. `update`/`remove` validate the id exists client-side (`find_target`). All bodies call `django.setup()` first.

- [ ] **Step 2: Tests** — the list projection for `list`; that `add`/`update`/`remove` build the expected full-list patch from a seeded settings dict.

- [ ] **Step 3: Manual verification** — `twicc settings notifications add json://localhost`, `… update <id> --disabled`, `… remove <id>`; confirm in `twicc settings notifications` and the UI.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/cli/settings/notifications.py src/twicc/cli/settings/command.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): notifications list/add/update/remove"
```

### Task 12: `notifications test` + `add --test`

**Files:**
- Modify: `src/twicc/cli/settings/notifications.py`

- [ ] **Step 1: Implement `test <id>`** — validate the id exists (offline), drop a `settings:notification_test` payload `{"id": id}`, `poll_status`, then `emit_settings_final` (it surfaces `tested` + `test_results` carried in `status_extra`).

- [ ] **Step 2: Implement `add --test`** — run the `add` flow; **only if it succeeds**, run the `test` flow on the new id (capture the generated id from the add path). On add failure, skip the test.

- [ ] **Step 3: Manual verification** — `twicc settings notifications add json://localhost --test` (against a reachable Apprise URL); confirm `tested:true` lands and the target then fires.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/cli/settings/notifications.py
git commit -m "feat(settings-cli): notifications test and add --test"
```

---

## Phase 5 — `info settings` + docs

### Task 13: `info settings` schema section

**Files:**
- Create: `src/twicc/cli/info/settings.py`
- Modify: `src/twicc/cli/info/command.py`
- Test: `tests/test_settings_cli.py`

- [ ] **Step 1: Add `"settings"` to `VALID_SECTIONS`** and dispatch to a `build()` in `info/settings.py` (mirror `info/agent_settings.py`).

- [ ] **Step 2: Implement `build()`** — emit, per synced key: `key`, `type` (from default), `default`, `owner` (`classify_key`), and the dedicated-command hint. Group by owner.

- [ ] **Step 3: Test** — `build()` includes a known generic key with its type/default, marks `waTheme` excluded, `claudeCodeDefaultModel` provider, `externalNotificationTargets` notifications.

- [ ] **Step 4: Run tests** + manual `twicc info settings`.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/cli/info/settings.py src/twicc/cli/info/command.py tests/test_settings_cli.py
git commit -m "feat(settings-cli): info settings schema section"
```

### Task 14: Docs + stale-docstring fix

**Files:**
- Modify: `SKILLS-AND-CLI.md`
- Modify: `src/twicc/synced_settings.py` (docstring only)

- [ ] **Step 1: Document the commands** in `SKILLS-AND-CLI.md` — the full `twicc settings` surface (read, generic set/unset/get, `provider <p>` + sub-commands, `notifications` + test, `info settings`). Note: human/program-facing, **no agent skill**.

- [ ] **Step 2: Fix the stale target shape** in `synced_settings.py` — the `externalNotificationTargets` comment must list `id`, `name`, and `notifyExtraUsageStart` (currently missing).

- [ ] **Step 3: Commit**

```bash
git add SKILLS-AND-CLI.md src/twicc/synced_settings.py
git commit -m "docs(settings-cli): document settings commands; fix target docstring"
```

---

## Done criteria

- `uv run pytest tests/test_settings_mutation.py tests/test_settings_cli.py -v` green.
- The Settings panel behaves exactly as before (Task 2 manual check).
- `twicc settings`, `twicc settings set/unset/get`, `twicc settings provider <p> [flags|sub-commands]`, `twicc settings notifications …`, `twicc info settings` all work against a live backend; remote-forwarding works (commands are not in `LOCAL_ONLY_COMMANDS`).
- `SKILLS-AND-CLI.md` updated; no `plugin.json` bump; no migration.
