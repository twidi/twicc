# Anonymous Telemetry — Design

**Status:** design complete, decisions resolved with the user 2026-07-18; implementation not started
**Date:** 2026-07-18
**Scope:** opt-out anonymous usage telemetry for self-hosted TwiCC instances — what is measured (usage level first, feature adoption second), how it is derived and sent (daily snapshot computed from the DB, one POST per day), where it lands (a minimal self-hosted collector), and the privacy/transparency guarantees (no content ever, random instance id, visible payload, first-launch notice, settings toggle + env kill switch).

---

## 1. Goals and constraints

- **Primary question:** overall level of use per instance — how many active days, how intense (sessions, messages, presence time), at what scale (projects, concurrent agents). Feature adoption is secondary.
- **Never content:** no message text, no titles, no paths, no hostnames, no emails, no prompt/response material of any kind. Only counters, booleans, enums, and bucketed values.
- TwiCC is self-hosted with no central service today; telemetry requires the project's **first outbound-collection endpoint**, which must be near-zero cost and fully under the maintainer's control.
- Telemetry is **enabled by default** and disabled from the Global section of the settings panel. Default-on is only acceptable with loud transparency (first-launch notice, inspectable payload, documented fields).

## 2. Decisions (user-resolved 2026-07-18)

| Topic | Decision |
|---|---|
| Collector | Minimal self-hosted endpoint: **Cloudflare Worker + D1** (not a third-party analytics service) |
| Client data model | **Daily snapshot derived from the DB at send time** — no event table, no scattered instrumentation |
| Announcement | **Visible first-launch notice** after the upgrade, with a direct link to the toggle |
| Costs/tokens | Included **as buckets** (`0`, `<1`, `1-10`, `10-50`, `50-100`, `100-250`, `250-500`, `500-1000`, `1000+` USD/day — top tiers extended 2026-07-18), never exact values |
| Primary metric focus | **Usage level** (volumetry, regularity, intensity, scale); feature booleans kept as a small secondary block |

## 3. What is measured

One JSON document per POST, with a versioned schema (`schema: 1`). Two parts:

### 3.1 Instance block (sent with every payload)

- `instance_id` — random UUID v4 (see §4)
- `twicc_version`, `python` (major.minor), `os` (`linux`/`darwin`/`windows`), `arch`
- `providers` — enabled provider keys (`claude_code`, `codex`)
- `install` — enum `pip` / `pipx` / `uv-tool` / `uvx` / `git-dev` / `other`, best-effort. Derived from the same signals `_resolve_twicc_launch_prefix()` already uses (`src/twicc/settings.py:125`): `UV_RUN_RECURSION_DEPTH` + `DEV_MODE` → `git-dev`; argv0/`sys.executable` path signatures distinguish uv tool venvs and the uvx cache. pipx detection is **new** path-signature logic (the current resolver has no pipx branch); anything unrecognized → `other`, never a guess.
- `projects_bucket` (`0`, `1`, `2-5`, `6-20`, `21-50`, `51-100`, `101+`), `workspaces_bucket` (`0`, `1`, `2-5`, `6-20`, `21+`) — bucketed counts, two distinct scales
- `remote_access` — boolean: a password is configured (instance served beyond localhost)

### 3.2 Daily blocks (one per not-yet-sent complete day)

All derived from existing models at send time unless noted:

