# TwiCC skills & CLI

> Drive TwiCC from a terminal or from inside an agent — the `twicc` command-line interface, the matching agent-skill plugin, and the built-in MCP server are three front doors to the same surface.

TwiCC ships a command-line interface (every `twicc <command>`) **and** a Claude Code / Codex plugin (auto-installed) whose skills wrap those same commands. Anything a skill lets an agent do, you can do from a shell with the exact same `twicc` command — the skills are guided wrappers around the CLI, not a separate capability set. That is why they are documented together here: each entry lists both its command and its **Skill**.

Two audiences, one surface:

- **You, in a terminal** — compose `twicc` into scripts; every structured command prints JSON.
- **An agent, mid-session** — the plugin skills teach the agent the same commands, plus the `self` / `parent` keywords so it can act on its own session and its parent without knowing any id up front.

## Conventions

- **JSON output.** Every structured command prints JSON on stdout — listings, inspections, and write commands (create / update / send / stop / …) alike. There is no text mode and no flag to pass: the CLI speaks JSON by default. The only exceptions are the interactive `password` commands, `token create` (which prints its one-time secret as plain text), and the `claude` / `codex` passthroughs, which stay text.
- **Read vs write.** Read commands query TwiCC's database directly and work whether or not a backend is running (a few, like live process state, need the backend). Write commands drop a request file that the **running** TwiCC server picks up, then poll for the server's final status — they need a live backend and accept `--timeout` (default 30 s). If the deadline passes the request stays on disk and may still apply server-side.
- **Exit codes.** `0` success; non-zero on failure (typically `1` not-found / validation, `2` backend down, `5` timeout). Run `twicc <command> --help` for a command's exact codes.
- **Paging (`--paginated`).** Every listing takes `--limit` / `--offset`, but a bare page never says whether another one follows. Pass `--paginated` and the result becomes `{"items": [...], "pagination": {"limit", "offset", "total", "has_more"}}` — same four keys on every command, so `has_more` answers the question without a probe request. Without an explicit `--limit` the page size is **50**, whatever the command's own default: the flag promises a page, so it supplies one (`--tail` keeps its own window). **Before 2026-09-15 the flag is opt-in; from that date the envelope is the only shape and `--paginated` is an accepted no-op** — the switch happens at local midnight on the machine running TwiCC. While the flag is opt-in, a call without it returns exactly what it always did, plus a one-line notice on stderr (and in the RPC envelope's `warnings` key) saying what is coming; agents on MCP are never notified, since they read the flag off the tool schema and adapt on their own. Either way a script that passes `--paginated` keeps working untouched. Accepted by `projects`, `workspaces`, `sessions`, `session content` / `messages` / `agents` / `workflows`, `artifacts`, `share`, `processes` and `search`. Two specifics: with `session messages --tail N` the window is reported as the range it covers and `has_more` means "messages remain *before* it"; on that same command, when nothing filters after extraction (no `--contains`, and no `--is-final` or one listing all three values), `total` counts raw items, a handful of which extract to nothing, so `has_more` can be a rare false positive — never a false negative.
- **Slim listings (`--slim`).** `--slim` returns a reduced projection of each session — identity, state, cost, and the `has_*` flags saying what else is there — dropping the payloads you can fetch per session (`tasks`, `plan_paths`, `goals`, `layout`), the timestamps `last_new_content_at` already answers for, the paths `project_id` already encodes, and the agent-settings bundle. About 80% lighter on a default page; use it whenever you are scanning rather than inspecting. Accepted by `sessions`, `sessions get` and `session agents` — the three that take it; `session <ID>` returns one row and always in full.
- **Catalogues drift.** The model / effort / permission / preset lists shown below are the current built-ins; the live source of truth is always `twicc info` (see below).

## The MCP server (`/mcp`)

Inside a TwiCC-driven agent session, every command below (minus `settings`, plus `whoami`) is also an MCP tool (`mcp__twicc__<command>` on Claude Code; names use `_` for `/` and `-`: `create_session`, `session_content`, `update_session_settings`, …). Same arguments (the JSON schema mirrors the CLI options), same JSON output wrapped in `{"exit_code", "result", "error"}`, same exit codes. Available in every permission mode and auto-approved (no prompt) — TwiCC's control plane, not the project's code. Prefer the tools: no shell, no drop-file latency, and your session identity travels with the call (`self` / `parent` / `whoami` work without PID tricks). **Their schemas are deferred on purpose** — every tool on Codex (`tool_search_always_defer_mcp_tools`), all but the five hot ones on Claude Code (`ALWAYS_LOAD_PATHS`) — so a tool missing from an agent's visible tool list is not a missing tool; it is one tool search away (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex). The CLI remains the way to script TwiCC from outside a session, and the only surface for `settings`, `password`, `token`, and the `claude` / `codex` passthroughs.

## Resolving the executable (`$TWICC`)

The `twicc` executable's path depends on how TwiCC was launched (`uvx`, `uv tool install`, a dev `uv run`, an absolute path). From inside an agent's Bash tool, resolve it once at the start of each invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (it may expand to multiple words). `twicc whoami` also returns the canonical invocation under `twicc_executable`.

## Acting from inside a session: `self` and `parent`

Commands that target a session accept two keywords resolved via PID ancestry, so an agent never needs to know its own id:

- **`self`** — the current session. Accepted by `update-session`, `update-sessions` / `send-messages` (as an explicit id), `topology`, `artifacts bookmark` / `unbookmark`, `share create session`, the share list `--session`, and the `--spawned-by` / `--spawn-tree` / `--descendants` / `--siblings` filters.
- **`parent`** — the session that spawned the current one. Accepted by `send-message`, `artifacts bookmark` / `unbookmark`, `share create session`, the share list `--session`, and the filiation filters (except `--spawn-tree` and `--siblings`, which reject `parent`).

`twicc whoami` is the explicit way for an agent to discover its own `session_id`, working directories, settings, and live process row.

## Driving a remote TwiCC (`--remote`)

Every command can run against a **remote** TwiCC instead of the local one: the CLI forwards it to the remote's `/rpc/` HTTP API and behaves as if it had run there. The full transport contract — endpoints, the `{exit_code, result, error}` envelope, the OpenAPI schema — lives in [`RPC-API.md`](RPC-API.md). The client-side essentials:

```bash
twicc --remote <url> [--remote-token <token>] <command> [args…]
```

- **Target.** `--remote <url>` (or `--remote=<url>`) points at the remote's base URL — a scheme is required (e.g. `http://box:3501`). A **bare** `--remote` falls back to `TWICC_REMOTE_URL`. The global `--remote` / `--remote-token` flags must come **before** the command.
- **Auth.** `--remote-token <token>` (or `TWICC_REMOTE_TOKEN`) is sent as a Bearer token; an explicit value wins over the environment. `/rpc/` is open only when neither a password nor any token is configured — otherwise a valid token is mandatory (mint one with `twicc token`, below).
- **Outcome.** The forwarder prints the remote command's `result` to stdout and `error` to stderr, and **exits with the remote command's exit code** — a script behaves the same locally or remote. A client-side misuse (a local-only command, `self` / `parent`, a malformed `remote:` path) exits `2`; a transport / remote-layer failure (unreachable host, rejected auth, HTTP error, timeout, malformed response) prints `twicc: remote error…` to stderr and exits `7`.
- **Local-only over remote.** `password`, `token`, `claude`, `codex`, `run`, `whoami` are host-bound and rejected client-side. The `self` / `parent` keywords are rejected too — they only mean something on the local host; pass an explicit session id.
- **Files.** `--attach <local file>` is read on the client and inlined as a base64 `data:` URI, so a local (even relative) path works without a shared filesystem. Path arguments (`--project`, `--directory`) are resolved on the server and must be absolute (or, for `--project`, an id).
- **`remote:` scheme.** To point at a file that already lives on the **server** instead of inlining the client's copy, prefix an **absolute** path with `remote:` (e.g. `remote:/srv/data/audit.md`) — supported on the prompt (`create-session` / `send-message`), `--message` (`send-messages`), and `--attach` (all three). Only valid with `--remote`; a relative path, or `remote:` without `--remote`, is an error. The same split applies inside `@@` include markers (see `create-session`): a plain marker resolves on the client, `@@remote:/abs/path` on the server.

