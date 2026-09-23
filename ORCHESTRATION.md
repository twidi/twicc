# TwiCC orchestration

> One TwiCC session can spawn others, which spawn their own — a **spawn tree** of cooperating agents that split a task, work in parallel, and report back.

Because a session can create and drive other sessions (see [`SKILLS-AND-CLI.md`](SKILLS-AND-CLI.md)), an agent can act as an **orchestrator**: decompose a job, hand pieces to spawned sessions, coordinate them, and aggregate the results — without a human in the loop for each step. TwiCC ships a family of agent skills that turn this from "possible" into a legible, repeatable practice.

This document explains the concept. The mechanics (commands, flags, keywords) live in [`SKILLS-AND-CLI.md`](SKILLS-AND-CLI.md); the agents themselves load the `twicc-orchestration*` skills.

## The model: leader, manager, worker

Every node in a spawn tree has a **mode**, defined by its position:

- **Leader** — the root. No TwiCC parent; driven by a user. Owns the global task, decides the decomposition, reports to the user.
- **Manager** — an internal node. Receives a mandate from its parent, decomposes it across its own children, aggregates, reports up.
- **Worker** — a leaf. Executes one concrete task and delivers.

A leader or manager can create managers and/or workers; a worker is terminal. A node never changes mode after birth — if a worker turns out to need help, it asks its parent rather than becoming a manager.

**Mode is orthogonal to job.** Mode is the orchestration position (which skill the session loads); the *job* is the functional hat it wears in the work (reviewer, backend dev, designer, …). When a parent spawns a child it picks both.

Each mode has its own skill, loaded on top of the shared `twicc-orchestration` skill:

- [`twicc-orchestration`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/SKILL.md) — the shared model and conventions (loaded first).
- [`twicc-orchestration-leader`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration-leader/SKILL.md) / [`-manager`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration-manager/SKILL.md) / [`-worker`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration-worker/SKILL.md) — the playbook for each mode.

## Building blocks

Orchestration is built entirely from the ordinary commands in [`SKILLS-AND-CLI.md`](SKILLS-AND-CLI.md):

- **Spawning** — a parent creates a child with `create-session`; the prompt carries the child's mode, job, and how to report back. A child starts with no memory of its parent, so every brief is self-contained.
- **Pushing up** — a child reports results with `send-message parent`.
- **Broadcasting** — one message reaches many children at once with `send-messages --spawned-by self --message "…"`: to steer them, request status, or ask for a graceful wrap-up (`sent` ≠ done, so pass `--wait-reply` to wait for the answers).
- **Pulling** — a parent can read any child's transcript at will with `session <id> messages --tail N`, independent of whether the child can push.
- **Mapping & tracking** — `topology self` maps the tree, `sessions --spawned-by self --active` tracks the direct children, and `sessions wait-reply <ids> ...` / `sessions stop <ids> ...` wait on or stop batches.
- **Tagging** — annotations keep the tree legible (`update-session self annotations set:status=done`), and a whole batch of children can be retagged, hidden, or archived at once with `update-sessions <op> --spawned-by self ...`.

## Communication

- **Push / pull coexist.** An executor child reports with `send-message parent`; a parent can also pull any child's messages at will. A read-only child (below) cannot push, so pull is the only way to read it.
- **Attribution.** A message sent from one session to another arrives prefixed with a sender header — `> Message from <relation> session <id> ("<title>")` then `---` — where the relation (`your spawned session` / `your parent session` / `a sibling session` / `another session`) is computed from the spawn tree. It is added automatically by `send-message` / `send-messages`; a message without it comes from the user.
- **Siblings never talk directly** — they route through the common parent.
- **A node waits only on its direct children** in normal synchronization, never on grandchildren. Each level pilots its own children; `--descendants` is for exceptional subtree cleanup, not for routine barriers.

## Permission modes: the two extremes

