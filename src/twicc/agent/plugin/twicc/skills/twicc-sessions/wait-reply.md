# `sessions wait-reply` — block until several sessions conclude

The plural of `session <ID> wait-reply`: one loop polls them all. MCP tool: `mcp__twicc__sessions_wait_reply`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC sessions wait-reply [SESSION_ID...] [--since INSTANT] [--wait-first|--wait-all] [--wait-timeout N] [--no-reply-text] [FILTERS]
```

For sessions **nobody just messaged**: spawned earlier, steered from the UI, messaged by someone else — or messaged by you with `send-messages` without `--wait-reply` (then pass `--since` an instant taken before the send, or wait on each with `session <id> wait-reply --from <last_line>`). When you send the messages yourself, use `send-messages --wait-reply`: it reads each cursor server-side. **A send without `--wait-reply` is never followed by a wait without a cursor.**

- **One budget covers the batch**: it costs one wall-clock wait, not N.
- Each session concludes the same two ways the singular does: an answer, or a **pending request** only a human can clear. An answer arriving in the same poll wins.
- `--wait-all` (default) waits for every one; `--wait-first` stops at the first to conclude, leaving the rest `outcome: pending`. A session whose turn crashed or was refused never ends a `--wait-first` batch.
- `--wait-timeout` (default 300 s) — the budget for the whole batch.
- `--no-reply-text` — drop each answer's `text`; `line_num` stays.
- **A bare call is refused**: at least one id or one filter. A wait with no filter would poll every indexed session until the deadline.

### Selection

Filters: `--project`, `--workspace`, `--provider`, `--state`, `--active`, `--only-hidden`, `--spawned-by`, `--spawn-tree`, `--descendants`, `--siblings` and `--annotation`, with named ids **unioned** on top (never replacing them). Each works as in the listing (file: `list.md`, section "Filters"), with these differences:

- `--include-hidden` and `--include-archived` do not apply. Hidden is always on (orchestration workers are hidden by convention); archived always off (archiving kills the agent). `--only-hidden` still narrows.
- `--state dead` is refused: a session with no process will never speak. `--active` is the shorthand for "wait on everything alive".
- Filters see indexed sessions only: a child spawned seconds ago matches none yet, so **name its id** (from its `create-session` result).
- A barrier on a subset (one phase, one annotation) names only that subset's ids: filters never narrow ids, so a named id outside the subset widens the barrier.

### The cursor

- **Each session starts after its own last user message**, so an answer already given is returned. Exception: while a session's compute is not current (e.g. right after a TwiCC restart), it starts at its current last line — pass `--since` an instant before the spawn or the send.
- `--since` — the cursor as an **ISO 8601 instant** (`2026-09-30T14:05:00+00:00`), translated per session. **No offset means UTC**; a bare date means its midnight UTC. Only a line written strictly after it counts.
- There is no `--from`: line 42 is a different place in every transcript, which is why an instant is what addresses a batch.
- `--since` exists on `sessions wait-reply` and `session <ID> wait-reply` only (the singular also takes `--from`) — not on `create-session`, `send-message` or `send-messages`, whose `--wait-reply` reads the cursor server-side.
- A named id with a live process but no indexed transcript yet (just spawned) is waited on from line 0; `--since` does not apply to it.

## Output format

`summary` + `results`, one `reply` block per id — the block the singular returns (Skill: `twicc-session`, file `wait-reply.md`).

- A named id with neither a session nor a live process comes back as `outcome: unknown_session`, carrying only that and `session_id` — none of the four keys every other block has (nothing was waited on) — rather than being dropped.
- `summary` — `total` (every id asked for, unknown ones included), `replied`, `awaiting_user_input`, `concluded`, `all_replied`. `replied` counts `outcome: replied` alone; `concluded` also counts the ones ended on a pending request, so `all_replied` can be `false` with nothing left to wait for.

### Exit codes

- `0` — whatever the outcomes: a per-id verdict does not fit in one code. Branch on `summary.all_replied` or on each `outcome`.
- `1` — a local refusal, before anything is waited on.

## Resuming a timed-out batch

Re-running resumes, except for a session whose compute is not current; `--since <the instant the batch started>` resumes in every case. Per session: hand each block's `since_line_num` to `$TWICC session <ID> wait-reply --from`.

## Known limit (Claude Code)

Rare and accepted. A message sent while a Claude session is busy is queued and recorded as a queued command, not a user message. Every wait for an answer — `--wait-reply` included, with a cursor or the default — returns the first final message past its cursor: the running turn's closing message. It usually covers the queued message; rarely, the queued message runs as a turn of its own afterwards, and the wait returned an answer that does not cover it. In an orchestration, message a session once it has finished, not while it works.

## Examples

```bash
$TWICC sessions wait-reply --spawned-by self --wait-timeout 300
$TWICC sessions wait-reply abc123 def456 --wait-first
$TWICC sessions wait-reply --active --annotation role=implementer --since 2026-09-30T14:05:00+00:00
```

## Related commands

- `$TWICC session <ID> wait-reply` — one session, with `--from`. Skill: `twicc-session`.
- `$TWICC send-messages --wait-reply` — send and wait in one call. Skill: `twicc-send-messages`.
- `$TWICC sessions` — preview what the filters select. File: `list.md`.

## How to present results

1. Report `summary`: how many replied, how many are blocked on a human.
2. Per session: `replied` — its `text` or a summary; `awaiting_user_input` — say it is blocked on a human; `pending` / `timeout` — still working.
3. Any other outcome (`unknown_session`, `ended`, `provider_error`, …) — name it and what it means.
