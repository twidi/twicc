# Telemetry Client (TwiCC-side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the TwiCC-side anonymous telemetry client: a background task that derives a daily usage snapshot from the DB, sends it to the collector, plus the opt-out settings UI (toggle, payload viewer, instance-id reset) and the one-time notice.

**Architecture:** A new `src/twicc/telemetry/` package (state file, install-method detection, snapshot builder, background task) wired into `cli/run.py` like the other periodic tasks. No new DB tables, no migrations: everything is derived at send time from existing models, except two values (presence minutes, peak concurrent agents) accumulated by a 60 s ticker into the state file. Frontend: a synced boolean + widgets at the bottom of the Global settings section, and a one-time notice dialog. Design: `docs/plans/2026-07-18-telemetry-design.md` (§3 payload, §4 anonymity, §5 client, §6 opt-out, §8 UI). The collector (already planned separately) must be reachable for the final E2E task only.

**Tech Stack:** Python (Django ORM read-only, httpx, asyncio task patterns from `pricing_task.py`), `atomic_json.py` for the state file, Vue 3 + Web Awesome for the UI, WS request/response idiom for the payload viewer.

**Not in this plan:** the collector (separate plan `2026-07-18-telemetry-collector-plan.md`), any CHANGELOG entry, any CLI/skill surface (telemetry is deliberately not exposed to agents), plugin version bump (no skill changes).

**Conventions:** everything in English. Conventional Commits with descriptive body + `Co-Authored-By` trailer for the running model (repo `CLAUDE.md`); precise `git add`, never `-A`. Backend tests follow `tests/test_settings_mutation.py` style (pytest-django, `transactional_db`, `monkeypatch`, `async_to_sync`, `broadcast=False`).

**Known deviation from the design doc (intentional):** the design (§6) sketched a nested `telemetry: {"enabled": true}` settings key; synced settings are flat camelCase in this codebase (`titleGenerationEnabled`, …), so the plan uses **`telemetryEnabled`** (+ **`telemetryNoticeSeen`** for the notice). Task 8 records this in the design doc.

---

## File structure

```
src/twicc/telemetry/
  __init__.py           # is_telemetry_active(), public re-exports
  state.py              # telemetry.json state file (instance id, last_sent_date, day accumulators, last payload)
  install_method.py     # pip/pipx/uv-tool/uvx/git-dev/other detection
  snapshot.py           # instance block + day blocks + buckets (pure sync DB reads)
  task.py               # start_telemetry_task(): 60s ticker + 24h send cycle
src/twicc/settings.py                   # + TELEMETRY_ENABLED kill switch (env TWICC_NO_TELEMETRY)
src/twicc/synced_settings.py            # + telemetryEnabled / telemetryNoticeSeen defaults
src/twicc/cli/settings/_keys.py         # + key descriptions (test-enforced)
src/twicc/cli/run.py                    # + task launch & cancel
src/twicc/asgi.py                       # + WS handlers get_telemetry_payload / reset_telemetry_instance_id
frontend/src/constants.js               # + SYNCED_SETTINGS_KEYS entries
frontend/src/stores/settings.js         # + schema, validators, getters, actions
frontend/src/composables/useWebSocket.js  # + request/response for payload + reset, inbound cases
frontend/src/components/app/SettingsPopover.vue        # + telemetry group at bottom of Global section
frontend/src/components/app/TelemetryPayloadDialog.vue # new
frontend/src/components/app/TelemetryNoticeDialog.vue  # new
frontend/src/App.vue                    # + mount TelemetryNoticeDialog
tests/test_telemetry_state.py
tests/test_telemetry_install_method.py
tests/test_telemetry_snapshot.py
tests/test_telemetry_task.py
```

---

### Task 1: State file (`telemetry/state.py`)

**Files:**
- Create: `src/twicc/telemetry/__init__.py`
- Create: `src/twicc/telemetry/state.py`
- Test: `tests/test_telemetry_state.py`

The state file is `<data_dir>/telemetry.json`, managed with `locked_json_file` from `src/twicc/atomic_json.py` (yields a `JsonFileTxn` with `.data` / `.write()`). Shape:

```json
{
  "instance_id": "uuid4",
  "last_sent_date": "2026-07-18",
  "days": { "2026-07-18": { "presence_minutes": 12, "peak_agents": 3 } },
  "last_payload": null,
  "last_sent_at": null
}
```

- [ ] **Step 1: Write the failing tests**

`tests/test_telemetry_state.py` — calibrate on `tests/test_settings_mutation.py` (fixtures + monkeypatch). Cover:

