# Timeline Actor and Origin — Design

**Status:** draft, reviewed (round 7 fixes applied), not committed. Decisions taken with the owner 2026-10-14.
**Date:** 2026-10-14
**Scope:** for every timeline event caused by a request, record who made the call and through which path. This is a delta on the implemented timeline. It does not edit `2026-10-13-session-timeline-design.md`; it supersedes the statements listed in section 8.
**Baseline:** the system as implemented on branch `feature/session-timeline` (migrations up to `0157`).

---

## 1. Goal and scope

Today every event caused by a human request has `actor=user`, whatever the entry path (UI, CLI, RPC, MCP). The reserved `data.via` key is never filled. A future timeline display cannot tell a click in the UI from a CLI call by an agent.

**Goal.** Each event caused by a request records the **path** (`via`), the **identity** of the caller when one exists (`actor_ref`), and a **snapshot of its display name** (`actor_name`). A display then needs no extra lookup.

**In scope.** Three new columns, the mapping from entry path to values, the propagation of the origin to the writers, the rules for process events.

**Out of scope.** Section 12.

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Request** | A call made by a human, an agent or an external client that causes a change: a UI action, a CLI command, an RPC call, an MCP tool call. |
| **Origin** | The tuple `(actor, via, actor_ref, actor_name)` of the request that caused an event. |
| **Carrier** | The request-scoped holder of the origin that services read when they write a timeline event (section 6). |
| **System set / automatic event** | Every event whose actor is `system`, with NULL origin fields. It includes the automatic process events of section 7 (cron restarts, restarts and kills for pending settings, hybrid switch kills, hybrid boot adoption, timeouts, shutdown, crashes, natural exits, lost-at-boot rows) and also `backend.started` / `backend.ended`, `settings.changed` with reason `model_retired` or `switch-hybrid`, `session.created` for sync or external sessions, and backfill rows. |
| **Natural end** | An `awaiting_input.ended` closed outside a `process.ended`. This includes a user answering the request: it is **not** attributed to that user and stays `agent` with NULL origin fields, as decided in O29 (which restates the O14 rule). A future change that attributes it would be a new decision. |
| **Not request-caused, unchanged** | Events that no request causes but that keep their actor of today: non-user titles and the standalone process-hook rows (`turn.started`, `turn.ended`, `awaiting_input.started`, natural `awaiting_input.ended`, see Natural end). Actor `agent`, NULL origin fields. They are not automatic events. |
| **System marker** | An explicit `system` origin set on a code path, distinct from "no origin set". It tells the writer that the event is automatic and that the default must not apply. |
| **Clean context** | Exactly the system marker: a context in which the carrier holds the explicit `system` origin, never an unset carrier. |
| **Explicit actor** | An actor passed by the caller of the recording service, as system writers do today. |

## 3. Data model

Three nullable columns are added on `TimelineEvent`, next to `actor`. The `actor` column keeps its four values (`user`, `agent`, `system`, `external`).

| Column | Type | Meaning |
|---|---|---|
| `via` | `CharField(20)`, null | The path: `ui`, `cli`, `rpc`, `mcp_internal`, `mcp_external`. NULL for an automatic event or a not-request-caused event. |
| `actor_ref` | `CharField(255)`, null | The identity of the caller: a session id, an API token id or an `McpConnection` id. NULL when the path has no identity. Not a foreign key: the referenced row may be deleted later. The width is 255, the same as a session id (the widest identity it holds; connection ids are at most 64 characters). A longer value is truncated, never rejected. |
| `actor_name` | `CharField(80)`, null | A snapshot of a display name, taken when the event is written. Today only the external MCP fills it, with the `McpConnection` name (the model limit is 80 characters, so the column is wide enough). A longer value is truncated, never rejected. |

