---
name: twicc-orchestration
description: Shared model and conventions for multi-session orchestration in TwiCC — the leader/manager/worker tree, talking to children, visibility/permission propagation. Load this first, then the skill for your mode.
---

# TwiCC Orchestration

A TwiCC session can spawn other sessions, which can spawn their own, forming a **spawn tree**. This skill is the shared playbook for every node in such a tree. Load it first, then load the skill for your **mode** (`twicc-orchestration-leader`, `-manager`, or `-worker`).

## When to use

- You or the user want to break a task into several cooperating sessions.
- You were spawned as part of an orchestration and need the shared conventions.
- You are about to spawn a child and need to know how to brief it.

## Resolving `$TWICC`

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** The same commands are exposed as `mcp__twicc__*` tools (`/` and `-` → `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__send_message`, `mcp__twicc__sessions`). Use them over the `$TWICC` CLI: same arguments, same JSON, no shell, and your identity travels with the call (`self`/`parent` resolve on their own). **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex); the `$TWICC` CLI below is the fallback only when the search finds nothing.

The examples here and in the role skills (`-leader`, `-manager`, `-worker`) call TwiCC's CLI as `$TWICC`. Its executable varies by launch mode (uvx, dev, installed tool), so resolve it at the start of each bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break. Every command skill (`twicc-create-session`, `twicc-send-message`, …) repeats this block, so you will see it again when you load one.

## The model

Three **modes**, defined by a node's position in the tree:

- **leader** — the root. No TwiCC parent; driven by a user. Owns the global task, decides the decomposition, reports to the user. Skill: `twicc-orchestration-leader`.
- **manager** — an internal node with a parent and children. Receives a mandate, decomposes it further, aggregates, reports up. Skill: `twicc-orchestration-manager`.
- **worker** — a leaf. Executes one concrete task and delivers. Skill: `twicc-orchestration-worker`.

Who creates whom:

- A leader creates managers and/or workers.
- A manager creates managers and/or workers.
- A worker is a leaf.

A node never changes mode after birth. If a worker turns out to need help, it does not become a manager — it tells its parent.

The tree is your **control and accountability** structure: who spawns whom, who waits on and aggregates whom, who reports to whom. It is **not** a restriction on who may talk to whom. Any session can reach any other — a child to its parent, a parent to a child, **and siblings to each other directly** — see *Talking between sessions* below and `patterns/peer-coordination.md`. Control flows along the tree; communication does not have to.

## Mode vs job

**Mode** (leader/manager/worker) is the orchestration position — a level above the work itself, deciding which skill the session loads. It is orthogonal to the session's **job** (or **role**): the functional hat it wears in the work (project lead, reviewer, designer, backend dev, …). When a parent spawns a child it picks both: the mode (which skill to load) and the job (what the child is there to do).

## Briefing a child

A spawned session starts with **no memory of you** — every prompt must be self-contained. When you create a child (`twicc-create-session`), put in its prompt:

- **its mode** — tell it explicitly which skills to load: `twicc-orchestration` plus `twicc-orchestration-manager` or `twicc-orchestration-worker`.
- **its job and mandate** — what it must do. The work and its context go **in the message**, never in annotations.
- **how to report back** — and that you are its parent (it reaches you with `send-message parent`).

## Talking between sessions

- **Push** — an executor child reports up with `send-message parent` (the `parent` keyword resolves to its spawner). Skill: `twicc-send-message`.
- **Pull** — a parent can read any child's messages at any time with `$TWICC session <child_id> messages --tail N`, whether or not the child can push. The two coexist: you can look in on a child without waiting for its report.
- A **read-only** child (see below) cannot push *through the CLI*, but **can** push via `mcp__twicc__send_message`; only with MCP disabled is pull the sole way to read it.
- **Attribution** — a message sent from one session to another arrives under a sender header (a single `:: message from <relation> session <id> ("**<title>**")` line, then the text), where the relation is `your spawned session`, `your parent session`, `a sibling session`, or `another session`. `send-message` / `send-messages` (CLI and MCP) add it automatically — never write it yourself. A message with no such header comes from the user.
- **Sideways** — siblings *can* talk to each other directly; there is no rule that exchanges must go through the parent. An executor reaches one peer with `send-message <sibling_id>` or broadcasts to all of them with `send-messages --siblings self` (the reference session is always excluded); any session can pull a peer with `$TWICC session <sibling_id> messages`. Discover your peers first with `sessions --siblings self` or `topology self --siblings`. Use it for genuine peer coordination — a handoff, a heads-up, sharing a result — not as a replacement for reporting: control and aggregation still flow up to the common parent. Some patterns (e.g. independent quorum advisors) deliberately keep siblings isolated for independence — don't wire peer chat there. Full pattern: `patterns/peer-coordination.md`.