```python
import orjson
import pytest

from twicc.telemetry import state


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "get_data_dir", lambda: tmp_path)
    return tmp_path


def test_ensure_state_creates_instance_id_and_no_backfill_marker(data_dir):
    st = state.ensure_state()
    assert len(st["instance_id"]) == 36
    # No-backfill rule (design §5.1): last_sent_date is initialized to today
    # on first run, so pre-telemetry DB history is never sent.
    assert st["last_sent_date"] == state.utc_today().isoformat()
    # Idempotent: a second call returns the same instance id.
    assert state.ensure_state()["instance_id"] == st["instance_id"]


def test_reset_instance_id_changes_id_and_persists(data_dir):
    old = state.ensure_state()["instance_id"]
    new = state.reset_instance_id()
    assert new != old
    raw = orjson.loads((data_dir / "telemetry.json").read_bytes())
    assert raw["instance_id"] == new


def test_record_tick_accumulates_presence_and_peak(data_dir):
    state.ensure_state()
    state.record_tick(present=True, live_agents=2)
    state.record_tick(present=False, live_agents=5)
    day = state.utc_today().isoformat()
    st = state.ensure_state()
    assert st["days"][day] == {"presence_minutes": 1, "peak_agents": 5}


def test_mark_sent_advances_marker_and_prunes_sent_days(data_dir):
    state.ensure_state()
    with state.state_txn() as txn:
        txn.data["days"] = {
            "2026-07-10": {"presence_minutes": 5, "peak_agents": 1},
            "2026-07-11": {"presence_minutes": 6, "peak_agents": 2},
        }
        txn.write()
    state.mark_sent("2026-07-11", {"schema": 1})
    st = state.ensure_state()
    assert st["last_sent_date"] == "2026-07-11"
    assert st["days"] == {}          # sent days pruned
    assert st["last_payload"] == {"schema": 1}
    assert st["last_sent_at"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_telemetry_state.py -v`
Expected: FAIL — module `twicc.telemetry.state` not found.

- [ ] **Step 3: Implement**

`src/twicc/telemetry/__init__.py` — empty for now (filled in Task 4).

`src/twicc/telemetry/state.py`:

```python
"""Telemetry state file (<data_dir>/telemetry.json).

Holds the anonymous instance id, the last-sent marker (the no-backfill
rule: initialized to *today* on first run so pre-telemetry DB history is
never sent — design doc §5.1), the per-day accumulators for the two
metrics that have no DB trace (presence minutes, peak concurrent agents),
and a copy of the last payload sent (for the settings "View last payload"
dialog).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from twicc.atomic_json import locked_json_file
from twicc.paths import get_data_dir

STATE_FILENAME = "telemetry.json"

# Offline catch-up cap (design §5.1): older unsent days are dropped.
MAX_DAY_ENTRIES = 30

_DEFAULT_STATE = {
    "instance_id": None,
    "last_sent_date": None,
    "days": {},
    "last_payload": None,
    "last_sent_at": None,
}


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def get_state_path() -> Path:
    return get_data_dir() / STATE_FILENAME


@contextmanager
def state_txn():
    """Locked read-modify-write on the state file, defaults ensured."""
    with locked_json_file(get_state_path(), default=dict(_DEFAULT_STATE)) as txn:
        for key, value in _DEFAULT_STATE.items():
            txn.data.setdefault(key, value if not isinstance(value, dict) else dict(value))
        if not txn.data["instance_id"]:
            # Random UUID derived from nothing (design §4).
            txn.data["instance_id"] = str(uuid.uuid4())
            txn.written = False  # force persist below even if caller doesn't write
            txn.write()
        if not txn.data["last_sent_date"]:
            txn.data["last_sent_date"] = utc_today().isoformat()
            txn.write()
        yield txn


def ensure_state() -> dict:
    with state_txn() as txn:
        return dict(txn.data)


def reset_instance_id() -> str:
    with state_txn() as txn:
        txn.data["instance_id"] = str(uuid.uuid4())
        txn.write()
        return txn.data["instance_id"]


def record_tick(*, present: bool, live_agents: int) -> None:
    """One ticker sample: +1 presence minute if present, max() the peak."""
    day = utc_today().isoformat()
    with state_txn() as txn:
        entry = txn.data["days"].setdefault(day, {"presence_minutes": 0, "peak_agents": 0})
        if present:
            entry["presence_minutes"] += 1
        entry["peak_agents"] = max(entry["peak_agents"], live_agents)
        _prune(txn.data)
        txn.write()


def mark_sent(sent_through: str, payload: dict) -> None:
    """Advance the marker after a successful POST and drop covered days."""
    with state_txn() as txn:
        txn.data["last_sent_date"] = sent_through
        txn.data["last_payload"] = payload
        txn.data["last_sent_at"] = datetime.now(timezone.utc).isoformat()
        txn.data["days"] = {d: v for d, v in txn.data["days"].items() if d > sent_through}
        txn.write()


def _prune(data: dict) -> None:
    days = data["days"]
    if len(days) > MAX_DAY_ENTRIES:
        for day in sorted(days)[: len(days) - MAX_DAY_ENTRIES]:
            del days[day]
```