### Authentication tokens: `twicc token <SUBCOMMAND>`

Host-only commands that manage the Bearer tokens gating `/rpc/` — never exposed over the API (and rejected over `--remote`).

- `token create --name <LABEL>` — mint a token; prints the secret **once** as plain text (only its digest is stored).
- `token list` — token metadata as JSON (id / name / timestamps), never the secret.
- `token revoke <TOKEN_ID>` — revoke a token by its id.

---

# Reference

## Discovery & self-knowledge

### `twicc info [SECTION...]`
Inspect TwiCC's per-provider catalogues. Sections: `presets`, `commands`, `models`, `agent-settings`, `settings`, `all`. Output always carries `twicc_version` and `providers` (with enabled/disabled + default + orchestration flags — `orchestration` is the user's soft preference for which providers agents should pick on their own when orchestrating; an explicit user request for another enabled provider still wins); each named section adds its key.
- `--provider TEXT` — filter every provider-keyed section by provider key (naming one bypasses the disabled-provider filter); `settings` is instance-wide and ignores it.
- `--project TEXT` — only for the `commands` section: also list commands scoped to that project.
- `--filter TEXT` — case-insensitive substring filter for `commands` (whitespace-separated tokens are ANDed).
- `--include-disabled-providers` — include disabled providers in the provider-keyed section payloads (not `settings`, which is instance-wide).
- Skill: [`twicc-info`](src/twicc/agent/plugin/twicc/skills/twicc-info/SKILL.md).

### `twicc status`
Report the live backend's state as JSON (running / starting / stale / dead_pid / not_running). Pure file reads, safe to call concurrently. Exit `0` only when fully running, so it works as a shell gate.
- Skill: [`twicc-status`](src/twicc/agent/plugin/twicc/skills/twicc-status/SKILL.md).

### `twicc usage`
Show the latest usage quota snapshot (quotas, burn rate, cost estimates) for every provider as JSON.
- Skill: [`twicc-usage`](src/twicc/agent/plugin/twicc/skills/twicc-usage/SKILL.md).

### `twicc whoami`
Print details of the session that owns the calling process — `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir` (when part of an orchestration), the resolved `agent_settings`, the full `session` payload, and the live `process` row. Exits `1` from a plain terminal (only meaningful inside a session).
- Skill: [`twicc-whoami`](src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md).

## Settings

Human/program-facing commands for reading and writing the synced settings (`settings.json`). No agent skill — use the CLI directly. Write commands need a live backend (drop-request pattern: `--timeout` default 30 s); read commands work offline. These commands are remote-forwardable via `--remote`.

### `twicc settings`
Print the **generic** synced settings as JSON (`_version` stripped) — only the directly-settable backbone keys this command family owns. Provider keys (`claudeCode*`/`codex*`/`defaultProvider`/…), notification keys, and UI-only `excluded` keys are not shown here (read them via their own commands / `twicc info settings`). Offline read, no server needed.

### `twicc settings get <KEY>`
Print the value of a single **generic** synced settings key as `{key: value}`. Only generic keys are accepted; provider and notification keys are redirected to their own commands and `excluded`/`unknown` keys rejected — the same gate as `set`/`unset` (validation error, exit `1`). Offline read.

### `twicc settings set <KEY> <VALUE>` / `twicc settings unset <KEY>`
Mutate a single **generic** scalar setting. `set` type-coerces `VALUE` to match the key's default type (bool: `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`; int: integer string; string: verbatim). `unset` reverts the key to its built-in default. The `--help` of `get`/`set`/`unset` lists every settable generic key with a one-line description. Both commands validate the key first:
- `excluded` keys (`waTheme`, `waBrand`, `defaultLayoutId`, `_version`) — UI-only visual preferences, not settable via CLI.
- `provider` keys (`defaultProvider`, `disabledProviders`, `orchestrationDisabledProviders`, and any `claudeCode*` / `codex*` prefixed keys) — use `twicc settings provider …`.
- `notifications` keys (`externalNotificationTargets`) — use `twicc settings notifications …`.
- `unknown` — no such setting.
Exit codes: `0` accepted, `1` validation, `2` server down, `3` rejected, `4` failed, `5` timeout.
- `--timeout INTEGER` (default 30).

### `twicc settings provider <PROVIDER>`
Show or mutate one provider's settings slice.

**Bare form (no flags, no sub-command):** offline read — prints `enabled`, `is_default`, `orchestration_enabled`, `agent_defaults` (the `{provider}Default*` bundle), `untrusted_permission_mode_default`, `usage_read_file`, `usage_dump_file`, and `quota_wakeup_time`.

**With flags:** drops a `settings:update` patch for the provider's `{provider}Default*` synced keys. Flags accepted by both providers (Claude Code and Codex):
- `--model TEXT` — default model for NEW sessions (aliases `max`/`strongest`/`min`/`fastest` resolved per provider).
- `--effort TEXT` — default reasoning effort (`low`, `medium`, `high`, `xhigh`; `max` also on Claude Code, GPT-6 Astra, and GPT-5.6 Codex models; aliases `min`/`max`).
- `--permission-mode TEXT` — default tool-permission policy for trusted projects (aliases `min`/`safe`, `max`, …).
- `--context-max TEXT` — default max context window (`200k`, `1m`, `272k`; aliases `min`/`max`).
- `--untrusted-permission-mode TEXT` — default permission mode for NEW sessions in an **untrusted** project; restricted to the provider's untrusted-allowed set; aliases `min`/`safe`/`max` resolve within that set. This flag maps to a separate synced key (`claudeCodeDefaultUntrustedPermissionMode` / `codexDefaultUntrustedPermissionMode`), not to the closed-bundle mapping.

Claude Code-only flags (rejected with a validation error for Codex):
- `--thinking / --no-thinking` — default for extended thinking.
- `--fast / --no-fast` — default for fast mode (Opus-tier models only).
- `--chrome / --no-chrome` — default for the Claude-in-Chrome MCP integration.

Usage-file flags (both providers):
- `--usage-read-file PATH` — path to a JSON file TwiCC reads this provider's quota from; also sets enabled to true.
- `--no-usage-read-file` — disable the usage read-file.
- `--usage-dump-file PATH` — path to a JSON file TwiCC dumps this provider's quota to; also sets enabled to true.
- `--no-usage-dump-file` — disable the usage dump-file.

Quota warm-up (both providers):
- `--quota-wakeup-time TEXT` — daily quota warm-up time as `HH:MM` (24-hour, server local clock); TwiCC opens a fresh usage window at that time. Pass `''` to disable. Malformed values are rejected (validation error).

`--timeout INTEGER` (default 30). Exit codes mirror `settings set`.

**Sub-commands** (each takes `--timeout`):
- `enable` — remove the provider from `disabledProviders` (idempotent).
- `disable` — add the provider to `disabledProviders` (idempotent). The server may reject this if live agents are running under the provider; corrections are printed.
- `set-default` — set `defaultProvider` to this provider. Client-side rejection if the provider is currently disabled.
- `orchestration-enable` — remove the provider from `orchestrationDisabledProviders` (idempotent).
- `orchestration-disable` — add the provider to `orchestrationDisabledProviders` (idempotent).