Orchestration uses only the two **non-interactive** extremes of each provider — interactive modes pause for per-tool approvals or questions, and a spawned session stuck on a UI dialog cannot be reliably unblocked from a parent:

- **Executor — allows everything** (Claude Code `bypassPermissions`, Codex `yolo`): can act, write, spawn children, and push to its parent. A manager *must* be an executor.
- **Read-only — allows only reading** (Claude Code `dontAsk`, Codex `strict`): pure analysis of a project; cannot run commands, so cannot spawn, message, or write. Always a terminal leaf, read only by pull — worth it for pure code/content analysis.

## Visibility & propagation

- **`--hidden` is strongly preferred** for spawned sessions: no UI clutter, and a hidden session can never get stuck on a UI dialog. Hidden sessions stay out of every list, search, and counter while their cost still flows into aggregates.
- **The user's choice wins and propagates.** If the user asks for visible sessions, or for a specific permission level, every descendant inherits the same rules.

## Annotations: a map of the tree

Annotations are short key/value tags (free-form JSON) on a session. They turn a tree of opaque sessions into something an orchestrator — and, on request, the user — can read at a glance, because `topology self` and `sessions --spawn-tree self` carry each node's annotations, and `sessions` / `search` / `topology` can filter by them. An annotation is paired with a filiation scope, e.g. `sessions --spawned-by self --annotation status=blocked` for direct children. A barrier on an annotated subset names only that subset's ids (`sessions wait-reply <REVIEW_IDS> --since 2000-01-01`): a child spawned seconds ago matches no filter yet, and named ids are unioned with the filters, never narrowed by them.

Conventional keys (free, not enforced):

- `mode` — `leader` / `manager` / `worker`.
- `job` / `role` — the functional hat (reviewer, backend-dev, …).
- `status` — `working` / `done` / `failed` / `blocked`, kept current as work progresses.
- `task_id` — ties the session to a tracked unit of work.

A parent tags a child at spawn; a session updates its own tags as it goes. Values stay short and single-line — annotations are metadata, not a message channel.

The same map is available visually in the TwiCC UI: any session that belongs to a spawn tree shows a read-only **Orchestration** tab, rendering the whole tree rooted at its top-level ancestor — each node's title, live status, own and cumulative cost, annotations, and timing — to follow the orchestration at a glance without opening each session.

## Scratch files: private & shared

Each session's context block gives a `scratch_base_dir`:

- **Private scratch** — a session's own working files go under `<scratch_base_dir>/<session_id>/` (pre-created by TwiCC, private to that session, no prefix needed).
- **Shared scratch** — to exchange bulky output (a large diff, a generated file, a long report) with the rest of the tree, sessions use the directory passed down as the `scratch_dir` annotation. The leader picks one folder and propagates it through the subtree; it is the leader's own scratch dir, pre-created by TwiCC (`mkdir -p` stays harmless for a custom path), and every file is prefixed with the writer's own session id so agents never clobber each other. A read-only session can **read** the shared scratch but cannot **write** it, so it returns its own result in its reply.

The recurring pattern: an executor writes `<scratch_dir>/<session_id>-result.md`, then sends a short `send-message parent` ("done, see that file"); the parent reads it.

## Lifecycle

A session goes `starting → assistant_turn → user_turn`, then `dead` when its process stops. A dead session is **resurrected automatically** when it receives a `send-message` — so a parent never keeps a child alive on purpose; it simply messages the child again whenever it's needed.

## Process control in an orchestration

`sessions stop` and `sessions wait-reply` accept `--annotation` alone, across every tree: use them scoped, on a node's own children or subtree.

