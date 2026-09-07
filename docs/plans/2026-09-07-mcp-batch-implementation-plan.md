# MCP Batch Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when available to execute this plan task by task. Implementation requires a separate user instruction. Steps use checkboxes for tracking.

**Goal:** Execute several existing TwiCC commands through one internal or external MCP call.

**Architecture:** Keep ordinary commands in the generated registry. Add two synthetic tools and one lifecycle-owned batch scheduler. Share command preparation and dispatch with individual calls; retain the current worker-thread and mutation-service paths.

**Tech Stack:** Python 3.13+, asyncio, Django, orjson, jsonschema, MCP Python SDK 2.1.1 from the current lockfile.

**Spec:** [MCP batch design](2026-09-07-mcp-batch-design.md), initially committed as `130237d7`; planning clarifies tool-result byte accounting.

## Global constraints

- `batch_read`: read-only commands, parallel by default, sequential optional, `continue` by default.
- `batch`: all caller-eligible commands, sequential only, `stop` by default.
- `calls`: 1–20 entries; required `id`, `name`, and `arguments`.
- IDs: `^[A-Za-z0-9_-]{1,64}$`, unique within one batch.
- Four admitted batches globally; eight batch child invocations globally; four parallel children per batch.
- HTTP request body: 48 MiB, shared by all calls and their attachments.
- Child command envelope: 384 KiB; explicit omission above this size.
- Aggregate output: 16 MiB, including both MCP content representations; see Task 3 for transport accounting.
- Maximum 100 rejection diagnostics; message and path limits: 512 characters each.
- Validate every child before invoking any child. Preserve every command's own argument schema and business checks.
- No parallel mutations, nested wrappers, result references, rollback, retry, deduplication, or durable batch jobs.
- No new hard execution timeout. Keep existing command timeouts. Added shutdown draining has a five-second grace.
- Keep single-tool behavior, OAuth scopes, origins, and provider approval wiring unchanged.
- No dependency, CLI route, RPC route, model, migration, or frontend control is required.
- Use NamedTuple for immutable records and orjson for backend JSON.
- All files and user-facing product strings are English.
- Stay on the user's current branch. Do not create a worktree, install packages, or restart servers without authorization.
- Preserve unrelated changes and stage only files belonging to the current task.

## Execution discipline

Each task has a meaningful failing test, implementation steps, and focused verification.
Use synchronous pytest functions with `asyncio.run(scenario())`, matching the existing MCP suite.
Use `pytest.mark.django_db(transaction=True)` when worker threads or async ORM access the test database.
Use Events and barriers for ordering tests, not elapsed-time guesses or large sleeps.

Run commands from the repository root:

```bash
uv run pytest tests/test_mcp_server.py tests/test_mcp_tools.py -q
```

If the user later selects a worktree, prefix commands with `cd <worktree> &&` and set `TWICC_DATA_DIR=$PWD` for Python commands.
Do not use `uv pip`, `--active`, manual migrations, or package installation for test setup.

Make one implementation commit per task after its checks pass. Use a Conventional Commit subject and descriptive body.
Read the actual current model identity for the Codex co-author trailer at commit time.
Do not claim that the commands documented here have already run.

## Source observations that constrain implementation

1. `_call_tool()` currently validates schema; `dispatch_tool()` does not. Calling the latter in a loop is insufficient.
2. `dispatch_tool()` sets session and backend-loop ContextVars, uses `_run_invoke()` in a thread, then writes external provenance.
3. `transport.wait()` can return exit code 5 while its service Future continues. Capacity counts dispatches, not all business effects.
4. External request context currently stores only `ExternalCaller(connection_id, name)`. Expiry/resource facts require separate trusted context.
5. The SDK uses an AnyIO task group for stateless requests. A request-owned task group is unsuitable for uncancellable worker ownership.
6. The SDK serializes JSON-RPC responses through `model_dump_json(by_alias=True, exclude_unset=True)`.
7. `ServerRequestContext.request_id` is available. A caller-selected JSON-RPC ID is not a child `id`.
8. MCP session-manager instances are single-use in existing tests. Runtime counters and loop primitives must also reset per lifespan.
9. `cli/run.py::_cancel_task()` has no general shutdown timeout. Do not rely on it to bound batch draining.
10. Existing tests monkeypatch `mcp.server._run_invoke` and directly call `dispatch_tool()`. Preserve those seams during extraction.

## File and dependency map

