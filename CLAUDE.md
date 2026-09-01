# CLAUDE.md

**TwiCC** (The Web Interface for Claude and Codex) — self-contained web UI for browsing and interacting with Claude Code and Codex sessions. Single process, zero external services, one command to launch.

## Working Rules

- **Quality:** best standards everywhere. Only allowed shortcuts: no mandatory tests or linting.
- **Never implement without explicit invitation.** When the user explains requirements or shares thoughts, wait for confirmation before writing code. Ask clarifying questions, but an explanation is not an invitation.
- **Preserve existing user changes.** The current checkout may already contain uncommitted changes. Treat any change you did not make as user-owned: do not revert, overwrite, move, or clean it up unless the user explicitly asks.
- **Git rebase:** never on remote branches (`origin/main`, …) unless explicitly asked. Always rebase on the local branch; if it exists, use it.
- **Language:** all written artifacts — code, UI strings, comments, names, docs (incl. `docs/plans/`) — in English. French is reserved for live chat (and the dev's `CLAUDE.local.md`). Even when the user speaks French, write UI/code/docs in English.

## Commit Conventions

- Format the subject line as a Conventional Commit — `type(scope): summary` (scope optional), with `type` one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore` (etc.) and a lowercase summary.
- When creating commits, include a descriptive commit body that explains the change, not only a subject line.
- Add a `Co-Authored-By: Claude MODEL <noreply@anthropic.com>` trailer for the model that performed the work. `MODEL` is the exact name of the Claude model you are currently running as, taken from your own environment at commit time — e.g. `Opus 4.8 (1M context)`, `Fable 5`, `Sonnet 4.6`. Do not hardcode a model name; use whichever one is actually running.
- **CHANGELOG:** only ever write to the `## [Unreleased]` section — create it at the top (above the newest dated release) if it is absent. NEVER modify a numbered, dated release section (`## [X.Y.Z] - DATE`): it is frozen history. Before touching the file, re-read its top and confirm which section a line actually falls under — `[Unreleased]` entries get promoted into a dated release between turns, so a line that was correct earlier may now sit under a released version. If so, add a new `[Unreleased]` entry rather than editing the promoted line (even to "extend" it, e.g. bumping `...to vB` → `...to vC`).

## Operations Reserved to User

Never run these on your own initiative. If the user explicitly asks, do it without confirmation. Otherwise notify at task end (or pause and ask if truly necessary mid-task):

- **Django migrations:** after you modify models and create the migration, remind the user to `migrate` their own running instance. (Starting/restarting via `devctl.py` auto-applies migrations at backend startup — never `migrate` by hand to bring servers up.)
- **Dev server restart:** after backend changes, remind the user to restart via `devctl.py` (no need to do it on every message)
- **Package installation:** after adding deps, remind the user to run `npm install` or `uv add`. (`devctl.py start` already runs `npm ci` via the editable rebuild — never pre-run it yourself.)

## Stack

uv + npm · Django 6 ASGI (Uvicorn, Python ≥ 3.13) · Channels + InMemoryChannelLayer · SQLite (WAL) · watchfiles · claude-agent-sdk + openai_codex · Vue 3 (Composition API, `<script setup>`) + Vite 7 · Pinia + VueUse · Web Awesome 3+ (`wa-*`) · CodeMirror 6 · xterm.js (PTY) · markdown-it + shiki + mermaid.

**Python lint: ruff (line-length=120), NOT an installed dependency** — always `uvx ruff check .`; `uv run ruff` fails with `Failed to spawn: ruff`. (It gets declared and pinned the day the project-wide lint pass happens, not before.) Same rule for **any** Python tool absent from the env: run it with `uvx <tool> …` instead of reporting it as missing, and never `uv add` it just to make a command work.

**Tests: pytest + pytest-django** (declared in the `test` extra, so `uv run` resolves them). Main repo: `uv run pytest`. Worktree: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest`.

**uv — never target the ACTIVE environment.**

| Command | Targets | |
|---|---|---|
| `uv run` · `uv sync` · `uv add` | the cwd's project | ✅ |
| `uvx <tool>` | nothing, ephemeral | ✅ |
| `uv pip …` | the ACTIVE environment | ❌ |
| anything `--active` | the ACTIVE environment | ❌ |

`VIRTUAL_ENV` says nothing about your project: an agent session inherits it from the TwiCC backend, which runs under `uv` in the MAIN repo. The ❌ forms do not merely READ that environment — uv treats it as the current project's environment and SYNCS the project into it. Run from a worktree, they install the worktree's editable package into the MAIN repo's venv, which then imports the worktree's sources until someone rebuilds it. Silent corruption of another checkout; it happened on 2026-08-14.

The ✅ forms resolve the project from the cwd and ignore a mismatched `VIRTUAL_ENV`. The `does not match the project environment path` warning they print is that mechanism working — never "fix" it with `--active`.

**Adding a dependency** is the user's call (see Operations Reserved to User) — but when they ask you to do it: `cd <target> && uv add <pkg>`. Never `uv pip install`.

**In doubt, check what you IMPORT, not which venv you are in:** `uv run python -c "import twicc; print(twicc.__file__)"` — the path must be inside the directory you are working in. `sys.prefix` does not answer this: the editable install's `.pth` can point at another checkout's `src`.

Frontend tests: `cd frontend && npm test` (node:test, auto-discovers `src/**/*.test.js`).

## Architecture

Entry: `run.py → cli.main()`.

- **Startup:** *Initial sync* scans each provider data root (`~/.claude/projects/`, `~/.codex/sessions/`, …) and bulk-inserts raw `SessionItem`s (fast, no metadata). Then a separate *background compute* process fills metadata (display_level, kind, groups, costs, git) for sessions whose `compute_version` is below the provider's `CURRENT_COMPUTE_VERSION`, then exits.
- **Django ASGI:** HTTP (REST + SPA catch-all) and WebSocket — `/ws/` `UpdatesConsumer` (data sync, process control, title suggestions) and `/ws/terminal/<session_id>/` (raw ASGI PTY, optional tmux).
- **watchfiles task:** JSONL change → incremental read from `last_offset` → save to DB (full metadata, inline for real-time accuracy) → WS broadcast → Pinia → Vue.
- **Periodic:** price sync from OpenRouter (24h); usage quota fetch from provider APIs (5min where supported) — idle-gated: the fetch loops pause after 30min with no human presence (monotonic) and no active agent, resuming eagerly on a presence ping or an agent entering ASSISTANT_TURN (`twicc/usage_task.py` `should_run_usage_cycle`/`note_activity`).
- **Agent managers:** provider SDKs drive interactive sessions → providers write JSONL → watcher picks up.
- **MCP server:** the skill-covered CLI surface is auto-exposed as MCP tools at `/mcp` (raw-ASGI in front of Django, token-auth; `src/twicc/mcp/`), wired per-session into both providers (Claude `mcp_servers`, Codex `thread_start` config). Tools run in-process: reads via `rpc/invoker`, writes through the dual-mode drop-request transport (`cli/_drop_request/transport.py`) straight into `core/services/*` — no drop files. A control plane orthogonal to project permissions: every tool available in every mode and auto-approved (Claude allow-lists `mcp__twicc` **and** auto-allows it in `can_use_tool` — the allow-list is required because `dontAsk`/read-only denies a permissioned tool before the callback fires; Codex `default_tools_approval_mode="approve"`). Adding a CLI command auto-adds the RPC route AND the MCP tool. Kill switch `TWICC_NO_MCP=1`; Codex schema deferral behind the `TWICC_MCP_CODEX_DEFER` constant.

**Sync strategy:** JSONL files are append-only — on change, compare `mtime`, `seek(last_offset)`, read new lines, insert, update offset.

## Data Directory

All persistent data (db, logs, config) lives in one data dir, resolved (centralized in `src/twicc/paths.py`; `devctl.py` has equivalent standalone logic):

1. git worktree → worktree root (forced); 2. `$TWICC_DATA_DIR` if set; 3. default `~/.twicc/`.

Contents: `.env` (infra config: ports, password hash), synced user config (`settings.json`, `workspaces.json`, `layouts.json` (named-layouts catalog), `terminal-config.json`, `message-snippets.json`, `seen-tips.json`, `seen-help.json`, `providers-status.json` (per-provider upstream status: current value, last incident, user acknowledgment), `{provider}-settings-presets.json`), `db/data.sqlite(+shm/+wal)`, `search-index/` (Tantivy), `drop-requests/` (CLI drop-files picked up by a watcher), `logs/` (`backend.log`, `frontend.log`, and in dev mode, `sdk/{provider}/{session_id}.jsonl`).

## devctl.py — Dev Servers

Use when the user asks to start/stop/restart dev servers.

```bash
uv run ./devctl.py start|stop|restart [front|back|all]
uv run ./devctl.py status
uv run ./devctl.py logs [front|back] [--lines=N]
```

Default ports: frontend 5173, backend 3500 (verified after start). `start --empty-db` for a fresh DB in worktrees on user request. Debug via `<data_dir>/logs/{backend,frontend}.log`. PIDs in `.devctl/pids/` (always local to project/worktree root).

**To start/restart, run the single `start` command and read the logs — devctl does everything:** it rebuilds the editable install (runs `npm ci`), auto-applies pending migrations at startup, on first setup copies db + search index + user config from `~/.twicc/` (never `.env`, `logs/`, `drop-requests/`) and symlinks `artifacts/` + `scratch/` to `~/.twicc/` (shared with the main instance; `--empty-db` drops the symlinks for isolation), finds free ports (default+1: 3501/5174), writes them to `.env`.

**Never run `npm install`/`npm ci`, `migrate`, or touch `node_modules` yourself when starting servers** — wasted and harmful: a parallel `npm install` corrupts devctl's `npm ci` (`ENOTEMPTY`). The post-start port check can time out during initial sync — not a failure; confirm via `backend.log`.

When starting in a worktree, give the user the localhost URLs from devctl's output. When asked to exit/kill/delete a worktree, you MUST run `stop all` even if you didn't start the processes.

### Worktrees

devctl auto-detects worktrees and sets `TWICC_DATA_DIR=<worktree root>`, so each worktree has its own backend/frontend, `.env` (ports), `.devctl/`, `db/`, `logs/`, and json data files. Always check your cwd before starting so you know whether you're in a worktree.

**Prefix every Bash command with `cd <worktree> && `** — never trust the persistent cwd. A wrong cwd on a destructive command (`devctl restart/stop`, manual `migrate`) hits the main project's servers/data dir and kills real work.

**Running Python/Django without devctl:** `paths.py` does NOT detect worktrees (only devctl injects `TWICC_DATA_DIR`). Any other invocation (one-liners, `manage.py`, shell, ad-hoc migrations) silently falls back to `~/.twicc/` — the **prod** data dir — even after `cd`. So always do both: (1) `cd` into the worktree (editable install resolves to its source + migrations); (2) set `TWICC_DATA_DIR=$PWD` in the command's env.

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.settings
```

Before any data-dir-dependent read/write (esp. migrations), sanity-check the resolved path:
```python
from django.conf import settings; print(settings.DATABASES['default']['NAME'])  # must be inside the worktree
```
Forgetting this is destructive: a `migrate` from the wrong cwd applies branch-only migrations to the prod DB, leaving it unwritable by `main`.

## Database Models

Key models in `src/twicc/core/models.py` (read the source for full field lists; non-obvious points only here):

- **`Project`** — cross-provider working dir, ID from path via `path_to_project_id`. `worktree_of` is an auto-detected self-FK to the main repo (never set manually; a worktree's sessions/cost/activity aggregate into it). Per-project agent defaults `default_provider` + `default_agent_settings` seed NEW sessions, inherited up the chain (worktree main repo, then path ancestors), resolved **at creation only** by `projectAgentDefaults.js` (UI) and its mirror `project_agent_defaults.py` (CLI) — never re-resolved for a running session. `default_layout_id` (nullable CharField) seeds the new session's `Session.layout` the same way — inherited up the same chain when null, with the global `settings.defaultLayoutId` as ultimate fallback. `default_browser_url` (nullable CharField) is the session Browser tab's default URL, inherited up the same chain with a workspace-level `browserUrl` (workspaces.json) fallback — resolved live by the pane, never materialized.
- **`Session`** — one JSONL file; `last_offset`/`last_line` drive incremental sync. Carries costs, `type` (session/subagent), `parent_session` (self-FK), lifecycle timestamps, the closed `AgentSettings` bundle (see below), `mute_on_user_turn` (mutable per-session UI state outside that bundle; suppresses only the finished-working notification family and is independent of `hidden`), `layout` (JSONField, the per-session dockable-layout intention blob — full intention minus the transient `maximized`; unlike the agent bundle it stays **mutable** after creation, persisted + synced like `annotations`), `browser_url` (nullable CharField, the Browser tab's last URL, restored at first activation after a reload — wins over the project/workspace default; mutable + synced like `layout`; the pane upgrades from toolbar-tracked to real in-page history when the embedded page includes the opt-in companion script (`/_twicc/browser-companion.js`, source `frontend/src/browser-companion/`, postMessage bridge)), and `tasks` (JSONField, latest cross-provider task/todo/plan snapshot — Claude `TodoWrite`/`Task*`, Codex `update_plan` — normalised to `{provider, source, line, updated_at, explanation, items:[{status, content?, activeForm?}]}`, refreshed by both compute paths; `{}` = none; mutable + synced like `layout`; drives the Tasks tab), and `plan_paths` (JSONField, plan-like documents the session touched — the native Claude plan plus pattern-detected docs (plans/specs/handoffs..., `providers/plan_docs.py`) written via file tools or shell commands, both providers; entries `{path, exists, created_at, updated_at, source}` with `path` relative to the project dir when inside it (worktree-portable), absolute otherwise; rebuilt authoritatively by full recompute, merged additively by live sync, native plan latched by the plans watcher; `[]` = none; drives the Plan tab), and `has_workflows` (bool, monotonic one-way — true once a `wf_*.json` file exists at the root of the session's `<id>/workflows/` folder; set by a filesystem probe in compute and latched live by the watcher, never reset to false; Claude-Code-only).
- **`SessionItem`** — one JSONL line; `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind`, `group_head`/`group_tail` for collapsible groups.
- **`PeerMessage`** — one cross-instance message. The local session link is `origin_session` for an outbound row and `delivered_to_session` for an inbound row. `reply_to_message` resolves the answered row once within the peer relationship. `(peer_id, thread_id)` is the local thread key; `thread_id` never crosses the wire. Design: `docs/plans/2026-08-11-peer-threading-design.md`.
- **`ArtifactBookmark`** — user bookmark of one rendered artifact (`session` FK, denormalised raw `project` FK, `relative_path`, `name`, `scope` reusing `PinMode`; unique `(session, relative_path)`). Scope mirrors session pinning (project/workspace/all, worktree-aware at resolution); "not bookmarked" = no row. Browsed via the sidebar's Artifacts mode (`/…/artifacts/:bookmarkId?`). `allowed_hosts` (JSON dict) is the network-broker allowlist — see **Artifact Network Broker** below. `denied_hosts` (same shape) = explicit owner deny decisions, auto-refused without prompt in the owner preview (broker reads both dicts live). **`ArtifactNetworkDenial`** — refused-fetch provenance rows (share/ip/user_agent + counter, NULL share = owner preview), coalesced+flushed by `artifacts/denial_tracking.py`, purged on allow; powers the bookmark dialog's Network access section (design `2026-07-10-artifact-network-access-design.md`).
- **`ToolResultLink`** / **`AgentLink`** — link tool_use ↔ tool_result / spawn-agent tool_use ↔ subagent session (both provider-agnostic).
- **`Workflow`** — one Claude Code workflow run (`Workflow` tool / `wf_*.json`); `session` FK + unique `run_id`. `raw_json` = source of truth, a **3-state** enriched envelope (minimalist → synthesized running view → real `wf_*.json`), durable past Claude deleting the files; helper columns `script_hash`, `synthesis`, `cost`, `phases_cost`. Subagents are `Session`s keyed `<run_id>:<agent_id>`. Claude-Code-only; design in the workflows handoff under `docs/plans/`.
- **`Share`** — one read-only public capability URL (`token`, 256-bit plaintext) to a `session` or `artifact_bookmark` (CheckConstraint: exactly one, matching `kind`); per-link `label`/`password_hash`/`expires_at`/`revoked_at`/`options` (kind-validated by `core/services/share_mutation.py`), denormalised `view_count`/`last_viewed_at`. Served under `/share/<token>/` ONLY on the dedicated share host (`shareBaseUrl`; host gate `origin_gate.py` (the common `PublicOriginGate`, design `docs/plans/2026-08-13-peer-origin-routing-design.md`), never the working origin); viewer bundle at `/_twicc/share/`. Agent-usable behind two default-off synced settings (`allowAgentSessionShares`/`allowAgentArtifactShares`): spawn-subtree scope + provenance (`Share.created_by_session`), shape-contract gate in the `*_from_payload` wrappers (`core/services/share_agent_gate.py`); the owner REST UI bypasses the gate. Agent extension: `docs/plans/2026-08-10-agent-sharing-design.md`. **`ShareAccess`** — one page-view row (pruned to 500/share). Design: `docs/plans/2026-07-05-sharing-design.md`.
- **`ModelPrice`** (OpenRouter pricing) · **`UsageSnapshot`** (per-provider quotas) · **`WeeklyActivity`/`DailyActivity`** (stats by `(project, date, provider)`, `project=NULL` = global) · **`ProcessRun`** (live process, cron lifecycle + crash recovery) · **`SessionCron`** (CLI-created crons) · **`Command`** (synced slash-commands) — all per provider where relevant.

### Agent Settings — Closed Bundle

The seven per-session fields (`selected_model`, `effort`, `thinking_enabled`, `permission_mode`, `context_max`, `claude_in_chrome`, `fast_mode`) are a **closed bundle** with one shape across all providers (on `Session`, the WS payload, synced localStorage). Each provider declares which fields it uses via `getAgentSettingsCategories()` (`frontend/src/providers/baseHelpers.js` + overrides); unlisted ones are ignored. New provider-specific flags follow the same pattern — add a `Session` column, classify it in the provider's categories, never a side table (rationale in the `Session.claude_in_chrome` comment).

**`permission_mode_if_untrusted` is NOT in the bundle** — a per-provider *default-shaping* field in global settings / presets / `default_agent_settings`, never on `Session`. At creation, project trust picks which one materializes into the stored `permission_mode` (trusted → `permission_mode`, else → `permission_mode_if_untrusted`, restricted to `UNTRUSTED_PERMISSION_MODES`, no `bypassPermissions`/`yolo`). A backend floor re-clamps at agent build and live at runtime (re-resolving trust fresh, not the launch snapshot) (`core/services/trust.py`), mirrored by the CLI (`cli/_drop_request/aliases.py`). **Trust is human-only** — never an agent-facing flag/skill. See `docs/plans/2026-06-09-project-trust-design.md` §13.

## Artifact Network Broker

Interactive HTML artifacts make outbound network calls with a **plain `fetch()`** — TwiCC brokers them server-side (CORS bypass) behind a **per-host user consent prompt**. Design: `docs/plans/2026-06-18-artifact-network-broker-design.md` (+ `-handoff.md`).

**Invariants — do NOT "harden" past these; they are the design, not gaps:**
- **Only the cloud metadata address (`169.254.169.254` / `fd00:ec2::254`) is ever hard-blocked.** Never range-block loopback/LAN/public — every other target is reachable with the user's informed consent on an IP-pinned, honest prompt (the self-host operator clicking *is* the authority). No flag, no deployment detection.
- **Headers pass through verbatim** (only mechanical drops: hop-by-hop, `host`, `content-length`, …); the artifact's `Authorization` is forwarded.
- **Consent is client-authoritative.** `frontend/src/artifact-broker/host.js` gates via the per-bookmark allow/deny lists (`ArtifactBookmark.allowed_hosts`/`denied_hosts`, read client-side) + in-memory "This session" / persisted "Forever" grants, coalescing concurrent requests to one host into one prompt; a denied host is auto-refused without prompting. The server proxy (`twicc/artifacts/proxy.py`) does **NOT** re-check consent — by decision (design §6.4 "Decision update"); don't add a server-side allowlist gate.
- **The CSP `connect-src 'none'`** on the artifact iframe is the real egress boundary; the injected shim (`artifact-broker/shim.js`, built bundle) is DX, not security.

**One shell, both run contexts.** The in-SPA preview (`FilePane.vue`) and the dedicated page (`/artifacts/<id>/`, a standalone Vite bundle under `frontend/src/artifact-shell/`) mount the **same** broker host + `ArtifactBrokerPrompt.vue` via `composables/useArtifactBroker.js` — never re-implement one side. Backend: `twicc/artifacts/broker_html.py` (shim+CSP wrap, the trusted shell page) + `views.artifact_serve`/`artifact_shell_asset`. The shim + shell + browser-companion + share-session bundles are **not HMR'd** — `cd frontend && npm run build` after editing `artifact-broker/*`, `artifact-shell/*`, `browser-companion/*`, `share-session/*`/`share-recent/*` (the standalone read-only share viewer, design §8) or `element-select/*` (the shared element picker, bundled into the companion and lazy-imported by the SPA for the artifact HTML preview's select mode).

**Artifact data persistence.** An HTML artifact may write under its own `data/` subfolder — plain `fetch` PUT/DELETE (+ dir-GET listing) through the broker's own-asset path, gated server-side by the host-set `X-Twicc-Artifact-Doc` header; `window.twicc.data` sugar in the shim. Silent under an artifacts root, tab-lifetime prompt elsewhere; shares stay read-only; a page's own `data/` writes never reload its preview. Design: `docs/plans/2026-08-05-artifact-data-persistence-design.md`.

## Python Patterns

- **`NamedTuple`** for simple immutable data (return values, decisions, configs) — works with all field types incl. lists; prefer over `@dataclass` when mutability isn't needed.
- **`orjson`**, not stdlib `json`, for all backend JSON (~6× faster, handles high-volume JSONL).
- **Aliased imports (`as`)** only when strictly necessary: (1) name collision (e.g. multiple `main` → `as foo_main`); (2) disambiguating intent for a less generic verb (e.g. `patch_client as patch_client_for_logging`). Avoid cosmetic `as _foo`/`as django_settings` when there's no real conflict — it's noise and harms grep.

## Frontend Patterns

### Circular imports (HMR) — CRITICAL

Cycles make Vite HMR fall back to full reloads (recurring issue).
- Never import `router.js` from utils/composables/stores → use lazy `await import('../router')`.
- Never mutual static imports store↔store or store↔composable → lazy `await import()` in the less-frequent direction.
- Never statically import components from composables when those components close a cycle → `defineAsyncComponent(() => import(...))`.
- Common shapes: `main.js → … → main.js` (extract shared code), `router → views → components → util → router` (lazy router), `store ↔ store`, `store ↔ composable`, `composable → component → store → composable`.

### Public assets (icons, images) — base-prefix trap

`frontend/public/` files are served at `/` in dev but under `/static/` in the built bundle (Vite `base: '/static/'`). A hardcoded absolute string like `/icons/foo.svg` is NOT rewritten by Vite (only HTML/CSS refs and imports are), so it 404s (→ SPA index HTML) in the built app served by the backend — works on Vite (5173), silently broken on the backend (3500). ALWAYS resolve public-asset paths through `resolvePublicAssetUrl()` (`utils/publicAsset.js`), never a raw `/icons/...`/`/foo.png` literal in JS.

### Drafts

Draft sessions/messages/media persisted to IndexedDB (`frontend/src/utils/draftStorage.js`), hydrated on startup before app mount.

### Virtual scrolling

Large item lists use a custom scroller (`useVirtualScroll.js`, `VirtualScroller.vue`): raw items → `computeVisualItems()` (display mode, group expansion) → rendered. Visual items are stabilized across recomputes — each new item is compared by `lineNum` to the cached one; identical → old reference reused, so Vue skips re-render even though `computeVisualItems` makes new objects.

### Persistent frames (iframes)

An `<iframe>` reloads whenever its DOM node is detached or re-parented — which is what KeepAlive (session switch) and Teleport (dock moves) do to panes. Embedded pages that must survive (Browser pane, artifact HTML preview) therefore render through `PersistentFrame` (`frontend/src/components/frames/`): the pane keeps a placeholder; the real iframe lives in `FrameHost` (mounted once in ProjectView), absolutely positioned over the placeholder's rect (`stores/framePool.js`). Never `<Teleport>` an iframe and never reorder `FrameHost`'s registry — both move the node and reload it. Over-iframe chrome goes through the frame's overlay layer (`overlayEl` + `<Teleport :disabled>`), not pane-local z-index (capped by `DockRegion`'s `isolation: isolate`). When a pane-local overlay must fully cover the preview instead (e.g. the Files tab's mobile file-tree overlay), the owner passes `PersistentFrame`'s `suppressed` prop to force the frame hidden (iframe keeps running) rather than fighting z-index.

### Session item content access — IMPORTANT

Never access `item.content` (raw JSON string) directly. Use `frontend/src/utils/parsedContent.js`:
- `getParsedContent(item)` — parsed object, lazy + `markRaw()` cached; works on session and visual items.
- `setParsedContent(item, parsed)` — set explicitly (synthetic items, or forwarding a cached result).
- `clearParsedContent(item)` — invalidate (e.g. when `item.content` changes).
- `hasContent(item)` — true if content available (raw or set); use instead of `!!item.content` for placeholders (synthetic items have parsed content but no string).

`JSON.parse(item.content)` and touching `_parsedContent` directly are forbidden.

### Dialog forms

When creating a form inside a `wa-dialog`, use `frontend/src/components/project/ProjectEditDialog.vue` as the reference implementation. Key patterns:

- **Form element:** wrap content in a `<form>` with `@submit.prevent="handleSave"` and a unique `id`.
- **Submit button outside form:** use `type="submit"` and set the `form` attribute via `setAttribute()` in a sync function (wa-button doesn't expose `form` as a property).
- **Focus management:** use the `@wa-after-show` event (not the `autofocus` attribute) to focus the first input after the dialog animation completes, and `setSelectionRange(len, len)` to put the cursor at the end.
- **Input validation:** apply `trim()` on text inputs before validation and submission.
- **Uniqueness checks:** validate client-side first (from store data); the backend enforces with a unique constraint.
- **Error display:** use `wa-callout variant="danger"` for validation and API errors.
- **Dialog width:** use `--width: min(Xpx, calc(100vw - 2rem))` to stay responsive.
- **Event propagation:** dialog `@wa-show`/`@wa-hide`/`@wa-after-*` handlers must guard against bubbling from nested `wa-*` children — see *Bubbling custom events* below. Failing to guard makes a nested `wa-select` opening/closing steal focus or close the whole dialog.

## Web Awesome (3.3+)

- Native events are **un**prefixed since v3 (`@click`, `@input`); custom WA events keep `wa-` (`@wa-show`, `@wa-after-show`).
- **Every component must be explicitly imported in `frontend/src/main.js`** (loads JS + shadow-DOM styles). Unstyled in prod but fine in dev → missing import.
- Icon slots: `start`/`end` inside buttons (not `prefix`/`suffix`), `icon` for `wa-dropdown-item`:
  ```html
  <wa-button><wa-icon slot="start" name="check"></wa-icon> Save</wa-button>
  <wa-dropdown-item><wa-icon slot="icon" name="plus"></wa-icon> New</wa-dropdown-item>
  ```
- Docs: one-file `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`; full set in same `dist` dir under `skills/webawesome/`.

### Bubbling custom events — recurring trap

WA custom events **bubble through the composed DOM**: a nested `wa-*` child fires the *same* event name the outer component listens for. Classic case — a `wa-select`/`wa-dropdown` inside a `wa-dialog` emits `wa-show`/`wa-hide`/`wa-after-show`/`wa-after-hide` on panel open/close; unguarded, the dialog handler re-runs focus logic (stealing focus from the dropdown) or treats it as the dialog closing (dismissing everything). Same family with `wa-switch`, nested `wa-details`, nested `wa-tab-group` (`wa-tab-show`/`wa-tab-hide`), per-row `wa-dropdown` `wa-select` reaching a parent selector, nested `wa-split-panel` `wa-reposition`.

**Always scope the handler to its own element** — equivalent idioms: a top guard `if (event.target !== ownRef/event.currentTarget) return`; the Vue `.self` modifier; or `.stop` when a nested control's event must never reach a same-named outer handler. When a parent must *veto* its own close while a child panel is open, combine the target guard with `event.preventDefault()` for the parent's own event only (see `ProjectView.vue` `onSelectorHide`).

## TwiCC Plugin (Agent Skills)

Skills live under `src/twicc/agent/plugin/twicc/skills/`, packaged as a versioned plugin (`.../twicc/.claude-plugin/plugin.json`).

**Any bundle change — add/edit/rename/remove a `SKILL.md` — REQUIRES bumping `version` in `plugin.json`**, or providers serve a stale cached copy. Bump: user-visible change → patch; new skill or new flags/options → minor; rename/removal → minor at least.

**Before creating/updating a skill, read `src/twicc/agent/plugin/README.md`** (structure, wording, anti-patterns) and a few existing skills to calibrate tone.

## Release Process

When the user asks to make a release, follow `docs/release-process.md`.
