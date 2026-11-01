# CLI `--remote` forwarder — implementation plan

**Spec:** `docs/superpowers/specs/2026-06-04-cli-remote-forwarder-design.md`
**Branch:** `feature/rpc-api-from-cli` (phase 2, on top of the server-side API)
**Process:** one implementer subagent per task + a 2-stage review (general
review, then targeted) before moving on — same as phase 1. Tasks are sequential
(R2–R5 all live in `_remote.py`); R1 is independent and goes first.
**No new dependency:** `httpx` is already used across the codebase.

---

## Anchors (verified)

- Entry point: `run.py` → `twicc.cli.main()` (`src/twicc/cli/__init__.py:1013`),
  which calls `app()` at line 1015. Interception goes *before* `app()`.
- Registry: `rpc/generator.py` → `build_registry() -> dict[path, CommandSpec]`.
  `CommandSpec` carries `chain` (levels w/ tokens + group arguments), `options`,
  `params` (all `ParamSpec`), `json_schema`, `summary`. `get_command()`
  (`rpc/invoker.py`) returns the root Click command.
- The denylist lives today at `rpc/generator.py:18` (`DENYLIST`), used at line 73.
- MIME sniffing: `_sniff_mime` in `src/twicc/cli/_drop_request/attachments.py`.
- **Path resolution caveat:** group ARGUMENTS are interleaved between command
  tokens (e.g. `session <id> content` → path `session/content`). A naive join of
  leading tokens is wrong — navigation must consume group args (use Click).

---

## R1 — Single source of truth for local-only commands

**Goal:** one canonical set shared by the API generator and the `--remote` forwarder.

- New `src/twicc/cli/_local_only.py`:
  `LOCAL_ONLY_COMMANDS = frozenset({"password", "claude", "codex", "run", "token", "whoami"})`
  with a docstring explaining each exclusion (interactive secret mgmt: password,
  token; provider passthroughs: claude, codex; local exec: run; host-bound
  identity: whoami).
- `rpc/generator.py`: import `LOCAL_ONLY_COMMANDS`, delete the in-file `DENYLIST`,
  update the use at line 73 (`sub_name in LOCAL_ONLY_COMMANDS`).

**Acceptance:** `build_registry()` still yields the same routes (40), none of the
6 roots present; no remaining `DENYLIST` reference.

---

## R2 — Forwarder skeleton: path resolution + rejections

**Goal:** `src/twicc/cli/_remote.py`, the pre-flight half of the forwarder.

- **Resolve argv → (command_path, CommandSpec, bound params)** by navigating the
  Click tree from `get_command()` with the argv (Click group resolution /
  `make_context`), so interleaved group arguments are consumed correctly. The
  resulting path must equal a `build_registry()` key.
- **Reject local-only roots:** if `argv[0] ∈ LOCAL_ONLY_COMMANDS` →
  `"<cmd> is a local-only command; not available over --remote"`, exit. (They are
  absent from the registry; this gives a helpful message instead of "unknown".)
- **Reject unknown command** (not in registry, not local-only) → clear message, exit.
- **Spec-aware `self`/`parent` rejection:** define a centralized
  `HOST_BOUND_PARAMS` set — the param names that accept `self`/`parent` (audit:
  the session-id positionals — `session_id` / `session` / `items` /
  `session_ids` … — plus the filiation options `spawned_by` / `spawn_tree` /
  `descendants`). Check the *bound* values for those params; if any is
  `self`/`parent` → `"self/parent has no meaning over --remote"`, exit. A
  free-text value that merely equals `self`/`parent` is untouched.

**Acceptance (sample argvs):** `["sessions","--limit","5"]` → `sessions`;
`["session","abc","content"]` → `session/content` (interleaved arg);
`["password","set"]` → local-only rejection; `["send-message","parent","hi"]` →
self/parent rejection; `["send-message","x","talk about self"]` → NOT rejected.

---

