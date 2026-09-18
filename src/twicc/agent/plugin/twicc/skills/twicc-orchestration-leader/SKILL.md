---
name: twicc-orchestration-leader
description: Act as the leader of a TwiCC orchestration tree — decompose a user's task, spawn managers and/or workers, aggregate, and report back. Load when a user puts you in charge; read twicc-orchestration first.
---

# TwiCC Orchestration — Leader

You are the **leader**: the root of an orchestration tree, with no TwiCC parent, driven directly by a user. You own the global task and decide how to break it down. Read `twicc-orchestration` first — it covers the shared model and how to resolve `$TWICC` used in the examples below.

## When to use

- A user asks you to run a task big enough to split across several sessions.
- You hold the full picture of the task and will coordinate the work.

## Decompose the task

You decide the shape:

- **Workers directly** when the pieces are concrete and independent — avoid the overhead of a middle layer.
- **Managers** when a piece is itself a sub-project that needs its own decomposition.

You can mix both. When unsure whether a piece is atomic, spawn a worker; if it escalates back, split it yourself.

Pick each child's permission mode by need. **Read-only (`strict`/`dontAsk`) confines a child to reading/analyzing the project** — no shell, no writes, no code edits — so it's the safe pick for a pure analyst. Note it is *not* cut off from orchestration: the `mcp__twicc__*` tools work in read-only too, so such a child can still report (`mcp__twicc__send_message parent`) and even spawn via MCP. Spawn an executor when the child must act **on the project** (run commands, write, edit code).

## Brief and spawn

Follow the standard briefing in `twicc-orchestration`, and be explicit about the skills each child must load:

- a **manager** child → load `twicc-orchestration` + `twicc-orchestration-manager`;
- a **worker** child → load `twicc-orchestration` + `twicc-orchestration-worker`.

Put each child's mandate (the actual work and context) in the message — never in annotations — and tell it that it reports to you. Set short tracking annotations on it for visibility (`mode`, `job`/`role`, …; see `twicc-orchestration`). Spawn with `twicc-create-session` and apply the shared visibility and notification policy in `twicc-orchestration`.

Children report to **you**, but they don't have to route everything through you: when two of them share a real cross-dependency, tell them in their briefs that they are siblings who may coordinate directly (`send-message <sibling_id>` / `send-messages --siblings self`, discovered via `sessions --siblings self`) — you still wait on and aggregate them yourself. See `patterns/peer-coordination.md`. Isolate children only when independence must be guaranteed (e.g. a quorum).

## Visibility and permissions

Decide the visibility, notification, and permission policy up front. Propagate it to the whole tree. Follow the full policy in `twicc-orchestration`, including the user's override.

## Collect results

- Read children as they report (`send-message parent` lands in your turn) and pull any child anytime with `$TWICC session <id> messages --tail N`.
- Track your direct children with `$TWICC processes --spawned-by self`; add `--annotation` only with that scope or another filiation scope.
- Wait only on **your direct children**:

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout 900
$TWICC topology self
```

## Handle failures

When a child fails or escalates: retry it, re-split the work, re-delegate to a fresh child, or bring the question back to the user. A manager's internal failures are the manager's job — you deal with the manager's deliverable, not its subtree. If you intentionally abort selected children, stop them with a scoped batch such as `$TWICC processes stop --spawned-by self --annotation status=cancelled --timeout 30`.

## Report and finish

Synthesize the children's results and report to the user. **Only non-hidden sessions are reachable by a user** — link those with `[link text](/project/{project_id}/session/{session_id})`, and **never link a hidden session** (the user would click a link that leads nowhere). If the user wants to review every agent that worked, list them, flag which are hidden, and offer to `unhide` any on request (`twicc-update-session unhide`, or `twicc-update-sessions unhide --spawned-by self` to unhide a whole batch at once).

The shared scratch is internal plumbing of your tree, not a place the user can browse — **never** tell the user to "see the report in the scratch", hand them a scratch path, or paste your children's raw scratch files at them. Whatever the work produced, **you** materialize the final result in your reply: pull the report(s) from the scratch, re-read and synthesize them, and present one coherent answer authored by you. Three workers with three reports become one synthesis you write — not three dumps.

**No archiving is needed** — hidden sessions are already invisible to the user. If you spawned non-hidden sessions and the user wants them tidied away, ask, and archive them with `twicc-update-session` (or `twicc-update-sessions archive --spawned-by self` for the whole batch) if they say yes.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a manager or worker. Skill: `twicc-create-session`.
- `$TWICC topology self` — map and cost your tree. Skill: `twicc-topology`.
- `$TWICC processes --spawned-by self` — track your direct children. Skill: `twicc-processes`.
- One call instead of two: `$TWICC sessions --spawned-by self --slim` carries each child's `process` block, so you get the finished ones too (`dead`) — `processes` only lists the live. See `twicc-orchestration` for the asymmetry.
- `$TWICC processes wait --spawned-by self ...` / `processes stop --spawned-by self ...` — wait on or stop direct child batches. Skill: `twicc-processes`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC send-message <ID> <TEXT>` — steer or follow up one child. Skill: `twicc-send-message`.
- `$TWICC send-messages --spawned-by self --message <TEXT>` — broadcast the same steer/correction to a whole batch of children. Skill: `twicc-send-messages`.
- `$TWICC update-session <ID> annotations|archive|unhide` — annotate, archive, or unhide one session. Skill: `twicc-update-session`.
- `$TWICC update-sessions {annotations|archive|unhide|hide|pin} --spawned-by self ...` — apply one such change to a whole batch of children. Skill: `twicc-update-sessions`.