## Choosing a provider

The user can exclude providers from orchestration (a per-provider switch in the TwiCC settings). `$TWICC info` reports the result as an `orchestration` boolean on each entry of its `providers` dict. When **you** pick the provider for a child (the user named none), spawn only on providers with `orchestration: true`. An explicit user request always prevails: if the user asks for a specific provider, use it even when its flag is `orchestration: false` — only `disabled: true` providers are actually unusable (the CLI refuses them).

## Permission modes — use only the two extremes

Every session knows its own `permission_mode` from its injected context. For orchestration, use only the two **non-interactive** extremes of each provider — one that allows everything, one that blocks the shell:

- **Executor — allows everything** (Claude Code `bypassPermissions`, Codex `yolo`; alias for both `open`): can run shell, write files, edit code, spawn children, and push to its parent — by any means (the `$TWICC` CLI or the MCP tools).
- **Read-only — no shell execution** (Claude Code `dontAsk`, Codex `strict`; alias for both `strict`): pure read/analysis of the given project. It **cannot run any shell command**, so it cannot use the `$TWICC` CLI, the bash-based skills, write files, or edit code.

**But read-only is not a dead end for orchestration.** The `mcp__twicc__*` tools are a control plane mediated by the provider core, *not* the execution sandbox — so they work in **every** mode, read-only included. A read-only session can therefore still drive TwiCC through those tools: `mcp__twicc__create_session` to spawn a child, `mcp__twicc__send_message` (target `parent`) to push, `mcp__twicc__update_session_annotations` to retag itself, and every read tool. So a read-only session is a **pull-only leaf only when MCP is disabled outright**; otherwise it delegates and pushes like an executor — it just still cannot touch the shell, the filesystem, or the project's code.

**Never conclude from your visible tool list that you lack an `mcp__twicc__*` tool.** TwiCC defers most of them on purpose, so they carry no schema until you ask for one: on Codex every tool is deferred, on Claude Code all but a handful. Search your full tool list for the tool you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex) before you treat it as absent. Below, "no MCP tools" always means MCP is off, never merely deferred.

Those aliases are provider-agnostic: pass `--permission-mode open` or `--permission-mode strict` and each provider resolves it to the concrete value above — no need to remember `bypassPermissions`/`yolo`/`dontAsk` per provider.

Avoid the interactive modes: they pause for per-tool approvals or questions, and a spawned session waiting on a user dialog is one you cannot reliably unblock or steer from a parent. Choose a child's mode by what it needs to do — a manager must spawn children and report, which a read-only session can do **only** through the `mcp__twicc__*` tools (never the CLI); make a manager an executor unless you are deliberately relying on its MCP tools.

## Wait only on your direct children

Only ever wait on the children **you** spawned in normal synchronization — never on grandchildren. Each level pilots its own children; a manager's subtree is the manager's responsibility, not yours. Use `--descendants` only for exceptional cleanup of a subtree you intentionally abort.

```bash
$TWICC sessions wait-reply <CHILD_ID>... --wait-timeout 300
$TWICC topology self
```

