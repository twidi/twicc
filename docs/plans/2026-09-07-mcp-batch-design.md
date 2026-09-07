# MCP batch calls

Date: 2026-09-07
Status: Proposed; implementation is not authorized by this document.
Scope: Internal and external TwiCC MCP servers.

## 1. Objective

Allow a client to submit multiple existing commands through one MCP tool call.
Authenticate the HTTP request once. Execute each command through the existing command system.
Return the collected results once execution finishes.

The primary benefit is fewer client round trips and repeated client-side checks.
Parallel reads provide an additional benefit. Sequential batches also meet the primary objective.
No measured speedup or desktop-client compatibility is claimed by this specification.

## 2. Current implementation and constraints

- `src/twicc/mcp/endpoint.py` authenticates internal tokens or external OAuth requests.
- `src/twicc/mcp/server.py` shares `_call_tool()` and `dispatch_tool()` between both servers.
- `_call_tool()` validates the command JSON Schema. `dispatch_tool()` alone does not perform that validation.
- `src/twicc/mcp/tools.py` derives command tools from the CLI registry.
- `src/twicc/rpc/generator.py` converts validated arguments into command arguments.
- `src/twicc/rpc/invoker.py` captures command output with a ContextVar.
- `src/twicc/rpc/views.py::_run_invoke()` closes old database connections around each invocation.
- `src/twicc/cli/_drop_request/transport.py` submits mutations to existing backend services.
- External dispatch records command provenance in `McpOperation`.
- Both MCP managers use stateless HTTP and JSON responses.
- The HTTP request-body limit is 48 MiB, including JSON and base64 attachments.

External token loading checks a local credential and connection. It does not repeat the complete OAuth authorization flow.
Client-side overhead and network latency must be measured separately from server authentication.

## 3. Decisions and alternatives

### 3.1 Two tools, one engine

Expose `batch_read` and `batch` on both MCP servers.

| Tool | Accepted commands | Execution | Default error policy |
|---|---|---|---|
| `batch_read` | Existing read-only MCP commands | Parallel by default; sequential optional | `continue` |
| `batch` | All commands exposed to this caller | Sequential only | `stop` |

Use one orchestration engine. Do not create separate internal and external engines.
Keep all existing individual tools available and preserve their contracts.

`batch_read` advertises `readOnlyHint: true` and `idempotentHint: true`.
`batch` advertises `readOnlyHint: false`, `destructiveHint: true`, and `idempotentHint: false`.
Do not claim closed-world behavior: nested commands can contact external services.
Read-only means no requested business mutation. Existing incidental access bookkeeping remains allowed.

Annotations are metadata, not enforcement. The engine must check read eligibility itself.
Client confirmation behavior remains client-controlled.

### 3.2 Rejected first-version alternatives

| Alternative | Reason |
|---|---|
| One general tool only | Read-only batches lose an honest read-only annotation |
| JSON-RPC batch arrays | Not a portable MCP tool-call contract |
| Loopback HTTP for each child | Repeats transport and authentication costs unnecessarily |
| A CLI batch command | Adds a second public interface without serving the initial MCP requirement |
| Execute arbitrary code or shell strings | Changes the authority and validation model |
| A generated union of every argument schema | Duplicates the catalog and enlarges tool discovery |
| Parallel mutations | Requires conflict and ordering semantics beyond transport aggregation |

MCP removed JSON-RPC batching in its 2025-06-18 revision.
Use an ordinary `tools/call`, which mainstream MCP clients already understand.
This establishes protocol compatibility, not verified behavior for a particular client account.

## 4. Input contract

`calls` is required. Its length is 1 through 20.
Each call contains exactly `id`, `name`, and `arguments`; all three fields are required.

| Field | Type and rules |
|---|---|
| `id` | String; pattern `^[A-Za-z0-9_-]{1,64}$`; unique within the batch |
| `name` | Exact, case-sensitive, unprefixed MCP command name |
| `arguments` | JSON object validated against that command's existing input schema |
| `mode` | Optional; `parallel` or `sequential` for `batch_read`; only `sequential` for `batch` |
| `on_error` | Optional; `continue` or `stop`; `stop` is invalid with parallel mode |

Reject unknown fields at the batch and call-entry levels.
Inside `arguments`, preserve the existing command schema, including its unknown-field rules and defaults.
Do not coerce types, repair names, split shell strings, or change command defaults.
An empty argument set must be supplied as `{}`.