### `twicc settings notifications`
Manage external **Apprise** notification targets (each target is an Apprise URL TwiCC pushes alerts to).

**Bare form:** offline read — prints `externalNotificationTargets` (the full list), `publicBaseUrl`, and `notifyOnExtraUsageStart`.

Each target in the list carries: `id` (stable handle for CLI operations), `name`, `url`, `enabled`, `tested` (`true`/`false`/`null`), `notifyUserTurn`, `notifyPendingRequest`, `notifyExtraUsageStart`, `notifyPeer`, `awayOnly`.

**Sub-commands:**

- `add <URL>` — add a new notification target. Generates a stable `id`; defaults: `enabled=true`, all notify flags on, `awayOnly=true`. Options:
  - `--name TEXT` — optional human-readable label.
  - `--enabled / --disabled` — active state (default: enabled).
  - `--user-turn / --no-user-turn` — notify when agent awaits user input (default: on).
  - `--pending / --no-pending` — notify on pending permission request (default: on).
  - `--extra-usage / --no-extra-usage` — notify when extra usage starts (default: on).
  - `--peer / --no-peer` — notify when a peer message or pairing request arrives (default: on).
  - `--away-only / --no-away-only` — hold notification while user is present (default: on).
  - `--test` — after a successful add, immediately send a test notification to the new target and emit the test result.
  - `--timeout INTEGER` (default 30).

- `update <ID>` — patch an existing target. Same flag set as `add` plus `--url TEXT` (changing the URL resets `tested` to `null`). Client-side rejection if the id is not found. Requires at least one flag. `--timeout INTEGER` (default 30).

- `remove <ID>` — remove a target by id. Client-side rejection if the id is not found. `--timeout INTEGER` (default 30).

- `test <ID>` — send a test Apprise notification to an existing target, persist the `tested` flag, and return `tested` + `test_results` in the response. Client-side rejection if the id is not found. `--timeout INTEGER` (default 30).

Exit codes for all write sub-commands: `0` accepted, `1` validation/not-found, `2` server down, `3` rejected, `4` failed, `5` timeout.

### `twicc info settings`
The `settings` section of `twicc info` emits a schema of every synced-settings key, grouped by owner (`generic`, `provider`, `notifications`, `excluded`). Each entry carries `key`, `type` (Python type name of the default), `default`, `owner`, and `hint` (the right command to use). Use this to discover the full settings surface and identify which keys are settable via `twicc settings set`.

```bash
twicc info settings
```

---

## Projects

### `twicc projects` / `twicc projects get <PROJECT...>`
List projects, or batch-look up specific ones.
- Listing: `--limit` (default 20), `--offset`, `--paginated`, `--include-archived`, `--workspace TEXT` (only projects in that workspace). Git worktrees appear as their own entries; every entry carries `worktree_of` (its main repo's id, or `null`), main repos carry `worktrees` (their worktree child ids), and every entry exposes its `trust` (`true`/`false`/`null` = inherit) + `trust_propagation` (read-only; trust is changed by a human) and its per-project agent defaults: `default_provider` (provider new sessions default to; `null` = inherit) + `default_agent_settings` (per-provider bundles seeding NEW sessions, incl. the optional `permission_mode_if_untrusted` key; a missing field inherits from the parent chain, then the global default).
- `get`: takes one or more project ids or directory paths (no filter flags); each input yields one entry in input order, archived included, with `known: false` placeholders for misses.
- Skill: [`twicc-projects`](src/twicc/agent/plugin/twicc/skills/twicc-projects/SKILL.md).

### `twicc project <PROJECT_ID>`
Show a single project as JSON. Accepts a project id (with or without leading dash) or a directory path.
- Skill: [`twicc-project`](src/twicc/agent/plugin/twicc/skills/twicc-project/SKILL.md).

### `twicc create-project <DIRECTORY>`
Register a directory as a TwiCC project (id derived from the canonical realpath; one project per directory).
- `--name TEXT` (≤ 25 chars, globally unique), `--color TEXT` (CSS hex), `--create-directory` (mkdir if missing).
- Plus the write-command flag `--timeout`.
- Skill: [`twicc-create-project`](src/twicc/agent/plugin/twicc/skills/twicc-create-project/SKILL.md).

### `twicc update-project <PROJECT>`
Update a project's name, color, archived state, default provider, worktree directory, and/or saved browser URLs — plus a `settings` sub-command for its per-provider agent-settings defaults. The directory is immutable; there is no delete (projects are archived, never removed).
- `--name TEXT` / `--unset-name`, `--color TEXT` / `--unset-color`, `--archive` / `--unarchive`, `--default-provider TEXT` / `--unset-default-provider`, `--worktree-directory PATH` / `--unset-worktree-directory` (each pair mutually exclusive). `default_provider` is the provider a NEW session in the project defaults to (inherited by sub-projects and git worktrees; unset = inherit). `worktree_directory` is the absolute base directory the worktree-create dialog proposes for this project's new git worktrees (unset = the global default, composed against the git root).
- **Saved browser URLs** (the session Browser tab's list, `{url, label?, default?}` entries, http(s) only; inherited by sub-projects and git worktrees, then a containing workspace's): `--add-browser-url URL` (idempotent; first saved URL becomes the default) with optional `--browser-url-label LABEL` and `--set-default`, `--remove-browser-url URL` (idempotent), `--set-default-browser-url URL` (must be saved), `--default-browser-url URL` (shorthand for add + set-default), `--unset-default-browser-url` (clear ALL; not combinable with the other browser-URL flags).
- **`settings` sub-command:** `twicc update-project <PROJECT> settings --provider P [flags]` patches one provider's bundle in `default_agent_settings` (the defaults seeding NEW sessions; never affects existing ones). Per-field flags mirror the session settings commands (`--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--context-max`) plus the project-only `--permission-mode-if-untrusted` (restricted to the provider's untrusted-allowed modes; aliases `min`/`safe`/`max`); `--unset <field>` removes a field (back to inherit). Aliases resolve per provider on every field; unsupported fields are silently ignored (`noop`). `--provider` is required and accepts disabled providers; `--permission-mode` keeps its full range here (it is the trusted-case default). No `--preset`. Flat flags and the sub-command don't combine (exit 64).
- **Trust (human-only):** `--trust` / `--untrust` / `--reset-trust` (mutually exclusive, and not combinable with the field flags above — set trust in its own call), plus `--propagate` / `--no-propagate` (only with `--trust`/`--untrust`; defaults to whether the project is under git). Trust is **deliberately not an agent-facing skill** — it is changed only from the web UI or this command. An untrusted project restricts the permission modes its sessions may use.
- Plus `--timeout`.
- Skill: [`twicc-update-project`](src/twicc/agent/plugin/twicc/skills/twicc-update-project/SKILL.md).

## Workspaces

### `twicc workspaces` / `twicc workspaces get <WORKSPACE_ID...>`
List workspaces, or batch-look up specific ones.
- Listing: `--limit` (default 20), `--offset`, `--paginated`, `--include-archived`.
- `get`: one or more ids (no filter flags), input order preserved, archived included, `known: false` for misses.
- Skill: [`twicc-workspaces`](src/twicc/agent/plugin/twicc/skills/twicc-workspaces/SKILL.md).

