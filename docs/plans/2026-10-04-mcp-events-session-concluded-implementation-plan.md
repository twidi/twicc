# MCP Events — `session.concluded` implementation plan

Date: 2026-10-04.
Status: ready for implementation; no implementation starts with this document.
Source: [spec revision 23](2026-10-03-mcp-events-session-concluded-spec.md).
Review: two adversarial rounds with two internal reviewers; final verdicts PASS, with no remaining actionable findings.

## 1. Goal and authority

Expose `session.concluded` through webhook delivery on the external MCP server.
Reuse `_SessionWait.step()` without changing its detection semantics.
Persist subscriptions, cursor state, turn state, and history numbering.

The spec controls every protocol and behavior decision.
Section references below point to revision 23 of the spec.
If the spec changes, update this plan before implementation.

**Deliverables:** one database migration, the events package, shared HTTPS transport, integration changes, tests, and documentation.

**Excluded:** new CLI commands, RPC routes, MCP tools, skills, frontend UI, persistent outbox, replay, SSE, and polling delivery.
Do not change the internal MCP server.
Do not reopen decisions R1–R11 or remove the accepted limits in §14.

## 2. Execution rules

- Work on the current branch. Do not create a branch or worktree without an explicit request.
- Inspect `git status --short` and file diffs before each task.
- Preserve all changes outside the implementation scope, including untracked files.
- Write code, comments, logs, tests, and documentation in English.
- Use `orjson` for backend JSON.
- Use `NamedTuple` for immutable records. Use a normal class for mutable monitor state.
- Keep database mutations under `twicc.mcp.oauth.storage.write`.
- Do not install packages, restart servers, or apply migrations on the running instance.
- Do not use `uv pip` or `--active`.
- Do not edit dated release sections in `CHANGELOG.md`.
- Create commits only if the implementation request authorizes them. Stage exact files and preserve foreign hunks.
- Each authorized commit uses a Conventional Commit subject, a descriptive body, and the current provider/model trailer.

**Dependency:** the user runs `uv add standardwebhooks` before signing tests or implementation that imports it.
The spec verifies `standardwebhooks` 1.1.0. Confirm the installed API before use; record the resolved version.
Keep the resulting `pyproject.toml` and `uv.lock` changes together.
Do not add another HTTP, SSRF, JSON Schema, or test dependency.

## 3. Repository anchors

These anchors exist in the checkout inspected for this plan.
Line numbers in the spec are navigation aids, not patch targets.

| Anchor | Current behavior | Implementation action |
|---|---|---|
| `src/twicc/mcp/server.py` | Separate `_server` and `_external_server`; existing batch runtime | Register events only on `_external_server` |
| `src/twicc/mcp/endpoint.py` | `external_caller` binds the connection; `mcp_lifespan()` owns managers and batch runtime | Add events startup, shutdown, and cleanup |
| `src/twicc/mcp/oauth/storage.py` | Async `write()` holds the backend write lock | Use it for every subscription mutation |
| `src/twicc/mcp/oauth/provider.py` | `_fetch_metadata()` performs pinned HTTPS with a 64 KiB cap | Extract transport without changing CIMD behavior |
| `src/twicc/cli/session.py` | `wait_reply()` checks an indexed session or a live process | Extract lookup with an events-only option |
| `src/twicc/cli/_wait_reply.py` | `_SessionWait` scans conclusions, requests, and stopped agents | Reuse unchanged |
| `src/twicc/providers/helpers.py` | `BaseProviderHelpers` and provider registry | Add command-line predicate to the provider interface |
| `src/twicc/providers/claude_code/sessions_watcher.py` | Sniffs `<command-name>`, parses JSON, and calls `extract_command()` | Share this predicate with events |
| `src/twicc/providers/codex/history_facts.py` | Recognizes injected commands through restored private source | Share this predicate with events |
| `src/twicc/providers/codex/rollout_migration.py` | Begin deletes history and resets tracking in one transaction | Increment epoch and enqueue an on-commit wake-up |
| `src/twicc/core/serializers.py` | `session_compute_ready()` compares provider compute versions | Reuse the same readiness rule |
| `tests/test_mcp_external.py` | Real SDK HTTP tests, OAuth helpers, stateless managers | Follow this pattern for events wire tests |
| `tests/test_wait_reply.py` | Existing wait semantics and transcript fixtures | Preserve and extend regression coverage |

The inspected dependency range is `mcp>=2.1.1,<3`; the spec checks SDK 2.1.1.
The inspected migration history ends at `0149_remove_redundant_message_index`, with an existing squash replacement.
Resolve the current migration leaf again when generating the new migration.
Do not assign its number from this document.

## 4. File ownership

| File | Responsibility |
|---|---|
| `src/twicc/mcp/pinned_https.py` **new** | Public-address resolution, IP pinning, bounded response, failure classification |
| `src/twicc/mcp/events/__init__.py` **new** | Small runtime access boundary; no startup side effects |
| `src/twicc/mcp/events/catalog.py` **new** | Event definition, schemas, protocol constants, identity helpers |
| `src/twicc/mcp/events/methods.py` **new** | Params models, handlers, capability middleware, subscription transactions, audit |
| `src/twicc/mcp/events/delivery.py` **new** | Payload, IDs, size fit, signatures, verification, authority, retry lifecycle |
| `src/twicc/mcp/events/runtime.py` **new** | Monitor state machine, queues, writer, worker, supervisor |
| `src/twicc/core/models.py` | `Session.history_epoch` and `McpEventSubscription` |
| `src/twicc/core/migrations/<next>_mcp_events.py` **new** | Both model changes in one migration |
| `src/twicc/cli/session.py` | Shared lookup and unchanged CLI cursor selection |
| `src/twicc/providers/helpers.py` | Provider-neutral command-line interface |
| `src/twicc/providers/claude_code/compute.py` | Shared slash-command echo predicate |
| `src/twicc/providers/claude_code/sessions_watcher.py` | Use shared predicate |
| `src/twicc/providers/claude_code/helpers.py` | Route predicate to Claude implementation |
| `src/twicc/providers/codex/compute.py` | Public injected-command predicate |
| `src/twicc/providers/codex/history_facts.py` | Use shared predicate where it replaces the same existing test |
| `src/twicc/providers/codex/helpers.py` | Route predicate to Codex implementation |
| `src/twicc/providers/codex/rollout_migration.py` | Epoch increment and runtime wake-up |
| `src/twicc/mcp/oauth/provider.py` | Call shared transport with the unchanged CIMD profile |
| `src/twicc/mcp/oauth/storage.py` | Subscription cleanup under the existing write lock |
| `src/twicc/mcp/server.py` | External registration and runtime access wiring |
| `src/twicc/mcp/endpoint.py` | Lifespan and periodic cleanup |
| `README.md`, `CLAUDE.md`, `AGENTS.md` | User-facing behavior and maintenance note |