| File | Responsibility |
|---|---|
| New `src/twicc/mcp/dispatch.py` | Ordinary-command lookup, immutable prepared descriptor, common caller restriction helpers |
| Existing `src/twicc/mcp/server.py` | Existing invocation/provenance path, wrapper routing, final MCP adapter, server instructions |
| New `src/twicc/mcp/batch_contract.py` | Constants, schemas, validation diagnostics, output records and size handling |
| New `src/twicc/mcp/batch.py` | Admission, scheduling, lifecycle ownership, cancellation and aggregation |
| Existing `src/twicc/mcp/identity.py` | Trusted grant and batch-correlation ContextVars |
| Existing `src/twicc/mcp/oauth/provider.py` | Shared live connection validity predicate and local child admission check |
| Existing `src/twicc/mcp/endpoint.py` | Trusted grant binding and common batch runtime lifespan |
| Existing `src/twicc/mcp/tools.py` | Synthetic tool descriptions and catalog merge |
| New `tests/test_mcp_batch_contract.py` | Pure validation, schema and output tests |
| New `tests/test_mcp_batch_runtime.py` | Scheduler and lifecycle tests using fake dispatches and real worker barriers |
| New `tests/test_mcp_batch_external.py` | Authenticated external batch requests and provenance |
| Existing `tests/test_mcp_server.py`, `test_mcp_tools.py`, `test_mcp_endpoint.py` | Single-call and transport regressions |
| Existing `frontend/public/help/external-mcp.md`, `CHANGELOG.md` | User documentation and Unreleased entry |

Dependency direction: `identity` and `dispatch` do not import `batch` or `server`.
`batch_contract` imports preparation helpers, not `server`.
`batch` consumes injected dispatch and authorization callbacks, not `server`.
`server` connects the runtime to its existing dispatcher. `endpoint` owns runtime startup and shutdown.
`tools` may import schema constants from `batch_contract`; preparation must not import `tools` at module scope.
Pass the ordinary registry into preparation instead. This avoids `tools -> batch_contract -> dispatch -> tools` cycles.

## Task 1: Extract shared command preparation without changing individual calls

**Files:** Create `mcp/dispatch.py`; modify `mcp/server.py`; extend `tests/test_mcp_server.py` and `tests/test_mcp_endpoint.py`.
Paths in task sections are relative to `src/twicc/` unless they start with `tests/` or `frontend/`.

**Interfaces produced:**

```python
class PreparedTool(NamedTuple):
    name: str
    spec: CommandSpec
    arguments: dict

class UnknownToolError(Exception):
    pass

def check_caller_arguments(name: str, arguments: dict, *, external: bool) -> None:
    # Raises the same unknown-tool/value errors as current external dispatch.


def prepare_tool(name: str, arguments: dict, *, registry: dict[str, CommandSpec],
                 external: bool) -> PreparedTool:
    # Resolve, validate with existing schema, check caller restrictions, copy arguments.

# Retain in server.py:
async def execute_prepared(prepared: PreparedTool, *, session_id: str | None,
                           on_start: Callable[[], None] | None = None) -> dict:
    # Existing render/invoke/provenance/normalization body.

async def dispatch_tool(name: str, arguments: dict, *, session_id: str | None) -> dict:
    # Compatibility entry; resolve and check caller, then execute_prepared.
```

The compatibility entry retains its existing lack of schema validation for direct Python callers.
The MCP single-call handler uses `prepare_tool()` and then `execute_prepared()`.
Re-export `UnknownToolError` from server.py to preserve current imports.
Keep `_run_invoke` referenced from server.py so existing thread tests still intercept the real execution seam.

- [ ] Add a parity test and a validation-before-invoke regression.

```python
def test_invalid_single_call_does_not_invoke(monkeypatch):
    called = []
    monkeypatch.setattr(mcp_server, "_run_invoke", lambda argv: called.append(argv))
    params = mcp_types.CallToolRequestParams(name="session", arguments={})
    result = asyncio.run(mcp_server._call_tool(SimpleNamespace(request=None), params))
    assert result.is_error is True
    assert called == []
```

Import `SimpleNamespace`, `mcp.types`, and existing `mcp_server` in this test file.
Capture the pre-refactor responses for valid calls, invalid schemas, unknown tools, and external restriction failures.
Assert the refactor preserves content, `isError`, and structured-content behavior.