## R3 — Attachment inlining (`--attach` local path → data: URI)

**Goal:** make local attachments work over HTTP.

- In `_remote.py`: if the resolved command has an attach-style param, rewrite each
  `--attach <value>` / `--attach=<value>` token in the argv:
  - value already `data:…` → leave as-is;
  - otherwise treat as a **local** file path (relative is fine — read client-side),
    read bytes, sniff MIME (reuse `_sniff_mime`), emit
    `data:<mime>;base64,<payload>`, and substitute it back into the argv.
- **No size handling** — oversized payloads surface as remote errors (R4).

**Acceptance:** `--attach ./img.png` → forwarded argv carries
`--attach=data:image/png;base64,…`; relative paths work; an existing `data:` value
passes through unchanged.

---

## R4 — HTTP call + envelope mapping + timeouts + error model

**Goal:** the network half of `_remote.py`.

- Build `<base>/rpc/<command_path>` (normalize the base URL); `POST` body
  `{"argv": <attachment-rewritten argv>}` with `httpx.Client`,
  `Authorization: Bearer <token>` when a token is set, `Content-Type: application/json`.
- **Timeout:** for `process/wait` & `processes/wait`, parse `--timeout` from the
  argv and set the httpx read timeout ≥ that + a margin; otherwise a sane default.
- **Command ran (HTTP 200 envelope):** print `result` to stdout (orjson indent-2,
  same shape as the CLI), `error` to stderr if present, exit with `exit_code`.
- **Transport / remote-layer failure** (connect/DNS error, read timeout, non-200,
  401/404/5xx, non-JSON body): print `twicc: remote error: <detail>` to stderr
  (detail names the cause), exit **`7`**.

**Acceptance (vs worktree backend):** a read command → result + exit 0; bad port
→ exit 7 + message; 401 when a token is required → exit 7 (auth detail); 404 for
a bogus route → handled.

---

## R5 — Entry-point interception

**Goal:** wire `--remote` into `cli.main()`.

- In `main()` (`cli/__init__.py:1013`), *before* `app()`: scan `sys.argv[1:]` for
  `--remote` / `--remote=` and `--remote-token` / `--remote-token=`.
  - Resolve **URL**: inline value → else `TWICC_REMOTE_URL` → else error. Space
    form `--remote <url>` is taken as the URL only when the next token contains
    `://` (commands never do).
  - Resolve **token**: inline value → else `TWICC_REMOTE_TOKEN` → else none.
  - `--remote` absent → `app()` unchanged (env vars alone don't trigger remote).
  - `--remote` present → strip those tokens; `sys.exit(forward(url, token, rest))`.
- Missing/empty URL → clear stderr message + non-zero exit.

**Acceptance:** `--remote=URL sessions`, `--remote URL sessions`, and bare
`--remote sessions` (with `TWICC_REMOTE_URL`) all route to the forwarder; no
`--remote` → normal local; token precedence (flag > env) holds.

---

## R6 — End-to-end validation + docs

**Goal:** confirm the whole path live, and document the client side.

- **E2E** against the worktree backend (the local `twicc` forwarding to its own
  `/rpc/`): a read command; auth open vs token-required (`--remote-token` and
  `TWICC_REMOTE_TOKEN`); attachment inlining (`create-session`/`send-message`
  `--attach <local file>` → verify it reaches the session); a `wait`; self/parent
  rejection; local-only rejection; a transport error (bad port).
- **Docs:** a short "Driving a remote (`--remote`)" note — minimal, in `RPC-API.md`
  (client counterpart to the server doc). Keep it to flags + precedence + the
  pointers already documented (paths/attachments/wait). No new bump unless a skill
  changes (none planned).

**Acceptance:** every behavior above confirmed live; docs reference present.

---

## Out of scope (per spec)
Named remote registry; retries/backoff/streaming; `TWICC_REMOTE_URL` as a
*trigger* (it only fills a bare `--remote`); any server change.