- **Volumetry:** sessions created, reported as **nested counts** provider → model family → version → effort (e.g. `{"claude_code": {"opus": {"4.8": {"high": 5}}}}`): each level is a real dimension — no composite string keys to re-parse downstream (family names contain dashes, versions dots), versions of one family are never mixed, and aggregating at any level is a subtree walk (user-decided 2026-07-18, replacing the earlier per-dimension breakdowns) — plus a per-provider permission_mode breakdown (mode vocabularies are provider-specific). The model resolves from the agent-settings alias, falling back to the raw SDK model id (`Session.model`) for sessions not created through TwiCC (external CLI runs, benchmarks); unresolvable → `"unknown"`, and a missing effort reads `"unknown"` in the couple. Also: user messages sent; subagent sessions; workflow runs (`Workflow`); crons created that day (`SessionCron.created_at` range count — the day-attributable reading actually implemented, in place of "active crons").
- **Regularity:** implicit — the sequence of day blocks itself gives active vs. inactive days per instance.
- **Intensity:** `presence_bucket` — minutes of human presence that day, bucketed (`0`, `<30`, `30-120`, `120-360`, `360-720`, `720+`). Presence is ephemeral in-memory (`src/twicc/presence.py`), so this is the one metric that needs an accumulator: a 60 s ticker checks `is_user_present()` and increments today's counter in the telemetry state file (§5.3).
- **Scale:** peak concurrent live agents that day (max simultaneous `ProcessRun`s — sampled by the same ticker, stored alongside presence minutes).
- **Cost:** `cost_bucket` per day (from `DailyActivity` aggregates), buckets as decided in §2.
- **Features (secondary, deliberately small):** booleans/counters that are genuinely free to derive from the DB — shares created (`Share`), artifact bookmarks created (`ArtifactBookmark`), sessions spawned by other sessions (orchestration). Metrics with **no DB trace** — terminal/PTY usage, MCP tool calls (only present inside raw `SessionItem.content`) — are **deferred**: including them would require instrumentation or content scans that §5.2 forbids. This block may shrink or grow across schema versions without renegotiating the design.

### 3.3 Explicitly excluded

Message/prompt content, session titles, project names and paths, file paths, git data, hostnames, usernames, emails, IP addresses (client never sends one; collector does not keep them), exact costs/token counts, per-session identifiers of any kind (no session ids, no project ids — only aggregate counters).

## 4. Anonymity model

- `instance_id` is a **random UUID v4** generated on first telemetry startup — derived from nothing (no MAC, no hostname hash), so it identifies nothing; it only makes "unique instances" and retention countable. Standard practice (Homebrew, VS Code, Next.js).
- A **"Reset instance ID"** action in settings regenerates it, severing history.
- The collector **does not log or store IP addresses** — a server-side commitment, stated in the public docs.
- **Transparency:** a "View last payload" action in settings shows the exact JSON last sent (persisted in the state file); the public transparency page on the collector hostname (§7) lists every field and its purpose. This inspectability is what makes default-on defensible.

## 5. Client architecture

### 5.1 `telemetry_task.py` (new module)

One periodic asyncio task following the `pricing_task.py` pattern (stop event, httpx, failures logged at debug and never blocking), started by the orchestrator like the other background tasks. Loop:

1. Skip entirely if disabled (§6) — checked every cycle, so a settings change applies without restart.
2. On startup and then every 24 h: compute day blocks for complete days after `last_sent_date` (capped at the **30** most recent — older days are dropped, not queued), build the payload, POST to the collector. Day boundaries are **UTC**, matching the `DailyActivity` keying; the presence/peak ticker (§5.3) accumulates under the same UTC date.
   **No historical backfill:** on the very first telemetry-enabled start, `last_sent_date` is initialized to the current UTC date, so pre-telemetry history in the DB is never sent — the first payload covers at most the first full day *after* the feature became active. This is what makes §6's "a user who disables on first sight leaks nothing" hold.
3. On HTTP success: advance `last_sent_date`, persist the payload copy for "View last payload". On failure: leave the marker untouched; the next cycle retries the same window.

A second lightweight ticker (60 s) accumulates presence minutes and samples peak concurrent `ProcessRun`s (§3.2) into the state file. It runs only while telemetry is enabled.

### 5.2 Snapshot derivation

Pure read-only ORM aggregation over `Session`, `DailyActivity`, `Share`, `ArtifactBookmark`, `Workflow`, `SessionCron`, `ProcessRun` — executed once per send, in the task. No new tables, no migrations, no instrumentation in feature code. Metrics not derivable from the DB are limited to the two ticker-sampled values above.