Use lazy imports at integration boundaries that would otherwise create circular imports.
Keep protocol constants in `catalog.py` and protocol error mapping in `methods.py`.
Keep events-specific subscription writes outside the generic OAuth credential logic, except cleanup.

## 5. State ownership and interfaces

These interfaces are proposed implementation seams. Names may change without changing their contracts.

| Owner | Interface | Contract |
|---|---|---|
| `session.py` | `lookup_wait_session(session_id, *, allow_history_epoch=False)` | Return the row, acceptance flag, and live-process result; preserve a rejected row for arrival metadata |
| Provider helpers | `is_user_message_command(content)` | Decide from stored raw JSON using existing provider predicates |
| `pinned_https.py` | `request_pinned_https(method, url, *, headers, body, timeout, max_response_bytes)` | Return status, bounded bytes, and overflow information; never follow redirects |
| `pinned_https.py` | `classify_send_failure(exc)` | Return `timeout`, `tls_error`, or `connection_refused` |
| `catalog.py` | `subscription_id(connection_id, url, name, arguments)` | Canonical identity excludes `since_line_num` |
| `delivery.py` | `build_data(session_id, title, reply, request=None)` | Copy the wait block; remove only `waited_seconds`; add specified fields |
| `delivery.py` | `fit_occurrence(occurrence)` | Return frozen body bytes within 262144 bytes, or an explicit drop result |
| `delivery.py` | Verification service | Own cache, shared in-flight verification, concurrency slots, and host rate windows |
| `delivery.py` | Delivery coroutine | Own attempts; read current authority and secrets before each attempt |
| `runtime.py` | `EventsRuntime.start()` / `close()` | Own thread, writer, supervisor, and delivery tasks |
| Runtime boundary | `add(row)` / `remove(id, created_at)` / `rebase(session_id)` | Enqueue commands; never mutate the monitor table from the loop |

Use immutable snapshots instead of sharing lazy ORM model instances between the loop and the worker.
Every subscription snapshot includes its generation: `(id, created_at)`.
Every cursor write also includes its numbering tag.

**Worker thread owns:** monitors, waits, cursors, turn state, request history, readiness history, failure counters, and dormant state.
**Backend loop owns:** verification tasks, deliveries, writer queue, supervisor, and runtime lifecycle.
**Database owns:** durable subscriptions and session epochs.

The thread uses `queue.Queue` for commands.
The loop uses `asyncio.Queue` for writes and barriers.
The thread posts writer items with `loop.call_soon_threadsafe(queue.put_nowait, item)`.

## 6. Task order

| Task | Scope | Depends on |
|---|---|---|
| 1 | Contract constants and deterministic test fixtures | Spec |
| 2 | Models and migration | 1 |
| 3 | Shared lookup and command predicates | 2 |
| 4 | Pinned HTTPS and CIMD extraction | 1 |
| 5 | Payload, IDs, size fit, signing | 1, user dependency installation |
| 6 | Callback verification | 4, 5 |
| 7 | Subscription handlers and capability middleware | 2, 3, 6 |
| 8 | Writer, queues, monitor load, and commands | 2, 3 |
| 9 | Cursor and turn state machine | 5, 8 |
| 10 | Readiness and durable history rebase | 2, 9 |
| 11 | Authority and delivery retries | 4, 5, 8 |
| 12 | Failure recovery, supervisor, and shutdown | 8–11 |
| 13 | Server integration, lifecycle, cleanup, and audit checks | 7, 12 |
| 14 | Full acceptance coverage and documentation | 1–13 |
| 15 | ChatGPT E2E release gate | 14, user deployment operations |

Implement in this order. Do not expose handlers before the runtime and delivery paths are complete.
Tasks 7–12 may use injected test seams before Task 13 wires the production runtime.

## 7. Detailed tasks

### Task 1 — Freeze contracts and create deterministic fixtures

**Files:** `events/__init__.py`, `events/catalog.py`, `tests/test_mcp_events_catalog.py`, `tests/mcp_events_helpers.py`.
**Spec:** §§2, 5, 6, 8.4, 12.

- [ ] Copy the event definition, `inputSchema`, and `payloadSchema` from the spec.
- [ ] Keep the complete `wait_background` description, including restart and no-new-answer behavior.
- [ ] Define event name, capability extension key, error constants, limits, and timing constants in the owning modules.
- [ ] Implement canonical identity with sorted-key `orjson` serialization.
- [ ] Make missing `wait_background` equivalent to `false`; exclude `since_line_num` from identity.
- [ ] Preserve callback URL identity exactly as supplied after validation; do not invent URL normalization.
- [ ] Test connection isolation, URL/name sensitivity, key-order stability, and starting-cursor independence.
- [ ] Add clock seams for UTC time, float epoch time, and monotonic tick/backoff time.
- [ ] Add fake registry, fake transport, controlled verification futures, and emission/write collectors.
- [ ] Provide small Claude and Codex transcript fixtures that the real `_SessionWait` can read.

Avoid a parallel fake implementation of wait semantics.
Use real `_SessionWait` tests for behavior; use injected failures for scheduling and recovery tests.
Use synchronization events and barriers instead of long sleeps.

**Exit:** contract tests pass; fixtures make no outbound requests or model calls.

### Task 2 — Add durable subscription state and session epoch

**Files:** `core/models.py`, one new migration, `tests/test_mcp_events_storage.py`.
**Spec:** §§7.6, 9.1, 9.2.

