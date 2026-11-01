# Artifact Data Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an HTML artifact a writable `data/` folder under its own directory, reachable with plain `fetch` (PUT/DELETE/list) plus a `window.twicc.data` sugar, per `docs/plans/2026-08-05-artifact-data-persistence-design.md`.

**Architecture:** No new transport — the broker host already forwards method/headers/body of same-origin own-dir requests (`hostDirectFetch`); the work is (1) a byte-oriented `data_store` helper module, (2) write dispatch in the three artifact-serving views behind a host-set header, (3) host-side write routing + consent, (4) the shim sugar, (5) a reload-heuristic fix, (6) docs.

**Tech Stack:** Django async views + orjson + pytest/pytest-django (backend); vanilla JS shim + framework-agnostic host + Vue 3 (frontend). Shim/shell bundles are NOT HMR'd — `cd frontend && npm run build` after touching them.

## Global Constraints

- Design doc: `docs/plans/2026-08-05-artifact-data-persistence-design.md` — cite it in docstrings as "design 2026-08-05".
- Writes confined to `<document dir>/data/`; two independent locks (host `ownDir` check + server header check). §3.
- Caps: **10 MB per file**, **100 MB per `data/` tree**; explicit JSON error payloads. §4.
- Consent: silent under a session's artifacts root, one tab-lifetime prompt elsewhere. §6.
- Share mode: strictly read-only. §8.
- All code/comments/docs in English. Conventional Commits with a body + `Co-Authored-By: Claude <model> <noreply@anthropic.com>` trailer (exact running model name).
- Backend JSON via **orjson**, never stdlib `json`.
- Tests: `uv run pytest tests/<file> -v` (pyproject forces `--ds=twicc.settings_test`). Run from the repo root; if working in a worktree, `cd` there first and use `uv run --active`.
- Do NOT restart dev servers, run `npm install`, or `migrate` (no migration exists in this plan anyway). Remind the user at the end to restart via devctl.
- Do NOT touch the CHANGELOG.

---

### Task 1: `data_store` backend helpers

**Files:**
- Create: `src/twicc/artifacts/data_store.py`
- Test: `tests/test_artifact_data_store.py`

**Interfaces:**
- Produces (consumed by Task 2):
  - `MAX_DATA_FILE_BYTES = 10 * 1024 * 1024`, `MAX_DATA_TREE_BYTES = 100 * 1024 * 1024`
  - `resolve_data_target(doc_dir: str, target: str) -> str | None`
  - `write_data_file(data_root: str, target: str, body: bytes) -> tuple[dict, int]`
  - `delete_data_file(target: str) -> tuple[dict, int]`
  - `list_data_dir(data_root: str) -> tuple[dict, int]`
  - All `tuple[dict, int]` returns are `(json_payload, http_status)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_artifact_data_store.py`:

```python
"""The artifact data-store helpers (design 2026-08-05 §3/§4): pure filesystem
byte-store confined to a document's ``data/`` subtree — target resolution,
atomic write with size caps, delete, recursive listing."""

from __future__ import annotations

import os

import pytest

from twicc.artifacts.data_store import (
    MAX_DATA_FILE_BYTES,
    MAX_DATA_TREE_BYTES,
    delete_data_file,
    list_data_dir,
    resolve_data_target,
    write_data_file,
)


@pytest.fixture
def doc_dir(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "index.html").write_bytes(b"<html></html>")
    return str(d)


# ── resolve_data_target ───────────────────────────────────────────────────────


def test_resolve_accepts_file_under_data(doc_dir):
    out = resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "x.json"))
    assert out == os.path.join(os.path.realpath(doc_dir), "data", "x.json")


def test_resolve_accepts_nested_file(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "a", "b.json")) is not None


def test_resolve_accepts_the_data_dir_itself(doc_dir):
    out = resolve_data_target(doc_dir, os.path.join(doc_dir, "data"))
    assert out == os.path.join(os.path.realpath(doc_dir), "data")


def test_resolve_rejects_outside_data(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "index.html")) is None
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "other", "x.json")) is None


def test_resolve_rejects_traversal(doc_dir):
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "..", "index.html")) is None
    assert resolve_data_target(doc_dir, os.path.join(doc_dir, "data", "..", "..", "x")) is None


def test_resolve_rejects_symlink_escape(doc_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    data = os.path.join(doc_dir, "data")
    os.makedirs(data)
    os.symlink(str(outside), os.path.join(data, "link"))
    assert resolve_data_target(doc_dir, os.path.join(data, "link", "x.json")) is None


# ── write_data_file ───────────────────────────────────────────────────────────


def _data_root(doc_dir):
    return os.path.join(os.path.realpath(doc_dir), "data")


def test_write_creates_file_and_parents(doc_dir):
    root = _data_root(doc_dir)
    payload, status = write_data_file(root, os.path.join(root, "a", "b.json"), b'{"x":1}')
    assert status == 200
    assert payload["ok"] is True
    assert payload["size"] == 7
    with open(os.path.join(root, "a", "b.json"), "rb") as fp:
        assert fp.read() == b'{"x":1}'


def test_write_overwrites(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"one")
    payload, status = write_data_file(root, os.path.join(root, "x.json"), b"two!")
    assert status == 200
    with open(os.path.join(root, "x.json"), "rb") as fp:
        assert fp.read() == b"two!"


def test_write_refuses_file_over_cap(doc_dir):
    root = _data_root(doc_dir)
    payload, status = write_data_file(root, os.path.join(root, "big"), b"x" * (MAX_DATA_FILE_BYTES + 1))
    assert status == 413
    assert payload["error"] == "too_large"
    assert payload["max_bytes"] == MAX_DATA_FILE_BYTES
    assert not os.path.exists(os.path.join(root, "big"))


def test_write_refuses_tree_over_quota(doc_dir, monkeypatch):
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_TREE_BYTES", 10)
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "a"), b"12345678")
    payload, status = write_data_file(root, os.path.join(root, "b"), b"123")
    assert status == 413
    assert payload["error"] == "quota_exceeded"


def test_write_quota_counts_replaced_file_once(doc_dir, monkeypatch):
    # Overwriting an 8-byte file with 9 bytes under a 10-byte quota must pass:
    # the old size is reclaimed by the replace.
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_TREE_BYTES", 10)
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "a"), b"12345678")
    payload, status = write_data_file(root, os.path.join(root, "a"), b"123456789")
    assert status == 200


# ── delete_data_file ──────────────────────────────────────────────────────────


def test_delete_removes_file(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"{}")
    payload, status = delete_data_file(os.path.join(root, "x.json"))
    assert status == 200 and payload["ok"] is True
    assert not os.path.exists(os.path.join(root, "x.json"))


def test_delete_missing_is_404(doc_dir):
    payload, status = delete_data_file(os.path.join(_data_root(doc_dir), "nope"))
    assert status == 404


def test_delete_directory_is_400(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "sub", "x"), b"1")
    payload, status = delete_data_file(os.path.join(root, "sub"))
    assert status == 400


# ── list_data_dir ─────────────────────────────────────────────────────────────


def test_list_recursive_relative_paths(doc_dir):
    root = _data_root(doc_dir)
    write_data_file(root, os.path.join(root, "x.json"), b"{}")
    write_data_file(root, os.path.join(root, "sub", "y.bin"), b"12345")
    payload, status = list_data_dir(root)
    assert status == 200
    by_path = {f["path"]: f for f in payload["files"]}
    assert set(by_path) == {"x.json", "sub/y.bin"}
    assert by_path["sub/y.bin"]["size"] == 5
    assert isinstance(by_path["x.json"]["mtime"], str)


def test_list_missing_data_dir_is_empty(doc_dir):
    payload, status = list_data_dir(_data_root(doc_dir))
    assert status == 200
    assert payload == {"files": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_artifact_data_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'twicc.artifacts.data_store'`