### 5.3 State file

`telemetry.json` at the data-dir root (`src/twicc/paths.py` resolution, atomic writes via `atomic_json.py`): `instance_id`, `last_sent_date`, per-day `{presence_minutes, peak_agents}` accumulators (pruned once sent or older than 30 days), `last_payload`. Per-worktree data dirs get their own instance id — acceptable: a worktree instance genuinely is a separate running instance.

## 6. Opt-out

- `telemetryEnabled: true` in synced settings (`src/twicc/synced_settings.py` defaults) — flat camelCase per the synced-settings convention; the nested `telemetry: {"enabled": ...}` sketch above was normalized at implementation. Toggled from the **bottom of the Global section** of `SettingsPopover.vue`. A companion flat key, `telemetryNoticeSeen`, tracks whether the first-launch notice (§6 below) has been acknowledged.
- `TWICC_NO_TELEMETRY=1` initializes `telemetryEnabled=false` once per instance at backend startup, including settings copied into a worktree. The `environment_default_applied` marker lives in instance-local `telemetry.json`. Manual changes survive restarts even while the variable remains set. The task stays running and checks synced settings at runtime.
- **Off means no data about that period:** on an off→on transition (setting re-enabled), the last-sent marker jumps to the current day — day blocks covering the disabled window are never sent, mirroring the first-run no-backfill rule.
- **First-launch notice:** on the first backend start where telemetry is active and no notice has been acknowledged (flag in the state file), the frontend shows a dismissible notice — "TwiCC collects anonymous usage statistics — see what is sent or disable it in Settings" — linking straight to the toggle. Sending is not blocked on acknowledgement, but the first cycle only fires 24 h of data later by construction (only *complete* days are sent), so a user who disables on first sight leaks nothing.

## 7. Collector (server side)

A **Cloudflare Worker + D1** database, insert-only:

- **Hostname (user-decided):** `twicc-telemetry.twidi.com`, attached to the worker as a Cloudflare **Custom Domain** (the `twidi.com` zone is already on Cloudflare; DNS + certificate are handled automatically). One hostname, two roles:
  - `GET /` — a public **transparency page** (what is collected, field by field, how to disable), served via Workers **static assets** (plain HTML/CSS files deployed alongside the worker; the worker code keeps only the API route). The docs link next to the settings toggle (§8) points here.
  - `POST /v1/telemetry` — the ingestion endpoint below.
- `POST /v1/telemetry` — validates schema version and size cap, inserts one row per payload (raw JSON + received date + schema version). No auth (the payload is worthless to forge at small scale; a size cap and per-IP rate limit — with the IP discarded after the check — bound abuse).
- **No IP persistence**, no user agent persistence.
- Reading is out of band for v1: D1 is queryable in SQL from the dashboard/CLI; a proper stats dashboard is future work.
- Lives in a **`telemetry-collector/` subfolder of the TwiCC repo** (user-decided 2026-07-18), with a README covering all operational tasks (deploy, update, query, domain) so day-to-day management never requires re-research. The collector is a **separate implementation plan** from the TwiCC-side work; the client/collector contract above is the only coupling. Deployment goes through the maintainer's Cloudflare account.

The client only knows one URL and one JSON schema, so the collector can be replaced later without touching the client contract.

## 8. Settings & UI summary

Bottom of the Global settings section, in order: the enable toggle, a one-line description with a docs link, "View last payload" (dialog with the stored JSON), "Reset instance ID". All secondary actions hidden when the toggle is off except the toggle itself.

## 9. Out of scope (v1)

- Fine-grained UI event instrumentation (clicks, tab usage) — would require frontend instrumentation and a WS funnel for low value at this stage.
- A public stats dashboard on top of D1.
- Any per-user (as opposed to per-instance) notion — TwiCC is single-user by design.
