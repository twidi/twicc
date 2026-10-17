# Data Dir File Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep dated copies of the user-data files at the root of the data dir, taken each time their content really changes, with a bounded retention.

**Architecture:** One pure module (`twicc/data_backups.py`) owns the covered-file list, the comparison, the version store and the retention; it is synchronous, Django-free, and never raises. The backend calls its `scan_all()` once at startup (in `cli/run.py:main()`, before any backend write) and runs a non-recursive `watchfiles` watcher (`twicc/data_backups_watcher.py`) on the data dir root for the rest of its life, with a catch-up scan when the watcher is armed and a final scan at shutdown.

**Tech Stack:** Python ≥ 3.13, `watchfiles` 1.3 (already a dependency), `orjson`, pytest + pytest-django (`asyncio.run` in tests, no pytest-asyncio).

**Spec:** `docs/plans/2026-10-17-data-dir-file-backups-design.md`

## Global Constraints

- Covered files (data dir root only): `settings.json`, `workspaces.json`, `layouts.json`, `message-snippets.json`, `terminal-config.json`, every `*-settings-presets.json`, `seen-tips.json`, `seen-help.json`, `api-tokens.json`, `.env`, `secret-key`. Nothing else (not `providers-status.json`, not `telemetry.json`, no runtime file, no subdirectory).
- A version is stored only when the normalized content differs from the newest stored version of that file. The stored copy is always the raw bytes on disk.
- Normalization ignores `_version` (top-level key of `settings.json`) and `last_used_at` (each entry of `tokens` in `api-tokens.json`). A file that is not valid JSON, or a non-`.json` file, is compared as raw bytes.
- A deleted file stores nothing and removes nothing.
- Storage: `<data_dir>/backups/<file name>/`, leading dot dropped from the folder name (`.env` → `env`). One file per version, named by its UTC timestamp, original extension kept. Two versions never share a name.
- Folders `0o700`, version files `0o600`, whatever the mode of the original.
- Retention, after each new version of a file: keep the 30 newest versions ∪ the newest version of each of the 30 most recent local calendar days that have a version. Delete the rest. Nothing else deletes versions.
- Any backup failure is logged and swallowed: it never fails or delays a write, the startup or the shutdown.
- Only the backend process takes versions; tests use the isolated data dir of `twicc.settings_test`.
- Commit steps run only if the user authorized commits for this execution (project rule: commit on explicit request). Commit format: Conventional Commit + body + `Co-Authored-By: Claude <current model name> <noreply@anthropic.com>`. Stage files by name, never a directory.

## Review Focus