- [ ] Run `uv run pytest tests/test_mcp_server.py -q`; demonstrate the new helper-specific tests fail before extraction.
- [ ] Move only lookup, validation, and caller checks to dispatch.py. Preserve current error wording for individual calls.
- [ ] Split execution into `execute_prepared()`, retaining renderer, ContextVar `finally` cleanup, `_run_invoke`, and orjson normalization.
- [ ] Add an optional `on_start` callback. Render arguments first; call the callback immediately before submitting `_run_invoke` with `asyncio.to_thread`.
- [ ] This submission marker means business effects may occur, not that the worker has already applied them. Rendering failures leave it unset.
- [ ] Keep ordinary exception types and messages unchanged; individual calls omit the callback.
- [ ] Migrate the five endpoint tests that patch `server.dispatch_tool` to patch `server.execute_prepared` with `(prepared, *, session_id)`.
- [ ] In those fakes, read `prepared.name` and `prepared.arguments`. Preserve every existing wire-envelope, error, caller-isolation, and attachment assertion.
- [ ] Copy validated arguments once before asynchronous execution. Do not mutate incoming arguments or schema defaults.
- [ ] Run `uv run pytest tests/test_mcp_server.py tests/test_mcp_endpoint.py tests/test_mcp_external.py tests/test_mcp_identity.py -q`.
- [ ] Commit this compatible extraction with `refactor(mcp): share command preparation`.

**Acceptance:** Existing clients see no batch tools yet and no single-call behavior changes.

## Task 2: Implement the pure batch input contract

**Files:** Create `mcp/batch_contract.py` and `tests/test_mcp_batch_contract.py`.
**Consumes:** `PreparedTool`, `prepare_tool`, existing command schemas and read-only paths.
**Produces:**

```python
class PreparedCall(NamedTuple):
    index: int
    id: str
    tool: PreparedTool

class PreparedBatch(NamedTuple):
    batch_id: str
    mode: str
    on_error: str
    calls: tuple[PreparedCall, ...]

class BatchValidation(NamedTuple):
    prepared: PreparedBatch | None
    rejection: dict | None

BATCH_NAMES = frozenset({"batch", "batch_read"})
MAX_CALLS = 20
MAX_ERRORS = 100
MAX_DIAGNOSTIC_CHARS = 512

def validate_batch(name: str, arguments: object, *, registry: dict[str, CommandSpec],
                   read_only_paths: frozenset[str], external: bool,
                   batch_id: str) -> BatchValidation:
    # Exactly one result member is non-null.
```

- [ ] Add parameterized validation cases for every rule in spec sections 4–5.

```python
def test_late_invalid_child_rejects_whole_batch():
    body = {"calls": [
        {"id": "first", "name": "workspaces", "arguments": {}},
        {"id": "second", "name": "session", "arguments": {}},
    ]}
    result = validate_batch("batch", body, registry=tools_by_name(),
                            read_only_paths=MCP_READ_ONLY_PATHS,
                            external=False, batch_id="test-batch")
    assert result.prepared is None
    assert result.rejection["executed"] == 0
    assert result.rejection["errors"][0]["index"] == 1
```

- [ ] Run `uv run pytest tests/test_mcp_batch_contract.py -q` and observe the missing contract implementation.
- [ ] Build separate input schemas with defaults and `additionalProperties: false` at both wrapper levels.
- [ ] Resolve defaults explicitly; JSON Schema validation does not insert defaults. Reject parallel `stop` and general-batch parallel mode.
- [ ] Validate outer shape first. Then collect child errors in input order, capped at 100, probing one more error to set truncation.
- [ ] Use `jsonschema.validators.validator_for(schema)` with the schema's declared dialect; validate schemas once and cache validators by tool name.
- [ ] Inspect real child schemas instead of rebuilding their fields. Reject wrappers before ordinary lookup.
- [ ] Classify errors without exposing exception text: structural -> `invalid_batch`; duplicate -> `duplicate_id`; policy -> `invalid_policy`; missing name -> `unknown_tool`; schema -> `invalid_arguments`; caller/read restriction -> `tool_not_allowed`.
- [ ] Build JSON Pointers only from schema-defined properties and numeric list indexes. Escape `~` and `/` per JSON Pointer.
- [ ] Use fixed messages by validator category: required, type, enum, additional properties, range/pattern, and generic invalid arguments.
- [ ] Test huge secret-bearing values and unknown keys. Assert the secret is absent, diagnostic limits hold, and the rejection remains small.
- [ ] Run `uv run pytest tests/test_mcp_batch_contract.py tests/test_mcp_tools.py -q`.
- [ ] Commit with `feat(mcp): validate batch command inputs`.

**Acceptance:** Validation is deterministic and performs no invocation, grant lookup, or mutation.

## Task 3: Implement result records, schemas, and output budgeting

**Files:** Extend `mcp/batch_contract.py` and `tests/test_mcp_batch_contract.py`.
**Produces:**

