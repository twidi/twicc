---
name: twicc-processes
description: Retired on 2026-10-01 (deprecated until then). Use `sessions …` / `session <id> …` instead.
---

# TwiCC Processes (retired)

The `processes` commands are deprecated until **2026-10-01** and refuse to run from that date: exit `64`, with an error naming the replacement. Use the session commands below. Skills: `twicc-sessions`, `twicc-session`.

## What replaces what

| Retired | Replacement | What changes |
|---|---|---|
| `processes [filters]` | `sessions --active [filters]` (or `--state …`) | A session not indexed yet (spawned seconds ago) is not listed: read it with `sessions get <id>`. From 2026-10-01 the row's `process` block is `{state}` only; `--full` adds `pid`, `started_at`, `last_state_change_at`. |
| `processes get <ids>` | `sessions get <ids>` (`--full` for `pid` and timestamps) | `known` means "a session row exists"; a live `process` block can ride on `known: false`. The process fields sit under `process.*`, not flat on the entry. |
| `processes stop [ids] [filters]` | `sessions stop [ids] [filters]` | Never call it bare: it stops every running session, the caller included. `parent`, `--spawn-tree` and `--siblings` are accepted and can stop the caller. `--annotation` alone selects across every tree: pair it with a filiation scope. Named ids are unioned with the filters. |
| `processes wait` | `sessions wait-reply <ids> --since <instant>` | Waits for an answer, not a state. Exit `0` whatever the outcomes: branch on `summary.all_replied` and each `results[id].outcome`. `--wait-first` replaces `--first` and also stops on `awaiting_user_input`. `--wait-timeout` (≤ 300 s) replaces `--timeout`; a longer wait repeats the call with the same `--since`, naming only the ids still on `timeout` — `ended`, `provider_error` and `unknown_session` are final (skill: `twicc-orchestration`). |

## Lifecycle waits

Waiting for a state (`starting`, `assistant_turn`, `user_turn`, `awaiting_user_input`, `dead`) and `--transition` have **no replacement**. Wait for the answer with `sessions wait-reply`, `send-messages --wait-reply` or `create-session --wait-reply`; its `awaiting_user_input` outcome covers a session blocked on a human. To check whether a session still runs, read `process.state` from `sessions get <ids>`.
