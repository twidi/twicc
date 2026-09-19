# Answering a session's pending question from the CLI

**Status:** design settled. Nothing implemented. No open decisions (§9).
**Date:** 2026-09-18.
**Scope:** questions only. Deliberately narrower than
[`2026-09-18-pending-request-cli-design.md`](2026-09-18-pending-request-cli-design.md),
which covers every pending-request kind and is shelved. That document holds the
full research (the 13 wire shapes, the provider plumbing, the security
analysis); this one states only what changes, and why the narrow scope is
cheap.

## 1. What this covers

Exactly two `tool_name` values:

| Provider | `tool_name` | `request_type` |
|---|---|---|
| claude_code | `AskUserQuestion` | `ask_user_question` |
| codex | `toolRequestUserInput` | `ask_user_question` |

Everything else is **out of scope and stays out**: tool approvals of any kind —
including a Codex MCP tool approval wearing a question's form, which is detected
and excluded (below) — MCP elicitations (`elicitationForm` / `elicitationUrl` — which carry
`request_type: ask_user_question` too, so the filter cannot be on
`request_type` alone), `planImplementation`, `autoReviewDenial`, `mcpToolCall`,
and the `hybrid_terminal` degradation.

**The one ambiguous case, and how it is resolved.** On Codex,
`toolRequestUserInput` is *also* the fallback form for **MCP tool approvals**,
used when the `ToolCallMcpElicitation` feature is off. Per
`docs/plans/2026-07-11-codex-mcp-approvals-plan.md:22-23` (which traced
`codex-rs/features/src/lib.rs:1113-1116` — a source not vendored here, so this
is second-hand) it is **on by default**, so the fallback only fires for a user
who disabled it in their own `~/.codex/config.toml`
(`providers/codex/agent/approvals.py:19-23`,
`codex/RequestUserInputBody.vue:8-11`).

**Decision (user, 2026-09-18): detect it and exclude it.** A disguised approval
is *not* a question, so this command must not answer it — it is reported as
out of scope (§3) like any other approval, not accepted by mistake.

The detection keys on the **authored** discriminator. Codex builds the approval
form itself, in `build_mcp_tool_approval_question`
(`mcp_tool_call.rs:1529-1573`, traced in
`docs/plans/2026-07-11-codex-mcp-approvals-plan.md:153-158`) with exactly **one**
question whose `id` is `mcp_tool_call_approval_<call_id>`. The rule is therefore:

```python
isinstance(questions, list)
and questions
and isinstance(questions[0], dict)
and isinstance(questions[0].get("id"), str)
and questions[0]["id"].startswith("mcp_tool_call_approval")
```

The `isinstance(questions[0], dict)` link is not redundant: the JS reference
below gets it for free (`"x".id` is `undefined`), Python raises `AttributeError`
on `"x".get`.

