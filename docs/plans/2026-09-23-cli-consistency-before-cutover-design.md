# CLI consistency before the 2026-10-01 cutover

**Status:** design, not implemented; implementation plan: `docs/plans/2026-09-23-cli-consistency-before-cutover-plan.md` (the plan wins where the two differ)
**Date:** 2026-09-23
**Cutover instant:** `LISTING_CUTOVER` (`src/twicc/cli/_output.py:113`), `2026-10-01T00:00:00` local — the instant already shared by the pagination, slim-listing and process-retirement changes; overridable for tests only by `TWICC_LISTING_CUTOVER` (section 8)

Paths are relative to the repository root; line numbers are those of `main`
at `bf57c675`. One file moved since: `src/twicc/providers/claude_code/compute.py`
(`32cb3a54`), so the section 5 references into it from `:1583` on are 31
lines lower (`compute_item_kind` is at `:1614`); `:66-69`, `:76` and `:348`
did not move. When HEAD has moved, locate a cited line by its quoted text.
This design extends three implemented ones and does not edit
them: `docs/plans/2026-09-08-pagination-cutover-design.md`,
`docs/plans/2026-09-23-session-listing-slim-cutover-design.md`,
`docs/plans/2026-09-23-process-commands-removal-design.md`.

## Problem

Reading the migration guide (`frontend/public/help/cli-rpc-migration-2026-10-01.md`)
as a user surfaced six inconsistencies in what lands on 2026-10-01, or next
to it:

1. Listings page at **50** from the date, not the 20 most of them use today.
2. `session <id>` and `whoami` keep the full session payload while every
   other session read turns reduced.
3. Batch lookups (`sessions get`, `projects get`, `workspaces get`) keep a bare
   array, and `peers` wraps its list in a `peers` key, while every listing
   returns `items`.
4. `sessions stop` returns a bare array while the other commands acting on
   several ids return `{summary, results}`.
5. `session <id> wait-reply` / `sessions wait-reply` start, by default, above
   the session's **current last line**. An orchestrator that spawns children
   without `--wait-reply` and then waits on them misses the answer of any
   child that answered before the wait began, and gets `ended`.
6. The guide presents `--since 2000-01-01` as a routine step and does not say
   which commands take `--since` / `--from`.

Two more points come from the owner, outside the guide:

7. A bare `sessions stop` stops every running session, the caller included,
   and no filter spares the caller.
8. Nothing shows the post-date behaviour before the date except patching the
   constant, which leaves every value computed at import on the old side.

## Decisions

| # | Point | Decision | When |
|---|---|---|---|
| 1 | Default page size | **20 everywhere**: every listing, `share` included, reads the one constant `PAGINATED_DEFAULT_LIMIT` (now `20`) instead of a literal | now (`share` 50 → 20 accepted by the owner); `session content` / `session messages` from the date, as already planned |
| 2 | `session <id>` | accepts `self` / `parent`; gains `--slim` / `--full`, accepted before **and** after the id; returns any `Session` row, like `sessions get` | now (additive) |
| 2 | `session <id>` default | reduced; before the date, full plus a notice | from the date |
| 2 | `whoami` | gains `--slim` / `--full`: with a flag, exactly what `session self` returns. Without a flag: today's shape and a notice before the date, the `session self` payload from the date | flags now; default from the date |
| 2 | CLI session payload | one enrichment step for every session a CLI command emits: `project_directory`, `artifacts_dir` (**always** the path), `scratch_dir`, `orchestration_scratch_dir`, the eight agent settings as **effective** values (stored values on a subagent row; `question_widget` `null` reported as `true`). The serializer and the UI are untouched | now (new keys additive; `artifacts_dir` and effective settings accepted by the owner) |
| 2 | Reduced projection | gains `last_line`, `cwd`, `git_directory`, `project_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir`, the seven agent settings it lacks, `compacted`, `hybrid` | now (`--slim` is unreleased) |
| 3 | Reading several objects | `{"items": [...]}`: listings (with `pagination`), batch lookups and `peers` (without) | from the date; `--paginated` opts in now |
| 4 | Acting on several ids | `{"summary": {...}, "results": {"<id>": {...}}}`: `sessions stop` joins `send-messages`, `update-sessions`, `sessions wait-reply` | now (unreleased) |
| 4 | `sessions stop` guardrails | a bare call is refused (exit 1); the calling session is never stopped, it is reported as `skipped_self` | now (unreleased) |
| 5 | Default wait cursor | **strictly after the session's last user message** (line 0 when it has none or no row yet); the current last line while the session's compute is not current | now (unreleased) |
| 6 | Usage rule | a send without `--wait-reply` is never followed by a wait without a cursor: `session <id> wait-reply --from <last_line>` after `send-message`; `sessions wait-reply --since <instant before the send>`, or one `session <id> wait-reply --from <last_line>` per id, after `send-messages`. Stated in the help texts and skills, not only in the guide | now |
| 7 | Test override | the undocumented variable `TWICC_LISTING_CUTOVER` replaces `LISTING_CUTOVER` when set | now |

**The output rule, in one sentence:** a command that **reads** objects returns
them in `items`; a command that **acts** on several ids returns a `summary`
and one `results` entry per id, keyed by id.

### What is released, what is not

`v1.94.2` is the last release. It has no `--paginated`, no envelope, no
`--slim` / `--full`, no `LISTING_CUTOVER`, no `sessions stop` and no
`wait-reply` command: the three cutovers themselves are unreleased. What is
released is the behaviour they change.

**The rule.** Nothing released breaks before 2026-10-01. A change that only
adds — a new key, a new flag, a new keyword (`session self` / `parent`), an
error that becomes an answer — applies now. A change to an unreleased
surface applies now. Everything else waits for the date and is announced by
a notice.

**Exceptions accepted by the owner.** Three changes of released values apply
now, without a date and without a notice: `artifacts_dir` always the path,
the agent settings as effective values, and the `share` page size 50 → 20.
The guide lists them (see "The migration guide").

**Dated to the cutover:** the `items` envelope of the batch lookups and
`peers`; `session <id>` and `whoami` reduced by default and `whoami`'s new
shape; and everything the three earlier cutovers already date.