- [ ] Add `Session.history_epoch = PositiveIntegerField(default=0)`.
- [ ] Add `McpEventSubscription` beside the other `Mcp*` models.
- [ ] Implement every field, type, length, nullability, index, and FK from §9.1.
- [ ] Keep `session_id` as an indexed string, without a Session FK.
- [ ] Store `cursor_at` and `turn_started_at` as floats, without datetime conversion.
- [ ] Allow `numbering = NULL` for a subscription created during replacement.
- [ ] Use `created_at` as the generation; preserve it on refresh and renew it on replacement.
- [ ] Bind new rows to `str(get_data_dir().resolve())`.
- [ ] Keep secrets outside owner/public snapshots, owner responses, audit targets, and logs.
- [ ] Permit secrets in private runtime snapshots and authority snapshots used to sign attempts.
- [ ] Generate one migration for both model changes against the current graph.
- [ ] Check existing rows get epoch zero; check connection deletion cascades to subscriptions.
- [ ] Check a subscription exists before its Session row and survives absence of that row.
- [ ] Check float transition timestamps survive a save/load without rounding to datetime precision.

No data migration, compute-version bump, Session serialization field, or frontend change is required.
Apply migrations only to an isolated test database during validation.

**Exit:** schema tests pass; migration graph has no new conflict.

### Task 3 — Share lookup and provider command predicates

**Files:** `cli/session.py`, provider files in §4, `tests/test_mcp_events_lookup.py`, `tests/test_mcp_events_commands.py`.
**Regression tests:** `tests/test_cli_sessions_wait_reply.py`, `tests/test_wait_reply.py`, existing affected provider tests.
**Spec:** §5.2 step 6 and §7.3 guard.

- [ ] Extract the wait-reply lookup without changing default cursor selection or CLI errors.
- [ ] Read an existing row even when its indexed-session acceptance filter fails.
- [ ] Return row acceptance separately from row presence and live-process acceptance.
- [ ] Add the optional `created_at` plus `history_epoch > 0` acceptance clause for events only.
- [ ] Let events read `last_line`, epoch, offset, and creation metadata from the arrival lookup.
- [ ] Preserve CLI behavior when a live process has a row that fails the old filter: its default cursor remains zero.
- [ ] Factor Claude's stored-content sniff, parse, text extraction, and `extract_command()` test into `compute.py`.
- [ ] Make the watcher call the shared predicate with its existing malformed-content behavior.
- [ ] Expose Codex's `_injected_command_text(_restore_private_source(parsed))` predicate.
- [ ] Replace the equivalent predicate use in `history_facts.py` to keep one source of truth.
- [ ] Route both predicates through `BaseProviderHelpers` and the two provider helpers.
- [ ] Implement one ordered, indexed USER_MESSAGE query for the first non-command prompt past a line.
- [ ] Use that query for initial `history` turn state and the transition guard.

Test actual command echoes, quoted `<command-name>` text, malformed JSON, and ordinary prompts.
Test Codex stored `twiccOriginalContent`; typed slash text must not become an injected command.
Test `/rename`, `/compact`, `/goal clear`, and bare `/plan` with their real provider formats.

**Exit:** both providers classify commands consistently; CLI wait-reply behavior remains unchanged.

### Task 4 — Extract pinned HTTPS without changing CIMD

**Files:** `mcp/pinned_https.py`, `mcp/oauth/provider.py`, `tests/test_mcp_pinned_https.py`, affected OAuth tests.
**Spec:** §§5.3, 8.2.

- [ ] Move DNS resolution and connection pinning from `_fetch_metadata()` into the helper.
- [ ] Require every returned address to be global; reject an empty answer or a mixed public/private answer.
- [ ] Raise `NonGlobalAddressError`, an `OSError` subclass, for address refusal.
- [ ] Connect to the selected IP; preserve the original Host and SNI hostname.
- [ ] Preserve path, query, explicit port, and IPv6 formatting.
- [ ] Disable redirects and environment proxy settings.
- [ ] Bound response reads and expose overflow separately from HTTP status.
- [ ] Preserve CIMD GET, status-200 requirement, 64 KiB cap, 5 s client timeout, and 10 s outer deadline.
- [ ] Preserve `_metadata_slots`, metadata parsing, client validation, and failure-to-`None` behavior.
- [ ] Configure webhook POSTs with a 10 s overall deadline and 4 KiB response cap.
- [ ] Traverse cause/context chains without loops; classify timeouts before TLS errors.
- [ ] Exclude `SSLWantReadError`, `SSLWantWriteError`, `SSLEOFError`, `SSLZeroReturnError`, and `SSLSyscallError` from TLS failures.
- [ ] Classify other send exceptions as `connection_refused`; log unexpected exceptions with traceback.

Use mocked DNS and HTTP for address-policy tests.
Use real local sockets for SSL exception-chain tests, through a test-only pinning seam.
The production helper continues to reject loopback.

Required socket cases: stalled handshake, stalled post-handshake read, outer timeout, TLS to plain HTTP, and ClientHello then close.
Check certificate failure maps to `tls_error`; DNS failure maps to `connection_refused`.
Check the artifact broker policy and implementation remain unchanged.

**Exit:** transport and exception tests pass; existing CIMD contracts still pass.

### Task 5 — Build payloads, IDs, body fitting, and signatures

**Files:** `events/delivery.py`, `tests/test_mcp_events_payload.py`, `tests/test_mcp_events_signing.py`.
**Spec:** §§6, 8.1, 8.4, 8.5.

- [ ] Build `data` from the exact `step()` reply block, without calling a second conclusion formatter.
- [ ] Remove `waited_seconds`; retain line number, final flag, starting cursor, and text presence.
- [ ] Always add `session_id` and nullable `session_title`.
- [ ] Add `request_type` only for the pending request selected by Rule B.
- [ ] Build the occurrence envelope with `cursor: null`.
- [ ] Use request creation time for awaiting; item timestamp for replied/provider error; tick time for ended.
- [ ] Fall back to tick time when the transcript timestamp is absent.
- [ ] Compute deterministic IDs with the exact NUL-separated keys in §8.4.
- [ ] Include epoch in every line-based key; include request ID for awaiting.
- [ ] Use the emission snapshot's `last_line`, or zero without a row, for ended without a line.
- [ ] Serialize the complete envelope with `orjson` and freeze the fitted bytes for all delivery attempts.
- [ ] If necessary, binary-search the longest whole-code-point text prefix with `text_truncated: true` included.
- [ ] Verify the next code point exceeds 262144 bytes; test JSON escaping and multibyte Unicode.
- [ ] Keep `text_truncated` absent when text fits. Preserve empty provider-error text.
- [ ] Drop and log an event whose non-text fields cannot fit; specify the drop result for the monitor.
- [ ] Sign the exact UTF-8 body with `Webhook.sign()` and an aware UTC datetime from the attempt's integer timestamp.
- [ ] Set all five headers from §8.1, including the subscription ID.
- [ ] Support current and previous signatures, separated by a space, during the five-minute rotation window.

