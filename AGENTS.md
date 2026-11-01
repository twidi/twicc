# TwiCC agent instructions

**TwiCC** (The Web Interface for Claude and Codex) is a self-contained web UI. It browses and drives Claude Code and Codex sessions. One process, no external service.

This file is shared by Claude (`CLAUDE.md`) and Codex (`AGENTS.md`). Lines marked **Claude:** or **Codex:** apply to that provider only.

## Working rules

- **Quality:** use the best standards. The only allowed shortcut: tests and linting are not mandatory.
- **Never implement without an explicit invitation.** An explanation of requirements is not an invitation. Ask questions, then wait.
- **Preserve user changes.** The checkout can contain uncommitted changes you did not make. Do not revert, overwrite, move or clean them.
- **Rebase:** never on a remote branch (`origin/main`, …) unless asked. Rebase on the local branch.
- **Language:** write all artifacts in English: code, UI strings, comments, names, docs (incl. `docs/plans/`). French is for live chat only.
- **Plans and specs location:** write every spec, design and implementation plan in `docs/plans/`, whatever skill produces it (Superpowers or other). Never write in `docs/superpowers/`. This overrides the skill's default path.
- **Local instructions:** **Claude:** `CLAUDE.local.md` loads automatically. **Codex:** if `AGENTS.local.md` exists at the repo root, read it after this file. It extends this file and wins on conflict. Direct system, developer and user instructions win over both.

## Commits and CHANGELOG