Note for the implementer: check `JsonFileTxn`'s actual semantics in `src/twicc/atomic_json.py:70` — if `.write()` already persists unconditionally, drop the `txn.written = False` line (it exists only to defeat a possible "already written" guard; adapt to the real API rather than fighting it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telemetry_state.py -v` — expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/telemetry/__init__.py src/twicc/telemetry/state.py tests/test_telemetry_state.py
git commit -m "feat(telemetry): state file with instance id and day accumulators"
```

---

### Task 2: Install-method detection (`telemetry/install_method.py`)

**Files:**
- Create: `src/twicc/telemetry/install_method.py`
- Test: `tests/test_telemetry_install_method.py`

Enum: `pip` / `pipx` / `uv-tool` / `uvx` / `git-dev` / `other` (design §3.1). Signals mirror `_resolve_twicc_launch_prefix()` (`src/twicc/settings.py:43`), plus new pipx path signatures.

- [ ] **Step 1: Write the failing tests**

Parametrized on fake `sys.executable` paths (monkeypatch `sys.executable` and the git-dev probe):

```python
import pytest

from twicc.telemetry import install_method


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        ("/home/u/.local/share/uv/tools/twicc/bin/python", "uv-tool"),
        ("/home/u/.cache/uv/archive-v0/AbCd/bin/python", "uvx"),
        ("/home/u/.local/share/pipx/venvs/twicc/bin/python", "pipx"),
        ("/home/u/.local/pipx/venvs/twicc/bin/python", "pipx"),
        ("/home/u/venvs/main/bin/python", "pip"),
        ("/usr/bin/python3", "other"),
    ],
)
def test_detection_from_executable(monkeypatch, executable, expected):
    monkeypatch.setattr(install_method, "_is_git_checkout", lambda: False)
    monkeypatch.setattr(install_method.sys, "executable", executable)
    assert install_method.detect_install_method() == expected


def test_git_checkout_wins(monkeypatch):
    monkeypatch.setattr(install_method, "_is_git_checkout", lambda: True)
    assert install_method.detect_install_method() == "git-dev"
```

For the `pip` case: a plain venv whose `site-packages` contains twicc but matches no uv/pipx signature → `pip`; the test monkeypatches the venv probe (see implementation) accordingly.

- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/test_telemetry_install_method.py -v`, expected FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Best-effort install-method detection (design §3.1).