### `twicc workspace <WORKSPACE_ID>`
Show a single workspace as JSON.
- Skill: [`twicc-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-workspace/SKILL.md).

### `twicc create-workspace <NAME>`
Create a workspace (name trimmed, ≤ 20 chars, unique; id slugified from the name).
- `--color TEXT`, `--add-project TEXT` (repeatable; id or path, must already exist), `--add-pattern TEXT` (repeatable auto-add directory glob), `--browser-url TEXT` (initial saved URL for the session Browser tab of the workspace's projects; becomes the default), `--archived`.
- Plus `--timeout`.
- Skill: [`twicc-create-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-create-workspace/SKILL.md).

### `twicc update-workspace <WORKSPACE_ID>`
Update a workspace. Flags combine into a single atomic edit.
- `--name TEXT`, `--color TEXT` / `--unset-color`, `--add-project` / `--remove-project` (repeatable; id or path), `--add-pattern` / `--remove-pattern` (repeatable), `--add-browser-url URL` (+ optional `--browser-url-label LABEL`, `--set-default`) / `--remove-browser-url URL` / `--set-default-browser-url URL` / `--browser-url URL` (shorthand for add + set-default) / `--unset-browser-url` (clear ALL saved URLs; not combinable with the other browser-URL flags) — the workspace's saved Browser-tab URLs (a project's own saved URLs win), `--archive` / `--unarchive`.
- Plus `--timeout`.
- Skill: [`twicc-update-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md).

### `twicc delete-workspace <WORKSPACE_ID>`
Delete a workspace by id. Projects are **not** deleted — only the grouping disappears.
- Plus `--timeout`.
- Skill: [`twicc-delete-workspace`](src/twicc/agent/plugin/twicc/skills/twicc-delete-workspace/SKILL.md).

## Sessions — browse & read

### `twicc sessions` / `twicc sessions get <SESSION_ID...>`
List sessions, or batch-look up specific ones.
- Listing: `--project TEXT`, `--workspace TEXT`, `--limit` (default 20), `--offset`, `--paginated`, `--slim`, `--include-archived`, plus the shared filiation/visibility/annotation filters (see below). `--project` and `--workspace` (mutually exclusive) each fold in git worktrees — a worktree's sessions belong to its main repository — mirroring the UI.
- `get`: one or more ids (no filter flags), input order preserved; subagents, archived and hidden sessions all returned, `known: false` for misses.
- **Live process state:** every row carries a `process` block. `{state}` under `--slim`, plus `id`, `started_at`, `last_state_change_at` and `pid` otherwise. `state` is one of `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead` — note `awaiting_user_input`, the non-obvious stop: the agent is blocked on a human, not working. It answers **what TwiCC is running**, so `dead` means no TwiCC-managed process, which is also the answer for the many sessions TwiCC indexed but never started (a `claude` run straight from a terminal keeps generating and still reads `dead`). `dead` also covers a TwiCC that is not running at all: an agent does not outlive its backend, so "TwiCC is down" and "TwiCC runs nothing for this session" are the same fact. The block is `null` for one case only — a subagent, which runs inside its parent's process and never has one of its own; `parent_session_id` identifies those.
- **Filtering on it:** `--state <VALUE>` keeps one bucket — **repeatable and OR-combined**, since a session holds a single state, so `--state assistant_turn --state awaiting_user_input` means "busy or blocked" and naming all five is the unfiltered listing. Unlike `processes --state` (single-valued, and which silently keeps the last of two), `dead` is accepted here and means "no TwiCC-managed process", `--active` keeps every state but `dead` — `user_turn` included, the agent is loaded and idle, not gone. The two are mutually exclusive. `--provider claude_code|codex` is an ordinary column filter, unrelated to state — and unlike `processes --provider`, an unknown value is refused (exit 1) rather than silently matching nothing. The state filter runs **before** the page is cut, so `total` and `has_more` count what matches. Hidden sessions stay hidden unless you ask (`--include-hidden`, or any filiation scope, which lifts it for you).
- **`sessions wait-reply [SESSION_ID...] [--since INSTANT] [--wait-first|--wait-all] [--wait-timeout N] [--no-reply-text]`** — block until several sessions conclude, past their own cursors. The plural of `session <ID> wait-reply`, for sessions nobody just messaged (when you sent the messages, `send-messages --wait-reply` reads the cursors for you). One loop, **one wall-clock budget** for the batch. Each ends on an answer or a **pending request** only a human can clear, an answer winning a tie; `--wait-all` (default) waits for every one, `--wait-first` stops at the first to conclude and leaves the rest `outcome: pending`. **A bare call is refused** — at least one id or one filter, since the listing's empty case would mean polling every indexed session. Filters: `--project`, `--workspace`, `--provider`, `--state` (minus `dead`, refused: no process means it will never speak), `--active`, `--only-hidden`, `--spawned-by`, `--spawn-tree`, `--descendants`, `--siblings` and `--annotation`, with ids unioned on top. `--include-hidden` and `--include-archived` do not apply — hidden always on, archived always off — while `--only-hidden` still narrows. Each session starts above its own last line, or above the instant `--since` names, translated per session — there is no `--from`, line 42 being a different place in every transcript. Returns `summary` + `results`, one `reply` block per id; `summary` carries `total` (every id asked for, unknown ones included), `replied`, `awaiting_user_input`, `concluded` and `all_replied`, where `replied` counts `outcome: replied` alone while `concluded` also counts pending requests, and a named id that does not exist comes back as `outcome: unknown_session`, carrying only that and `session_id`.
- **`sessions stop [SESSION_ID...]`** — stop the agents behind selected sessions. Only sessions that currently have a process are targeted, so a bare `sessions stop` stops everything running and is bounded by what is alive, not by how many sessions exist. Takes the same selection as the listing (`--project`, `--workspace`, `--provider`, `--state`, the filiation scopes, `--annotation`) plus `--timeout` (default 30, a wall-clock budget for the whole batch) and `--force` (SIGKILL, no grace window). Naming ids bypasses the filters. **Hidden sessions are included** — they are the orchestration convention, and sparing them would leave the workers running; archived ones never are, since archiving already kills the agent. `--state dead` is refused: there is nothing to stop. Idempotent, one entry per target (`status`, `session_known`, `error`), exit 0 whatever the per-id outcome.
- Skill: [`twicc-sessions`](src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md).

### `twicc session <SESSION_ID> <SUBCOMMAND>`
Inspect a single session.
- Default (no sub-command) — the full session row, `process` block included: the same one `sessions` joins onto every row, `null` on a subagent. The same row `sessions get <SESSION_ID>` returns for one id, minus its `known` flag — reach for that one for several ids, for `--slim`, or for an id this one refuses: an id with no row at all comes back as a `known: false` placeholder, and a session with no user message — one still being computed, or one that never had a user turn at all — comes back in full with `known: true`; both exit 1 here.
- `content [RANGE] [--contains TEXT ...]` — raw item content by line number/range (e.g. `5`, `10-20`) and/or substring. `--contains` is case-insensitive and repeatable (AND-combined). Also takes `--limit`, `--offset`, `--tail N` (last N matches; mutually exclusive with `--limit`/`--offset`) and `--paginated`, applied **after** the range and `--contains`: the range is an absolute address in the JSONL, the window is a rank in the matches, and they stack. Use `--tail` to reach the end of a filtered result, which has no line address. At least one selector (`RANGE`, `--contains`, `--limit`/`--offset`, `--tail`, `--paginated`) is required — a bare call would dump the whole session, the heaviest payload the CLI can produce. No match is an empty result, not an error. Each result is `{line_num, content}`.
- `messages [--contains TEXT ...]` — all user/assistant messages, cross-provider, uniform shape (`{line_num, text, role, timestamp, is_final}`). `is_final` marks the assistant message that closes a turn, as opposed to the ones it emits between tool calls (`null` = unknown, always so on a user message). Options: `--range`, `--role user|assistant`, `--contains TEXT` (case-insensitive substring on the extracted message text, repeatable/AND-combined, applied before paging), `--is-final true|false|null` (repeatable but **OR**-combined — a message carries a single value; omitted, nothing is filtered, so `null` is never dropped silently), `--limit`, `--offset`, `--paginated`, `--tail N` (last N; mutually exclusive with `--limit`/`--offset`).
- `stop` — stop this session's live agent (`--timeout`, `--force`). Same operation as `process <ID> stop`.
- `wait-reply [--from N] [--since INSTANT] [--wait-timeout N] [--no-reply-text]` — block until the session concludes past the cursor. The other waits ride on a command that triggered the turn, so their cursor falls out of the send; here nothing was sent, so `--from` names the line to start above — the `line_num` or `since_line_num` a previous wait handed back, which is what makes a timed-out wait resumable. `--since` names that same cursor as an ISO 8601 instant instead — mutually exclusive with `--from`, the wait starting just below the first line stamped strictly after that moment. A timestamp that goes backwards can only push the cursor lower, never past a line, and a line with no timestamp is not a boundary, one below the boundary not being re-scanned; no offset means UTC, the form this CLI stores and prints, and a bare date means its midnight. Use it when you have a time and not a `line_num`, or when one moment must address several sessions — line 42 is a different place in every transcript. Omitted, the cursor is the session's current last line ("tell me the next thing it says"), and an idle session reports `ended` rather than hanging: unlike `process wait --transition`, a session that has stopped is observable — though not instantly, since the loop waits out a ~5 s flush window first, so a shorter `--wait-timeout` can only ever report `timeout`. Two endings, and no flag chooses between them: an answer, or a **pending request** — a tool approval or a question, which only a human can clear, so riding the budget out would buy nothing. An answer arriving in the same poll wins. That is exactly what `--wait-reply` does on the commands that send, which is why it carries the same name, and it returns the same `reply` block. **The exit code says how it went**, since waiting is the command rather than a follow-up: `0` answered or blocked, `5` neither came (`timeout`, `ended`, `provider_error`), `2` TwiCC stopped, `1` a local refusal (a bad `--from` or `--since`, the two cursors passed together, a non-positive `--wait-timeout`, an unknown session) or the wait itself breaking.
- `pending-request [--raw] [--timeout N]` — what this session's live agent is waiting on. Lists **every** pending request, not only the ones answerable here: a session frozen on a tool approval is waiting even though nothing in the CLI can unblock it, and an empty list would say the opposite. Each entry is `{request_id, created_at, kind, ...}`. `kind: "question"` carries `age_seconds`, the `questions` (`id`, `header`, `question`, `multi_select`, `allows_free_text`, `secret`, `options`) and the `actions` you may take; `kind: "out_of_scope"` carries a `reason` (`tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only`, `choice`) and an empty `actions`. `--raw` adds each request's untouched `tool_input` — on every entry, so asking for it on a session blocked on a large patch returns that patch. **Emptiness is not an error:** nothing pending and no agent at all both exit 0, the second with `agent_state: "dead"`.
- `answer <answer|cancel> [--request-id ID] [--answer 'ID=VALUE'] [--timeout N]` — answer, or decline, the question the agent is waiting on. Only a question: a tool approval or an MCP elicitation is refused (`not_a_question`) and stays the UI's business. `--answer` splits on the **first** `=`, so a value may contain more; repeat it once per question, or several times on one id for a multi-select question. A value matching no option is free text, accepted when the question's `allows_free_text` says so, and exclusive of the options. **The id comes from `pending-request`** and is not the same thing on both providers: a 1-based index on Claude Code, which has no question ids, and the native id on Codex. `--request-id` is optional when one question is pending and required when several are; a script that read `pending-request` should pass it anyway, so a request that cleared in between surfaces as `request_gone` instead of a wrong answer. Answering every question submits, answering some sends the agent a partial answer where the provider has one (Claude Code only), answering none is refused — declining is what `cancel` is for. **A session cannot answer its own question.** Rejections (exit 3): `no_pending_question`, `ambiguous_request`, `not_a_question`, `request_gone`, `agent_dead`, `missing_answers`, `unknown_question_id`, `free_text_not_allowed`, `free_text_exclusive`, `multi_select_unsupported`, `secret_answer_unsupported`, `option_not_accepted`, `self_answer_refused`, `provider_disabled`.
- `agents` — list subagents. Options: `--limit` (default 20), `--offset`, `--paginated`, `--slim`. Rows carry a `process` block too, always `null`: a subagent runs inside its parent's process.
- `plan [PATH] [--list]` — the session's tracked plan documents (`Session.plan_paths` — native Claude plan + detected plans/specs/handoffs..., incl. subagent-written, both providers). Default: the content of the most recently updated one, as `{path, abs_path, content}`; errors when the session tracks none. With `PATH`: that document, matched against the tracked entries only (stored project-relative/absolute path as shown by `--list`, or its resolved absolute form — never an arbitrary filesystem path). `--list`: every tracked entry, newest first, enriched with a resolved `abs_path` (worktree-aware) and fresh `exists` — same entries as the default view's `plan_paths` field (minus `abs_path`).
- `workflows` — list the session's workflows (Claude Code only). Options: `--limit` (default 20), `--offset`, `--paginated`, `--result`, `--full`. The listing answers "which runs are there": name, summary, status, timing, counts, phase progress. It drops the execution trace (`workflowProgress`), the script, the logs, the args and the result — on the heaviest run measured, `workflowProgress` alone was 94% of 774 KB, one prompt and one result preview per agent. `--result` adds what each run produced; `--full` returns the envelope verbatim, which is what `session workflow <id>` gives for a single run.
- `workflow <ID>` — show one (Claude Code only).
- Skill: [`twicc-session`](src/twicc/agent/plugin/twicc/skills/twicc-session/SKILL.md).

### `twicc search "<QUERY>"`
Full-text search across all session history using Tantivy query syntax (e.g. `websocket`, `body:websocket AND from_role:user`).
- `--limit` (default 20), `--offset`, `--paginated`, `--project TEXT` / `--workspace TEXT` (mutually exclusive; scope to a project plus its git worktrees, or a whole workspace including its members' worktrees), plus the shared filiation/visibility/annotation filters.
- Skill: [`twicc-search`](src/twicc/agent/plugin/twicc/skills/twicc-search/SKILL.md).

## Sessions — create & drive

### `twicc create-session <PROMPT>`
Create a session. `PROMPT` is text or a path to a file whose content is the prompt. With no flags it uses the default provider, the current directory as project, and the settings defaults. Over `--remote`, a file-path prompt is read on the client; prefix an absolute path with `remote:` to read it on the remote server instead. `@@` include expansion: an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the prompt (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max); inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path, and in inline text they are an error; a missing file expands to nothing (a marker alone on its line takes the whole line with it — optional includes); a directory, unreadable or non-UTF-8 file is an error; `@@@@` escapes a literal `@@`; the final text is capped at 500 KB; over `--remote`, markers resolve on the client and `@@remote:/abs/path` on the server. `--no-expand` disables it.
- **Target:** `--project TEXT` (id or path; new directories auto-resolved), `--provider claude_code|codex`.
- **Worktree:** land the session in a git worktree of `--project` (the CLI counterpart of the UI's new-worktree button): `--project` becomes the source repo, the session lands in the worktree — registered as its own project linked back via `worktree_of`, inheriting the source's agent defaults/trust. `--worktree-branch BRANCH` **creates** a new worktree (existing branch checked out, new one created with `-b`), with `--worktree-path PATH` (absolute) its new directory — required with the branch. `--worktree-path` **without** `--worktree-branch` instead **adopts** an existing worktree of `--project` (idempotent; the path must be a real linked worktree, no `git worktree add`). `--worktree-start-from REF` is optional and creation-only (defaults to the source HEAD, ignored when the branch already exists). Shared orchestration (create + adopt) with the HTTP endpoints (`POST .../worktrees/`, `POST .../worktrees/adopt/`, `GET .../worktrees/`) via `core/services/worktree_creation.py`. Settings flags resolve against `--project`, which the worktree inherits from.
- **Settings:** `--preset NAME`, `--model`, `--effort`, `--permission-mode`, `--thinking/--no-thinking`, `--claude-in-chrome/--no-claude-in-chrome`, `--fast-mode/--no-fast-mode`, `--question-widget/--no-question-widget` (drops the provider's widget tool — Claude Code `AskUserQuestion`, Codex `request_user_input` — so the agent asks in plain text), `--context-max` (`200k`/`1m`/`272k`). Per-flag options override a preset; unset fields fall back to the synced defaults. Run `twicc info agent-settings models presets` for the current valid values.
- **Settings aliases (provider-agnostic).** Beyond each provider's literal values, the settings flags accept aliases resolved per provider: `--model max`/`strongest` → top family, `medium`/`balanced` → middle family, `min`/`fastest`/`cheapest` → lightest; `--effort` / `--context-max` `min`/`max` → smallest/largest; `--permission-mode` `min`/`strict`/`safe` → most-locked (non-interactive), `max`/`open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive). A flag the chosen provider doesn't support (e.g. `--thinking` on Codex) is silently ignored (a no-op, not an error), so one command works across a mix of providers. `twicc info agent-settings` carries the live alias tables. **Project defaults inheritance:** a settings field neither passed as a flag nor set by the preset takes the target project's `default_agent_settings` value for the chosen provider (inherited up the project chain: git-worktree main repo, then path ancestors), then the global default; `--provider` itself, when omitted, falls back to the chain's `default_provider` before the global one. The resolved values are frozen onto the session at creation (same snapshot semantics as the UI). In an **untrusted** project (or unknown trust), `create-session` / `update-session` resolve `permission_mode` against a restricted subset (`bypassPermissions`/`yolo` removed; `max` → the most permissive *allowed* mode) and clamp an out-of-subset value to the project's untrusted default with a note on stderr — see the `permission_mode_if_untrusted` block of `twicc info agent-settings`.
- **Visibility:** `--hidden` — create the session invisible to the UI (no list/search/broadcast/counter), still counted in cost aggregates. Requires a non-interactive permission mode (`bypassPermissions`/`dontAsk` for Claude Code; `yolo`/`strict` for Codex) and `question_widget=False`.
- **Notification behavior:** `--mute-on-user-turn` suppresses only this session's finished-working notifications (toast, sound, browser notification, and Apprise user-turn event). Questions and approvals still notify. The flag is independent of `--hidden` and does not change global notification settings.
- **Metadata:** `--title TEXT` (≤ 200 chars), `--annotation KEY=VALUE` (repeatable), `--annotations-file PATH`, `--attach PATH` (repeatable; images/PDF/text up to 5 MB each, 100 files / 32 MB total; over `--remote`, prefix an absolute path with `remote:` to read it on the server).
- **Wait for the answer:** `--wait-reply` keeps going after the session is created, until it answers, and adds a `reply` block to the result: `outcome`, `line_num`, `is_final`, `since_line_num`, `waited_seconds`, the answer's `text`, and an `error` string on `wait_failed` (drop it with `--no-reply-text` when you only need the go-ahead, not the payload in your context). `outcome` is `replied` (the message closing the turn), `awaiting_user_input` (a **pending request** — a tool approval or a question — which only a human can clear, so there is nothing left to wait for; an answer arriving in the same poll wins), `provider_error` (the provider refused the turn: quota, outage — your request was not the problem), `ended` (the turn is over and nothing closed it: a crash, an interruption, or an empty answer), `timeout`, `backend_gone` (TwiCC stopped or restarted, so nothing could be observed), or `wait_failed` (the wait itself broke; the session is unaffected). The wait reads the transcript, not the process state, so a session held busy by a Monitor or a live subagent reports as soon as it speaks instead of hours later. `--wait-timeout` caps it (default **300 s**, the ceiling MCP callers are asked to respect — an MCP client may itself give up on a tool silent that long, so over MCP pass a shorter value and come back; no way to wait forever). **A timeout is not a failure** — the agent keeps working and `since_line_num` says where to resume; the exit code never reflects the wait, only whether the session was created.
- Plus `--timeout`.
- The spawning session is recorded automatically (`spawned_by`) when the command runs from inside a session.
- Skill: [`twicc-create-session`](src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md).

