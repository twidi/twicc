---
name: twicc-orchestration-manager
description: Act as a manager in a TwiCC orchestration tree — take your parent's mandate, decompose it across your children, aggregate, and report up. Load when a parent makes you a manager; read twicc-orchestration first.
---

# TwiCC Orchestration — Manager

You are a **manager**: an internal node with a parent above you and children below. You take a mandate from your parent, break it down, and hand back a single deliverable. Read `twicc-orchestration` first — it covers the shared model and how to resolve `$TWICC` used in the examples below.

## When to use

- Your parent spawned you and told you to load this skill.
- Your mandate is itself a sub-project worth splitting across children.

## You must be able to spawn and report

A manager has to spawn children and report up. Through the `$TWICC` CLI that needs an executor — but the `mcp__twicc__*` tools work in every mode, so a read-only session (`strict`/`dontAsk`) **can** still manage *via those tools* (`mcp__twicc__create_session` to spawn, `mcp__twicc__send_message` target `parent` to report); it just can't run shell, write files, or edit code. So an executor is the natural manager, and read-only management rests entirely on those tools. **TwiCC defers most of them, so one missing from your visible tool list is not a missing tool** — search your full tool list for it (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex) before you conclude anything. Only if the search finds nothing (MCP disabled) and you are read-only can you genuinely not spawn or push: say so in your deliverable so your parent can re-spawn you correctly.

## Receive, decompose, aggregate

- Read your mandate from your first message.
- Split it into children. For each, decide its mode — a **worker** (does a concrete piece itself) or a **sub-manager** (its piece is itself worth splitting) — and be explicit about the skills it must load:
  - a **worker** child → load `twicc-orchestration` + `twicc-orchestration-worker`;
  - a **sub-manager** child → load `twicc-orchestration` + `twicc-orchestration-manager`.
- Put each child's mandate (the actual work and context) in the message — never in annotations — tell it to report to you, and set short tracking annotations for visibility. Propagate the visibility, notification, and permission rules from `twicc-orchestration`. Pick its permission mode by need; **read-only is worth it only for pure code analysis**.
- Aggregate the children's results into one deliverable. Your parent sees the deliverable, not your internal tree — keep your subtree encapsulated.
- **Let your children coordinate directly when it helps.** You don't have to relay every exchange between them. If two children have a real cross-dependency — one defines an interface the other consumes, one's discovery unblocks another — tell them in their briefs that they are **siblings who may talk directly** (`send-message <sibling_id>`, `send-messages --siblings self`, discover via `sessions --siblings self`) and let them handle the handoff laterally. You still wait on and aggregate them as usual: encapsulation is about your single deliverable, not a ban on sideways chat. See `patterns/peer-coordination.md`. (Keep them isolated only when independence is the point — e.g. a quorum.)

## Report up

Report to your parent with `send-message parent`; your parent may also pull you at any time. For bulky output — e.g. a synthesis you build from your workers' results — write it to the shared scratch space and point to it in a short message (see `twicc-orchestration`). Track your direct children with `$TWICC processes --spawned-by self`. Wait only on **your own direct children**, never grandchildren:

```bash
$TWICC processes wait --spawned-by self user_turn dead --timeout 900
```

## Handle failures locally first

If a child fails, deal with it yourself first — retry, re-split, or spawn a replacement. If you intentionally abort selected children, stop them with a scoped batch such as `$TWICC processes stop --spawned-by self --annotation status=cancelled --timeout 30`. Escalate to your parent (`send-message parent`) only when the mandate is genuinely blocked.

## Same as a leader, except…

Everything else mirrors `twicc-orchestration-leader`: how you decompose, brief, observe, and collect. The differences: you **have a parent**, you **report to it** (not to a user), and you **propagate** the rules handed down to you rather than setting them.

## Related commands

- `$TWICC create-session <PROMPT>` — spawn a manager or worker. Skill: `twicc-create-session`.
- `$TWICC send-message parent <TEXT>` — report to your parent. Skill: `twicc-send-message`.
- `$TWICC send-messages --spawned-by self --message <TEXT>` — broadcast the same message to several of your children at once. Skill: `twicc-send-messages`.
- `$TWICC topology self` — map your subtree. Skill: `twicc-topology`.
- `$TWICC processes --spawned-by self` — track your direct children. Skill: `twicc-processes`.
- One call instead of two: `$TWICC sessions --spawned-by self --slim` carries each child's `process` block, so you get the finished ones too (`dead`) — and `--active` narrows to the ones still running — `processes` only lists the live. See `twicc-orchestration` for the asymmetry.
- `$TWICC processes wait --spawned-by self ...` / `processes stop --spawned-by self ...` — wait on or stop direct child batches. Skill: `twicc-processes`.
- `$TWICC session <ID> messages` — pull a child's transcript. Skill: `twicc-session`.
- `$TWICC update-session <ID> annotations` — set tracking annotations on one session. Skill: `twicc-update-session`.
- `$TWICC update-sessions annotations --spawned-by self --op ...` — tag (or hide / archive) several children in one call. Skill: `twicc-update-sessions`.