1. A covered file that is empty or invalid JSON (half-written by an editor, damaged by a bug) → stored as a version, compared as raw bytes, never a crash. Test in Task 1.
2. Atomic-write temp files (`settings.json.<rand>.tmp`, `tmp<rand>.tmp`, `secret-key.tmp`), heartbeats and `*.lock` at the root → never versioned, no folder created. Tests in Task 1 (`is_covered`) and Task 4 (watcher).
3. Two versions of the same file taken within the same microsecond (or a clock that returns the same instant) → two distinct names, nothing overwritten. Test in Task 1.
4. `backups/` unusable (a regular file in its place, no permission) → `take_version` / `scan_all` return without raising; the covered file is untouched. Test in Task 3.
5. A burst of writes (UI typing, a loop) → one version per quiet period, and a burst on one day never removes the newest version of earlier days. Tests in Task 2 (retention) and Task 4 (watcher).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/twicc/paths.py` (modify) | `get_backups_dir()` |
| `src/twicc/data_backups.py` (create) | covered list, normalization, version store, retention, `scan_all()` |
| `src/twicc/data_backups_watcher.py` (create) | async watcher: catch-up scan, per-change versions, final scan |
| `src/twicc/cli/run.py` (modify) | startup scan in `main()`, watcher task in `run_server()` |
| `.gitignore` (modify) | `/backups/` at a worktree root |
| `CLAUDE.md`, `AGENTS.md`, `README.md` (modify) | mention `backups/` in the data dir contents |
| `tests/conftest.py` (modify) | `fresh_data_dir` fixture (empty data dir + `TZ=UTC` per test) |
| `tests/test_data_backups.py` (create) | Tasks 1–3 |
| `tests/test_data_backups_watcher.py` (create) | Task 4 |

---

### Task 1: Version store and comparison

**Files:**
- Modify: `src/twicc/paths.py` (next to the other `get_*_dir()` getters, e.g. after `get_project_icons_dir()`)
- Create: `src/twicc/data_backups.py`
- Modify: `tests/conftest.py` (the `fresh_data_dir` fixture)
- Test: `tests/test_data_backups.py`

**Interfaces:**
- Produces:
  - `tests/conftest.py::fresh_data_dir` fixture (used by Tasks 1–4).
  - `paths.get_backups_dir() -> Path` → `get_data_dir() / "backups"`.
  - `data_backups.COVERED_FILENAMES: frozenset[str]`, `data_backups.COVERED_PATTERN = "*-settings-presets.json"`.
  - `data_backups.is_covered(name: str) -> bool` — exact name in `COVERED_FILENAMES`, or `fnmatch(name, COVERED_PATTERN)`.
  - `data_backups.version_folder(name: str) -> Path` → `get_backups_dir() / name.lstrip(".")`.
  - `data_backups.normalize(name: str, raw: bytes) -> bytes` — comparison key (see Global Constraints); JSON → `orjson.dumps(obj, option=orjson.OPT_SORT_KEYS)` after dropping the ignored values. The ignored values are dropped only where the shape matches (`settings.json` a dict; `api-tokens.json` a dict whose `tokens` is a list, dict entries only); any other valid JSON is compared as its sorted dump, so a damaged-but-valid file still becomes a version.
  - `data_backups.list_versions(name: str) -> list[Path]` — oldest first; only files whose name parses as a version timestamp (temp files ignored).
  - `data_backups.version_time(path: Path) -> datetime` — aware UTC datetime parsed from the version name.
  - `data_backups.take_version(name: str, *, now: datetime | None = None) -> bool` — reads `get_data_dir() / name`; stores a version when the file is a regular file and `normalize` differs from the newest version's; returns `True` when a version was stored. Calls `prune(name)` (Task 2) after storing. Never raises. Its whole body runs under a module-level `threading.Lock` (`_lock`): the startup scan, the watcher's worker threads and the final scan never interleave, so the free-name check and the write cannot race.

- [ ] **Step 1: Add the shared fixture, then write the failing tests.**

In `tests/conftest.py`, add a non-autouse fixture `fresh_data_dir(tmp_path, monkeypatch) -> Path`: creates `tmp_path / "data"`, points `TWICC_DATA_DIR` at it with `monkeypatch.setenv`, and sets the process time zone to `UTC` for the test, with the repo's pattern (`tests/test_peer_messages.py:53-61`): `monkeypatch.setenv("TZ", "UTC"); time.tzset(); yield path; monkeypatch.undo(); time.tzset()`. A test that needs another zone uses `monkeypatch.setenv("TZ", ...)` + `time.tzset()`; the fixture's `undo()` restores both. Both backup test files start with `pytestmark = pytest.mark.usefixtures("fresh_data_dir")`.

In `tests/test_data_backups.py`: `_root()` returns `paths.get_data_dir()`; `T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)`.

```python
def test_covered_names():
    for name in ("settings.json", "workspaces.json", "layouts.json", "message-snippets.json",
                 "terminal-config.json", "codex-settings-presets.json", "claude_code-settings-presets.json",
                 "seen-tips.json", "seen-help.json", "api-tokens.json", ".env", "secret-key"):
        assert data_backups.is_covered(name)
    for name in ("providers-status.json", "telemetry.json", "twicc.info.json", "twicc.heartbeat",
                 "settings.json.lock", "settings.json.ab12.tmp", "tmpab12.tmp", "secret-key.tmp", "backups"):
        assert not data_backups.is_covered(name)