Never guesses: unrecognized layouts return "other". Signals are path
signatures on sys.executable plus a .git probe for editable checkouts —
the same family of signals as _resolve_twicc_launch_prefix()
(src/twicc/settings.py), which distinguishes launch modes for another
purpose (building the re-invocation command).
"""

from __future__ import annotations

import sys
from pathlib import Path

import twicc


def _is_git_checkout() -> bool:
    # Editable install / dev checkout: a .git directory above the package
    # source (src/twicc/ -> repo root).
    package_root = Path(twicc.__file__).resolve().parent
    for parent in package_root.parents:
        if (parent / ".git").exists():
            return True
        if (parent / "pyproject.toml").exists():
            break
    return False


def _in_virtualenv(exe: Path) -> bool:
    return (exe.parent.parent / "pyvenv.cfg").exists()


def detect_install_method() -> str:
    if _is_git_checkout():
        return "git-dev"
    exe = str(Path(sys.executable))
    if "/uv/tools/" in exe:
        return "uv-tool"
    if "/.cache/uv/" in exe or "/uv/cache/" in exe:
        return "uvx"
    if "/pipx/venvs/" in exe:
        return "pipx"
    if _in_virtualenv(Path(sys.executable)):
        return "pip"
    return "other"
```

For the `pip` test case above, monkeypatch `_in_virtualenv` to `True` (and to `False` for the `other` case) instead of building real venv layouts on disk.

- [ ] **Step 4: Run tests to verify they pass** — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/telemetry/install_method.py tests/test_telemetry_install_method.py
git commit -m "feat(telemetry): best-effort install-method detection"
```

---

### Task 3: Snapshot builder (`telemetry/snapshot.py`)

**Files:**
- Create: `src/twicc/telemetry/snapshot.py`
- Test: `tests/test_telemetry_snapshot.py`

Pure **sync** DB reads (called from the async task via `asyncio.to_thread`). Produces the design §3 payload. All model facts verified: `Session` (line 374: `created_at`, `type` with `SessionType.SESSION/SUBAGENT`, `provider`, `selected_model`, `effort`, `permission_mode`, `spawned_by`), `DailyActivity` (line 364; global rows have `project=NULL`; fields `date`, `provider`, `user_message_count`, `session_count`, `cost`), `Workflow` (line 888 — **no `created_at`, only `updated_at`**: day attribution uses `updated_at__date`, an accepted approximation since workflows update while running), `SessionCron` (line 1391, `created_at`), `Share`/`ArtifactBookmark` (`created_at`, lines 1631/722), `Project`, and `get_enabled_providers()` from `src/twicc/providers/state.py:106`.

Model-family resolution: `get_provider_helpers(provider).find_model(selected_model)` (`src/twicc/providers/helpers.py`, base at line 983, per-provider overrides) → `ModelVersion.model` is the family alias (`"opus"`, `"fable"`, `"gpt-terra"`); `None`/unresolved → `"unknown"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_telemetry_snapshot.py` — with `transactional_db`, seed: 3 sessions on day D (2 claude_code `opus` effort high mode bypassPermissions, 1 codex `gpt-terra`), 1 subagent, 1 spawned session, global `DailyActivity` rows for D (messages + cost 12.5), 1 `Share` + 1 `ArtifactBookmark` created on D, day-state `{"presence_minutes": 45, "peak_agents": 3}`. Assert on `build_day_block(D, day_state)`:

```python
block = snapshot.build_day_block(day, {"presence_minutes": 45, "peak_agents": 3})
assert block["date"] == day.isoformat()
assert block["sessions_by_model"] == {"claude_code": {"opus": 2}, "codex": {"gpt-terra": 1}}
assert block["sessions_by_effort"] == {"high": 3}
assert block["sessions_by_permission_mode"] == {"bypassPermissions": 2, "yolo": 1}
assert block["messages_sent"] == <seeded sum>
assert block["subagents"] == 1
assert block["sessions_spawned"] == 1
assert block["shares_created"] == 1
assert block["bookmarks_created"] == 1
assert block["cost_bucket"] == "10-50"
assert block["presence_bucket"] == "30-120"
assert block["peak_agents"] == 3
```

Plus: `test_bucket_edges` (exact boundary values for the three bucket scales), and `test_instance_block_contains_no_forbidden_fields` — build the instance block and assert the serialized JSON contains **no** project directory path and no hostname (`socket.gethostname()` value absent), guarding design §3.3.

- [ ] **Step 2: Run tests to verify they fail** — expected FAIL (module missing).

- [ ] **Step 3: Implement**

Structure (full queries left to the implementer — every field name above is verified):

```python
"""Daily telemetry snapshot, derived from the DB at send time (design §3/§5.2).