Validate all four outcome data objects with `jsonschema` against the declared payload schema.
Verify signatures with `standardwebhooks.Webhook.verify()` and independently check header timestamp equality.
Check IDs stay stable across generations and change across history epochs.

**Exit:** body shape, byte limit, maximal prefix, ID, and signature tests pass.

### Task 6 — Implement callback verification

**Files:** `events/delivery.py`, `tests/test_mcp_events_verification.py`.
**Spec:** §§5.3, 8.3.

- [ ] Build and sign the challenge with the incoming secret and derived subscription ID.
- [ ] Accept only a 2xx response with an object containing the identical string challenge.
- [ ] Parse with `orjson`; compare encoded bytes through `hmac.compare_digest()`.
- [ ] Map overflow, bad JSON, missing challenge, non-string challenge, or mismatch to `challenge_failed`.
- [ ] Map redirects to `challenge_failed`; never follow them.
- [ ] Map 4xx/5xx to the closed protocol reasons; use Task 4 for transport exceptions.
- [ ] Cache successful `(connection_id, url)` verification for 24 hours, in memory only.
- [ ] Share one in-flight future for concurrent callers with the same cache key.
- [ ] Give only the leader a semaphore slot and per-host rate entry.
- [ ] Limit active verifications to eight; wait at most five seconds for a slot.
- [ ] Enforce 60 verifications per rolling minute per callback hostname.
- [ ] Release slots and in-flight entries on success, failure, and cancellation.
- [ ] Propagate the same protocol result to joiners without duplicate challenges.

Test ten concurrent subscriptions, cache expiry, cross-connection separation, the ninth slot timeout, and the 61st host request.
Test non-ASCII challenge strings and a JSON body containing a lone surrogate.
Do not use real callback destinations in automated tests.

**Exit:** verification tests reproduce every error reason and both resource limits.

### Task 7 — Implement methods and external discovery middleware

**Files:** `events/methods.py`, `tests/test_mcp_events_methods.py`.
**Spec:** §5 in full, §§7.2, 7.3 initial values, 9.1.

- [ ] Define three params models using SDK `RequestParams`, `Any = None` fields, and `extra="allow"`.
- [ ] Accept `_meta` and unknown top-level keys; perform strict field validation inside handlers.
- [ ] Map errors through SDK `MCPError` with the exact code and `data` from §5.3.
- [ ] Return the single definition from `events/list` without pagination.
- [ ] Follow all twelve subscribe processing steps, in the specified order.
- [ ] Capture arrival time before verification and retain it for both row classifications and TTL grant.
- [ ] Validate JSON Schema arguments; convert an accepted `since_line_num: 3.0` to integer 3.
- [ ] Validate secret type, prefix, padded strict base64, and 24–64 decoded bytes.
- [ ] Validate HTTPS URL, ASCII hostname, valid port, length, no credentials, and no fragment.
- [ ] Catch `urlsplit()` and `.port` failures, including malformed IPv6 URLs.
- [ ] Reject booleans and non-finite floats for TTL before verification; clamp integers directly before timedelta creation.
- [ ] Apply default/null TTL of 24 hours; clamp numeric values to one hour through seven days.
- [ ] Capture arrival `L0` and numbering from the lookup; store NULL numbering only for the documented replacement marker.
- [ ] Derive identity even when session lookup fails, so a live subscription can still refresh.
- [ ] Pre-check new-subscription limits: 50 per connection, 100 total, current data dir, live at arrival.
- [ ] Verify outside the database write critical section.
- [ ] Reclassify inside one `storage.write` transaction; recheck limits and the retained lookup-failure flag.
- [ ] Refresh only expiry, secrets, and stored arguments. Preserve generation, cursor, numbering, L0, and turn state.
- [ ] Replace expired/foreign rows with a fresh generation and initial state.
- [ ] Initialize working turns as `initial`; otherwise initialize from the first actual prompt as `history`.
- [ ] Audit actual creation once, in the mutation transaction, without auditing refreshes.
- [ ] Enqueue a full-row `add` after commit for both creation and refresh.
- [ ] Return id, ISO UTC `refreshBefore`, null cursor, and `truncated` based only on non-null incoming cursor.
- [ ] Implement unsubscribe using connection-bound identity; delivery mode is optional.
- [ ] Validate identity field types without enforcing the complete subscribe schema or checking session existence.
- [ ] Delete idempotently; audit only actual deletion; enqueue a generation-bound remove after commit.
- [ ] Implement middleware that patches only the already-serialized `server/discover` result after `call_next(ctx)`.
- [ ] Preserve existing capabilities; add both `events: {}` and the extension entry.
- [ ] Provide an explicit registration function for Task 13; do not register on the internal server.

**Required concurrency tests:** identical inserts, limit contention, deleted-during-verification refresh, and expired/foreign replacement.
Test a failed lookup whose live row disappears before the write: `-32011`, without insertion.
Test a timely refresh that finishes past expiry: it retains its generation while the cleanup margin protects its row.

Test a reply and a prompt/crash during verification: creation uses arrival L0, not the post-verification end.
Test secret rotation, successful-cache behavior, and verification failure leaving an existing monitor unchanged.
Test non-null incoming cursor, `_meta`, invalid scalar params, and every §12 validation case.

**Exit:** handlers pass contract and transaction tests using a fake runtime boundary.

### Task 8 — Implement writer, monitor loading, and generation commands

**Files:** `events/runtime.py`, `tests/test_mcp_events_writer.py`, `tests/test_mcp_events_runtime.py`.
**Spec:** §§7.1, 9.1, 9.2.