def test_first_change_stores_the_raw_bytes():
    raw = b'{"publicBaseUrl": "https://x", "_version": 3}'
    (_root() / "settings.json").write_bytes(raw)
    assert data_backups.take_version("settings.json", now=T0) is True
    [version] = data_backups.list_versions("settings.json")
    assert version.read_bytes() == raw
    assert version.parent == _root() / "backups" / "settings.json"
    assert version.name == "2026-10-17T12-00-00-000000Z.json"
    assert data_backups.version_time(version) == T0

def test_same_content_stores_nothing():  # also with only `_version` changed
    (_root() / "settings.json").write_bytes(b'{"a": 1, "_version": 1}')
    data_backups.take_version("settings.json", now=T0)
    (_root() / "settings.json").write_bytes(b'{"_version": 9, "a": 1}')
    assert data_backups.take_version("settings.json", now=T0 + timedelta(seconds=1)) is False
    assert len(data_backups.list_versions("settings.json")) == 1

def test_token_last_used_at_is_ignored():
    ...  # two stores differing only in tokens[0]["last_used_at"] → one version; a changed "name" → two

def test_invalid_json_and_raw_files_compare_as_bytes():
    ...  # settings.json b"{not json" stored; same bytes again → no new version
    ...  # an empty settings.json (b"") after it → a new version holding b""
    ...  # settings.json b"[1]" (valid JSON, wrong shape) → a new version; same bytes again → none
    ...  # ".env" b"A=1\n" stored as backups/env/2026-10-17T12-00-00-000000Z (no extension); b"A=2\n" → second version

def test_missing_file_stores_nothing_and_keeps_versions():
    ...  # store one version, delete the file → take_version is False, list_versions still has 1

def test_same_instant_never_overwrites():
    ...  # two different contents with now=T0 both times → 2 versions, distinct names, both readable

def test_permissions_are_owner_only():
    ...  # original chmod 0o664; folder mode 0o700, version mode 0o600 (stat.S_IMODE)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.data_backups`).

- [ ] **Step 3: Implement `get_backups_dir()` and `data_backups.py` (Task 1 interfaces)**

- Version names: `now.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%S-%fZ") + suffix`, `suffix` = `".json"` for `.json` files, `""` otherwise (no dot in the timestamp, so a name without extension has no `Path.suffix`). `version_time` strips a literal trailing `.json` before parsing; a name that does not parse is not a version. When the name exists, add one microsecond until it is free (keeps lexical order = chronological order).
- Write through `tempfile.mkstemp(dir=folder, prefix=".", suffix=".tmp")` (mode 0o600) then `os.replace`; create folders with `mkdir(mode=0o700, parents=True, exist_ok=True)` and `os.chmod(folder, 0o700)` (umask-proof) for `backups/` and the per-file folder.
- Newest version = last of `list_versions(name)`; compare `normalize(name, file_bytes)` with `normalize(name, newest.read_bytes())`.
- Wrap the whole body of `take_version` in `try/except Exception: logger.exception(...); return False`. Log each stored version at DEBUG (`Backed up %s (%s)`: file name, version name); the INFO lines belong to the callers (Tasks 3 and 4), so the first startup does not print a dozen lines on the console.
- Call `prune(name)` after storing; until Task 2 lands, define `prune(name: str) -> None` as a no-op stub with its final docstring.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

Run: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/data_backups.py src/twicc/paths.py tests/conftest.py tests/test_data_backups.py`
Expected: `All checks passed!`

```bash
git add src/twicc/paths.py src/twicc/data_backups.py tests/conftest.py tests/test_data_backups.py
git commit  # feat(backups): store versions of the data dir root files on change
```

---

### Task 2: Retention

**Files:**
- Modify: `src/twicc/data_backups.py` (replace the `prune` stub)
- Test: `tests/test_data_backups.py`

**Interfaces:**
- Consumes: `list_versions`, `version_time`, `take_version` (Task 1).
- Produces: `data_backups.KEEP_NEWEST = 30`, `data_backups.KEEP_DAYS = 30`, `data_backups.prune(name: str) -> None` (never raises).

- [ ] **Step 1: Write the failing tests.** Build versions by calling `take_version` with distinct content and explicit `now` values. The `fresh_data_dir` fixture pins `TZ=UTC`, so local day = UTC day except in the time-zone test.

```python
def test_keeps_the_30_newest():
    # 40 versions, one per minute on the same day → exactly the 30 newest remain
    assert [data_backups.version_time(p) for p in data_backups.list_versions("layouts.json")] == expected_last_30