```python
MAX_CHILD_BYTES = 384 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024

def command_record(call: PreparedCall, envelope: dict) -> dict:
    # Seven fields; status from exit_code; outcome_unknown when exit_code == 5.

def failed_record(call: PreparedCall, *, started: bool) -> dict:
    # execution_error, no exception strings, outcome_unknown=started.

def skipped_record(call: PreparedCall, *, code: str, caused_by: str | None = None) -> dict:
    # No envelope; no unknown outcome or omitted response.

def completed_batch(batch_id: str, records: list[dict]) -> dict:
    # Summary categories depend on execution statuses; ok also requires retained output.

def fit_result(payload: dict) -> mcp_types.CallToolResult:
    # Same bounded payload in structured_content and compact JSON text.
```

`command_record` measures the normalized command envelope immediately with `len(orjson.dumps(envelope))`.
For oversized envelopes, retain execution status, null the response, and set `response_too_large`.
Make the omission helper shared by per-child and aggregate fitting, so they produce identical metadata.
If a provenance failure prevents a normal envelope, preserve the current dispatch exception as `tool_error`; do not replay.

- [ ] Add tests for all exit classes, technical errors before/after start, skipped records, and output omission.

```python
def test_omission_keeps_success_status(prepared_call):
    record = command_record(prepared_call, {
        "exit_code": 0, "result": "x" * (384 * 1024), "error": None,
    })
    assert record["status"] == "success"
    assert record["response"] is None
    assert record["response_omitted"] is True
    payload = completed_batch("test-batch", [record])
    assert payload["summary"]["succeeded"] == 1
    assert payload["ok"] is False
```

Define `prepared_call` in this test module by preparing one real `workspaces` call through the Task 2 validator.

- [ ] Run the new result tests before implementing the constructors.
- [ ] Publish `BATCH_OUTPUT_SCHEMA` as a union of rejected/completed objects, with strict required fields and seven-field child records.
- [ ] Define response as null or the existing three-field envelope, with unconstrained JSON `result`; never invent an output schema for command results.
- [ ] Use schema enum sets for statuses and error codes from spec section 8. Check relational invariants in tests and constructors.
- [ ] Serialize the actual SDK `CallToolResult` with aliases and unset-field exclusion when measuring size. Include both content representations.
- [ ] Recompute `ok` after each omission; remove retained envelopes from the end until the serialized result fits.
- [ ] Test accepted and rejected payloads, ASCII quotes, backslashes, control characters, non-ASCII text, and twenty boundary-size records.
- [ ] Compare the helper's serialized bytes with the actual endpoint transport response in Task 6.
- [ ] Run `uv run pytest tests/test_mcp_batch_contract.py -q`.
- [ ] Commit with `feat(mcp): assemble bounded batch results`.

**Transport accounting clarification:** The bounded object is the MCP `CallToolResult`, including `content` and `structuredContent`.
The outer JSON-RPC envelope repeats the client-supplied request ID, which can itself exceed 16 MiB under the existing 48 MiB input limit.
Do not change individual transport behavior or introduce a new request-ID restriction for this feature.
Spec section 9 now names the serialized tool result rather than promising a cap on the outer JSON-RPC envelope.
Endpoint tests assert the tool-result cap and separately measure JSON-RPC overhead. This is a scope clarification, not silent truncation of IDs.

## Task 4: Carry trusted external grant facts and batch provenance

**Files:** Modify `mcp/identity.py`, `mcp/oauth/provider.py`, `mcp/endpoint.py`, `mcp/server.py`; create `tests/test_mcp_batch_external.py`.
**Consumes:** Current `ExternalCaller` and `AccessToken` returned by `load_access_token()`.
**Produces:**

```python
class ExternalGrant(NamedTuple):
    connection_id: str
    resource: str
    expires_at: int

class BatchCorrelation(NamedTuple):
    batch_id: str
    call_id: str
    index: int

external_grant: ContextVar[ExternalGrant | None]
batch_correlation: ContextVar[BatchCorrelation | None]

# In oauth/provider.py:
def active_connection_filter(resource: str, *, prefix: str = "") -> Q:
    # revoked_at is null AND resource equals the supplied resource.

async def batch_grant_valid(grant: ExternalGrant) -> bool:
    # Live config/resource/expiry checks, then connection existence using shared predicate.
```

Keep `ExternalCaller`'s two-field shape unchanged for existing callers and tests.
`external_grant` stores no token, digest, prompt, or complete request object.
The endpoint sets and resets both trusted contexts around the session manager.
External batches with missing trusted grant context must stop with `authorization_unavailable`; never fall back to internal identity.