- **NULL `via` has two meanings.** (1) "Origin unknown": every row written before this change, backfilled rows included. (2) "Automatic or not request-caused": rows written after this change by the system set or by the not-request-caused group. NULL alone does not tell them apart. Old rows have actor `user`, `agent` (process hooks, titles) or `system`, as written then, and backfilled rows fit both meanings. The spec names no cutover instant. For rows written after this change, an `agent` row with NULL `via` is a non-request row. Old `agent` rows with NULL `via` are not: the inner `turn.ended` and `awaiting_input.ended` rows closed with a manual or archive `process.ended` were `agent` although a stop request caused them. A display must not infer an origin from NULL alone; `actor` and `recorded_at` are the only hints. No consumer may read a NULL `via` as "automatic" without that check.
- The three columns are not part of the `data` payload and are not validated by the payload registry. `via` takes only the five listed values. Recording never raises, so an invalid `via` falls back to NULL: the event is never lost. The `actor`, `actor_ref` and `actor_name` of the same event keep their resolved values.
- No index. The columns are read with the row, never filtered on in v1.
- **Every write path must carry the three columns:** the single-event path (`record_event[_sync]`), the batch path (`record_events_bulk_sync`, `insert_event_rows`) and the batch fallback. Only the shared batch insert is built from the model metadata; the single-event path and the batch fallback build their rows with explicit fields, so each must be extended. The backfill insert writes NULL.

## 4. Mapping

| Entry path | `actor` | `via` | `actor_ref` | `actor_name` |
|---|---|---|---|---|
| Web UI (WebSocket and REST); also the **default** when a request-caused event is written and no origin is known | `user` | `ui` | NULL | NULL |
| CLI run by a human (the caller cannot be traced to an agent session) | `user` | `cli` | NULL | NULL |
| CLI run by an agent (the caller resolves to a TwiCC session) | `agent` | `cli` | that session id | NULL |
| Internal MCP (per-session token) | `agent` | `mcp_internal` | the calling session id | NULL |
| Local MCP endpoint authenticated by an API token (no session) | `user` | `mcp_internal` | the token id | NULL |
| External MCP (approved `McpConnection`) | `external` | `mcp_external` | the connection id | the connection name at event time |
| RPC call authenticated by an API token, including a CLI in `--remote` mode | `user` | `rpc` | the token id | NULL |
| RPC call with no token record (instance without password or token, full scope) | `user` | `rpc` | NULL | NULL |
| Automatic event (including every `process.started` not caused by a request, section 7), marked by the system marker | `system` | NULL | NULL | NULL |

The cookie-scope RPC is read-only and writes no timeline event, so it needs no row.

**Events that are not caused by a request keep their actor of today.**
- A `title.changed` whose `source` is not `user` (`auto`, `agent`) comes from compute, watcher or Codex title jobs, not from a request. Its actor stays `agent` as today, `via` NULL, no ref. Nothing is reclassified. Only a `user`-sourced title takes the request origin.
- The standalone process-hook rows (`turn.started`, `turn.ended`, `awaiting_input.started`, and a natural `awaiting_input.ended`) keep actor `agent` as today, `via` NULL, no ref.

## 5. Origin resolution per entry path

| Path | What identifies the caller |
|---|---|
| Web UI, WebSocket (`/ws/`) | Nothing: the authenticated browser user. Origin is the UI one. |
| Web UI, REST | Same. |
| CLI, file mode (a real CLI process) | The CLI side resolves the caller with the existing mechanism (`resolve_current_session()`: the TwiCC session whose live agent process is an ancestor of the CLI process). A hit gives the agent origin, no hit the human one. |
| CLI, `--remote` mode | The server only sees an RPC call with a Bearer token. It is recorded exactly like an RPC call (`user` / `rpc` / token id). No local agent identity is forwarded (section 11). |
| RPC `/rpc/<command>` | The API token record. The auth middleware finds it but only stamps the scope and records the token usage, so the token id is not available to the services today: the RPC entry point must expose it to the carrier. With no token record, `actor_ref` is NULL. |
| Internal MCP | The per-session token, which identifies the calling session. A plain API token on the same endpoint (no session) gives the token id. The endpoint's authorization returns only a boolean or a session id today, so this entry point must also expose the token id to the carrier. |
| External MCP | The approved `McpConnection` of the request: its id and its current name. |
| Drop-requests watcher | The origin written in the drop file (section 6). A file with no origin falls back to the default. |

Rules:

- **Default.** The default `user` / `ui` applies **only** to a request-caused write that reaches the writer with no origin at all, because its entry point did not set one (for example a drop file from an old CLI). It never applies to an automatic event. This keeps today's behaviour (`actor=user`) and adds `via=ui`.
- **Automatic starts are marked, not unset.** Every automatic start carries the system marker, so that it never falls to the default. The paths that set the marker are: the follow-ups spawned from the state-change funnel (cron restart, pending-settings restart); hybrid boot adoption (which has no carrier at all); boot-time cron restarts; and the restart performed by **the same operation** that issued an `apply-settings` kill, whatever the call context (inline in a user's send, or through the pending-settings path). The rule is bound to that operation: a later independent start (for example the next day's send, after an `apply-settings` kill that restarted nothing) takes the origin of its own send. A hybrid switch sets no marker for its start: only its kill is `system`; the start that follows is caused by a send and takes the origin of that send (section 7).
- **Deliberate stops resolve at request time.** A deliberate stop resolves its origin when the stop is requested, default included (`user` / `ui` if the carrier is empty), and stores it on the agent. The DEAD hook never reads the carrier for a deliberate stop reason; it reads only the captured origin on the agent. For a non-deliberate reason (error, natural exit, timeout, shutdown, `apply-settings`, `switch-hybrid`) the origin is `system` with NULL fields and the carrier is never read.
- **Precedence.** Two cases beat the carrier. (a) An explicit actor given by a system or non-request writer (boot reconciliation, backfill, non-user titles, standalone process-hook rows): its origin fields (`via`, `actor_ref`, `actor_name`) are all NULL. (b) An explicitly **captured full origin** (a stopped process, a pending title, creation attributes): all four fields are kept exactly as captured. The carrier is read only by a write made directly in the awaited chain of a request. **Deferred writers never read the carrier:** the pending-title flush and its retry and verify tasks, and the process DEAD hook. They run inside the state-change funnel (where the carrier holds the system marker) or in a task with a stale context, so a literal read would record a user title as `system`. They use the captured origin, else the explicit default `user` / `ui`.
- **In-process entry points win.** The RPC, internal MCP and external MCP run the commands in the backend process through the same transport, with no drop file. An origin already set by such an entry point always wins. The CLI-side resolution (process ancestry) runs only in a real CLI process, never in the backend. In backend mode the common place that fills the drop payload forwards the carrier unchanged. So an external-MCP call never becomes `user` / `cli`, an internal-MCP call never becomes `agent` / `cli`, and an RPC call never walks the backend's own ancestry.
- **Resolution is cheap.** The CLI resolves its caller once per invocation, not once per event.
- A human typing `twicc ...` inside an agent's terminal is attributed to that agent (section 11).

## 6. Propagation

The origin is held in a `contextvars.ContextVar`, set at every entry point and read by the services that write timeline events. This is the same mechanism as the existing `forced_session_id` and `external_caller` variables.

**Requirements.**