def test_keeps_the_newest_of_each_of_the_30_most_recent_days_with_a_version():
    # one version on each of 45 distinct days (some days skipped between them), then a burst
    # of 40 versions on a new day → remaining = 30 newest of the burst day
    #   + newest of each of the 29 most recent earlier days with a version (30 days counted with the burst day)
    ...

def test_a_burst_never_removes_an_old_day():
    # one version on 2026-08-15, then 200 versions on 2026-10-17 → the 2026-08-15 version is still there
    ...

def test_days_without_versions_do_not_count():
    # 5 versions on 5 days spread over a year → all 5 kept
    ...

def test_days_are_local_calendar_days(monkeypatch):
    # monkeypatch.setenv("TZ", "Europe/Paris") + time.tzset() (restored by the fixture), KEEP_NEWEST monkeypatched to 1.
    # Versions at 2026-10-17T21:00Z and 21:30Z (local 17 Oct) and 22:30Z (local 18 Oct, same UTC date)
    # → kept: 21:30Z and 22:30Z; 21:00Z removed. Grouping by UTC date would keep only 22:30Z.
    ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q -k "test_keeps_the_30_newest or test_keeps_the_newest_of_each or test_days_are_local"`
Expected: these three FAIL (the stub keeps everything). `test_a_burst_never_removes_an_old_day` and `test_days_without_versions_do_not_count` already pass against the stub: they guard against over-pruning.

- [ ] **Step 3: Implement `prune(name)`.** Read `KEEP_NEWEST` / `KEEP_DAYS` at call time (module globals, so a test can monkeypatch them). Keep set = the `KEEP_NEWEST` newest versions ∪, grouping versions by `version_time(p).astimezone().date()` (local day), the newest version of each of the `KEEP_DAYS` most recent days. Unlink every other version; a failed unlink is logged and skipped.

- [ ] **Step 4: Run the whole file**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/data_backups.py tests/test_data_backups.py
git commit  # feat(backups): keep the 30 newest versions and the newest of the 30 last days
```

---

### Task 3: Startup scan

**Files:**
- Modify: `src/twicc/data_backups.py`
- Modify: `src/twicc/cli/run.py` (`main()`, right after the `if log_trim_enabled(): ...` block and before `logger.info("TWICC starting...")`)
- Test: `tests/test_data_backups.py`

**Interfaces:**
- Consumes: `is_covered`, `take_version` (Task 1).
- Produces: `data_backups.scan_all(*, now: datetime | None = None, reason: str = "startup scan") -> int` — calls `take_version` for every regular file of the data dir root whose name `is_covered`; returns the number of versions stored; never raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_scan_takes_the_first_version_of_each_covered_file_then_nothing():
    for name in ("settings.json", "workspaces.json", ".env", "codex-settings-presets.json"):
        (_root() / name).write_bytes(b"{}" if name.endswith(".json") else b"X=1\n")
    (_root() / "telemetry.json").write_bytes(b"{}")
    (_root() / "db").mkdir()
    assert data_backups.scan_all(now=T0) == 4
    assert data_backups.scan_all(now=T0 + timedelta(seconds=1)) == 0
    assert not (_root() / "backups" / "telemetry.json").exists()

def test_scan_catches_a_change_made_while_stopped():
    ...  # scan, rewrite workspaces.json with new content, scan again → 1