- [ ] Define immutable command/write records and mutable monitor state.
- [ ] Include cursor, cursor time, L0, numbering, turn fields, reported requests, readiness snapshot, and expiry.
- [ ] Create the worker's `queue.Queue` and loop's `asyncio.Queue` once per runtime.
- [ ] Load only current-data-dir, unexpired rows with unrevoked connections.
- [ ] Restore persisted state without recomputing initial turn state from the current agent.
- [ ] Set `first = cursor_line < initial_last_line`; create `_SessionWait` with `want_text=True` and the existing live-PID resolver.
- [ ] Set `transport.backend_loop` inside the worker thread before reading agent state.
- [ ] Let only the thread own the monitor table; drain commands before each tick.
- [ ] Add absent generations; update same generations; replace different generations.
- [ ] Ignore stale update/remove commands from old generations.
- [ ] Update only arguments, expiry, and rotation fields for an existing monitor.
- [ ] Preserve in-memory cursor, turn state, request IDs, and wait during an active refresh.
- [ ] Mark expired monitors dormant; wake a same-generation refresh with preserved state and a fresh wait.
- [ ] Run one FIFO writer; catch per-item errors and continue; always resolve barrier items.
- [ ] Match every mutation by id and generation.
- [ ] Persist turn changes immediately in producer order; persist silent cursor moves immediately.
- [ ] Implement SQL cursor MAX behavior with numbering-tagged line updates and independent monotonic `cursor_at` updates.
- [ ] Provide a dedicated non-monotonic rebase CAS write for Task 10.

Test real SQL updates, not only queue contents.
Test an old generation cannot mutate a replacement row and an old epoch cannot advance its new cursor.
Test older delivery completion cannot regress either cursor field after a later completion or silent move.

**Exit:** loading, command, dormant, generation, writer, and monotonicity tests pass.

### Task 9 — Implement continuous conclusions and Rules A/B

**Files:** `events/runtime.py`, `tests/test_mcp_events_turns.py`, `tests/test_mcp_events_background.py`.
**Spec:** §§7.2–7.5, 7.7.

- [ ] Tick each active monitor every `POLL_INTERVAL_SECONDS` (currently 0.25 seconds).
- [ ] Capture tick time; read `get_agent_info()` once for transition detection and Rule B.
- [ ] Recognize new turns only with working state, changed previous state, and both timestamp comparisons from §7.3.
- [ ] Implement all three new-turn branches, including closing an ignored-background turn before opening another.
- [ ] Compute `turn_start_line` from the pre-move cursor, stored reference, and ignored line exactly as specified.
- [ ] Silently advance to `scanned_up_to` when needed; preserve `cursor_at`; always replace the wait for a new turn.
- [ ] Post new-turn state writes before `step()`; keep these effects if a later operation fails.
- [ ] Call `_SessionWait.step()` unchanged.
- [ ] Emit replied/provider error without outcome filtering.
- [ ] Drop ended immediately when no turn is open.
- [ ] Apply transition-only ended guard using a carried line, agent death, or the shared prompt query.
- [ ] Close guard-dropped turns and advance `turn_start_line` past the observed transcript end.
- [ ] Preserve later turns when an earlier transcript conclusion has an older timestamp.
- [ ] Close `history` turns on their first replied/provider error, regardless of timestamp.
- [ ] Renew earlier-conclusion guard references using the conclusion line before any L0 jump.
- [ ] Always close emitted ended turns; use tick time and the post-step session end for state and ID.
- [ ] Emit one new pending request per tick in registry order; retain the last 256 reported IDs across cursor changes.
- [ ] On awaiting, set `turn_opened_by = awaiting`; open an absent turn with request creation time and current cursor.
- [ ] Move cursor for replied/provider error to its line; move ended to the wait's `scanned_up_to`.
- [ ] For the first historical conclusion, jump to at least L0 and end `first`; awaiting does neither.
- [ ] Keep the same wait for dropped conclusions; do not reset stopped timers on every idle tick.
- [ ] Pass `wait_background` unchanged; never synthesize delivery of an ignored final when work ends without another answer.
- [ ] Keep hidden, mute, archive, Apprise, and browser broadcasts outside event eligibility.

Use this emission order: `step()` → guard query → timestamp read → Session snapshot → epoch/readiness decision → ID/data/fit → post.
The Session snapshot is the last database read, not the last computation.
Use its title, epoch, and last line for the payload and ID.
Complete every fallible computation before posting; Task 10 adds the epoch/readiness decision.
Prepare the post-emission state delta before posting; enqueue emission, then writes, then apply memory effects.

The emission's durable cursor write occurs after delivery finishes, through Task 11.
Turn-state writes occur immediately; awaiting never writes a new cursor.

**Exit:** idle, history, transition, pending-request, watcher-lag, pseudo-turn, and background scenarios pass.

### Task 10 — Protect readiness and implement durable history rebase

**Files:** `events/runtime.py`, `codex/rollout_migration.py`, `tests/test_mcp_events_rebase.py`, `tests/test_mcp_events_readiness.py`.
**Regression:** `tests/test_codex_rollout_migration.py`.
**Spec:** §7.3 readiness, §7.6, §9.1.

- [ ] Add one primary-key snapshot query for epoch, provider, compute version, last line, and title.
- [ ] Include offset in the arrival lookup used to recognize a mid-replacement subscription.
- [ ] Preserve the latest snapshot taken before `step()` as the previous readiness read.
- [ ] Treat no Session row as ready, epoch zero, last line zero.
- [ ] For ended, require both reads ready with equal epoch and last line.
- [ ] Drop an unready ended without closing its turn or replacing its wait.
- [ ] Retain readiness history across wait replacement and turn changes.
- [ ] Read snapshot on load/wake, emission, each pending-rebase tick, each rebase command, and every five seconds as backstop.
- [ ] Avoid readiness queries on idle, non-pending ticks outside the five-second epoch backstop.
- [ ] At emission, compare epochs after every other read, regardless of emit/readiness-drop/guard-drop outcome.
- [ ] On mismatch or NULL numbering, suppress all emission-phase writes and enter pending rebase.
- [ ] While pending, skip `step()` and poll the single snapshot until compute is current.
- [ ] Apply cursor, L0, and guard line to that snapshot's end; set `first=false` and create a fresh wait.
- [ ] Keep cursor time and reported request IDs.
- [ ] At apply, read current agent state: working becomes an `initial` turn; otherwise close the turn and clear its start time.
- [ ] Enqueue one rebase CAS matching generation and old numbering, including `IS NULL` when required.
- [ ] Keep pending when an already-pending monitor finds no Session row.
- [ ] Increment epoch with an `F()` expression in the existing begin/reset UPDATE.
- [ ] Post `rebase(session_id)` only through `transaction.on_commit` beside existing begin hooks.
- [ ] Make the wake-up a no-op when events runtime is absent, including `TWICC_NO_MCP` and offline compute.
- [ ] Leave finish-time tracking, compute classification, migration leases, and snapshot-share reanchoring unchanged.

