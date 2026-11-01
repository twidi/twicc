# Artifact Network Access Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Owner-side management of an artifact bookmark's network access — a unified allowed/denied/pending host list in the bookmark edit dialog, fed by server-recorded denial events with full provenance (shares, viewer ip/ua, owner preview).

**Architecture:** New `ArtifactNetworkDenial` model (app-level upsert with counter) + `ArtifactBookmark.denied_hosts` dict; viewer denials recorded via a proxy callback into a batched `denial_tracking` module (view_tracking pattern); owner denials via a direct POST; the broker host switches to live `getAllowedHosts()`/`getDeniedHosts()` getters with a persisted-deny gate. Spec: `docs/plans/2026-07-10-artifact-network-access-design.md` — read it first, especially §3.3 (states/transitions) and §5 (broker precedence).

**Tech Stack:** Django 6 ASGI + orjson + pytest-django (backend); Vue 3 + Web Awesome (SPA); penpal broker host (non-HMR bundles).

**Conventions (from CLAUDE.md — binding):**
- Worktree: prefix EVERY Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/sharing && `.
- Tests: `uv run --active pytest …` (never plain `uv run` — the main repo's venv would win).
- Python: ruff line-length 120, `orjson`, English everywhere.
- Never run `migrate` (devctl auto-applies at restart; remind the user at the end).
- Non-HMR bundles: after touching `frontend/src/artifact-broker/*` or `frontend/src/artifact-shell/*`, run `cd frontend && npm run build`.
- `git add` with precise paths only (never `-A`/`-a`).
- Human-only feature: no CLI verb, no MCP tool, no drop-request kind, no skill.
- Commit trailer: `Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>` where `<MODEL>` is the model actually running at commit time (e.g. `Fable 5`) — per CLAUDE.md, never hardcode.

---

### Task 1: Model + migration + serializers

**Files:**
- Modify: `src/twicc/core/models.py` (add `denied_hosts` on `ArtifactBookmark` ~line 691; new model after `ShareAccess` ~line 1603)
- Create: `src/twicc/core/migrations/0125_artifact_network_denial.py` (via `makemigrations`)
- Modify: `src/twicc/core/serializers.py` (`serialize_artifact_bookmark`; new `serialize_network_denial`)
- Test: `tests/test_artifact_network_denials.py` (new)

- [ ] **Step 1: Write failing tests** — new file `tests/test_artifact_network_denials.py`. Reuse the fixture style of `tests/test_artifact_bookmarks.py` (a `session`/`project` fixture pair; check its top for the exact shape) plus a `share` fixture like `tests/test_share_view_tracking.py`'s:

```python
import pytest
from twicc.core.models import ArtifactBookmark, ArtifactNetworkDenial
from twicc.core.serializers import serialize_artifact_bookmark, serialize_network_denial


def test_new_bookmark_has_empty_denied_hosts(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    assert bm.denied_hosts == {}


def test_denial_row_defaults_and_cascade(session, project, share):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=share, host_key="https://api.x:443", kind="public", ip="1.2.3.4", user_agent="UA")
    assert d.count == 1 and d.first_at and d.last_at
    share.delete()
    assert not ArtifactNetworkDenial.objects.filter(id=d.id).exists()


def test_bookmark_cascade(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="https://api.x:443", kind="public")
    bm.delete()
    assert not ArtifactNetworkDenial.objects.exists()


def test_owner_denial_has_null_share(session, project):
    bm = ArtifactBookmark.objects.create(session=session, project=project, relative_path="a.html", name="A", scope="project")
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="http://localhost:9000", kind="loopback")
    assert d.share is None and d.ip == "" and d.user_agent == ""


def test_serializers(session, project, share):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.html", name="A", scope="project",
        denied_hosts={"http://localhost:9000": {"kind": "loopback"}},
    )
    assert serialize_artifact_bookmark(bm)["denied_hosts"] == {"http://localhost:9000": {"kind": "loopback"}}
    d = ArtifactNetworkDenial.objects.create(bookmark=bm, share=share, host_key="https://api.x:443", kind="public", ip="1.2.3.4", user_agent="UA")
    d = ArtifactNetworkDenial.objects.select_related("share").get(id=d.id)
    s = serialize_network_denial(d)
    assert s["host_key"] == "https://api.x:443" and s["kind"] == "public" and s["count"] == 1
    assert s["share"] == {"id": share.id, "label": share.label or "", "status": share.status()}
    assert s["ip"] == "1.2.3.4" and s["user_agent"] == "UA" and s["first_at"] and s["last_at"]
    d2 = ArtifactNetworkDenial.objects.create(bookmark=bm, share=None, host_key="https://api.x:443", kind="public")
    assert serialize_network_denial(d2)["share"] is None
```

- [ ] **Step 2: Run tests, verify failure** — `cd /home/twidi/dev/twicc-poc/.worktrees/sharing && uv run --active pytest tests/test_artifact_network_denials.py -v` → ImportError on `ArtifactNetworkDenial`.

- [ ] **Step 3: Implement.** In `models.py`, on `ArtifactBookmark` right after `allowed_hosts`:

```python
    # Explicit owner "deny" decisions, symmetric to allowed_hosts: {host_key: {kind}}.
    # A denied host is auto-refused (no prompt) in the owner preview and stays
    # refused for viewers; its ArtifactNetworkDenial rows are kept (provenance).
    # Mutually exclusive with allowed_hosts (the services enforce it). Human-only,
    # same as allowed_hosts. Design: 2026-07-10-artifact-network-access-design.md.
    denied_hosts = models.JSONField(default=dict, blank=True)
```

After `ShareAccess` (needs `Share` defined):

```python
class ArtifactNetworkDenial(models.Model):
    """One aggregated network-denial provenance: the broker refused ``host_key``
    for this bookmark, seen from one ``(share, ip, user_agent)`` origin. NULL
    ``share`` = the owner's own preview (prompt "Deny"; ip/ua left empty).
    App-level upsert with ``count`` (no DB unique constraint: SQLite treats NULL
    shares as distinct); pruned to the newest 500 rows per bookmark. Purged when
    the host is allowed; kept when it is explicitly denied."""

    bookmark = models.ForeignKey(ArtifactBookmark, on_delete=models.CASCADE, related_name="network_denials")
    share = models.ForeignKey(Share, on_delete=models.CASCADE, null=True, blank=True, related_name="network_denials")
    host_key = models.CharField(max_length=255)  # normalized scheme://host:port
    kind = models.CharField(max_length=16)  # public | loopback | lan (server-resolved)
    ip = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")
    count = models.PositiveIntegerField(default=1)
    first_at = models.DateTimeField(auto_now_add=True)
    last_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_at"]
        indexes = [models.Index(fields=["bookmark", "-last_at"], name="idx_netdenial_bookmark_at")]
```

In `serializers.py`, add `"denied_hosts": bookmark.denied_hosts or {}` next to `allowed_hosts` in `serialize_artifact_bookmark`, and:

```python
def serialize_network_denial(denial):
    """Serialize an ArtifactNetworkDenial. Callers must select_related("share")
    (pure-serializer convention: no queries here)."""
    share = denial.share
    return {
        "host_key": denial.host_key,
        "kind": denial.kind,
        "share": {"id": share.id, "label": share.label or "", "status": share.status()} if share else None,
        "ip": denial.ip,
        "user_agent": denial.user_agent,
        "count": denial.count,
        "first_at": denial.first_at.isoformat() if denial.first_at else None,
        "last_at": denial.last_at.isoformat() if denial.last_at else None,
    }
```

- [ ] **Step 4: Make the migration** (never `migrate`): `cd /home/twidi/dev/twicc-poc/.worktrees/sharing && TWICC_DATA_DIR=$PWD uv run --active python -m django makemigrations core -n artifact_network_denial --settings=twicc.settings`. Verify the file is `src/twicc/core/migrations/0125_artifact_network_denial.py` and contains AddField `denied_hosts` + CreateModel `ArtifactNetworkDenial`.

- [ ] **Step 5: Run tests, verify pass** — same pytest command → all PASS. Also run `uv run --active pytest tests/test_artifact_bookmarks.py -v` (serializer change must not break existing shape assertions; fix any `==`-on-full-dict test by adding the new key).

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/sharing && git add src/twicc/core/models.py src/twicc/core/migrations/0125_artifact_network_denial.py src/twicc/core/serializers.py tests/test_artifact_network_denials.py
git commit -m "feat(network-access): ArtifactNetworkDenial model + denied_hosts field" -m "Aggregated denial provenance rows (share/ip/ua with counter, NULL share = owner preview) and the explicit per-host deny decision dict, per docs/plans/2026-07-10-artifact-network-access-design.md §3." -m "Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>"  # <MODEL> = the model running at commit time (conventions block above)
```

---

### Task 2: Services — deny/un-deny, allow purge, denials broadcast

**Files:**
- Modify: `src/twicc/core/services/artifact_bookmark_mutation.py` (extend `add_artifact_allowed_host` ~175; new functions after `remove_artifact_allowed_host`)
- Test: `tests/test_artifact_network_denials.py`

- [ ] **Step 1: Write failing tests** (append; these are async services — follow the `asyncio.run(...)` / `async def test` style used by the allowed-host service tests in `tests/test_artifact_bookmarks.py` — read them and mirror exactly. **Copy that file's autouse `_passthrough_db_write_lock` fixture into the new test file**: it is file-local, not in `conftest.py`, and without it every service call fails with `RuntimeError("DB writer not started")`):

```python
# add_artifact_denied_host: normalizes the key, stores {kind}, removes the key
# from allowed_hosts if present, keeps existing denial rows.
# remove_artifact_denied_host: pops the key; returns False when absent.
# add_artifact_allowed_host (extended): removes the key from denied_hosts AND
# deletes the bookmark's ArtifactNetworkDenial rows for that host_key (other
# hosts' rows untouched).
```

Concrete assertions: after `add_artifact_denied_host(bookmark=bm, url="http://LOCALHOST:9000/x", kind="loopback")` → `bm.denied_hosts == {"http://localhost:9000": {"kind": "loopback"}}`; seeding `allowed_hosts={"http://localhost:9000": {...}}` first → key gone from `allowed_hosts` after deny; `add_artifact_allowed_host` on a host with 2 denial rows (one share, one owner) + a `denied_hosts` entry → rows for that host deleted, entry gone, rows for another host kept; invalid kind (`"metadata"`, `"weird"`) → `ValueError`.

- [ ] **Step 2: Run, verify failure** — ImportError on the new service names.

- [ ] **Step 3: Implement.** In the allowlist section of `artifact_bookmark_mutation.py`:

```python
_DENIABLE_KINDS = ("public", "loopback", "lan")


async def add_artifact_allowed_host(*, bookmark, url: str, kind: str) -> str:
    """… (keep existing docstring, add:) Allowing also clears any explicit deny
    for the key and purges its denial rows — the pending list is an inbox, not
    an audit log (design N2)."""
    from twicc.artifacts.proxy import normalize_host_key
    from twicc.core.models import ArtifactNetworkDenial

    key = normalize_host_key(url)
    allowed = dict(bookmark.allowed_hosts or {})
    allowed[key] = {"kind": kind}
    bookmark.allowed_hosts = allowed
    denied = dict(bookmark.denied_hosts or {})
    denied.pop(key, None)
    bookmark.denied_hosts = denied

    async def _write():
        await bookmark.asave(update_fields=["allowed_hosts", "denied_hosts", "updated_at"])
        await ArtifactNetworkDenial.objects.filter(bookmark=bookmark, host_key=key).adelete()

    await run_under_db_write_lock(_write)
    await broadcast_artifact_bookmark_updated(bookmark)
    await broadcast_artifact_network_denials_updated(bookmark.id)
    return key


async def add_artifact_denied_host(*, bookmark, url: str, kind: str) -> str:
    """Mark ``url``'s normalized key as explicitly denied (design N5): the owner
    preview auto-refuses it without prompting and the pending list remembers the
    decision. Denial rows are KEPT (abuse stays visible). Removes the key from
    allowed_hosts if present (the dicts are mutually exclusive)."""
    from twicc.artifacts.proxy import normalize_host_key

    if kind not in _DENIABLE_KINDS:
        raise ValueError(f"kind must be one of {_DENIABLE_KINDS}; got {kind!r}")
    key = normalize_host_key(url)
    denied = dict(bookmark.denied_hosts or {})
    denied[key] = {"kind": kind}
    bookmark.denied_hosts = denied
    allowed = dict(bookmark.allowed_hosts or {})
    allowed.pop(key, None)
    bookmark.allowed_hosts = allowed
    await run_under_db_write_lock(
        lambda: bookmark.asave(update_fields=["allowed_hosts", "denied_hosts", "updated_at"])
    )
    await broadcast_artifact_bookmark_updated(bookmark)
    return key


async def remove_artifact_denied_host(*, bookmark, url: str) -> bool:
    """Un-deny: drop the key from denied_hosts (back to pending if it still has
    rows). Returns whether an entry was removed."""
    from twicc.artifacts.proxy import normalize_host_key

    key = normalize_host_key(url)
    denied = dict(bookmark.denied_hosts or {})
    if key not in denied:
        return False
    del denied[key]
    bookmark.denied_hosts = denied
    await run_under_db_write_lock(
        lambda: bookmark.asave(update_fields=["denied_hosts", "updated_at"])
    )
    await broadcast_artifact_bookmark_updated(bookmark)
    return True


async def broadcast_artifact_network_denials_updated(bookmark_id: int) -> None:
    """Lightweight ping: this bookmark's denial rows changed; an open dialog
    refetches the list. The dicts travel via artifact_bookmark_updated."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "artifact_bookmark_denials_updated", "bookmark_id": bookmark_id},
    })
```

Also validate `kind` in `add_artifact_allowed_host`? No — its REST view already validates; keep parity (the new denied view validates too, Task 4).

- [ ] **Step 4: Run tests, verify pass** — `uv run --active pytest tests/test_artifact_network_denials.py tests/test_artifact_bookmarks.py -v`.

- [ ] **Step 5: Commit** — `git add src/twicc/core/services/artifact_bookmark_mutation.py tests/test_artifact_network_denials.py`, message `feat(network-access): deny/un-deny services + allow purges denials` (+ body + trailer as in Task 1).

---

### Task 3: `denial_tracking` module + flush task

**Files:**
- Create: `src/twicc/artifacts/denial_tracking.py` (pattern: `src/twicc/share/view_tracking.py` — read it first)
- Modify: `src/twicc/cli/run.py` (import ~91, `create_task` ~280, cancel ~370 — next to `start_share_view_flush_task`)
- Test: `tests/test_artifact_network_denials.py`

- [ ] **Step 1: Write failing tests:**

```python
# note_denial coalesces in memory: 3 notes for the same (bookmark, share, host,
#   ip, ua) → one pending entry with count 3; different ip → separate entry.
# _persist upserts: creates a row count=3; a second _persist for the same key
#   increments to 5 and refreshes kind to the latest value.
# _persist drops entries whose host_key is in bookmark.allowed_hosts (flush
#   guard, design §4.1) and entries whose bookmark id doesn't exist.
# _persist prunes to _MAX_DENIAL_ROWS per bookmark by (-last_at, -id)
#   (monkeypatch _MAX_DENIAL_ROWS = 3, insert 6 distinct ips, expect 3 rows).
# record_owner_denial (async): direct write path — upserts a share=None row,
#   skips when the host is allowed, ValueError on bad kind and on bad scheme.
# flush re-queue: monkeypatch _persist to raise inside one loop turn of
#   start_denial_flush_task (or call the loop body's logic directly) and assert
#   the drained entries are back in _pending with their counts.
```

- [ ] **Step 2: Run, verify failure** — module not found.

- [ ] **Step 3: Implement** `src/twicc/artifacts/denial_tracking.py`:

```python
"""Batched recording of refused broker fetches (design 2026-07-10 §4).

``note_denial`` coalesces viewer refusals in memory (the share proxy's
``not_allowed`` path can be hammered by an artifact's retry loop); a 30 s flush
task upserts ``ArtifactNetworkDenial`` rows, prunes, and pings open dialogs.
``record_owner_denial`` is the direct-write path for the owner's prompt "Deny"
(one event per human click — no batching needed). Same coalescing philosophy as
``share.view_tracking``."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 30
_MAX_DENIAL_ROWS = 500

# (bookmark_id, share_id|None, host_key, ip, user_agent) -> {"kind": str, "count": int}
_pending: dict[tuple[int, str | None, str, str, str], dict] = {}


def note_denial(*, bookmark_id: int, share_id: str | None, host_key: str, kind: str,
                ip: str = "", user_agent: str = "") -> None:
    """Record one refused fetch in memory (no I/O)."""
    key = (bookmark_id, share_id, host_key, ip[:64], (user_agent or "")[:255])
    entry = _pending.setdefault(key, {"kind": kind, "count": 0})
    entry["kind"] = kind  # latest resolved kind wins
    entry["count"] += 1


def _drain():
    snapshot = dict(_pending)
    _pending.clear()
    return snapshot


def _persist(snapshot) -> set[int]:
    """Blocking DB work — run via ``asyncio.to_thread`` (no write lock —
    mirrors ``view_tracking._persist``). Upserts rows, drops entries for
    now-allowed hosts (the allow purge already removed their rows — design
    §4.1) and unknown bookmarks, prunes per bookmark. Returns the bookmark ids
    whose rows changed."""
    from twicc.core.models import ArtifactBookmark, ArtifactNetworkDenial

    updated: set[int] = set()
    allowed_cache: dict[int, set[str] | None] = {}  # id -> allowed keys, None = missing
    for (bookmark_id, share_id, host_key, ip, user_agent), entry in snapshot.items():
        if bookmark_id not in allowed_cache:
            bm = ArtifactBookmark.objects.filter(id=bookmark_id).only("allowed_hosts").first()
            allowed_cache[bookmark_id] = set((bm.allowed_hosts or {}).keys()) if bm else None
        allowed = allowed_cache[bookmark_id]
        if allowed is None or host_key in allowed:
            continue
        row = ArtifactNetworkDenial.objects.filter(
            bookmark_id=bookmark_id, share_id=share_id, host_key=host_key,
            ip=ip, user_agent=user_agent,
        ).first()
        if row is None:
            ArtifactNetworkDenial.objects.create(
                bookmark_id=bookmark_id, share_id=share_id, host_key=host_key,
                kind=entry["kind"], ip=ip, user_agent=user_agent, count=entry["count"],
            )
        else:
            row.count += entry["count"]
            row.kind = entry["kind"]
            row.save(update_fields=["count", "kind", "last_at"])
        updated.add(bookmark_id)
    for bookmark_id in updated:
        count = ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id).count()
        if count > _MAX_DENIAL_ROWS:
            keep_ids = list(
                ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id)
                .order_by("-last_at", "-id").values_list("id", flat=True)[:_MAX_DENIAL_ROWS]
            )
            ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark_id).exclude(id__in=keep_ids).delete()
    return updated


async def record_owner_denial(*, bookmark, url: str, kind: str) -> None:
    """Direct write for one owner-preview "Deny" (share=None, no ip/ua). Raises
    ``ValueError`` for a bad kind or a bad scheme (via ``normalize_host_key``)."""
    from twicc.artifacts.proxy import normalize_host_key
    from twicc.core.services.artifact_bookmark_mutation import (
        _DENIABLE_KINDS,
        broadcast_artifact_network_denials_updated,
    )

    if kind not in _DENIABLE_KINDS:
        raise ValueError(f"kind must be one of {_DENIABLE_KINDS}; got {kind!r}")
    host_key = normalize_host_key(url)
    snapshot = {(bookmark.id, None, host_key, "", ""): {"kind": kind, "count": 1}}
    updated = await asyncio.to_thread(_persist, snapshot)
    if updated:
        await broadcast_artifact_network_denials_updated(bookmark.id)


async def start_denial_flush_task(stop_event: asyncio.Event) -> None:
    """Flush pending denials every ``_FLUSH_INTERVAL`` s. Started in run_server."""
    from twicc.core.services.artifact_bookmark_mutation import (
        broadcast_artifact_network_denials_updated,
    )

    logger.info("Artifact denial-tracking flush task started (every %ss)", _FLUSH_INTERVAL)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        else:
            break
        snapshot = _drain()
        if not snapshot:
            continue
        try:
            updated = await asyncio.to_thread(_persist, snapshot)
            for bookmark_id in updated:
                await broadcast_artifact_network_denials_updated(bookmark_id)
        except Exception:  # noqa: BLE001 — keep the loop alive
            for key, entry in snapshot.items():
                pending = _pending.setdefault(key, {"kind": entry["kind"], "count": 0})
                pending["count"] += entry["count"]
            logger.warning("Artifact denial flush failed (re-queued)", exc_info=True)
```

> **Lock decision (resolved):** `_persist` runs via plain `await asyncio.to_thread(_persist, snapshot)` — **no** `run_under_db_write_lock` — mirroring `view_tracking`'s flush exactly (its `_persist` also writes without the lock; the lock is for the mutation-service surface, and taking it here would also make the tests need extra patching — the autouse passthrough fixture only covers the mutation module).

In `run.py`: `from twicc.artifacts.denial_tracking import start_denial_flush_task` next to the view-tracking import; `denial_flush_task = asyncio.create_task(start_denial_flush_task(shutdown_event))` next to line ~280; `await _cancel_task(denial_flush_task, "Artifact denial flush task")` next to ~370.

- [ ] **Step 4: Run tests, verify pass** — `uv run --active pytest tests/test_artifact_network_denials.py -v`.

- [ ] **Step 5: Commit** — `git add src/twicc/artifacts/denial_tracking.py src/twicc/cli/run.py tests/test_artifact_network_denials.py`, `feat(network-access): batched denial tracking + flush task`.

---

### Task 4: Proxy hook + share wrapper + REST endpoints

**Files:**
- Modify: `src/twicc/artifacts/proxy.py` (`artifact_proxy` signature ~274, `not_allowed` branch ~338)
- Modify: `src/twicc/share/artifact_views.py` (`share_artifact_proxy` ~124)
- Modify: `src/twicc/views.py` (new views next to `artifact_bookmark_allowed_hosts` ~3478)
- Modify: `src/twicc/urls.py` (~46)
- Test: `tests/test_artifact_network_denials.py` (+ read `tests/test_share_proxy_ssrf.py` for the share-proxy test harness to reuse)

- [ ] **Step 1: Write failing tests:**

```python
# Proxy hook (mirror the fetch-mode test setup of tests/test_share_proxy_ssrf.py —
# fake resolver, allowlist):
#   - fetch with enforced_allowlist not containing the host + on_not_allowed=spy
#     → response {"error":"blocked","reason":"not_allowed"} AND spy called once
#       with (normalized key, resolved kind).
#   - allowed host → spy not called. metadata target → spy not called.
#   - on_not_allowed=None (default) → unchanged behavior.
# Share wrapper end-to-end: POST /share/<token>/api/proxy/ for a non-allowed
#   host → a pending entry lands in denial_tracking._pending keyed with the
#   share id and the request ip/ua (clear _pending in setup/teardown).
#   NOTE: this needs an ARTIFACT-kind Share fixture (bookmark FK), not the
#   session-kind one copied from test_share_view_tracking — build it with
#   Share.objects.create(kind="artifact", token=mint_token(), artifact_bookmark=bm)
#   and mirror the request harness of tests/test_share_proxy_ssrf.py (share URLs
#   are reachable through the test client — the host gate is raw-ASGI, bypassed
#   in tests, as tests/test_share_public_routes.py shows).
# Endpoints:
#   - GET /api/artifact-bookmarks/<id>/network-denials/ → {"denials":[…]},
#     serialized shape incl. share summary and owner rows, ordered -last_at.
#   - POST same with {"url": "http://localhost:9000", "kind": "loopback"} →
#     204/200, row created share=None; bad kind → 400; allowed host → no row.
#   - POST/DELETE /api/artifact-bookmarks/<id>/denied-hosts/ → dict mutations
#     (assert via GET bookmark), passing a stored host_key as url works; DELETE
#     of an absent key → 200 with unchanged dict; bad kind/scheme → 400;
#     unknown bookmark → 404; GET on denied-hosts → 405.
```

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement.**

`proxy.py` — signature `async def artifact_proxy(request, *, enforced_allowlist: set[str] | None = None, on_not_allowed=None):` (document: called with `(host_key, kind)` when a share fetch is refused as `not_allowed`; used for denial recording — design 2026-07-10 §4.1). In the branch:

```python
        if enforced_allowlist is not None:
            key = normalize_host_key(url)
            if key not in enforced_allowlist:
                if on_not_allowed is not None:
                    on_not_allowed(key, kind)
                return JsonResponse({"error": "blocked", "reason": "not_allowed"})
```

`share/artifact_views.py::share_artifact_proxy`:

```python
    allowed = set((ctx.bookmark.allowed_hosts or {}).keys())

    def _on_not_allowed(host_key: str, kind: str) -> None:
        # Record the refusal with full provenance (design 2026-07-10 §4.1): the
        # flush task persists + pings; nothing blocks the proxy response.
        from twicc.artifacts.denial_tracking import note_denial
        from twicc.auth.views import _get_client_ip

        note_denial(
            bookmark_id=ctx.bookmark.id, share_id=ctx.share.id, host_key=host_key,
            kind=kind, ip=_get_client_ip(request),
            user_agent=request.headers.get("User-Agent") or "",
        )

    return await artifact_proxy_mod.artifact_proxy(
        request, enforced_allowlist=allowed, on_not_allowed=_on_not_allowed,
    )
```

`views.py` — two async views modeled on `artifact_bookmark_allowed_hosts` (same 404/JSON/error conventions; docstrings must state the human-only stance):

```python
async def artifact_bookmark_denied_hosts(request, bookmark_id):
    """POST/DELETE — mark / unmark a host as explicitly denied (design N5).
    Body {"url", "kind"} on POST ({"url"} on DELETE); the stored host_key is a
    valid url value (normalize_host_key is idempotent on keys). Browser-host
    only (human decision) — no CLI/MCP surface. Returns the updated bookmark."""
    # method guard, aget bookmark → 404, orjson body parse, url required — copy
    # the exact allowed-hosts view skeleton; POST validates kind in
    # ("public","loopback","lan"); calls add_artifact_denied_host /
    # remove_artifact_denied_host; ValueError → 400;
    # returns JsonResponse(serialize_artifact_bookmark(bookmark)).


async def artifact_bookmark_network_denials(request, bookmark_id):
    """GET — this bookmark's denial provenance rows (newest last_at first).
    POST — record one owner-preview denial event {"url","kind"} (share=NULL).
    Human-only; the viewer-side rows are written by the share proxy, never here."""
    # GET: rows = await sync_to_async(lambda: list(
    #     ArtifactNetworkDenial.objects.filter(bookmark_id=bookmark.id)
    #     .select_related("share").order_by("-last_at", "-id")))()
    #     → JsonResponse({"denials": [serialize_network_denial(d) for d in rows]})
    # POST: validate kind, await record_owner_denial(bookmark=bookmark, url=url,
    #     kind=kind); ValueError → 400; return JsonResponse({"ok": True})
```

`urls.py`, next to the allowed-hosts route:

```python
    path("api/artifact-bookmarks/<int:bookmark_id>/denied-hosts/", views.artifact_bookmark_denied_hosts),
    path("api/artifact-bookmarks/<int:bookmark_id>/network-denials/", views.artifact_bookmark_network_denials),
```

- [ ] **Step 4: Run the full backend suite** — `uv run --active pytest -x -q` (the proxy signature change is additive; everything must stay green).

- [ ] **Step 5: Commit** — `git add src/twicc/artifacts/proxy.py src/twicc/share/artifact_views.py src/twicc/views.py src/twicc/urls.py tests/test_artifact_network_denials.py`, `feat(network-access): record viewer denials + denied-hosts/network-denials endpoints`.

---

### Task 5: Broker host — live getters, persisted-deny gate, onDenied

**Files:**
- Modify: `frontend/src/artifact-broker/host.js`
- Modify: `frontend/src/composables/useArtifactBroker.js`
- Modify: `frontend/src/components/files/FilePane.vue` (~345-365)
- Modify: `frontend/src/artifact-shell/ArtifactShellApp.vue`, `frontend/src/artifact-shell/main.js`
- Modify: `src/twicc/artifacts/broker_html.py` (`_shell_html`/`artifact_shell_response`), `src/twicc/views.py::artifact_serve` (~3555 caller)
- Test: `tests/test_artifact_broker_html.py` (island content), manual for JS (no frontend test harness)

No frontend test runner exists — the backend island test is the only automated piece; correctness here rides on the precise diff below plus Task 8's end-to-end verify.

- [ ] **Step 1 (backend TDD): failing test** in `tests/test_artifact_broker_html.py`: `artifact_shell_response(bookmark_id=1, allowed_hosts={...}, denied_hosts={"http://localhost:9000": {"kind": "loopback"}})` island JSON contains `deniedHosts`. Run → TypeError.

- [ ] **Step 2: `broker_html.py`** — `_shell_html(bookmark_id, allowed_hosts, denied_hosts)` adds `"deniedHosts": denied_hosts or {}`; `artifact_shell_response(*, bookmark_id, allowed_hosts, denied_hosts: dict | None = None)` — the **default keeps the five existing calls in `tests/test_artifact_broker_html.py` green**; update the one caller `views.py::artifact_serve` → `artifact_shell_response(bookmark_id=bookmark.id, allowed_hosts=bookmark.allowed_hosts, denied_hosts=bookmark.denied_hosts)`. Run test → PASS. Run `uv run --active pytest tests/test_artifact_broker_html.py -q` (whole file must be green).

- [ ] **Step 3: `host.js`** — the core change (design §5; keep the file's comment style, update the header comment):
  - `createBrokerHost({ documentUrl, getBookmarkId, getAllowedHosts, getDeniedHosts, showPrompt, persistAllow, onDenied, mode, proxyUrl, onBlocked })` — **replace** the `allowedHosts` snapshot param with the two getters (no back-compat shim; all call sites change in this task). Normalize once at top: `const allowedNow = () => (typeof getAllowedHosts === 'function' ? getAllowedHosts() : {}) || {}` and same for `deniedNow`.
  - Delete the merged `const allowed = {...}` dict. `isAllowed(key, kind)` becomes: `const entry = sessionGranted[key] || allowedNow()[key]; return !!(entry && entry.kind === kind)`. In `gate()`'s allow paths, keep writing `sessionGranted[key]` but drop `allowed[key] = …`.
  - In `gate()`, deny decision: `if (decision === 'deny') { onDenied?.(url, target.kind); throw new Error('denied by user') }` (fire-and-forget; onDenied must never reject unhandled — the callers pass a `.catch(() => {})`-wrapped fetch).
  - In `proxyFetch()`, owner path, **precedence** (design §5 — own-dir stays first and unconditional):

```js
        // The artifact's own files → served directly, no prompt (§6.6).
        if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)

        // … share-mode block unchanged …

        // Persisted deny (design 2026-07-10 §5): checked first among egress
        // paths and read live, so a Deny in the bookmark dialog applies to an
        // open preview immediately and overrides an earlier session grant.
        const key = normalizeHostKey(req.url)
        if (deniedNow()[key]) {
            delete sessionGranted[key]
            throw new Error('denied by owner')
        }

        const pre = await callProxy(…)  // unchanged from here on
```

  Note: `key` was previously computed after the preflight — reuse this earlier `const key`, delete the later duplicate declaration.
- [ ] **Step 4: `useArtifactBroker.js`** — config passthrough: replace `allowedHosts: config.allowedHosts ?? {}` with `getAllowedHosts: config.getAllowedHosts`, add `getDeniedHosts: config.getDeniedHosts`, `onDenied: config.onDenied`. Update the JSDoc `getConfig` type.
- [ ] **Step 5: `FilePane.vue`** — in the broker config (~360): replace `allowedHosts: artifactBookmark.value?.allowed_hosts ?? {}` with

```js
                  getAllowedHosts: () => artifactBookmark.value?.allowed_hosts ?? {},
                  getDeniedHosts: () => artifactBookmark.value?.denied_hosts ?? {},
                  onDenied: (url, kind) => {
                      const id = artifactBookmark.value?.id
                      if (id == null) return
                      apiFetch(`/api/artifact-bookmarks/${id}/network-denials/`, {
                          method: 'POST',
                          headers: { 'content-type': 'application/json' },
                          body: JSON.stringify({ url, kind }),
                      }).catch(() => {})
                  },
```

  (Store reactivity makes the getters live: `artifact_bookmark_updated` broadcasts refresh `dataStore.artifactBookmarks`.)
- [ ] **Step 6: shell** — `main.js`: pass `deniedHosts: shellData.deniedHosts ?? {}` prop. `ArtifactShellApp.vue`: add `deniedHosts` prop; in the broker config replace `allowedHosts: props.allowedHosts` with `getAllowedHosts: () => props.allowedHosts`, `getDeniedHosts: () => props.deniedHosts` (static island → reload-only semantics, accepted by design §5), and `onDenied` = owner-mode-only POST like `persistAllow` (same fetch pattern, `.catch(() => {})`, skip when `props.bookmarkId == null` or share mode).
- [ ] **Step 7: rebuild the non-HMR bundles** — `cd /home/twidi/dev/twicc-poc/.worktrees/sharing/frontend && npm run build`. Expected: clean build, no errors.
- [ ] **Step 8: grep for stragglers** — `cd /home/twidi/dev/twicc-poc/.worktrees/sharing && rg -n "allowedHosts" frontend/src src/twicc` — every remaining use must be a getter call site, a prop/island name, or share-viewer code; no `allowedHosts:` config key may remain pointing at the old snapshot param.
- [ ] **Step 9: Commit** — `git add frontend/src/artifact-broker/host.js frontend/src/composables/useArtifactBroker.js frontend/src/components/files/FilePane.vue frontend/src/artifact-shell/ArtifactShellApp.vue frontend/src/artifact-shell/main.js src/twicc/artifacts/broker_html.py src/twicc/views.py tests/test_artifact_broker_html.py` (the built bundles under `src/twicc/static/` are **gitignored** — never add them), `feat(network-access): live allow/deny getters + persisted deny in the broker host`.

---

### Task 6: Extract `AccessLogList.vue` from ShareListPanel

**Files:**
- Create: `frontend/src/components/share/AccessLogList.vue`
- Modify: `frontend/src/components/share/ShareListPanel.vue` (~57-72 template, ~33-42 script, ~98-125 styles)

- [ ] **Step 1: Create the component** — move the "Recent views" panel's list verbatim (loading/empty states stay in the callers):

```vue
<script setup>
// Shared date/ip/user-agent access log list: per-share "Recent views"
// (ShareListPanel) and per-host denial details (ArtifactBookmarkDialog).
// Rows: { at, ip, user_agent, count? } — count > 1 shows a ×N badge.
import { ref } from 'vue'

defineProps({ entries: { type: Array, required: true } })
function when(iso) { try { return new Date(iso).toLocaleString() } catch { return '' } }
const expandedUa = ref({})
function toggleUa(i) { expandedUa.value[i] = !expandedUa.value[i] }
</script>

<template>
    <ul class="access-log">
        <li v-for="(a, i) in entries" :key="i">
            <span class="when">{{ when(a.at) }}</span>
            <span v-if="a.count > 1" class="count">×{{ a.count }}</span>
            <code v-if="a.ip">{{ a.ip }}</code>
            <span v-if="a.user_agent" class="ua" :class="{ 'ua--expanded': expandedUa[i] }"
                  role="button" tabindex="0"
                  :title="expandedUa[i] ? '' : 'Click to show the full user agent'"
                  @click="toggleUa(i)" @keydown.enter="toggleUa(i)">{{ a.user_agent }}</span>
        </li>
    </ul>
</template>

<style scoped>
/* Capped height: a busy log scrolls instead of blowing up the dialog. */
.access-log { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; max-height: 14rem; overflow-y: auto; }
.access-log li { display: flex; gap: 0.6rem; align-items: baseline; }
.when { color: var(--wa-color-text-quiet); white-space: nowrap; }
.count { color: var(--wa-color-text-quiet); font-size: 0.75rem; }
.ua { flex: 1; min-width: 5rem; color: var(--wa-color-text-quiet); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.ua:hover { color: var(--wa-color-text); }
.ua--expanded { overflow: visible; white-space: normal; word-break: break-word; }
</style>
```

- [ ] **Step 2: Use it in `ShareListPanel.vue`** — replace the `<ul>…</ul>` block with `<AccessLogList :entries="accesses[s.id]" />`; delete `expandedUa`/`toggleUa`/`when` (keep `when` only if still used elsewhere in the file — it isn't) and the now-dead styles (`.ua*`, `.when`, the `ul` rules inside `.share-views-panel`). Keep the panel wrapper + loading/empty `<p>`s.
- [ ] **Step 3: Verify in the running SPA** (dev server assumed running; do NOT restart it): open a share's "N views" — identical rendering. If not verifiable now, flag it for the Task 8 verify pass.
- [ ] **Step 4: Commit** — `git add frontend/src/components/share/AccessLogList.vue frontend/src/components/share/ShareListPanel.vue`, `refactor(share): extract AccessLogList from the recent-views panel`.

---

### Task 7: WS plumbing for `artifact_bookmark_denials_updated`

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js` (~1107, next to the bookmark cases)

- [ ] **Step 1: Add the case** (the `twicc:plan-changed` CustomEvent at ~1090 is the exact precedent):

```js
            case 'artifact_bookmark_denials_updated': {
                // Denial rows changed for this bookmark; an open bookmark dialog
                // refetches its Network access list (nothing stored globally).
                window.dispatchEvent(new CustomEvent('twicc:network-denials-updated', {
                    detail: { bookmarkId: msg.bookmark_id },
                }))
                break
            }
```

- [ ] **Step 2: Commit** — `git add frontend/src/composables/useWebSocket.js`, `feat(network-access): forward denial pings to the bookmark dialog`.

---### Task 8: "Network access" section in `ArtifactBookmarkDialog`

**Files:**
- Modify: `frontend/src/components/artifacts/ArtifactBookmarkDialog.vue`

The big UI task. Edit mode only. Follow the dialog-form conventions already in the file (event-bubbling guards, wa-* patterns). Widen the dialog: `--width: min(560px, calc(100vw - 2rem))`.

- [ ] **Step 1: Data layer (script setup):**

```js
// Network access (edit mode): denial rows + the bookmark's live dicts.
const denials = ref(null)           // null = loading; [] = none
const liveBookmark = computed(() => store.artifactBookmarks[existingId.value] || null)
const allowedHosts = computed(() => liveBookmark.value?.allowed_hosts || existingAllowedHosts.value || {})
const deniedHosts = computed(() => liveBookmark.value?.denied_hosts || {})

async function fetchDenials() {
    if (!isEditMode.value) return
    const res = await apiFetch(`/api/artifact-bookmarks/${existingId.value}/network-denials/`)
    denials.value = res.ok ? (await res.json()).denials : []
}
function onDenialsPing(e) {
    if (e.detail?.bookmarkId === existingId.value && dialogRef.value?.open) fetchDenials()
}
onMounted(() => window.addEventListener('twicc:network-denials-updated', onDenialsPing))
onBeforeUnmount(() => window.removeEventListener('twicc:network-denials-updated', onDenialsPing))
```

Call `fetchDenials()` from `open()` when editing (reset `denials.value = null` first). Import `apiFetch` from `../../utils/api`, add `computed`/`onMounted`/`onBeforeUnmount` imports.

- [ ] **Step 2: Grouping computed** — one entry per host, state-resolved, pending → denied → allowed:

```js
const hostRows = computed(() => {
    const rows = new Map() // host_key -> { host, kind, state, total, provenances: Map }
    const ensure = (host, kind, state) => { /* create-or-get, latest kind wins for pending */ }
    for (const [host, entry] of Object.entries(allowedHosts.value)) ensure(host, entry.kind, 'allowed')
    for (const [host, entry] of Object.entries(deniedHosts.value)) ensure(host, entry.kind, 'denied')
    for (const d of denials.value || []) {
        const row = ensure(d.host_key, d.kind, 'pending')  // does NOT override allowed/denied state
        row.total += d.count
        const pkey = d.share ? d.share.id : '__owner__'
        const prov = row.provenances.get(pkey) || { label: d.share ? (d.share.label || d.share.id) : 'You, in preview', count: 0, entries: [] }
        prov.count += d.count
        prov.entries.push({ at: d.last_at, ip: d.ip, user_agent: d.user_agent, count: d.count })
        row.provenances.set(pkey, prov)
    }
    const order = { pending: 0, denied: 1, allowed: 2 }
    return [...rows.values()].sort((a, b) => order[a.state] - order[b.state] || a.host.localeCompare(b.host))
})
```

- [ ] **Step 3: Actions** — every call refetches nothing manually: the WS broadcasts (`artifact_bookmark_updated` → store dicts; `…denials_updated` → `fetchDenials`) drive the refresh. Selection: `const checked = reactive({})` (host → bool, pending rows only); `selectedHosts` computed.

```js
async function postHost(pathSuffix, method, body) { /* apiFetch wrapper, throws on !ok, sets sectionError */ }
function kindOf(host) { /* hostRows lookup → kind */ }
async function performAllow(hosts) {
    for (const h of hosts) {
        const kind = kindOf(h)
        if (!kind) continue  // row vanished (e.g. cross-tab change) — skip, never POST kind: undefined
        await postHost('allowed-hosts', 'POST', { url: h, kind })
    }
    hosts.forEach((h) => delete checked[h])
}
async function allowHosts(hosts) {
    // Non-public gate (design N7): any loopback/lan host stages the confirm
    // callout instead of acting. A plain Allow click ALWAYS (re-)arms with
    // exactly these hosts — never treat an already-armed flag as consent for a
    // DIFFERENT selection (that would let a second click bypass the warning).
    // Only confirmAllowNow (the "Allow anyway" button) consumes the armed set.
    if (hosts.some((h) => kindOf(h) !== 'public')) { confirmAllow.hosts = hosts; confirmAllow.armed = true; return }
    await performAllow(hosts)
}
async function confirmAllowNow() {
    const hosts = confirmAllow.hosts
    confirmAllow.armed = false
    await performAllow(hosts)
}
```

(The "Allow anyway" button binds `confirmAllowNow`, not `allowHosts`.)

```js
const denyHost = (h) => postHost('denied-hosts', 'POST', { url: h, kind: kindOf(h) })
const undenyHost = (h) => postHost('denied-hosts', 'DELETE', { url: h })
const removeAllowed = (h) => postHost('allowed-hosts', 'DELETE', { url: h })
```

- [ ] **Step 4: Template** — after the Scope form-group, edit-mode only:

```html
<div v-if="isEditMode" class="form-group network-section">
    <label class="form-label">Network access</label>
    <p v-if="denials === null" class="muted">Loading…</p>
    <p v-else-if="!hostRows.length" class="muted">No network activity on this artifact.</p>
    <template v-else>
        <div v-for="row in hostRows" :key="row.host" class="host-row">
            <div class="host-row-main">
                <wa-checkbox v-if="row.state === 'pending'" :checked="!!checked[row.host]"
                             @change.stop="checked[row.host] = $event.target.checked"></wa-checkbox>
                <wa-tag size="small" :variant="row.state === 'allowed' ? 'success' : (row.state === 'denied' ? 'danger' : 'warning')">{{ row.state }}</wa-tag>
                <code class="host-key">{{ row.host }}</code>
                <wa-tag v-if="row.kind !== 'public'" size="small" variant="warning">{{ row.kind }}</wa-tag>
                <button v-if="row.total" class="denial-count" type="button" @click="toggleHost(row.host)">
                    {{ row.total }} refused</button>
                <span class="host-actions">
                    <wa-button v-if="row.state !== 'allowed'" size="small" appearance="outlined" @click="allowHosts([row.host])">Allow</wa-button>
                    <wa-button v-if="row.state === 'pending'" size="small" appearance="outlined" @click="denyHost(row.host)">Deny</wa-button>
                    <wa-button v-if="row.state === 'denied'" size="small" appearance="plain" @click="undenyHost(row.host)">Un-deny</wa-button>
                    <wa-button v-if="row.state === 'allowed'" size="small" appearance="plain" variant="danger" @click="removeAllowed(row.host)">Remove</wa-button>
                </span>
            </div>
            <!-- level 2: provenances -->
            <ul v-if="expandedHosts[row.host]" class="prov-list">
                <li v-for="[pkey, p] in row.provenances" :key="pkey">
                    <span class="prov-label">{{ p.label }}</span>
                    <button class="denial-count" type="button" @click="toggleProv(row.host, pkey)">{{ p.count }}×</button>
                    <!-- level 3: detail -->
                    <AccessLogList v-if="expandedProvs[`${row.host}|${pkey}`]" :entries="p.entries" />
                </li>
            </ul>
        </div>
        <wa-button v-if="selectedHosts.length" size="small" variant="brand" @click="allowHosts(selectedHosts)">
            Allow selected ({{ selectedHosts.length }})</wa-button>
        <wa-callout v-if="confirmAllow.armed" variant="warning">
            <!-- Adaptive non-public warning (mirror ArtifactBrokerPrompt's copy — read
                 that component and reuse its loopback/lan sentences): where the host
                 resolves, TwiCC's server makes the request, and anonymous viewers of
                 EVERY share of this artifact will reach it. -->
            … <wa-button size="small" variant="warning" @click="confirmAllowNow">Allow anyway</wa-button>
            <wa-button size="small" appearance="plain" @click="confirmAllow.armed = false">Cancel</wa-button>
        </wa-callout>
        <wa-callout v-if="sectionError" variant="danger" size="small">{{ sectionError }}</wa-callout>
    </template>
    <div class="form-hint">Hosts this artifact may reach — for you in preview and for the viewers of every share. Refused requests are listed with who triggered them.</div>
</div>
```

Import `AccessLogList` from `../share/AccessLogList.vue`. Small state: `expandedHosts` / `expandedProvs` reactive maps with their togglers `toggleHost(host)` / `toggleProv(host, pkey)` (key `` `${host}|${pkey}` ``), `confirmAllow = reactive({ armed: false, hosts: [] })`, `sectionError = ref('')`. Level-3 entries: sort `p.entries` by `at` desc when building. Owner rows have no ip/ua → `AccessLogList` renders just the date (+ ×N), which is the intended "You, in preview" detail.

- [ ] **Step 5: Owner label copy check** — level-2 owner label is exactly `You, in preview` (spec §8.1); share label falls back to the share id.
- [ ] **Step 6: Verify end-to-end in the running SPA** (do not restart servers; if the backend needs the new endpoints and hasn't been restarted by the user yet, flag it and pause): bookmark an HTML artifact that fetches an external host; deny in preview → open the edit dialog → host listed pending with "You, in preview"; Allow a public host → moves to allowed, counts gone; Deny → tag flips, preview stops prompting (reload the preview iframe); Remove → gone. Create a share, view it (share host), have the artifact hit a non-allowed host → within ~30 s the dialog updates with the share provenance.
- [ ] **Step 7: Commit** — `git add frontend/src/components/artifacts/ArtifactBookmarkDialog.vue`, `feat(network-access): unified allow/deny host list in the bookmark dialog`.

---

### Task 9: ShareDialog bridge

**Files:**
- Modify: `frontend/src/components/share/ShareDialog.vue` (artifact `<template v-else>` block ~242-275)

- [ ] **Step 1:** async back-reference (HMR cycle rule — `ArtifactBookmarkDialog` statically imports `ShareDialog`):

```js
import { defineAsyncComponent } from 'vue'
const ArtifactBookmarkDialog = defineAsyncComponent(() => import('../artifacts/ArtifactBookmarkDialog.vue'))
const bookmarkDialogRef = ref(null)
const dataStore = useDataStore()  // import from ../../stores/data
function openBookmarkDialog() {
    const bm = dataStore.artifactBookmarks[props.bookmarkId]
    if (bm) bookmarkDialogRef.value?.open(bm)
}
```

- [ ] **Step 2:** in the artifact template block, replace the two static "open the artifact and approve hosts" hints' wording with a pointer to the dialog and add the button + nested dialog (keep the existing callouts; add after them):

```html
<p class="net-hint">
    Network access (allowed and refused hosts) is managed in the bookmark settings.
    <wa-button size="small" appearance="outlined" @click="openBookmarkDialog">Manage network access…</wa-button>
</p>
```

and at the end of the template, inside the root `wa-dialog` (stacking works — this file is itself nested inside the bookmark dialog today): `<ArtifactBookmarkDialog v-if="kind === 'artifact'" ref="bookmarkDialogRef" @saved="() => {}" @removed="emit('close')" />`.

- [ ] **Step 3: Verify** — from the global Shares manager, edit an artifact share → button opens the bookmark dialog on top, share dialog stays open behind; check no `wa-hide` bubbling closes the outer dialog (both dialogs already guard on `e.target`).
- [ ] **Step 4: Commit** — `git add frontend/src/components/share/ShareDialog.vue`, `feat(network-access): manage-network-access bridge from the share dialog`.

---

### Task 10: Docs + final pass

**Files:**
- Modify: `CLAUDE.md` (ArtifactBookmark bullet in Database Models + the Artifact Network Broker section, one line each)
- Modify: `AGENTS.md` (mirror — CLAUDE.md changes must propagate)

- [ ] **Step 1: CLAUDE.md** — in the `ArtifactBookmark` model bullet, after the `allowed_hosts` sentence, add: `denied_hosts` (same shape) records explicit owner "deny" decisions (auto-refused without prompt in preview); **`ArtifactNetworkDenial`** rows record refused broker fetches with provenance (share/ip/ua, NULL share = owner preview; upserted+pruned by `artifacts/denial_tracking.py`, purged on allow) and power the bookmark dialog's Network access section. Keep it to 2-3 lines — concise, no internals beyond what a future agent needs.
- [ ] **Step 2: AGENTS.md** — condensed mirror of the same addition.
- [ ] **Step 3: Full test suite** — `cd /home/twidi/dev/twicc-poc/.worktrees/sharing && uv run --active pytest -q` → all green. `uv run --active ruff check src tests` → clean.
- [ ] **Step 4: Commit** — `git add CLAUDE.md AGENTS.md`, `docs: document artifact network-access management`.
- [ ] **Step 5: Remind the user** (do not do these yourself): restart the backend via devctl to apply migration `0125` and pick up the new endpoints/flush task; the frontend bundles were rebuilt in Task 5 (`npm run build`) — no other action needed.