def test_unusable_backups_dir_never_raises():
    (_root() / "settings.json").write_bytes(b"{}")
    (_root() / "backups").write_bytes(b"not a dir")
    assert data_backups.scan_all(now=T0) == 0
    assert (_root() / "settings.json").read_bytes() == b"{}"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q -k "scan or unusable"`
Expected: FAIL (`AttributeError: scan_all`).

- [ ] **Step 3: Implement `scan_all()`** (logs one INFO line `Data backups: %d new version(s) at %s` with the count and `reason`, only when the count is not zero), then call it in `main()`: `from twicc.data_backups import scan_all` and `scan_all()` at the place given above, with a one-line comment: the scan must precede every backend write to a covered file (the first ones are in `run_server()`: MCP password enforcement, provider auto-enable, telemetry bootstrap, then the legacy-key rewrite on the first settings read and the changelog bookkeeping), and must follow the log trim (it logs, which opens the delayed file handler).

- [ ] **Step 4: Run the tests**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/data_backups.py src/twicc/cli/run.py tests/test_data_backups.py
git commit  # feat(backups): back up the data dir root files at startup
```

---

### Task 4: Watcher

**Files:**
- Create: `src/twicc/data_backups_watcher.py`
- Modify: `src/twicc/cli/run.py` (`run_server()`: start the task next to `title_auto_task`, just before the outer `try:`; cancel it in the outer `finally`)
- Test: `tests/test_data_backups_watcher.py`

**Interfaces:**
- Consumes: `is_covered`, `take_version`, `scan_all` (Tasks 1, 3).
- Produces: `async def run_data_backups_watcher(*, step_ms: int = 1000, debounce_ms: int = 5000, rust_timeout_ms: int = 1000) -> None` — runs until cancelled.

Behaviour:
- `awatch(data_dir, recursive=False, watch_filter=<Path(path).parent == data_dir and is_covered(basename)>, step=step_ms, debounce=debounce_ms, rust_timeout=rust_timeout_ms, yield_on_timeout=True, stop_event=<a private asyncio.Event>)`, `data_dir = get_data_dir()` resolved once. `step` is the quiet delay for writes that create a new path, which every backend writer does (atomic temp file + `os.replace`): such a burst yields once. An in-place rewrite of the same path (an editor) yields about every two steps, which only gives a few more versions. `debounce` caps a never-ending burst. The event is private on purpose: `awatch` sets the event it is given when it is cancelled, and the backend's `shutdown_event` must not be set as a side effect. The watcher stops only by cancellation.
- On the **first** yield (the watcher is armed: a change or the timeout), run `scan_all(reason="watch start")` once in a thread: this covers every write between the startup scan of Task 3 and the moment the watch started.
- For each batch: for each `(change, path)` with `change` in `{Change.added, Change.modified}` and a covered basename, `await asyncio.to_thread(take_version, basename)` (each name once per batch). `Change.deleted` does nothing.
- In a `finally`: call `scan_all(reason="shutdown")` synchronously (a change still pending in the quiet delay at shutdown; the files are a few KB), then let a `CancelledError` propagate. Any other exception from the loop is logged and ends the watcher without raising. Log one INFO line per version the watcher stores (`Backed up %s`).
- In `run_server()`: `data_backups_task = asyncio.create_task(run_data_backups_watcher())` next to `title_auto_task`, before the outer `try:` (the startup writes made before that point are covered by the catch-up scan). In the outer `finally`, wrap the existing `try:/finally:` chain: `try: if not data_backups_task.done(): await _cancel_task(data_backups_task, "Data backups watcher")` / `finally: <the existing chain, unchanged>`, so a second cancellation during that await still cancels the title task and stops the inline exports. The outer `finally` runs on every exit (normal shutdown and every early failure or cancellation before `server.serve()`), so the task never outlives `run_server()`.

- [ ] **Step 1: Write the failing tests** (file-level `pytestmark = pytest.mark.usefixtures("fresh_data_dir")`). Each test runs one `asyncio.run(scenario())` where `scenario` starts the watcher as a task (`step_ms=50, debounce_ms=500, rust_timeout_ms=100` unless stated), polls `list_versions` with a 5 s deadline instead of fixed sleeps, and ends by cancelling the task and awaiting it (`CancelledError` expected). **Sync point:** before acting, every test except the catch-up one writes a sentinel `seen-help.json` before starting the task and waits for its version: the catch-up scan is then done and cannot overlap the test's writes.