- [ ] Add unit tests for validity, expiry, revocation, disablement, changed resource, and missing connection.
- [ ] Reuse the external test suite's transaction-enabled configuration pattern, isolated settings, and database-write passthrough.
- [ ] Add a grant-context endpoint test asserting it is present inside the handler and reset after the request.
- [ ] Run `uv run pytest tests/test_mcp_batch_external.py -q` before implementing grant support.
- [ ] Factor the connection predicate into a `Q` helper using keys `prefix + "revoked_at__isnull"` and `prefix + "resource"`.
- [ ] Call it with an empty prefix for connection queries and `prefix="connection__"` for credential queries.
- [ ] Check `base_url()` is nonempty, captured resource equals current `resource_url()`, and expiry exceeds the current time.
- [ ] Query connection validity without refreshing tokens or updating last-used timestamps per child.
- [ ] Keep the original credential digest/kind/expiry checks and throttled last-used update in `load_access_token()`.
- [ ] In existing external operation recording, append `_batch` from the trusted correlation context only when present.
- [ ] Verify ordinary provenance remains unchanged; external child failures do not create fake successful audit rows.
- [ ] Run `uv run pytest tests/test_mcp_batch_external.py tests/test_mcp_external.py tests/test_mcp_identity.py -q`.
- [ ] Commit with `feat(mcp): preserve batch caller authority and provenance`.

**Policy boundary:** A validity lookup exception is distinct from a false lookup. Task 5 converts these to `authorization_unavailable` and `authorization_changed` respectively.

## Task 5: Implement one lifecycle-owned scheduler

**Files:** Create `mcp/batch.py` and `tests/test_mcp_batch_runtime.py`.
**Consumes:** Prepared batches, result constructors, async prepared dispatch, trusted grant checker.
**Produces:**

```python
class Execute(Protocol):
    def __call__(self, tool: PreparedTool, session_id: str | None, *,
                 on_start: Callable[[], None]) -> Awaitable[dict]:
        ...

CheckGrant = Callable[[ExternalGrant], Awaitable[bool]]

class BatchRuntime:
    def __init__(self, *, execute: Execute, check_grant: CheckGrant,
                 max_batches: int = 4, max_children: int = 8,
                 per_batch: int = 4, shutdown_grace: float = 5.0):
        # Construct only within the owning running event loop.

    async def run(self, batch: PreparedBatch, *, session_id: str | None) -> dict:
        # Immediate admission, owned coordinator, shielded result delivery.

    async def close(self) -> None:
        # Disable admission, stop coordinators, bounded drain, forced async cancellation.
```

Runtime state consists of an accepting flag, integer admitted count, a global semaphore, and a set of owned coordinator tasks.
Each coordinator owns its child task set, ordered result slots, next input index, stop Event, and optional authorization stop code.
The runtime also retains all active child tasks directly, so a coordinator failure cannot hide them from shutdown.
No task is created for a non-admitted batch. A coordinator creates at most four child tasks, not twenty semaphore-waiting tasks.

- [ ] Write a test proving prevalidation, admission, sequential error policy, and ordering through injected callbacks.

```python
def test_sequential_stop_never_starts_third():
    async def scenario():
        seen = []
        async def execute(tool, session_id, *, on_start):
            on_start()
            seen.append(tool.arguments["session_id"])
            code = 4 if len(seen) == 2 else 0
            return {"exit_code": code, "result": None, "error": None}
        async def check_grant(grant):
            return True
        body = {"calls": [
            {"id": str(i), "name": "session", "arguments": {"session_id": str(i)}}
            for i in range(3)
        ]}
        prepared = validate_batch("batch", body, registry=tools_by_name(),
            read_only_paths=MCP_READ_ONLY_PATHS, external=False, batch_id="test").prepared
        runtime = BatchRuntime(execute=execute, check_grant=check_grant)
        try:
            result = await runtime.run(prepared, session_id=None)
            assert seen == ["0", "1"]
            assert result["results"][2]["error"]["code"] == "previous_call_failed"
        finally:
            await runtime.close()
    asyncio.run(scenario())
```