Apply missing `mode` and `on_error` defaults from section 3 before checking their combination.
For example, `batch_read` with `on_error: stop` requires explicit `mode: sequential`.

Reject `batch` and `batch_read` as child names. Do not permit nesting through aliases.
Names refer only to the ordinary MCP command registry, never arbitrary RPC or CLI commands.
Reject local-only commands, excluded settings commands, and caller-ineligible commands.

Publish a compact JSON Schema for each wrapper. Do not duplicate each child schema inside it.
Agents must discover each child's normal schema before constructing arguments.
Descriptions and server instructions must explain this requirement.

### 4.1 Parallel read example

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {
      "id": "detail",
      "name": "session",
      "arguments": {"session_id": "01a0795b-d3ed-7231-bc16-76bec979fc41"}
    }
  ],
  "mode": "parallel",
  "on_error": "continue"
}
```

### 4.2 Sequential semantics

All arguments must be known when the client submits the batch.
An earlier result cannot populate later arguments. Strings such as `$ref` have no special meaning.
Sequential order does not guarantee that an asynchronous business operation has finished.
It guarantees only that the existing command has returned before the next command starts.
Existing multi-target commands retain their own internal behavior and partial-success contracts.

## 5. Validation before execution

Perform these stages in order:

1. Authenticate the HTTP request using the existing endpoint.
2. Validate the wrapper structure, defaults, field limits, and unique IDs.
3. Resolve every child against the ordinary MCP registry.
4. Validate every child's arguments with its existing JSON Schema.
5. Apply existing external caller restrictions to each child.
6. For `batch_read`, verify every resolved path against the read-only eligibility set.
7. Attempt bounded batch admission.

Any failure before admission means zero child commands execute.
Return all discovered child validation errors, up to 100 errors total.
Use `errors_truncated: true` if additional errors exist.
Malformed outer structure can return one structural error without attempting child validation.

Use `MCP_READ_ONLY_PATHS` as the initial eligibility source, intersected with the exposed registry.
Its membership becomes a server-enforced batch capability and requires regression tests.
New commands are ineligible until deliberately classified.
Do not alter individual-tool availability based on this classification.

Extract caller restriction checks into shared helpers rather than copying them into the batch engine.
External `whoami`, prohibited `self`/`parent` parameter values, and missing topology anchors remain rejected.
Inspect the audited parameter names, not arbitrary text inside prompts or attachments.

Static validation does not reserve resources or guarantee business success.
Commands still perform all live authorization, existence, trust, and provider checks during execution.

## 6. Execution and scheduling

### 6.1 Common execution path

Factor shared child preparation and execution out of the current handler.
Preparation returns an immutable descriptor containing the resolved spec and validated arguments.
Use a NamedTuple where a simple immutable record is sufficient.
Execution uses the current renderer, invoker, database-connection cleanup, and mutation transport.
Keep ordinary single-call response behavior unchanged.

Batch tools are synthetic MCP tools, not fake `CommandSpec` entries.
List them alongside generated tools, but route them explicitly before ordinary command lookup.
The ordinary registry must never contain wrappers, which prevents recursive dispatch structurally.

Bind caller context separately in each child task and restore it in `finally` blocks.
Propagate `forced_session_id`, `external_caller`, and `transport.backend_loop` across worker execution.
Do not resolve external identity through PID ancestry or an explicit target session.

### 6.2 Fixed initial capacity

| Limit | Value | Scope |
|---|---|---|
| Commands | 20 | Per batch |
| Active admitted batches | 4 | Process-wide, internal and external combined |
| Active child executions | 8 | All batches combined |
| Active children in one parallel batch | 4 | Per batch |
| Active children in a sequential batch | 1 | Per batch |

Reject excess batch admission immediately with `server_busy`; do not build an unbounded batch queue.
Admitted batches may wait for child permits. Acquire the global child permit only when a child is ready to execute.
Never hold a database-write lock while awaiting batch results or acquiring scheduling permits.
The existing command execution path owns its normal database locking.
Release permits when the command invocation and its dispatch bookkeeping actually finish, including failure paths.
An invocation can return a timeout while an already-submitted service operation continues.
That service operation no longer occupies a child permit after dispatch returns.
These permits count command invocations, not running agents, multi-target operations, or all outstanding business effects.

These limits bound additional batch load. They do not claim to cap existing individual MCP calls or all backend work.
Keep single-call admission unchanged in this scope.

Parallel mode starts children in input order as capacity permits. Completion order is unspecified.
Return results in input order, irrespective of completion order.
One ordinary child exception must not cancel siblings.
Catch ordinary exceptions per child; treat cancellation separately.

Sequential `stop` stops after a nonzero exit code, a technical error, or an indeterminate execution outcome.
Mark remaining children `skipped` with `previous_call_failed` and the triggering call ID.
Sequential `continue` proceeds after these failures unless a batch-wide stop condition applies.
Do not interpret nested business data to override a command's existing exit code.
Output omission is a delivery condition, not a command execution failure.
It does not trigger sequential `stop`, whether detected immediately or during final response sizing.
Later commands can therefore run even when an earlier successful command's response is omitted.

### 6.3 No batch transaction

Successful changes remain applied when later commands fail.
There is no rollback, shared database snapshot, or all-or-nothing business guarantee.
Prevalidation is all-or-nothing only for starting command execution.
Use existing multi-target tools when they better express the requested operation.

## 7. Authentication and revocation

The initial HTTP authentication remains unchanged for both MCP surfaces.
Every child inherits the same authenticated caller; caller identity is not an input field.
Internal aliases and share restrictions remain unchanged.
External connection attribution and full-access share rules remain unchanged.

Before each external child starts, recheck connection validity through shared OAuth validity logic.
Perform this check after acquiring the execution permit, immediately before invoking the command.
Check revocation, external enablement, current resource binding, and access-token expiry.
Carry the authenticated expiry as trusted request context; never accept it from arguments.
This is a local check, not a new OAuth exchange or token refresh.

On invalidation, stop starting children in both modes, regardless of `on_error`.
Mark unstarted children `skipped` with `authorization_changed`.
Allow already-started operations to finish under their existing contracts.
Do not promise atomic exclusion between the local validity check and an immediately subsequent revocation.

Token expiry during a batch is not automatically refreshed by the server.
The client must authenticate a subsequent request normally.
Revalidation failure does not discard results already collected.
If the validity check raises an exception, fail closed rather than treating the caller as valid or confirmed revoked.
Stop all new starts, independent of `on_error`. Mark unstarted children `skipped` with `authorization_unavailable`.
Retain collected results and allow already-started invocations to settle.
Do not expose the database exception or automatically retry the check.
Both authorization stop reasons produce a completed aggregate with `ok: false`, including when zero children start.
The skipped record has `response: null`, `outcome_unknown: false`, and `response_omitted: false`.
Its wrapper error uses the relevant authorization code and `caused_by: null`.

## 8. Output contract

Publish an output schema covering completed batches and rejected batches.
Return the same JSON object as `structuredContent` and JSON text in `content`, following current interoperability practice.
Normalize command values with orjson, preserving current datetime serialization.

### 8.1 Executed batch

```json
{
  "batch_id": "019b1caf-8dc4-7600-a5a7-e3561e3f8f21",
  "status": "completed",
  "ok": false,
  "summary": {"total": 2, "succeeded": 1, "failed": 1, "skipped": 0},
  "results": [
    {
      "id": "recent",
      "name": "sessions",
      "status": "success",
      "outcome_unknown": false,
      "response": {"exit_code": 0, "result": [], "error": null},
      "error": null,
      "response_omitted": false
    },
    {
      "id": "detail",
      "name": "session",
      "status": "command_error",
      "outcome_unknown": false,
      "response": {"exit_code": 4, "result": null, "error": "Example command failure."},
      "error": null,
      "response_omitted": false
    }
  ]
}
```

The example command error is illustrative, not a promised command-specific message.
`batch_id` is a server-generated UUID for correlation, not an idempotency key.
`status: completed` means aggregation has finished. It does not mean every command succeeded.
`ok` is true only when every child succeeded, every response is present, and no outcome is unknown.

Every child record has the seven fields shown above.
`response` is the exact existing `{exit_code, result, error}` envelope when available.
Otherwise it is null. Do not invent CLI exit codes for wrapper errors.

| Child status | Meaning | Summary category |
|---|---|---|
| `success` | Returned exit code zero | succeeded |
| `command_error` | Returned nonzero exit code | failed |
| `tool_error` | No normal command envelope is available | failed |
| `skipped` | Command invocation never starts | skipped |

`error` is null or `{code, message, caused_by}`. `caused_by` is null or another call ID.
Messages are bounded to 512 characters and must not include raw arguments or credentials.
Stable wrapper codes are `execution_error`, `previous_call_failed`, `authorization_changed`,
`authorization_unavailable`, and `response_too_large`.

`outcome_unknown` describes business effects, not whether the wrapper received an exception.
Set it true for command exit code 5 and for technical failures after invocation starts.
This is deliberately conservative, including read commands.
An ordinary nonzero code otherwise preserves the command's established meaning.
Success counts can coexist with `ok: false` when output is omitted.
Summary counts always sum to total and depend on child execution status, not output delivery.

### 8.2 Rejected batch

```json
{
  "batch_id": "019b1caf-8dc4-7600-a5a7-e3561e3f8f21",
  "status": "rejected",
  "ok": false,
  "executed": 0,
  "errors": [
    {
      "index": 1,
      "id": "detail",
      "code": "invalid_arguments",
      "path": "/calls/1/arguments/session_id",
      "message": "Required argument is missing."
    }
  ],
  "errors_truncated": false
}
```

Use zero-based indexes and JSON Pointer paths.
For outer errors, `index` and `id` are null.
For an invalid or oversized supplied ID, return null rather than echoing unsafe input.
Bound each rejection message and path to 512 characters. Bound every error ID by the input ID rules.
Construct messages from safe validator categories and schema-defined field names.
Never forward raw jsonschema exception messages, supplied property names, values, or exception strings.
For a path containing an untrusted property name, report the nearest safe schema-defined ancestor.
If that safe path exceeds the bound, report `/calls/<index>/arguments` instead.
Use an empty string for the root JSON Pointer.
Stable rejection codes: `invalid_batch`, `duplicate_id`, `unknown_tool`, `invalid_arguments`,
`tool_not_allowed`, `invalid_policy`, and `server_busy`.

Rejected batches use MCP `isError: true`.
Executed batches use `isError: false`, including partial failure, matching current CLI-error semantics.
HTTP authentication and body-limit failures retain their existing transport responses and need not contain this schema.

## 9. Output size and attachments

The existing 48 MiB request limit applies to the complete batch, not to each child.
Existing per-command attachment limits also apply. Do not multiply them into a larger HTTP limit.

Limit each serialized child command envelope to 384 KiB of UTF-8 compact JSON.
If an envelope exceeds that limit, omit the complete envelope rather than truncating its data.
Keep the execution status and `outcome_unknown` value.
Set `response: null`, `response_omitted: true`, and wrapper error code `response_too_large`.
The wrapper error message states that the command ran but its response is omitted.
Do not automatically retry the command or expose an artifact/download endpoint for the omitted data.

Twenty retained envelopes use at most 7.5 MiB. Bound wrapper metadata and use compact JSON text.
Require the full serialized MCP response, including both representations, to remain below 16 MiB.
Check this invariant in tests with worst-case valid metadata and escaped text.
If escaping exceeds the final limit, omit retained envelopes from the end until the response fits.
Never change execution statuses or retry commands while shrinking the response.

This bounds retained/returned batch data, not allocations inside existing commands.
A single command can still create a large result before the wrapper measures it.
Clients should request pagination or smaller results. Smaller external client limits remain possible.

## 10. Timeouts, cancellation, and retries

Do not introduce a new hard batch execution timeout in the first version.
Keep each command's existing timeout semantics and parameters.
A hard wrapper timeout cannot guarantee that a worker thread or submitted mutation has stopped.
This deliberately replaces the earlier exploratory suggestion of an unspecified global deadline.

Sequential duration can equal the sum of command durations. Parallel duration includes permit waiting and the slowest child.
The first version returns only the final aggregate and adds no progress notifications or durable jobs.
Descriptions must advise short batches and separate calls for long waits.
Existing documented wait guidance of at most 300 seconds remains guidance, not a universal command timeout.
The first version deliberately preserves valid long command timeouts rather than silently clamping them.
Four long or stuck batches can occupy all batch admission slots, including after client cancellation.
Other batch requests then receive `server_busy`; individual calls remain outside this admission gate.
Shared worker and database resources can still delay individual calls.
The fixed limits bound concurrency but do not guarantee availability, fairness, or maximum response time.
This is an accepted first-version limitation. A strict service deadline requires cooperative cancellation or process isolation.

When MCP cancellation reaches the handler, stop scheduling new children immediately.
Do not cancel started worker invocations under the assumption that this stops their effects.
Retain strong references to started tasks until they settle, consume exceptions, and perform context/connection cleanup.
Own these tasks in the batch lifecycle registry, outside the request's cancellation scope.
Await them through shielding so cancelling a request cannot cancel the owned dispatch task.
Create each owned task with the correct caller context; the registry must not supply another caller's context.
Keep their child permits occupied until actual completion.
Keep the cancelled batch's admission slot occupied until its started children settle.
This prevents repeated cancellation from bypassing capacity limits.

An HTTP disconnect is not guaranteed to produce handler cancellation with the installed SDK and JSON-response transport.
If no cancellation is delivered, the batch continues under normal policy.
Compatibility tests must document this behavior rather than promise cancellation on disconnect.
No final result is promised after cancellation or transport loss.

Backend shutdown stops admission and new child scheduling before closing the batch task registry.
The batch lifecycle may wait at most five seconds for started dispatch tasks to settle.
After that grace period, cancel their async orchestration and log remaining invocations as indeterminate.
Do not synchronously join worker threads or wait indefinitely for them in MCP cleanup.
Use a timeout on waiting itself; do not use a cancellation helper that waits indefinitely for cancellation acknowledgement.
Command-thread cleanup still runs if and when the thread returns.
This five-second bound covers added batch draining, not complete backend or Python executor shutdown.
The current backend has no universal bounded MCP shutdown helper; existing executor shutdown can still wait for threads.
Hard process-exit guarantees are outside this feature and must not be claimed.
A process crash or forced shutdown can lose remaining responses and correlation logs.
Do not persist or resume batches after restart.

Never automatically retry a child or batch.
Neither client IDs nor batch IDs provide deduplication.
Clients must inspect actual resource state after an uncertain write before deciding whether to retry.
Persistent idempotency and result retrieval are explicitly outside this version.

## 11. Provenance and diagnostics

Keep one existing `McpOperation` record per externally dispatched command, including nonzero command returns.
Add reserved metadata to its existing `targets` JSON under `_batch`:
`{id: <batch UUID>, call_id: <client ID>, index: <zero-based index>}`.
Do not accept this metadata from child arguments.
No schema migration is required for this JSON extension.
Individual calls keep their current provenance shape.

Use structured backend logs for batch admission, rejection, per-child completion, cancellation, and final summary.
Include batch ID, index, validated tool name, caller identifier, status, exit code when available, and elapsed time.
Do not log complete arguments, prompts, attachment data, tokens, or full results.
Validation and execution exceptions must not fall through to the current logger's `arguments=%r` for wrapper calls.

A provenance-write failure can occur after a command has applied effects.
Report a child technical failure with `outcome_unknown: true` if the current dispatch cannot provide its normal envelope.
Log the correlation without claiming rollback. Do not execute the command again.
Validation failures and skipped calls have logs but no fabricated executed-command row.
This is diagnostic correlation, not a durable replay or exactly-once audit service.

Measure HTTP/authentication time separately from validation, permit waiting, command execution, and aggregate serialization.
Compare identical command sets as individual calls, sequential batches, and parallel read batches.
Report warm and cold client behavior separately. Do not attribute all client delay to OAuth.

## 12. Integration boundaries

| File or area | Required change during implementation |
|---|---|
| `mcp/tools.py` | Synthetic tool declarations, compact schemas, annotations, collision assertions |
| New `mcp/batch.py` | Wrapper validation, policies, scheduler, result assembly, bounds |
| `mcp/server.py` | Shared validated preparation/execution and wrapper routing |
| `mcp/identity.py` | Trusted expiry/correlation context as needed; no caller-controlled identity |
| OAuth validity helpers | Reusable local admission check for external children |
| External operation recording | Optional `_batch` metadata in existing targets JSON |
| MCP lifespan | Initialize/reset loop-bound batch capacity and track settling tasks |
| MCP instructions | Tool selection, schema discovery, errors, output limits, retries |
| `frontend/public/help/external-mcp.md` | Explain batching and limitations to external users |
| Tests | New batch suite plus single-call regression coverage |

Do not add CLI/RPC batch routes, dependencies, frontend controls, database models, or a background job subsystem.
Do not alter the two MCP origins, OAuth scopes, token issuance, or provider approval wiring.
Keep wrappers deferred under the current provider rules; do not expand the hot-tool list initially.
Explain the new wrappers in internal and external server instructions so agents can discover them.
Do not claim every tool is a direct CLI command or uses the single-command envelope after this change.

A packaged skill edit is optional, not required for wrapper execution.
If implementation edits packaged skills, read the plugin README and bump the plugin version under repository rules.
An implementation changelog entry belongs only in the then-current Unreleased section.

## 13. Acceptance criteria and tests

### 13.1 Contract

1. Both catalogs expose both wrappers with correct schemas, descriptions, and annotations.
2. Ordinary tool names cannot collide with wrapper names.
3. Empty/oversized batches, duplicate IDs, unknown fields, bad types, and invalid policy pairs execute zero commands.
4. Every child uses its current schema, including enum, required, and additional-property behavior.
5. A later invalid child prevents earlier valid mutations from starting.
6. Nested wrappers and excluded CLI commands cannot execute.
7. Every output validates against the wrapper output schema.
8. Original single-tool MCP responses remain unchanged.
9. Results retain input order under deliberately reversed completion order.
10. Summary, omitted-output, skipped, timeout, and technical-error cases follow section 8 exactly.

### 13.2 Identity and authority

11. Concurrent batches with distinct internal and external identities cannot leak ContextVars.
12. Internal `self`/`parent` and external explicit-ID restrictions behave as in individual calls.
13. Share, message attribution, trust, and provider restrictions remain enforced.
14. A forged identity field is rejected; a target session does not become the caller.
15. Revocation, expiry, disablement, and resource changes stop unstarted external children in both modes.
16. Existing in-flight commands remain subject to their normal completion semantics.
17. Every read-eligible command path is deliberate; mutation paths are rejected from `batch_read`.

### 13.3 Failure and lifecycle

18. Sequential stop/continue policies work for command errors and ordinary exceptions.
19. A parallel child failure does not cancel siblings.
20. Applied effects survive later failure; no rollback or retry occurs.
21. Cancellation stops new starts and retains permits until workers actually finish.
22. Repeated cancelled requests cannot exceed batch or child capacity.
23. Lifespan restart does not reuse stale asyncio primitives across loops.
24. A provenance failure after an effect returns uncertainty without replay.
25. Worker exceptions are consumed after client cancellation; database connections close normally.
26. Measure actual disconnect behavior with the pinned SDK and transport.

### 13.4 Capacity and output

27. Global and per-batch limits hold across both MCP endpoints combined.
28. Excess admission rejects before any invocation; waiting children hold no database lock.
29. Combined attachments obey the existing request and per-command limits.
30. Oversized outputs preserve execution status and set explicit omission metadata.
31. Worst-case escaping and dual output representation remain within the final byte limit.
32. Logs contain correlation without raw prompts, attachments, or credentials.
33. Oversized invalid property names and values produce bounded, sanitized rejection diagnostics.
34. Output omission does not stop an otherwise successful sequential batch.
35. A timed-out command can leave a service operation running without extending invocation-level permit accounting.
36. Long waits exhaust admission predictably; excess batches reject without allocating worker tasks.
37. MCP batch draining respects its five-second grace even when a worker never returns; no hard process-exit claim follows.
38. An external validity-check exception stops new starts with `authorization_unavailable` and preserves previous results.
39. Both accepted and rejected responses satisfy the final serialized response limit, including escaped diagnostic strings.

### 13.5 Product validation

Exercise a real internal Claude agent and a real internal Codex agent.
Exercise at least one desktop external client and one remote external client when accounts are available.
Check discovery, argument construction, read/write confirmations, partial failures, and response-size handling.
Record unavailable client checks as unexecuted, not passed.
Benchmark representative short calls without promising a required speedup factor.
Run focused MCP tests and relevant existing identity, endpoint, server, and external tests.

## 14. Delivery boundary

This specification incorporates the corrections from an internal sub-agent's adversarial review on 2026-09-07.
The same reviewer verifies the corrections and reports no remaining blockers.
The review uses source inspection; runtime behavior and client compatibility still require implementation validation.
It does not authorize implementation, installation, migrations, or server restarts.
No mandatory product decision remains unspecified; numerical limits are initial defaults to validate during implementation.
Changing public error or scheduling semantics requires updating this specification before shipping.

## 15. References

- [External MCP design](2026-09-05-external-mcp-design.md)
- [MCP server implementation](../../src/twicc/mcp/server.py)
- [MCP tool registry](../../src/twicc/mcp/tools.py)
- [MCP 2025-06-18 changes](https://modelcontextprotocol.io/specification/2025-06-18/changelog)
- [MCP tool contracts](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

Protocol sources were consulted during the preceding analysis on 2026-09-07.
Client behavior must be verified against the installed client and SDK during implementation.