- [ ] **Step 3: Implement `src/twicc/artifacts/data_store.py`**

```python
"""Byte store for an HTML artifact's ``data/`` subtree (design 2026-08-05 §3/§4).

Pure filesystem helpers, no Django imports: the serving views (file_raw,
standalone_file_raw, artifact_serve) resolve WHO may write where — these
helpers only enforce the ``data/`` confinement, the size caps, and atomicity.
Every mutator returns ``(json_payload, http_status)`` so the views translate
uniformly.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

MAX_DATA_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_DATA_TREE_BYTES = 100 * 1024 * 1024  # 100 MB per data/ tree


def resolve_data_target(doc_dir: str, target: str) -> str | None:
    """Resolve ``target`` and require it inside ``<doc_dir>/data/``.

    Symlinks are resolved on both sides before comparison (a link under
    ``data/`` pointing outside must not escape). The ``data/`` directory
    itself is accepted (the listing endpoint targets it). Returns the
    resolved path, or ``None`` when the target falls outside.
    """
    data_root = os.path.join(os.path.realpath(doc_dir), "data")
    resolved = os.path.realpath(target)
    if resolved == data_root or resolved.startswith(data_root + os.sep):
        return resolved
    return None


def _tree_size(data_root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(data_root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def write_data_file(data_root: str, target: str, body: bytes) -> tuple[dict, int]:
    """Create or overwrite ``target`` atomically (temp file + ``os.replace``).

    Missing parent directories under ``data/`` are created. Refused with an
    explicit payload when the body exceeds the per-file cap or would push the
    tree over its quota (the replaced file's current size is reclaimed first).
    """
    if len(body) > MAX_DATA_FILE_BYTES:
        return {"error": "too_large", "max_bytes": MAX_DATA_FILE_BYTES, "size": len(body)}, 413
    existing = 0
    try:
        existing = os.path.getsize(target)
    except OSError:
        pass
    used = _tree_size(data_root) if os.path.isdir(data_root) else 0
    if used - existing + len(body) > MAX_DATA_TREE_BYTES:
        return {
            "error": "quota_exceeded",
            "max_bytes": MAX_DATA_TREE_BYTES,
            "used_bytes": used - existing,
            "size": len(body),
        }, 413
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".twicc-data-")
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(body)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        return {"error": "write_failed", "detail": str(exc)}, 500
    return {"ok": True, "size": len(body)}, 200


def delete_data_file(target: str) -> tuple[dict, int]:
    """Delete one file. Directories are refused — files only (design §4)."""
    if os.path.isdir(target):
        return {"error": "is_directory"}, 400
    try:
        os.unlink(target)
    except FileNotFoundError:
        return {"error": "not_found"}, 404
    except OSError as exc:
        return {"error": "delete_failed", "detail": str(exc)}, 500
    return {"ok": True}, 200


def list_data_dir(data_root: str) -> tuple[dict, int]:
    """Recursive index of the ``data/`` tree: relative path, size, ISO mtime.

    A missing ``data/`` directory is an empty listing, not an error — the
    artifact probes its store before the first write.
    """
    files = []
    if os.path.isdir(data_root):
        for dirpath, _dirnames, filenames in os.walk(data_root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                files.append(
                    {
                        "path": os.path.relpath(full, data_root).replace(os.sep, "/"),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
    return {"files": files}, 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_artifact_data_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/twicc/artifacts/data_store.py tests/test_artifact_data_store.py
git commit  # feat(artifacts): add the data/ byte-store helpers — body explains design 2026-08-05 §3/§4
```

---

### Task 2: write dispatch in the three serving views