**Name the children's ids** (from each `create-session` result): a child spawned seconds ago matches no filter yet, while a named one is waited on as soon as it runs. The wait starts after each child's last user message, so an answer already given is returned (except while a session's compute is not current — e.g. right after a TwiCC restart: then pass `--since` an instant before the spawn or the send). For a later round, use `send-messages --wait-reply`, or pass `--since` the instant captured before that send (`date -u +%Y-%m-%dT%H:%M:%SZ` from a shell). **A send without `--wait-reply` is never followed by a wait without a cursor:** after `send-messages`, pass `sessions wait-reply` `--since` an instant taken before the send, or wait on each child with `session <ID> wait-reply --from <last_line>` (the `last_line` its entry returned); after `send-message`, `session <ID> wait-reply --from <last_line>`. `--since` is an ISO 8601 instant (no offset means UTC; a bare date means its midnight UTC); `--since` / `--from` exist only on the two wait commands, not on `create-session`, `send-message` or `send-messages`.

The call exits 0 whatever the outcomes: read each `results[id].outcome`.

- `replied`, `awaiting_user_input` — done (for the second, read `session <ID> pending-requests`).
- `timeout` — repeat the same call, naming only those ids. Re-running resumes, except for a session whose compute is not current; `--since <the instant the batch started>` resumes in every case. This is how a wait longer than 300 s is written: keep each `--wait-timeout` ≤ 300.
- `wait_failed` — the wait broke, the child still runs: repeat the same way. `backend_gone` — repeat once TwiCC is back.
- `ended`, `provider_error`, `unknown_session` — final: a repeat returns them at once. Re-message, re-spawn, or report.
- `pending` — only with `--wait-first`: not concluded yet, wait on it again.

Never loop until `summary.concluded == summary.total`: `concluded` counts `replied` and `awaiting_user_input` only, so a crashed child keeps that loop running forever. An MCP caller whose `send-messages --wait-reply` timed out resumes per id with `session <ID> wait-reply --from <since_line_num>`, from that id's `reply` block.

## Visibility and permission propagation

- **The user's choice wins and propagates.** If the user asks for visible (non-hidden) sessions, or for a specific permission level, honor it **and pass the same rules to every child** — managers spawn with the same rules.
- Propagate the permission policy separately from visibility. Managers apply both policies to their children.

## Session notifications

1. `--hidden` and `--mute-on-user-turn` are independent session behaviors.
2. Hide a child when the user has no reason to read it.
3. Keep a child visible, muted, and `--no-question-widget` when the controlling agent reads and handles its result.
4. Keep a child visible and unmuted when the user is expected to read and act on its result.
5. Questions and approvals still notify when the child is muted.

Global notification settings still apply. Session mute does not enable or change them.

## Annotations

Annotations are short key/value tags on a session (free-form JSON). They are **strongly recommended** in an orchestration: they turn a tree of opaque sessions into a legible map.

What they buy you:

- **An overview at a glance.** `topology self` and `sessions --spawn-tree self` carry each node's annotations, so you — and the user, on request — see who does what and where it stands without reading any transcript. The user reads the same map visually: any session in the tree has a read-only **Orchestration** tab in the UI (titles, status, cost, annotations, timing) — point the user there to follow progress (its URL is a session's URL with `/orchestration` appended, e.g. `/project/<project_id>/session/<session_id>/orchestration`).
- **Filtering by predicate.** `sessions`, `search`, and `topology` accept `--annotation KEY=VALUE` (also `!=`, `:exists`, `:in:a,b`). Pair it with a filiation scope: `sessions --spawned-by self --annotation status=blocked` lists your blocked children. A barrier on an annotation subset (`job=review`) names **only that subset's ids** — `sessions wait-reply <REVIEW_IDS>...`: a child spawned seconds ago matches no filter yet, and named ids are unioned with the filters, never narrowed by them.

Useful keys (free — conventions, not rules):

- `mode` — `leader` / `manager` / `worker`, to filter the tree by position.
- `job` / `role` — the functional hat in the work (reviewer, backend-dev, designer, …).
- `status` — `working` / `done` / `failed` / `blocked`, kept current as work progresses.
- `task_id` — ties the session to a unit of work you track.

Who sets them:

- A **parent** tags a child at spawn (`create-session --annotation mode=worker --annotation job=reviewer`).
- A session updates **its own** tags as it goes (`update-session self annotations set:status=done`) — a read-only session can't run the CLI for this, but retags itself with `mcp__twicc__update_session_annotations`; only with MCP disabled does it keep whatever the parent gave it.

Keep values **short and single-line** — annotations are metadata, not a message channel. Anything long goes in the message or a scratch file.

## Scratch files: private and shared

Your context block gives a `scratch_base_dir`. Two uses:

- **Your own scratch** — for your private working files, use `<scratch_base_dir>/<your_session_id>/`. It already exists (TwiCC creates it for you) and is yours alone, so no filename prefix is needed.
- **Shared scratch** — to exchange files with the other sessions of your tree, use the directory given by the **`scratch_dir` annotation**. Messages and annotations must stay short, so a shared folder is the right channel for bulky output (a large diff, a generated file, a long report).

How the shared scratch works:

- The leader picks a folder (typically `<scratch_base_dir>/<leader_session_id>/`) and passes its absolute path to each child as the `scratch_dir` annotation. A child that spawns children **propagates the same `scratch_dir`**, so the whole subtree converges on one folder. A `scratch_dir` annotation **takes precedence** over your own `scratch_base_dir`.
- **Already created**: the shared folder is the root session's own scratch dir, which TwiCC pre-creates (like every session's), so write to it directly — `mkdir -p` stays harmless/idempotent if you ever point `scratch_dir` at a custom path.
- **Prefix every file with your own session id** (`<session_id>-report.md`) so two agents never clobber the same file.
- **Read-only sessions**: cannot **write** the scratch (that is a filesystem write, which the MCP tools do not grant), but **can read** it — TwiCC grants every tree member read access to the shared scratch, so a read-only session still pulls peers' files from there. It returns its own result either in its reply (the parent pulls it) or by pushing it with `mcp__twicc__send_message`.

Pattern: an executor (worker or manager) writes `<scratch_dir>/<session_id>-result.md`, then sends a short `send-message parent` ("done, see `<session_id>-result.md`"); the parent reads the file.