The guards are still worth writing, though §4.1's decision makes them much less
load-bearing than an earlier draft claimed. The rule now runs inside the read
service, on the drop-request path, where `execute_drop_payload` already catches
everything and turns it into `status: "failed"`
(`drop_requests_watcher.py:242-246`). So a malformed payload costs one failed
command, not a corrupted state transition. Write them anyway: an
`AttributeError` (`"x".get`) on a third-party payload is a crash report nobody
needs, and the repo's own Codex body defends the same two cases for the same
reason (`codex/RequestUserInputBody.vue:48-51,198-201`: *"a pathological payload
could send a missing/non-string id"*).

The literal `mcp_tool_call_approval` is present in the bundled Codex binary
(0.153.4), and `call_id` is appended by a format string, so the check is on the
prefix rather than an exact value.

**The id is not a protocol field the model is barred from writing.** It is a
property of the `request_user_input` tool schema, documented in the same binary
as *"Stable identifier for mapping answers (snake_case)."* — so a model authoring
a genuine question chooses it. What makes the rule work is only that Codex
*always* writes the prefix when the form really is an approval. Recognising the
approval is therefore reliable; the converse is not guaranteed. Three
consequences an implementer must know:

- **It degrades toward *accepting*.** If Codex ever renames that id, the form
  stops being recognised and is treated as a question again — the pre-decision
  behaviour, the opposite of fail-safe under this decision. The rule deserves a
  test pinning the prefix and a re-check at each Codex re-vendoring (same
  discipline as the enum patch in the GPT-5.6 integration).
- **It can also over-exclude.** A model that names a genuine question's id
  `mcp_tool_call_approval_…` gets it reported `out_of_scope` and unanswerable
  from the CLI — still answerable in the web UI, so nothing is stuck, but the
  command lies about why. Vanishingly unlikely, and it fails toward "not
  answered", which is the safe side under this decision. Not mitigated.
- **A second, independent signal exists** if belt-and-braces is ever wanted: the
  options come from the fixed set `Allow` / `Allow for this session` / `Allow and
  don't ask me again` / `Cancel`, which the Codex parser matches **verbatim**
  (`parse_mcp_tool_approval_response`, `mcp_tool_call.rs:1808-1845`) and so
  cannot change without breaking itself. It is no less model-writable than the
  id — requiring *both* would narrow the over-exclusion above; requiring
  *either* would catch a renamed id. Neither adopted: one authored
  discriminator, stated plainly, beats a scoring heuristic nobody can reason
  about later.

**Rejected, by the same decision:** pinning
`features.tool_call_mcp_elicitation = true` in TwiCC's per-thread config patch
(where `features.*` keys are already written,
`providers/codex/agent/manager.py:904-906,942-943`) would make the fallback
unreachable whatever the user's config says. Rejected because TwiCC should not
override a Codex setting the user chose. Recorded so the choice stays traceable.

## 2. Why the narrow scope is cheap

Against the three obstacles the full design hit:

| Obstacle (full scope) | Here |
|---|---|
| **Security.** Answering an approval grants an unprompted capability to every agent, in every mode and trust state, and an agent can approve its own parallel `Bash` in the same turn. | **Reduced to one rule**, not gone. See below. |
| **The data is not readable from a CLI process.** Pending requests live only in backend memory; the DB keeps one boolean. | **Still true**, and answered the same way as the full design: a read lane in the existing drop transport, reading the live registry. The narrow scope briefly made a persisted projection viable — a question payload is small, unlike a `fileChange` diff — but that option was weighed and rejected (§4.1). §4. |
| **`hybrid_terminal` would make `answer` report a false success.** | **Reduced to a filter**, but the filter criterion matters: the degradation rewrites `request_type` and leaves `tool_name` intact (`hybrid/agent.py:776`), so filtering on `tool_name` alone would let it through. §3. |

**What the security reduction really is.** A question answer carries no
`updated_permissions`, no `setMode`, no `permission_mode` change, and nothing
executes: the escalation path that made the full scope a blocker is absent.
Verified: the Claude `ask_user_question` branch cannot carry
`updated_permissions`, and the `setMode` persist is gated to `tool_approval`
(`providers/claude_code/ws.py:384-420,431`).

But it is **not** true that this grants nothing new. The obvious analogy —
"an agent can already write arbitrary text into another session with
`send-message`" — fails precisely here: `send-message` **refuses** while
`ProcessRun.awaiting_user_input` is true (`core/services/send_message.py:142-155`),
which is exactly the state `answer` targets. So `answer` does create a new
capability for any caller other than the target session: **unblocking a session
a human deliberately left pending**, and writing caller-chosen text into the
tool result.

That capability is bounded (text into a transcript, no execution, no permission
change), which is why this design does not carry the full-scope gate — no synced
setting, no trust re-resolution. §6 states the one rule that remains and the
mechanism behind it.

Net: 2 wire shapes instead of 13, one security rule instead of a gate, no
migration, and four edits to a shared status vocabulary (§4.1).

## 3. The filter, and staying honest about what it hides

A pending request is **answerable** iff:

```
request_type == "ask_user_question"
AND tool_name in {"AskUserQuestion", "toolRequestUserInput"}
AND NOT (tool_name == "toolRequestUserInput"
         and questions[0]["id"].startswith("mcp_tool_call_approval"))
```

This filter is the **only** definition of *answerable*, and it is what target
selection uses throughout: `no_pending_question`, `ambiguous_request` and the
default when `--request-id` is omitted all count the requests that pass it. §5's
"structurally unanswerable" cases (an empty `questions` list, any `secret`
question) are a different thing — they pass this filter, they are selected
normally, and they only shape what `actions` advertises and which rejection the
answer attempt earns. A lone pending question flagged `secret` is therefore
`secret_answer_unsupported`, never `no_pending_question`.

The third condition is §1's exclusion of the disguised MCP tool approval; it
applies to Codex only, and to a payload with at least one question (an empty
`questions` list cannot be a disguised approval — Codex always builds exactly
one — and is reported as a question with nothing to answer).

The first two conditions are load-bearing too. `request_type` alone lets the elicitations
in; `tool_name` alone lets a degraded hybrid question through, because
`_schedule_gui_expiry` rewrites only the `request_type`
(`providers/claude_code/agent/hybrid/agent.py:776`). A hybrid Claude session
really can hold an `AskUserQuestion`: the tool is only stripped when
`question_widget is False` (`hybrid/launch.py:152-153`).

**Filtered-out requests are still reported.** A session blocked on a tool
approval has `awaiting_user_input` true; a `pending-request` that returned an empty list
would tell a caller "nothing is waiting" about a frozen session. So every
pending request appears, and the non-answerable ones appear as a minimal entry:

```json
{"request_id": "…", "created_at": "…", "kind": "out_of_scope",
 "reason": "tool_approval" | "mcp_tool_approval" | "elicitation"
           | "terminal_only" | "choice",
 "tool_name": "commandExecution", "actions": []}
```

`reason` is derived, in this order: `request_type == "hybrid_terminal"` →
`terminal_only`; the §1 disguised-approval rule → **`mcp_tool_approval`** (its
own value, not folded into `tool_approval`, so the exclusion is visible in the
output and debuggable); `tool_name` in `{elicitationForm, elicitationUrl}` →
`elicitation`; `tool_name == "planImplementation"` → `choice`; everything else
(`commandExecution`, `fileChange`, `permissions`, `mcpToolCall`,
`autoReviewDenial`, and every Claude tool approval — `Bash`, `Read`,
`ExitPlanMode`, …) → `tool_approval`. That covers every producer the code has
(full design §1.1).

`reason` says why an entry is not answerable **here**; it is not a
cross-provider taxonomy. Hence an asymmetry worth naming rather than papering
over: Codex's post-plan prompt is `choice`, while Claude's plan gate
(`ExitPlanMode`, an ordinary `tool_approval` through `can_use_tool`,
`claude_code/agent/agent.py:698-701`) reports `tool_approval`. The two are
equivalent to a human and different to the code; the code's view is the one
reported.

No normalizer, no payload (absent `--raw`, which §5 extends to every entry), no
provider knowledge — just the truth that something is waiting and this command
cannot answer it.

## 4. Transport

### 4.1 `pending-request` — a read lane in the existing transport

**Decided (user, 2026-09-18).** Nothing is written to the database. The command
rides the same drop-request transport as every other CLI mutation, with a new
kind `session:pending_requests` whose service reads the live registry and returns
the normalized list.

The reasoning that settled it: a pending request only exists while a session has
a live agent, so the information is already in the backend process's memory.
`AgentManagerRegistry.get_agent_info(session_id)` (`agent/registry.py:75`) hands
it over for both providers. An MCP or `/rpc/` call runs *inside* that process and
touches no filesystem at all — `transport.submit` schedules
`execute_drop_payload` on the running loop instead of writing a file
(`cli/_drop_request/transport.py:119-127`). A local `twicc` process, being a
separate process, goes through the file exchange like `process stop` and
`send-message` do. Either way the answer is the live truth, and there is nothing
to keep in sync.

The service returns its payload through `status_extra`, which
`execute_drop_payload` already merges verbatim into the status dict
(`drop_requests_watcher.py:254-257`); `peer_status` is the existing precedent.

**The one cost: a new terminal status.** Every status word the transport knows
today — `created`, `sent`, `updated`, `stopped`, `deleted`, `rejected`, `failed`
— names a mutation, because until now the transport only carried mutations. A
read has no word. `fetched` is that word, and it must be declared in four places
plus the handler table:

| Site | What |
|---|---|
| `cli/_drop_request/transport.py:38` | `_FINAL_STATUSES` |
| `cli/_drop_request/polling.py:39` | the same tuple, duplicated — hygiene, not a requirement: `poll_status` has no callers left (the live local-mode check is `Submission.poll` → `_FINAL_STATUSES`, `transport.py:82`), but leaving the copy out of sync is a trap for whoever revives it |
| `cli/_drop_request/output.py:49` | `build_final` — needs a `fetched` branch emitting the payload instead of the id projection |
| `drop_requests_watcher.py:209-217` | `_STATUS_TIME_FIELDS` — a status absent here silently gets no timestamp (`stamp_status_times`, `:221`) |
| `drop_requests_watcher.py:44` | `_KIND_HANDLERS`, `success_status` slot |

A repo-wide grep finds the vocabulary hardcoded nowhere else — not in `mcp/`,
`rpc/`, the batch runner, or the tests.

**What this buys over the rejected alternative.** Persisting a normalized
projection on `ProcessRun` was the other candidate: one JSONField filled in
`BaseAgentManager._persist_process_run_transition` (`agent/base_manager.py:960`),
which already fires on every pending-request add and remove (`:985-990`), and
`pending-request` would have been a plain DB read touching no shared file. It
was rejected for two reasons:

- **Two copies of an ephemeral truth.** A pending request dies with its agent;
  persisting it means a mirror that can drift on a failed write, plus DEAD
  forcing and a boot-cleanup clear to keep it honest
  (`agent/process_run_cleanup.py:93-96` updates only `state` and
  `last_state_change_at` today).
- **`--raw` coverage.** Under the projection, storing raw `tool_input` for every
  kind would have reopened the multi-megabyte `fileChange` diff problem, so
  `--raw` would have been limited to question entries. Off the live registry it
  covers every kind (§5).

The migration it would have cost is not the point; the mirror is.

**Ephemeral sessions: out of reach, and that is the decision** (user,
2026-09-18). An earlier draft claimed the live-registry lane would serve them
and the projection would not. That was wrong in both halves, and the conclusion
drawn from it has been reversed rather than patched.

The mechanism, because it is counter-intuitive: an ephemeral session *is* alive
in the agent registry, run by the same SDK machinery as any other. But the CLI's
shared pre-check does not ask the registry — it asks the database
(`Session.objects.filter(id=…)`, `cli/_drop_request/session_lookup.py:70-77`),
and the `Session` table is filled by the JSONL watcher, not by the agent
manager. An ephemeral run writes no JSONL
(`claude_code/agent/agent.py:979-980`, `no-session-persistence`), so no row is
ever created (`docs/plans/2026-09-05-ephemeral-sessions-design.md:8-12`). Two
directories for the same thing; the pre-check consults the one that does not
know. Result: `session_not_found` → exit 1, under **either** transport.

The read service *could* skip that lookup and go straight to
`registry.get_agent_info`, which would serve them — something persistence could
never do, with no `ProcessRun` row to read. **Not done, deliberately.** The
whole CLI treats ephemeral sessions as invisible (`twicc sessions`,
`twicc session`, the `process` family — `2026-09-05-ephemeral-sessions-design.md:418-423`),
and "unknown session" is the consistent answer, not a regression to repair here.
Skipping the lookup would also trade five precise refusals (unknown session,
subagent, stale, no project directory, unknown provider) for an undifferentiated
"nothing pending".

One thing this decision closes off, **rejected in §9**: feeding `processes
--state awaiting_user_input` a one-line summary, so a caller learns what is
waiting everywhere in one call instead of one call per blocked session. Those
listings are DB reads, so the summary would need either its own live-registry
round-trip per row or the persisted column this section just rejected. Not a
follow-up, not a V2 — decided against.

### 4.2 `answer` — the existing drop-request transport

New kind `session:answer_pending_question`, handled like `process:stop`
(`drop_requests_watcher.py:44`, `core/services/process_kill.py`). Its
`success_status` is the **existing** `updated` — no new status word, so the
shared vocabulary stays untouched on this side too.

## 5. `pending-request` — output

```
twicc session <ID> pending-request [--raw] [--timeout N]
```

Example: `twicc session 019f4d27-… pending-request`.

The payload below is **wrapped**, like every other drop-request result:
`build_final` stamps `status` and `request_uuid` on every outcome
(`cli/_drop_request/output.py:70,85,92,104`), so the `fetched` branch emits
`{"status": "fetched", "request_uuid": …, <the object below>}` rather than the
bare object. A caller reads `pending_requests` and ignores the envelope.

`--timeout` is not decoration: §4.1 puts this command on the drop-request
transport, so it submits and polls exactly like `answer` does, and
`transport.wait` requires a budget (`cli/_drop_request/transport.py:132`).
Default 30 seconds, the house value every other drop-request command uses
(`cli/__init__.py:382,666,990`). Exceeding it is exit 5, same as the write.

**The read and the write are not siblings.** The read is named
`pending-request` because that is what it reports: every pending request,
answerable or not (§3), which is also why its top-level key stays
`pending_requests`. It takes **no verb** — a lone `get` under it would name a
choice that does not exist. Declared as a Typer group with
`invoke_without_command=True`, the way `session` and `artifacts` already are
(`cli/__init__.py`), so a second read verb is added later without moving the
first. The write is a **flat** `session <ID> answer <ACTION>` (§6), with no
intermediate group at all.

The asymmetry is deliberate and the reason is that there is nothing to
disambiguate on the write side: exactly one family of pending request is
answerable here, so a group whose only job is to say *which kind you are
answering* would carry no information. It earns its place the day a second kind
becomes answerable — at which point `answer` grows a target, rather than the
tree growing a level nobody needed in between.

```json
{
  "session_id": "…",
  "provider": "claude_code",
  "agent_state": "awaiting_user_input",
  "pending_requests": [
    {
      "request_id": "…",
      "created_at": "2026-09-18T15:04:11.123456+00:00",
      "age_seconds": 42.1,
      "kind": "question",
      "tool_name": "AskUserQuestion",
      "questions": [
        {"id": "1", "header": "Database", "question": "Which database should we use?",
         "multi_select": false, "allows_free_text": true, "secret": false,
         "options": [{"label": "PostgreSQL", "description": "Reliable, feature-rich"},
                     {"label": "SQLite",     "description": "Lightweight, file-based"}]}
      ],
      "actions": [
        {"action": "answer", "label": "Answer the questions", "accepts": ["--answer"]},
        {"action": "cancel", "label": "Decline to answer"}
      ]
    }
  ]
}
```

- `agent_state` — the 5-value virtual vocabulary the `process` family already
  publishes, under the key `state` there (`cli/_process_state.py:24-32,71`). The
  key is renamed here on purpose: this object also carries a list of requests,
  each with its own kind and status, so a bare `state` would read as *their*
  state. Same vocabulary, unambiguous name.

  **Derived from the live `AgentInfo`, never from the DB.** The family's
  `project_virtual_state` (`cli/_process_state.py:39-54`) takes a `ProcessRun`
  row and reads its persisted `awaiting_user_input` mirror; reusing it here
  would reintroduce exactly the drift §4.1 rejects. The service computes it
  instead: no agent → `dead`; else `awaiting_user_input` when
  `info.pending_requests` is non-empty; else `info.state`
  (`agent/states.py:104-115`). Values: `dead`, `starting`,
  `assistant_turn`, `awaiting_user_input`, `user_turn`. `AgentState` itself has
  only four values and never says `awaiting_user_input` — the runtime stays in
  `ASSISTANT_TURN` while the callback blocks, which is why the projection
  exists.
- `kind` — `question` or `out_of_scope`. Nothing else.
- Per-question fields:
  - `id` — the native Codex question id; for Claude, the **1-based index** into
    `tool_input["questions"]`. Claude has no ids. Nothing is cached between
    `pending-request` and `answer`: the answer service recomputes the index→text mapping
    from the stored questions, the same list the WS handler reads at
    `providers/claude_code/ws.py:401`.
  - `multi_select` — Claude only (`question.multiSelect`). Always `false` on
    Codex: the native tool never emits multi-select and the front-end drops the
    concept entirely (`codex/RequestUserInputBody.vue:20-22`).
  - `allows_free_text` — always `true` on Claude ("Other" is unconditional); on
    Codex it mirrors `question.isOther`, or a question with no options at all
    (`RequestUserInputBody.vue:181-188`). That second branch is defensive: the
    schema types `options` as `array | null`, but the native tool rejects a
    question without them (*"request_user_input requires non-empty options for
    every question"*, bundled binary 0.153.4), so in practice only `isOther`
    decides. Kept because the front-end keeps it.
  - `secret` — Codex `question.isSecret`. See §6.
  - `header`, `options[].label`, `options[].description` — verbatim.
- `actions` — `{action, label, accepts?}`. A question entry normally carries
  `answer` and `cancel`. It carries **`cancel` alone** when the request is
  structurally unanswerable — an empty `questions` list, or any question flagged
  `secret` (§6 turns both into a guaranteed rejection, so advertising `answer`
  would be a lie a script would act on). A flag passed to an action whose
  `accepts` omits it is `option_not_accepted`. `accepts` lists only the
  **answer-carrying** flags; `--request-id` and `--timeout` are accepted by
  every action and never appear there. Without that rule
  `answer cancel --request-id X` would be rejected, leaving a
  targeted cancel inexpressible when two questions are pending.

`--raw` adds `"raw": {"tool_input": …}` to **every** entry, question or not.
That is a consequence of §4.1: reading the live registry means the untouched
`tool_input` is there for the asking, with nothing **persisted** — no DB column
to size. On the local CLI route it does transit a status file on the way back
(`drop_requests_watcher` writes `<uuid>.status.json`, mode 0600, and the caller
deletes it); over MCP nothing touches the disk at all. So the size caveat the
persisted alternative had to design around — a Codex `fileChange` splices a
multi-megabyte diff into `tool_input` — becomes a caller's problem rather than
the schema's: ask for `--raw` on a session blocked on a large patch and you get
the patch, through a large temporary file if you are local.

Without `--raw`, the two entry shapes stay deliberately asymmetric, and
`age_seconds` marks the line: it is computed at read time from `created_at` and
emitted on question entries only. An out-of-scope entry exists to say *"something
is waiting and it is not mine"*, so it stops at what that sentence needs —
`request_id`, `created_at`, `kind`, `reason`, `tool_name`, and an empty
`actions`. Anything more would be a payload this command has no use for.

Note that a Claude question pending *also* carries `permission_suggestions` —
on the **SDK path**, where the `setMode` picker is injected for every tool but
`ExitPlanMode` (`claude_code/agent/agent.py:633-662`). A hybrid session builds
none: it keeps hook-native suggestions only, with no injected picker and no
synthesized entries (`hybrid/agent.py:525-536`). §3 keeps a non-degraded hybrid
`AskUserQuestion` in scope, so both cases occur. This
design **ignores them and never emits `updated_permissions`**. Adding them back
"because the field is there" is exactly what would reintroduce the security
problem this scope removes.

Exit 0 with an empty list when nothing is pending, and exit 0 with
`agent_state: "dead"` when no agent is attached. A non-match is not an error,
consistent with `session content`.

## 6. `answer` — input

```
twicc session <ID> answer <ACTION>
    [--request-id ID]     # default: the only answerable one; error if several
    [--answer 'ID=VALUE'] # repeatable
    [--timeout N]         # seconds to wait for the server's answer; exit 5 past it
```

Examples:

```
twicc session 019f4d27-… answer answer --answer 1=PostgreSQL
twicc session 019f4d27-… answer cancel
```

Two actions, and only two:

| Action | Claude `AskUserQuestion` | Codex `toolRequestUserInput` |
|---|---|---|
| `answer` | all questions answered → `PermissionResultAllow(updated_input={questions, answers})` · some → `PermissionResultDeny(<native clarify text>)` | `{"answers": {id: {"answers": [value]}}}` |
| `cancel` | `PermissionResultDeny(<fixed decline text>)` | `{"answers": {}}` (Codex reads an empty map as a cancel) |

Both deny texts are **fixed, server-side** (`providers/claude_code/ws.py:161-197`);
no caller-supplied message, hence no `--message` flag. The `questions` echoed in
Claude's `updated_input` are rebuilt from the stored pending request, never from
the caller — so a caller cannot forge the question set.

**Why `--request-id` exists.** Its obvious justification is the weaker one. Two
answerable questions at the same instant is rare: Claude's widget holds
several questions inside *one* request, and the web UI has never rendered two
question forms side by side — it shows the oldest pending request and counts the
rest in a badge (`stores/data.js:1053-1057`,
`SessionItemsList.vue:215-218,2145-2146`). Several pending requests on one
session are nevertheless ordinary — parallel tool calls each carry their own
(`agent/base_agent.py:474-476`) — they queue rather than coexist on screen.

The real reason is **stability between the two calls**. Without an id, "the only
answerable one" is resolved again at answer time; if an approval cleared or a
new request arrived in between, the caller answers something `pending-request` never showed
it. With the id, that case surfaces as `request_gone` instead of a wrong answer.
So: optional for a human at a terminal, expected from a script that read `pending-request`
first.

A `--request-id` matching no pending request of the live agent is
`request_gone`: "never existed" and "already resolved" are indistinguishable
from the outside, and the caller's next move is the same either way (re-run
`pending-request`).

**Answer keys and values.** `--answer 1=PostgreSQL`, split on the **first** `=`
— the value may contain more, which is reachable since free text is accepted.
Repeat `--answer` for multi-select (`--answer 1=A --answer 1=B`). The rule is on the **question**, not
the provider: repeating `--answer` for a question whose `multi_select` is
`false` is `multi_select_unsupported`. Since Codex is always single-select and
Claude is single-select unless `question.multiSelect`
(`claude_code/PendingRequestBody.vue:858-867`), that one rule covers both. A
value matching no option is free text: accepted when `allows_free_text`, else
`free_text_not_allowed`. Free text is **exclusive** on a multi-select question —
mixing it with option labels is `free_text_exclusive`, mirroring the UI, where
picking an option clears "Other" (`claude_code/PendingRequestBody.vue:741-742,
746-748`) and activating "Other" clears the selections (`:821-825`). Without that rule the
denormalizer would join them with `", "` into a value the widget can never
produce. An id absent from the
question list is `unknown_question_id`. The denormalizer then does the
provider-shaped work — Claude joins multiple values with `", "` and keys by
question **text** (the join at `claude_code/PendingRequestBody.vue:858-867`, the
keying at `:876-882`); Codex wraps the
single value in a list and keys by question **id**
(`codex/RequestUserInputBody.vue:193-205`).

**How many answers.** Three cases, matching the front-end's own three states
(`claude_code/PendingRequestBody.vue:583-593,911-917`):

| Answers given | Claude | Codex |
|---|---|---|
| all | submit | the answers map |
| some, ≥ 1 | the native `partial` / *clarify* deny | `missing_answers`, naming the ids |
| none | `missing_answers` — the UI offers no button here either; a caller that means "decline" says `cancel` | `missing_answers` |

A request whose `questions` list is **empty** is the one case where "all" and
"none" would both match. §3 admits it: the filter cannot rule it out, and it is
reported as a question with nothing to answer. It resolves as `missing_answers`
too — the rule reads *"at least one question, and every one of them answered"*,
so zero questions never submits. `cancel` stays available, and is the way out.

**Secret answers.** `secret_answer_unsupported` is raised **per request, not per
question**: submitting needs every question answered, so one `isSecret` question
makes the whole request unanswerable. `cancel` remains allowed on it — declining
carries no value, so nothing sensitive would transit. The refusal applies on
**every** route. The reason is the local CLI
route: there `transport.submit` writes the payload in cleartext to
`<data_dir>/drop-requests/<uuid>.json`
(`cli/_drop_request/transport.py:112-118`), and the front-end deliberately never
persists secret answers (`codex/RequestUserInputBody.vue:220-231`). Over MCP the
same transport writes nothing — it runs `execute_drop_payload` in memory
(`transport.py:119-127`) — so a per-route exception would be technically
possible. It is refused anyway: one rule for one command beats a capability that
silently depends on how the caller reached it. The native tool never sets
`isSecret` today; the branch exists defensively on both sides.

**The one security rule.** A session may not answer its own pending question
(`self_answer_refused`). The comparison is `caller_session_id` against the
target `session_id`, and the caller id comes from one of two sources:

- **CLI / Bash:** stamped client-side from `resolve_current_session()`, exactly
  like the share commands' `_with_caller` (`cli/share_mutation.py:9-24`) — which
  documents itself as *"a guardrail, not a security boundary"*. Same status
  here: a caller determined to forge it can.
- **MCP:** authenticated. The connection resolves the calling session from its
  signed token (`mcp/identity.py:52-61`) and pins it for the command
  (`forced_session_id`, set by `mcp/server.py:89`, read by
  `cli/_drop_request/whoami.py:73-75`). So the rule is enforceable on the
  surface that matters — the one an agent actually uses. (`external_caller`,
  `identity.py:83`, is the *other* case — an OAuth connection with no calling
  session — and `whoami.py:71-72` returns `None` for it before
  `forced_session_id` is ever read.)

Why the rule is needed at all, on either route: Claude issues
parallel tool calls, each with its own pending request and Future
(`agent/base_agent.py:436-438,474-476`), and `mcp__twicc__*` tools short-circuit
to an immediate allow before any pending is created
(`claude_code/agent/agent.py:693`) — so an agent blocked on `AskUserQuestion`
can reach this command in the same turn. Auto-answering would not escalate any
privilege, but it would let an agent decide for the human, which is the whole
point of the widget. Subagents need no separate rule: they are never a CLI
target (`cli/_drop_request/session_lookup.py:77-84`) and a Claude subagent's
questions surface as the parent session's pending request anyway.

Nothing beyond that rule is gated: no synced setting, no trust re-resolution,
no spawn-subtree scope. §2 explains why the full-scope gate is not warranted
here — and what this command does grant, which is not nothing.

## 7. Errors, races, exit codes

**Argument validation is exit 1; `64` is not used.**

`64` is tempting because `process <ID> wait` uses it for a bad `--timeout`
(`cli/process_wait.py:55-59`, via `emit_error(..., code=64)` at
`cli/_output.py:69-81`). But it is an **outlier confined to two files**, not a
convention: repo-wide, `code=64` has four call sites — `cli/process_wait.py:58`,
`:64`, `:75` and `cli/update_project/command.py:313`. Every command that
validates the same kind of argument uses 1: `cli/session.py:582-585` (the closest
sibling — same `session <ID>` group, same bad-timeout condition),
`cli/processes_wait.py:93`, `cli/processes_stop.py:69`,
`cli/sessions_stop.py:54`, `cli/_batch_runner.py:101`.

| Exit | Condition |
|---|---|
| 0 | answered · or `pending-request` succeeded (empty list and dead agent included) |
| 1 | validation: the five `lookup_session` guards (`session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`, `unknown_provider` — `cli/_drop_request/session_lookup.py:57-105`), an ACTION word outside `{answer, cancel}`, a malformed `--answer`, a non-positive `--timeout` |
| 2 | no live backend, on **both** commands |
| 3 | rejected by the server |
| 4 | service failure |
| 5 | timeout |

A stray token after the read command (`pending-request foo`) never reaches the
body: Typer rejects it and Click's `UsageError.exit_code` is 2. An unknown
ACTION word is different — `answer` takes it as a positional argument, so the
command body sees it and refuses with exit 1, per the table above.

Both commands now share the same failure surface, because §4.1 put both on the
drop-request transport: `transport.ensure_server_available` gives them exit 2
for free, and both can come back `rejected`, `failed` or timed out. That
uniformity is a small gain of the decision — a script driving the pair tests one
set of conditions, not two.

Rejection codes (exit 3). `provider_disabled` belongs to **both** commands: it
is raised server-side by `_lookup_session_for_update`
(`core/services/session_update.py:141-146`), the shared guard §8 puts at the top
of each service — not client-side. `validate_provider`
(`cli/_drop_request/validation.py:43-63`) is a different check, reached only by
`create-session` and the settings resolver; neither command here calls it.
For `pending-request`, emptiness is never a rejection: no live agent and no
pending request are both exit 0 (§5). For `answer` they are — see `agent_dead`
and `no_pending_question` below. Everything after `provider_disabled` belongs to
`answer` alone:

`provider_disabled` (shared, above), `no_pending_question` (the agent is live and holds no **answerable** question —
it may still hold out-of-scope entries, which `pending-request` lists), `agent_dead`,
`request_gone` (the `--request-id` matches no pending request at all: already
resolved, or never existed), `ambiguous_request` (several answerable questions,
no `--request-id`), `not_a_question` (the `--request-id` names an out-of-scope
entry),
`missing_answers`, `unknown_question_id`, `free_text_not_allowed`,
`free_text_exclusive`, `multi_select_unsupported`, `secret_answer_unsupported`,
`option_not_accepted`, `self_answer_refused`.

Races:

1. **Answered meanwhile** — a human clicked in the UI, or another caller won.
   `BaseAgent.resolve_pending_request` returns `False`
   (`agent/base_agent.py:471-492`); the service turns that into `request_gone`
   rather than a silent success. The same mechanism makes two concurrent
   `answer` calls safe: the Future resolves once, the loser gets `request_gone`.
   The hybrid override is the exception to watch: it does return `False` for an
   already-gone pending (`hybrid/agent.py:661-668`), but it returns `True`
   **before delivery** — the status-file write is fire-and-forget in a task
   (`:687-690` → `_finalize_gui_answer`). So a `True` from it means "accepted",
   not "delivered", which is why the `hybrid_terminal` degradation must be
   filtered out upstream (§3) rather than relied on to fail.
2. **Agent dead before the call** — `agent_dead` for `answer`;
   `agent_state: "dead"` with an empty list for `pending-request`.
3. **Agent dies between lookup and resolve** — the pending Futures are
   cancelled on kill (`base_agent.py:460-470`) and a cancelled Future is
   `done()`, so `resolve_pending_request` returns `False` → `request_gone`.
4. **A read that ages between the two calls** — `pending-request` reads the live
   registry, so it is accurate at the instant it answers, but the caller acts on
   it a moment later. §4.1 removes the *stale mirror* a persisted projection
   would have added; it cannot remove the caller's own round-trip. Self-
   correcting either way: `answer` re-validates against the live agent and
   returns `request_gone`.
5. **A hybrid session's answer is accepted before it is delivered.** §3 keeps a
   *non*-degraded hybrid `AskUserQuestion` in scope, and for it
   `HybridClaudeAgent.resolve_pending_request` returns `True` as soon as it pops
   the pending (`hybrid/agent.py:686-690`); the status file the polling hook
   reads is written afterwards in a fire-and-forget task
   (`_finalize_gui_answer`). A write failure is logged server-side and the
   pending is already gone. So on a hybrid session, exit 0 means **accepted**,
   not **delivered** — the same guarantee the web UI gives, since it goes
   through the identical call. Stated rather than fixed: closing the gap means
   changing the hybrid contract, which is out of scope here.

## 8. The refactor

Small, because the scope is small:

```
core/services/pending_question.py
    read_pending_requests_from_payload(payload)      # kind session:pending_requests
        → lookup session + provider (_lookup_session_for_update, shared guards)
        → registry.get_agent_info() → normalize every pending request (§3, §5)
    answer_pending_question_from_payload(payload)    # kind session:answer_pending_question
        → lookup session + provider (_lookup_session_for_update, shared guards)
        → self-answer refusal (§6)
        → registry.get_agent_info() → find the PendingRequest by request_id
        → refuse anything the §3 filter excludes, incl. the §1 disguised
          MCP approval (`not_a_question`)
        → helpers.build_question_response(pending, answer) → wire response
        → manager.resolve_pending_request(session_id, request_id, response)

providers/claude_code/pending_question.py   # normalize + denormalize
providers/codex/pending_question.py         # normalize + denormalize
```

Reached through `BaseProviderHelpers` (`providers/helpers.py:274`), the existing
per-provider dispatch point. No import cycle:
`core/services/{send_message,session_update,session_creation}.py` already import
`twicc.providers.helpers` at module level, and `providers/helpers.py` imports
only `core.enums` and `pricing`.

What moves out of the WebSocket layer:

- Claude: the `ask_user_question` branch of
  `ClaudeCodeWSHandler._handle_pending_request_response`
  (`providers/claude_code/ws.py:384-420`) plus `_build_clarify_message`
  (`:167-197`) and `_QUESTION_CANCEL_MESSAGE` (`:161`). About 40 lines. The
  approval branch stays where it is — this design does not touch it.
- Codex: `CodexWSHandler._build_request_user_input_response`
  (`providers/codex/ws.py:346-374`) is **already a pure function** over the
  payload; it moves as-is and the handler calls it from its new home.

The WS handlers then delegate, so there is exactly one implementation per
provider. The web UI keeps sending its current payloads: the extracted
denormalizer either accepts both shapes, or the WS handlers translate into the
normalized answer first.

Also worth updating once shipped: the `send-message` refusal text still says
"Resolve the pending dialog before sending"
(`core/services/send_message.py:151`) — it should name this command, for the
question case at least.

## 9. Decisions

**Settled — transport (§4.1), user, 2026-09-18.** The read lane in the existing
drop transport, reading the live registry. No persistence, no migration. Cost:
a `fetched` terminal status declared in four shared files — one of which,
`output.py`, is where `build_final` needs its new branch — plus the handler
table.
The persisted-projection alternative and the two reasons it lost are in §4.1;
they are kept rather than deleted so the choice can be re-examined without
redoing the analysis.

**Settled — listings, user, 2026-09-18: no.** Not deferred, not "later" —
`processes` and `sessions` will not carry a summary of the waiting question.

It would have been the one feature turning *"what is waiting everywhere?"* into
a single call instead of one call per blocked session, so the appeal was real.
Two reasons it loses. First, the transport decision makes it expensive rather
than free: both listings are DB reads and the database knows nothing about
pending requests, so the summary needs either its own live-registry round-trip
per row or the persisted column §4.1 rejected — and reviving that column here
would reintroduce the mirror for a convenience. Second, the shape is wrong:
those serializers are shared (`cli/_process_state.py`) and feed every session
listing, so one command's payload would grow on everyone's.

The capability stays reachable: `processes --state awaiting_user_input` already
answers *which* sessions are blocked, and `pending-request` answers *on what*,
one call each. The web UI is unaffected — it never reads the DB for this; live
`process_state` frames carry the full pending requests
(`agent/states.py:150-182`).

## 10. Out of scope

- Every non-question pending request: reported as `out_of_scope`, never
  answered (§3). The full-scope design is
  `2026-09-18-pending-request-cli-design.md`, shelved.
- Answering a Codex MCP tool approval wearing a question's form: **detected and
  excluded** (§1), reported as `out_of_scope` with
  `reason: "mcp_tool_approval"`. Widening the detection to the option-label set,
  and pinning `features.tool_call_mcp_elicitation`, are both recorded and both
  rejected in §1.
- Codex `isSecret` questions: refused on every route (§6), including the MCP
  route where the transport writes nothing to disk. One rule per command beats a
  capability that depends on how the caller connected.
- Claude's "approve with changes" (`updated_input` edits) and
  `permission_suggestions` / `updated_permissions`: never sent (§5).
- Delivery confirmation on hybrid sessions: exit 0 means the agent accepted the
  answer, not that the tmux CLI consumed it (§7, race 5). Closing that gap means
  changing the hybrid contract, which the web UI shares.
- Registering the read as read-only: its registry path must join
  `COOKIE_READONLY_COMMANDS` (`rpc/permissions.py:52-80`, fail-closed by design,
  `:27-30`), which also feeds `MCP_READ_ONLY_PATHS` (`mcp/tools.py:39-42`).
  Without the entry it ships advertised as a mutation and is refused by
  `batch_read`. `answer` stays out of both, deliberately.
- A summary of the waiting question in `processes` / `sessions`: **decided
  against** (§9), not postponed.
- Documentation duties at implementation time, not before: `SKILLS-AND-CLI.md`,
  the `twicc-session` skill, a `plugin.json` version bump (minor — new
  commands), and `AGENTS.md` if `CLAUDE.md` gains anything.