Counters, booleans, enums and buckets only — never content, titles,
paths, or identifiers (§3.3). Sync code: run it in a thread from the task.
"""

from __future__ import annotations

import platform
import sys
from datetime import date, datetime, time, timedelta, timezone

from django.conf import settings as django_settings

from twicc.core.models import (
    ArtifactBookmark, DailyActivity, Project, Session, SessionCron, SessionType, Share, Workflow,
)
from twicc.providers.helpers import get_provider_helpers
from twicc.providers.state import get_enabled_providers
from twicc.telemetry.install_method import detect_install_method
from twicc.workspaces import ...  # workspace count: reuse the existing read function

SCHEMA_VERSION = 1

# (upper_bound_exclusive, label); None = catch-all. Exact edges are part of
# the public schema (transparency page lists them).
COST_BUCKETS = ((0, "0"), (1, "<1"), (10, "1-10"), (50, "10-50"), (None, "50+"))
# Integer counts: upper bound EXCLUSIVE, so 1 -> "1", 5 -> "2-5", 20 -> "6-20".
COUNT_BUCKETS = ((0, "0"), (2, "1"), (6, "2-5"), (21, "6-20"), (None, "21+"))
PRESENCE_BUCKETS = ((0, "0"), (30, "<30"), (120, "30-120"), (360, "120-360"), (None, "360+"))


def bucket(value, edges) -> str: ...  # <= for the 0 edge, < for the rest


def model_family(provider: str, selected_model: str | None) -> str:
    if not selected_model:
        return "unknown"
    try:
        mv = get_provider_helpers(provider).find_model(selected_model)
    except Exception:
        return "unknown"
    return mv.model if mv else "unknown"


def build_instance_block() -> dict:
    return {
        "twicc_version": django_settings.APP_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "os": {"win32": "windows"}.get(sys.platform, sys.platform),  # design §3.1: linux/darwin/windows
        "arch": platform.machine(),
        "providers": sorted(p.value for p in get_enabled_providers()),
        "install": detect_install_method(),
        "projects_bucket": bucket(Project.objects.count(), COUNT_BUCKETS),
        "workspaces_bucket": bucket(<workspace count>, COUNT_BUCKETS),
        "remote_access": bool(django_settings.TWICC_PASSWORD_HASH),  # check the real setting name
    }


def build_day_block(day: date, day_state: dict) -> dict:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    ...
    # sessions: Session.objects.filter(type=SessionType.SESSION,
    #     created_at__gte=start, created_at__lt=end)
    #     .values_list("provider", "selected_model", "effort", "permission_mode")
    # -> three independent per-dimension breakdowns (design §3.2), families via model_family()
    # messages_sent: sum of user_message_count on DailyActivity global rows
    #     (project__isnull=True, date=day)
    # cost_bucket: bucket(sum of cost on the same global rows, COST_BUCKETS)
    # subagents / sessions_spawned / shares_created / bookmarks_created /
    # crons_created: created_at range counts; workflows: updated_at__date=day
    # presence_bucket / peak_agents: from day_state (missing keys -> 0)


def build_payload(state: dict) -> dict | None:
    """Payload for all complete UTC days after state["last_sent_date"].

    Returns None when there is no complete unsent day (nothing to send).
    Capped at the 30 most recent days (older ones dropped, design §5.1).
    The "days" list is sorted ascending by date — send_cycle() relies on
    days[-1]["date"] being the most recent day covered.
    """
    ...
    return {"schema": SCHEMA_VERSION, "instance_id": state["instance_id"],
            "instance": build_instance_block(), "days": day_blocks}
```

Implementation notes: `TWICC_PASSWORD_HASH` — grep `settings.py`/`auth/` for the actual password-configured signal and use that. Workspace count — reuse the read function in `src/twicc/workspaces.py` (count only, nothing else). Keep every aggregate a plain int/str; no model instances leak into the payload.

- [ ] **Step 4: Run tests to verify they pass** — `uv run pytest tests/test_telemetry_snapshot.py -v`, expected PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/telemetry/snapshot.py tests/test_telemetry_snapshot.py
git commit -m "feat(telemetry): daily snapshot builder derived from the DB"
```

---

### Task 4: Background task + gating + wiring

**Files:**
- Create: `src/twicc/telemetry/task.py`
- Modify: `src/twicc/telemetry/__init__.py`
- Modify: `src/twicc/settings.py` (kill switch, next to the other `TWICC_NO_*` switches around lines 334-355)
- Modify: `src/twicc/cli/run.py` (launch ~line 285, cancel in the `finally` block)
- Test: `tests/test_telemetry_task.py`

- [ ] **Step 1: Add the kill switch to `settings.py`**

Next to `CRON_AUTO_RESTART` etc. (same idiom, `src/twicc/settings.py:334`):

```python
# Anonymous telemetry (design docs/plans/2026-07-18-telemetry-design.md).
# Env kill switch; the synced setting telemetryEnabled is checked at runtime.
TELEMETRY_ENABLED = os.environ.get("TWICC_NO_TELEMETRY", "").strip().lower() not in ("1", "true", "yes")
```

- [ ] **Step 2: Write the failing tests**

`tests/test_telemetry_task.py` — no network: monkeypatch the POST. Cover:

- `is_telemetry_active()` false when `TELEMETRY_ENABLED=False` (monkeypatch `django_settings`), false when synced `telemetryEnabled` is False (use the `temp_settings` fixture pattern from `tests/test_settings_mutation.py`), true otherwise.
- `send_cycle()` with no complete unsent day → no POST attempted, marker untouched.
- `send_cycle()` with an unsent complete day → POST called once with a payload whose `days` cover exactly that day; on fake 204 → `mark_sent` advanced; on fake network error → marker untouched (retried next cycle).
- Ticker gating: `tick_once()` does nothing (no state-file day entry) when `is_telemetry_active()` is False — the reviewer-requested "toggle off mid-day stops accumulation cleanly" case: record a tick, disable, tick again, assert the day entry did not change.

- [ ] **Step 3: Run tests to verify they fail** — expected FAIL.

- [ ] **Step 4: Implement `task.py`**

```python
"""Telemetry background task (design §5.1).

One loop, 60 s granularity: every tick accumulates presence/peak into the
state file; every TELEMETRY_SEND_INTERVAL (and once at startup) builds and
POSTs the pending payload. Failures are logged at debug and never raise
out of the loop. The enabled state is re-checked every tick, so toggling
the synced setting applies without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

TICK_INTERVAL = 60
TELEMETRY_SEND_INTERVAL = 24 * 60 * 60
DEFAULT_ENDPOINT = "https://twicc-telemetry.twidi.com/v1/telemetry"


def get_endpoint() -> str:
    # Override for dev/E2E (e.g. a local `wrangler dev` collector).
    return os.environ.get("TWICC_TELEMETRY_URL", "").strip() or DEFAULT_ENDPOINT


def is_telemetry_active() -> bool:
    if not django_settings.TELEMETRY_ENABLED:
        return False
    from twicc.synced_settings import read_synced_settings
    return bool(read_synced_settings().get("telemetryEnabled", True))