### `twicc send-message <SESSION_ID|parent> [PROMPT]`
Send a message into an existing session (resurrects it if dead). Keeps the session's stored settings. `parent` targets the spawner of the calling session. When the caller is itself a TwiCC session, the recipient receives the text under a sender header — a single `:: message from <relation> session <id> ("**<title>**")` line, then the text — with the relation (`your spawned session` / `your parent session` / `a sibling session` / `another session`) computed from the spawn tree; no header for a human caller or a self-send. Over `--remote`, a file-path `PROMPT` is read on the client; prefix an absolute path with `remote:` to read it on the server instead. The same `@@` include expansion as `create-session` applies (`--no-expand` disables it).
- `--attach PATH` (repeatable; over `--remote`, prefix an absolute path with `remote:` to read it on the server), plus `--timeout`. `PROMPT` is optional when at least one `--attach` is given: a message made only of attachments is valid (unlike `create-session`, which always needs text).
- **Wait for the answer:** `--wait-reply` keeps going until the session concludes — an answer, or a **pending request** only a human can clear (`outcome: awaiting_user_input`) — and adds the same `reply` block as `create-session`, with `--wait-timeout` and `--no-reply-text`. Here `since_line_num` is a real cursor — the session's `last_line`, read server-side the instant the agent took the message — so the previous turn's closing message is not returned in its place; chaining `--wait-reply` calls is safe by construction, each returning only once the answer is indexed. That cursor also ships on its own as `last_line` in every `sent` result, with or without the flag; `since_line_num: 0` on a `replied` means none came back (a backend older than the flag) and the answer is worth checking.
- Skill: [`twicc-send-message`](src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md).