```python
def test_a_change_takes_one_version():
    ...  # after the sync point, write settings.json → exactly one version appears

def test_a_burst_takes_one_version():
    ...  # step_ms=200, debounce_ms=500 (a 200 ms quiet delay: a loaded run cannot split the burst);
    ...  # after the sync point, atomic writes like the backend's (mkstemp(dir=root,
    ...  # prefix="settings.json.", suffix=".tmp") + os.replace), distinct content, one every 20 ms over ~250 ms
    ...  # → exactly 1 new version, holding the last content

def test_uncovered_files_and_temp_files_are_ignored():
    ...  # after the sync point, write telemetry.json, twicc.heartbeat, settings.json.ab12.tmp and sub/settings.json,
    ...  # then write workspaces.json and wait for its version → backups/ holds only seen-help.json/ and workspaces.json/

def test_catch_up_scan_covers_writes_before_the_watch_started():
    ...  # write workspaces.json, then start the watcher → a version appears without further writes

def test_cancel_compares_a_pending_change():
    ...  # after the sync point, write layouts.json and call task.cancel() with no await in between
    ...  # (the loop cannot process the change; only the final scan_all can store it)
    ...  # → awaiting the task raises CancelledError in < 2 s and the layouts.json version exists
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups_watcher.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.data_backups_watcher`).

- [ ] **Step 3: Implement the watcher and its wiring in `run_server()`.**

- [ ] **Step 4: Run both test files**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_data_backups.py tests/test_data_backups_watcher.py tests/test_title_auto_task.py tests/test_mcp_startup.py -q`
Expected: PASS (the last two prove `run_server()` leaves no task behind on its early-exit paths).

- [ ] **Step 5: Commit**

```bash
git add src/twicc/data_backups_watcher.py src/twicc/cli/run.py tests/test_data_backups_watcher.py
git commit  # feat(backups): watch the data dir root and back up each change
```

---

### Task 5: Ignore rule, docs, full suite

**Files:**
- Modify: `.gitignore` (in the root-anchored data-dir block, after `/project-icons/`)
- Modify: `CLAUDE.md` and `AGENTS.md` (section "Data Directory", the "Contents:" sentence — same wording in both)
- Modify: `README.md` (FAQ "Where is my data stored?")

- [ ] **Step 1: Add the ignore rule** — a short comment (data-dir subdirectory at the worktree root, holds copies of `.env` and `secret-key`) and `/backups/`.

Run: `cd /home/twidi/dev/twicc-poc && mkdir -p backups/settings.json && touch backups/settings.json/x && git check-ignore -v backups/settings.json/x; rm -r backups`
Expected: the matching rule is `.gitignore:<line>:/backups/`.

- [ ] **Step 2: Docs.** `CLAUDE.md` / `AGENTS.md`: add to the data dir contents `backups/` (dated versions of the root user-data files, taken on change by `twicc/data_backups.py`; 30 newest + newest of the 30 last days with a change). `README.md`, after "This includes the SQLite database, logs, and user settings.": "Previous versions of the settings files (settings, workspaces, layouts, presets…) are kept in the `backups/` folder of the data directory, one folder per file."

- [ ] **Step 3: Full suite and lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest -q 2>&1 | tail -3 && uvx ruff check src/twicc/data_backups.py src/twicc/data_backups_watcher.py src/twicc/cli/run.py src/twicc/paths.py tests/conftest.py tests/test_data_backups.py tests/test_data_backups_watcher.py`
Expected: no failure; `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore CLAUDE.md AGENTS.md README.md
git commit  # docs(backups): document and git-ignore the data dir backups
```

- [ ] **Step 5: Hand over.** Tell the user to restart TwiCC (backend change; reserved operation), then check `backend.log` for `Data backups: N new version(s) at startup scan` and `ls ~/.twicc/backups/`.
