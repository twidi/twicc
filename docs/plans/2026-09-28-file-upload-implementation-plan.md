# File upload — implementation plan

Date: 2026-09-28
Spec: `docs/plans/2026-09-28-file-upload-design.md` (the source of truth).

This plan only splits the work. Every behaviour, code, state and rule is in
the spec; a task names the spec sections it implements and never restates
them. When the plan and the spec disagree, the spec wins.

## How to run the tasks

- One task = one fresh implementer sub-agent, then one focused review of that
  task's diff against the spec sections it names, then one commit.
- The implementer receives: this plan, the spec, the task's section list, and
  the rules below. It reads the whole spec once, then implements only its
  task.
- A review finding is fixed and re-checked by the **same** reviewer, scoped to
  the fix. A finding that would need a mechanism the spec does not define is
  not implemented: the orchestrator records it as a limit (spec §8) or
  rejects it.
- Order: tasks run **one at a time**, 1 → 9 (one checkout, one git index:
  two agents committing in parallel could take each other's staged files).
  Task 6 does not need the backend code (only the record shape, the codes and
  the headers of the spec), so its position is free, but it never runs in
  parallel with another task.
- Work on the current branch, unless Twidi asks for a branch or a worktree.

## Rules for every implementer

- Read `CLAUDE.md` first. Key points for this feature:
  - backend JSON with `orjson`; `NamedTuple` for simple immutable data;
  - lint: `uvx ruff check <files>` on the files you touch (no project-wide
    lint pass);
  - backend tests: `uv run pytest <test files>` (in a worktree:
    `cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest <test files>`);
    frontend tests: `cd frontend && npm test` (node:test,
    `src/**/*.test.js`);
  - never `uv pip`, never `--active`;
  - frontend: no static import cycle (lazy `import()` where the spec says
    so); every new `wa-*` component imported in `frontend/src/main.js`;
    public assets through `resolvePublicAssetUrl()`;
  - never run `migrate`, `npm install`, or restart servers (no migration is
    needed; `tus-js-client` is already installed).
- Commit per task, Conventional Commit subject, descriptive body, the
  `Co-Authored-By` trailer of the running model. Stage the task's files
  explicitly (never a directory). No CHANGELOG entry (task 9 proposes it).
- Do not modify behaviour outside the task's sections. If the task needs a
  decision the spec does not make, stop and report it instead of inventing
  one.

## Tasks

### Task 1 — Backend foundations: staging area and metadata

- **Spec:** §5.1 (module layout, error-number vocabulary, staging-dir rules),
  §5.2 (metadata fields, metadata-write rules, file lifecycle), §5.8 (record
  shape).
- **Files:** `src/twicc/paths.py` (`get_uploads_dir()`), `.gitignore`
  (`/uploads`), new `src/twicc/uploads/__init__.py`, `store.py` (staging
  access, metadata read/write with the version rule, record builder, name
  filter), `broadcast.py` (`upload_state` broadcast of a persisted record).
- **Tests:** new `tests/test_uploads_store.py` — metadata write rules
  (version from disk, failed write changes nothing and broadcasts nothing,
  offset re-sync), one `upload_state` broadcast per persisted write with an
  increasing `version`, record shape, name filter and unparsable files,
  staging dir mode `0o700`.
- **Done:** tests pass; no route exists yet.
- **Commit:** `feat(uploads): add the upload staging area and metadata store`.

### Task 2 — Backend: creation, list and the view decorator

- **Spec:** §5.3 (tus conformance, `X-Twicc-Upload` and the decorator,
  `POST` checks 1–5 with the idempotency lookup, creation order, zero-byte
  case, `GET`), §5.4 (the guarded-operation runner and the creation lock
  only), §5.5 (codes used here).
- **Files:** `src/twicc/uploads/locks.py` (runner: fresh-context shielded
  task, strong references, creation lock, per-upload lock getter),
  `src/twicc/uploads/views.py` (decorator, creation views, list view),
  `src/twicc/urls.py`: **one** `api/uploads/` route whose view dispatches
  `GET` (list) and `POST` (standalone creation), plus the project and session
  creation routes.
- **Zero-byte uploads:** the finalization step does not exist yet; the view
  calls a finalization hook that task 4 fills. Until then, a zero-byte
  creation answers `500` and a test marks it `xfail` with a reference to
  task 4.
- **Tests:** new `tests/test_uploads_create.py` — every check of §5.3 `POST`
  (codes, types, bounds, filename rules, `PC_NAME_MAX`, staging-dir
  exclusion via `realpath`, absolute check before scope), each prefix,
  idempotency (retry before checks, concurrent `POST`s with one
  `client_id`), `.part` before `.json`, disk full → `507`, `GET` (24 h
  tombstones, `now`), `X-Twicc-Upload` on every response, the decorator
  (exception → JSON `500`, `CancelledError` propagates), the runner starts
  its task in a fresh `contextvars.Context`.
- **Done:** tests pass (zero-byte `xfail` only).
- **Commit:** `feat(uploads): add upload creation and listing endpoints`.

### Task 3 — Backend: HEAD, PATCH, DELETE and locking

- **Spec:** §5.3 (general rules of the `<id>` routes: id format, unknown or
  unparsable `.json` → `404` before any lock or path work, re-read under the
  lock → `404`; `HEAD` lock-free table and the recovery path entry; `PATCH`
  rules and append outcome, incl. `PATCH` on an `active` upload with a
  complete `.part` → `409`; `DELETE` incl. disk-full ordering), §5.4 (whole
  section), §5.5.
- **Files:** `src/twicc/uploads/views.py`, `store.py`, `locks.py` (the
  "finalizing now" set), `src/twicc/urls.py` (`api/uploads/<id>/`).
- **Finalization and recovery:** not implemented yet. `PATCH` reaching
  `size` and `HEAD` needing recovery call hooks that task 4 fills; their
  tests are `xfail` with a reference to task 4.
- **Tests:** new `tests/test_uploads_transfer.py` — `<id>` routes: invalid
  format, unknown id and unparsable `.json` → `404`; tus flow up to a
  complete `.part`, every `PATCH` error code (incl. complete `.part` →
  `409`), excess bytes with and without
  `Content-Length`, disk full on append (`507`, still `active`, answer `507`
  even when the metadata sync fails), `412` + `Tus-Version` on the three
  methods, `HEAD` lock-free rows, `DELETE` (metadata first, idempotent,
  `409` while finalizing, disk-full ordering), the disconnect test through
  Django's real `ASGIHandler` (spec §7).
- **Done:** tests pass (finalization/recovery ones `xfail`).
- **Commit:** `feat(uploads): add the tus transfer endpoints`.

### Task 4 — Backend: finalization and recovery

- **Spec:** §5.6 (whole section, incl. the `ArtifactsWatcher` filter note),
  §5.7 (whole section), §5.3 (`HEAD` answers after recovery: the `507` gate,
  the `500` throttle, decisions from the re-read under the lock; `PATCH`
  answers after finalization).
- **Files:** `src/twicc/uploads/store.py` (finalization and recovery thread
  work, verdicts), `views.py` / `locks.py` (coroutine side: re-validation,
  broadcasts, "finalizing now" `finally`), `src/twicc/artifacts_watcher.py`
  (drop `.twicc-upload-*.tmp` before grouping).
- **Tests:** new `tests/test_uploads_finalize.py` — every finalization and
  recovery test of spec §7 (free names, link / copy / `EXDEV` / reserve +
  replace, error mapping, pre-commit / commit-write / post-commit failures,
  every crash window, `replace` over foreign content, `HEAD` gates, last
  bytes appended with a failed metadata sync → finalization still runs, temp
  file and reservation created with mode argument `0o666`, a cancellation of
  the awaiting coroutine during finalization runs no cleanup in parallel with
  the thread); remove the `xfail` marks of tasks 2–3. Watcher test in the
  same file: temp file ignored, no "artifacts available" on a temp file
  alone.
- **Done:** all upload tests pass, no `xfail` left.
- **Commit:** `feat(uploads): finalize uploads into the target directory with crash recovery`.

### Task 5 — Backend: janitor

- **Spec:** §5.9 (whole section), §5.7 ("Recovery runs" list), §5.1
  (unparsable `.json` removal after 24 h).
- **Files:** new `src/twicc/upload_cleanup_task.py`, `src/twicc/cli/run.py`
  (start and cancel next to the other periodic tasks).
- **Tests:** new `tests/test_upload_cleanup_task.py` — pass order (recovery
  before expiry), re-check under the lock, expiry by `last_transfer_at`
  (`failed`, `"expired"`), first-pass exclusion of `active` with `error`,
  later-pass selection, tombstone and lock-entry removal, terminal leftovers,
  orphans, unparsable `.json`.
- **Done:** tests pass; `uvx ruff check` clean on the task's files.
- **Commit:** `feat(uploads): add the upload cleanup janitor`.

### Task 6 — Frontend core: pure modules and the lifecycle controller

- **Spec:** §6.1, §6.2 (testable core, entries, `clientId`, sets, stalled
  rule), §6.3 (whole section), §6.4 (whole section), §6.5 (whole section),
  §6.6 (path helpers incl. the `//` collapse, `takePickedFiles`), §6.7
  (`entryActions` texts and buttons, the controller method
  `retryFinalization`), §6.8 (fingerprint, resume rules), §6.9 (toast
  rules), §6.10 (the `onCompleted` event, the tree-node merge,
  `createDirRefresher` with the "contains" rule and the coalescing), §6.11
  (the `statusByOrigin` aggregate), §6.12 and §6.13 (the controller logic).
  Tasks 7 and 8 only **use** these parts for the same sections; they do not
  change them.
- **Files:** new `frontend/src/utils/uploads/` (pure modules:
  `applyServerRecord` rules, stalled rule, `statusByOrigin`, pump selection,
  `clientId` / path helpers, fingerprint, tree-node merge,
  `createDirRefresher`, `entryActions`, `takePickedFiles`; the controller
  `createUploadsController(deps)`), `frontend/src/utils/hash.js`
  (`hashBytes`), new `frontend/src/utils/appNavigation.js`.
- **Tests:** `*.test.js` next to each module — every frontend item of spec
  §7 that concerns these modules, the controller with fake dependencies
  (tus factory, `apiFetch`, toast, timers, events).
- **Done:** `cd frontend && npm test` passes; nothing is wired into the UI
  yet.
- **Commit:** `feat(uploads): add the frontend upload lifecycle core`. This
  commit also stages `frontend/package.json` and `frontend/package-lock.json`
  (`tus-js-client`, already installed): task 6 is the first task that uses
  it.

### Task 7 — Frontend: store, WebSocket, entry point, strip and resume

- **Spec:** §6.2 (Pinia wrapper: it builds the controller with **every** real
  dependency — the `navigator.wakeLock` adapter, the window event source for
  `online` / `visibilitychange` / `beforeunload`, `isVisible`,
  `isAppNavigation`, the `tabId` from `sessionStorage`, the shared 401
  helper — and exposes the controller state, `onCompleted` and
  `statusByOrigin`), §6.3 (`useWebSocket` wiring:
  `upload_state`, `reconcile()` in the `OPEN` watcher, lazy imports), §6.6
  (menu item, hidden input, synchronous `click()`, `//` collapse), §6.7
  (origins, the strip, its size cap, the mobile overlay CSS change, the
  resume input), §6.8 (resume flow in the UI), `utils/api.js` (the shared
  401 helper used by `apiFetch` and the controller).
- **Files:** new `frontend/src/stores/uploads.js`,
  `frontend/src/composables/useWebSocket.js`, `frontend/src/utils/api.js`,
  `FileTreeContextMenu.vue`, `FileTreePanel.vue`, `FilesPanel.vue` (props
  `uploadOrigin`, strip, CSS), `SessionView.vue` and `ProjectDetailPanel.vue`
  (pass `uploadOrigin`), a new strip component under
  `frontend/src/components/files/`.
- **Tests:** unit tests for any new pure helper; the rest is covered by task
  6 and the manual test.
- **Done:** `npm test` passes; `npm run build` passes (no missing `wa-*`
  import, no cycle warning).
- **Commit:** `feat(uploads): upload files from the file tree context menu`.

### Task 8 — Frontend: tree refresh, tab labels, app-navigation marks

- **Spec:** §6.10 (`FilesPanel` subscribes to `onCompleted` and wires
  `createDirRefresher` to its tree), §6.11 (`UploadTabStatus`, every render
  site incl. the `DockGutter` mirrors and `ProjectDetailPanel`, reading the
  store's `statusByOrigin`), §6.13 (the `markAppNavigation()` calls in
  `LoginView.vue`, `App.vue`, `utils/resync.js`). The wake lock and the
  `beforeunload` handler are already wired by task 7.
- **Files:** `FilesPanel.vue`, new
  `frontend/src/components/files/UploadTabStatus.vue`, `SessionView.vue`,
  `SessionLayout.vue`, `DockRegion.vue`, `DockGutter.vue`,
  `LayoutOverlay.vue`, `ProjectDetailPanel.vue`, `LoginView.vue`, `App.vue`,
  `frontend/src/utils/resync.js`.
- **Done:** `npm test` and `npm run build` pass.
- **Commit:** `feat(uploads): show upload status in the Files and Artifacts tabs`.

### Task 9 — Wrap-up

- Run the whole backend and frontend test suites once.
- Check `SKILLS-AND-CLI.md` and the plugin skills: no CLI or skill changes
  are expected; confirm and say so.
- Propose (do not write) a CHANGELOG `[Unreleased]` entry to Twidi.
- Propose (do not write) the `CLAUDE.md` / `AGENTS.md` lines: `uploads/` in
  "Data Directory → Contents", the upload janitor in the "Periodic"
  architecture line.
- Remind Twidi: backend restart via `devctl.py`; the manual end-to-end tests
  of spec §7 (phone, tunnel) are his.
