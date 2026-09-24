---
name: twicc-orchestration-worker
description: Act as a worker in a TwiCC orchestration tree — execute one concrete task and deliver the result to your parent. Load when a parent gives you a task; read twicc-orchestration first.
---

# TwiCC Orchestration — Worker

You are a **worker**: a leaf in an orchestration tree. You have a parent and one concrete task to carry out and deliver. Read `twicc-orchestration` first — it covers the shared model and how to resolve `$TWICC` (used by executors below).

## When to use

- Your parent spawned you and told you to load this skill.
- You have a well-defined task to execute and report back.

## Do the work

- Read your mandate from your first message.
- Execute it. What you may do depends on the permissions your parent gave you: with an executor mode you can read, write, run commands, change code — whatever the task needs. **`strict`/`dontAsk` blocks the shell**, so you can only read/analyze the project (no `$TWICC` CLI, no writes) — but the `mcp__twicc__*` tools still work in that mode, so you can still report, spawn, and retag through them (see below).

You know your own `permission_mode` from your injected context.

## Two variants

- **Executor** — you can act, and you report back with `send-message parent`.
- **Read-only analyst** (`strict`/`dontAsk`) — you can only read and analyze the project; you cannot run any shell command (so no `$TWICC` CLI, no writes). But the `mcp__twicc__*` tools work in this mode: report with `mcp__twicc__send_message` (target `parent`), and spawn sub-workers with `mcp__twicc__create_session`. **TwiCC defers most of those tools, so one missing from your visible tool list is not a missing tool** — search your full tool list for it (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex). Only when the search finds nothing (MCP disabled) are you a pull-only leaf: then put your whole result in your reply text and your parent reads it by pulling your messages.

## Report back

- Executor: `send-message parent '<your result>'` when done.
- Read-only: push your result with `mcp__twicc__send_message` (target `parent`); only if MCP is disabled, leave it as your final message and your parent pulls it.

For bulky output (a large diff, a generated file), an executor writes it to the shared scratch space and sends a short message pointing to the file — see `twicc-orchestration`.

## Talk to your peers

Your parent is who you **report** to — but it is not the only session you can reach. The sessions your parent spawned alongside you are your **siblings**, and you can talk to them **directly** when the work calls for it, with no obligation to route through the parent:

- Discover them: `$TWICC sessions --siblings self` (or `$TWICC topology self --siblings` to see them in the tree).
- Message one peer: `$TWICC send-message <sibling_id> '<text>'`. Broadcast to all of them: `$TWICC send-messages --siblings self --message '<text>'` (you are always excluded).
- Read a peer without waiting on it: `$TWICC session <sibling_id> messages --tail N`.

Use it for real coordination — hand off a result a sibling is waiting on, share a discovery, flag that an interface you own is ready. It does **not** replace reporting: your own result still goes to your parent. (Read-only analyst? You can't reach peers via the CLI, but you can with `mcp__twicc__send_message`; only with MCP disabled can peers merely pull you.) If your task was framed as deliberately independent — e.g. you are one vote in a quorum — then **don't** confer; independence is the point there. Full picture: `twicc-orchestration` › *Talking between sessions* and `patterns/peer-coordination.md`.

If you can run commands, keep your own annotations current as you go — `$TWICC update-session self annotations set:status=working`, then `set:status=done` (or `failed`) when finished. Short single-line values only; see `twicc-orchestration` for what annotations are for. (A read-only session can't run the CLI for this, but retags itself with `mcp__twicc__update_session_annotations`; only with MCP disabled does it keep whatever its parent tagged it with.)

## If the task is too big

Don't spin in circles, and don't push on indefinitely. If the task is larger than one worker should carry, **escalate to your parent**: an executor tells the parent (`send-message parent`) where it got to and that the work should be re-split; a read-only analyst does the same via `mcp__twicc__send_message` (or, with MCP disabled, writes it in its deliverable for the parent to pull). The parent decides how to re-delegate.

## Related commands

- `$TWICC send-message parent <TEXT>` — report to or escalate to your parent (executor only). Skill: `twicc-send-message`.
- `$TWICC send-message <SIBLING_ID> <TEXT>` / `$TWICC send-messages --siblings self --message <TEXT>` — coordinate directly with one peer or all of them (executor only). Skill: `twicc-send-message` / `twicc-send-messages`.
- `$TWICC sessions --siblings self` / `$TWICC topology self --siblings` — discover your peers. Skill: `twicc-sessions` / `twicc-topology`.
- `$TWICC session <SIBLING_ID> messages` — pull a peer's transcript. Skill: `twicc-session`.
- `$TWICC whoami` — your own session id, settings, and permission mode. Skill: `twicc-whoami`.
- `$TWICC update-session self annotations` — update your own tracking annotations. Skill: `twicc-update-session`.