Test shorter and longer rebuilt histories, creation before begin, creation mid-replacement, and two overlapping rebuilds.
Test readiness commit between scan and final read; it must not produce a false ended.
Test raw insertion between two ready reads: changed last line prevents ended until the next classified scan.

Test restart while pending, partial replacement repair, lost wake-up, lost rebase CAS, dormant rebase, and supervisor reload.
After a lost CAS, the running monitor keeps its new numbering; persisted line writes remain blocked until a later load heals it.
Test a working turn already observed before pending begins: apply opens it from current agent state.

**Exit:** every numbering interleaving is protected without changing wait-reply detection or compute versions.

### Task 11 — Implement delivery authority and bounded retries

**Files:** `events/delivery.py`, `events/runtime.py`, `tests/test_mcp_events_delivery.py`.
**Spec:** §§8.6, 8.7, 9.1.

- [ ] Track delivery tasks on the backend loop; the monitor never waits for a callback.
- [ ] Before every attempt, read the generation-bound row and related connection.
- [ ] Suppress gone, replaced, or expired subscriptions.
- [ ] Delete revoked/resource-mismatched subscriptions and enqueue matching generation removes.
- [ ] If external MCP is unconfigured, suppress delivery and retain the subscription.
- [ ] Do not use access-token expiry as subscription authority.
- [ ] Read current and previous secrets from the authority snapshot for each attempt.
- [ ] Send immediately; retry after 30 seconds, then after 120 seconds; stop after three attempts.
- [ ] Retry connection refusal, timeout, 408, 425, 429, and 5xx.
- [ ] Do not retry TLS failure, redirects, 410, 413, or other statuses.
- [ ] Do not delete a subscription because the receiver returns 410.
- [ ] Reuse frozen body and event ID; generate timestamp and signatures again for each attempt.
- [ ] After terminal completion, enqueue cursor and cursor-time persistence regardless of delivery success or authority suppression.
- [ ] Do not persist a delivery cursor when its coroutine is cancelled.
- [ ] Record outcome/status/category without logging secrets or reply text.

Test secret rotation between attempts, unsubscribe during retry, re-creation during retry, and revocation from every existing path.
Test that resource change deletes while empty `base_url()` retains.
Test newer completions and silent moves cannot be reversed by a slow retry.

**Exit:** transport status matrix, authority matrix, cancellation, and cursor-completion tests pass.

### Task 12 — Implement failure containment, supervisor, and shutdown

**Files:** `events/runtime.py`, `tests/test_mcp_events_recovery.py`.
**Spec:** §7.1, §14 failure limits.

- [ ] Catch each monitor's tick exception; call `django.db.close_old_connections()` and continue other monitors.
- [ ] On transient failure, rebuild the wait from unchanged cursor so a scanned conclusion is detected again.
- [ ] Track consecutive failures at `(cursor, scanned_up_to)`; reset counters on successful ticks.
- [ ] Back off from 0.25 seconds, doubling to a 60-second cap.
- [ ] After three failures at the same position, keep the failed wait to skip a poison batch.
- [ ] Mark a persistently failing pending request reported; keep retrying a failing ended at backoff pace.
- [ ] Log the first traceback and at most one summary per minute per monitor.
- [ ] Supervise thread and writer independently every five seconds; retain both queues when restarting their consumer.
- [ ] Before thread reload, restart a dead writer and enqueue a barrier with loop `call_soon`.
- [ ] Wait for barrier or writer termination; restart a dead writer and continue awaiting the same barrier.
- [ ] Load durable state only after the barrier resolves; retain commands committed during rebuilding.
- [ ] On shutdown, stop supervisor and signal the worker without blocking the loop.
- [ ] Join with `await asyncio.to_thread(thread.join, 5)`.
- [ ] Once stopping begins, suppress last-tick emissions and every state delta tied to them.
- [ ] Cancel in-flight deliveries without persisting their cursors.
- [ ] Restart a dead writer before draining; drain at most two seconds, then cancel.
- [ ] Release thread-local database connections on thread exit.

Inject failures after `step()` in timestamp reads, guard queries, payload construction, and fitting.
Check retry produces the same event ID; check no fallible operation remains after emission is posted.
Test a failed writer item does not block a barrier, and a writer death while awaiting the barrier recovers.

Test last-tick ended leaves the durable turn open and emits after restart.
Test an already-running ended has closed turn state and follows the documented restart limit.
Test failure backoff isolates one monitor without delaying all other monitors through intentional sleeps.

**Exit:** monitor, writer, thread, rebuild, and shutdown fault tests pass.

### Task 13 — Wire external server, lifespan, cleanup, and audit

**Files:** `mcp/server.py`, `mcp/endpoint.py`, `oauth/storage.py`, events runtime boundary.
**Tests:** `tests/test_mcp_events_external.py`, existing MCP startup/endpoint/external tests.
**Spec:** §§5.1, 5.2 Audit, 7.1, 9.2–9.4.

- [ ] Register three custom methods and one middleware on `_external_server` exactly once.
- [ ] Keep internal handlers and capability output unchanged.
- [ ] Start events runtime inside `mcp_lifespan()` next to batch runtime before setting `_started=true`.
- [ ] Ensure startup failure cleans up every runtime already started.
- [ ] Close both runtimes under the existing shield; clear runtime references even if a close fails.
- [ ] Add subscription cleanup to the 60-second loop with independent error containment from OAuth cleanup.
- [ ] Delete only current-data-dir rows expired more than 60 seconds or attached to revoked connections.
- [ ] Capture deleted generations under `storage.write`; post removes after the database commit.
- [ ] Keep foreign rows intact and exclude them from loading, cleanup, and counts.
- [ ] Keep event subscriptions outside the owner snapshot and UI.
- [ ] Check creation/deletion audit targets and connection provenance through real handlers.
- [ ] Keep refreshes unaudited; never add secrets to audit.
- [ ] Confirm revocation through owner action, token revocation, refresh-token reuse, and `revoke_all` stops the next attempt.

Use authenticated real SDK HTTP calls at protocol `2026-07-28`.
Assert discovery contains both capabilities after actual serialization, not only a direct middleware call.
Assert custom responses carry SDK `resultType: "complete"` in stateless JSON mode.

Assert legacy initialize exposes no events capability and internal requests cannot call these handlers.
Assert events work with no browser WebSocket connected.
Assert the batch runtime and existing external tool calls still work.