### `twicc send-messages [SESSION_ID...] [--message <TEXT>]`
Batch sibling of `send-message`: the same message to several sessions at once, selected with the same model as `update-sessions` plus a peer channel (positional `SESSION_ID...` ∪ `--spawned-by <ID|self>` / `--descendants <ID|self>` / `--siblings <ID|self>` / `--annotation`; no `parent`). `--siblings self` is the canonical worker → peers broadcast (unique to `send-messages` among the batch commands; the reference session is always excluded). `--attach` is validated per session against its provider (a file one provider rejects becomes a per-id error); `--message` is optional when at least one `--attach` is given. The same sender header as `send-message` tops the text per recipient (the relation wording depends on each target). Over `--remote`, a file-path `--message` and any `--attach` accept the `remote:` prefix on an absolute path to read it on the server instead of inlining the client's copy. Each send starts/resumes an agent — a batch can cold-start many stopped sessions; and `sent` ≠ done, so pass `--wait-reply` (below) rather than chaining `processes wait`. Output is keyed by session id (`{summary, results}`); a per-session failure never fails the batch (exit `0`), exit `6` when nothing was sent. The same `@@` include expansion as `create-session` applies to `--message` (`--no-expand` disables it).
- **Wait for the answers:** `--wait-reply` adds the same `reply` block `send-message` returns, per entry, plus `replied` / `all_replied` to the summary — `replied` counting `outcome: replied` alone, so a recipient that concluded on `awaiting_user_input` leaves `all_replied` `false` — each recipient waited from its own cursor, only those that reached `sent`. `--wait-first` stops at the first recipient to answer or hit a pending request and leaves the rest `outcome: pending`; `--wait-all` (default) waits for every one. A recipient whose turn crashed never ends a `--wait-first` batch. `--no-reply-text` behaves as on `send-message`; `--wait-timeout` (default 300 s) is one wall-clock budget for the whole batch, since they are waited on together.
- Skill: [`twicc-send-messages`](src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md).