- [ ] Run `uv run pytest tests/test_mcp_batch_runtime.py -q` before implementing scheduling.
- [ ] Admit synchronously before the first await: check accepting/count, increment count, create a coordinator task, and register its completion callback.
- [ ] Capture caller contexts at admission. Run coordinators as native asyncio tasks outside the SDK AnyIO request task group.
- [ ] Await the coordinator with `asyncio.shield()`. On request cancellation, set its stop Event and re-raise without awaiting drainage in the cancelled request.
- [ ] The coordinator owns child startup serially, including grant checks, so input-order starts and authorization stops have one decision point.
- [ ] Before a child starts: check stop, wait for global capacity interruptibly, check stop again, check grant, check stop again, then create the child without another await.
- [ ] Implement interruptible permit acquisition by racing a semaphore-acquire task with `stop.wait()`. Cancel and consume the losing waiter.
- [ ] If stop and acquisition both complete, release the acquired permit exactly once and do not launch the child.
- [ ] Do not hold the semaphore permit indefinitely in a failed grant check: false or exception releases it and sets the batch authorization stop code.
- [ ] For external batches, capture the grant locally; missing grant context produces `authorization_unavailable`. Internal API-token calls may legitimately have `session_id=None`.
- [ ] Child tasks set/reset session, external caller, grant, and correlation ContextVars in `finally`; execute the injected dispatcher and build a record.
- [ ] Initialize `started=False` in each child. Pass a closure that sets it true as the dispatcher's `on_start` callback.
- [ ] Convert ordinary exceptions to `failed_record(started=started)`. A rendering failure is known unstarted; provenance failure after submission is indeterminate.
- [ ] Release the child permit only after dispatch and provenance settle. Exit code 5 releases it even if a service Future continues.
- [ ] In parallel mode, wait for first completion when four local slots are occupied; collect all settled records before filling slots again.
- [ ] In sequential mode, inspect execution status and unknown outcome before starting the next call. Ignore output omission for stop policy.
- [ ] On an authorization stop, skip all remaining inputs using that code. Existing children finish normally and preserve their results.
- [ ] On request cancellation, stop pending scheduling and drain started children in the owned coordinator. Do not produce a client result promise.
- [ ] Wrap the coordinator scheduling loop in exception handling and `finally` drainage, including unexpected scheduler failures.
- [ ] On an unexpected coordinator error, stop scheduling, retain indexed child records, drain owned children, and mark never-started calls skipped with `execution_error`.
- [ ] These skipped records have `outcome_unknown=False` and `caused_by=None`; completed children retain their own actual statuses.
- [ ] Keep child references in both the coordinator and runtime until completion. Never cancel siblings simply because the coordinator encounters an ordinary exception.
- [ ] The coordinator cannot finish before its children settle during normal operation. Only forced lifespan shutdown may abandon drainage after grace.
- [ ] Release the admission count in the coordinator completion callback, after children settle, not in the request handler's `finally`.
- [ ] Use callbacks to consume coordinator exceptions and keep references until completion. Do not print argument-containing exception messages.
- [ ] Implement `close()` using a snapshot of owned coordinators, their stop Events, and `asyncio.wait(..., timeout=shutdown_grace)`.
- [ ] After grace, explicitly cancel tracked child and coordinator async tasks; consume eventual exceptions via callbacks. Do not join threads or await cancellation indefinitely.
- [ ] Drop the runtime singleton only after admission is disabled; next lifespan creates fresh loop primitives. Never reuse the closed runtime.
- [ ] Run `uv run pytest tests/test_mcp_batch_runtime.py -q`.
- [ ] Commit with `feat(mcp): schedule lifecycle-owned batch calls`.

**Required runtime scenarios:**

| Scenario | Test construction | Required assertion |
|---|---|---|
| Parallel overlap/order | Four entry Events; release in reverse order | Four overlap; output retains input order |
| Global capacity | Four batches with blocked fake calls | Peak child invocations is eight |
| Admission overflow | Four blocked batches, then fifth | Fifth gets rejected `server_busy`; no child task created |
| Child exception | One fake dispatch raises after barrier | Siblings complete; one `tool_error` |
| Thread cancellation | `_run_invoke` blocked on threading.Event | Cancelled request does not release capacity before thread return |
| Coordinator failure | Inject a scheduling exception while a real worker remains blocked | Admission remains occupied; runtime retains the child; release in finally |
| Before submission failure | Make `render_argv` raise before the start callback | `tool_error` with known unstarted outcome; no worker invocation |
| After submission failure | Raise in provenance recording after a worker returns | `tool_error` with unknown outcome; no repeated invocation |
| Repeated cancellation | Fill/cancel/retry before thread release | No capacity bypass |
| Cancel while queued | Consume all global permits; cancel waiting batch | No invocation; no leaked permits |
| Grant invalidation | First grant passes, next returns false | Remaining records skipped; prior result retained |
| Grant exception | Checker raises after one completed child | `authorization_unavailable`, no exception text |
| Partial write timeout | Fake invocation returns 5 with a separate unfinished service Future | Permit released; unknown outcome recorded; no replay |
| Omitted successful response | Oversized first success then second call | Second runs even with sequential stop |
| Runtime close | Fake thread blocks; inject short grace | close returns after grace; release test thread in finally |