**Files:**
- Modify: `src/twicc/views.py` (`file_raw` ~2115, `standalone_file_raw` ~2143, `artifact_serve` ~3753; new helper next to `_serve_artifact_file` ~2083)
- Modify: `src/twicc/settings.py` (upload-size bump, after the `MIDDLEWARE` block ~172)
- Test: `tests/test_artifact_data_writes.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces (relied on by Task 4's host code):
  - Request header **`X-Twicc-Artifact-Doc`** = the serving document's URL **pathname** (percent-encoded, as `new URL(u).pathname` yields). Server unquotes it.
  - `PUT/DELETE` on the three routes with that header → data-store semantics; absent header → 405 (unchanged `HttpResponseNotAllowed`).
  - `GET` on a **directory** target with that header → the JSON listing; without the header, GET is byte-for-byte unchanged.
  - New helper `_dispatch_data_request(request, doc_dir: str) -> HttpResponse | None` in `views.py`.

**Threat-model note for the implementer (do NOT "harden" past this):** the header is trusted because the broker HOST overwrites it before forwarding (Task 4) — the artifact cannot forge it. An authenticated user forging it with curl gains nothing new: the standalone file-modify endpoints (`_standalone_file_modify`) already let them create/delete arbitrary paths. Same logic as broker design §6.4.

- [ ] **Step 1: Write the failing tests**

**Repo test convention (MANDATORY):** this repo has NO pytest async plugin — an `async def test` errors out under pytest 9. Every HTTP test is a sync `def` driving Django's `AsyncClient` through a `_run` helper, with a `client(settings)` fixture that clears `TWICC_PASSWORD_HASH` (see `tests/test_session_artifacts.py:105`, `tests/test_artifact_bookmarks.py:95`). `AsyncClient.put/delete(..., headers={...})` is the header-passing form (Django 6.0.4).

Create `tests/test_artifact_data_writes.py`:

```python
"""Write dispatch on the artifact-serving routes (design 2026-08-05 §3/§4):
``PUT``/``DELETE``/dir-``GET`` gated by the host-set ``X-Twicc-Artifact-Doc``
header, confined to ``<doc dir>/data/`` AND the route's own confinement root.
Exercised end-to-end through Django's ``AsyncClient`` on the standalone route
(the richest matrix), plus the project route and the bookmark route."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import orjson
import pytest
from django.test import AsyncClient

pytestmark = pytest.mark.django_db(transaction=True)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def _b64(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


@pytest.fixture
def artifact(tmp_path):
    """A demo artifact under a fake artifacts root: <root>/demo/index.html."""
    root = tmp_path / "artifacts-root"
    doc_dir = root / "demo"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.html").write_bytes(b"<html><head></head></html>")
    return {"root": root, "doc_dir": doc_dir}


def _url(artifact, rel: str) -> str:
    # doc_dir is absolute (starts with /), so "<b64>{doc_dir}" joins cleanly.
    return f"/api/file-raw/{_b64(str(artifact['root']))}{artifact['doc_dir']}/{rel}"


def _doc(artifact) -> str:
    return f"/api/file-raw/{_b64(str(artifact['root']))}{artifact['doc_dir']}/index.html"


# ── standalone route (/api/file-raw/<root_b64>/…) ─────────────────────────────


def test_put_without_header_is_405(client, artifact):
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}"))
    assert resp.status_code == 405


def test_put_creates_file(client, artifact):
    resp = _run(client.put(_url(artifact, "data/x.json"), b'{"a":1}',
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 200
    assert orjson.loads(resp.content)["ok"] is True
    assert (artifact["doc_dir"] / "data" / "x.json").read_bytes() == b'{"a":1}'


def test_put_outside_data_is_403(client, artifact):
    resp = _run(client.put(_url(artifact, "index.html"), b"pwned",
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 403
    assert (artifact["doc_dir"] / "index.html").read_bytes() == b"<html><head></head></html>"


def test_put_foreign_prefix_header_is_405(client, artifact, tmp_path):
    # A doc header under ANOTHER root_b64 fails the prefix check → treated as
    # absent (no proof the write comes from a document this route serves).
    other = tmp_path / "elsewhere"
    other.mkdir()
    bad_doc = f"/api/file-raw/{_b64(str(other))}{other}/index.html"
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}",
                           headers={"x-twicc-artifact-doc": bad_doc}))
    assert resp.status_code == 405


def test_put_doc_outside_root_is_403(client, artifact, tmp_path):
    # SAME route prefix, but the claimed doc lives outside the decoded root:
    # the doc must satisfy the same confinement as the target.
    outside = tmp_path / "outside-root" / "page"
    outside.mkdir(parents=True)
    bad_doc = f"/api/file-raw/{_b64(str(artifact['root']))}{outside}/index.html"
    resp = _run(client.put(_url(artifact, "data/x.json"), b"{}",
                           headers={"x-twicc-artifact-doc": bad_doc}))
    assert resp.status_code == 403


def test_delete_and_404(client, artifact):
    h = {"x-twicc-artifact-doc": _doc(artifact)}
    _run(client.put(_url(artifact, "data/x.json"), b"{}", headers=h))
    assert _run(client.delete(_url(artifact, "data/x.json"), headers=h)).status_code == 200
    assert _run(client.delete(_url(artifact, "data/x.json"), headers=h)).status_code == 404


def test_dir_get_with_header_lists(client, artifact):
    h = {"x-twicc-artifact-doc": _doc(artifact)}
    _run(client.put(_url(artifact, "data/sub/y.json"), b"12345", headers=h))
    resp = _run(client.get(_url(artifact, "data/"), headers=h))
    assert resp.status_code == 200
    assert [f["path"] for f in orjson.loads(resp.content)["files"]] == ["sub/y.json"]


def test_dir_get_with_header_before_first_write_is_empty(client, artifact):
    # data/ does not exist yet → empty listing, not 404 (the artifact probes
    # its store before the first write; design §4).
    resp = _run(client.get(_url(artifact, "data/"),
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 200
    assert orjson.loads(resp.content) == {"files": []}


def test_dir_get_without_header_stays_404(client, artifact):
    (artifact["doc_dir"] / "data").mkdir()
    resp = _run(client.get(_url(artifact, "data/")))
    assert resp.status_code == 404


def test_file_get_unchanged(client, artifact):
    resp = _run(client.get(_url(artifact, "index.html")))
    assert resp.status_code == 200


def test_oversize_put_is_413_with_payload(client, artifact, monkeypatch):
    monkeypatch.setattr("twicc.artifacts.data_store.MAX_DATA_FILE_BYTES", 8)
    resp = _run(client.put(_url(artifact, "data/big"), b"123456789",
                           headers={"x-twicc-artifact-doc": _doc(artifact)}))
    assert resp.status_code == 413
    assert orjson.loads(resp.content)["error"] == "too_large"
```

Then, in the same file, the **project route** and the **bookmark route**. Copy the `project`, `session` and `artifacts_root` fixture bodies from `tests/test_artifact_bookmarks.py` verbatim (a `Project` row whose `directory` sits under `tmp_path`; artifacts root monkeypatched); align `ArtifactBookmark.objects.create` field names with `core/models.py` at implementation time:

```python
# ── project route (/api/projects/<id>/file-raw/…) ────────────────────────────


def test_project_route_put_creates_file(client, project):
    doc_dir = Path(project.directory) / "widget"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "index.html").write_bytes(b"<html></html>")
    url = f"/api/projects/{project.id}/file-raw{doc_dir}/data/x.json"
    doc = f"/api/projects/{project.id}/file-raw{doc_dir}/index.html"
    resp = _run(client.put(url, b"{}", headers={"x-twicc-artifact-doc": doc}))
    assert resp.status_code == 200
    assert (doc_dir / "data" / "x.json").read_bytes() == b"{}"


def test_project_route_foreign_project_header_is_405(client, project):
    doc_dir = Path(project.directory) / "widget"
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "index.html").write_bytes(b"<html></html>")
    url = f"/api/projects/{project.id}/file-raw{doc_dir}/data/x.json"
    doc = f"/api/projects/-some-other-project/file-raw{doc_dir}/index.html"
    resp = _run(client.put(url, b"{}", headers={"x-twicc-artifact-doc": doc}))
    assert resp.status_code == 405


# ── bookmark route (/artifacts/<id>/…) ───────────────────────────────────────


@pytest.fixture
def bookmark(session, artifacts_root):
    from twicc.core.models import ArtifactBookmark
    target = artifacts_root / session.id / "demo" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"<html><head></head></html>")
    return ArtifactBookmark.objects.create(
        session=session, project=session.project,
        relative_path="demo/index.html", name="Demo", scope="all",
    )


def _bm_headers(bookmark):
    return {"x-twicc-artifact-doc": f"/artifacts/{bookmark.id}/__twicc_doc__"}


def test_bookmark_route_put_and_read_back(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/data/x.json", b'{"a":1}',
                           headers=_bm_headers(bookmark)))
    assert resp.status_code == 200
    resp = _run(client.get(f"/artifacts/{bookmark.id}/data/x.json"))
    assert resp.status_code == 200


def test_bookmark_route_put_without_header_is_405(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/data/x.json", b"{}"))
    assert resp.status_code == 405


def test_bookmark_route_put_outside_data_is_403(client, bookmark):
    resp = _run(client.put(f"/artifacts/{bookmark.id}/other.txt", b"x",
                           headers=_bm_headers(bookmark)))
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_artifact_data_writes.py -v`
Expected: FAIL — 405 on every PUT (method guard) and 404 on the dir GET.

- [ ] **Step 3: Bump Django's body-size ceiling in `settings.py`**

Django's default `DATA_UPLOAD_MAX_MEMORY_SIZE` is 2.5 MB — accessing `request.body` on a 10 MB PUT would raise `RequestDataTooLarge` before our cap ever runs. Add near the middleware block:

```python
# Artifact data-store PUTs (design 2026-08-05 §4) carry up to 10 MB bodies;
# Django's 2.5 MB default would reject them at request.body. Kept modestly
# above the per-file cap so the data_store's own 413 stays the visible limit.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
```

- [ ] **Step 4: Implement the shared dispatch helper in `views.py`**

Place next to `_serve_artifact_file` (~line 2083):

```python
def _dispatch_data_request(request, doc_dir: str, target: str):
    """Handle a ``data/`` store request on an artifact-serving route
    (design 2026-08-05 §3/§4): ``PUT``/``DELETE`` on a file, ``GET`` on the
    ``data/`` tree (the listing). Called only when the ``X-Twicc-Artifact-Doc``
    header checked out; ``doc_dir`` is the serving document's directory,
    already validated by the caller against the route's own confinement — this
    helper only enforces the ``data/`` boundary. ``target`` is the caller's
    already-normalized filesystem path for the request. Returns ``None`` when
    the request is a plain file ``GET``/``HEAD`` (caller keeps its existing
    raw-serving path).
    """
    from twicc.artifacts import data_store

    data_root = os.path.join(os.path.realpath(doc_dir), "data")
    if request.method in ("GET", "HEAD"):
        resolved = os.path.realpath(target)
        # The data/ root itself lists even when it does not exist yet (empty
        # store — the artifact probes before its first write); an existing
        # subdirectory under it lists its own subtree. Files stay raw-served.
        if resolved == data_root or (
            resolved.startswith(data_root + os.sep) and os.path.isdir(resolved)
        ):
            payload, status = data_store.list_data_dir(resolved)
            return JsonResponse(payload, status=status)
        return None
    resolved = data_store.resolve_data_target(doc_dir, target)
    if resolved is None:
        return JsonResponse({"error": "outside_data"}, status=403)
    if request.method == "PUT":
        payload, status = data_store.write_data_file(data_root, resolved, request.body)
    else:
        payload, status = data_store.delete_data_file(resolved)
    return JsonResponse(payload, status=status)
```

- [ ] **Step 5: Wire the three views**

**`standalone_file_raw` (~2143):** replace the method guard and thread the header:

```python
    if request.method not in ("GET", "HEAD", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "HEAD", "PUT", "DELETE"])
```

After the existing root decoding + `validate_standalone_root` + realpath confinement checks (keep them, they now also cover write targets), resolve the doc dir and dispatch:

```python
    doc_dir = _doc_dir_from_header(request, prefix=f"/api/file-raw/{root_b64}/")
    needs_doc = request.method in ("PUT", "DELETE")
    if needs_doc and doc_dir is None:
        return HttpResponseNotAllowed(["GET", "HEAD"])  # writes only from a served document
    if doc_dir is not None:
        # The doc itself must satisfy the same confinement as the target.
        if validate_standalone_root(doc_dir, root) is not None:
            return JsonResponse({"error": "doc_outside_root"}, status=403)
        handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir, normalized)
        if handled is not None:
            return handled
```

with the small header parser placed next to `_dispatch_data_request`:

```python
def _doc_dir_from_header(request, *, prefix: str) -> str | None:
    """Filesystem directory of the serving document, from the host-set
    ``X-Twicc-Artifact-Doc`` header (its URL pathname). ``None`` when absent
    or not under this route's own ``prefix`` — a doc served by another route
    (or a forged value) never authorizes a write here."""
    from urllib.parse import unquote

    raw = request.headers.get("X-Twicc-Artifact-Doc")
    if not raw:
        return None
    doc_path = unquote(raw)
    if not doc_path.startswith(prefix):
        return None
    return os.path.dirname(_normalize_raw_filepath(doc_path[len(prefix):]))
```

**`file_raw` (~2115):** same guard change; prefix depends on scope:

```python
    if session_id:
        prefix = f"/api/projects/{project_id}/sessions/{session_id}/file-raw/"
    else:
        prefix = f"/api/projects/{project_id}/file-raw/"
    doc_dir = _doc_dir_from_header(request, prefix=prefix)
    if request.method in ("PUT", "DELETE") and doc_dir is None:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if doc_dir is not None:
        _s, _d, doc_error = await sync_to_async(validate_path)(project_id, doc_dir, session_id=session_id)
        if doc_error:
            return doc_error
        handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir, normalized)
        if handled is not None:
            return handled
```

(The existing `validate_path` on the target's dirname stays and now runs for writes too — order it before the dispatch.)

**`artifact_serve` (~3753):** guard becomes `("GET", "HEAD", "PUT", "DELETE")`. The doc dir is intrinsic (the bookmark), the header is presence+prefix only:

```python
    if request.method in ("PUT", "DELETE") or (
        request.method in ("GET", "HEAD") and request.headers.get("X-Twicc-Artifact-Doc")
    ):
        doc_dir_fs = None
        doc_dir = _doc_dir_from_header(request, prefix=f"/artifacts/{bookmark_id}/")
        if doc_dir is not None:
            # Header checks out for THIS bookmark → doc dir on disk:
            doc_dir_fs = os.path.dirname(abs_root) if abs_root else None
        if request.method in ("PUT", "DELETE") and doc_dir_fs is None:
            return HttpResponseNotAllowed(["GET", "HEAD"])
        if doc_dir_fs is not None and asset not in ("", ARTIFACT_INNER_DOC_PATH):
            target = confined_artifact_path(
                bookmark.session_id, os.path.join(os.path.dirname(bookmark.relative_path), asset)
            )
            # confined_artifact_path realpaths a possibly-not-yet-existing file fine;
            # None here means escape from the session's artifacts dir → 403 for writes.
            if request.method in ("PUT", "DELETE") and target is None:
                return JsonResponse({"error": "outside_root"}, status=403)
            if target is not None:
                handled = await asyncio.to_thread(_dispatch_data_request, request, doc_dir_fs, target)
                if handled is not None:
                    return handled
```

Check at implementation time that `confined_artifact_path` accepts a non-existent leaf (it realpaths — it does); keep the rest of `artifact_serve` untouched.

- [ ] **Step 6: Run the new tests, then the full suite**

Run: `uv run pytest tests/test_artifact_data_writes.py -v` → all PASS
Run: `uv run pytest tests/test_session_artifacts.py tests/test_artifact_bookmarks.py tests/test_artifact_broker_html.py tests/test_artifact_proxy.py -v` → no regression (GET paths untouched)
Run: `uv run pytest -q` → full suite green

- [ ] **Step 7: Commit**

```bash
git add src/twicc/views.py src/twicc/settings.py tests/test_artifact_data_writes.py
git commit  # feat(artifacts): accept data/ writes on the artifact-serving routes
```

---

### Task 3: share routes stay read-only (regression tests)

**Files:**
- Modify: `src/twicc/share/artifact_views.py` (only if a guard is missing)
- Test: extend `tests/test_share_public_routes.py` (or the closest existing share-route test file — check where `share_artifact_asset` is already exercised and add there)

**Interfaces:** none new — this task pins design §8.

- [ ] **Step 1: Write the tests**

**No artifact-share fixture exists anywhere in `tests/`** (`test_share_public_routes.py` only builds *session* shares, and `share_artifact_asset` is exercised nowhere) — build it from scratch. The snapshot lives at `get_share_snapshot_dir(share_id)` = `<data_dir>/shares/<share_id>/` (`src/twicc/paths.py:171`, resolved by `confined_snapshot_path`, `src/twicc/core/services/share_mutation.py:110`), so hand-build it under a monkeypatched data dir — no need to drive the snapshotting service:

```python
def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def artifact_share(db, tmp_path, monkeypatch, settings):
    """An artifact-kind share with a hand-built on-disk snapshot:
    index.html + data/seed.json. Field names: align Share/ArtifactBookmark
    creation with core/models.py and the fixtures of test_share_model.py."""
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr("twicc.paths.get_data_dir", lambda: tmp_path)
    from twicc.core.models import ArtifactBookmark, Session, Share
    session = Session.objects.create(id="sess-share")  # + required FKs per model
    bookmark = ArtifactBookmark.objects.create(
        session=session, project=session.project,
        relative_path="demo/index.html", name="Demo", scope="all",
    )
    share = Share.objects.create(
        kind="artifact", artifact_bookmark=bookmark, token="t" * 64, options={},
    )
    snap = tmp_path / "shares" / str(share.id)
    (snap / "data").mkdir(parents=True)
    (snap / "index.html").write_bytes(b"<html><head></head></html>")
    (snap / "data" / "seed.json").write_bytes(b'{"seed":1}')
    return share


def test_share_artifact_asset_refuses_put(artifact_share):
    client = AsyncClient()
    resp = _run(client.put(f"/share/{artifact_share.token}/data/x.json", b"{}",
                           headers={"x-twicc-artifact-doc": f"/share/{artifact_share.token}/__twicc_doc__"}))
    assert resp.status_code == 405  # even WITH the doc header: shares are read-only (design §8)


def test_share_artifact_doc_refuses_put(artifact_share):
    client = AsyncClient()
    resp = _run(client.put(f"/share/{artifact_share.token}/__twicc_doc__", b"x"))
    assert resp.status_code == 405


def test_share_artifact_data_read_still_serves(artifact_share):
    # Snapshot data/ files remain readable — publishing them is design §8's
    # documented consequence, not a bug.
    client = AsyncClient()
    resp = _run(client.get(f"/share/{artifact_share.token}/data/seed.json"))
    assert resp.status_code == 200


def test_share_artifact_data_listing_is_404(artifact_share):
    # Documented limitation (design §8): the share routes serve files only —
    # no dir-GET listing on a share; the shim's list() maps this to [].
    client = AsyncClient()
    resp = _run(client.get(f"/share/{artifact_share.token}/data/",
                           headers={"x-twicc-artifact-doc": f"/share/{artifact_share.token}/__twicc_doc__"}))
    assert resp.status_code == 404
```

If Share/Session creation needs more required fields than sketched, mirror `tests/test_share_model.py`'s fixtures — the assertions above are the contract, the fixture plumbing is not.

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_share_public_routes.py -v`
`share_artifact_asset` already guards GET/HEAD (`src/twicc/share/artifact_views.py:118`) so the asset test should pass; `share_artifact_doc` (~line 99) has **no** method guard — if its test fails, add at its top:

```python
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
```

(import `HttpResponseNotAllowed` from `django.http` alongside the module's existing imports). Same check for `share_artifact_page`/router entry points if the PUT reaches them.

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q` → green

- [ ] **Step 4: Commit**

```bash
git add src/twicc/share/artifact_views.py tests/test_share_public_routes.py
git commit  # test(share): pin artifact share routes read-only against data/ writes
```

---

### Task 4: broker host write routing + consent, shim `window.twicc.data`

**Files:**
- Modify: `frontend/src/artifact-broker/host.js`
- Modify: `frontend/src/artifact-broker/shim.js`
- Modify: `frontend/src/composables/useArtifactBroker.js` (pass-through of one new option)
- Modify: `frontend/src/components/artifacts/ArtifactBrokerPrompt.vue` (second prompt type)
- Modify: `frontend/src/components/files/FilePane.vue` (~354, config: `inArtifactsRoot`)
- Modify: `frontend/src/artifact-shell/ArtifactShellApp.vue` (~60, config: `inArtifactsRoot: true`)

**Interfaces:**
- Consumes: Task 2's header protocol (`X-Twicc-Artifact-Doc` = document URL pathname; dir-GET listing).
- Produces:
  - host config option `inArtifactsRoot: boolean` (silent writes when `true`; tab-lifetime prompt when `false` — design §6).
  - prompt objects gain `type`: `'network'` (existing shape) or `'data-write'` (`{ type, path, settle }`).
  - `window.twicc.data` = `{ get, set, list, remove }` inside every artifact iframe.

No JS test infra exists in this repo (pytest only) — verification is by build + manual snippet (Step 5).

- [ ] **Step 1: host.js — write routing**

In `createBrokerHost`, accept `inArtifactsRoot = false` and `getDataDirLabel` in the destructured options. Add next to `sessionGrants` (module scope, same rationale — survives host re-mounts, unreachable from the artifact):

```js
// Tab-lifetime "this page may write its data/" grants for documents OUTSIDE an
// artifacts root (design 2026-08-05 §6). Module scope like sessionGrants: a
// preview re-mount must not re-prompt; a page reload starts fresh.
const writeGrants = new Set() // artifactKey
```

Inside `createBrokerHost`, after the `gate()` function (so `gateChain` is in scope), add the write-consent gate. **It MUST run through the same `gateChain` serialization as the network prompts**: `showBrokerPrompt` (the composable) holds exactly ONE pending prompt — a second concurrent `showPrompt` call would overwrite `brokerPrompt.value` and orphan the first prompt's `settle`, hanging its fetch forever. Two concurrent `twicc.data.set()` calls (e.g. `Promise.all` on page init) or a data-write prompt racing a network prompt would deadlock without this:

```js
    // Consent for data/ writes outside an artifacts root (design 2026-08-05 §6).
    // Serialized on the SAME gateChain as network prompts (one dialog at a time)
    // and coalesced per artifact: concurrent writes share one prompt/decision.
    // Tab-lifetime only — no "Forever" (nothing to persist onto; design §6).
    let pendingWriteGate = null
    function writeGate() {
        if (writeGrants.has(artifactKey)) return Promise.resolve()
        if (pendingWriteGate) return pendingWriteGate
        const run = gateChain.then(async () => {
            if (writeGrants.has(artifactKey)) return // granted while we waited
            const label = (typeof getDataDirLabel === 'function' && getDataDirLabel())
                || new URL(ownDir).pathname + 'data/'
            const decision = await showPrompt({ type: 'data-write', path: label })
            if (decision === 'deny') throw new Error('denied by user')
            writeGrants.add(artifactKey)
        })
        gateChain = run.then(() => {}, () => {}) // keep the chain alive on either outcome
        const settled = run.finally(() => {
            if (pendingWriteGate === settled) pendingWriteGate = null
        })
        pendingWriteGate = settled
        return settled
    }
```

Replace the own-asset branch of `proxyFetch` (currently `if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)`, line ~212) with:

```js
        // The artifact's own files → served directly, no prompt (§6.6). Writes
        // and data/ requests additionally carry the data-store protocol
        // (design 2026-08-05): confined to <ownDir>data/, doc header set by US
        // (never trusted from the artifact), consent outside an artifacts root.
        // Behaviour change, deliberate: an own-dir write outside data/ (or on a
        // share) now REJECTS the artifact's fetch instead of surfacing the
        // server's 405 response.
        if (sameOrigin && url.href.startsWith(ownDir)) {
            const isWrite = req.method !== 'GET' && req.method !== 'HEAD'
            const dataDir = ownDir + 'data/'
            const inData = url.href === dataDir || url.href.startsWith(dataDir)
            if (isWrite && mode === 'share') throw new Error('broker: shared artifact is read-only')
            if (isWrite && !inData) throw new Error('broker: writes allowed only under data/')
            if (isWrite || inData) {
                if (isWrite && !inArtifactsRoot) await writeGate() // throws on deny
                // Overwrite any artifact-supplied homonym — this header is the
                // server's proof the write comes from a served document.
                req.headers = {
                    ...req.headers,
                    'x-twicc-artifact-doc': new URL(documentUrl).pathname,
                }
            }
            return await hostDirectFetch(req)
        }
```

Keep the network prompt payloads as they are but tag them `type: 'network'` where `showPrompt` is called in `gate()` (one added property).

- [ ] **Step 2: shim.js — `window.twicc.data`**

Append before `main()` and call inside it (after `interceptor.apply()`):

```js
// Persistence sugar over the data/ store (design 2026-08-05 §5). Pure wrapper
// over the same fetch() the interceptor already routes through the host — no
// extra penpal method. Its real point is detectability: an artifact can test
// `window.twicc?.data`, while nothing advertises that a PUT would be accepted.
function installDataApi() {
    const data = {
        async get(name) {
            const res = await fetch('data/' + name)
            if (res.status === 404) return null
            if (!res.ok) throw new Error(`twicc.data.get: ${res.status}`)
            return await res.json()
        },
        async set(name, value) {
            const res = await fetch('data/' + name, {
                method: 'PUT',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify(value),
            })
            if (!res.ok) {
                let detail = ''
                try { detail = (await res.json()).error || '' } catch { /* not JSON */ }
                throw new Error(`twicc.data.set: ${res.status}${detail ? ` (${detail})` : ''}`)
            }
        },
        async list() {
            const res = await fetch('data/')
            if (res.status === 404) return []
            if (!res.ok) throw new Error(`twicc.data.list: ${res.status}`)
            return (await res.json()).files
        },
        async remove(name) {
            const res = await fetch('data/' + name, { method: 'DELETE' })
            if (res.status === 404) return false
            if (!res.ok) throw new Error(`twicc.data.remove: ${res.status}`)
            return true
        },
    }
    window.twicc = Object.assign(window.twicc || {}, { data })
}
```

Relative `'data/' + name` resolves against the document URL — correct in every serving context, and CSP `base-uri 'none'` means an artifact cannot skew it with a `<base>` tag.

- [ ] **Step 3: prompt component + composable + mount sites**

`ArtifactBrokerPrompt.vue`: branch on `prompt.type === 'data-write'` — reuse the dialog shell, copy per design §6, and make the dialog label match (`label="Network request"` is currently hardcoded):

```html
    <wa-dialog
        :open="!!prompt"
        :label="prompt && prompt.type === 'data-write' ? 'Store data' : 'Network request'"
        @wa-hide.self="emit('decision', 'deny')"
    >
        <div v-if="prompt && prompt.type === 'data-write'" class="broker-prompt">
            <p>This page wants to store data in:</p>
            <p class="broker-prompt-host"><strong>{{ prompt.path }}</strong></p>
            <p class="broker-prompt-target">Files are written on the machine TwiCC runs on, next to the page.</p>
        </div>
```

with footer buttons emitting `decision('session')` ("Allow for this tab") and `decision('deny')` ("Deny") — no "Forever" (design §6: tab-lifetime only). Guard the existing network markup with `prompt.type !== 'data-write'`. Keep the `@wa-hide.self` dismiss-is-deny wiring untouched.

`useArtifactBroker.js`: forward two new config keys in `setupBroker()`'s `mountBrokerHost` call: `inArtifactsRoot: config.inArtifactsRoot ?? false` and `getDataDirLabel: config.getDataDirLabel`.

`FilePane.vue` (~line 358, the broker config object): add

```js
                  // Silent data/ writes only when the previewed doc lives under
                  // a session's artifacts root (design 2026-08-05 §6); the
                  // project Files tab prompts.
                  inArtifactsRoot: !!props.artifactBookmarkSessionId,
                  // Human-readable location for the data-write prompt: the
                  // page's real directory on disk, not the /api/file-raw URL.
                  getDataDirLabel: () => {
                      const fp = props.filePath || ''
                      return fp.slice(0, fp.lastIndexOf('/') + 1) + 'data/'
                  },
```

`ArtifactShellApp.vue` (~line 60 config): add `inArtifactsRoot: true,` — the dedicated page serves bookmarks, which live under an artifacts root by construction. (Share mode never reaches the write path — the host throws first.)

- [ ] **Step 4: Build the non-HMR bundles**

Run: `cd frontend && npm run build`
Expected: builds succeed; `src/twicc/static/artifact-broker/shim.js` and `static/artifact-shell/` regenerate.

- [ ] **Step 5: Manual verification**

Ask the user (or use an existing session's Artifacts tab) with a scratch artifact `demo/index.html`:

```html
<script>
(async () => {
  await window.twicc.data.set('config.json', { radius: 12 })
  const back = await window.twicc.data.get('config.json')
  document.body.textContent = 'roundtrip: ' + JSON.stringify(back) + ' — files: ' + JSON.stringify(await window.twicc.data.list())
})()
</script>
```

Expected: the page shows the round-tripped object; `data/config.json` appears in the Artifacts tree; **no prompt** (artifacts root). Then preview the same file from a project's Files tab → the data-write prompt appears once; Deny → `set` rejects; re-preview + Allow → write lands.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/artifact-broker/ frontend/src/composables/useArtifactBroker.js \
        frontend/src/components/artifacts/ArtifactBrokerPrompt.vue \
        frontend/src/components/files/FilePane.vue frontend/src/artifact-shell/ArtifactShellApp.vue \
        src/twicc/static/artifact-broker/ src/twicc/static/artifact-shell/
git commit  # feat(artifacts): route data/ writes through the broker host + window.twicc.data
```

(If `src/twicc/static/` is gitignored, drop those two paths from the add — check `.gitignore` first.)

---

### Task 5: reload-heuristic fix (design §7)

**Files:**
- Modify: `frontend/src/components/files/FilesPanel.vue` (`changeAffectsHtmlPage`, ~507)

**Interfaces:** none — behavior-only. Without this, an artifact's own save reloads its page and wipes its state.

- [ ] **Step 1: Implement**

Replace the function body:

```js
function changeAffectsHtmlPage(renderedRel, paths) {
    const renderedSegments = renderedRel.split('/')
    // A page's own data/ store (design 2026-08-05 §7): the artifact writing its
    // state must NOT reload itself — the save would wipe the very state it
    // persists. data/ changes still refresh the tree, never the preview.
    const pageDir = renderedSegments.slice(0, -1).join('/')
    const dataPrefix = (pageDir ? pageDir + '/' : '') + 'data/'
    const relevant = paths.filter(p => !p.startsWith(dataPrefix))
    if (renderedSegments.length <= 1) return relevant.length > 0  // page at the root → any non-data change
    const topFolder = renderedSegments[0]
    return relevant.some(p => {
        const segments = p.split('/')
        return segments.length > 1 && segments[0] === topFolder
    })
}
```

Update the function's JSDoc comment (lines ~496-506) to mention the `data/` exclusion.

- [ ] **Step 2: Manual verification**

With the Task 4 scratch artifact open in the Artifacts tab: click a button that calls `twicc.data.set(...)` → the tree shows the new file, the page does **not** reload. Then edit `index.html` itself (agent-side or via the editor) → the preview reloads as before.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/files/FilesPanel.vue
git commit  # fix(artifacts): don't reload an HTML preview on its own data/ writes
```

---

### Task 6: documentation

**Files:**
- Modify: `src/twicc/agent/system_prompt.py` (Artifacts section, ~103-146)
- Modify: `frontend/src/components/share/ShareDialog.vue` (artifact-share copy — design §8)
- Modify: `CLAUDE.md` (Artifact Network Broker section)
- Modify: `AGENTS.md` (condensed mirror — required by project rule)

**Interfaces:** none. Without the addendum change the whole feature stays dormant — no agent will know it exists (design §10).

- [ ] **Step 1: system-prompt addendum**

In the Artifacts section of `src/twicc/agent/system_prompt.py`, inside the HTML-artifact bullet list, add one concise bullet (match the surrounding tone; no over-explanation):

```
- **Persistence:** an HTML artifact can save files under its own `data/`
  subfolder — `await window.twicc.data.set('config.json', obj)` / `.get` /
  `.list()` / `.remove` (or plain `fetch('data/x.json', {method:'PUT', body})`).
  Use it to let the user make choices you read back later: seed
  `<artifact dir>/data/config.json` yourself, have the page load it, edit it,
  save — then Read the file. Writes are silent, confined to `data/`, capped
  (10 MB/file, 100 MB total). The user sees the files in the Artifacts tab.
```

- [ ] **Step 2: CLAUDE.md + AGENTS.md**

In `CLAUDE.md`, at the end of the **Artifact Network Broker** section, add a short paragraph:

```
**Artifact data persistence.** An HTML artifact may write under its own `data/` subfolder — plain `fetch` PUT/DELETE (+ dir-GET listing) through the broker's own-asset path, gated server-side by the host-set `X-Twicc-Artifact-Doc` header; `window.twicc.data` sugar in the shim. Silent under an artifacts root, tab-lifetime prompt elsewhere; shares stay read-only; a page's own `data/` writes never reload its preview. Design: `docs/plans/2026-08-05-artifact-data-persistence-design.md`.
```

Mirror a condensed version into `AGENTS.md` at the equivalent spot (project rule: AGENTS.md follows CLAUDE.md).

- [ ] **Step 3: share-dialog copy (design §8)**

Sharing an artifact publishes whatever its `data/` held at snapshot time — including values the owner entered through the artifact's own UI. Surface it instead of leaving it implicit: in `frontend/src/components/share/ShareDialog.vue`, next to the artifact snapshot option (`<wa-option value="snapshot">`, ~line 249), add a one-line hint in the surrounding help/description text:

```
The snapshot includes any files the artifact saved under its data/ folder.
```

Match the neighbouring copy's tone and placement (a `hint`/help slot or the existing description paragraph — whichever the snapshot select already uses).

- [ ] **Step 4: Commit**

```bash
git add src/twicc/agent/system_prompt.py frontend/src/components/share/ShareDialog.vue CLAUDE.md AGENTS.md
git commit  # docs(artifacts): document the data/ persistence capability
```

---

## Self-review + agent-review notes (already applied)

- **Spec coverage:** §3 double lock → Tasks 2+4; §4 surface+caps → Tasks 1+2; §5 sugar → Task 4; §6 consent → Task 4; §7 reload fix → Task 5; §8 share read-only → Task 3 (+ host-side early throw in Task 4) and share-dialog copy → Task 6 Step 3; §10 docs/addendum → Task 6. `DATA_UPLOAD_MAX_MEMORY_SIZE` (unstated in spec, discovered in planning, confirmed against Django 6.0.4) → Task 2 Step 3.
- **Agent review (2026-08-05) applied:** tests rewritten to the repo's sync `_run` convention (no async plugin installed); data-write consent serialized through `gateChain` (concurrent prompts would deadlock `showBrokerPrompt`); foreign-prefix header test asserts 405 and a same-prefix/outside-root test asserts 403; project-route tests added; artifact-share fixture specified from scratch (none exists); empty listing reachable pre-first-write; prompt shows the on-disk path via `getDataDirLabel` + its own dialog label; share `list()` limitation documented (design §8) and pinned by test.
- **Deliberately absent:** no migration, no CHANGELOG entry (user rule: never without explicit ask), no plugin version bump (no skill touched), no JS unit tests (no JS test infra in this repo — manual verification steps instead).
- **Open implementation freedom:** exact model field names in test fixtures (align with `core/models.py` / `test_share_model.py`), whether `static/` bundles are committed (mirror whatever `.gitignore` says).
