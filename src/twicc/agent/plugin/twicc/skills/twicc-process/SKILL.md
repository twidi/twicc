---
name: twicc-process
description: Retired on 2026-10-01 (deprecated until then). Use `sessions …` / `session <id> …` instead.
---

# TwiCC Process (retired)

The `process <id>` commands are deprecated until **2026-10-01** and refuse to run from that date: exit `64`, with an error naming the replacement. Use the session commands below. Skills: `twicc-session`, `twicc-sessions`.

## What replaces what

| Retired | Replacement | What changes |
|---|---|---|
| `process <id>` | `session <id>` (or `sessions get <id>` right after a spawn) | Exit `0` with `process.state: "dead"` when nothing runs, instead of exit `1`. `session <id>` exits `1` on a session not indexed yet; `sessions get <id>` returns it as `known: false` with its live `process` block. |
| `process <id> stop` | `session <id> stop` | None: same `--force` and `--timeout`. |
| `process <id> wait` | `session <id> wait-reply` | Waits for an answer, not a state. Exit `0` on `replied` or `awaiting_user_input`, `5` on `timeout`, `ended` or `provider_error`, `2` on `backend_gone`, `1` on a refusal or `wait_failed`. Right after `create-session`, use `create-session --wait-reply` (or `--since 2000-01-01`, or an answer already given is missed); after `send-message`, use `send-message --wait-reply`. |

## Lifecycle waits

Waiting for a state (`starting`, `assistant_turn`, `user_turn`, `awaiting_user_input`, `dead`) and `--transition` have **no replacement**. Wait for the answer with `session <id> wait-reply`; its `awaiting_user_input` outcome covers a session blocked on a human. To check whether a session still runs, read `process.state` from `session <id>`.