### `twicc update-session <SESSION_ID|self> <SUBCOMMAND>`
Change a session without sending a message. `self` targets the current session. All subcommands accept `--timeout`.
- `settings` — change agent settings (patch by default; `--preset` switches to replace mode). Per-field flags mirror `create-session` (`--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--question-widget`, `--context-max`), including the provider-agnostic aliases (`max`/`min`/`open`/`strict`/… — see `create-session`); `--unset <field>` resets one to the synced default. A field the session's provider doesn't support is silently ignored (no-op); when **every** touched field is a no-op the command returns status `noop` and exits `0`. Startup settings restart the agent; live ones apply on the next turn. Exception — Codex `question_widget` is a startup setting the Codex manager does not auto-restart for (it rides the per-thread `config` patch, read at `thread_start`/`thread_resume` only): the value is stored and applies once the process starts again (`processes stop`, then a message).
- `title <NEW_TITLE>` — rename (trimmed, non-empty, ≤ 200 chars).
- `archive` / `unarchive` — archive kills any live agent, tears down its tmux terminal, and (under `autoUnpinOnArchive`) unpins.
- `pin <project|workspace|all>` / `unpin` — sidebar pin scope.
- `hide` / `unhide` — toggle hidden visibility (hide requires a non-interactive permission mode and `question_widget=False`).
- `mute` / `notify` — suppress or restore this session's finished-working notification path without restarting its agent. These commands do not affect questions or approvals and are independent of hidden visibility. `notify` does not override global notification settings.
- `annotations <OPERATION...>` — ordered ops: `clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY`.
- Skill: [`twicc-update-session`](src/twicc/agent/plugin/twicc/skills/twicc-update-session/SKILL.md).

### `twicc update-sessions <SUBCOMMAND> [SESSION_ID...]`
Apply the same update to several sessions at once — the batch sibling of `update-session`. Sub-commands: `archive` / `unarchive`, `pin --mode <project|workspace|all>` / `unpin`, `hide` / `unhide`, `mute` / `notify`, `annotations --op <OPERATION>` (each `--op` repeatable), and `settings` (same flags as the singular). `mute` and `notify` change only the per-session finished-working notification path and do not restart agents; `notify` does not override global notification settings. No `title` (a shared title across sessions doesn't apply). Each sub-command takes a positional `SESSION_ID...` list merged (union) with the same scoped selection as `processes stop`: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. No `parent`, no `--spawn-tree`, no `--siblings` (unlike `send-messages`: you don't batch-mutate your peers). `--timeout` is a wall-clock budget for the whole batch. `settings` resolves per session against its own provider — provider-agnostic aliases (`max`/`min`/`open`/`strict`/…) land on the right value per session, a field a session's provider doesn't support is a silent no-op for that session (per-id status `noop`, counted as success), and a genuinely invalid value on a supported field becomes a per-id error (the other sessions still update). Output is keyed by session id: `{summary: {total, succeeded, failed, all_succeeded}, results: {<id>: <per-id outcome>}}`. A per-session failure never fails the batch (exit `0`); exit `6` when no session was updated.
- Skill: [`twicc-update-sessions`](src/twicc/agent/plugin/twicc/skills/twicc-update-sessions/SKILL.md).

## Artifacts