1. **Every entry point sets the origin before calling a service:** the WebSocket handlers, the REST views, the RPC views, the MCP server (internal and external), the MCP batch runtime, and the drop-requests watcher. MCP batch children are created outside the request task and set their own caller context: they must carry the origin of the batch call.
2. **Drop files carry the origin, in one common place.** The CLI side writes the origin into the drop-file payload for all drop kinds. The watcher re-establishes it before calling the service handler. No kind has its own origin code. A new kind gets the origin with no extra work.
3. **Visibility rules (tested facts).** The variable is visible in `asyncio.to_thread`, in tasks created after the `set()`, and in `run_coroutine_threadsafe` called from a `to_thread` worker. It is **not** visible in tasks created before the `set()` (an agent's long-lived reader or message-loop task keeps the context of the request that started it), nor from a plain `threading.Thread`. Any write that crosses one of these boundaries must capture the origin explicitly.
4. **Batch operations** use one origin for the whole batch. The bulk archive passes the request origin to its single batched insert.
5. **Deferred writes.** The deferred title flush writes after the request is gone. It carries the origin captured when the title was stored, kept with the pending title. With none captured, the explicit default `user` / `ui` applies. The flush, its retry and verify tasks never read the carrier. The captured origin must survive the Codex draft-to-canonical session id re-key, which moves the pending title field by field: a missed field would silently revert the origin to the default.
6. **Created sessions.** `session.created` and its initial flag rows (`hidden.started`, `muted.started`) are written by the watcher or the DB writer, long after the creating request, and cannot read the carrier. One rule, by `data.origin`. `twicc` means only that a TwiCC process exists for the session when the watcher creates the row; it does not mean that creation attributes were stashed (they can be absent). When `data.origin` is `twicc`, `session.created` **and** its flag rows take the creating request's origin when one was captured with the pending creation attributes (a UI creation gives `user` / `ui`; an agent creating a hidden session through CLI or MCP gives `agent` / `cli` or `mcp_internal` / the creating session id). When `data.origin` is `twicc` but no creation origin was captured, both take the **default** (`user` / `ui`). For any other session (`data.origin` is `sync` or `external`, backfill), both are `system` with NULL origin fields. Today `session.created` is always `system`; it changes to the request origin for TwiCC-created sessions (section 8). The pending creation attributes are also re-keyed field by field for Codex sessions (draft id to canonical id); the creating origin must be forwarded with them, or a missed field silently reverts it to the default.
7. **Long-lived tasks never read a request origin by accident.** Tasks created during a request keep its context for their whole life: the agent's reader and message-loop tasks, the deferred pending-title flush and its retry and verify tasks, and the state-change callback funnel. None may take the starting request's origin as the cause of a later event. Each such path either runs with a clean context, meaning the system marker where the event is automatic (the callback funnel, automatic follow-ups), or must not read the carrier at all (agent tasks, whose inherited context is the starter's stale one) or uses an explicitly captured origin (the pending title, the stop requested on the agent, the creation attributes).

**Writers that need the origin** (those that hard-code `TimelineActor.USER` today): bulk archive, `persist_session_settings` default, `apply_session_archived_change`, `apply_session_pinned_change`, `apply_session_mute_on_user_turn_change`, the hide/unhide flag helper, the Claude permission-mode update (`providers/claude_code/ws.py`), `session.created` and the flag rows written by `record_session_created`, `record_title_change` (user-sourced titles only), and the process hooks (`agent/timeline_hooks.py`). The plan must grep `TimelineActor.USER` again to confirm the list.

## 7. Process events

| Event | Origin |
|---|---|
| `process.started`, caused by a request | The origin of the request that caused the start. The single call site (`_register_and_start`) is inside the awaited chain of the request, so the carrier is correct there. |
| `process.started`, not caused by a request | `system`, `via` NULL, no ref: runtime and boot cron restarts, hybrid boot adoption, and **the restart performed by the same operation that issued an `apply-settings` kill**, whether it runs inline in the awaited chain of a user's send or through the pending-settings path, and even when it re-sends a parked user text. The rule is **by cause, bound to that operation, not by call context**: that restart is never attributed to the request, so the default does not apply to it. |
| `process.started`, independent of any kill | The origin of the send that causes it. This holds after an `apply-settings` kill that restarted nothing (settings-only change in USER_TURN with no text and no crons, or a pending-settings kill with nothing parked): a later send takes its own origin. It also holds after a hybrid switch, with or without a kill (an idle session kills nothing): the start is caused by a send from the UI, an agent through CLI or MCP, or an external client, and takes that send's origin. |
| `process.ended`, stop asked by a request (manual stop, force stop, archive) | The origin of the request that asked for the stop. |
| `process.ended` with reason `apply-settings` | `system`, no origin, whatever the context of the request that changed the settings. |
| `process.ended`, anything else | `system`, no origin (list below). |

**Stale context.** The DEAD transition is often written from the agent's own long-lived task. Its inherited context is the one of the request that **started** the session, not of the one that stopped it. Reading the carrier at that point would attribute the end to the wrong caller. Therefore:

- The origin is **captured on the agent object at the moment the stop is requested**, next to the existing kill reason (`kill_reason`), and resolved at that time: if the carrier is empty, the default `user` / `ui` is captured. The DEAD hook, running in the agent's own task with the starter's stale context, never reads the carrier for a deliberate reason. If a deliberate reason has no captured origin, the DEAD hook uses the default `user` / `ui`.
- **Reason and origin are always paired.** Two deliberate stops can overlap (a soft stop in its grace window then a force kill; an archive after a stop). The pairing holds at **every** assignment of the stop reason (the provider agents, the hybrid agent, the Codex agent and the manager's hard kill): wherever the reason is assigned or re-assigned, the origin is assigned with it. A soft stop in its grace window followed by a force kill that re-assigns the reason leaves the surviving reason and the origin from the same request (last writer wins). A later non-request reason (error, shutdown, timeout, `apply-settings`) clears the origin, so an automatic cause never keeps a stale human origin.
- A **new agent object never inherits a previous origin.**
- The state-change callback funnel (`BaseAgent._notify_state_change`) runs automatic follow-ups (cron restart, pending-settings restart). It must give them a **clean context**, which is the system marker (section 2): the restart is explicitly automatic, so it neither reads the origin of whoever triggered the previous transition nor falls to the default. The clean context is the mechanism for this funnel only; the `apply-settings` restart is decided by its cause (above), not by context, and sets the system marker too. Hybrid boot adoption and boot-time cron restarts set the marker as well.

**Scope of the system marker.** The marker holds for the automatic start or stop only. It never leaks to later writes of the enclosing request: an inline `apply-settings` restart inside a user's send must not turn the send's other writes (the message, settings changes, other events) into `system`. The marker is restored to the request origin when the automatic start or stop returns.

**Hybrid switch.** Only the `switch-hybrid` kill is `system`, although a UI click triggers it: the reason is not a deliberate one, so the ordinary rule applies. The `process.started` that follows is caused by a send and takes that send's origin, like any request-caused start, including when no kill happened. O33 is superseded for the start by O34.

**Automatic, `system` with no origin:** cron restarts (runtime and boot), restarts and kills for pending settings (`reason=apply-settings`), hybrid switch kills, hybrid boot adoption, timeouts, shutdown, crashes (`error`), natural exits, and rows lost at boot (`lost`).

A deliberate stop keeps the actor rule of today (`DELIBERATE_STOP_REASONS`): the actor is `user` / `agent` / `external` from the captured origin instead of a fixed `user`. A non-deliberate reason stays `system`.

**Inner rows.** The `turn.ended` and `awaiting_input.ended` rows closed together with `process.ended` take the same `actor`, `via`, `actor_ref` and `actor_name` as that `process.ended`, because the stop caused them. Today they are `agent`. This is a behaviour change (section 8). The `caused_by` link of an archive-driven end is unchanged.

## 8. Interaction with existing behaviour

**Superseded statements** of `2026-10-13-session-timeline-design.md` (only these two):

- §3, row `actor`: "in v1 every human-initiated change uses `user`, whatever the entry surface" and the reserved `data.via` (`ui` / `cli` / `mcp`).
- §10, "Deferred to a future scope": "`actor` precision and `data.via` plumbing". It is done here. The `awaiting_input.ended` `outcome` item is dropped for good (section 12).

**Changes to the implemented behaviour** (the first design does not state them; the code does):

- Process hooks, actor of each kind: the inner rows closed with `process.ended` are no longer fixed to `agent`; they follow the `process.ended` row (section 7). `process.started` is no longer fixed to `user`: an automatic start is `system`. A user-sourced title, the flags and the stop take the request origin. The `process.started` of the restart performed by the same operation as an `apply-settings` kill is `system`, even inline in a request. A hybrid switch start follows its send's origin. Nothing else is reclassified: non-user titles and standalone process-hook rows keep `agent`.
- The `session.created` row: its actor is no longer always `system`. For a session created through TwiCC it takes the creating request's origin, and so do its initial flag rows (previously `user`). Other sessions stay `system`.
- Settings changes (the `persist_session_settings` callers and the Claude permission-mode update) move from a fixed `user` to the request origin, which can now be `agent` or `external`.
- The initial flag rows (`hidden.started`, `muted.started`) of a session whose `data.origin` is not `twicc` are `user` today. They become `system` with NULL origin fields.
- The registry no longer needs to accept `data.via`. The plan decides whether it is rejected or ignored; no caller may rely on it.

**Unchanged guarantees.**

- Recording never raises and never blocks or undoes the action it describes. A failure to read or resolve the origin falls back to the default (`user` / `ui`) only for request-caused writes. An event marked `system`, or written by a system writer, keeps `system`. Recording never raises either way; a failure is logged.
- Order is by `id`.
- `session_id`, `project`, `dedupe_key` and all payload rules are unchanged.
- **An `ended` row does not inherit the origin fields from its `started` row.** It records its own cause: the person who stops a process is rarely the person who started it. The "ended repeats started" rule of design §3 applies to payload data only.

## 9. Compatibility and rollout

- One new migration, numbered after `0157`, adds the three columns as nullable with no default. The owner runs it himself.
- Existing rows keep NULL. No backfill of old rows (the origin of past events cannot be recovered).
- The incremental backfill is unchanged and writes NULL.
- No API or UI change in this scope: nothing reads the new columns yet.

## 10. Decisions

| # | Decision |
|---|---|
| O1 | Three nullable columns (`via`, `actor_ref`, `actor_name`) next to `actor`, not a `data` key. `actor` keeps its four values. |
| O2 | `via` values: `ui`, `cli`, `rpc`, `mcp_internal`, `mcp_external`. NULL for automatic events. |
| O3 | The mapping of section 4 is final. |
| O4 | Carrier: a `ContextVar`, set at every entry point; drop files carry the origin from one common place for all kinds; services read the carrier; an explicit actor wins. |
| O5 | A request-caused write that reaches the writer with no origin at all gets the UI origin. Automatic events never fall to it (O25). |
| O6 | `process.ended` takes the origin of the request that asked for the stop, captured on the agent object at that moment. `process.started` takes the origin of the request that started it. |
| O7 | An `ended` row records its own cause and inherits no origin field. |
| O8 | `actor_name` is a snapshot. `actor_ref` is not a foreign key; a deleted token or connection stays readable as an id. |
| O9 | Old rows are not backfilled. |
| O10 | (after review round 1) Every `process.started` not caused by a request is `system`, `via` NULL, no ref: cron restarts, hybrid boot adoption, pending-settings restarts (even re-sending a parked text). Refined by O19 and O25. |
| O11 | (after review round 1) An origin set by an in-process entry point always wins. CLI-side process-ancestry resolution runs only in a real CLI process. In backend mode the common drop-payload place forwards the carrier unchanged. |
| O12 | (after review round 1) A `--remote` CLI call is recorded like an RPC call. An RPC call with no token record is `user` / `rpc` / NULL ref. The cookie-scope RPC is read-only and writes nothing. |
| O13 | (after review round 1) A call on the local MCP endpoint authenticated by an API token is `user` / `mcp_internal` / the token id. |
| O14 | (after review round 1) Titles whose source is not `user` and the standalone process-hook rows are not request-caused: they keep actor `agent`, `via` NULL, no ref. Nothing is reclassified. |
| O15 | (after review round 1) Inner rows closed with `process.ended` take its actor, via, ref and name. |
| O16 | (after review round 1) The creating request's origin travels with the pending creation attributes. `session.created` and its flag rows carry it. A session found without a TwiCC creating request gets `system`. |
| O17 | (after review round 1) The deferred title flush uses the origin captured when the title was stored; none captured means the default. |
| O18 | (after review round 1) An invalid `via` falls back to NULL; the event is never lost. |
| O19 | (after review round 2; **amended by O34**: bound to the same operation) The rule for `process.started` and `process.ended` after an `apply-settings` kill is by cause: `system`, whatever the call context, for the restart performed by the operation that issued the kill; a later independent start takes its own send's origin. Updates O10. The clean context remains the mechanism for the callback funnel only. |
| O20 | (after review round 2) `session.created` and its flag rows take the creating request's origin when `data.origin` is `twicc`; otherwise `system`. Updates O16; refined by O26. |
| O21 | (after review round 2) An explicit actor of a system or non-request writer gives NULL origin fields; an explicitly captured full origin keeps all four fields. |
| O22 | (after review round 2) The RPC and local-MCP entry points must expose the API token id to the carrier; it is not available today. |
| O23 | (after review round 2) Reason and stop origin are paired: the last deliberate stop request sets both; a later non-request reason clears the origin. |
| O24 | (after review round 2) Widths: `via` 20, `actor_ref` 255, `actor_name` 80; longer `actor_ref` and `actor_name` values are truncated, never rejected. |
| O25 | (after review round 3) Automatic starts carry an explicit system marker (an explicit `system` origin, distinct from "no origin set"). It is set by the state-change funnel follow-ups, hybrid boot adoption, boot-time cron restarts and the restart performed by the same operation as an `apply-settings` kill (bound to that operation, see O34). "Clean context" means exactly this marker. The default applies only to request-caused writes with no origin at all. Updates O5, O10, O19. |
| O26 | (after review round 3) `data.origin` `twicc` means a TwiCC process exists for the session, not that attributes were stashed. With no captured creation origin, `session.created` and its flag rows take the default `user` / `ui`. Other origins stay `system`. Updates O20. |
| O27 | (after review round 3) A deliberate stop resolves its origin at request time, default included, and stores it on the agent. The DEAD hook never reads the carrier; a non-deliberate reason gives `system` with NULL fields. |
| O28 | (after review round 3) "Automatic" means exactly the `system` set; non-user titles and standalone process-hook rows are "not request-caused, unchanged" (`agent`). |
| O29 | (after review round 4) The system set is every event whose actor is `system`. A user-answered `awaiting_input.ended` is a natural end and stays `agent` with NULL fields. NULL `via` has two meanings, told apart by `actor` and age. |
| O30 | (after review round 4) The captured origins of the pending title and the pending creation attributes survive the Codex draft-to-canonical re-key. |
| O31 | (after review round 4) The system marker is scoped to the automatic start or stop and never leaks to later writes of the enclosing request. A deliberate stop with no captured origin uses the default in the DEAD hook, never the carrier. |
| O32 | (after review round 5) Deferred writers (pending-title flush with its retry and verify tasks, the DEAD hook) never read the carrier; they use the captured origin, else the default. The reason/origin pairing holds at every assignment of the stop reason. |
| O33 | (after review round 6; **superseded for the start by O34**, the kill rule stays) The by-cause rule of O19 also covers `switch-hybrid`: any `process.started` that follows a `switch-hybrid` kill is `system` / NULL `via` / no ref, whatever the call context, including the start made inside the user's send that follows the composer's switch request. The kill is `system` (non-deliberate reason). |
| O34 | (after review round 7) The `apply-settings` system rule is bound to the same operation: only the restart performed by the operation that issued the kill is `system`. A later independent start takes its own send's origin. For `switch-hybrid`, only the kill is `system`; the following `process.started` takes the origin of the send that causes it, with or without a kill. Supersedes O33 for the start and amends O19 and O25. |
| O35 | (after review round 7) An invalid `via` is stored as NULL; the event keeps the resolved `actor`, `actor_ref` and `actor_name`. |

## 11. Known limits

- The hybrid switch kill is `system` although a UI click triggers it. A settings change that needs a restart gives a `system` stop and a `system` restart (same operation), not the user. Accepted.
- A human typing `twicc ...` inside an agent's terminal (for example a hybrid Claude CLI in tmux) is attributed to that agent.
- The origin in a drop file is forgeable by any local process. It is an audit trail, not a security control.
- A peer message is indistinguishable from a UI send (the user sends it himself) and is recorded as `ui`.
- Hide and unhide have no UI path today, so they are never `via=ui`.
- A CLI in `--remote` mode cannot be told from a plain RPC call and cannot forward a local agent identity; its events are recorded as RPC (`user` / `rpc` / token id).
- The restart performed by the same operation as an `apply-settings` kill, inline in a send or through the pending-settings path, and even when it re-sends a parked user text, is recorded as `system` (and so is its `apply-settings` stop), not as the user who changed the settings or parked the text. A later independent start is not.
- If two deliberate stops overlap, the last one sets reason and origin together; the first caller is not recorded.
- A detached or re-parented process cannot be traced to an agent and is attributed as a human CLI call.
- An RPC token may be deleted later; `actor_ref` then points to nothing. Accepted.
- A deleted `McpConnection` keeps its id and its name snapshot in old rows. Accepted.
- Rows written before the migration have NULL origin fields.

## 12. Out of scope

- Any display of the timeline (UI, CLI, MCP).
- Exposure of the new fields in public shares (to decide when the display is designed).
- Backfilling old rows.
- `awaiting_input.ended.outcome`: dropped for good, the information exists in the session transcript.
- Tier 2 events.

## 13. Verification expectations

The future plan's tests must demonstrate these behaviours.

1. **Mapping.** For each row of section 4, an event written through that path carries the expected `actor`, `via`, `actor_ref`, `actor_name`.
2. **Default and precedence.** With no origin set, a request-caused event is `user` / `ui`. An explicit `system` actor wins over a set origin and leaves `via`, `actor_ref`, `actor_name` NULL.
3a. **In-process precedence.** An external-MCP call is never `user` / `cli`. An internal-MCP call is never `agent` / `cli`. An RPC call does not walk the backend's own process ancestry.
3b. **RPC variants.** A `--remote` CLI call and an RPC call with a token give `user` / `rpc` / token id. An RPC call with no token record gives `user` / `rpc` / NULL ref. The cookie-scope RPC writes no event.
3c. **Local MCP with an API token.** It gives `user` / `mcp_internal` / the token id.
3. **Drop-file path.** For every drop kind that writes a timeline event, the origin written by the CLI side reaches the service through the watcher, from the one common place. A drop file without origin gives the default. A CLI run from a process descending from an agent gives the agent origin, a plain shell gives the human one.
4. **Entry points.** UI (WebSocket and REST), RPC with a token, internal MCP, external MCP and the MCP batch runtime (children included) each set the origin. Two requests with different origins handled in the same process never mix.
5. **Name snapshot.** An external MCP event keeps the connection name seen at event time after the connection is renamed or deleted. A name over 80 characters is truncated.
6. **Batch.** The bulk archive writes one origin on all rows.
7. **Deferred write.** A title flushed later keeps the origin of the request that set it, or the default if none was kept.
7c. **Codex re-key.** An agent-created Codex session, and a Codex title set before the canonical id is known, keep their captured origin after the draft-to-canonical re-key.
7a. **Titles.** A `user`-sourced title takes the request origin. A title with source `auto` or `agent` keeps actor `agent`, `via` NULL, no ref.
7b. **Created sessions.** A hidden or muted session created by an agent through CLI or MCP gives `session.created` and the flag rows with that agent's origin. A non-hidden, non-muted session created from the UI gives `session.created` = `user` / `ui` and no flag rows. A session whose `data.origin` is `twicc` but whose creation origin was not captured gives the default `user` / `ui` for `session.created` and its flag rows. A session found by the initial sync or created externally, and a backfilled session, gives `system` with NULL origin fields for both.
8d. **Stop with no origin.** A drop file from an old CLI with no origin stops an agent that was started by an agent through MCP: `process.ended` records the default `user` / `ui`, not the starter, even though the DEAD hook runs in the agent's own task.
8e. **System marker.** Runtime cron restart, pending-settings restart, hybrid boot adoption, boot-time cron restart and the restart performed by the same operation as an `apply-settings` kill each give `system` / NULL `via`, never `user` / `ui`, including when the carrier is unset. Also: (i) an `apply-settings` kill without restart followed by a later user send gives a start `user` / `ui`; (ii) a `switch-hybrid` on an idle session (no kill) followed by a send gives a start with that send's origin; (iii) a `switch-hybrid` with a live agent followed by a send from an agent through CLI or MCP gives a start `agent` / that `via`, and the kill is `system`.
8f. **Not request-caused, unchanged.** Non-user titles and standalone process-hook rows keep `agent` with NULL origin fields.
8g. **Marker scope.** After an inline `apply-settings` restart inside a user's send, the send's other timeline writes still carry the user's origin.
8a. **Overlapping stops.** A soft stop then a force kill, or an archive after a stop: `process.ended` carries the origin paired with the surviving reason, from the same request, at every assignment site. A later error, shutdown, timeout or `apply-settings` reason clears the origin.
8h. **Batch fallback.** When the batch insert fails and falls back row by row, every row carries the same origin columns as on the batch path.
8i. **Deferred writers.** A user-sourced title flushed inside the state-change funnel records the captured origin (or the default), never `system`.
8b. **Token ids.** An RPC call and a local-MCP call with an API token expose the token id to the carrier; the event carries it.
8c. **Widths.** An `actor_ref` or `actor_name` over its limit is truncated (255 and 80); the event is written.
8. **Process events.** `process.started` has the origin of the starting request. A stop asked by caller B on a session started by caller A gives `process.ended` with origin B, even when the DEAD transition runs in the agent's task. An error that overrides a pending stop reason clears the captured origin. A new agent object for the same session starts with no origin. Every automatic case of section 7 gives `system` with NULL `via`. Automatic `process.started` and `process.ended` after an `apply-settings` kill, inline in a user's send as well as through the pending-settings path, give `system` with NULL `via` while the carrier holds the user's origin. Automatic `process.started` (cron restart, hybrid boot adoption, pending-settings restart including one that re-sends a parked text) gives `system`, never `user` / `ui`. Standalone `turn.started`, `turn.ended`, `awaiting_input.started` and natural `awaiting_input.ended` keep `agent` with NULL `via`. The `turn.ended` and `awaiting_input.ended` rows closed with a `process.ended` carry the same actor, via, ref and name as that row.
9. **Clean follow-up.** A cron restart or pending-settings restart triggered by a state change caused by a request carries no origin of that request.
10. **`ended` rows.** An `ended` row never copies `via`, `actor_ref`, `actor_name` from the open `started` row.
11. **Registry and invalid values.** `data.via` is no longer produced by any writer. An invalid `via` is stored as NULL and the event is still written.
12. **Compatibility.** Rows written before the migration read as NULL in the three columns. The backfill writes NULL. The recording service still never raises when the origin cannot be resolved.