- Subject: Conventional Commit, `type(scope): summary`, lowercase summary (`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, …).
- Always add a body that explains the change.
- Trailer, with the exact name of the model you run as now (never hardcode it):
  - **Claude:** `Co-Authored-By: Claude MODEL <noreply@anthropic.com>`.
  - **Codex:** `Co-Authored-By: Codex MODEL <codex@openai.com>`.
- **CHANGELOG:** write it only on explicit request. Write only in `## [Unreleased]` (create it at the top if absent). Never edit a dated release section (`## [X.Y.Z] - DATE`). Re-read the top of the file first: an `[Unreleased]` line can be promoted into a release between turns. If so, add a new `[Unreleased]` entry.
- Releases: follow `docs/release-process.md`.

## Operations reserved to the user

Do not run these on your own initiative. On explicit request, run them without confirmation. Otherwise, remind the user at the end of the task.

- **Django `migrate`** after you create a migration. (`devctl.py start` applies migrations itself.)
- **Dev server restart** after backend changes (via `devctl.py`).
- **Package installation** (`npm install`, `uv add`). On request: `cd <target> && uv add <pkg>`. Never `uv pip install`.

## Stack

uv + npm · Django 6 ASGI (Uvicorn, Python ≥ 3.13) · Channels (in-memory layer) · SQLite (WAL) · watchfiles · claude-agent-sdk + openai_codex · Vue 3 (`<script setup>`) + Vite 7 · Pinia + VueUse · Web Awesome 3 (`wa-*`) · CodeMirror 6 · xterm.js · markdown-it + shiki + mermaid.

## Commands and tools

- **Lint:** `uvx ruff check .` (ruff is not installed; `uv run ruff` fails). Run any absent Python tool with `uvx <tool>`. Never `uv add` a tool to make a command work.
- **Python tests:** `cd <checkout> && uv run pytest`. `twicc.settings_test` isolates data dir, provider homes and DB, and forbids the real data dirs. Never add `TWICC_DATA_DIR=` to a pytest command.
- **Frontend tests:** `cd frontend && npm test`.
- **Codex real-binary tests** (`tests/test_codex_migration_integration.py`): skipped unless `TWICC_CODEX_INTEGRATION=1`.
- **Hermetic LLM diagnostic:** run `uv run python scripts/diagnose_hermetic_llm.py --provider all --live --yes` after one of these changes:
  - the small Codex model (`TITLE_MODEL`, `_REFRESH_MODEL`);
  - a Codex or vendored Codex SDK update (`docs/codex-vendoring.md`);
  - a Claude Agent SDK update.

  Do not ship on `FAIL` or `INCONCLUSIVE`.

### uv: never target the ACTIVE environment

| Command | Targets | |
|---|---|---|
| `uv run` · `uv sync` · `uv add` | the project of the cwd | ✅ |
| `uvx <tool>` | nothing (ephemeral) | ✅ |
| `uv pip …` · anything with `--active` | the ACTIVE environment | ❌ |

An agent session inherits `VIRTUAL_ENV` from the TwiCC backend, which runs in the MAIN repo. The ❌ forms sync the project into that venv: from a worktree, they make the main repo import the worktree's sources. The `does not match the project environment path` warning is normal: never "fix" it with `--active`.

To check which sources you run: `uv run python -c "import twicc; print(twicc.__file__)"`. The path must be inside your checkout.

## Data directory

- Resolution in `src/twicc/paths.py`: `$TWICC_DATA_DIR` if set, else `~/.twicc/` (the **prod** data dir).
- `paths.py` does **not** detect worktrees. Only `devctl.py` sets `TWICC_DATA_DIR=<worktree root>`.
- The `.env` in the data dir wins over the inherited environment. `TWICC_DATA_DIR` comes only from the environment.
- Provider homes (`CLAUDE_CONFIG_DIR`, `CLAUDE_SECURESTORAGE_CONFIG_DIR`, `CODEX_HOME`) come **only** from the `.env`. Read every provider path through `src/twicc/provider_homes.py` at call time, never as an import-time constant. Give every launched process `provider_env_overlay()`. Design: `docs/plans/2026-09-02-provider-home-dirs-design.md`.
- Contents and backups: see `src/twicc/paths.py` and `src/twicc/data_backups.py`.

## devctl.py: dev servers

```bash
uv run ./devctl.py start|stop|restart [front|back|all]
uv run ./devctl.py status
uv run ./devctl.py logs [front|back] [--lines=N]
uv run ./devctl.py kill-tmux   # worktrees only
```

- Default ports: frontend 5173, backend 3500. Debug with `<data_dir>/logs/{backend,frontend}.log`.
- To start, run **one** `start` command and read the logs. devctl runs `npm ci`, applies migrations, and in a worktree copies the DB and user config, picks free ports and writes the `.env`.
- **Never** run `npm install`/`npm ci`, `migrate`, or touch `node_modules` to start servers. A parallel `npm install` corrupts devctl's `npm ci` (`ENOTEMPTY`).
- A post-start port-check timeout during initial sync is not a failure. Confirm in `backend.log`.

### Worktrees

- Each worktree has its own data dir: `.env` (ports), `.devctl/`, `db/`, `logs/`, JSON data files.
- **Prefix every Bash command with `cd <worktree> && `.** A wrong cwd on `devctl stop/restart` or `migrate` hits the main servers and data.
- Give the user the localhost URLs that devctl prints.
- `start --empty-db`: fresh DB. `start --fresh-providers`: fresh DB plus the worktree's own empty provider homes. Otherwise the worktree shares the provider homes of its `.env` (by default the real `~/.claude` / `~/.codex`).
- To exit or delete a worktree, **always** run `stop all` then `kill-tmux`, even if you did not start the processes.
- A worktree backend lets direct loopback requests skip the password. All password checks go through `src/twicc/auth/access.py`. Never write one inline.

### Python or Django outside devctl

Without `TWICC_DATA_DIR`, Python or Django outside devctl (`manage.py`, shell, one-liners) uses `~/.twicc/`, even after `cd`. A `migrate` there applies branch migrations to the prod DB. Do both steps, then check the DB path:

```bash
cd <worktree>
TWICC_DATA_DIR=$PWD uv run python -m django <command> --settings=twicc.settings
# check: settings.DATABASES['default']['NAME'] must be inside the worktree
```

## Architecture map

- Entry: `run.py → cli.main()` (`src/twicc/cli/`). Startup: initial sync of provider roots into `SessionItem` rows, then a background compute process fills metadata (`compute_version`).
- Django ASGI (`src/twicc/asgi.py`): REST + SPA, WebSocket `/ws/` (`UpdatesConsumer`) and `/ws/terminal/<session_id>/` (PTY).
- Watcher (`src/twicc/providers/sessions_watcher.py`): JSONL files are append-only. On change, read from `last_offset`, save, broadcast over WS to Pinia.
- Agent managers drive provider SDKs (`src/twicc/providers/`). Providers write JSONL. The watcher picks it up.
- Periodic tasks (price sync, usage quotas): `src/twicc/pricing_task.py`, `src/twicc/usage_task.py`.
- MCP server: `src/twicc/mcp/`. A new CLI command auto-adds its RPC route and MCP tool. Kill switch: `TWICC_NO_MCP=1`.
- Models: `src/twicc/core/models.py`. Frontend: `frontend/src/`. Designs: `docs/plans/`.

## Backend invariants

- **Python style:** `orjson`, never stdlib `json`. `NamedTuple` for immutable data. Import aliases (`as`) only for a real name collision or a needed intent clarification.
- **Timeline:** write `TimelineEvent` only through `record_event` / `record_event_sync` (`src/twicc/core/services/session_timeline.py`). Never insert directly. Never use Django signals (the code uses `queryset.update()`). Designs: `docs/plans/*timeline*-design.md`.
- **Agent settings:** the seven per-session fields are a closed bundle with one shape for all providers. A new provider flag is a new `Session` column, classified in the provider's `getAgentSettingsCategories()` (`frontend/src/providers/`). Never a side table.
- **Project defaults** resolve at session creation only, in two mirrors: `frontend/src/utils/projectAgentDefaults.js` and `src/twicc/project_agent_defaults.py`. Keep them in sync.
- **Trust is human-only.** Never expose it as an agent flag or skill. `permission_mode_if_untrusted` is never a `Session` field. See `src/twicc/core/services/trust.py` and `docs/archives/plans/2026-06-09-project-trust-design.md`.

## Artifact network broker

HTML artifacts use plain `fetch()`. TwiCC proxies it after a per-host user consent. These are the design, **not gaps. Do not "harden" them:**

- Hard-block only the cloud metadata address (`169.254.169.254` / `fd00:ec2::254`). Never range-block loopback, LAN or public addresses.
- Forward headers verbatim, `Authorization` included (only hop-by-hop and similar mechanical drops).
- Consent is client-side (`frontend/src/artifact-broker/host.js`). Do not add a consent check to `src/twicc/artifacts/proxy.py`.
- The iframe CSP `connect-src 'none'` is the egress boundary. The shim is DX, not security.

The in-SPA preview and the `/artifacts/<id>/` page share `frontend/src/composables/useArtifactBroker.js`. Never re-implement one side. Design: `docs/archives/plans/2026-06-18-artifact-network-broker-design.md`.

## Frontend traps

- **Not HMR'd bundles:** after you edit `frontend/src/` `artifact-broker/`, `artifact-shell/`, `artifact-theme/`, `browser-companion/`, `share-session/`, `share-recent/` or `element-select/`, run `cd frontend && npm run build`.
- **Circular imports break HMR.** Never import `router.js` statically from utils, composables or stores: use `await import('../router')`. Break store↔store and store↔composable cycles with a lazy `import()`. Use `defineAsyncComponent` when a composable imports a component that closes a cycle.
- **Public assets:** `frontend/public/` is served at `/` in dev but at `/static/` in the build. A raw `/icons/foo.svg` string breaks only on the backend. Always use `resolvePublicAssetUrl()` (`frontend/src/utils/publicAsset.js`).
- **Item content:** never read `item.content` directly, never `JSON.parse` it, never touch `_parsedContent`. Use `getParsedContent` / `setParsedContent` / `clearParsedContent` from `frontend/src/utils/parsedContent.js`. Use `hasContent(item)`, not `!!item.content`: synthetic items have parsed content but no raw string.
- **Iframes:** an iframe reloads when its node moves. Embedded pages render through `PersistentFrame` (`frontend/src/components/frames/`). Never `<Teleport>` an iframe. Never reorder `FrameHost`'s registry.
- **Dialog forms:** copy `frontend/src/components/project/ProjectEditDialog.vue` (agents often get these wrong):
  - Wrap the content in a `<form id="…" @submit.prevent="handleSave">`.
  - Put the submit button outside the form: `type="submit"`, and set its `form` attribute with `setAttribute()` (`wa-button` does not expose `form` as a property).
  - Focus the first input in `@wa-after-show`, never with `autofocus`. Put the cursor at the end with `setSelectionRange(len, len)`.
  - `trim()` text inputs before validation and submit. Check uniqueness client-side first.
  - Show errors in `wa-callout variant="danger"`.
  - Width: `--width: min(Xpx, calc(100vw - 2rem))`.
  - Guard `@wa-show`/`@wa-hide`/`@wa-after-*` handlers against bubbling from nested `wa-*` children (see Web Awesome).

## Web Awesome (3.3+)

- Import **every** component explicitly in `frontend/src/main.js`. A missing import works in dev and is unstyled in prod.
- Native events are unprefixed (`@click`). Custom events keep `wa-` (`@wa-show`).
- **Icon slots** (agents often get these wrong): in a button, use `slot="start"` / `slot="end"`, never `prefix` / `suffix`. In a `wa-dropdown-item`, use `slot="icon"`.
  ```html
  <wa-button><wa-icon slot="start" name="check"></wa-icon> Save</wa-button>
  <wa-dropdown-item><wa-icon slot="icon" name="plus"></wa-icon> New</wa-dropdown-item>
  ```
- **Custom events bubble.** A nested `wa-select` in a `wa-dialog` fires `wa-show`/`wa-hide` on the dialog. Scope every handler to its own element: `event.target !== event.currentTarget` guard, `.self`, or `.stop`.
- Docs: `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`.

## TwiCC plugin (agent skills)

- Skills live in `src/twicc/agent/plugin/twicc/skills/`. Read `src/twicc/agent/plugin/README.md` before you create or edit one.
- **Every `SKILL.md` change requires a `version` bump** in `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`. Otherwise providers serve a stale cached copy. Patch for a user-visible change; minor for a new skill, new flags, a rename or a removal.