**Exit:** external wire tests, startup/stop tests, cleanup tests, and existing MCP regression tests pass.

### Task 14 — Complete acceptance coverage and update documentation

**Files:** event tests, `README.md`, `CLAUDE.md`, `AGENTS.md`; optional authorized Unreleased changelog entry.
**Spec:** §§11, 12, 14.

- [ ] Review every unit-test bullet in spec §12 and map it to a passing test.
- [ ] Use the coverage table below to identify cross-task gaps.
- [ ] Test declared accepted limits without claiming guaranteed delivery or full replay.
- [ ] Add the external webhook event sentence to both project instruction files.
- [ ] Add the README sentence about subscription and answer text sent to the client's callback URL.
- [ ] Do not change plugin skills or plugin version.
- [ ] Record the installed SDK and signing package versions in validation notes.
- [ ] Review the final diff for unauthorized provider behavior, dependency changes, and API additions.
- [ ] Record automated checks actually run, their result, and any remaining manual release gate.

**Exit:** spec §12 has complete automated coverage; only the recorded E2E release gate remains.

### Task 15 — Run ChatGPT E2E before release

**Spec:** §12 mandatory E2E, §13 user operations.

- [ ] Ask the user to complete dependency installation, running-instance migration, and backend restart through `devctl.py`.
- [ ] Use a ChatGPT Work chat connected to the external MCP.
- [ ] Confirm `server/discover` works with both capability forms and `session.concluded` appears on the plugin page.
- [ ] Subscribe to one session, then two sessions, then ten sessions; verify all subscriptions succeed.
- [ ] Confirm replied events supply answer text directly to the chat.
- [ ] Confirm a truncated answer remains accessible through the content tools.
- [ ] Confirm questions and approvals produce one event per request with the correct request type.
- [ ] Confirm `since_line_num` delivers the first conclusion that precedes subscription but follows the cursor.
- [ ] Refresh across backend restart, unsubscribe, and revoke the connection; confirm delivery stops as specified.
- [ ] Exercise the instructed event/send feedback loop; stop it through monitoring cancellation and connection revocation.
- [ ] Record observations, callback status, package versions, and pass/fail without storing credentials or signing keys.

This task requires the user's connected ChatGPT environment.
If unavailable, mark E2E pending and release blocked; do not claim completion from mocked tests.

Optional conformance uses the draft suite and a bearer-injecting proxy.
Its localhost HTTP receiver cannot pass production callback validation.
Webhook tests need a public HTTPS receiver tunnel configured through `EVENTS_WEBHOOK_CALLBACK_BASE`.
Never relax the production HTTPS/public-address rules to run that suite.

## 8. Acceptance coverage map

Every row requires named tests, including the spec's detailed interleavings.
This table supplements §12; it does not replace its full list.

| Family | Required assertions | Owner |
|---|---|---|
| Identity | Stable canonical keys; connection isolation; cursor excluded; unsubscribe identity matches | 1, 7 |
| Params | `_meta`/extras accepted; strict types; missing secret; bad base64; unpadded secret accepted | 7 |
| URL | Empty host, malformed IPv6, wrong type, Unicode hostname, invalid port, credentials, fragment, excessive length rejected | 7 |
| TTL | Absent/null/zero/negative/too-large clamped; 400-digit int safe; 1e400, NaN, infinities, string, bool rejected before verification | 7 |
| Line schema | `3.0` normalizes to 3; negative and values above 2147483647 rejected | 7 |
| Arrival race | Reply during verification delivered; prompt/crash during verification opens history turn | 7, 9 |
| Write classification | Identical calls converge; expired row replacement at limit succeeds; foreign rows ignored; missing refresh row becomes new | 7 |
| Lookup race | Existing live subscription refreshes despite lookup failure; deletion before final classification yields unknown_session | 3, 7 |
| Refresh memory | Pending delivery and queued state remain newer than row; same-generation refresh cannot restore stale cursor/turn | 8 |
| Dormancy | Fresh wait on wake; preserved request/turn state; complete dormant turn has no premature ended; margin protects timely refresh | 7, 8, 13 |
| Payload | CLI shape for all outcomes; null title; absent/empty text; request type; timestamps; schema validation | 5 |
| Size | Full body within 256 KiB; longest prefix; Unicode/escapes; no truncation marker when unchanged | 5 |
| Signing | Exact bytes; UTC timestamp/header agreement; two signatures; retry uses rotated secret | 5, 11 |
| Verification | Echo, non-string/non-ASCII challenge, lone surrogate, overflow, bad JSON, all response/error reasons | 4, 6 |
| Verification limits | Shared challenge; joiners consume no slot/rate; ninth slot waits; 61st host verification rejected; 24 h cache | 6 |
| Idle/answer | Idle never emits ended; replied while working then idle has no ended; a later new final still emits replied | 9 |
| History | No prompt means no ended; prompt/crash means ended; several past conclusions yield one; history always closes on first conclusion | 9 |
| Initial/history overlap | Historical replied before an initial running turn does not discard that turn's later empty ended | 9 |
| Pending requests | One per tick; no repeat across cursor/L0 jump; answer before block delivered first; stop after awaiting yields one ended | 9 |
| Turn timing | Idle gap shorter than tick; watcher lag before/after next turn's death; earlier conclusion leaves current turn open | 9 |
| Turn references | Same-state reset ignored; old prompt never satisfies later pseudo-turn guard; renewal never regresses guard line | 9 |
| Pseudo-turns | Hybrid rename/local command/adoption; Codex compact/cold goal clear/cold plan suppressed by transition guard | 3, 9 |
| Dead process | No assistant line and dead agent passes guard; two line-writing crashes produce distinct IDs | 9 |
| Interrupted turn | Pseudo-turn during flush keeps existing turn reference; every new transition gets a fresh wait and flush window | 9 |
| Late hybrid transition | Final indexed before its own transition: one replied, no spurious ended; pseudo-transition before late final behaves likewise | 9 |
| Background | Ignored then real final; shell reopening; idle without answer; death carrying ignored text; next crash after silent move | 9 |
| Background pseudo-turn | Pseudo-turn while ignored closes old turn; Codex user message during hold then compact produces no ended | 9 |
| Cursor/time | Ended uses scanned end; old carried line never regresses cursor time; non-monotonic item timestamps do not regress it | 8, 9 |
| Readiness | Boot raw final withheld; compute between scan/read withheld; ready/raw/compute/ready with changed end withheld; missing row ready | 10 |
| No-row creation | Live process starts at epoch zero; row appearance does not rebase; death before indexing emits ended; fresh Codex offset zero keeps epoch zero | 7, 10 |
| Query budget | Idle non-pending monitor performs no readiness read outside the five-second backstop | 10 |
| Epoch update | Begin increments epoch and resets tracking atomically; rollback posts no wake-up; only real begin changes epoch | 2, 10 |
| Rebase durability | Restart pending; partial replacement repair; compute failure/restart; consumed/lost wake-up; dormant and backstop recovery | 10 |
| Rebase overlap | Two begins around CAS; new emission epoch mismatch suppresses all emission-phase writes | 10 |
| Rebase creation | Arrival before begin; mid-replacement NULL; no history flood; Claude stale compute retains supplied cursor | 7, 10 |
| Rebase turn | Working at apply opens initial turn even when already observed before pending; queued-during-rebuild turn also opens | 10 |
| Rebase write loss | Failed CAS leaves durable cursor frozen by tag; next load reapplies at current end without duplicate delivered conclusion | 8, 10, 12 |
| Epoch IDs | Same line in a rebuilt history has another ID, including unsubscribe/re-subscribe | 5, 10 |
| Rebuild closure | An old open turn closes at rebase; no empty ended follows unless an agent works at apply | 10 |
| Restart | Open turn dies; older replay keeps newer turn open; first historical replay remains bounded; pending request retains ID | 9, 12 |
| Generation | Expired row recreation resets monitor; late removes, retries, and writes cannot touch new generation | 7, 8, 11 |
| Writer | Item failure continues; barrier resolves; dead writer restarted on same queue before/during reload and shutdown | 8, 12 |
| Supervisor | FIFO barrier covers scheduled prior writes only; retained commands apply after reload; thread restarts | 12 |
| Float reload | Reload during a hold after replied does not reopen the same transition or produce ended | 2, 12 |
| Tick faults | Post-step failure re-detects same ID; one monitor cannot kill others; poison batch advances after three failures | 12 |
| Fault limits | Backoff cap and bounded logs; pending request skipped after three failures; ended retains retry pace | 12 |
| Shutdown | Last-tick emission/state dropped; delivery cancellation skips cursor; writer drains; dropped ended emits next boot | 12 |
| Delivery statuses | 408/425/429/5xx retry; TLS/3xx/410/413/other 4xx do not; 410 retains row | 11 |
| Authority | Unsubscribe/expiry/generation suppress; revocation/resource change delete; empty config retains; token expiry irrelevant | 11, 13 |
| Instance | Foreign data dir never loads, cleans up, delivers, or counts; same-path copy remains an accepted limit | 7, 13 |
| Audit | One row per creation/deletion, none per refresh; correct connection and targets; no secrets | 7, 13 |
| Wire scope | Both discovery forms; complete result type; internal and legacy capabilities unchanged; no browser needed | 13 |
| Regression | CLI wait, provider command handling, Codex rebuild, CIMD, batch, OAuth, and lifecycle unchanged | 3, 4, 10, 13 |