def tick_once() -> None:
    """Sync: one accumulator sample. Called in a thread."""
    if not is_telemetry_active():
        return
    from twicc.agent.states import AgentState
    from twicc.core.models import ProcessRun
    from twicc.presence import is_user_present
    from twicc.telemetry.state import record_tick

    live = ProcessRun.objects.exclude(state=AgentState.DEAD.value).count()
    record_tick(present=is_user_present(), live_agents=live)


def build_pending_payload() -> dict | None:
    """Sync: state + snapshot. Called in a thread."""
    if not is_telemetry_active():
        return None
    from twicc.telemetry.snapshot import build_payload
    from twicc.telemetry.state import ensure_state

    return build_payload(ensure_state())


async def send_cycle() -> None:
    payload = await asyncio.to_thread(build_pending_payload)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(get_endpoint(), json=payload)
            response.raise_for_status()
    except Exception as exc:
        logger.debug("Telemetry send failed (will retry next cycle): %s", exc)
        return
    from twicc.telemetry.state import mark_sent
    sent_through = payload["days"][-1]["date"]
    await asyncio.to_thread(mark_sent, sent_through, payload)


async def start_telemetry_task(stop_event: asyncio.Event) -> None:
    if not django_settings.TELEMETRY_ENABLED:
        logger.info("Telemetry disabled (TWICC_NO_TELEMETRY)")
        return
    logger.info("Telemetry task started")
    ticks_since_send = TELEMETRY_SEND_INTERVAL  # send on first loop entry
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(tick_once)
                ticks_since_send += TICK_INTERVAL
                if ticks_since_send >= TELEMETRY_SEND_INTERVAL:
                    ticks_since_send = 0
                    await send_cycle()
            except Exception:
                logger.debug("Telemetry cycle failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=TICK_INTERVAL)
            except asyncio.TimeoutError:
                pass
            else:
                break
    finally:
        logger.info("Telemetry task stopped")
```

`src/twicc/telemetry/__init__.py`: re-export `is_telemetry_active`, `start_telemetry_task`.

- [ ] **Step 5: Wire into `cli/run.py`**

Next to the other task launches (`src/twicc/cli/run.py:277-285`):

```python
telemetry_task = asyncio.create_task(start_telemetry_task(shutdown_event))
```

and in the `finally` block (~lines 362-409), alongside the others:

```python
await _cancel_task(telemetry_task, "Telemetry task")
```

Import `start_telemetry_task` with the other task imports (~line 100).

- [ ] **Step 6: Run tests to verify they pass** — `uv run pytest tests/test_telemetry_task.py -v`, expected PASS. Also run the full suite once (`uv run pytest`) to catch import-time breakage.

- [ ] **Step 7: Commit**

```bash
git add src/twicc/telemetry/task.py src/twicc/telemetry/__init__.py src/twicc/settings.py src/twicc/cli/run.py tests/test_telemetry_task.py
git commit -m "feat(telemetry): background task with ticker, 24h send cycle and kill switch"
```

---

### Task 5: Synced settings keys (backend)

**Files:**
- Modify: `src/twicc/synced_settings.py` (`_GENERIC_SYNCED_SETTINGS_DEFAULTS`, line 45)
- Modify: `src/twicc/cli/settings/_keys.py` (`GENERIC_KEY_DESCRIPTIONS` — **required**, enforced by `tests/test_settings_cli.py`)

- [ ] **Step 1: Add the defaults**

In `_GENERIC_SYNCED_SETTINGS_DEFAULTS`:

```python
"telemetryEnabled": True,
"telemetryNoticeSeen": False,
```

- [ ] **Step 2: Add the key descriptions in `_keys.py`**

Match the neighbouring entries' tone; e.g. `"telemetryEnabled": "Send anonymous usage statistics (no content, ever)"`, `"telemetryNoticeSeen": "One-time telemetry notice acknowledged"`.

- [ ] **Step 3: Run the enforcing test** — `uv run pytest tests/test_settings_cli.py -v`, expected PASS.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/synced_settings.py src/twicc/cli/settings/_keys.py
git commit -m "feat(telemetry): telemetryEnabled and telemetryNoticeSeen synced settings"
```

---

### Task 6: WS endpoints (payload viewer + instance-id reset)

**Files:**
- Modify: `src/twicc/asgi.py`

Follow the `validate_usage_dump_path` idiom exactly (dispatch at `asgi.py:697` area, handler at `asgi.py:1447`).

- [ ] **Step 1: Add two message types to the WS dispatch**

- `"get_telemetry_payload"` → `_handle_get_telemetry_payload`: in a thread, read `ensure_state()`; if `last_payload` present reply with it, else build a **live preview** via `build_payload(state)` (may be None → reply with `payload: null`). Response:

```python
await self.send_json({
    "type": "telemetry_payload",
    "payload": payload,                # dict | None
    "sent_at": state["last_sent_at"],  # str | None
    "preview": is_preview,             # True when built live, False when last sent
})
```

- `"reset_telemetry_instance_id"` → `_handle_reset_telemetry_instance_id`: `await asyncio.to_thread(reset_instance_id)`, reply `{"type": "telemetry_instance_id_reset", "instance_id": new_id}`.

Use `asyncio.to_thread` for both (file I/O + ORM in the preview path). No broadcast — these are per-connection request/responses.

- [ ] **Step 2: Manual check** — none yet (frontend lands in Task 7); rely on the E2E task.

- [ ] **Step 3: Commit**

```bash
git add src/twicc/asgi.py
git commit -m "feat(telemetry): WS endpoints for payload preview and instance-id reset"
```

---

### Task 7: Frontend — settings toggle, payload dialog, reset

**Files:**
- Modify: `frontend/src/constants.js` (`SYNCED_SETTINGS_KEYS`, line 204)
- Modify: `frontend/src/stores/settings.js` (`SETTINGS_SCHEMA` line 24, `SETTINGS_VALIDATORS` line 101, getters ~301, actions ~486)
- Modify: `frontend/src/composables/useWebSocket.js` (senders ~604, inbound cases ~1370)
- Modify: `frontend/src/components/app/SettingsPopover.vue` (Global section, insert before `</section>` at line 1344)
- Create: `frontend/src/components/app/TelemetryPayloadDialog.vue`

No frontend unit tests (repo convention). Verify in the browser at the end (dev server only — do not restart servers; remind the user instead).

- [ ] **Step 1: Store plumbing**

- `constants.js`: add `'telemetryEnabled'`, `'telemetryNoticeSeen'` to `SYNCED_SETTINGS_KEYS`.
- `settings.js`: schema entries (`telemetryEnabled: null`, `telemetryNoticeSeen: null` — synced keys use the null placeholder), validators (`(v) => typeof v === 'boolean'` like `showCosts`, line 114), getters `isTelemetryEnabled: (state) => state.telemetryEnabled !== false` (default-on when unset) and `isTelemetryNoticeSeen`, actions `setTelemetryEnabled(enabled)` / `setTelemetryNoticeSeen(seen)`. The deep watcher (settings.js:1182) pushes changes automatically — no extra wiring.

- [ ] **Step 2: WS request/response helpers**

In `useWebSocket.js`, copy the `sendValidateUsageDumpPath` idiom (lines 604-613 + case at 1370-1381):

- `requestTelemetryPayload()` → sends `{ type: 'get_telemetry_payload' }`, resolver stashed on `__hmrState.telemetryPayloadResolve`; inbound `case 'telemetry_payload':` resolves `{ payload, sent_at, preview }`.
- `requestTelemetryInstanceIdReset()` → sends `{ type: 'reset_telemetry_instance_id' }`; inbound `case 'telemetry_instance_id_reset':` resolves `{ instance_id }`.

- [ ] **Step 3: Global-section UI**

Insert after the Worktree directory template group (before line 1344), following the section's markup conventions (`wa-divider`, `div.setting-group`, `wa-switch :checked/@change`, `span.setting-group-hint`, `wa-icon name="cloud" class="synced-icon"`):

```html
<wa-divider></wa-divider>
<div class="setting-group">
    <label class="setting-group-label">Anonymous telemetry</label>
    <wa-switch :checked="telemetryEnabled" @change="onTelemetryEnabledChange" size="small">
        Enabled <wa-icon name="cloud" class="synced-icon"></wa-icon>
    </wa-switch>
    <span class="setting-group-hint">
        Anonymous usage statistics — counters only, never content, messages, titles or paths.
        <a href="https://twicc-telemetry.twidi.com/" target="_blank" rel="noopener">What is collected</a>
    </span>
    <div v-if="telemetryEnabled" class="telemetry-actions">
        <wa-button size="small" appearance="outlined" @click="showTelemetryPayload = true">View last payload</wa-button>
        <wa-button size="small" appearance="outlined" @click="resetTelemetryInstanceId">Reset instance ID</wa-button>
    </div>
</div>
```

Script side mirrors `showCosts` (getter computed at ~442, handler at ~824): `telemetryEnabled = computed(() => store.isTelemetryEnabled)`, `onTelemetryEnabledChange(event) { store.setTelemetryEnabled(event.target.checked) }`; `resetTelemetryInstanceId()` awaits the WS helper and shows a success toast (`useToast`). Secondary actions hidden when the toggle is off (design §8) — the `v-if` above.

- [ ] **Step 4: `TelemetryPayloadDialog.vue`**

Copy the structure of `frontend/src/components/app/StopProcessConfirmDialog.vue` (open prop + `watch` + `@wa-hide`). Body: on open, call `requestTelemetryPayload()`; render states — loading; `payload === null` → "Nothing to send yet."; else a heading line ("Last sent <sent_at>" or "Preview — not sent yet" when `preview`) and `<pre>{{ JSON.stringify(payload, null, 2) }}</pre>` (reuse the `jhv-pre` styling from `components/json/JsonHumanView.vue:827`, or drop in `<JsonViewer :data="payload" />` from `components/json/` if nicer). Mount it inside SettingsPopover like `ShareManagerDialog` (`:open="showTelemetryPayload" @close="showTelemetryPayload = false"`, cf. line 1975 area). Mind the WA dialog event-bubbling guards (repo `CLAUDE.md` "Bubbling custom events").

- [ ] **Step 5: Browser check**

On the dev frontend (5173): toggle renders at the bottom of Global with the cloud icon; toggling syncs (check another tab); dialog shows the preview JSON; reset returns a new UUID. Remind the user to restart the backend if WS handlers 404 (never restart it yourself).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/constants.js frontend/src/stores/settings.js frontend/src/composables/useWebSocket.js frontend/src/components/app/SettingsPopover.vue frontend/src/components/app/TelemetryPayloadDialog.vue
git commit -m "feat(telemetry): settings toggle, payload viewer and instance-id reset UI"
```

---

### Task 8: One-time notice + design-doc sync

**Files:**
- Create: `frontend/src/components/app/TelemetryNoticeDialog.vue`
- Modify: `frontend/src/App.vue`
- Modify: `docs/plans/2026-07-18-telemetry-design.md`

- [ ] **Step 1: Notice dialog**

Model on `frontend/src/components/app/HybridAnnouncementDialog.vue` (auto-open-once gated on a synced flag, lines 27-61): open when `isAppReady && isTelemetryEnabled && !isTelemetryNoticeSeen`. Content (sober, 3 short lines): TwiCC now collects anonymous usage statistics — counters only, never content; link to the transparency page; "you can disable it in Settings → Global". Buttons: **Open settings** (opens the settings popover on Global — reuse the mechanism HybridAnnouncementDialog or the notifications callout uses to jump to a section, cf. SettingsPopover.vue:807) and **Got it**. Both set `telemetryNoticeSeen = true` via the store action; `@wa-hide` too (any dismissal counts as seen — the notice must never nag twice). Mount in `App.vue` next to `HybridAnnouncementDialog` (line 654), behind `v-if="isAppReady"`.

- [ ] **Step 2: Record the settings-key deviation in the design doc**

In `docs/plans/2026-07-18-telemetry-design.md`: (a) §6 — change the first bullet to name the real keys: `telemetryEnabled` (flat camelCase per the synced-settings convention; the design's earlier nested sketch was normalized at implementation) and mention `telemetryNoticeSeen` as the notice-acknowledged flag; (b) §3.2 — "active crons" becomes "crons created that day" (`SessionCron.created_at` range count, the day-attributable reading actually implemented). The transparency page must describe both as implemented.

- [ ] **Step 3: Browser check** — with `telemetryNoticeSeen` unset/false, reload: notice appears once; dismiss; reload: absent.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/app/TelemetryNoticeDialog.vue frontend/src/App.vue docs/plans/2026-07-18-telemetry-design.md
git commit -m "feat(telemetry): one-time notice dialog"
```

---

### Task 9: End-to-end verification (with the user)

No new files. The backend restart is **user-owned** (repo rule) — ask, don't do.

- [ ] **Step 1: Full test suite** — `uv run pytest`, expected all green.
- [ ] **Step 2: E2E against a collector** — with the collector running (locally via `wrangler dev` in `telemetry-collector/`, or the deployed one):
  1. Ask the user to restart the backend with `TWICC_TELEMETRY_URL=http://localhost:8787/v1/telemetry` (local case) in the environment.
  2. First start: `telemetry.json` appears in the data dir with an `instance_id` and `last_sent_date` = today; **no POST content for past days** (no-backfill), so the collector receives nothing yet — expected.
  3. To exercise a real send without waiting a day: edit `telemetry.json` (backend stopped, user-run), set `last_sent_date` to yesterday-minus-one, restart → the send fires on startup; verify one row in the collector DB and `last_payload` populated (visible in the "View last payload" dialog, `preview: false`).
  4. Toggle off in settings → confirm (log or state file) that ticks stop accumulating; toggle back on.
  5. `TWICC_NO_TELEMETRY=1` restart → log line "Telemetry disabled (TWICC_NO_TELEMETRY)", no state-file changes.
- [ ] **Step 3: Report** — summarize results to the user; no CHANGELOG entry unless they ask.