### `twicc artifacts` / `twicc artifacts bookmark <SESSION_ID|self|parent> <PATH>` / `twicc artifacts unbookmark <SESSION_ID|self|parent> <PATH>`
List bookmarked artifacts (viewable files saved from a session's Artifacts tab), or add / rename / re-scope / remove a bookmark. A bookmark is keyed on `(session, relative_path)` and carries a name plus a visibility scope (`project`/`workspace`/`all`). The only artifacts TwiCC tracks are bookmarked ones.
- Listing (read-only, no server needed): `--project TEXT` / `--workspace TEXT` (mutually exclusive; same worktree-aware scope helpers as `twicc sessions`), `--scope <project|workspace|all>` (filter on each bookmark's own scope, independent of project/workspace — e.g. `--scope all` for the ones bookmarked everywhere), `--limit` (default 20), `--offset`, `--paginated`. Ordered by most recently updated; each row is `{id, name, scope, session_id, project_id, relative_path, root, file_ext, created_at, updated_at}`.
- `bookmark` — upsert on `(session, path)`: create, or rename / re-scope an existing bookmark. `PATH` is relative to the session's artifacts directory (the listing's `relative_path`) or an absolute path confined to it. `--name` required; `--scope` defaults to `project` on create and is kept as-is on update when omitted.
- `unbookmark` — remove the bookmark for `(session, path)` (symmetric with `bookmark`; the file need not still exist).
- `self` or `parent` without a current session fails locally with `session_context_not_found`; `parent` from a root session fails with `parent_not_found`.
- Both writes require the live server (broadcast so open UIs refresh) and take `--timeout`; shared mutation service with the REST endpoints (`core/services/artifact_bookmark_mutation.py`).
- Skill: [`twicc-artifacts`](src/twicc/agent/plugin/twicc/skills/twicc-artifacts/SKILL.md).

## Sharing

Read-only public links to a session transcript or a bookmarked artifact, served under `/share/<token>/` on a **dedicated share host** (a hostname distinct from the working origin; set it in Settings → Sharing — `shareBaseUrl`). The token is the credential; per-link password / expiry / revoke are separate. Agents can use the full share surface (skill [`twicc-share`](src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md) + MCP tools), gated by two synced settings, both off by default: `allowAgentSessionShares` / `allowAgentArtifactShares` (Settings → Sharing). With a kind enabled, an agent may create shares whose target is its own session or a spawn-tree descendant, manage shares created in its own subtree, revoke ANY share of that kind, and read every URL for shares of that kind; with that kind disabled, mutations are refused (`agent_sharing_disabled`) and reads return rows with `token`/`url` null (`"redacted": true`). Agent session shares default to frozen snapshots; `--max-display debug` and password clearing are refused to agents. This gate is a guardrail against an obedient agent, not a security boundary — the CLI, the DB file and the settings themselves are reachable from a session's shell (accepted trust model, design §5.2).

### `twicc share` / `twicc share show <ID>`
List (read-only, direct DB — works with the server down) or show one share as JSON. Listing: `--kind <session|artifact>`, `--session <ID|self|parent>`, `--project TEXT` (worktree-aware scope), `--include-revoked`, `--limit` (default 50), `--offset`, `--paginated`. Each row is the owner serializer (`id`, `token`, `kind`, `label`, `status`, `options`, `view_count`, …) plus a resolved `url` (absolute when `shareBaseUrl` is set, else the relative `/share/<token>/` path).

### `twicc share create session <SESSION_ID|self|parent>` / `twicc share create artifact <BOOKMARK_ID>`
Create a link (requires the live server). Session: `--label`, `--password`, `--expires ISO`, `--live/--frozen` (live-follow or snapshot), `--max-display <conversation|simplified|normal|debug>`, `--include-subagents/--no-subagents`, `--title` (public title; default = the session title), `--show-title/--no-title` (master switch; `--no-title` shows viewers a generic label and ignores `--title`). Artifact: `--label`, `--password`, `--expires`, `--title` (default = the bookmark name), `--show-title/--no-title` (same master switch); the artifact is snapshotted at creation (design D7).

Creation returns `{status, share_id}`, not a token or URL. Use `twicc share show <SHARE_ID>` next to get the resolved URL.

### `twicc share update <ID>` / `revoke` / `unrevoke` / `delete` / `propagate <ID>`
Manage an existing link (live server; broadcasts so open UIs refresh). `update` edits `--label` / `--password` / `--expires`. `revoke`/`unrevoke` toggle availability (row + counters kept). `delete` removes it (and its snapshot dir). `propagate` re-freezes a snapshot session share to the current line / re-snapshots an artifact share.

## Peers

Cross-instance agent messages between two users' TwiCC installations. Pairing (add / verify / accept / revoke) is **human-only** — managed in Settings → Peers, no CLI or MCP surface exists for it. An outbound message always waits for the REMOTE user's approval before reaching any of their agents; messages must be self-contained (instances share no memory).

### `twicc peers`
List peer instances as `{id, name, state, last_contact_at}` — `active` (messageable) and `broken` (revoked or unreachable, kept so a failing send can be explained). No arguments.
- Skill: [`twicc-peers`](src/twicc/agent/plugin/twicc/skills/twicc-peers/SKILL.md).

### `twicc peer-send <PEER> <TITLE> <PROMPT>`
Send a titled message to a peer (id or exact local name). `TITLE` is the required subject the remote user triages on — inline text only, one flattened line, 100 chars max (over-long is rejected, never truncated); `PROMPT` is inline text or a file path; `--reply-to <MESSAGE_ID>` answers a message of this peer using the id from its delivered-message header; `--attach` (repeatable) works like `send-message`; `--timeout` sets the wait. A malformed id reports `invalid_reply_to`; a conforming id absent from this peer reports `unknown_reply_to`. Success returns `{status: "sent", message_id, peer_id, peer_status: "pending"}` — `pending` until the remote user delivers or refuses. Server failures land as `rejected` (exit 3) with the detail in the error code (`peer_broken`, `unreachable`, `send_failed`).
- Skill: [`twicc-peer-send`](src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md).

### `twicc peer-message <MESSAGE_ID>`
Re-check one outbound message's status (read-only): `pending` / `delivered` / `done` (the remote user dealt with it themselves, no agent) / `refused` / `failed` plus the summary metadata, including who answered it (`latest_reply_author`). There is no push on resolution — poll this when asked.
- Skill: [`twicc-peer-message`](src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md).

## Live processes

### `twicc processes` / `twicc processes <SUBCOMMAND>`
List or act on the live agent processes the backend currently runs. The CLI projects state onto four virtual values: `starting`, `assistant_turn` (generating), `awaiting_user_input` (blocked on a UI click), `user_turn` (idle).
- Listing: `--provider`, `--state <virtual>`, `--limit` (default 20), `--offset`, `--paginated`, plus the shared filiation/visibility filters. `--annotation` requires a filiation scope here; use `--spawned-by self --annotation ...` for direct children, or `--spawn-tree self --annotation ...` only when you explicitly want the whole tree.
- `get <SESSION_ID...>` — live state per id (`state="dead"` placeholder for stopped; `session_known` flags typos).
- `stop [SESSION_ID...]` — batch-stop (idempotent, tolerant). Optional scoped selection: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. No `parent`, no `--spawn-tree`. `--timeout` is a wall-clock budget for the whole batch; `--force` hard-kills every selected process (SIGKILL the tree, no grace window).
- `wait [SESSION_ID...] <STATUS...>` — block until session ids reach matching virtual states. Items mix ids and statuses, auto-discriminated. Optional scoped selection: `--spawned-by <ID|self>` or `--descendants <ID|self>`, plus `--annotation` to narrow that scope. Required `--timeout FLOAT`; `--all` (default) / `--first`; `--transition` (require a state change first).
- Skill: [`twicc-processes`](src/twicc/agent/plugin/twicc/skills/twicc-processes/SKILL.md).

### `twicc process <SESSION_ID> <SUBCOMMAND>`
Inspect or control one session's live process. Bare `twicc process <id>` prints the current row.
- `stop` — kill the live agent (`reason="manual"`, like the UI's *Stop process*; idempotent). Options `--timeout`; `--force` (hard kill — SIGKILL the process tree now, no grace window).
- `wait <STATUS...>` — block until the process reaches any listed virtual state (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`). Required `--timeout FLOAT`; `--transition`.
- Skill: [`twicc-process`](src/twicc/agent/plugin/twicc/skills/twicc-process/SKILL.md).

## Spawn tree & filiation

### `twicc topology <SESSION_ID|self>`
Show the spawned-session tree containing a session, rooted at its top-level ancestor: an id-only tree first, then per-node metadata, process state, and aggregate child/cost data. Any id in the tree resolves to the whole tree.
- `--processes/--no-processes` (default on) — include compact live process state.
- `--full-sessions/--no-full-sessions` (default off) — full `session` serialization per node vs. a slim subset.
- `--annotation` — mark nodes with `matches_annotations` (see below).
- `--siblings` — mark the anchor's siblings with `matches_siblings` (see below). Boolean flag; the tree is preserved, not pruned.
- Skill: [`twicc-topology`](src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md).

### Shared filiation, visibility & annotation filters

`sessions`, `processes` listing, and `search` accept these cross-cutting filters (`topology` takes the annotation/siblings variants as node markers, see below):

- `--spawned-by <ID|self|parent>` — direct children of a session.
- `--spawn-tree <ID|self>` — every session in the tree containing that id (any id resolves to its tree).
- `--descendants <ID|self|parent>` — every session transitively spawned by a session, target excluded.
- `--siblings <ID|self>` — the *other* sessions spawned by the same parent, target always excluded. `parent` is not supported. The direct way to address your peers; `--spawned-by parent` is the same set but includes yourself.
- The four filiation filters are mutually exclusive and each implies `--include-hidden`.
- `--include-hidden` / `--only-hidden` — opt hidden sessions into (or restrict to) the results.
- `--annotation KEY[OP]VALUE` (repeatable, AND-combined) — operators `=`, `!=`, `:exists`, `:not-exists`, `:in:V1,V2`; `KEY` is a dotted path; values are typed (`true`/`false`/`null`/int/float/string).

`topology` preserves the full tree but marks nodes: `--annotation` adds a `matches_annotations` flag, `--siblings` adds a `matches_siblings` flag (the anchor's siblings).

Process-control and batch-mutation subcommands are narrower on purpose: `processes stop`, `processes wait`, and `update-sessions` accept `--spawned-by <ID|self>` or `--descendants <ID|self>` plus optional `--annotation`, but not `parent`, not `--spawn-tree`, and not `--siblings` — you observe and message peers, you don't stop or mutate them. `send-messages` is the one batch command that does take `--siblings` (peer broadcast).

## Run the provider CLIs directly

`twicc claude [...]` and `twicc codex [...]` run the Claude Code or Codex CLI bundled with TwiCC, using your existing credentials. They honour the instance's `.env`, provider homes included (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`), so they run against the same homes as the backend. These are passthrough utilities (no dedicated skill).

---

For multi-session coordination built on top of these commands — spawning a tree of cooperating sessions and aggregating their work — see [`ORCHESTRATION.md`](ORCHESTRATION.md).