| Surface | Released value (`v1.94.2`) | Change | When |
|---|---|---|---|
| flagless page size of `projects`, `workspaces`, `sessions`, `artifacts`, `session agents`, `session workflows`, `search` | 20 | none | — |
| flagless page size of `session content`, `session messages` | everything | 20 | from the date, with the existing pagination notice |
| flagless page size of `share` | 50 | 20 | **now, no notice** (owner's exception) |
| `--paginated` page size | unreleased (50 on `main`) | 20 | now |
| `session <id>` default payload | full | reduced | from the date, with a notice |
| `whoami` default payload | its own object | the `session self` payload, reduced | from the date, with a notice |
| `--slim` / `--full` on `session <id>` and `whoami` | absent | present | now (additive) |
| `session self`, `session parent` | exit 1 (no session with that id) | the caller's / its parent's row | now (an error becomes an answer) |
| `session <id>` on a row with no user message | exit 1 | the row | now (an error becomes an answer) |
| keys `project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget` on `sessions`, `sessions get` (placeholders included, as `null`), `session <id>`, `session agents` | absent | present | now (additive) |
| `artifacts_dir` on `sessions`, `sessions get`, `session <id>`, `session agents` | `null` until an artifact exists | always the path | **now, no notice** (owner's exception) |
| agent settings on `sessions`, `sessions get`, `session <id>` | stored value, often `null` | effective value | **now, no notice** (owner's exception) |
| `sessions get`, `projects get`, `workspaces get`, `peers` shapes | bare array / `{"peers": …}` | `{"items": …}` | from the date, with a notice; `--paginated` (new) opts in now |
| reduced projection (`--slim`) | unreleased | field additions | now |
| `sessions stop` | unreleased | `{summary, results}`; bare call refused; caller never stopped | now |
| `session <id> wait-reply`, `sessions wait-reply` | unreleased | new default cursor | now |
| `TWICC_LISTING_CUTOVER` | absent | test override, undocumented | now |

Checked with `git show v1.94.2:<path>`: `src/twicc/cli/_output.py` has no
`LIMIT` / `CUTOVER` / `slim` line, `src/twicc/cli/share.py` declares
`limit: int = 50`, `src/twicc/cli/sessions_stop.py` and
`src/twicc/cli/sessions_wait_reply.py` do not exist, and
`git show v1.94.2:src/twicc/cli/__init__.py | grep -c '"wait-reply"'` is 0.

The migration guide lists every row marked "now" on a released surface (see
"The migration guide").

## 1. Page size: 20 everywhere

`PAGINATED_DEFAULT_LIMIT` (`src/twicc/cli/_output.py:89`) goes from `50` to
`20`. Every non-retired listing passes the constant where it passes a literal
today, in its three places (`pagination_notice(default_limit=…)`,
`resolve_limit(default=…)`, `limit_help(…, …)`):

- the seven listings at 20: `src/twicc/cli/projects.py:18,41`,
  `workspaces.py:15,26`, `sessions.py:229,256`, `artifacts.py:27,65`,
  `session.py:376,388` (agents) and `:532,539` (workflows), `search.py:43,73`,
  and the `limit_help` calls in `src/twicc/cli/__init__.py:86,159,229,943,976,1067,1905`;
- `share`: `src/twicc/cli/share.py:37` (`default_limit=50`, and the comment
  above it at `:35-36`), `share.py:64` (`default=50`) and
  `src/twicc/cli/__init__.py:1193` (`limit_help("shares", 50)`).

`session content` / `session messages` keep `default=None` (everything before
the date). The retired `processes` listing (`src/twicc/cli/processes.py:116`,
`__init__.py:1420`) keeps its literal: it stops on the date. Its
`--paginated` page follows the constant through `resolve_limit` and becomes
20, like every other.

| Command | Flagless today | From the date (with 50) | From the date (now) |
|---|---|---|---|
| `projects`, `workspaces`, `sessions`, `artifacts`, `session agents`, `session workflows`, `search` | 20 | 50 | **20** (no change) |
| `session content`, `session messages` | everything | 50 | **20** |
| `share` | 50 | 50 | **20, now** |

**`share` changes now.** "One constant" leaves no literal to keep at 50 until
the date: a dated branch for one command would be the only per-command page
size left. `share` returns 20 shares where it returned 50, without a notice.
It is one of the owner's three exceptions to the rule (see "What is
released"); the guide lists it. It keeps no page-size clause in its
pagination notice: its default equals the constant.

Texts that name the page size:

- `pagination_notice` (`_output.py:155`) renders "and pages at N by default"
  only when `default_limit != PAGINATED_DEFAULT_LIMIT` (`:189-190`). With
  every listing passing the constant, only `session content` / `session
  messages` (`None`) keep the clause. No code change; its docstring
  (`:164-168`, "``50`` means its page size is not changing, which exempts
  ``share``") becomes "a default equal to the constant means the page size is
  not changing".
- `CUTOVER_NOTICE` (`:339`) and `CUTOVER_NOTICE_OBJECT` (`:347`) say "and
  pages at {PAGINATED_DEFAULT_LIMIT} by default" unconditionally. With the
  constant at 20 that clause would be false on the eight commands whose page
  size does not change on the date (the seven listings at 20 and `share`). So: `CUTOVER_NOTICE` and `CUTOVER_NOTICE_OBJECT`
  (`search`) lose the clause; a new `CUTOVER_NOTICE_PAGED` keeps it and
  replaces `CUTOVER_NOTICE` on `session content` and `session messages`
  (`src/twicc/cli/__init__.py:648,673`).
- `limit_help` (`_output.py:366`) already drops the "; N with --paginated"
  clause when `default == PAGINATED_DEFAULT_LIMIT` (`:376`): the eight
  commands at 20 render "(default: 20)." on both sides. Its comment (`:375`,
  "`share` already pages at 50") becomes "a command whose default equals the
  constant: the flag changes nothing for it".
- `PAGINATED_HELP` (`:355`) says "the page size becomes {N}"; it becomes "the
  page size is {N}" (true for the commands at 20 too).

## 2. One session payload: `session <id>`, `whoami`, and the CLI enrichment

### `session <id>`: keywords and lookup

`session <id>` accepts `self` and `parent`, resolved by
`resolve_session_keyword(session_id, param_name="SESSION_ID",
allowed=SELF_PARENT_KEYWORDS)` (`src/twicc/cli/_session_keywords.py:20`, the
helper `artifacts bookmark` and `share` already use) in the `session` group
callback (`_session_default`, `src/twicc/cli/__init__.py:634`), which stores
the resolved id in `ctx.obj`: every subcommand (`session self messages`,
`session parent wait-reply`) gets it. `session_id` is already in
`HOST_BOUND_PARAMS` (`src/twicc/cli/_remote.py:132`), so `--remote` and the
external MCP refuse the keywords, as for every other call site. The comment
above the set (`_remote.py:100-105`) says `session` "treat[s] the id
literally", so rejecting it is "conservative but harmless": it moves to the
list of commands that truly resolve `self` / `parent` (`send-message`,
`update-session`, `topology`), where rejecting is required.

**Help is not blocked by the resolution.** Click runs the group callback
before the subcommand parses its own arguments, so the callback cannot see a
`--help` meant for the subcommand; resolving there would make `session self
messages --help` fail outside a session. The group's `parse_args` (next
section) sees the whole argv: it sets `ctx.meta["twicc.session_help"] = True`
when a token after the id is one of `ctx.help_option_names`. The callback
skips the resolution when that flag is set or `ctx.resilient_parsing` is
true (completion), and keeps the raw value.

**Lookup rule of the bare call.** The bare `session <id>` returns any
`Session` row with that id — the rule `sessions get` applies (`known: true`
for any row) — and exits 1 when there is no row, or when `self` / `parent`
cannot be resolved (a structured `validation_error` on stdout). It applies now: an
error becomes an answer. Today it requires
`created_at` and `user_message_count > 0` (`_get_session`,
`src/twicc/cli/session.py:14-27`). One rule then covers `session <id>`,
`session self` and `whoami`, which must keep answering for a session whose
first user message is not indexed yet (`whoami` reads the row
`resolve_current_session` returns, any row:
`src/twicc/cli/_drop_request/whoami.py:57-76`). The bare call reads metadata,
which exists as soon as the row does. The subcommands that read the
transcript (`content`, `messages`, `agents`, `plan`, `workflows`, `workflow`)
keep `_get_session`.

### `session <id>` flags: before or after the id

The bare call takes `--slim` and `--full`, with the same help constants as
the listings (see "Help texts") and the same mutual exclusion (checked in
the callback, below). Both natural orders work: `session X --full` and
`session --full X`.

**Why a custom group.** A Click group does not intersperse arguments: in
`session X --full` the parser stops at `X`, and `--full` becomes the
subcommand name, "No such command '--full'" (checked with Click 8.3.2 /
Typer 0.24.1). `allow_interspersed_args=True` on the group is not an option:
the group parser would then consume every option it knows anywhere in the
argv, so `session X agents --full` would set the group's `--full` and never
reach `agents`, and a subcommand option the group does not know
(`session X messages --limit 5`) would fail as an unknown group option.

**Mechanism.** `session_app = typer.Typer(..., cls=SessionGroup)`, where
`SessionGroup(TyperGroup)` overrides `parse_args(ctx, args)` before
delegating to `super().parse_args`:

1. Skip the leading tokens that start with `-`. Stop at `--` and change
   nothing: that is the argv `render_argv` builds for the bare MCP / RPC call
   (`["--full", "--", "<id>"]`, `src/twicc/rpc/generator.py:119-148`), which
   Click already parses.
2. The first other token is the id. Take the run of `--slim` / `--full`
   tokens right after it.
3. Run empty: change nothing.
4. Run followed by nothing: move the run before the id (`X --full` →
   `--full X`), so Click parses it as the group's own option.
5. Run followed by a registered subcommand name (`self.commands`): refuse,
   exit 2, with ``Error: --slim / --full apply to `session <id>` alone, not
   to `<subcommand>`.`` (through `emit_error`, so RPC and MCP get the same
   message), unless `ctx.resilient_parsing`.
6. Run followed by anything else: change nothing; Click reports it as today.

The callback runs three checks, in this order, **before** the
`ctx.invoked_subcommand` early return and before the keyword resolution:

1. a subcommand is invoked and `--slim` or `--full` is set
   (`session --full X agents`, where the flag precedes the id): the same
   refusal as step 5, exit 2;
2. both flags set: `emit_error("Error: --slim and --full are mutually
   exclusive.", code=2)`;
3. the keyword resolution (above).

The subcommand's own flags (`session X agents --full`) never reach the
group: the run after the id is empty.

A prototype of this class on a Typer app with the same shape answers:
`session X --full` → bare, `full=True`; `session --full X` → same;
`session X --full agents` → exit 2 with the message; `session X agents --full`
→ `agents` with its own `full=True`; `session --full -- X` → bare,
`full=True`.

The body (`main`, `src/twicc/cli/session.py:68`) becomes
`main(session_id, *, slim=False, full=False)`: it calls
`slim = slim_notice("session", slim, full)` after `django.setup()`, then
builds the payload (see "The CLI session payload").

In the registry the bare `session` route owns the group's options
(`src/twicc/rpc/generator.py:60-77`, `_walk`: a callable group registers its
own options; subcommand routes carry only the group's arguments), so the MCP
tool `session` gains `slim` / `full`, and no subcommand tool gets `slim` /
`full` from the group; `session_agents` keeps its own.

### `whoami`

The command keeps its name: it is an always-loaded MCP tool
(`ALWAYS_LOAD_PATHS`, `src/twicc/mcp/tools.py:53`) and the entry point the
skills teach. It gains `--slim` / `--full`.

1. `if slim and full: emit_error("Error: --slim and --full are mutually
   exclusive.", code=2)` — first, before `django.setup()` and the lookup, so
   the refusal wins outside a session and records no notice.
2. `session = resolve_current_session()`; `None` exits 1 with today's message
   (`src/twicc/cli/whoami.py:51-58`), from a plain terminal as today.
3. `legacy = not slim and not full and not listing_cutover_passed()`, computed
   **before** `slim_notice`, which only returns a boolean.
4. `slim = slim_notice("whoami", slim, full, kind="whoami")` (the notice fires
   only on the legacy call, on the terminal only: `whoami` is local-only over
   `/rpc/`, `src/twicc/cli/_local_only.py:18`, and MCP gets no notice).
5. `legacy`: today's code path, unchanged (its nine-field `process` row, its
   `session` sub-object from `serialize_session`).
6. Otherwise: the same function `session <id>` runs, on the row it already
   has (no second lookup). The result is exactly `session self` with the same
   flag.

| | Before the date, no flag | `--slim` / `--full` now; default from the date |
|---|---|---|
| shape | today's: `session_id`, `title`, `project_id`, `project_directory`, `current_working_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir` (when orchestrated), `agent_settings` (resolved), `session` (full, no process block), `process` (nine-field row) | the payload of `session self`: the session row (reduced or full) with its `process` block inside, and nothing else |
| notice | yes, on the terminal only (no `/rpc/` route, no MCP notice) | none |

Where the removed keys are read from:

| Removed top-level key | Read instead |
|---|---|
| `session_id` | `id` |
| `title` | `title` (the serializer already applies the pending title) |
| `project_id` | `project_id` |
| `project_directory` | `project_directory` |
| `current_working_directory` | `git_directory` |
| `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir` | same names (`orchestration_scratch_dir` is `null` instead of absent outside an orchestration) |
| `agent_settings.<field>` | `<field>`, effective (see below) |
| `session.<field>` | `<field>` (with `--full` for the fields the reduced projection drops) |
| `process` (nine fields) | `process`: `{state}`, or five fields (`id`, `state`, `started_at`, `last_state_change_at`, `pid`) with `--full`; `provider`, `session_id`, `session_title`, `project_id` are the session's own `provider`, `id`, `title`, `project_id` |

So `whoami | jq .process.pid` needs `--full` from the date.

`whoami` is local-only over `/rpc/` (`src/twicc/cli/_local_only.py:18`) but an
MCP tool: its schema gains `slim` / `full`.

Its help is today the docstring of `whoami_cmd` (`src/twicc/cli/whoami.py:11-31`,
registered by `app.command("whoami")(whoami_cmd)`, `src/twicc/cli/__init__.py:2052`),
which describes the legacy object key by key. Prefixing a notice needs an
explicit `help=` on that registration, as `sessions get` does
(`__init__.py:595-606`): `help=WHOAMI_CUTOVER_NOTICE + WHOAMI_HELP`, with
`WHOAMI_HELP = cutover_help(before, after)` in `_output.py`:

- before: "Print details of the session that owns the calling process,
  found by walking the PID ancestry. Without --slim or --full, until {date}:
  a JSON object with session_id, title, project_id, project_directory,
  current_working_directory, artifacts_dir, scratch_dir,
  orchestration_scratch_dir (only inside an orchestration), the resolved
  agent_settings, the full session payload, and the nine-field process row.
  With --slim or --full: the `session self` payload — the session row,
  reduced or in full, with its process block inside. From a plain terminal,
  this command exits 1."
- after: "Print the session that owns the calling process, found by walking
  the PID ancestry: the `session self` payload — the session row, reduced
  by default (--full for every field), with its process block inside. From
  a plain terminal, this command exits 1."

The docstring stays as the code comment.

### The CLI session payload

Every command that emits a session (`sessions`, `sessions get`,
`session agents`, `session <id>`, `whoami` in its new shape) builds it in this
order — the order today's code already uses for the projection and the
process block (`src/twicc/cli/sessions.py:259-267`), with the enrichment
inserted:

1. `serialize_session(session)` — unchanged; it stays query-free and is
   shared with the UI and the WebSocket broadcasts, which this design does not
   touch;
2. **enrichment** — a new CLI-layer function,
   `cli_session_payloads(sessions) -> list[dict]`
   (`src/twicc/cli/_session_payload.py`), batched so a page costs a bounded
   number of queries (below);
3. **projection** — `slim_session` when reduced;
4. **the `process` block** — attached last, as today (`attach_process_blocks`,
   or `None` on every `session agents` row). It comes after the projection
   because `slim_session` keeps only `SESSION_LISTING_FIELDS`, which does not
   hold `process`.

The enrichment sets, on each row:

- `project_directory`: read through the in-memory cache of
  `src/twicc/projects.py` (`_project_directories`, `:75`), with the helper
  below: 15 calls on the same session, or 15 sessions of the same project,
  cost at most one query. `null` only for a session with no project or a
  project with no directory. A project's directory can change:
  `ensure_project_directory` (`projects.py:128-161`) rewrites it when the
  value it receives differs. Every write of `Project.directory` in
  `projects.py` also updates the cache of the process that runs it, after
  commit (`ensure_project_directory`, `_create_or_get_project`,
  `_adopt_directory_sync`). A value cached by a terminal `twicc` lives for
  one call only. One narrow race remains in the backend: a cache-miss read
  taken before that commit can be stored after the `on_commit` update, and
  keeps the old value until the next write for that project. Accepted: the
  window is one transaction, the value only feeds a read-only CLI field, and
  `get_project_directory` shares the same model;
- `artifacts_dir`: **always** `str(get_session_artifacts_dir(session.id))`,
  the place to write, where `serialize_session` gives `null` until an artifact
  exists (`src/twicc/core/serializers.py:263`; the UI keeps that meaning).
  Over MCP / RPC (the backend runs the command), `has_artifacts` says
  whether the folder holds anything; from a terminal it is always `false`
  (`session_has_artifacts`, `src/twicc/artifacts_watcher.py:212-219`), so
  check the folder itself. On a subagent row it
  is the path computed from the row's id, as for any row;
- `scratch_dir`: `str(get_session_scratch_dir(session.id))`;
- `orchestration_scratch_dir`: `(annotations or {}).get("scratch_dir")`, else
  `null` — under its own name, so it is never confused with the session's own
  `scratch_dir` (`annotations` stays as it is);
- the **eight agent settings**, every field of `AgentSettings`
  (`src/twicc/providers/helpers.py:99-124`: `permission_mode`,
  `selected_model`, `effort`, `thinking_enabled`, `claude_in_chrome`,
  `fast_mode`, `context_max`, `question_widget`), as described next.
  `question_widget` is hidden from the frontend
  (`AGENT_SETTINGS_HIDDEN_FROM_FRONTEND`, `helpers.py:166`) and legitimately
  visible to the CLI.

**Effective agent settings.** On a session row (`parent_session_id` null),
each field is
`get_provider_helpers(session.provider).resolve_agent_settings(AgentSettings.from_session(session))`
(`helpers.py:608`), the resolution `whoami` does today
(`src/twicc/cli/whoami.py:60-61`), then `question_widget` `null` → `true`.

- **A stored `null` is common.** On the developer's database on 2026-09-23,
  `selected_model` is `null` on 1 989 of 3 153 Claude Code sessions and 354
  of 1 180 Codex sessions, and `question_widget` on 2 819 and 638. `null`
  means "not chosen: the default applies".
- **The effective value is the default that applies now**: the stored value
  when there is one, else the current global synced default of the field
  (`resolve_agent_settings`, `helpers.py:608-643`). It is not a guarantee of
  what a resume would run with: an agent build also applies
  `enforce_agent_settings_consistency` (`helpers.py:1277`, e.g.
  `src/twicc/core/services/send_message.py:171-172`) and the trust clamp
  (`clamp_permission_mode_for_untrusted`, `src/twicc/core/services/trust.py:293`).
- **`question_widget`** has no synced default in either provider
  (`AGENT_SETTINGS_FIELDS_MAPPING`, `src/twicc/providers/claude_code/constants.py:136-144`,
  `src/twicc/providers/codex/constants.py:79-85`), so resolution leaves a
  stored `null` as `null`. Both agents treat `null` as enabled — Claude Code
  strips `AskUserQuestion` only when it is `False`
  (`src/twicc/providers/claude_code/agent/agent.py:992-1000`), Codex offers
  `request_user_input` when it `is not False`
  (`src/twicc/providers/codex/agent/manager.py:699-704`) — so the enrichment
  reports it as `true`.
- **Where a `null` remains:** a field the provider does not support and does
  not store (Codex: `thinking_enabled`, `claude_in_chrome`; resolution keeps a
  stored value even for an unsupported field, and the developer's database
  holds none), and every field a subagent row stores as `null`.
- **Subagent rows keep their stored values**, unresolved. A subagent has no
  TwiCC settings of its own: it runs inside its parent's process. Its stored
  values are mostly `null` (every field on all 7 188 Claude Code subagents;
  Codex subagents store `context_max` on 565 of 1 012).

The UI keeps the raw values; nothing changes there.

**The project-directory helper.** Two public functions in
`src/twicc/projects.py`, next to `get_project_directory` (`:104-106`):

- `project_directories_cached(project_ids: Iterable[str]) -> dict[str, str | None]`.
  An id that is a key of `_project_directories` is answered from the cache,
  a stored `None` included: no query. The ids absent from the cache are read
  in **one** `Project.objects.filter(id__in=<the misses>).values_list("id",
  "directory")` query, and each row is stored with a single-key assignment,
  `_project_directories[project_id] = directory`. An id with no `Project` row
  is not stored and maps to `None`. The enrichment calls it once per page,
  with the page's distinct project ids.
- `project_directory_cached(project_id: str) -> str | None`: the one-id form,
  `project_directories_cached([project_id])[project_id]`.

The test is key membership (`in`), not `.get()`: `get_project_directory`
returns `None` both for an absent key and for a stored `None`, so it cannot
tell a miss from a hit.

**Safe next to the backend's own writers.** The helper writes single keys
(atomic under the GIL) and never calls `clear()`. The watchers and the
background computes rebuild the cache with `load_project_directories()`
(`:80-89`: `clear()`, then `update()`). A lookup that lands between the two
finds the key absent and reads the database: one more query, the same value.
A stored `None` (a project whose first session has not reported its
directory yet) is replaced by the process's own writers when the directory
becomes known (`ensure_project_directory`, `:147,161`;
`_create_or_get_project`, `:394`; `_adopt_directory_sync`, `:434`). In a terminal
`twicc` process the cache starts empty and lives for one call.

**`sessions get` placeholders.** A placeholder (`known: false`, no `Session`
row) is not enriched, but it must keep the shape of a known row: the tests
`test_batch_lookup_projects_its_placeholders_too`
(`tests/test_cli_pagination_envelope.py:641`) and
`test_an_unknown_id_keeps_the_shape_of_a_known_one`
(`tests/test_cli_session_process_state.py:229`) assert `set(found) ==
set(missing)`. `_build_placeholder_template` (`src/twicc/cli/sessions_get.py:33`)
derives the keys from `serialize_session` alone, which lacks the four keys the
enrichment adds (`project_directory`, `scratch_dir`,
`orchestration_scratch_dir`, `question_widget`). `_session_payload.py` exports
them as `CLI_ENRICHED_KEYS`; the template becomes the serializer's keys plus
`CLI_ENRICHED_KEYS`, all `null`, in both branches (sample row and the
`{"id": None}` fallback). The placeholder's `artifacts_dir` stays `null`: there
is no session to own a folder.

`topology` builds its nodes from `serialize_session` and its own
`TOPOLOGY_SESSION_FIELDS` (`src/twicc/cli/topology.py:17,409`), and is not
enriched: its `--full` nodes are the serializer's payload, as today.

### The reduced projection

`SESSION_LISTING_FIELDS` (`src/twicc/core/serializers.py:129`) gains, in a
group "detail a caller acts on", sixteen fields:

`last_line`, `cwd`, `git_directory`, `project_directory`, `artifacts_dir`,
`scratch_dir`, `orchestration_scratch_dir`, `compacted`, `hybrid`, and the
seven agent settings it lacks: `permission_mode`, `selected_model`,
`effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`,
`question_widget`. (`context_max`, the eighth, is already in the tuple.)

Four of them (`project_directory`, `scratch_dir`,
`orchestration_scratch_dir`, `question_widget`) are not produced by the
serializer: they come from the enrichment, which runs before the projection.
The rule of the reduced projection becomes: **everything except what is
verbose or of no use to a caller**. It still drops `tasks`, `plan_paths`,
`goals`, `layout` (the verbose blobs), `last_started_at`, `last_updated_at`,
`last_stopped_at`, `last_viewed_at`, `mtime` (redundant with
`last_new_content_at`), `self_cost`, `subagents_cost` (detail of
`total_cost`), `slug`, `browser_url`, `compute_version_up_to_date`, and the
`id` / `started_at` / `last_state_change_at` / `pid` of the `process` block.
Every other serializer key is kept. The comment above the tuple
(`serializers.py:113-128`, "the paths `project_id` already encodes, and the
agent-settings bundle") is rewritten to state the new rule.

`topology` keeps its own `TOPOLOGY_SESSION_FIELDS`, unchanged.

`test_the_three_listings_share_one_slim_key_set`
(`tests/test_cli_session_process_state.py:269`) and every test asserting
`set(row) == SESSION_LISTING_FIELDS | …` (e.g.
`test_batch_lookup_takes_the_same_projection`,
`tests/test_cli_pagination_envelope.py:631`) follow the tuple; tests asserting
that a now-kept field is absent are updated.

### Help texts

- `SLIM_HELP` (`_output.py:381`) says the projection drops "the redundant
  timestamps and paths, and the agent-settings bundle. About 60% lighter."
  Both become false. It becomes: "Return a reduced projection of each
  session: every field except the payloads you fetch per session (tasks,
  plan_paths, goals, layout), the redundant timestamps (mtime,
  last_started_at, last_updated_at, last_stopped_at, last_viewed_at), the
  cost breakdown (self_cost, subagents_cost), slug, browser_url and
  compute_version_up_to_date; its process block is {state}." plus the
  existing date clause. **No size figure**: the 60% was measured on the old
  tuple, and the grown one is not re-measured. The same description (paths
  and agent settings dropped) and the same figure are corrected in
  `SKILLS-AND-CLI.md:18`, `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md:80`
  and the guide (`:88`, `:108-114`).
- `FULL_HELP` (`:392`) names "the same fields as `session <id>`" on both
  sides; from the date `session <id>` is reduced too. Its after-date side
  (`:396-398`) also describes the reduced projection as "identity, state,
  cost, and the has_* flags telling you what else is there", false for the
  grown tuple. Before: "Return the full session payload — every field of the
  session. It is the default until {date}; pass --full now to keep it after
  that date. Mutually exclusive with --slim." After: "Return the full
  session payload — every field of the session payload — instead of the default
  reduced projection, which drops the per-session payloads, the redundant
  timestamps, the cost breakdown, slug, browser_url and
  compute_version_up_to_date. Mutually exclusive with --slim."
- `_TOPOLOGY_FULL_LEAD` (`:409`, used by `TOPOLOGY_FULL_HELP`, `:418`) names
  "the fields `session <id>` returns". A topology node is not enriched, so it
  does not carry what `session <id>` returns. It becomes "Emit the full
  serializer payload for every node — agent settings as stored,
  `artifacts_dir` as the serializer reports it (set only once the backend has
  seen an artifact, so always `null` from a terminal), none of the CLI-added
  keys (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`,
  `question_widget`) — minus its `process` block, which sits at
  `nodes[].process` — …". ("every stored session field" would be false:
  `serialize_session`, `src/twicc/core/serializers.py:183-285`, leaves out
  stored columns such as `file_path`, `last_offset`, `compute_version`,
  `question_widget`.) The same
  correction applies to `src/twicc/agent/plugin/twicc/skills/twicc-topology/SKILL.md:44,129`.
- `SLIM_CUTOVER_NOTICE` (`:445`) is prepended to the help of `session` (the
  group help, `session_app`, `src/twicc/cli/__init__.py:625-629`). Its text is
  true for that command.
- `whoami` gets its own texts, because the generic ones are false for it
  (before the date the flagless call returns neither the full nor the reduced
  payload, and `--full` does not keep today's keys):
  - a help prefix `WHOAMI_CUTOVER_NOTICE`, through `cutover_help`: before —
    "DEPRECATION: from {date} this returns the `session self` payload (the
    session row, reduced by default, with its `process` block inside) instead
    of the current object. Pass --slim or --full now to get that shape
    today. "; after — "";
  - the command help `WHOAMI_HELP` (section 2, `whoami`), both sides written
    out there;
  - `WHOAMI_SLIM_HELP` / `WHOAMI_FULL_HELP`: "Return the `session self`
    payload, reduced" / "… in full — every field of the session payload", each
    followed before the date by "Without --slim or --full, this command
    returns its current object until {date}.";
  - the `kind="whoami"` notice in `slim_notice`: "`whoami` returns the
    `session self` payload — the session row with its `process` block inside,
    reduced by default — instead of its current object (`session_id` becomes
    `id`, `agent_settings.<field>` becomes `<field>`,
    `current_working_directory` becomes `git_directory`). Pass --full to get
    that row in full", completed by the template's ", or --slim to get the new
    shape today; after that date --slim is accepted but does nothing."
- The `session_id` argument help of `session` names `self` and `parent`.

## 3. Reading several objects: `items`

From the date:

| Command | Today | From the date |
|---|---|---|
| `sessions get <ids>` | `[ {…}, … ]` | `{"items": [ {…}, … ]}` |
| `projects get <ids>` | `[ … ]` | `{"items": [ … ]}` |
| `workspaces get <ids>` | `[ … ]` | `{"items": [ … ]}` |
| `peers` | `{"peers": [ … ]}` | `{"items": [ … ]}` |

**No `pagination` key.** A batch lookup returns exactly one entry per id asked,
in input order; `peers` returns every approved peer. There is no page, no
`limit`, no `has_more` to state.

**Opting in before the date:** each of the four gains `--paginated`, the flag
that already means "the new envelope" on the listings. On these commands it
wraps the result in `{"items": …}` and adds no `pagination`; from the date it
is accepted and does nothing, as on the listings. Its help is a new constant,
`LOOKUP_ENVELOPE_HELP`, through `cutover_help`:

- before: "Wrap the result in {items} — no pagination: one entry per id asked.
  Off by default until {date}, when it becomes the only shape and this flag an
  accepted no-op."
- after: "Accepted and ignored: the result is always wrapped in {items}."

(For `peers`: "every approved peer" in place of "one entry per id asked".)

**Notice:** `pagination_notice` gains two `shape` values, rendered without the
page-size clause whatever `default_limit` is:

- `shape="lookup"`: "`{command}` returns {"items": [...]} instead of a bare
  array".
- `shape="peers"`: "`peers` returns its list under `items` instead of
  `peers`".

Each body calls `paginated = pagination_notice("<command>", paginated,
default_limit=None, shape=…)` after `django.setup()` (`sessions get`,
`projects get`, `peers`) or first (`workspaces get`, which never sets Django
up: `src/twicc/cli/workspaces_get.py`), then emits `{"items": results}` when
`paginated`, else the current shape. `emit_list` is not used: it would add
`pagination`.

**Two notices on a flagless `sessions get`**, before the date: the lookup
one, then the slim one. The body calls `pagination_notice` before
`slim_notice`, the order `sessions` (`src/twicc/cli/sessions.py:229-230`) and
`session agents` (`src/twicc/cli/session.py:376-377`) already use.

Command help prefix, through `cutover_help`, emptied after the date:

- `LOOKUP_CUTOVER_NOTICE`, on `sessions get`, `projects get`,
  `workspaces get`: "DEPRECATION: from {date} this returns {items} instead
  of a bare array. Pass --paginated now to get that shape today. ";
- `PEERS_CUTOVER_NOTICE`, its `peers` variant: "DEPRECATION: from {date}
  this returns its list under {items} instead of {peers}. Pass --paginated
  now to get that shape today. ".

On `sessions get` the help reads `LOOKUP_CUTOVER_NOTICE +
SLIM_CUTOVER_NOTICE + <text>`, the order of `sessions`
(`CUTOVER_NOTICE + SLIM_CUTOVER_NOTICE`, `src/twicc/cli/__init__.py:212`).
`sessions get` already has an explicit `help=` (`src/twicc/cli/__init__.py:595-606`).
The three others take their help from a docstring: `_projects_get`
(`@projects_app.command(name="get")`, `__init__.py:102-103`), `_workspaces_get`
(`__init__.py:173-174`) and `peers_cmd` (`src/twicc/cli/peers.py:6-14`,
registered by `app.command(name="peers")(peers_cmd)`, `__init__.py:2096`).
Each registration gains an explicit `help=<notice> + <text of the docstring>`;
a prefix cannot be prepended to a docstring. `peers_cmd` takes no parameter
today: it gains `paginated: bool = typer.Option(False, "--paginated", …)`.

The in-repo readers of these four outputs are listed in "In-repo consumers".

## 4. Acting on several ids: `sessions stop`

`sessions stop` (unreleased) returns:

```json
{
  "summary": {"total": 3, "succeeded": 2, "failed": 1, "all_succeeded": false},
  "results": {"<session_id>": {…}, "<session_id>": {…}}
}
```

- `results` maps each targeted id, in selection order, to today's per-id
  entry, unchanged (`src/twicc/cli/_stop_batch.py:79-88`): `session_id`,
  `session_known`, `status`, `request_uuid`, `provider`, `session_title`,
  `project_id`, `error`.
- `summary` uses the keys and the formula of `twicc.cli._batch_runner`
  (`src/twicc/cli/_batch_runner.py:266-276`): `total` (entries), `succeeded`
  (`status == "stopped"`), `failed` (every other status: `rejected`,
  `failed`, `timeout`, `skipped_*`, `skipped_self` included),
  `all_succeeded` (`failed == 0`: unlike the `total > 0 and failed == 0`
  of `_batch_runner.py:266-276`, an empty selection answers `true`, below).
- An empty selection returns `{"summary": {"total": 0, "succeeded": 0,
  "failed": 0, "all_succeeded": true}, "results": {}}` (today `[]`,
  `src/twicc/cli/sessions_stop.py:124-126`). `true`, as `_batch_runner`
  answers for `send-messages` / `update-sessions` on an empty set
  (`_batch_runner.py:174-181`): nothing was asked, nothing failed, and a
  script testing `all_succeeded` after "stop everything running" must not
  read an idle instance as a failure. (`sessions wait-reply` answers
  `all_replied: false` on an empty selection: it reports answers, and none
  came.)
- The exit code does not change: `0` whenever the command ran (it "never fails
  as a whole"), unlike `_batch_runner`'s exit `6`.

### A bare call is refused

`sessions stop` with no id and no filter exits 1, as `sessions wait-reply`
does (`src/twicc/cli/sessions_wait_reply.py:100-109`):

```
Error: sessions stop needs at least one session id or one filter — a bare call would stop every running session.
```

The filters are the ones `has_filter` already lists
(`src/twicc/cli/sessions_stop.py:94-97`: `--project`, `--workspace`,
`--provider`, `--state`, the four filiation scopes, `--annotation`). The check
tests the raw `session_ids`, so it runs with the other argument checks —
after the `--state dead` refusal and `reject_conflicting_scopes`
(`:62-72`), **before** `transport.ensure_server_available()` (`:74-77`):
a bad call is named as a bad call even with no backend. "Stop everything
running" stays possible, written on purpose: `sessions stop --state
starting --state assistant_turn --state awaiting_user_input --state
user_turn`.

### The caller is never stopped

`sessions stop` never stops the session that runs it — not when a filter
selects it (`--spawn-tree self`, `--spawned-by parent`, `--state …`,
`--annotation …`), not when its id is named, `self` included. It is
reported in `results` with the status **`skipped_self`**, `request_uuid`
`null`, and `error`: "The calling session is never stopped by `sessions
stop`; use `session self stop`." (`session self stop` works through the
keyword resolution of section 2; see below.) No drop request is submitted
for it.

- **The caller** is `resolve_current_session()`
  (`src/twicc/cli/_drop_request/whoami.py:57-103`), called once after
  `django.setup()`: the MCP identity when set (`forced_session_id`, `:74-76`),
  else the PID ancestry (`:78-91`). It is `None` from a plain terminal, over
  `/rpc/` (the backend's own ancestry holds no agent), and for an external
  MCP caller (`:71-73`): nothing is excluded then. A caller with no `Session`
  row yet also resolves to `None`; no filter can select it (every filter
  reads `Session`), and its id comes from `whoami`, which needs the row.
- **Where:** `stop_session_ids` (`src/twicc/cli/_stop_batch.py:32`) gains a
  keyword `caller_id: str | None = None`. In the per-id loop (`:76-105`), the
  entry of `caller_id` gets `skipped_self` before `lookup_session`, and the
  loop continues. `sessions stop` passes the caller's id; `processes stop`
  passes nothing and keeps its behaviour until its removal.
- **Counted as `failed`.** The formula stays "`succeeded` ⇔ `stopped`", like
  every other `skipped_*`. `all_succeeded` answers "is every targeted agent
  stopped now?", and the caller's agent is still running: `true` would be
  false. A selection that must not include the caller already exists:
  `--descendants self` and `--siblings self` exclude their target
  (`twicc-sessions/SKILL.md:89-90`), so `skipped_self` appears only when the
  call itself selected the caller.
- **The singular stops the caller.** `session self stop` fails today:
  `stop_cmd` looks the literal `"self"` up (`lookup_session`,
  `src/twicc/cli/process_stop.py:48-52`) and exits 1. The keyword resolution
  of section 2 (the `session` group callback stores the resolved id in
  `ctx.obj`, which `_session_stop` passes to `stop_cmd`,
  `src/twicc/cli/__init__.py:829`) is what makes it work. The `skipped_self`
  error names `session self stop`, so both land together.

No notice and no date: the command has never been released. The retired
`processes stop` keeps its own array and its own behaviour until its code is
removed.

## 5. The default wait cursor

`session <id> wait-reply` and `sessions wait-reply`, when neither `--from` nor
`--since` is given, start **strictly after the session's last user message**:
the `line_num` of its last `SessionItem` of kind `ItemKind.USER_MESSAGE`, or
`0` when it has none. A session with no indexed row yet keeps cursor `0` (P1
of the process-retirement design). A session whose compute is not current
keeps the old default, its current `last_line` (see "The compute window").
`--from` and `--since` are unchanged and keep their meaning.

The question a wait answers is almost always "what did the session say back
to the last thing it was told". The last user message is where that answer
starts.

### What a user message is

| Provider | `USER_MESSAGE` | Not a user message |
|---|---|---|
| Claude Code (`compute_item_kind`, `src/twicc/providers/claude_code/compute.py:1583`) | a `user` entry with visible text or content (`:1670-1672`): a prompt typed in the UI, a message sent by `send-message` / `send-messages` (sender header included) **while the agent is idle**; a slash command other than `/clear`, `/model`, `/effort`, `/fast` (`:1632-1638`, `_SYSTEM_SLASH_COMMANDS` `:76`) | a message delivered **while the agent is busy**: stored as an `attachment` of type `queued_command`, and every `attachment` entry is `SYSTEM` (`:1605-1618`); meta entries (`isMeta`, `:1640-1642`), including a `CronCreate` job firing; entries starting with `<twicc-` (`_SYSTEM_XML_PREFIXES`, `:66-69`, `_is_system_xml_content`, `:348`), including TwiCC's cron restart and renewal messages (`src/twicc/providers/claude_code/cron_restart.py:516-598`, delivered by `send_to_session`); tool results, task notifications, compaction summaries |
| Codex (`compute_item_kind`, `src/twicc/providers/codex/compute.py:3249`) | a visible `UserMessage` item (`:3271-3274`), busy or idle; a subagent's `NEW_TASK` envelope (`:3309-3310`) | TwiCC's internal `<twicc-resume>` instruction (`:3272-3273`); every other item |

The `queued_command` and cron classifications were checked on the
developer's database: busy-time messages are `system` items carrying
`attachment.type == "queued_command"`, and a `CronCreate` firing is a `user`
entry with `isMeta: true`, classified `system`.

No new parsing: the cursor counts `USER_MESSAGE` only.

### Cases

| Situation | Old default (current last line) | New default (after the last user message) |
|---|---|---|
| child just spawned, not answered yet | waits ✓ | waits ✓ |
| child just spawned, already answered | misses it, `ended` ✗ | returns it ✓ |
| session re-messaged, not answered yet | waits ✓ | waits ✓ |
| session re-messaged, already answered — Codex, or Claude idle when the message arrived | misses it, `ended` ✗ | returns it ✓ |
| Claude re-messaged while busy, then a wait | misses an answer already there; else the first final message past the call | the first final message past the previous user message: the running turn's closing message (see "Known limit") |
| session idle, answer already there, nobody messaged it | waits, then `ended` | returns that answer at once |
| session idle, its last turn ended on a surfaced API error | waits, then `ended` (exit 5 on the singular) | `provider_error` at once (exit 5 on the singular) |
| a pending request (question, approval) | `awaiting_user_input` at once | unchanged |
| session whose compute is not current (after a backend restart, until the background compute reaches it; after a compute-version bump; a Codex legacy rollout awaiting migration) | current last line | current last line (the old default, kept on purpose) |

The API-error row follows from the loop: it scans `ASSISTANT_MESSAGE` and
`API_ERROR` items past the cursor, and a surfaced error (`isApiErrorMessage`)
ends the wait as `provider_error` when no final message precedes it
(`src/twicc/cli/_wait_reply.py:336-380`). A turn that recovered after the
error still returns `replied`.

**Known limit (Claude Code), rare and accepted.** It is not introduced by
this design: it affects every wait for an answer on a Claude session that is
busy when the message arrives — `send-message --wait-reply` and
`send-messages --wait-reply` included, `--from` and the new default alike.

- **Mechanism.** A message sent while the agent runs a turn is queued by
  Claude; TwiCC keeps the agent in `ASSISTANT_TURN`
  (`src/twicc/providers/claude_code/agent/agent.py:1717-1719`). The
  transcript records it as a `queued_command` attachment, classified
  `SYSTEM`, not as a user message. Every wait returns the **first** final
  message past its cursor (`src/twicc/cli/_wait_reply.py:355-359`):
  `--wait-reply` places its cursor at the `last_line` read when the agent
  takes the message (`src/twicc/core/services/send_message.py:186-201`), and
  the new default leaves the anchor on the previous user message. Either
  way, the first final message is the running turn's closing message.
- **Usually right.** Claude usually reads the queued message after its next
  tool result, inside the running turn. The turn's closing message then
  covers it, and the wait returns the right answer.
- **The rare wrong case.** When the running turn's next text output is its
  final one, the queued message runs as a turn of its own afterwards. The
  wait returns the running turn's closing message, which does not answer the
  queued message.
- **Why it is accepted.** In an orchestration, a session usually messages
  another once that one has finished, not while it works. No fix is
  planned; new parsing of `queued_command` attachments is out of scope.

The skills and the guide state this limit once, in these terms. No help text
states that a wait escapes it.

**The indexing-lag case**, accepted by decision: a script that runs
`send-message` **without** `--wait-reply`, then `session <id> wait-reply`
**without** `--from`, within the watcher's indexing lag (seconds). The new
message is not indexed yet, the "last user message" is the previous one, and
the wait returns the **previous** answer as `replied`. The old default handled
it. The usage rule forbids that sequence: use `--wait-reply` on the send, or
pass `--from <last_line>` from the send result.

**Other edges, accepted:**

- A turn with no user message before it — a `CronCreate` firing, TwiCC's cron
  restart or renewal message, a Monitor or task-notification wake-up, a
  subagent finishing, a Codex `<twicc-resume>`: the default returns the first
  final message after the last real user message, which is that message's
  answer when it exists, not the new turn's.
- A slash command run after the answer (`/cost`, `/context`) is a user
  message: the anchor moves after it, no answer follows, and the wait ends
  `ended` after its flush window.

### The compute window

The anchor reads `kind`, and `kind` is written by the compute. Two paths
write items without it:

- The initial sync at backend start inserts raw items (`SessionItem` with
  `line_num` and `content` only: `src/twicc/providers/db_writer.py:2430-2434`
  for a new session, `:2448-2452` for new lines of a known one). A session
  that receives new lines gets its `compute_version` reset to `NULL`
  (`:2460-2462`, driven by `reset_compute_version` in
  `src/twicc/providers/claude_code/initial_sync.py:364-367` and
  `src/twicc/providers/codex/initial_sync.py:417-420`); a session the sync
  creates has none (`compute_version` defaults to `NULL`,
  `src/twicc/core/models.py:397`).
- The background compute then fills `kind` session by session, and sets
  `compute_version` when it is done.

In between, the last `USER_MESSAGE` row is the last one the previous compute
saw. The new default would anchor on an older user message and return an old
answer as `replied`. So the default falls back to the session's current
`last_line` — the old default — whenever the session's compute is not
current: `not session_compute_ready(session)`
(`src/twicc/core/serializers.py:14-17`, `compute_version` equal to the
provider's `current_compute_version`). This covers the restart window, a
compute-version bump after an upgrade (every session, until the background
compute reaches it), and a Codex session created from a legacy rollout
(`compute_ready_on_create` false, `src/twicc/providers/codex/sessions_watcher.py:179`).
In that window, a child that answered before the wait is missed and reports
`ended`, as today: a missed answer, never a wrong one.

**Not `kind IS NULL`.** A `NULL` kind is also a computed value:
`compute_item_kind` returns `None` for items that are neither message nor
tool call (`src/twicc/providers/compute_base.py:923`). On the developer's
database on 2026-09-23, every session's compute is current, yet 190 520
items have a `NULL` kind — Codex `function_call_output`,
`custom_tool_call_output` and `item_completed` items, Claude `cost-state`
lines — and 1 984 Codex sessions have one past their last user message. A
"no `NULL` kind past the anchor" condition would turn the new default off on
nearly every Codex session. Compute readiness alone is exact: the initial
sync never adds items to a session without leaving its compute not current.

**Implementation:** one helper in `src/twicc/cli/_wait_reply.py`,
`default_wait_cursors(sessions) -> dict[str, int]`, taking `Session` rows:

- a row whose compute is not current → `session.last_line`;
- the others → one query,
  `SessionItem.objects.filter(session_id__in=ids, kind=ItemKind.USER_MESSAGE)`
  grouped by `session_id` with `Max("line_num")`, on the index
  `idx_session_kind_line` (`src/twicc/core/models.py:798-801`); `0` for an id
  with no `USER_MESSAGE` row.

The singular (`src/twicc/cli/session.py:636`) and the plural
(`src/twicc/cli/sessions_wait_reply.py:162`) use it in place of
`session.last_line`. Both already hold full rows (`session.py:624-626`,
`sessions_wait_reply.py:133`), so the readiness check costs no query.

## 6. Texts

### The usage rule (decision 6)

The rule: a send without `--wait-reply` is never followed by a wait without
a cursor. After `send-message`: `session <id> wait-reply --from <last_line>`,
with the `last_line` of the send result. After `send-messages`:
`sessions wait-reply --since <an instant taken before the send>` (the plural
has no `--from`), or one `session <id> wait-reply --from <last_line>` per id.
The simplest form stays `--wait-reply` on the send.

Stated in:

- the help of `send-message` (its `--wait-reply` option): "To wait for the
  answer, pass --wait-reply. A separate wait must pass --from with the
  `last_line` this command returns.";
- the help of `send-messages` (its `--wait-reply` option): "To wait for the
  answers, pass --wait-reply. A separate wait must pass `sessions wait-reply`
  an instant taken before this command (--since), or `session <id>
  wait-reply` the `last_line` of each entry (--from).";
- the help of `session <id> wait-reply` (`--from` and the command help) and
  of `sessions wait-reply` (`--since` and the command help);
- the skills `twicc-send-message`, `twicc-send-messages`, `twicc-session`,
  `twicc-sessions`, and `twicc-orchestration/SKILL.md` where it describes
  waiting;
- `SKILLS-AND-CLI.md` in the same places.

The Claude busy-message limit (section 5) is stated in the skills
`twicc-session`, `twicc-sessions`, `twicc-send-message`,
`twicc-send-messages`, in `SKILLS-AND-CLI.md` next to the waits, and in the
guide — as a rare, accepted limit of every answer-wait, `--wait-reply`
included. No help text mentions it, and none is added that claims a wait
escapes it. The existing texts saying that the `--wait-reply` cursor keeps
"the previous turn's closing message" from answering
(`src/twicc/cli/send_message/command.py:61,123`,
`src/twicc/cli/send_messages.py:192`, `twicc-send-message/SKILL.md:149`,
`twicc-send-messages/SKILL.md:111`, `SKILLS-AND-CLI.md:292`) stay: they speak
of a finished turn, and stay true. The limit concerns the turn still running
when the message arrives.

Each added `--wait-reply` mention in a document guarded by
`tests/test_session_wait_documentation.py` raises its `SANCTIONED` count
(`:53-57`, `twicc-session/SKILL.md`, `SKILLS-AND-CLI.md`, `session wait-reply
--help`) deliberately, as that test asks.

### The cursor, everywhere it is described

Every text describing the default cursor as "the session's current last line"
/ "tell me the next thing it says" is rewritten to "after the last user
message". Every `--since 2000-01-01` recipe for children never messaged
since their spawn becomes the plain call (no `--since`). Every sentence
saying an answer given before the wait is missed without `--since` goes.

**Resuming, one sentence everywhere.** Every text on resuming a timed-out
wait ("repeat the same call, same `--since`", "re-running re-reads each
current last line") becomes, word for word or adapted to its grammar only:
"Re-running resumes, except for a session whose compute is not current;
`--since <the instant the batch started>` resumes in every case." Below,
**R** names that sentence.

**Compute-window qualifier.** A target below that says, with no condition,
that the default returns an answer already given (`twicc-session/SKILL.md:247`,
`twicc-orchestration/SKILL.md:95`, `twicc-process/SKILL.md:16`,
`twicc-orchestration/control-cookbook.md:54-58`,
`twicc-orchestration/patterns/worker-pool.md`, the reference docs, the
guide's "Waiting" section) is false while the session's compute is not
current (section 5, "The compute window"). Once per document, next to the
first such sentence: "(except while a session's compute is not current —
e.g. right after a TwiCC restart: then pass `--since` an instant before the
spawn or the send)".

Found with `grep -rn "2000-01-01\|last line\|already given\|same \`--since\`\|--since <INSTANT>"`
over `src/twicc/cli`, the skills (`src/twicc/agent/plugin/twicc/skills/`),
`SKILLS-AND-CLI.md` and `ORCHESTRATION.md`.

Code (help texts and docstrings):

- `src/twicc/cli/__init__.py`: the plural's `--since` help (`:454-455`,
  "instead of above its current last line"), its docstring (`:550-552`, and
  the "Resuming a timed-out batch" paragraph `:570-575`, which becomes R
  followed by the per-session alternative, each block's `since_line_num`
  handed to `session <ID> wait-reply --from`), the singular's
  `--from` help (`:723-724`) and docstring (`:787`);
- `src/twicc/cli/session.py:586-591` (`wait_reply` docstring) and
  `src/twicc/cli/sessions_wait_reply.py:8-13` (module docstring, "The
  cursors").

Skills (paths under `src/twicc/agent/plugin/twicc/skills/`):

| Place | Today | Target |
|---|---|---|
| `twicc-session/SKILL.md:14` | "omitted, it is the session's current last line" | "omitted, the wait starts after the session's last user message" |
| `twicc-session/SKILL.md:247` | "the cursor becomes the session's current last line, "tell me the next thing it says"" | "the cursor goes after the session's last user message: an answer already given is returned" |
| `twicc-sessions/SKILL.md:32` | "Each session starts above its own last line" | "Each session starts after its own last user message" |
| `twicc-sessions/SKILL.md:36` | resuming: "Without it, re-running re-reads each session's current last line and silently skips an answer that arrived in between" | R; the per-session alternative (`since_line_num` handed to `session <ID> wait-reply --from`) stays |
| `twicc-create-session/SKILL.md:228` | `sessions wait-reply <SESSION_ID>... --since 2000-01-01`, "never pass today's date …" | `sessions wait-reply <SESSION_ID>...`; the `--since` sentence goes |
| `twicc-process/SKILL.md:16` | "(or `--since 2000-01-01`, or an answer already given is missed)" | "(or `session <id> wait-reply`, which returns an answer already given)" |
| `twicc-processes/SKILL.md:17` | replacement `sessions wait-reply <ids> --since <instant>`; "repeats the call with the same `--since`" | `sessions wait-reply <ids>`; "repeats the call" followed by R |
| `twicc-send-messages/SKILL.md:119` | "without `--since` it re-reads each current last line and skips what arrived in between"; the recipe `sessions wait-reply --since <the instant the batch started>` (no id, no filter: refused as a bare call, `sessions_wait_reply.py:104-109`) | the clause "— without `--since` … in between" becomes R; the recipe names the ids: `sessions wait-reply <SESSION_ID>... --since <the instant the batch started>` |
| `twicc-send-messages/SKILL.md:131` | `sessions wait-reply <SESSION_ID>... --since <INSTANT>` — "await a batch messaged without `--wait-reply`" | unchanged recipe, text: "an instant taken before the send" (the usage rule) |
| `twicc-orchestration/SKILL.md:91` | `--since 2000-01-01` in the barrier | removed |
| `twicc-orchestration/SKILL.md:95` | "`--since 2000-01-01` fits children never messaged … an answer already given is missed. For a later round, … the instant captured before that send" | "The wait starts after each child's last user message, so an answer already given is returned. For a later round, use `send-messages --wait-reply`, or pass `--since` the instant captured before that send." |
| `twicc-orchestration/SKILL.md:100` | "repeat the same call, same `--since`" | "repeat the same call", then R |
| `twicc-orchestration/SKILL.md:129` | `sessions wait-reply <REVIEW_IDS>... --since 2000-01-01` | `sessions wait-reply <REVIEW_IDS>...` |
| `twicc-orchestration/SKILL.md:172` | `--since 2000-01-01` | removed |
| `twicc-orchestration/SKILL.md:210` | `sessions wait-reply <ID>... --since <INSTANT>` | `sessions wait-reply <ID>...` |
| `twicc-orchestration/control-cookbook.md:13,38,50` | `--since 2000-01-01` in the three recipes | removed |
| `twicc-orchestration/control-cookbook.md:16-18` | "`--since 2000-01-01` fits children never messaged … a future instant misses an answer already given." | "For a later round, pass `--since` the instant captured before that send." |
| `twicc-orchestration/control-cookbook.md:20-21` | "Repeat the same call, same `--since`" | "Repeat the same call", then R |
| `twicc-orchestration/control-cookbook.md:54-58` | "the same call with the same `--since` returns the blocked session at once. A winner that finished before the call is seen only with `--since`." | "the same call returns the blocked session at once." The last sentence goes: the default returns a winner that finished before the call. |
| `twicc-orchestration/examples/large-migration.md:14`, `hard-bug-race.md:10`, `parallel-feature-integration.md:15`, `pr-review.md:13` | `--since 2000-01-01` | removed |
| `twicc-orchestration/examples/hard-bug-race.md:16`, `patterns/speculative-race.md:23` | "wait again with the same `--since`" | "wait again", then R |
| `twicc-orchestration/patterns/divide-and-conquer.md:27`, `scatter-gather.md:19`, `speculative-race.md:18`, `phase-gated-fanout.md:19`, `composing.md:21`, `single-writer-integration.md:21`, `pipeline.md:19` | `--since 2000-01-01` | removed |
| `twicc-orchestration/patterns/worker-pool.md:19-24` | `--since <INSTANT>`; "`--since` is `2000-01-01` for fresh workers. For reused workers, capture the instant …" | `sessions wait-reply <WAVE_ID>...`; "Fresh workers need no `--since`. For reused workers, capture the instant before sending the next task and pass it as `--since`, or send it with `send-message --wait-reply`." |
| `twicc-orchestration-leader/SKILL.md:48`, `twicc-orchestration-manager/SKILL.md:34` | `--since 2000-01-01` | removed |
| `twicc-orchestration-leader/SKILL.md:52`, `twicc-orchestration-manager/SKILL.md:37` | "Repeat the same call, same `--since`, …; the other outcomes and the `--since` choice are in `twicc-orchestration`" | "Repeat the same call, …", then R; "the other outcomes and the later-round cursor are in `twicc-orchestration`" |
| `twicc-orchestration-leader/SKILL.md:71`, `twicc-orchestration-manager/SKILL.md:54` | `sessions wait-reply <ID>... --since <INSTANT>` | `sessions wait-reply <ID>...` |

Reference docs:

- `SKILLS-AND-CLI.md:249` ("Each session starts above its own last line")
  and `:259` ("Omitted, the cursor is the session's current last line ("tell
  me the next thing it says")") → "after the session's last user message";
- `ORCHESTRATION.md:58` (`sessions wait-reply <REVIEW_IDS> --since
  2000-01-01`) → without `--since`; `ORCHESTRATION.md:89` (`--since
  2000-01-01 --wait-timeout 300`; "same `--since`, only those ids") → without
  `--since`; "the same call, only those ids", then R.

`twicc-session/SKILL.md:262` (`--since 2026-09-19T05:38:20+00:00`, "Anything
said after that moment") is a correct `--since` example: unchanged.

Tests that pin this wording:

- `test_the_two_commands_describe_one_cursor_rule`
  (`tests/test_sessions_wait_reply_documentation.py:178`) pins the `--since`
  help of both commands: "strictly after" present, "at or before" absent. It
  pins the `--since` rule, not the default cursor; the rewritten `--since`
  help keeps "strictly after".
- `tests/test_session_wait_documentation.py` pins, in its four `RULE_SOURCES`
  (`:419-420`), the `--since` anchors "strictly after" and "not a boundary",
  and forbids its `RETIRED` phrases; the rewrite keeps both anchors.

`--since` is described precisely wherever it appears: an **ISO 8601 instant**
(`2026-09-30T14:05:00+00:00`, or without offset, read as UTC); a bare date is
accepted and means midnight UTC. It and `--from` exist on
`session <id> wait-reply` (both) and `sessions wait-reply` (`--since` only) —
not on `create-session`, `send-message` or `send-messages`, whose
`--wait-reply` reads the cursor server-side.

### The migration guide

`frontend/public/help/cli-rpc-migration-2026-10-01.md` is updated:

- the introduction: "three changes" (`:5`) loses its count ("these
  changes"): the lookup envelope and the `session <id>` / `whoami` payload
  join the three earlier ones; "(a bare `sessions` call gets two)" (`:34`)
  becomes "(a flagless `sessions`, `sessions get` or `session agents` call
  gets two)";
- section 1: the page-size table rewritten (20 everywhere); `share` 50 → 20
  **now**; the envelope example's `"limit": 50` → 20; "and they page at
  **50**" (`:45`), "the 50-item page" (`:70`), "They now return 50" (`:76`)
  and checklist item 3 (`:296`) → 20; the "Unchanged: the batch lookups keep
  returning a bare array" line (`:82-83`) replaced by the new section below;
- section 2: "The commands that return **one** session (`session <id>`,
  `whoami`) are unchanged" (`:89-90`) and "One session in detail: `twicc
  session <id>` still returns everything" (`:124`) replaced: `session <id>`
  and `whoami` join the reduced commands, with their flags, `session self` /
  `parent`, and the `whoami` key mapping; "about 60% lighter" (`:88`) goes;
  the kept / dropped field lists (`:99-114`) follow the grown projection;
- a new section: batch lookups and `peers` → `items`, opt-in `--paginated`;
- a section "Changed now, without a notice", which states the rule of "What
  is released" and lists the owner's three exceptions — `share` 20;
  `artifacts_dir` always the path; agent settings effective (with the
  subagent exception) — then the additive changes: the keys
  `project_directory`, `scratch_dir`, `orchestration_scratch_dir`,
  `question_widget`; `session self` / `parent`; `session <id>` answering for
  a row with no user message; `--slim` / `--full` on `session <id>` and
  `whoami`;
- section 3, "Reading state": "`session <id>` exits `1` on both" (`:153-157`)
  becomes "exits `1` when there is no row"; "`session <id>` always carries
  them" (`:169-170`) becomes "with `--full`";
- the "Waiting" section: `--wait-reply` first; the default cursor "after the
  last user message", and the current last line while a session's compute
  is not current; resuming after a timeout in the terms of R; `--from` /
  `--since` after a send without `--wait-reply`, and the commands that take
  them; `--since` as
  a date-time; the `--since 2000-01-01` recipe (`:212-214`, `:227-236`)
  removed; "repeat the call with the same `--since`" (`:263-264`, "Longer
  than 300 s") becomes "repeat the call", then R; the usage rule; the Claude busy-message limit, in the terms of
  section 5 (rare, accepted, every answer-wait including `--wait-reply`);
- the "Stopping" section: "A bare `sessions stop` stops every running
  session, the caller included" (`:185-187`) becomes "A bare `sessions stop`
  is refused, and `sessions stop` never stops the caller (`skipped_self`);
  `session self stop` does"; the output is `{summary, results}`;
- the checklist follows (items 2, 3, 5, and a new item for the lookups,
  `peers` and `whoami`).

`TWICC_LISTING_CUTOVER` appears in no user doc, skill or help (section 8).

### Skills, reference docs, plugin

Every skill and reference doc naming the changed defaults, shapes or cursor is
updated — the list is in "In-repo consumers". `plugin.json`
(`src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, `0.102.1`) takes
a minor bump. `docs/plans/*` other than this one are not edited.

## 7. In-repo consumers

Found with `grep` over `src/`, `frontend/src/`, `frontend/public/help/`,
`tests/`, the skills, `SKILLS-AND-CLI.md` and `ORCHESTRATION.md`. The built
copies under `src/twicc/static/` follow the build and are not edited.

### `whoami` output

- `src/twicc/agent/plugin/twicc/skills/twicc-whoami/SKILL.md:8` (the key
  list) and `:45-49` (`jq -r .session_id`, `.agent_settings.selected_model`,
  `.process.pid`, `.artifacts_dir`, `.scratch_dir` → `whoami --slim | jq -r .id`,
  `whoami --slim | jq -r .selected_model`, `whoami --full | jq -r .process.pid`,
  unchanged paths). Every recipe that reads a moved key passes `--slim` or
  `--full`: before the date a flagless `whoami` keeps `session_id` /
  `agent_settings`, so `.id` reads `null` there. The
  skill documents `--slim` / `--full` (usable now), the date, the notice and
  the key mapping of section 2.
- `SKILLS-AND-CLI.md:43` and `:90-92` (the key list); `:34` claims `whoami`
  returns `twicc_executable`, which `src/twicc/cli/whoami.py` never emitted.
  The pointer stays, on the right command: "`twicc info` also returns the
  canonical invocation under `twicc_executable`"
  (`src/twicc/cli/info/command.py:117`).
- `twicc-orchestration/SKILL.md:213` and `twicc-orchestration-worker/SKILL.md:56`
  ("your own session id, settings, and permission mode"): still true.
- `src/twicc/agent/system_prompt.py:64,154,218` point at the skill, not at key
  names: unchanged.
- Tests: `tests/test_mcp_server.py:33-51` reads `result["session"]["id"]`
  (the legacy shape: it breaks on the real date; it pins the clock before the
  date, and a twin asserts `result["id"]` after it); its session has no
  `created_at` and no user message, which the new lookup rule serves;
  `tests/test_process_commands_removal.py:342-358`
  (`test_whoami_still_serves_its_nine_field_process_row`, under the `after`
  fixture) asserts the nine-field row after the date: it becomes "after the
  date, `whoami --full` carries the five-field block and the session's own
  `provider` / `id` / `title` / `project_id`", and a before-the-date twin keeps
  the nine fields; it patches `resolve_live_twicc_or_exit`, which the new path
  does not call (it resolves through `resolve_listing_twicc_pid`).

### `session <id>` shape and lookup

**Where `session self` / `parent` and `--slim` / `--full` on `session` are
documented:** `twicc-session/SKILL.md` (the argument hint `:4`, the list
`:11`, the "Default — session metadata" section `:48-54`) and the
`SKILLS-AND-CLI.md` entry "Default (no sub-command)" (`:255`), and the
keyword lists of `SKILLS-AND-CLI.md:40-41` ("Accepted by …"), which gain
`session <ID>` and its subcommands, and also `sessions stop` /
`sessions wait-reply` as explicit ids (they already resolve both keywords
through `resolve_explicit_ids`, `_session_selection.py:48-56`;
`sessions stop self` answers `skipped_self`). `whoami`'s
flags: `twicc-whoami/SKILL.md` (below) and `SKILLS-AND-CLI.md:43,90-92`.

`twicc-session/SKILL.md` (paths under `src/twicc/agent/plugin/twicc/skills/`):

| Place | Today | Target |
|---|---|---|
| `:4` argument hint | `<session_id> [content\|…]` | `<session_id\|self\|parent> [--slim\|--full] [content\|…]` |
| `:11` | "Default — full session metadata, `process` block included" | "Default — the session row, `process` block included: full until 2026-10-01, reduced from that date; `--full` / `--slim` choose. `self` / `parent` name your own session / its spawner." |
| `:48-54` | the command line; "The same row `sessions get <SESSION_ID>` returns … both exit 1 here." | the command line with `[--slim\|--full]` and `self` / `parent`; the flags accepted before or after the id, never before a subcommand; the date and the notice, as for `sessions`; "exits 1 when no session row has that id, or when `self` / `parent` cannot be resolved (a structured `validation_error` on stdout)" |
| `:56-101` full example | stored agent settings (`"selected_model": null`, `"effort": null`), no `project_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget` | headed "With `--full` (the default before 2026-10-01)"; gains those five keys (`artifacts_dir` a path, `orchestration_scratch_dir` `null`); the agent settings effective (`"selected_model": "opus"`, `"effort": "high"`, `"thinking_enabled": true`, `"question_widget": true`) |
| after the example | — | one line: the reduced projection is the `twicc-sessions` one (its example), with `process: {state}` |
| `:103-118` key fields | no entry for the new keys; `last_viewed_at` / `slug` unmarked | gains `project_directory`, `artifacts_dir` (always the path; over MCP / RPC `has_artifacts` says whether it holds anything, from a terminal it is always `false`), `scratch_dir`, `orchestration_scratch_dir` (`null` outside an orchestration), the agent settings ("effective: the stored value, else the current default; stored values on a subagent"); `slug` and `last_viewed_at` marked "`--full` only" |

`twicc-sessions/SKILL.md`:

| Place | Today | Target |
|---|---|---|
| `:80` | "identity, state, cost, and the `has_*` flags … the redundant timestamps and paths, and the agent-settings bundle — **about 60% lighter**"; "`--full` returns the full payload, the one `session <ID>` returns" | the `SLIM_HELP` description (section 2, "Help texts"), no size figure; "`--full` returns the full payload, the one `session <ID> --full` returns" |
| `:113-147` reduced example | the 27 fields of today's tuple, plus `process` | gains the sixteen fields, with example values: `last_line`, `cwd`, `git_directory`, `project_directory`, `artifacts_dir`, `scratch_dir`, `orchestration_scratch_dir`, `compacted`, `hybrid`, `permission_mode`, `selected_model`, `effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`, `question_widget` |
| `:149` | "each row is the full payload `session <ID>` returns: the fields above plus `last_line`, `mtime`, … `cwd`, `git_directory`, the agent-settings bundle …, `compacted`, … `hybrid`, `artifacts_dir`" | "each row is the full payload (`session <ID> --full`): the fields above plus `mtime`, the `last_started_at` / `last_updated_at` / `last_stopped_at` / `last_viewed_at` timestamps, `slug`, `compute_version_up_to_date`, `self_cost` / `subagents_cost`, `layout`, `browser_url`, `tasks`, `plan_paths`, `goals` — and a five-field `process` block" |
| `:154` `slug`, `:160` `last_viewed_at` | "`--full` only" | unchanged (still dropped) |
| `:158` `compacted` | "`--full` only" | the mark goes (kept) |
| `:201` | "`session <session_id>` — full metadata for one session (exit 1 if missing)" | "`session <session_id\|self\|parent>` — one session's row, same `--slim` / `--full` (exit 1 when no row has that id)" |

Other texts:

- `SKILLS-AND-CLI.md:18`: "`session <ID>` returns one row and always in
  full" → "`session <ID>` and `whoami` follow the same flags and date
  (`whoami`'s flagless call keeps its own object until then)"; "Accepted by
  `sessions`, `sessions get`, `session agents` and `topology`" gains
  `session <ID>` and `whoami`; "a flagless `sessions` or `session agents`
  call therefore prints two notices" gains `sessions get` (the lookup
  notice, then the slim one); `session <ID>` and `whoami` print one (on the
  terminal only for `whoami`); the projection description and "About 60%
  lighter" follow `SLIM_HELP`; `:255`, the whole "Default (no sub-command)"
  bullet (not only "both exit 1 here": "the full session row" and "reach
  for that one … for the reduced projection" become false too), is
  rewritten with the target of the `twicc-session/SKILL.md:48-54` row;
  every "exit 1 if missing" / "exits 1 only when" text on `session <id>`
  follows the same exit rule;
- six "See also" lines describe the bare `session <id>` as the full
  payload; each becomes "one session's row (reduced from 2026-10-01;
  `--full` for every field)" (the plan, Task 11 Step 3, lists them);
- four texts send the reader to "the default view's `plan_paths` field",
  which the reduced projection drops: `twicc-session/SKILL.md:381,393`,
  `SKILLS-AND-CLI.md:263`, the `plan` docstring
  (`src/twicc/cli/session.py:415-416`). Each points to `plan_paths` in
  `session <ID> --full`;
- `twicc-process/SKILL.md:14` and `twicc-create-session/SKILL.md:230`
  ("`session <id>` exits 1 on a session not indexed yet"): true only with no
  row, reworded so;
- `twicc-topology/SKILL.md:44,129`: see "Help texts".
- Tests: `tests/test_cli_session_process_state.py:685-751` call
  `cli_session.main("s1")` without a clock pin;
  `test_it_is_the_same_block_the_listing_builds` (`:694`) compares the
  singular's default block with `sessions get --full`: it passes `full=True`
  to both. `tests/test_slim_cutover.py`:
  - `test_the_flags_reach_the_mcp_schema` (`:363`) gains the `session` route
    and the MCP `whoami` tool;
  - `test_the_help_texts_match_the_side_of_the_cutover_we_are_on` (`:374`)
    gains `session` in `announcing` and checks `whoami`'s own prefix;
  - the `--slim --full` parametrization of
    `test_slim_and_full_are_mutually_exclusive` (`:311-316`) gains
    `["session", "slim-root", "--slim", "--full"]` and
    `["whoami", "--slim", "--full"]` (exit 2, "--slim and --full", no
    warning — `whoami`'s check runs before the lookup, so this holds outside
    a session);
  - `session X --full agents` gets its own test, not a parameter of that
    one: its message is "--slim / --full apply to `session <id>` alone, not
    to `agents`", not "--slim and --full";
  - `assert_full` (`:121-127`) proves the full payload with `"cwd" in row`;
    `cwd` joins the reduced projection, so it tests `"layout" in row`, a
    field the reduced projection still drops;
  - `test_the_notice_date_is_read_at_call_time` (`:189-195`): its docstring
    says "`sessions get` has no pagination notice, so the date can only come
    from the slim one"; `sessions get` gains the lookup notice before the
    date. The call passes `paginated=True`, so the slim notice is the only
    one, and the docstring says so.

### Batch lookups and `peers`

- `twicc-sessions/SKILL.md:106,171`, `twicc-projects/SKILL.md:49,85`,
  `twicc-workspaces/SKILL.md:48,72`, `twicc-peers/SKILL.md:39,56`,
  `SKILLS-AND-CLI.md` sections `:187`, `:214`, `:243`.
  (`twicc-sessions:115`, `twicc-projects:56` and `twicc-workspaces:55`
  open the **listing** examples, which keep their listing shape.)
- The usage lines gain `[--paginated]`: `twicc-projects/SKILL.md:46`
  (`projects get`), `twicc-workspaces/SKILL.md:45` (`workspaces get`),
  `twicc-sessions/SKILL.md:103` (`sessions get … [--slim | --full]`).
- `peers` "takes no argument" goes: `twicc-peers/SKILL.md:34` ("No arguments
  or options") names `--paginated`; `SKILLS-AND-CLI.md:345-347` (the shape
  `{id, name, state, last_contact_at}` listed with no wrapper, and "No
  arguments.") gains the `{"peers": …}` → `{"items": …}` change, the date and
  `--paginated`.
- Tests: `tests/test_peer_cli.py:51-56` (`res.result["peers"]`) and
  `tests/test_cli_pagination_envelope.py:631-658` (its `read()`,
  `:65-66`, does not unwrap `items`) read `sessions get` / `peers` in the
  current shape: pinned before the date, or read `items`;
  `tests/test_slim_cutover.py:313,346`.
  `tests/test_cli_session_process_state.py:229-251,269-281` need no change:
  its `read()` already unwraps `items` (`:91-93`).
- `frontend/src/**` reads peers from the WebSocket (`msg.peers`,
  `src/twicc/asgi.py`) and `tests/test_peer_handshake.py:1066` from an HTTP
  view: neither is the CLI output, neither changes.

### `sessions stop`

Every text saying that a bare call stops everything, that no guardrail spares
the caller, or that the output is one entry per target:

- Code: `src/twicc/cli/sessions_stop.py:1-8` (module docstring, "a bare
  ``sessions stop`` safe to allow"); `src/twicc/cli/__init__.py:380-385`
  (the `SESSION_ID...` help: "a bare `sessions stop` stops every running
  session"); the command help (`:372-375`, "Stop the agents behind selected
  sessions (only those actually running).") gains "A bare call is refused;
  the calling session is never stopped.";
  `src/twicc/cli/sessions_wait_reply.py:24-27` ("``sessions stop`` can afford
  a bare call"); `src/twicc/cli/_stop_batch.py:32-37` (the new keyword and
  status).
- Skills (under `src/twicc/agent/plugin/twicc/skills/`):
  `twicc-sessions/SKILL.md:44` (bare call, "No guardrail spares the caller")
  and `:48` ("one entry per target", the status list gains `skipped_self`);
  `twicc-processes/SKILL.md:16` ("Never call it bare: it stops every running
  session, the caller included … can stop the caller");
  `twicc-orchestration/SKILL.md:176` ("`sessions stop` has no guardrail. A
  bare call stops every running session, you included");
  `twicc-orchestration/control-cookbook.md:83-86` (same);
  `twicc-orchestration-leader/SKILL.md:56` and
  `twicc-orchestration-manager/SKILL.md:41` ("never bare, which stops every
  running session, you included");
  `twicc-update-sessions/SKILL.md:158` ("bare call, … and can stop you too").
  Target: a bare call is refused; `sessions stop` never stops the caller,
  which gets `skipped_self`; `parent`, `--spawn-tree` and `--siblings` still
  reach beyond the caller's children; `--annotation` alone still selects
  across every tree.
- Reference docs: `SKILLS-AND-CLI.md:250` (same claims, "one entry per
  target"), `:363` ("with wider selection and no guardrail"), `:398` ("the
  `sessions stop` guardrails are in its entry above": still true);
  `ORCHESTRATION.md:93` ("`sessions stop` has no guardrail: a bare call stops
  every running session, the caller included").
- The guide: see "The migration guide".
- Tests: `tests/test_cli_sessions_stop.py` — the `run` helper (`:75-78`) and
  every assertion on its list (e.g. `:106` `== []`) read `results`;
  `test_a_bare_call_stops_everything_running` (`:87`) becomes "a bare call
  is refused, exit 1, before the server check"; every other test that calls
  `run` with no id and no filter (e.g. `test_a_stopped_session_is_not_in_the_batch`,
  `:100`) passes a filter. So do the tests that call
  `sessions_stop.main([], …)` directly (`test_a_non_positive_timeout_is_refused`,
  `test_no_backend_means_nothing_to_stop`,
  `test_an_unreachable_server_is_refused_before_anything_is_selected`): a
  bare call is refused before the check each one targets. The docstrings of
  `test_every_filter_reaches_the_query` ("turns its case into a bare stop")
  and of `tests/test_cli_stop_batch.py:52-53,92-93` ("one entry per input
  id", "aligns the array") follow the new shape. The `stop_session_ids` stub `fake_stop(ids, *,
  timeout, force, twicc_pid)` (`:51`) breaks once `sessions stop` passes
  `caller_id=`: it gains the keyword and records it with the others.

### Default cursor

- Texts: see "The cursor, everywhere it is described".
- Tests: the rows of `tests/test_cli_session_wait.py` (fixture `session`,
  `:50-56`) and `tests/test_cli_sessions_wait_reply.py` (`make_session`,
  `:41-46`) are created without `compute_version`, so their compute is not
  current and the default stays their `last_line`: these files stay green
  and now pin the compute-window fallback.
  `test_the_cursor_defaults_to_the_session_s_last_line`
  (`tests/test_cli_session_wait.py:109-118`),
  `test_each_session_starts_above_its_own_last_line`
  (`tests/test_cli_sessions_wait_reply.py:169`, expects `{"a": 40, "b": 7}`)
  and `test_the_batch_survives_a_broken_wait` (`:521`, expects
  `since_line_num` 40 and 7) keep their assertions; their names and
  docstrings say "a session whose compute is not current". The new-default
  tests create rows with `compute_version` set to
  `get_provider_helpers(provider).current_compute_version`
  (`src/twicc/providers/helpers.py:446`), the value
  `session_compute_ready` compares against (`src/twicc/core/serializers.py:17`).
  Never a literal from `src/twicc/settings.py:412-413`: the suite runs with
  `settings_test`, which sets `CLAUDE_CODE_COMPUTE_VERSION = 99`
  (`src/twicc/settings_test.py:67`).

### Page size

- Tests pinning 50: `tests/test_pagination_cutover.py:126-127,151-155,198,350-361`
  and `tests/test_cli_pagination_envelope.py:315-318,364` (→ 20), and
  `:432` (`test_processes_empty_scope_reports_the_same_window`, `"limit": 50`
  → 20: the retired listing's `--paginated` page follows the constant).
- `test_the_flag_overrides_a_command_s_own_default`
  (`tests/test_cli_pagination_envelope.py:323-332`, "``sessions`` defaults to
  20 without the flag and 50 with it") loses its premise: no listing has a
  page size that differs from the constant. It is **deleted**. Rewritten on
  `session messages` (the one command whose flagless default differs), it
  would duplicate `test_the_flag_forces_a_page_size_where_there_was_none`
  (`:310-320`, which moves to 20 with the others); on a command at 20 it
  would assert nothing the constant tests do not.
- Texts: the guide (see above), and every "50" that is a page size:
  - "(default: 20; 50 with `--paginated`)" → "(default: 20)":
    `twicc-artifacts/SKILL.md:42`, `twicc-search/SKILL.md:37`,
    `twicc-sessions/SKILL.md:78`, `twicc-projects/SKILL.md:37`,
    `twicc-workspaces/SKILL.md:37`;
  - "the page size becomes **50**" on `--paginated`:
    `twicc-artifacts/SKILL.md:44`, `twicc-search/SKILL.md:39`,
    `twicc-sessions/SKILL.md:83`, `twicc-projects/SKILL.md:39`,
    `twicc-workspaces/SKILL.md:39`, `twicc-share/SKILL.md:52`,
    `twicc-session/SKILL.md:144,182,188` (the example's `"limit": 50`),
    `SKILLS-AND-CLI.md:17`;
  - the `content` / `messages` cap: `twicc-session/SKILL.md:179,225,226`;
  - `share`'s default: `twicc-share/SKILL.md:50`, `SKILLS-AND-CLI.md:331`.

  The `--limit 50` values in examples (`twicc-artifacts/SKILL.md:136`,
  `twicc-search/SKILL.md:118`, `twicc-sessions/SKILL.md:191`,
  `twicc-projects/SKILL.md:111`, `twicc-workspaces/SKILL.md:90`) are explicit
  arguments, not defaults: unchanged.

## 8. Test override: `TWICC_LISTING_CUTOVER`

An undocumented environment variable replaces `LISTING_CUTOVER` when set. It
lets the owner and the test suite run the post-date behaviour before the
date — and the pre-date behaviour after it — with every value on the same
side, the import-time ones included.

**Where it is read.** In `src/twicc/cli/_output.py`, where the constant is
defined (`:113`; `os` joins the module's imports, `logging` is already
there). The module logger `_NOTICE_LOGGER` (`logging.getLogger(
"twicc.cli.cutover")`, today at `:138`) moves above the constant, and the
function logs through it, looked up at call time as a module global:

```python
_NOTICE_LOGGER = logging.getLogger("twicc.cli.cutover")

_BUILT_IN_LISTING_CUTOVER = datetime(2026, 10, 1)  # noqa: DTZ001
LISTING_CUTOVER_ENV = "TWICC_LISTING_CUTOVER"


def _listing_cutover_from_env(default: datetime) -> datetime:
    raw = os.environ.get(LISTING_CUTOVER_ENV, "").strip()
    if not raw:
        return default
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        value = None
    if value is None or value.tzinfo is not None:
        _NOTICE_LOGGER.warning(
            "Ignoring %s=%r: expected a naive ISO date or date-time; using %s.",
            LISTING_CUTOVER_ENV, raw, default.isoformat(),
        )
        return default
    return value


LISTING_CUTOVER = _listing_cutover_from_env(_BUILT_IN_LISTING_CUTOVER)
```

- **Accepted values:** an ISO date (`2026-09-24`, midnight) or date-time
  (`2026-09-24T13:30`), naive, read as local time like the constant.
- **An invalid value never crashes:** an unparsable string or a value with
  an offset (`…Z`, `…+02:00`) falls back to the built-in date and logs a
  warning. At import, before any logging configuration, Python's
  last-resort handler prints it on stderr, never on stdout: the JSON output
  stays clean.
- **What follows it:** everything derived from the constant. At import:
  `_CUTOVER_DATE`, every `cutover_help` value (help texts, hence the MCP tool
  descriptions), `hidden=listing_cutover_passed()` of the retired commands
  (`src/twicc/cli/__init__.py:1402,1739`), `MCP_EXCLUDED_ROOTS`
  (`src/twicc/mcp/tools.py:33`) and the `@cache`d MCP registry. At call
  time: `listing_cutover_passed()`, every notice, every refusal.

**Where the value comes from.** `src/twicc/cli/__init__.py:21` calls
`ensure_env_loaded()` before it imports `_output` (`:25`): the data dir's
`.env` is loaded first, and a key it defines wins over the inherited
environment (`src/twicc/paths.py:75-113`). So:

- **CLI (terminal):** the environment works —
  `TWICC_LISTING_CUTOVER=2000-01-01 twicc sessions` — unless the data dir's
  `.env` defines the key, whose value then wins. A terminal process is
  short-lived: each call reads the value again.
- **Backend (RPC, MCP):** those calls run in the backend process and follow
  its value. Set the key in the data dir's `.env` (in a worktree, the
  worktree's own `.env`, so the main instance is untouched), then restart the
  backend: the import-time values change only at process start. A terminal
  `twicc` on the same data dir reads the same `.env`, so both sides agree.

**Undocumented on purpose.** It appears in no user doc, skill, help text,
`SKILLS-AND-CLI.md` or migration guide — only in the comment next to the
constant, this design and the tests. It exists to test, not to postpone the
date.

**Tests** (`tests/test_pagination_cutover.py`, next to the predicate tests):

- `_listing_cutover_from_env` with the variable unset, empty, or blank →
  the built-in date, no warning;
- `2026-09-24` → `datetime(2026, 9, 24)`; `2026-09-24T13:30` → that instant;
- `not-a-date`, `2026-09-24T00:00+02:00`, `2026-09-24T00:00Z` → the built-in
  date and one warning record, no exception. Not through `caplog` on the
  real logger: under `settings_test`, `disable_existing_loggers: True`
  (`src/twicc/settings_test.py:59-65`) disables `twicc.cli.cutover` once
  `django.setup()` runs, so the record would never be emitted. The test
  swaps `_output._NOTICE_LOGGER` with the existing
  `_isolate_logger(monkeypatch, handlers=[…])`
  (`tests/test_pagination_cutover.py:280-298`, which documents that trap),
  given a collecting handler as the test at `:303-308` does, then calls
  `_output._listing_cutover_from_env`; the unset / empty / blank cases use
  the same swap to assert that no record is emitted;
- in a subprocess (`sys.executable -c`), with `TWICC_DATA_DIR` set to a
  `tmp_path` holding no `.env` and `TWICC_LISTING_CUTOVER=2000-01-01`:
  `listing_cutover_passed()` is true and `CUTOVER_NOTICE == ""` at import;
  with an invalid value, the process exits 0, stdout holds the expected
  print only, and stderr carries the warning;
- in a subprocess, with a `.env` in that `tmp_path` defining
  `TWICC_LISTING_CUTOVER=2000-01-01` and the environment giving
  `2200-01-01`: the constant is 2000-01-01 (the `.env` wins).

## Tests

- **Constant:** the tests pinning 50 (above) move to 20; `share`'s default is
  asserted at 20 on both sides of the date, with no page-size clause in its
  notice; `CUTOVER_NOTICE` carries no page-size clause and
  `CUTOVER_NOTICE_PAGED` does, on `session content` / `session messages`
  only.
- **`session` flags:** the four orders through `invoke` —
  `session X --full` (bare, full), `session --full X` (bare, full),
  `session X --full agents` (exit 2, "--slim / --full apply to `session <id>`
  alone, not to `agents`"),
  `session X agents --full` (the subcommand's flag, the group's unset) — plus
  `session --full X agents` (exit 2, the same message), `session X --slim
  --full` (exit 2, "--slim and --full are mutually exclusive"), and
  the MCP / RPC form: `render_argv(build_registry()["session"], {"session_id":
  X, "full": True})` gives `["session", "--full", "--", X]`, which runs as the
  bare call with `full=True`; for `session/agents` the flag lands after the
  subcommand token.
- **Slim:** `session <id>` and `whoami` before / after the date (full + notice
  / reduced), `--full` on both sides; MCP schemas of `session` and `whoami`
  carry `slim` / `full`; no subcommand tool gets `slim` / `full` from the
  group; `session_agents` keeps its own; the grown projection is
  asserted field by field.
- **`session self` / `parent`:** resolved on the bare call and on a subcommand
  (`session self messages`, and `session self stop`, which submits the
  caller's resolved id); refused outside a session like the other
  keyword call sites; `session self messages --help` prints the help outside
  a session (no resolution).
- **Lookup:** `session <id>` returns a row with no `created_at` and no user
  message; exits 1 with no row; `session <id> messages` still refuses such a
  row.
- **`whoami`:** before the date without a flag, today's shape and a notice
  on the terminal (none on MCP); with `--slim` / `--full` and after the date, equal to
  `session self` with the same flag; exit 1 outside a session;
  `whoami --slim --full` exits 2 with the mutual-exclusion message, outside a
  session too (the check runs before the lookup).
- **Project-directory helper** (each test starts from an empty cache,
  `monkeypatch.setattr(projects, "_project_directories", {})`, and counts
  with `django_assert_num_queries`): a hit asks no query; a miss asks one
  query, stores the value, and the next call asks none; a key stored with
  `None` returns `None` with no query; the misses of several ids cost one
  query; a key already in the cache survives a call (no `clear()`).
- **Enrichment:** `project_directory` from the helper, one query at most
  for a page whose projects are not cached, none for a cached page;
  `artifacts_dir` set when the session has no artifact yet;
  `scratch_dir`; `orchestration_scratch_dir` `null` outside an orchestration
  and equal to `annotations.scratch_dir` inside; agent settings effective for
  a session stored with `null`s and equal to the stored values otherwise;
  `question_widget` `true` when stored `null`, `false` when stored `false`;
  a subagent row keeps its stored values; a Codex row keeps `null` for
  `thinking_enabled` / `claude_in_chrome`; `serialize_session` output
  unchanged (the UI contract); a placeholder has every enriched key, `null`.
- **Items:** the four commands before (current shape + one notice naming the
  shape, no page-size clause; a flagless `sessions get` gets the lookup
  notice, then the slim one) / after (`{"items": …}`, no `pagination`),
  `--paginated` on both sides; MCP no notice; the help flip read on the
  effective constant (the real clock, and both `TWICC_LISTING_CUTOVER` runs).
- **`sessions stop`:** the new shape, every per-id field, the summary counts
  across statuses, the empty selection with a filter (`all_succeeded:
  true`), exit 0. A bare call (no id, no filter) exits 1 with the refusal,
  before `ensure_server_available` is called. With the caller resolved
  (`resolve_current_session` patched to return a row): named explicitly,
  named as `self`, and selected by a filter (`--spawn-tree`), it is reported
  `skipped_self`, counted in `failed`, and no drop is submitted for it (the
  transport stub never sees its id); the other targets are stopped. With no
  caller (`None`), nothing is skipped. `processes stop` keeps stopping the
  caller.
- **Cursor:** rows created with `compute_version` current. With a session
  having user messages and finals, the default returns the answer after the
  last user message, including one written before the wait started; a
  session with no user message waits from 0; a re-messaged session returns
  the new answer, not the first; a Claude `queued_command` attachment does
  not move the anchor (the documented limit is pinned); an idle session
  ending on a surfaced API error returns `provider_error`; `--from` /
  `--since` unchanged; the indexing-lag case is pinned as the documented
  behaviour (the previous answer is returned); the plural computes every
  cursor in one query. **Compute window:** the same session with
  `compute_version` `NULL`, then with an older version, waits from its
  `last_line` (an answer below it is not returned); in the plural, a ready
  and a not-ready session in one batch each get their own rule. A Codex row
  with `NULL`-kind items (a `function_call_output`) past its last user
  message still anchors on that message.
- **Clock — both sides of the date, on every value.** The suite runs three
  times: on the real clock; with `TWICC_LISTING_CUTOVER=2000-01-01 uv run
  pytest` (every value on the after side); with
  `TWICC_LISTING_CUTOVER=2200-01-01 uv run pytest` (the before side, the
  only way to reach it once the date is past). The variable is read at the
  import of `_output` (section 8), so the help texts, `hidden=`,
  `MCP_EXCLUDED_ROOTS` and the MCP registry follow it. The two
  constant-patching `-p`-plugin runs of the slim design are replaced: they
  left those values on the real side, so
  `test_the_mcp_descriptions_match_the_side_of_the_cutover_we_are_on`
  (`tests/test_pagination_cutover.py:365-390`) was a known false positive
  there, and the `tests/test_mcp_tools.py` registry check could not be
  exercised past the date. The data dir the suite loads (`~/.twicc/.env`
  from the main checkout, the worktree's `.env` from a worktree) must not
  define the key: its value would win. Faking the clock instead is rejected:
  a `datetime.datetime` subclass is refused by orjson ("Type is not JSON
  serializable", hit at `src/twicc/mcp/server.py:140`) and shifts every
  timestamp the tests order by; libfaketime needs a system library and
  shifts the whole process.

## Not in scope

- `project <id>`, `workspace <id>`: one object each, not a session payload.
- `topology`'s own projection and enrichment.
- The retired `processes*` / `process*` commands' shapes.
- New parsing of Claude `queued_command` attachments as user messages, and
  any other fix of the Claude busy-message limit (section 5).
- Documenting `TWICC_LISTING_CUTOVER` for users (section 8).
- The CHANGELOG (the owner's call).