- **Observing the direct children** with `sessions --spawned-by self --active`, or narrowing them with `sessions --spawned-by self --annotation status=blocked`. A child spawned seconds ago is not listed yet: `sessions get <id>` reads it.
- **Waiting for the direct children** with `sessions wait-reply <child ids> --since 2000-01-01 --wait-timeout 300`, named by id. When only a phase or role should participate, name only that subset's ids. The call exits `0` whatever the outcomes: a child still on `timeout` is waited on again (same `--since`, only those ids); `ended`, `provider_error` and `unknown_session` are final.
- **Stopping a selected batch** with `sessions stop --spawned-by self --annotation status=cancelled --timeout <N>`, or explicit session ids when the losing or runaway children are known exactly.
- **Cleaning up a subtree**, only when it is intentionally aborted: `sessions stop <manager_id> --descendants <manager_id> --timeout <N>` stops the manager plus its proper descendants (the id and the scope are unioned).

`sessions stop` has no guardrail: a bare call stops every running session, the caller included, and `parent`, `--spawn-tree` or `--siblings` reach beyond a node's own children.

## Patterns

Leaders and managers compose a structure from five axes — topology, channel, synchronization, aggregation, voice diversity. The recurring combinations are written up as bundled pattern files next to the `twicc-orchestration` skill (the entry point is [`composing.md`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/composing.md)):

- **Distribute** — [scatter-gather](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/scatter-gather.md), [multi-angle](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/multi-angle.md), [divide-and-conquer](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/divide-and-conquer.md)
- **Gate phases** — [phase-gated-fanout](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/phase-gated-fanout.md)
- **Chain** — [pipeline](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/pipeline.md), [plan-then-execute](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/plan-then-execute.md)
- **Decide / verify** — [quorum](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/quorum.md), [debate](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/debate.md), [produce-refute](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/produce-refute.md)
- **Watch & steer** — [supervisor](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/supervisor.md)
- **Survive failure / scale** — [speculative-race](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/speculative-race.md), [worker-pool](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/worker-pool.md)
- **Integrate safely** — [single-writer-integration](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/single-writer-integration.md)
- **Stay within context** — [context-offload](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/patterns/context-offload.md)

## Examples

Worked, end-to-end walkthroughs that combine these patterns on a real task live in the [`examples/`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples) files next to the `twicc-orchestration` skill — each applies a concrete combination of the patterns above:

- [`pr-review`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/pr-review.md) — exhaustively review a pull request (multi-angle + produce-refute, then synthesize).
- [`codebase-audit`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/codebase-audit.md) — audit or refactor a large codebase (divide-and-conquer + worker-pool).
- [`architecture-decision`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/architecture-decision.md) — choose between two architectures (debate + judge).
- [`research-synthesis`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/research-synthesis.md) — research and synthesize a question (plan-then-execute + scatter-gather).
- [`ship-feature`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/ship-feature.md) — ship a feature end-to-end (plan-then-execute + divide-and-conquer + produce-refute).
- [`parallel-feature-integration`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/parallel-feature-integration.md) — integrate parallel feature work through one writer (single-writer-integration + produce-refute).
- [`content-pipeline`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/content-pipeline.md) — turn raw material into a polished deliverable (pipeline + a produce-refute fact-check stage).
- [`go-no-go-quorum`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/go-no-go-quorum.md) — make a risky go/no-go decision (quorum, with cross-provider diversity).
- [`long-running-watchdog`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/long-running-watchdog.md) — run a long migration under a watchdog (supervisor + retry/escalation).
- [`large-migration`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/large-migration.md) — migrate in bounded, validated waves (phase-gated-fanout + worker-pool + supervisor).
- [`hard-bug-race`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/hard-bug-race.md) — crack a hard bug by racing approaches (speculative-race, with diverse approaches).
- [`context-relay`](src/twicc/agent/plugin/twicc/skills/twicc-orchestration/examples/context-relay.md) — relay a job too big for one context (context-offload).

## A note on freedom

This is an unconstrained model: nothing here is enforced except the technical limits of read-only mode. The conventions above are what keep an orchestration legible and steerable; nothing else enforces them, and an agent is free to depart from them — at the cost of a tree that is harder to read and steer.