Every real-thread test must release its Events in `finally`, even when assertions fail, to avoid hanging pytest executor shutdown.
Test shutdown cancellation under an AnyIO CancelScope too; a normal asyncio-only test is insufficient for the actual SDK lifecycle.

## Task 6: Integrate catalogs, handler routing, and lifecycle

**Files:** Modify `mcp/tools.py`, `mcp/server.py`, `mcp/endpoint.py`; extend MCP catalog, endpoint, and external batch tests.
**Consumes:** All preceding interfaces.
**Produces:** Both advertised tools, shared live runtime, unchanged ordinary call surface.

- [ ] Add the catalog regression before exposing wrappers.

```python
def test_batch_tools_do_not_enter_command_registry():
    tools = {tool.name: tool for tool in iter_mcp_tools()}
    assert {"batch", "batch_read"} <= tools.keys()
    assert "batch" not in tools_by_name()
    assert "batch_read" not in tools_by_name()
    assert tools["batch_read"].annotations.read_only_hint is True
    assert tools["batch"].annotations.read_only_hint is False
    assert tools["batch"].output_schema == BATCH_OUTPUT_SCHEMA
```

- [ ] Run the new catalog and endpoint tests and confirm wrappers are absent before integration.
- [ ] Add two synthetic tool declarations using Task 2 schemas and Task 3 output schema. Assert no generated name collisions.
- [ ] Keep wrappers out of `ALWAYS_LOAD_PATHS`. Preserve the external OAuth securitySchemes metadata on both wrappers.
- [ ] In `_call_tool`, branch on `BATCH_NAMES` before the ordinary schema/error handler. Do not apply `params.arguments or {}` to wrapper inputs before structural validation.
- [ ] Generate the batch UUID, validate, and return rejection immediately when invalid. Otherwise call the common runtime and then `fit_result()`.
- [ ] Supply dispatch callback `execute(prepared, session_id, *, on_start)` that forwards both keywords to `execute_prepared`.
- [ ] Wrap the batch branch with dedicated safe exception logging. No wrapper failure may enter the old `arguments=%r` exception path.
- [ ] Runtime initialization occurs once in `mcp_lifespan()` inside the active loop, shared across both managers. Do not create separate runtime objects for the endpoints.
- [ ] Set `_started=False` and stop runtime admission before shutting down manager task groups.
- [ ] Protect the five-second cleanup with `anyio.CancelScope(shield=True)` so an already-cancelled MCP task can execute its bounded drain.
- [ ] Preserve existing manager single-use reset fixtures and add runtime reset assertions across separate `asyncio.run()` calls.
- [ ] Add authenticated internal HTTP tests using `mint_session_token`, `_rpc`, and the existing `_client()` pattern.
- [ ] Add external HTTP tests using isolated OAuth authorization/token issuance from `tests/test_mcp_external.py`; use real handlers and DB grant rows.
- [ ] Verify both tool representations decode to equal JSON; validate them against the published output schema.
- [ ] Check error classes across HTTP 401/413, MCP rejected result, and completed partial-failure result.
- [ ] Test external identity through a real mutation to a temporary test workspace/session. Assert `_batch` correlation and unchanged sender/share provenance rules.
- [ ] Test aggregate attachments with a small monkeypatched transport body cap and synthetic payloads. Preserve per-command validation fixtures.
- [ ] Add a raw ASGI disconnect harness; explicitly send `http.disconnect` while a child is blocked. Record whether this SDK delivers cancellation.
- [ ] Test SDK cancellation separately from disconnect. Stateless calls may not share a cancellation backchannel between separate POSTs.
- [ ] Run `uv run pytest tests/test_mcp_tools.py tests/test_mcp_server.py tests/test_mcp_endpoint.py tests/test_mcp_batch_contract.py tests/test_mcp_batch_runtime.py tests/test_mcp_batch_external.py -q`.
- [ ] Commit with `feat(mcp): expose internal and external batch tools`.

**Failure boundary:** If runtime initialization is missing during shutdown, no child starts. The batch path returns a safe busy/unavailable rejection.
Unexpected pre-invocation orchestration failures must not claim a command executed. Unexpected post-invocation failures must retain completed records where available and never retry.

## Task 7: Add diagnostics, documentation, and acceptance validation