## 9. Validation commands

Run targeted tests as each task completes. Record actual commands and results.
Use transaction-enabled Django tests for real async/threaded database access.
Keep callback transport mocked except the explicit local SSL-chain tests.

Main checkout commands:

```bash
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_mcp_events_*.py tests/test_mcp_pinned_https.py -q
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_wait_reply.py tests/test_cli_sessions_wait_reply.py tests/test_codex_rollout_migration.py -q
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_mcp_*.py -q
cd /home/twidi/dev/twicc-poc && uvx ruff check <changed-python-files>
cd /home/twidi/dev/twicc-poc && git diff --check
```

Also run affected provider tests selected from the command-predicate changes.
Run the broader suite once if integration reveals effects outside the targeted modules.
Tests and lint are planned validation, not commands executed while writing this plan.

If the user requests a worktree, prefix every command with its explicit directory.
Set `TWICC_DATA_DIR=$PWD` for Python/Django invocations in that worktree.
Check the resolved database path before any data-dependent ad-hoc invocation.
Do not apply the migration to the user's running database during automated validation.

## 10. Completion and release conditions

**Implementation is complete when:**

- All tasks through 14 finish and every automated acceptance scenario passes.
- `_SessionWait` stays unchanged and only the external server exposes events.
- One migration contains both schema changes.
- Callback verification, signed delivery, generation isolation, and numbering recovery work together through real SDK HTTP tests.
- Documentation describes callback text disclosure and the accepted delivery limits.

**Release requires:** the mandatory ChatGPT Work E2E in Task 15.
Automated tests cannot prove ChatGPT accepts discovery or handles event-triggered tasks correctly.

**User operations:** `uv add standardwebhooks`; apply migrations through the user's instance workflow; restart the backend through `devctl.py`.
`devctl.py` startup applies migrations automatically. Do not also run `migrate` manually for server startup.

**Accepted limits remain explicit:** retries disappear on restart; replay is bounded; dormant and rebuild windows can lose conclusions.
Failed state writes heal only as specified; no-row/no-line and pseudo-turn cases keep their documented limits.
External MCP configuration loss can suppress delivery while the durable cursor advances.
Use spec §14 as the full limit register; this implementation must not claim exactly-once or guaranteed delivery.

## 11. Adversarial plan review record

Review date: 2026-10-04. Two internal reviewers perform two rounds of read-only review.
The spec already passes numerous review rounds; its decisions and accepted limits remain authoritative.
Reviewers assess plan fidelity, execution order, repository integration, and acceptance coverage.

| Round | Runtime/persistence reviewer | Protocol/delivery reviewer | Result |
|---|---|---|---|
| 1 | PASS; two Minor clarifications | PASS; same two Minor clarifications | Correct both ambiguities |
| 2 | PASS; no actionable findings | PASS; no actionable findings | Plan ready for implementation |

**Corrections:** Task 2 permits secrets in private runtime/authority snapshots while prohibiting them in public snapshots.
Task 9 explicitly places the last database read before event ID, payload construction, and body fitting.
Both reviewers verify the corrected sequence and perform a second adversarial pass.

No Critical or Important issue is reported in either round.
No implementation code changes or implementation tests run during this review.
The automated checks and ChatGPT E2E release gate remain pending implementation.
