# Live process state in the CLI's session listings

**Status:** implemented 2026-09-18, after five review rounds on this document and one on the code.
**Date:** 2026-09-17.

## Goal

`twicc sessions` answers "what sessions exist"; `twicc processes` answers "what
TwiCC is running". A caller who wants both — the orchestration question *"which
of my workers is still working?"* — must call two commands and join them by
hand.

This adds the process state to the three CLI commands that return full session
rows, reusing the projection `topology` already built for the same need.

## Decisions taken

Settled with the user on 2026-09-17, then corrected across three review rounds.

1. Follow `topology`'s technique, and **factor out** what the two now share.
2. On by default (`--processes/--no-processes`), mirroring `topology`'s flag.
3. **One shape in both modes**: a nested `process` object, carrying only
   `state` under `--slim` and the full block otherwise. The asymmetry first
   proposed (a flat `process_state` string in slim) was rejected as two names
   for one concept.
4. **CLI only.** `serialize_session()` is not touched.
5. A subagent gets `process: null`. It has no process of its own.
6. `process: null` also means "no state could be read" — the meaning
   `topology` already gives that value (`topology.py:408`). Round 2, after the
   claim that justified reporting `"dead"` instead proved false.
7. **No availability envelope**, even though two of the three commands could
   carry one today. The user is pushing the `--paginated` cutover date back, so
   a design keyed on the envelope's presence would break when it moves. Round 3.

## Established facts

Each was checked against the code, and each constrains the design.

### `serialize_session` has no process state, and cannot have one

`src/twicc/core/serializers.py:149` emits no process field, in either mode —
`SESSION_LISTING_FIELDS` (`:128`) has none either. Its `state`-sounding fields
(`archived`, `hidden`, `pinned`, `stale`) are user state, not runtime state.

It also carries a hard contract, in the module docstring (`:4-7`):

> These serializers only access model attributes that are already loaded in
> memory (no lazy-loaded relationships, no database queries). This makes them
> safe to call from async contexts without `sync_to_async` wrapping, as long
> as the model instance was already fetched from the database.

The state lives in `ProcessRun`, a separate table. Reading it inside the
serializer would break that contract for every REST and WebSocket caller.
**The join therefore belongs to the caller** — which is what `topology`
already does, and decision (4) above.

### The frontend does not want it there either

The SPA receives process state on its own WebSocket channel: the
`active_processes` snapshot (`src/twicc/asgi.py:559`, consumed by
`setActiveProcesses`, `frontend/src/stores/data.js:4809`) and per-event updates
(`setProcessState`, `:4696`). Putting it in `serialize_session` would give it a
second, staler source of the same fact.

### `state` describes a TwiCC-managed process

An earlier draft claimed an agent cannot outlive its TwiCC instance and hung
the design on it. **False.** The honest framing is narrower, and it is a
definition rather than a limitation.

`state` answers *"what is TwiCC running for this session?"* — the same
definition `processes`, `process`, `process wait` and `topology` have always
used. Under it, `"dead"` means **no TwiCC-managed process**, which is exactly
right for the large majority of rows:

- **A session TwiCC never started has no row, ever, and correctly reads
  `"dead"`.** The initial sync and the watcher create a `Session` row for every
  JSONL under `~/.claude/projects/` and `~/.codex/sessions/`, whoever wrote it —
  including a plain `claude` in a terminal, and `twicc claude` / `twicc codex`,
  which are bare `os.execvp` passthroughs (`src/twicc/cli/claude.py:25`,
  `codex.py:35`). The only `ProcessRun` create site is
  `agent/base_manager.py:594`, reached only from `_register_and_start`.
  Measured on the live database, on two days: **~4300 `type="session"` rows
  against fewer than ten `ProcessRun` rows.** Such a session may well be
  generating; TwiCC is not running it, so TwiCC does not claim that it is.
  **The skill docs must state this in one sentence** — the ratio makes it the
  case a reader meets first, and someone expecting "is anything running?" needs
  to know the question is "is TwiCC running anything?".

No mismatch remains in the other direction — a TwiCC-managed process still
alive while the table says otherwise. Hybrid CLI mode looks like one and is
not:

- A **clean shutdown kills each hybrid CLI** (`hybrid/agent.py:886`, SIGTERM →
  SIGKILL on the pane's process tree), so after a normal stop the session
  really is over and `"dead"` is right.
- A survivor exists only when "the kill didn't land or TwiCC was hard-killed
  (SIGKILL/OOM) before reaching it" (`claude_code/agent/manager.py:374`). But
  TwiCC is then down, so this design emits `null`, not `"dead"` — no wrong
  answer is produced.
- At the next boot, `adopt_running_hybrid_sessions()` runs **immediately after**
  the stale-row cleanup and re-registers the survivor. The only window where a
  live hybrid could read `"dead"` sits between those two steps, during startup,
  before the backend serves anything.

(`CLAUDE.md:97` — "`kill-tmux` is not part of `stop` because hybrid CLIs must
survive a backend restart" — is about the tmux **servers**, which devctl keeps
alive so a restart can re-attach; not about the agents, which the clean
shutdown kills.)

This design inherits the existing commands' fidelity exactly: it adds no
mismatch and closes none.


(An earlier draft cited ephemeral runs as a third hole. They have no
`ProcessRun` row — `base_manager.py:610` guards the create — but no `Session`
row either, on **both** providers: Claude launches them with
`--no-session-persistence` (`providers/claude_code/agent/agent.py:980`), and
Codex threads started with `ephemeral=True` skip the on-disk rollout
(`providers/codex/agent/manager.py:764`, documented at
`providers/codex/title_suggest.py:134-136`). No JSONL, so nothing for the
watcher to index. They never reach these commands. Not a hole here.)

### Two consequences, and they are the design

**1. A missing row means "dead" only when the table could be read.** Rows are
filtered by `twicc_pid` (`topology.py:364`), the pid of the live backend. With
no backend there is no pid, so *every* row would come back `"dead"` — a
conclusion an orchestrator acts on ("all my workers finished") and is wrong
about. Eight commands refuse to answer in that case rather than guess: four via
`resolve_live_twicc_or_exit()` (`processes.py:83`, `processes_get.py:42`,
`process.py:29`, `whoami.py:67`), three via `resolve_live_twicc()` plus an
explicit `emit_error(code=2)` (`process_wait.py`, `processes_wait.py`,
`processes_stop.py`), and `process stop` via `transport.ensure_server_available()`.
`topology` answers with `null` plus an envelope.

These are **session** commands, and `SKILLS-AND-CLI.md:15` publishes that read
commands work whether or not a backend is running — so exiting would be a
regression. **With no live backend the block is `null`.** A caller who needs the
reason runs `twicc status`.

**2. The `twicc_pid` filter is what keeps the answer honest.** Boot cleanup
(`agent/process_run_cleanup.py`) runs only at the *next* startup, so after a
crash the table still holds the previous instance's rows, frozen at
`assistant_turn`. Querying without the pid filter reports them as live. And
`ProcessRun.twicc_pid` is **nullable** (`core/models.py:1385`, "unknown for rows
imported from older schemas"), so `twicc_pid=None` renders as `twicc_pid IS
NULL` and matches legacy rows. `topology` guards this at `topology.py:159`
before calling the loader; see *What moves* for where the guard belongs now.

### Subagents never have a process of their own

Measured on the live database, three times over two days: **no subagent session
has a `ProcessRun` row**. Structurally confirmed too — the single create site is
reached only for real sessions. A subagent runs inside its parent's process, so
any state reported for one would be fabricated. Hence decision (5).

`sessions` is unaffected — it filters `type="session"` (`sessions.py:58`).
`sessions get` applies no type filter (`:75`) and `session agents` returns
nothing else (`session.py:360`).

### Four of the nine `processes` fields are already in a session payload

`serialize_process_row` (`_process_state.py:57`) returns `id`, `provider`,
`session_id`, `session_title`, `project_id`, `state`, `started_at`,
`last_state_change_at`, `pid`. Four duplicate fields the session payload
already carries, in **both** modes: `provider`, `session_id` (as `id`),
`session_title` (as `title`) and `project_id` are all in
`SESSION_LISTING_FIELDS`. `topology._serialize_process` (`:407`) already keeps
only the five that are not.

## Design

### The `process` block

Full mode — the five non-redundant fields, identical to `topology`'s node
block:

```json
"process": {
  "id": 12,
  "state": "assistant_turn",
  "started_at": "2026-09-17T18:58:51+00:00",
  "last_state_change_at": "2026-09-17T19:02:10+00:00",
  "pid": 4242
}
```

Slim mode — the same key, one field:

```json
"process": {"state": "assistant_turn"}
```

With a live backend but no row the shape does not change: `state` is `"dead"`
and the four row-bound fields are `null`, exactly as `topology`'s dead branch
emits (`topology.py:412-418`).

`state` takes the five values `project_virtual_state` already produces
(`_process_state.py:39-54`): `starting`, `assistant_turn`,
`awaiting_user_input`, `user_turn`, `dead`. **No value is added.**
`awaiting_user_input` needs explicit mention in the skill docs. `user_turn`
also means "not working", but obviously so; `awaiting_user_input` is the
**non-obvious** stop, because the underlying `ProcessRun.state` column stays
`ASSISTANT_TURN` (`core/models.py:1387-1394`) and only the virtual projection
surfaces it.

### The cases, one meaning per value

| Case | Payload |
|---|---|
| Live backend, row found | `{"state": "<its state>", ...}` |
| Live backend, no row | `{"state": "dead", ...}` |
| No live backend | `process: null` |
| Session that cannot own one (subagent) | `process: null` |
| `--processes` not requested | key **absent** |

`null` carries one meaning — *no process state is available for this row* — and
it is the meaning `topology` already gives it. An absent key cannot be mistaken
for a fact, which is why `--no-processes` omits it rather than nulling it.

One combination looks contradictory and is not: a `ProcessRun` row is created
**before** the `Session` row exists (`base_manager.py:579-581` — "the create
works even when no Session row exists yet; the watcher creates the Session when
the JSONL file appears"). So `sessions get <just-started-id>` can legitimately
return `known: false` **with a real, live `process` block**. Do not "fix" it.

**`parent_session_id` joins `SESSION_LISTING_FIELDS`.** A consumer must be able
to tell the two `null` cases apart, and the obvious discriminator — "is this a
subagent?" — is **not** in the slim projection today (verified: neither
`parent_session_id` nor `type` is in `SESSION_LISTING_FIELDS`, and `spawned_by`
is null on a subagent, so it does not substitute). Without it,
`sessions get --slim` cannot distinguish a subagent from a down backend.

Adding it fits the projection's stated rule — it is identity, and filiation is
already represented there by `spawned_by` / `spawn_root`. It costs nothing
extra either: the two exact-key-set tests are being updated anyway.

**Fix the comment while you are there.** The same block (`serializers.py:124-127`)
says "a tree needs filiation and no visibility state, a flat listing needs the
reverse" — which reads as *a flat listing does not want filiation*. That is
already inaccurate (`spawned_by` and `spawn_root` are in the list) and this
change makes it more so.

Nothing else consumes the constant: `SESSION_LISTING_FIELDS` has exactly three
readers (the serializer itself and the two tests), the frontend never reads it,
and `SLIM_HELP`, `SKILLS-AND-CLI.md:18` and `twicc-sessions/SKILL.md:43`
describe slim in prose with no field list.

### What moves into `_process_state.py`

That module is already "shared projection + serialization helpers for the
`process` CLI family". It grows the listing-side pieces, and `topology` imports
them instead of keeping private copies:

| New symbol | Replaces |
|---|---|
| `load_process_rows(session_ids, twicc_pid)` | `topology._load_process_rows` (`:358`) |
| `serialize_compact_process(row, *, slim=False)` | `topology._serialize_process` (`:407`) minus its unavailable branch |
| `resolve_listing_twicc_pid()` | the pid lookup, so the in-function import that keeps it patchable lives in one place |
| `attach_process_blocks(entries, rows, *, twicc_pid, slim)` | the per-entry decision, shared by the two commands that query |

**The `None`-pid guard moves INSIDE `load_process_rows`** (`if twicc_pid is
None: return {}`). No call site in this design can reach it with `None` —
topology guards at `:159`, and the three new commands short-circuit to
`process: null` before querying. The guard is there for the natural next
implementation, `twicc_pid = info.pid if info else None` passed straight
through, whose failure mode is silent: `twicc_pid IS NULL` matches legacy rows
and reports them as live. A convention whose failure mode is silent belongs in
mechanism, not in an instruction. `topology` keeps its own `if` regardless
(it also sets `processes_available`), so the shared guard is redundant there,
not wrong.

**The unavailable branch does NOT move.** `topology._serialize_process` returns
`None` when its envelope says processes were not read; that is a topology
policy, meaningful only because topology has an envelope. Keeping it at the call
site (`None if not available else serialize_compact_process(row)`) lets the
shared function have one behaviour instead of a discriminator argument. An
earlier draft proposed passing a boolean; that was a sign the two concerns
should not share a function, not a design.

### Per-command scope

The three commands that return full session rows — the exact set
`SKILLS-AND-CLI.md:18` publishes as sharing one `--slim` projection:

| Command | Notes |
|---|---|
| `sessions` | The primary target. `type="session"` only, so `null` means "no backend" |
| `sessions get` | Explicit ids, so a subagent is possible. An unknown id is looked up like any other — see the just-started case below |
| `session agents` | Subagents only, so always `null` — in scope to keep the three key sets identical |
| `topology` | Unchanged. Already has `--processes`; only its internals move |

**`twicc session <id>` is deliberately out of scope.** It is not a leaf command
but a Typer *group callback* (`cli/__init__.py:371`,
`@session_app.callback(invoke_without_command=True)`), so an option declared
there attaches to the **group**. Two consequences, both verified on the real
app:

- It must be passed **before the positional session id**:
  `twicc session --no-processes ID`. Anything after the id is parsed as a
  subcommand token, so `twicc session ID --no-processes` exits 2.
- It is accepted, and silently does nothing, on every subcommand:
  `twicc session --no-processes ID messages` parses fine and ignores the flag.

It would *not* pollute the subcommands' help — an earlier draft claimed that and
it is false: group options do not appear in a subcommand's `--help` (checked:
`twicc sessions get --help` lists only its own `--slim`), and
`rpc/generator.py:63-65` carries only a group's *arguments* into descendant
routes, so the MCP surface is unaffected too.

The ordering wart alone is enough to keep it out, and nothing is lost:
`twicc processes get <ID>` already returns the full block for one session,
including the dead state. (`twicc process <id>` is not the substitute — it exits
1 when there is no live row, `cli/process.py:38-42`.)

### The `sessions get` placeholder, and a cache to not poison

`sessions_get.py:29` builds the placeholder for unknown ids from
`serialize_session`'s keys, all `None`, and caches it in a **module-level
global** (`_PLACEHOLDER_TEMPLATE`). `main`'s docstring (`:50-52`) states the
rule it protects:

> a batch whose rows changed shape depending on whether the id resolved would
> be worse than no projection at all.

Because `process` is added **outside** `serialize_session`, the placeholder does
not get it for free. Set it **per entry, after the projection — never in the
template**, exactly as `known` is already set (`:91-95`). The MCP server runs
CLI commands in-process (`src/twicc/rpc/invoker.py`), so the module global
survives across tool calls: writing the key into the template would let one
`--processes` call poison every later `--no-processes` call in the same backend
process.

### Test determinism

`settings_test` isolates the DB and the provider homes, but **not the data
dir**: an unpatched `resolve_live_twicc()` reads the developer's real
`~/.twicc/twicc.info.json`, so a test answers differently depending on whether a
backend happens to be running. (It is running right now, so this is live, not
theoretical.)

The repo solves this without touching production code:
`tests/test_cli_topology.py:236` does
`monkeypatch.setattr("twicc.cli._twicc_info.resolve_live_twicc", lambda: None)`.

**Patch `resolve_live_twicc`, not `resolve_live_twicc_or_exit`.** The
`live_twicc` fixture in `tests/test_cli_processes_listing.py:80-85` patches the
latter, which the new code never calls; `_or_exit` delegates to
`resolve_live_twicc` (`_twicc_info.py:72`), so patching the base covers both,
not the reverse.

**Import the symbol inside the function**, as `topology.py:155-157` does. A
module-level `from twicc.cli._twicc_info import resolve_live_twicc` binds past
the monkeypatch and silently re-introduces the non-determinism this section
exists to remove.

**Add no parameter.** An earlier draft proposed an injectable `twicc_pid` on
each `main()`, citing `build_topology` (`topology.py:78`). That seam is on
topology's inner *builder*; `topology.main()` (`:32-39`) has none, so the
precedent argued for the opposite of what was proposed.

### Cost

One extra bulk query per invocation — `session_id__in` over the page's ids —
plus one `resolve_live_twicc()`, which is a single JSON read and one
`psutil.pid_exists()`. Per invocation, not per session.

`session agents` does **neither**: its answer is unconditionally `null`, so it
emits the key without resolving a pid or querying anything.

`_load_process_rows`'s dedupe (`order_by("session_id", "-started_at")`, first
wins) keeps the most recent row per session. `started_at` is non-nullable
(`core/models.py:1370`), but that does not make the ordering *total* — two rows
can share a timestamp and SQLite's tie order is unspecified. It is correct in
practice because a newer row wins over a kept-DEAD one, the only shape the
cleanup leaves behind; do not restate it as a guarantee. It deliberately does
not exclude `DEAD`, which is what lets `project_virtual_state` collapse it.

No new size ceiling: `sessions get` already passes the same unbounded id list to
`Session.objects.filter(id__in=...)` (`sessions_get.py:75`).

## Backward compatibility

This adds a key to three payloads **by default**, and adds one field to
`SESSION_LISTING_FIELDS`. Every existing script and every MCP agent sees both.
That is accepted, and it is the point of decision (2): `sessions` is on every
orchestrating agent's hot path, and a state they must opt into is a state they
will not know exists.

The change is purely additive — no key is renamed, removed or re-typed, and
`--no-processes` restores the previous `process` shape exactly (the new
`parent_session_id` in slim stays either way).

`SKILLS-AND-CLI.md:18` publishes `--slim` as one projection shared by the three
commands; all three keep identical key sets (modulo `sessions get`'s extra
`known`), which is why `session agents` is in scope.

Two existing tests assert an **exact key set** and must be updated:
`tests/test_cli_pagination_envelope.py:445` (`sessions --slim` equals
`SESSION_LISTING_FIELDS`) and `:636` (`sessions get --slim` equals
`SESSION_LISTING_FIELDS | {"known"}`). They become
`SESSION_LISTING_FIELDS | {"process"}` and
`SESSION_LISTING_FIELDS | {"known", "process"}` — `parent_session_id` arrives
through the constant itself. They stay environment-independent and need no
monkeypatch: the **key** is present whether the backend is up (`{"state": ...}`)
or down (`null`). They are the machine-enforced shape contract; extending them
is the deliberate act of changing it.

## Tests

Each row names the mutant it must kill — a test that cannot kill its own mutant
is not evidence.

| Test | Mutant it must kill |
|---|---|
| Slim carries `process` with `state` alone | Slim emits the full block |
| Full carries the five fields | A field is dropped or renamed |
| `--no-processes` omits the key entirely | The key appears as `null` |
| Live backend, no row → `state: "dead"` + four `null` fields | The block is `null`, or the fields are absent |
| No live backend → `process: null` on every row | It reports `"dead"` — the wrong-conclusion case |
| A row in `awaiting_user_input` surfaces as such | It reports `assistant_turn` |
| A row under another `twicc_pid` is ignored | The pid filter is dropped — a dead agent reads as live |
| `twicc_pid=None` returns no rows — a **unit test on `load_process_rows`**, unreachable from the CLI | The `IS NULL` match — a legacy row reads as live |
| A subagent in `sessions get` → `process: null` | It reports `"dead"` |
| `session agents` carries the key, always `null` | The command was skipped, forking the `--slim` contract |
| The three commands' `--slim` key sets are identical modulo `known` | One of them drifted |
| An unknown id's placeholder has the same key set as a known id | The key is missing from the placeholder |
| `sessions get --processes` **then** `--no-processes` on an **unknown id**, same process, **full mode** | The key was written into `_PLACEHOLDER_TEMPLATE` |
| The query count is the same for 1 and for 5 sessions | A per-session query |
| The MCP schema exposes `processes` as a boolean on the three routes | The flag stops being generated |
| The 9 tests in `tests/test_cli_topology.py` stay green, **unmodified** | The refactor changed topology's output |

Three notes on how these must be written, each a way a test would otherwise
pass for the wrong reason:

- **The poisoning test needs three things at once**, and dropping any one
  lets the mutant live. `--processes` must run **first**, since that is the
  call that writes the key. The second call must name an **unknown id**, or the
  placeholder branch (`sessions_get.py:89-92`) never runs. And both must run in
  **full** mode: under `--slim`, `slim_session` (`serializers.py:146`) filters
  to `SESSION_LISTING_FIELDS` and would drop the poisoned key.
- **Count queries relatively** (1 session vs 5), not against an absolute
  number: `sessions.main` already issues several unrelated queries, so a fixed
  total is brittle. Use `CaptureQueriesContext`, which hands back the queries
  themselves (`tests/test_wait_reply.py:618-631` uses it for per-table counts),
  rather than `django_assert_num_queries`, which asserts one exact literal.
- **The topology row is the guard on the refactor**: those 9 tests must stay
  green *and unmodified*, or the refactor was not invisible.

## Non-goals

- **The `--state` filter on `sessions`.** This is the groundwork for it (the
  user's parked "filter the active ones" note) but a separate change. Verified:
  the only `--state` option in `cli/__init__.py` is on `processes`.
- **The frontend, REST and WebSocket payloads.** Unchanged.
- **`whoami`**, which already emits a top-level `process` next to its session
  block — nine fields, and its query *excludes* DEAD (`whoami.py:70-75`) where
  the shared loader deliberately does not. Left alone: `whoami` answers about
  one session the caller already owns, so the shapes never meet in a payload.
- **`processes` / `process` / `process wait`.** Vocabulary and shape unchanged.
- **Closing any `ProcessRun` hole.** Out of reach here; documented instead.
- **`processes_get.py:68-77`**, which holds a fourth copy of the same
  filter-and-dedupe loop. It would migrate mechanically to `load_process_rows`,
  but the `processes` family is out of scope above; noted for whoever touches
  it next.

## Files

- `src/twicc/cli/_process_state.py` — the two new shared symbols, with the
  `None`-pid guard inside the loader.
- `src/twicc/cli/topology.py` — drop the two private copies, import instead,
  keep the unavailable branch at the call site.
- `src/twicc/cli/sessions.py`, `sessions_get.py`, `session.py` (`agents` only)
  — the join, plus the `include_processes` parameter the flag binds to (same
  shape as `topology.main()`). **No `twicc_pid` parameter** — see
  *Test determinism*.
- `src/twicc/core/serializers.py` — `parent_session_id` into
  `SESSION_LISTING_FIELDS`.
- `src/twicc/cli/__init__.py` — `--processes/--no-processes` on the three
  commands. The MCP parameter follows automatically
  (`src/twicc/rpc/generator.py:111-112`, `--x/--no-x` via `secondary_opt`;
  verified end to end).
- `src/twicc/agent/plugin/twicc/skills/twicc-sessions/SKILL.md`,
  `twicc-session/SKILL.md` — the flag, the block, the five values, and the
  one-sentence caveat about what `ProcessRun` does not know.
- `twicc-orchestration/SKILL.md` and its `-leader` / `-manager` variants — they
  answer the Goal's question today with `processes --spawned-by self`; that
  advice now has a one-call alternative.
- `SKILLS-AND-CLI.md` — the flag and the projection note.
- `plugin.json` — minor bump (new options).
- `tests/test_cli_pagination_envelope.py` — the two key-set assertions.
- `tests/` — the table above.