**Files:** Modify `mcp/batch.py`, `mcp/server.py`, `mcp/endpoint.py`, `frontend/public/help/external-mcp.md`, and `CHANGELOG.md`; extend relevant batch tests.

- [ ] Add a log-capture test with a secret marker in a prompt and invalid key; assert no batch diagnostic contains it.
- [ ] Emit bounded structured events for admission/rejection, child completion, authorization stop, cancellation, shutdown abandonment, and final summary.
- [ ] Use validated tool names and IDs only. Record caller ID, elapsed milliseconds, execution status and known exit code; never arguments or result bodies.
- [ ] Measure authentication at the endpoint, validation in the adapter, permit waiting in the scheduler, dispatch duration in children, and serialization in `fit_result()`.
- [ ] Keep request/auth timing correlation in trusted per-request context or ASGI scope. Do not send timing fields that are absent from the output schema.
- [ ] Update both instruction strings to distinguish ordinary CLI envelopes from batch aggregate envelopes.
- [ ] Add this behavioral guidance to internal and external instructions and external help:

```text
Use batch_read for independent reads and batch for ordered commands.
Discover each command's schema first. Supply its unchanged name and arguments.
All inputs are validated before any command starts. Results retain input order.
Batch does not roll back completed changes or pass results into later arguments.
Keep batches short. Cancellation or a timeout does not prove that writes stopped.
Do not replay an uncertain write batch automatically. Inspect the affected resources.
Large command responses can be omitted explicitly even when the command succeeds.
```

- [ ] Document the exact limits, error policies, `ok` versus execution status, unavailable grants, and long-wait admission limitation.
- [ ] Re-read the top of `CHANGELOG.md`; add a concise entry only under the current `## [Unreleased]`.
- [ ] Do not edit packaged skills for this implementation. MCP descriptions and help are sufficient and avoid an unnecessary plugin version change.
- [ ] Run all focused batch tests, then the MCP regression set once:

```bash
uv run pytest tests/test_mcp_*.py tests/test_rpc_auth.py -q
```

- [ ] Run `git diff --check` on changed files. If Python lint is useful, run `uvx ruff check` on those Python files only.
- [ ] Verify no migrations, dependencies, CLI routes, or unrelated generated assets entered the patch.
- [ ] Commit with `docs(mcp): document batch behavior and validation` after the implementation checks pass.

### Product checks after the implementation is available

Do not start/restart the user's server or send real messages merely to satisfy this document.
Request those actions only when needed and not already authorized. Use temporary test resources for mutations.

| Client | Actions | Evidence to record |
|---|---|---|
| Internal Claude agent | Discover schema; two read calls; sequential safe mutation test | Discovery and correct aggregate handling |
| Internal Codex agent | Same operation set | Deferred-tool discovery and structured results |
| Desktop external client | Authenticate; read batch; authorized write batch | Confirmation UI and correct identity |
| Remote external client | Same operation set | Network/client latency, partial errors, output handling |

Use the same short command set for separate calls, sequential batches, and parallel read batches.
Record command count, payload sizes, warm/cold status, endpoint time, total client time, and several repeated measurements.
Do not assert an arbitrary speedup threshold. The requirement is correct aggregation with fewer tool-call exchanges.
If client/account access is unavailable, record the check as unexecuted and distinguish it from automated test coverage.

## Spec coverage map

| Spec section / acceptance IDs | Implementation tasks |
|---|---|
| 1–3: reuse, two tools, annotations | 1, 6, 7 |
| 4–5; acceptance 1–6, 14, 17, 33 | 1, 2, 6 |
| 6; acceptance 9, 18–20, 27–28, 34–36 | 5 |
| 7; acceptance 11–16, 38 | 4, 5, 6 |
| 8–9; acceptance 7–10, 29–31, 39 | 3, 6 |
| 10; acceptance 21–23, 25–26, 37 | 5, 6 |
| 11; acceptance 24, 32 | 4, 7 |
| 12–14: integration, product checks, delivery | 6, 7 |

## Planning review and delivery

Review this plan against the current code and spec before implementation.
Apply review corrections directly to this document. Keep only a short closure statement here, not a separate review file.
Plan approval and this document's commit do not execute its unchecked implementation steps.

An internal adversarial sub-agent reviews this plan on 2026-09-07.
Corrections cover endpoint test seams, child ownership after coordinator failure, and the invocation-start marker.
The review also validates the spec's explicit tool-result byte boundary.
The same reviewer verifies the revised documents and reports no remaining blockers.
This is a source-based planning review. Implementation and runtime validation remain unexecuted.