The scratch is **internal to the tree** — a handoff channel between its sessions, not something the end user browses (they have no UI for it). Whatever must reach the user travels up the tree in messages and is finally surfaced by the leader in its own reply; never hand the user a scratch path. See `twicc-orchestration-leader`.

## Lifecycle

A session goes `starting → assistant_turn → user_turn`, then `dead` when its process stops. A `dead` session is **resurrected automatically** when it receives a `send-message` — so you never need to keep a child alive; message it whenever you need it.

## Process control

Use process controls as scoped operations:

- List your direct children with `$TWICC sessions --spawned-by self --slim`: each row carries its `process.state`, and `--active` keeps the ones still running (`user_turn` counts: loaded and idle, not gone). Narrow with `--annotation status=blocked`. A child spawned seconds ago is not listed until its transcript is indexed: **name its id** — `$TWICC sessions get <ID>` returns it with its live `process` block.
- Wait on direct children with `$TWICC sessions wait-reply <CHILD_ID>...` (see *Wait only on your direct children*).
- Stop selected children with `$TWICC sessions stop <ID>... --timeout <N>`, or with `--spawned-by self --annotation status=cancelled`.
- Abort a subtree deliberately with `$TWICC sessions stop <manager_id> --descendants <manager_id> --timeout <N>`; `--descendants` excludes the target, and named ids are unioned with the scope, so the manager stops too.

**`sessions stop` refuses a bare call and never stops you**: the calling session is reported `skipped_self` (stop yourself with `session self stop`). `parent`, `--spawn-tree` and `--siblings` still reach beyond your children (`--spawn-tree self` selects you, reported `skipped_self`): use them only when that is the intent. Use `--annotation` only with a filiation scope; alone it still selects across every tree.

For exact command recipes (barriers, scoped waits, races, batch stops, subtree aborts), read `control-cookbook.md` only when you need to operate live processes.

## Freedom

This is the unconstrained model: nothing here is enforced except the technical limits of read-only mode (no shell, no file writes — though the `mcp__twicc__*` tools still work, see *Permission modes*). The conventions above are what keep an orchestration legible — follow them, but the system will not stop you from doing otherwise.

## Orchestration patterns (for leaders and managers)

You compose a structure from five axes — topology, channel, synchronization, aggregation, voice diversity. The recurring useful combinations are written up as bundled pattern files next to this skill, under `patterns/`. Read `patterns/composing.md` first (the five axes), then open the one that fits — you don't need them all:

- Distribute — `patterns/scatter-gather.md`, `patterns/multi-angle.md`, `patterns/divide-and-conquer.md`
- Gate phases — `patterns/phase-gated-fanout.md`
- Chain — `patterns/pipeline.md`, `patterns/plan-then-execute.md`
- Coordinate laterally — `patterns/peer-coordination.md`
- Decide / verify — `patterns/quorum.md`, `patterns/debate.md`, `patterns/produce-refute.md`
- Watch & steer — `patterns/supervisor.md`
- Survive failure / scale — `patterns/speculative-race.md`, `patterns/worker-pool.md`
- Integrate safely — `patterns/single-writer-integration.md`
- Stay within context — `patterns/context-offload.md`

For worked, end-to-end walkthroughs that combine these patterns on a real task, see `examples/` — `pr-review`, `codebase-audit`, `architecture-decision`, `research-synthesis`, `ship-feature`, `parallel-feature-integration`, `content-pipeline`, `go-no-go-quorum`, `long-running-watchdog`, `large-migration`, `hard-bug-race`, `context-relay`. Each pattern file also links the examples that use it.

Workers don't orchestrate, so they can ignore this.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a child. Skill: `twicc-create-session`.
- `$TWICC send-message <ID|parent> <TEXT>` — push to a session or to your parent. Skill: `twicc-send-message`.
- `$TWICC send-messages --spawned-by self --message <TEXT>` — broadcast the same message to several children at once. Skill: `twicc-send-messages`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC topology self` — map your spawn tree. Skill: `twicc-topology`.
- `$TWICC sessions --spawned-by self --active` — track your direct children. Skill: `twicc-sessions`.
- `$TWICC sessions wait-reply <ID>...` / `sessions stop <ID>...` — wait on or stop child batches. Skill: `twicc-sessions`.
- `$TWICC update-session <ID> annotations` — set tracking annotations on one session. Skill: `twicc-update-session`.
- `$TWICC update-sessions annotations --spawned-by self --op ...` — tag (or hide / archive) several children in one call. Skill: `twicc-update-sessions`.
- `$TWICC whoami` — your own session id, settings, and permission mode. Skill: `twicc-whoami`.
