# MCP Events — `session.concluded` on the external MCP

Date: 2026-10-03 — Status: design (spec), not implemented. Revision 23 (after twenty-one adversarial review rounds).

## 1. Problem and goal

An external MCP client (ChatGPT today) drives TwiCC sessions through the external MCP (OAuth, dedicated public origin,
`src/twicc/mcp/endpoint.py:65`). To learn that a session answered, it must call `session_wait_reply` (or a send with
`wait_reply`). That call blocks inside one HTTP request. It is bounded by the client's and the proxy's timeouts, and the
external MCP design explicitly refuses special adapters for long waits
(`docs/plans/2026-09-05-external-mcp-design.md:253`).

ChatGPT now supports **MCP Events** with **webhook delivery** (OpenAI, "MCP Events",
<https://developers.openai.com/plugins/build/mcp-events>, read 2026-10-03). It implements the draft of the MCP
Triggers & Events Working Group
(<https://github.com/modelcontextprotocol/experimental-ext-triggers-events/blob/main/docs/design-sketch-proposal.md>,
merged 2026-09-08). With it, the client subscribes once, and the server POSTs a signed event to a callback URL when
something happens. ChatGPT then runs a task in the subscribed chat with the event data.

**Goal.** Expose exactly one event, `session.concluded`. It is the event form of what `wait_reply` already waits for:
"the session concluded past my cursor, and here is how". Nothing more.

## 2. Recorded decisions

These were decided before this spec. They are not reopened here.

| # | Decision |
|---|---|
| R1 | One event only: `session.concluded`. |
| R2 | `outcome` is a **payload field**, never a subscription filter. `wait_reply` does not filter outcomes; it concludes and says how. |
| R3 | Subscription by explicit `session_id` only. No project, workspace, spawn-tree or annotation selectors in v1. |
| R4 | Subscription arguments: `session_id` (required), `wait_background` (bool), `since_line_num` (optional int). No `include_text`: text is always included. |
| R5 | `since_line_num` closes the send → subscribe race. A conclusion already past that cursor is delivered at once. |
| R6 | Subscriptions are **persisted** (one small table). There is **no** persistent outbox or retry queue for deliveries. |
| R7 | `mute_on_user_turn` and `hidden` never filter an explicit MCP subscription. |
| R8 | Detection reuses the semantics and the code of `_SessionWait.step()`. It never uses Apprise or the `USER_TURN` transition. |
| R9 | The capability is announced in **both** forms: top-level `capabilities.events` and `capabilities.extensions["io.modelcontextprotocol/events"]`. An E2E ChatGPT test is mandatory. |
| R10 | Signing uses the `standardwebhooks` Python package. |
| R11 | Payload adds `session_title` and `request_type` (for `awaiting_user_input`). `eventId` is deterministic. Text is truncated **only** when the 256 KiB body limit requires it, keeping as much text as fits. |

## 3. Design constraints

**C1 — One contract, the `wait-reply` one.** The event `data` reuses the JSON that `twicc session <ID> wait-reply`
already returns: `{"session_id": ..., "reply": {...}}` (`src/twicc/cli/session.py:636`). The `reply` block comes from
the same code: `_SessionWait.build()` (`src/twicc/cli/_wait_reply.py:430`), plus the `text` that `step()` adds for a
provider error (`:528-531`). The event layer adds only fields the transport needs or that R11 decided. It never builds a
parallel representation of a conclusion.

**C2 — One detection logic.** The condition "the session concluded past the cursor" is decided by
`_SessionWait.step()` (`src/twicc/cli/_wait_reply.py:459`) and nothing else. The event layer only decides **which
conclusions to emit** and **when to move the cursor** (section 7). It does not re-implement replied / awaiting /
provider error / ended detection, and it does not change `_SessionWait`.

**C3 — Simple first.** No outbox, no replay, no UI, no new CLI command, no skill change. Delivery retries live in memory
only.

**C4 — External MCP only.** The internal MCP (`_server`, `src/twicc/mcp/server.py`) does not change. Its clients
(Claude Code, Codex) do not support MCP Events.

## 4. Scope and non-goals

In scope: `events/list`, `events/subscribe`, `events/unsubscribe` on the external MCP server; capability announcement;
the detection runtime; signed webhook delivery with callback verification; persistence and lifecycle of subscriptions.

Not in scope (v1):

- `events/poll`, `events/stream`, the `gap` and `terminated` control envelopes (ChatGPT supports none of them).
- Replay (`cursor` is always `null`).
- Any other event (peer messages, usage, shares, workflows).
- Selectors other than `session_id` (R3).
- An owner UI to list subscriptions. Revoking the MCP connection removes them (section 9.4).
- The `/.well-known/mcp-webhook-receiver.json` verification path and asymmetric signing (`v1a,`).

## 5. Protocol surface

All three methods and the capability exist only on `_external_server` (`src/twicc/mcp/server.py:313`). The caller is the
`McpConnection` already bound by the endpoint in the `external_caller` ContextVar
(`src/twicc/mcp/endpoint.py:110-113`). That connection is the **principal** of every subscription.

### 5.1 Capability

A server middleware, appended to `_external_server.middleware`, patches the wire result of `server/discover` after
`call_next`:

```json
"capabilities": {
  "tools": {"listChanged": false},
  "events": {},
  "extensions": {"io.modelcontextprotocol/events": {}}
}
```

Why a middleware: the SDK serializes `server/discover` through the wire model `ServerCapabilities`
(`mcp_types/_v2026_07_28/__init__.py:3552`, `extra="ignore"` at `:3557`), which has no `events` field. A top-level
`events` key returned by a handler is dropped (verified on `mcp` 2.1.1 with `mcp_types.methods.serialize_server_result`).
A middleware receives the already-serialized dict (`mcp/server/runner.py:225-230`) and may add the key (verified by
experiment on a stateless JSON manager at protocol `2026-07-28`). `extensions` survives serialization, but the
middleware sets both for one code path.

Only `server/discover` is patched. ChatGPT requires protocol `2026-07-28`, whose handshake is `server/discover`; legacy
`initialize` clients get no events capability.

`Server.middleware` is marked provisional in the SDK (`TODO(L54)`, `mcp/server/lowlevel/server.py:432`). A test pins the
patched `server/discover` output so an SDK upgrade cannot remove the capability silently.

### 5.2 Methods

Registered with `_external_server.add_request_handler(method, params_model, handler)`. For the SDK these are custom
methods: they skip the spec surface validation, are validated against `params_model`, and get `resultType: "complete"`
automatically (`mcp/server/runner.py:193-216`, `:393`). They work in the current stateless JSON mode: only
`subscriptions/listen` needs SSE (`mcp/server/_streamable_http_modern.py:419`, `:455`).

**Params models.** The SDK validates the whole `params` object, including `_meta`, against `params_model`. The three
models therefore subclass the SDK's `RequestParams` (so `_meta` parses), declare `name`, `arguments`, `delivery`,
`cursor` and `ttlMs` (as applicable) typed **`Any = None`**, and **allow extra keys**. Typed fields would let pydantic's
lax mode coerce before the handler runs (`ttlMs: "5"` → `5.0`, `ttlMs: true` → `1.0`, a non-string `name` → the SDK's
generic `-32602` with empty data; verified on `mcp` 2.1.1). Field-level validation is done in the handler, so each
failure maps to the error of section 5.3 instead of a generic validation error.

#### `events/list`

Returns one definition. No pagination.

```json
{
  "events": [{
    "name": "session.concluded",
    "description": "A TwiCC session concluded past the cursor: it answered, it is blocked on a question or an approval, the provider refused the turn, or the turn ended without an answer. Same conclusion as the session_wait_reply tool.",
    "delivery": ["webhook"],
    "inputSchema": {
      "type": "object",
      "properties": {
        "session_id": {"type": "string", "minLength": 1, "description": "Full id of the session to watch."},
        "wait_background": {"type": "boolean", "default": false, "description": "A final message read while background work runs behind the agent (a subagent, background shell, Monitor, scheduled wake-up or goal) does not count; the event fires on the first final message read once that work has ended. If the agent then starts a new turn, the ignored message is skipped and the new turn's conclusion is delivered. If no new answer comes and the agent stays idle, no event fires; the ignored message stays readable with the session content tools (after a server restart it can still be delivered as replied once the background work has ended). A dead agent fires 'ended' carrying it."},
        "since_line_num": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "On a new subscription: deliver the first conclusion strictly after this transcript line, even if it already happened. Pass the last_line a send returned."}
      },
      "required": ["session_id"],
      "additionalProperties": false
    },
    "payloadSchema": "<the schema of section 6.3>"
  }]
}
```

#### `events/subscribe`

Params (OpenAI shape): `name`, `arguments`, `delivery: {mode, url, secret}`, `cursor`, optional `ttlMs`.

Processing order:

0. Shape: a missing or non-string `name`, a missing or non-object `delivery`, a missing `delivery.mode`, a missing
   or non-object `arguments`, or a `ttlMs` that is present and neither a finite number nor `null` (the SDK parses bodies
   with stdlib `json`, which accepts `NaN` / `Infinity`; `NaN` would break the clamp; booleans are checked first:
   `isinstance(True, int)` is true in Python) → `-32602`. The request's **arrival time** is taken here; every expiry
   judgement of this request (steps 8 and 11) uses it.
1. `name` must be `session.concluded`, else `-32011`.
2. `delivery.mode` must be `webhook`, else `-32014`.
3. `arguments` validated against the `inputSchema` with `jsonschema` (already a dependency), else `-32602` with a
   reason. `jsonschema` accepts `3.0` as an integer, so `since_line_num` is converted with `int()` after validation.
4. `delivery.secret`: a string with the `whsec_` prefix and a value that decodes to 24–64 bytes, else `-32602` (a
   missing or non-string secret included). Decoding: pad the value
   with `=` to a multiple of 4 (unpadded secrets are accepted, as the `standardwebhooks` constructor accepts them), then
   `base64.b64decode(..., validate=True)` so characters outside the base64 alphabet are refused (the constructor alone
   would decode them leniently).
5. `delivery.url`: `https` scheme, a **non-empty ASCII** hostname (`urlsplit("https:///x").hostname` is `None`; the pinned helper sends the original `Host` header, and a
   non-ASCII one raises `UnicodeEncodeError` in httpx; an IDN must be sent in its `xn--` form), a valid port when one is
   given, no userinfo, no fragment, at most 2048 characters, else `-32602`. A non-string `delivery.url`, or any
   `ValueError` raised by `urlsplit()` itself (`urlsplit("https://[::1/x")`: "Invalid IPv6 URL") or by `.port`, is also
   `-32602`. Address checks happen at connection time (section 8.2).
6. Session lookup: the same rule as `session <ID> wait-reply` (`src/twicc/cli/session.py:618-626`): a `Session` row
   with `created_at` set and `user_message_count > 0`, or a live process (`_live_session_ids`,
   `src/twicc/cli/session.py:651`), **or** a row with `created_at` set and `history_epoch > 0`. Else `-32011`.
   `hidden`, `archived` and `mute_on_user_turn` are not checked (R7).
   The last clause is an events-only extension of the shared lookup. Why: a Codex history rebuild resets
   `user_message_count` to 0 in its begin step (`apply_contribution_changes(..., repair=True)` recounts the deleted
   items, `providers/live_aggregates.py:76`, `:146`), the new items stay unclassified until the session's compute
   restores the count (`providers/compute_base.py:3004`, `:3352`), and the agent is dead during a rebuild, so without
   it every subscribe and refresh of that session would fail with `unknown_session` for the whole rebuild window. A
   session with `history_epoch > 0` had a history. The shared lookup takes the clause as an option, so
   `session wait-reply` keeps its current behaviour.
   **A refresh never fails this lookup**: when the lookup fails, the handler still derives the id (step 7) and
   classifies the row (step 8); if the row is **live**, the request continues as a refresh (its cursor and `numbering`
   come from the row, not from this read). Only a new subscription gets `-32011`. This covers a session subscribed
   while live, before any user message was indexed, whose agent died since. The handler keeps a **lookup failed** flag:
   if step 11's second classification then finds no live row (deleted meanwhile by an unsubscribe or the cleanup), the
   "new" branch returns `-32011` instead of inserting.
   The lookup is shared code, factored out of `session.py`, not copied. The same read gives `L0`: the `last_line` of
   the session's row when any `Session` row exists (even one that fails the lookup filter above, for a live session),
   else `0`, at the request's arrival (section 7.2): a conclusion written during the verification of step 10 is then
   past the starting cursor of a new subscription without `since_line_num`, and is delivered. The same read also gives
   the session's `history_epoch`, stored as the row's `numbering` — `0` when the session has no row, `NULL` when it is
   mid-replacement (`last_offset == 0` with `history_epoch > 0`) (section 7.6).
7. **Identity.** The identity arguments are `session_id` and `wait_background` (default `false` written explicitly),
   serialized as canonical JSON (sorted keys). `since_line_num` is **not** part of the identity: it is a starting
   cursor, not a filter (section 7.2). This deviates from the draft, where all `arguments` are part of the identity
   ("a subscribe call with a different value for any of them addresses a different subscription"). Following the
   draft would create one subscription per send to the same session (each send returns a new `last_line`), each one
   firing on every later conclusion. The deviation is invisible to a client that resends the same arguments on refresh,
   as ChatGPT does. The subscription id is
   `"sub_" + sha256(connection_id \x00 url \x00 name \x00 canonical_identity_json).hexdigest()[:32]`.
   The same identity rule serves `events/unsubscribe`.
8. **Classify** the row with this id (pre-check): **live** (same `data_dir`, `refresh_before` after the arrival time)
   → a refresh; **expired** (same `data_dir`, `refresh_before` at or before the arrival time, not yet removed by
   cleanup) or **foreign** (another `data_dir`, section 9.2) or **none** → a new subscription. The draft treats a
   subscription after expiry as fresh. This classification only selects the pre-checks below; step 11 classifies again
   and that second classification decides.
9. Limits, for a new subscription only: at most 50 subscriptions per connection and 100 in total, counting only rows of
   the current `data_dir` whose `refresh_before` is after the arrival time (the expired row being replaced does not
   count), else `-32013` with `data: {"limit": "subscriptions", "max": <n>}`. This is a cheap pre-check before the
   verification.
10. Callback verification (section 8.3), unless `(connection_id, url)` was verified less than 24 h ago. Failure:
    `-32015` with `data.reason`. On a refresh, a failed verification leaves the existing row and its monitor unchanged.
11. **Write**, in one `storage.write` critical section (section 9.1). Verification can take up to 15 s, during which the
    row can be cleaned up or created by a concurrent identical subscribe, so the row is classified again here, with the
    same rules and the same arrival time:
    - **live** → refresh: `refresh_before`, `secret` (with rotation, section 8.1) and the stored arguments are updated;
      the cursor and the turn state are never moved, and `numbering` and `initial_last_line` are never changed (the
      epoch and `L0` read at step 6 serve only a new row; storing them on a refresh would mask a pending rebase and
      break its compare-and-set, section 7.6). This also covers a concurrent identical subscribe that inserted the
      row first: the second one becomes a refresh, not a second insert.
    - **expired**, **foreign** or **none** → new: the authoritative limit check of step 9 runs again (it can still return
      `-32013` after a successful verification), the expired or foreign row, if any, is deleted, and a new row is
      inserted with a new generation and the starting cursor and turn state of section 7.
    After the commit, the handler posts an **`add`** carrying the full row (generation, cursor, `numbering`, turn state, secrets,
    `refresh_before`), for a refresh as for a new row. What the runtime does with it is defined in section 7.1: for a
    monitor of the same generation — running, or **dormant** because its `refresh_before` passed while the
    verification ran — it changes only `refresh_before`, the secrets and the arguments, and a dormant monitor resumes
    ticking with its own in-memory state and a fresh `_SessionWait` from its cursor (section 7.1, Expiry); the full row is used only when no monitor of that generation exists (a new
    row, a supervisor rebuild, a restart). No wait on the writer is needed.

Result: `{"id": "sub_…", "refreshBefore": "<ISO 8601 UTC>", "cursor": null, "truncated": <bool>}`. `truncated` is
`true` only when the request carried a non-null `cursor` (TwiCC never issues one, so no history can be resumed).

TTL grant (`refreshBefore` = the request's arrival time + the granted lifetime, like every other expiry judgement of the
request): `ttlMs` absent → 24 h. `ttlMs: null` (no expiry requested) → 24 h, a finite grant, as OpenAI allows
("Otherwise, return a finite expiration"). `ttlMs` number → clamped to [1 h, 7 days] (OpenAI allows a minimum lifetime);
zero or a negative number is clamped up to 1 h, as the draft gives TTL values no rejection path. A `ttlMs` that is
neither a finite number nor `null` (`NaN`, `±Infinity`, a string, a boolean) is refused at step 0. "Finite": an `int`
(not a `bool`) is always finite and is clamped directly — stdlib `json` parses a 400-digit number as an `int`, and
`math.isfinite()` on it raises `OverflowError` — while only a `float` is tested with `math.isfinite()` (`1e400` parses
as `inf`). The clamp runs before any `timedelta` is built. ChatGPT refreshes by calling `events/subscribe` again
before `refreshBefore`.

#### `events/unsubscribe`

Params: `name`, `arguments`, `delivery: {mode, url}`. `delivery.mode` is optional (the draft's own example omits it). A
missing or malformed `name`, `arguments` (not an object) or `delivery.url` → `-32602`. The `arguments` are not
validated against the `inputSchema` here: only the identity arguments are read (`session_id`, `wait_background` with
its default). The id is derived from the caller's connection with the identity rule of step 7. The row is deleted if it exists and its monitor stops. Always returns `{}`: OpenAI asks for an
idempotent unsubscribe, so an unknown subscription is not an error (the draft's `-32011` for this case is deliberately
not used). Another connection's subscription can never match, because the connection is part of the id.

#### Audit

`events/subscribe` that **creates** a subscription and `events/unsubscribe` that **deletes** one each write one
`McpOperation` row like a tool call (`execute_prepared`, `src/twicc/mcp/server.py`): `tool` = the method name, `targets`
= `{"session_id": …, "subscription_id": …}`. Refreshes are not audited (one every few hours per subscription, no
information). These rows count in the telemetry group `"other"` (`src/twicc/telemetry/snapshot.py:315-317`), which is
acceptable for v1.

### 5.3 Error codes

| Code | Name | Used for | `data` |
|---|---|---|---|
| `-32602` | InvalidParams | arguments, secret, URL | `{"reason": …}` |
| `-32011` | NotFound | unknown event name; unknown session | `{"kind": "event"}` for an unknown name (the draft's `kind`); `{"reason": "unknown_session"}` for an unknown session |
| `-32013` | ResourceExhausted | subscription limits; verification limits | `{"limit": "subscriptions" \| "concurrent_verifications" \| "verifications_per_minute", "max": n}` |
| `-32014` | Unsupported | delivery mode other than `webhook` | `{"feature": "deliveryMode", "value": <mode>}` |
| `-32015` | CallbackEndpointError | callback verification failed | `{"reason": r}`, `r` ∈ the draft's closed list: `connection_refused`, `timeout`, `tls_error`, `http_4xx`, `http_5xx`, `challenge_failed` |

Mapping for `-32015`: DNS failure, a non-global address, or a refused connection → `connection_refused`; deadline
exceeded → `timeout`; TLS handshake or certificate error → `tls_error`; a `4xx` / `5xx` status → `http_4xx` /
`http_5xx`; a `2xx` with a missing or different challenge → `challenge_failed`. The list has no category for a
redirect: a `3xx` (never followed) is reported as `challenge_failed`, because the endpoint did not echo the challenge.

How the classifier reads exceptions: httpcore wraps `ssl.SSLError` into a connect error, and httpx raises
`httpx.ConnectError` both for a refused connection and for a certificate failure. So the classifier walks the whole
exception chain (`__cause__` / `__context__`), once per category, **in this order**. The order matters: a real timeout
over TLS always carries an `ssl.SSLWantReadError` (an `ssl.SSLError` subclass) deeper in its `__context__` chain — for
example `httpx.ConnectTimeout → httpcore.ConnectTimeout → TimeoutError → CancelledError → ssl.SSLWantReadError`, and the
same for a read timeout or the outer `asyncio.wait_for` deadline (verified with httpx 0.28) — so the timeout test must
come first, and the "want read / want write" errors are not TLS failures.

1. an `httpx.TimeoutException` or a `TimeoutError` (`asyncio.TimeoutError` is an alias) anywhere → `timeout`;
2. else an `ssl.SSLError` that is not an `ssl.SSLWantReadError`, `ssl.SSLWantWriteError`, `ssl.SSLEOFError`,
   `ssl.SSLZeroReturnError` or `ssl.SSLSyscallError` anywhere → `tls_error`. The excluded ones are I/O conditions, not
   TLS failures: a peer that closes during the handshake (a proxy or load balancer dropping the connection) raises
   `ssl.SSLEOFError`, which is transient and falls into rule 3, so it is retried (a
   real TLS failure is `httpx.ConnectError → httpcore.ConnectError → ssl.SSLError`, linked through `__cause__`);
3. else (`socket.gaierror`, the helper's own non-global-address refusal, any other `httpx.TransportError` — connect,
   read, write, protocol errors — `OSError`, or **any other exception** raised while connecting or sending, such as an
   `httpx.InvalidURL`) → `connection_refused`. An exception that is neither an `httpx.TransportError` nor an `OSError`
   is most likely a bug in TwiCC's own send path: it is logged with its traceback at every occurrence
   before being classified. For a verification
response: a `2xx` body over the 4 KiB cap, not JSON, or without the right `challenge` → `challenge_failed`.

`-32012` (Forbidden) is not used: the only scope is `twicc:full`, and every authenticated connection may watch every
session, as with the existing tools.

## 6. Payload

### 6.1 Envelope

One `EventOccurrence` per POST (OpenAI, "Send an event"):

```json
{
  "eventId": "evt_…",
  "name": "session.concluded",
  "timestamp": "<ISO 8601 with timezone>",
  "data": { … },
  "cursor": null
}
```

- `timestamp`: the occurrence time, as OpenAI asks.
  - `awaiting_user_input`: the reported `PendingRequest.created_at` (a float epoch, converted with the existing
    `created_at_iso()`, `src/twicc/providers/pending_question.py:135`).
  - `replied`, `provider_error`: the `SessionItem.timestamp` of the line at `reply.line_num` when it has one, else the
    tick's start time (section 7.3, "Times").
  - `ended`: the tick's start time (section 7.3, "Times"), the moment the stop was established, never the time of the
    older line it carries.
  The first two are stable across re-detections (for `replied` / `provider_error`, when the line has a timestamp;
  `SessionItem.timestamp` is nullable, `core/models.py:801`). An `ended` that was sent is not re-detected; one dropped at
  shutdown is re-detected at the next start (section 7.1), but it was never sent, so its timestamp has no earlier
  value to match.
- `eventId`: section 8.4.

### 6.2 `data`

```json
{
  "session_id": "…",
  "session_title": "…",
  "request_type": "ask_user_question",
  "reply": {
    "outcome": "replied",
    "line_num": 123,
    "is_final": true,
    "since_line_num": 98,
    "text": "…",
    "text_truncated": true
  }
}
```

| Field | Origin | Rule |
|---|---|---|
| `session_id` | same key as the `session wait-reply` output | always |
| `reply` | the block `step()` returns | as returned, with the two changes below |
| `reply.outcome` | `step()` | one of `replied`, `awaiting_user_input`, `provider_error`, `ended`. `step()` returns nothing else; the other outcomes of the module (`timeout`, `pending`, `backend_gone`, `wait_failed`) come from the batch loop and never reach an event. |
| `reply.line_num`, `reply.is_final`, `reply.since_line_num` | `step()` | unchanged |
| `reply.text` | `step()` with `want_text=True` | exactly as the CLI: present when a message exists for `replied` / `ended` (`build()`, `:438-439`); always present for `provider_error`, possibly `""` (`:528-531`); absent for `awaiting_user_input` |
| `reply.waited_seconds` | `build()` | **removed**: it measures a blocking wait and has no meaning in an event |
| `reply.text_truncated` | event layer | present and `true` only when `text` was cut (section 8.5) |
| `session_title` | `Session.title` | always present, `null` when unknown (R11) |
| `request_type` | `PendingRequest.request_type` | present only when `reply.outcome` is `awaiting_user_input`: the type of the reported request (`ask_user_question`, `tool_approval`, `hybrid_terminal`) (R11) |

`background_work_in_progress` never appears. It is added only by `build_cut_short()` (`src/twicc/cli/_wait_reply.py:442`,
`:455-457`), which serves `timeout` and `pending`, two outcomes events never carry.

**Factoring.** One function builds `data` from a `step()` block: it removes `waited_seconds` and adds `session_id`,
`session_title` and `request_type`. The size fit (section 8.5) is the only place that touches `text`.

### 6.3 `payloadSchema`

OpenAI requires `data` to match it. The descriptions are schema documentation; the payload itself carries no
instruction (OpenAI: "do not add instructions telling the model how to behave inside the event payload").
`additionalProperties` is left open, so a later additive field does not break the contract (the draft relies on
additive schema evolution).

```json
{
  "type": "object",
  "properties": {
    "session_id": {"type": "string", "description": "Full id of the session."},
    "session_title": {"type": ["string", "null"], "description": "Session title, null when unknown."},
    "request_type": {"type": "string", "description": "Only for awaiting_user_input: ask_user_question, tool_approval or hybrid_terminal."},
    "reply": {
      "type": "object",
      "properties": {
        "outcome": {"type": "string", "enum": ["replied", "awaiting_user_input", "provider_error", "ended"], "description": "Same values and meaning as the reply.outcome of session_wait_reply."},
        "line_num": {"type": ["integer", "null"], "description": "Transcript line of the message, usable with the session content tools. Null when there is none."},
        "is_final": {"type": ["boolean", "null"], "description": "Whether that message closed a turn. Null when there is no message or when it is unknown."},
        "since_line_num": {"type": "integer", "description": "The cursor this conclusion was searched after."},
        "text": {"type": "string", "description": "Text of the message or of the provider error. Absent when there is none."},
        "text_truncated": {"type": "boolean", "description": "Present and true only when text was cut to fit the delivery size limit; the full text is readable with line_num."}
      },
      "required": ["outcome", "line_num", "is_final", "since_line_num"]
    }
  },
  "required": ["session_id", "session_title", "reply"]
}
```

## 7. Detection runtime

### 7.1 Structure

One `EventsRuntime`, started and stopped inside `mcp_lifespan()` (`src/twicc/mcp/endpoint.py:143`), next to the batch
runtime.

- **Monitors.** One monitor per active subscription. A monitor holds: one `_SessionWait` (the current wait), the
  cursor and `cursor_at`, the turn state (`turn_open`, `turn_started_at`, `turn_opened_by`, `turn_start_line`,
  section 7.3), the reported request ids (section 7.4), a `first` flag (section 7.2), its `numbering` (section 7.6), and
  `refresh_before`, the secrets and the arguments of its row.
- **One worker thread.** It ticks every monitor every `POLL_INTERVAL_SECONDS` (0.25 s, `src/twicc/cli/_wait_reply.py:80`),
  the way `wait_for_replies` ticks a batch (`:603`).
  - The thread is a plain `threading.Thread`, which does not inherit ContextVars. Its first action is
    `transport.backend_loop.set(loop)` with the backend loop, so `_agent_activity` reads the in-memory agent registry
    (`src/twicc/cli/_wait_reply.py:301`) instead of `ProcessRun`, the same path an MCP tool call takes through
    `asyncio.to_thread` (`src/twicc/mcp/server.py`, `execute_prepared`).
  - The monitor table is owned by the thread. The loop never touches it directly: subscribe, unsubscribe, expiry and
    revocation post commands (add / update / remove, each for one subscription generation; and `rebase`, keyed by
    session, section 7.6) to a `queue.Queue`, which the thread drains at the start of each tick. A dormant monitor
    that wakes with a rebase pending keeps waiting for it (section 7.6).
  - Every add / update / remove command carries the row's generation, `(id, created_at)` (section 9.1). An `add` for an id whose monitor has
    the **same** generation is applied as an `update` (concurrent identical subscribes cannot create two monitors); an
    `add` with a **different** generation replaces the monitor. `update` and `remove` apply only to a monitor of the
    same generation, so a late `remove` of an old generation never stops a re-created subscription.
  - **What an update changes**: only `refresh_before`, the secrets (with `previous_secret_until`) and the arguments.
    The cursor, `cursor_at`, the turn state, the reported request ids and the current `_SessionWait` are never taken
    from a command for a running monitor: the in-memory values are newer than the row (the cursor is persisted only
    after a delivery ends, and turn-state writes wait in the writer queue). The full row is used only when a monitor is
    **created** (an `add` with no monitor for that id, or with a different generation).
- **Order of a tick that emits.** The rule covers the emission phase (section 7.3, steps 3 and 4). Everything that can
  raise runs **before** the emission is posted: `step()`, the guard query, the timestamp read, then **the one session
  read** last (section 7.6: epoch, readiness, `last_line` and title in one snapshot; the readiness drop and the epoch check
  are decided on it), then the `eventId`, the `data` build and the size fit. Then the emission is posted, then its state
  writes, then its in-memory effects (cursor move, reported request id, turn-state change) are applied. Nothing that can
  raise comes after the emission is posted, so a posted emission is never re-detected by the failure rule below.
  New-turn detection (section 7.3, step 1) runs earlier in the tick and applies its state writes and its in-memory
  effects together, before `step()`; a later failure in the same tick leaves them applied, which is consistent (the
  fresh wait starts from the moved cursor).
- **Failure handling.** Each monitor's tick runs inside its own `try/except Exception`. A failure (for example a locked
  SQLite, the reason the CLI has `wait_for_reply_or_degrade`, `:215-243`) runs `django.db.close_old_connections()` so a
  broken thread-local connection is not reused, then:
  - **Transient failure** (the first consecutive ones): the monitor's `_SessionWait` is **replaced** by a fresh one from
    the monitor's cursor, which is unchanged. `step()` moves `scanned_up_to` before it returns `replied` /
    `provider_error` (`_wait_reply.py:484-485`), so keeping a wait that failed after that point would skip the
    conclusion for good, while a fresh wait re-detects it with the same `eventId`. The cost is a lost `last_ignored`
    (section 14).
  - **Persistent failure**: each monitor counts its consecutive failures. The monitor is not ticked again before a
    backoff (0.25 s doubled per failure, capped at 60 s). After 3 consecutive failures at the same
    `(cursor, scanned_up_to)`, the failed wait is **kept** instead of replaced: its moved `scanned_up_to` skips the
    batch that keeps failing (a poison line), and that one conclusion is lost (section 14). When the failing
    conclusion is an `awaiting_user_input`, its request id is marked as reported after 3 failures (lost, section 14),
    so later requests are still reported. A failing `ended` has no batch to skip: it is retried at the backoff pace
    until a new turn replaces the wait (section 14). A tick without failure resets the counter.
  - Logging: the first failure of a series is logged with its traceback and the subscription id; the following ones
    are summarized at most once per minute per monitor.
  No `wait_failed` event exists.
- **Supervisor.** A supervisor task on the backend loop checks, every 5 s and independently of each other, the worker
  thread and the writer task, and restarts whichever died (a restarted writer consumes the same queue). The command
  queue is kept across a thread restart (commands committed meanwhile are not lost). Before the restarted thread
  loads its monitors from the database (section 9.2), the supervisor first checks the writer task and restarts it if it
  died (a dead writer would never resolve the barrier), then posts a **barrier** into the writer queue — with
  `loop.call_soon(queue.put_nowait, barrier)`, so it lands after the writes the dead thread already scheduled with
  `call_soon_threadsafe` — and awaits it together with the writer task
  (`asyncio.wait({barrier, writer_task}, return_when=FIRST_COMPLETED)`): if the writer dies first, it is restarted on
  the same queue and the await continues. Every state write posted before the barrier is then committed, and the wait
  is bounded by the items ahead of it, not by later writes.
- **Expiry.** At each tick the thread makes **dormant** the monitors whose `refresh_before` has passed: a dormant monitor
  is not ticked and emits nothing, but keeps its in-memory state (cursor, turn state, reported ids). A same-generation
  `add` (a refresh, section 5.2 step 11) wakes it with that state and a **fresh** `_SessionWait` from its cursor (the old
  wait's `stopped_since` / `confirming` would let a turn that ran entirely inside the dormant window conclude `ended`
  without a flush window). The cleanup loop's `remove` deletes it together with the row (section 9.3).
- **Emission.** A conclusion to emit is handed to the backend loop with `asyncio.run_coroutine_threadsafe`. Delivery runs
  there (section 8). The tick never waits for a delivery.
- **State writes.** The thread never writes to the database. Every write of monitor state (the turn state, a cursor
  move with its `cursor_at`) is posted to one writer task on the loop, which applies them in order through
  `storage.write` (section 9.1). The writer's queue is an `asyncio.Queue`, which is not thread-safe: the thread posts
  with `loop.call_soon_threadsafe(queue.put_nowait, item)`, which keeps the posting order. The writer catches and logs
  a failed item (a locked SQLite) and goes on with the next one; a barrier item is always resolved; the supervisor also
  restarts the writer task if it died; a restarted writer consumes the **same** `asyncio.Queue`, so queued items and
  their order are kept; the item being applied when the writer died is lost, like a failed write (section 14). A lost rebase CAS heals at the next **load** (restart or supervisor rebuild), not before: the
  running monitor already holds the new numbering, so it sees no mismatch, and until that load its cursor writes do
  not move the persisted `cursor_line` (tag mismatch); at the load, the row's `numbering` mismatches the epoch, and the
  rebase applies again at the current end. A lost turn-state or cursor write stays stale until the next write of that
  field (section 14).
- **Shutdown** (lifespan exit): the supervisor stops; a stop sentinel is posted to the command queue (a thread-safe
  `queue.Queue`); the thread finishes its current tick and exits; the loop waits for it with
  `await asyncio.to_thread(thread.join, 5)`, so the loop is never blocked. Emissions produced during that last tick are
  dropped: once the stop is requested, the thread posts no emission to the loop, **and no state change tied to a dropped
  emission** (other state writes are still posted). In-flight delivery coroutines are cancelled; a cancelled delivery
  does not persist its cursor. So a `replied` or `provider_error` is re-detected at the next start (same `eventId`),
  and so is an `ended` whose emission was dropped: its turn is still persisted as open, its agent died with the backend,
  and the next start concludes `ended` again. An `ended` whose delivery was already running when the stop came has
  persisted `turn_open = false` and is not re-detected (section 14). The writer task then drains its
  queue for at most 2 s (turn-state writes matter for the next start, section 9.2), and is cancelled after that; a
  writer found dead at that point is restarted on the same queue first, so the drain still runs.

The `_SessionWait` constructor is used as is: `started` = the wait's start, `twicc_pid` = the live PID,
`want_text=True`, `wait_background` from the arguments.

### 7.2 Cursor

Two values are fixed at creation (a rebase, section 7.6, resets them): `L0`, the session's `last_line` read at the request's arrival (section 5.2, step 6;
the row's `last_line` when any `Session` row exists, else `0`), and the starting cursor. The initial turn state (section 7.3) is computed at
insert time (step 11) from that starting cursor.

- **New subscription with `since_line_num`:** the starting cursor is `since_line_num`. This is exactly
  `session <ID> wait-reply --from <since_line_num>`: the first conclusion past that line, even if it already happened.
- **New subscription without `since_line_num`:** the starting cursor is `L0` (`0` for a session with no row yet, as
  `create-session --wait-reply` does). This deliberately differs from the `session wait-reply` default (after the last
  user message, `default_wait_cursors`, `:177-212`): a subscription without a cursor means "from now".
- Both values are persisted at creation (`cursor_line`, `initial_last_line`, section 9.1).
- **`first`** is true while `cursor < L0`: the monitor is still looking at history, before the creation.
- **Existing subscription (refresh or repeated subscribe):** the cursor never moves, whatever `since_line_num` says.
- **After an emitted conclusion that moves the cursor:**
  - `replied`, `provider_error`: the new cursor is `reply.line_num`.
  - `ended`: the new cursor is the wait's `scanned_up_to` (always ≥ `reply.line_num`), so the next wait does not scan
    again the non-final lines of the turn that ended.
  - If `first` was true, the new cursor is `max(new cursor, L0)`, and `first` becomes false. History between the two is
    not replayed: a subscription delivers at most **one** conclusion from before its creation, then only conclusions
    that happen after it.
  - `cursor_at` (section 7.3) becomes `max(cursor_at, the conclusion's time)` (section 7.3, "Times"), in memory as in
    the database (section 9.1): it never moves back.
  - The monitor's `_SessionWait` is replaced by a new one from the new cursor.
- `awaiting_user_input` never moves the cursor (the turn is still open), and never ends `first`.
- **Dropped conclusions** (sections 7.3 and 7.4) do not end the wait: the monitor keeps calling `step()` on the **same**
  `_SessionWait`. That is safe, because `step()` has no "done" state:
  - After `ended`, it keeps `stopped_since` and `confirming`, so it returns `ended` again at each tick while the agent
    stays stopped and the watcher is not behind (`:561-588`).
  - When the agent works again, the working branch resets both and returns `None` (`:543-559`). A new turn therefore
    never inherits a stale `ended` timer.
  - A new final message past the scan is still returned as `replied` (`:478-510`).
  - What `step()` does not reset is the message an `ended` carries (`last_message`, and `wait_background`'s
    `last_ignored`). The silent cursor move on a new turn (section 7.3) handles both.

### 7.3 Rule A — `ended` only closes an open turn

`step()` returns `ended` for any idle session with nothing new past the cursor (`src/twicc/cli/_wait_reply.py:588`). In a
one-shot wait that is correct. In a recurring one it would follow every answer. So `ended` is emitted only when a turn is
**open**: a turn that started after the last emitted conclusion, or that the subscriber is known to wait for.

**Turn state of a monitor** (all persisted, section 9.1):

- `turn_open` (bool);
- `turn_started_at`: when the latest turn transition seen started; none before any turn. It moves on every new turn,
  even while a turn is already open (the latest turn is the one a late conclusion must not close);
- `turn_opened_by`: `initial` (agent working at creation), `history` (a prompt past the starting cursor at creation),
  `transition` or `awaiting` — how the open turn was opened (section 7.3, step 4 for `awaiting`);
- `turn_start_line`: the line after which a prompt counts for the guard. It is set when a turn opens, renewed by an
  "earlier" conclusion (step 4), and moved past every existing line when an `ended` is emitted or guard-dropped (step 4),
  so a crashed turn's own prompt can never make a later pseudo-turn pass the guard;
- `cursor_at`: the time of the conclusion that set the cursor (section 7.2).

**Times.** All comparisons use float epoch seconds. `state_changed_at` already is one (`time.time()`), and
`cursor_at` / `turn_started_at` are stored as floats (section 9.1); a `SessionItem` timestamp (an aware UTC datetime,
`providers/compute_base.py:409-424`) and a `PendingRequest.created_at` (already a float) are used as epoch seconds
(`.timestamp()` for the datetime). A conclusion's time: for `replied` and `provider_error`, the `SessionItem.timestamp`
of its line when it has one, else the tick's start time (read before step 1 of the tick order); for `ended`, always the
tick's start time — the line an `ended` carries can be much older than the stop (an ignored final message, a message
written before a question), and an `ended` that was sent is never re-detected (one dropped at shutdown is, but it
was never sent), so stability brings nothing.

**New-turn detection.** The monitor reads `get_agent_info(session_id)` at every tick (in memory; `None`, for a dead or
unknown agent, is "not working"). `AgentInfo` carries `state`, `previous_state` and `state_changed_at`
(`src/twicc/agent/states.py:104-111`), set together by `_set_state` with `time.time()`
(`src/twicc/agent/base_agent.py:183-188`). A **new turn** is observed when all of these hold:

- `state` is `starting` or `assistant_turn`;
- `previous_state != state` (a re-set of the same state is not a new turn; Codex re-sets `assistant_turn` on a hold,
  `providers/codex/agent/agent.py:1510`);
- `state_changed_at > cursor_at` (the transition happened after the conclusion that set the cursor);
- `state_changed_at > turn_started_at` when set (not a transition already seen).

On a new turn:

1. Turn state, in three cases:
   - **No turn open**: `turn_open = true`, `turn_opened_by = transition`, `turn_start_line` =
     `max(the current cursor taken before the silent move of step 2, the stored turn_start_line, last_ignored.line_num
     when the current wait carries one)`. The first term keeps the new turn's prompt line past it even if one of its
     assistant lines was already scanned; the second keeps a previous crashed turn's prompt before it (step 4); the third
     keeps the prompt of a turn whose final was ignored under `wait_background` before it.
   - **A turn is open and the current wait carries a `last_ignored` message** (`wait_background`, section 7.5): the
     ignored work is over, so that turn is closed first, then a fresh one is opened: `turn_opened_by = transition`,
     `turn_start_line` = the cursor **after** the silent move of step 2 (the old turn's prompt must not let the new
     turn's guard pass).
   - **Any other open turn** (an interrupted turn still inside its flush window, a pseudo-turn arriving during a turn):
     `turn_opened_by` and `turn_start_line` keep their values.
   In every case `turn_started_at = state_changed_at`.
2. **Silent cursor move**: if the current wait scanned past the cursor (`wait.scanned_up_to > cursor`), the cursor moves
   to `scanned_up_to` without any event. In every case the wait is replaced by a new one from the (possibly moved)
   cursor, so a new turn never keeps the old wait's `stopped_since` / `confirming` (an agent that stops between step 1
   and `step()` still gets its full flush window). `cursor_at` does not change. The silent move is safe: `step()` returns a final message or a provider error the moment it scans one, so every line already
   scanned past the cursor is either a non-final message or a final message ignored under `wait_background`; neither
   is owed to the subscriber. It prevents a later `ended` from carrying a previous turn's message (`step()` keeps
   `last_message` and `last_ignored` across its working branch, `:511-512`, `:557-559`, `:588`).

A process start gives two transitions (`starting`, then `assistant_turn`); both may count. That is harmless: the turn
is already open at the second one, so only `turn_started_at` moves and a fresh wait replaces one that has scanned
nothing yet.

Why timestamps and not "an idle period was seen": a transition is recorded on the agent even if no tick saw it. An idle
gap shorter than the 0.25 s tick (a cron that fires right after a turn) and a watcher that indexes the previous turn's
final message late (the lag "has no useful upper bound", `:352-355`) both leave `state_changed_at` after the previous
conclusion's time, so the new turn is still detected.

**Tick order** (fixed):

1. Read `get_agent_info(session_id)` and apply new-turn detection.
2. Call `step()` on the current `_SessionWait`.
3. Apply the rules to its result:
   - `replied` / `provider_error` → emit;
   - `awaiting_user_input` → Rule B (section 7.4);
   - `ended` → if `turn_open` is false: drop (this check comes first: it needs no query, and an idle monitor meets it
     at every tick).
   - `ended` while the session's compute is not current (`session_compute_ready()` false, `core/serializers.py:14-17`,
     read with the one session read of section 7.6,
     `values_list("history_epoch", "provider", "compute_version", "last_line", "title")`, because the wait loads its
     row with `.only(...)`, `_wait_reply.py:469-474`; a session with no row counts as ready). That single read, done
     once in the emission phase as its last read (section 7.1), supplies readiness, the epoch check, the `last_line`
     used both for `turn_start_line` (step 4) and for the `ended`-without-line `eventId` key (section 8.4), and the
     `session_title` of the payload.
     **Readiness for an `ended` needs two reads.** That read runs after `step()`'s scan, and a compute commits the
     item kinds no later than `compute_version` (in the same transaction, `compute_base.py:3509-3560`, `:3717-3744`, or
     in earlier chunk transactions for large results, `providers/db_writer.py:1990-2025`): a commit between
     the scan and the read would show "ready" for a scan that saw the final message unclassified. So the session counts
     as ready for an `ended` only if this read **and** the monitor's previous session read both say ready **with the
     same `history_epoch` and the same `last_line`**. Why this is exact: every raw insert moves `last_line` and resets
     `compute_version` in one transaction (`providers/db_writer.py:2436-2459`), a ready read sees only classified lines,
     and the transcript is append-only within an epoch; so two ready reads with equal `last_line` around a scan prove the
     scan saw only classified lines. The **previous read** is the latest session read the monitor took before this
     tick's `step()` call, whatever its source (creation, wake, backstop, `rebase` command, pending-rebase tick, an
     emission phase, a dropped tick included); a same-tick read taken before `step()` counts. It is not reset when the
     wait is replaced or the turn changes. A missing previous read counts as not ready. A read that finds **no
     `Session` row** compares as (ready, epoch `0`, `last_line` `0`), matching the epoch-0 rule of section 7.6 and the
     `0` key of section 8.4, so the `ended` of `step()`'s no-row branch (`_wait_reply.py:589-598`) is emitted. Cost: one more tick when lines
     were added since the previous read; the next `step()` rescans with classified kinds and returns the `replied` if
     there is one. It cannot block an `ended` forever: every `ended` tick with an open turn takes the read, so the next
     tick has a previous read, unless `last_line` changes at every tick while the agent is stopped.
     Not ready: drop, keep the same wait, change nothing else. At boot, the initial sync inserts the new lines raw, without
     `kind`, and moves `last_offset` to the end of the file (`providers/claude_code/initial_sync.py:350-368`,
     `providers/db_writer.py:2440-2456`); until the background compute fills `kind`, `step()` cannot see a final message
     (`kind__in` filter, `_wait_reply.py:478-481`) and `_watcher_is_behind` is false, so an `ended` would be emitted
     for a turn whose answer is already on disk (typically a hybrid CLI turn completed while the backend was down).
     The CLI guards the same hazard (`default_wait_cursors`, `:177-212`). Once the compute is current, the kept wait
     returns the `replied`, or the `ended` again.
   - `ended` otherwise: if the turn was opened by `transition`, apply the **guard**: emit only if
     - the block has a `line_num`, or
     - the agent is dead (`get_agent_info()` returned `None` at step 1 of this tick): a process that died is a real
       failure the subscriber must hear about (a resume or startup failure dies before the CLI writes the prompt line),
       while every pseudo-turn below ends with the agent alive in `user_turn`; or
     - a **prompt** exists past `turn_start_line`: a `USER_MESSAGE` item that is not a **command line** (one indexed
       query on `idx_session_kind_line`, run on this path only).
     A guard drop sets `turn_open = false` (the agent stopped; that turn is over) and moves `turn_start_line` as an
     emitted `ended` does (step 4). Else emit.

     A **command line** is decided by one provider-aware predicate, factored from the code that already makes the
     distinction, not copied:
     - Claude Code: the slash-command echo test of the watcher (sniff `<command-name>`, parse, `extract_command`;
       `providers/claude_code/sessions_watcher.py:661-675`, `providers/claude_code/compute.py:245`). Only `/clear`,
       `/model`, `/effort` and `/fast` echoes are classified `SYSTEM` (`compute.py:93-98`); every other command echo,
       `/rename` included, is a `USER_MESSAGE`.
     - Codex: a TwiCC-injected command (`/compact`, `/goal clear`, bare `/plan`) is rewritten into a real user message
       (`providers/codex/compute.py:1534`, `_INJECTED_COMMANDS`; `:1577-1599`, `_injected_command_text`; injected by
       `providers/codex/agent/agent.py:1704-1712`, `:1861-1869`, `:2117-2122`). The rewrite keeps the original under
       `twiccOriginalContent`. A Codex `USER_MESSAGE` is a command line when
       `_injected_command_text(_restore_private_source(parsed))` returns a command — the test the Codex history facts
       already use (`providers/codex/history_facts.py:144`; `_restore_private_source`, `codex/compute.py:1114`). It
       proves the line was injected, not typed.
4. After an emitted conclusion with conclusion time `T`:
   - `replied` or `provider_error` on a turn opened by `history`: `turn_open = false`, whatever `T`. A `history` turn
     is opened from a prompt that already existed at creation, and its first conclusion ends it: history past one
     conclusion is not replayed (section 7.2), so an "earlier" rule must not keep it open.
   - `replied` or `provider_error` otherwise: if `turn_open` and `T < turn_started_at`, the conclusion belongs to an
     **earlier** turn (indexed late, or re-detected after a restart): the turn stays open, and its guard reference is
     renewed — `turn_start_line` = `max(turn_start_line, reply.line_num)` (the conclusion's own line, taken before any `L0` jump of section
     7.2), and `turn_opened_by = transition` unless it is `awaiting`. The earlier turn's own prompt is then before
     `turn_start_line`, so it can no longer make the guard pass; a later turn's prompt is after it (the transcript is
     append-only). Else `turn_open = false`.
   - `ended`: always `turn_open = false`. `step()` concludes `ended` only while the agent is stopped
     (`_wait_reply.py:543-588`), so the open turn is over, whatever the time of the message it carries. Also
     `turn_start_line` = `Session.last_line`, read **after** `step()` returned this conclusion (a read before it could
     miss late-committed lines of the crashed turn); when the session has no row (the `step()` branch at `:589-598`),
     `turn_start_line` keeps its value. Why: `scanned_up_to` moves only on assistant and API-error
     lines (`:478-485`), so a turn interrupted before any assistant line leaves its prompt past the cursor; without this
     move, the next pseudo-turn (an automatic `/rename`, a Codex `/compact`) would find that prompt and pass the guard.
     A real later turn's prompt is past `last_line`.
   In every case the cursor and `cursor_at` move (section 7.2).
   After an emitted `awaiting_user_input`: `turn_opened_by = awaiting`, always, so the guard never drops the `ended` of
   a turn the subscriber already knows is blocked. If no turn was open: `turn_open = true`, `turn_started_at` = the
   request's `created_at`, `turn_start_line` = the cursor. (The turn is open and blocked; if it is then stopped or the
   agent dies, its `ended` is delivered.)

Why the `ended` guard for transition-opened turns: some transitions are not conversation turns. A hybrid local slash
command outside a turn (echo → `assistant_turn`, ack → `user_turn`, `providers/claude_code/agent/hybrid/agent.py:596-627`,
including the automatic `/rename`, `:1057-1063`, which also runs when a client renames the session), the `starting`
window of a hybrid adoption at boot (`hybrid/agent.py:200`), and Codex injected commands (`/compact`,
`providers/codex/agent/agent.py:1697`, `:1761`; `/goal clear` or bare `/plan` on a cold-woken session, which goes
through `starting`, `:730-741`) all open a turn with no prompt and no assistant line, and end with the agent alive;
their only `USER_MESSAGE`, if any, is a command line. Without the guard each would end in an `ended` with nothing in it.
Most real turns that crash wrote an assistant line (then `line_num` is set), were started by a prompt line, or killed
their agent; the exceptions are listed in section 14. The same guard covers a hybrid turn whose transition is stamped
when the watcher processes its user line (`on_jsonl_user_message`, `hybrid/agent.py:587-594`),
possibly after that turn's own final message (`providers/claude_code/agent/manager.py:557-568` applies the signals after the insert): its `replied` is
then "earlier" than `turn_started_at`, the turn stays open with `turn_start_line` renewed to the final message's line
(step 4), and its `ended` is dropped by the guard (no prompt follows the final message), which closes the turn.

Effects:

- A `replied` emitted while the agent still works (a Monitor, a live subagent or a wake-up keeps the turn running,
  `:8-12`) is followed by no transition: when that turn later goes idle without a new final message, the turn is closed
  and its `ended` is dropped. A new final message from it is still `replied` (transcript-based, never filtered).
- A turn whose final message is indexed after the next turn started: that late `replied` does not close the next turn
  (its time is before `turn_started_at`). The next turn's `ended` is kept, whether it is still running or already over,
  and across a restart (the turn state is persisted).
- `replied` and `provider_error` are never filtered by Rule A.

**Initial values** (at creation):

- `cursor_at` = the creation time.
- `turn_start_line` = the starting cursor.
- If the agent is working at creation: `turn_open = true`, `turn_started_at` = its `state_changed_at`,
  `turn_opened_by = initial`. The subscriber wants the conclusion of the turn in progress.
- Else, if at least one prompt (a `USER_MESSAGE` that is not a command line) exists past the starting cursor:
  `turn_open = true`, `turn_started_at` = the timestamp of the **first** such message (`0.0` if it has none, so no
  conclusion can count as "earlier" than the turn), `turn_opened_by = history`. A turn started after the cursor, so its
  `ended` is the true answer, as `--from` reports it. Other lines (a custom title, a system line) do not count. This
  holds with or without `since_line_num`: without it, the starting cursor is `L0`, read at the request's arrival, so a
  prompt past it is a turn that started during the verification (step 10) and may already have died by the insert.
- Else `turn_open = false`, `turn_started_at` = none.

Known effects, listed in section 14:

- Without `since_line_num`, a turn running at creation that already wrote its final answer before `L0` (a Monitor keeps
  the turn open): the answer is not reported, and the turn's later `ended` is.
- Turns that start with no state transition are not new turns for Rule A: a message queued while a turn runs (Claude
  Code keeps `assistant_turn`, `providers/claude_code/agent/agent.py:2051-2054`); a Codex user message during a subagent
  hold (`providers/codex/agent/agent.py:805-818`); Claude follow-up turns under a hold (a background agent ends, a
  Monitor event, a wake-up; `providers/claude_code/agent/agent.py:2436-2484`). Their `replied` is delivered; their
  `ended` (crash, interruption) is dropped once an earlier `replied` closed the turn.

### 7.4 Rule B — one event per pending request

While a request is pending, `step()` returns `awaiting_user_input` at every tick (`:537-541`). The monitor reads
`pending_requests` from the `AgentInfo` of step 1 (`None` or an empty tuple: drop):

- The requests whose `request_id` was never reported by this monitor are taken in their list order. The first one is
  emitted (its `request_id`, `request_type` and `created_at` feed the payload, the `eventId` and the timestamp) and its
  id is remembered. Any other unreported request is emitted at the next tick, one per tick.
- Otherwise the conclusion is dropped.
- The set of reported ids is **never cleared** when the cursor moves: request ids are unique (UUIDs, nonces, Codex item
  or approval ids), so a request still pending across a cursor move is not reported twice. The set is kept in memory and
  bounded to the last 256 ids. After a restart or a supervisor rebuild (section 7.1) it is empty, and a still-pending
  request is reported again with the same `eventId` (section 8.4).

Because `step()` scans the transcript before it checks pending requests (`:537`), an answer written before a block is
still reported as `replied` first, as in the CLI.

### 7.5 `wait_background`

The flag is passed to `_SessionWait` and keeps the CLI rule (`WAIT_BACKGROUND_HELP`, `:134-150`): a final message read
while `background_work_in_progress` is not null does not count; the next final message read while it is null concludes
`replied`; a dead agent concludes `ended` carrying the ignored message.

Once a final message was ignored, `step()` keeps the wait open for an alive idle agent and never returns `ended`
(`:543-546`). The CLI leaves that state only on `timeout`, which carries the ignored message, or on a resume "from its
`line_num`" to wait for **the next answer** (`WAIT_BACKGROUND_HELP`, `:146-148`). Events have no `timeout`, so the monitor
applies that resume:

- **On a new turn** (section 7.3): the silent cursor move goes to the wait's `scanned_up_to`, which is at or past
  `last_ignored.line_num`. The ignored message is not delivered; the new turn's conclusion is.
  - Claude Code reopens a turn when a background shell ends (`providers/claude_code/agent/agent.py:2495-2497`: a turn
    the CLI starts on its own), and a background shell does not hold `assistant_turn` (`:264-268`, `:2438`). So the
    usual flow is: interim final message
    ignored → agent idle while the shell runs → shell ends → reopened turn (new turn: resume past the ignored message) →
    its final message → one `replied`, carrying the real answer. This matches the CLI, where the ignored message stays
    behind the cursor.
- **Without a new turn** (Codex answers after a process ends only if the agent waited for it within its turn): **no event
  is emitted** while the agent stays alive and idle. The ignored message is never delivered by an event; it stays
  readable with the session content tools.
- **Agent death**: `step()` returns `ended` carrying the ignored message (`:588`), emitted because the turn is open.

The `inputSchema` description of `wait_background` states this behaviour, in the vocabulary of `WAIT_BACKGROUND_HELP`.

### 7.6 Codex history rebuild (renumbering)

Everything a monitor keeps is keyed on line numbers (cursor, `L0`, `turn_start_line`, the `last_line` key of an
`ended`). A Codex history rebuild throws that numbering away: `_begin_replace_codex_history` deletes every `SessionItem`
and resets `last_offset`, `last_line` and `compute_version` in one transaction (`providers/codex/rollout_migration.py:349-378`);
`_insert_replace_codex_history_chunk` re-inserts the items numbered from the canonical rollout (`:386-392`); and
`_finish_replace_codex_history` publishes the new `last_line` / `last_offset` (`:395-405`). It runs when the watcher
detects a rewrite (`providers/codex/sessions_watcher.py:167-213`) and in the boot pass (legacy migration, repair of a
replacement interrupted between begin and finish, `providers/codex/background_compute.py:340-347`). The codebase
already re-anchors line-keyed state across this rebuild (snapshot shares, `rollout_migration.py:291-337`).

**The numbering belongs to the session.** A new column `Session.history_epoch` (`PositiveIntegerField(default=0)`)
counts the renumberings of a session. `_begin_replace_codex_history` increments it in its own `UPDATE`, the statement
that already resets `last_offset`, `last_line` and `compute_version` (`rollout_migration.py:377-378`), so the epoch and
the reset commit together. Nothing else changes it.

Each subscription row stores `numbering`: the epoch its line values (cursor, `L0`, `turn_start_line`) refer to. It is
read at arrival together with `L0` (section 5.2, step 6). The monitor holds it too. A monitor whose `numbering` differs
from the session's `history_epoch` has a **pending rebase**. That comparison is exact for every interleaving, survives
every restart and rebuild (both values are persisted), and fires only for real renumberings.

**No row.** A session with no `Session` row yet (a live process the watcher has not indexed, the main flow of
`create_session` followed by an immediate subscribe) reads as **epoch 0** and **never** as mid-replacement: step 6
stores `numbering = 0`, and every comparison below treats a missing row as epoch 0, so such a monitor is never pending.
Only the *apply* of an already pending rebase with no row stays pending (a deletion guard).

**When the monitor reads the session's epoch** (one indexed primary-key read,
`values_list("history_epoch", "provider", "compute_version", "last_line", "title")`: one SQLite statement, one snapshot, so the
epoch and the readiness are always consistent):

- when it is created or woken (boot, supervisor rebuild, `add`, dormant wake);
- in the emission phase, as its **last read** (section 7.1, "Order of a tick that emits"), after `step()`, the guard
  query and the timestamp read; this is the same single read that gives readiness and `last_line`. A mismatch seen by
  this read enters the pending rebase **whatever the outcome** of the tick (emission, readiness drop, guard drop):
  no write of the emission phase is posted. Writes of that tick's step 1 (new-turn detection, section 7.3) may
  already be posted; they come before the rebase CAS in the writer's FIFO order, and the CAS supersedes them;
- every 5 s as a backstop (a cursor that a shorter numbering put past the end would otherwise never produce a
  conclusion that triggers the check);
- on a `rebase(session_id)` command, posted from a `transaction.on_commit` of the begin step (next to its existing
  hooks, `:368-369`), only to react without waiting for the backstop; a no-op when the runtime does not exist
  (`TWICC_NO_MCP`).

**Pending rebase.** The monitor does not tick and emits nothing. At every tick it runs the one read above, until that
read shows the compute current (`session_compute_ready`, section 7.3). Begin sets the epoch and resets
`compute_version` in one `UPDATE`, so a single snapshot can never pair a new epoch with "ready". Then it **applies** the
rebase:

- `cursor` = `turn_start_line` = `L0` = the `last_line` of that same read; `first = false`; a fresh `_SessionWait`;
  `numbering` = the epoch of that same read; `cursor_at` is kept; the reported request ids are kept (they do not depend
  on line numbers).
- The turn state is set from `get_agent_info()` read at apply. The agent is dead when a rebuild runs
  (`background_compute.py:265-267`, `:355`, `:370`), so the turn open at the rebuild is over (its conclusion fell in the
  undelivered window, section 14), and any working state at apply is a turn started after the rebuild (the apply can
  come later: a dormant monitor, the 5 s backstop; or a send queued during the rebuild, released with the migration
  gate, `codex/agent/manager.py:228`):
  - agent working: `turn_open = true`, `turn_opened_by = initial`, `turn_started_at` = its `state_changed_at`,
    `turn_start_line` = the new cursor (new-turn detection alone would miss that turn when its transition is earlier
    than `cursor_at`, for example on a subscription created during the rebuild);
  - otherwise: `turn_open = false`, `turn_started_at` = none, so the next transition counts as a new turn.
- persisted with one dedicated **non-monotonic**, compare-and-set write: `SET cursor_line = …, initial_last_line = …,
  turn_start_line = …, numbering = <new epoch>, turn_open = …, turn_opened_by = …, turn_started_at = … WHERE id = …
  AND created_at = … AND numbering = <old numbering>`, the one exception to the `MAX` rule of section 9.1. If a second rebuild began meanwhile, the session's
  epoch has moved again, the next epoch read sees it, and the monitor enters a new pending rebase; the CAS on the old
  numbering keeps the two writes ordered.
- A rebase with no `Session` row stays pending (a rebuild always has a row; this only guards a deletion).

**Old-numbering writes.** Every cursor write carries the `numbering` it was computed in, and the writer applies the
`cursor_line` part only `WHERE numbering = <tag>` (in SQL, so it holds across supervisor rebuilds and restarts); the
`cursor_at = MAX(…)` part, which does not depend on numbering, stays unconditional.

**Subscriptions created across a rebuild.** A row stores the epoch read at arrival with its `L0`.

- Arrival **before** a rebuild's begin: the session's epoch moves after that read, so the monitor starts with a pending
  rebase.
- Arrival **between** begin and finish: the epoch read is already the new one, but `L0` is the reset `0`
  (`rollout_migration.py:377-378`), which would replay the whole new history. That window has a durable marker:
  `last_offset == 0` with `history_epoch > 0` (begin's documented "unfinished replacement" marker, `:351-357`; a new
  session never has `history_epoch > 0`). When step 6 reads it, `numbering` is stored as `NULL`, meaning "numbering
  unknown": a pending rebase, applied like any other (the CAS matches `numbering IS NULL`).

In both cases the `since_line_num` is discarded and the monitor starts at the new end (section 14). No special check is
needed at step 11. A monitor whose `numbering` is `NULL` emits nothing; it only waits for its rebase.

**`eventId`.** The line-based keys of section 8.4 include `numbering`. The epoch never resets — not for a new
subscription generation, not after unsubscribe and subscribe — so a conclusion of one numbering never reuses the id of
a different conclusion of another.

Effect: no old conclusion is re-emitted under its new line number, no new conclusion is lost behind a cursor that the
new numbering puts past the end, and no `eventId` is reused. Conclusions that occur between the rewrite detection and
the applied rebase are not delivered (section 14).

### 7.7 What does not filter

- `hidden`, `mute_on_user_turn`, `archived`: no effect (R7). The CLI wait ignores them for an explicit id.
- `process_state` broadcasts and Apprise: not used. The runtime does not depend on the broadcast callback, which is
  registered only when a browser connects (`src/twicc/asgi.py:605`).
- A deleted or never-indexed session: `step()` already handles a missing row (`SESSION_ROW_GRACE_SECONDS`,
  `src/twicc/cli/_wait_reply.py:100`). Rule A drops the resulting `ended` unless a turn is open.

## 8. Delivery

### 8.1 Signing

`standardwebhooks.Webhook(secret).sign(msg_id, timestamp: datetime, data: str) -> "v1,<b64>"` (verified on 1.1.0).

- The body is serialized once with `orjson`; the same bytes are sent, and their UTF-8 decoding is the `data` argument.
- One integer `ts = int(time.time())` per attempt feeds both the `webhook-timestamp` header and the `timestamp` argument,
  as `datetime.fromtimestamp(ts, UTC)`. `sign()` replaces the datetime's `tzinfo` with UTC without converting, so a naive
  or non-UTC datetime would sign a different instant.

Headers: `Content-Type: application/json`, `webhook-id` (= `eventId`, or `msg_verification_<random>` for a challenge),
`webhook-timestamp`, `webhook-signature`, `X-MCP-Subscription-Id`.

Secret rotation: a refresh that brings a different secret stores the old one as `previous_secret` for 5 minutes. During
that window each request carries both signatures, space-separated, one `Webhook` instance per key.

Which secret signs: every attempt (the first and every retry) signs with the row's `secret`, plus `previous_secret`
while `previous_secret_until` is in the future, both read in the authority check that precedes the attempt
(section 8.7). A retry after a rotation therefore uses the new secret, never a snapshot taken at emission time.

### 8.2 Transport: one pinned HTTPS helper

The CIMD fetch already implements a pinned, no-redirect HTTPS request (`_fetch_metadata`,
`src/twicc/mcp/oauth/provider.py:94`): it resolves the host, requires every address to be `is_global`, connects to the
pinned IP with the original `Host` header and `sni_hostname`, and never follows redirects. That logic moves into one
shared helper used by both the CIMD fetch (GET) and the webhook sender (POST). No new SSRF library.

Only the address resolution, the pinning and the no-redirect connection are shared. The CIMD fetch keeps its current
profile and return contract: GET, status 200 only, 64 KiB cap, its 5 s client timeout inside a 10 s outer
`wait_for`, `_metadata_slots`, and any failure → `None`, with no classifier and no traceback log.

Webhook profile of the helper:

- `https` only, no redirects, `trust_env=False`.
- Every resolved address must be `is_global`; a refusal raises `NonGlobalAddressError`, an `OSError` subclass, so the
  classifier puts it in `connection_refused` without the "bug" traceback log of section 5.3. This is required by the
  OpenAI profile and the draft. It does **not**
  change the artifact broker's policy (`docs/plans/2026-06-18-artifact-network-broker-design.md`), which governs a
  different path.
- 10 s overall deadline per attempt (`asyncio.wait_for` around connect + send + read).
- Response body read capped at 4 KiB (enough for a challenge echo).
- Failures are classified with the categories of section 5.3; deliveries reuse them in logs.

### 8.3 Callback verification

Before a subscription is stored, unless `(connection_id, url)` was verified less than 24 h ago:

- POST `{"type": "verification", "challenge": <secrets.token_urlsafe(32)>}`, signed with the request's secret,
  `webhook-id` = `msg_verification_<random>`, `X-MCP-Subscription-Id` = the derived id.
- Success: a `2xx` status and a JSON object body (parsed with `orjson`; a body it refuses, such as one with a lone
  surrogate, is "not JSON" → `challenge_failed`) whose `challenge` passes, in this order: not a `str` →
  `challenge_failed`; else `hmac.compare_digest(received.encode(), sent.encode())` must be true, else
  `challenge_failed`. Comparing bytes avoids the `TypeError` that `compare_digest` raises on a non-ASCII `str`.
- **Concurrency.** At most 8 verifications run at the same time, through an `asyncio.Semaphore(8)` acquired with a
  bounded wait of 5 s (`asyncio.wait_for`). A subscribe that gets no slot within 5 s fails with `-32013` and
  `data: {"limit": "concurrent_verifications", "max": 8}`. A user who asks to monitor several sessions makes ChatGPT
  subscribe several times, possibly in parallel: up to 8 run at once, the others wait briefly instead of failing.
- **Per destination host** (the draft: "SHOULD be rate-limited per destination host"): at most 60 verifications per
  rolling minute for one callback hostname, in memory. Beyond that, `-32013` with
  `data: {"limit": "verifications_per_minute", "max": 60}`. Every ChatGPT callback is on one receiver hostname, so this
  is sized well above the 50-subscription limit of one connection.
- **Deduplication.** Concurrent verifications of the same `(connection_id, url)` share one challenge and its result.
  Only the first takes a concurrency slot and counts toward the per-host rate; the joiners take none.
- The verified cache is in memory. After a restart, the persisted subscriptions keep delivering; the next
  `events/subscribe` for a `(connection_id, url)`, a refresh included, re-verifies.

### 8.4 `eventId`

`"evt_" + sha256(subscription_id \x00 outcome \x00 key).hexdigest()[:32]`, with:

- `replied`, `provider_error`, and `ended` with a `line_num`: `key` = `f"{numbering}:{line_num}"` (`numbering` is the
  session's `history_epoch`, section 7.6: persisted, never reset, so the key stays deterministic across restarts and
  subscription generations and changes with every Codex history renumbering).
- `awaiting_user_input`: `key` = the reported `request_id`.
- `ended` without a `line_num`: `key` = `f"last_line:{numbering}:{Session.last_line}"`, `last_line` read at detection
  (`0` without a row).

For transcript conclusions and pending requests, the same conclusion detected again (after a restart, from the
persisted cursor; or after a subscription is re-created with the same `since_line_num`) carries the same id, and the
receiver can deduplicate. For an `ended` without a line, the cursor does not move past anything (`scanned_up_to` only
moves on assistant messages and API errors, `:478-485`), so a key based on the cursor would repeat for two different
turns. `Session.last_line` moves with a turn as soon as that turn writes any line (a user message, a system line, a tool
call), so two such turns never share the key. Only two consecutive turns that each write nothing at all would share it;
the receiver would then deduplicate the second `ended` (section 14). An `ended` that was sent is not re-detected after a restart (one dropped at shutdown is, but it was never sent, section 7.1), so cross-restart determinism brings nothing here. The id is kept across retries.

### 8.5 Size fit

The complete body must be at most 262 144 bytes (OpenAI, "Handle delivery responses"). The event is serialized whole. If
it is too large and `reply.text` exists, the largest prefix of `text` (whole code points) that fits is found by binary
search on the serialized size, and `reply.text_truncated: true` is added. No other field is cut. If even an empty `text`
does not fit (not reachable with the fields above), the event is dropped and logged.

### 8.6 Retries (in memory only)

- Attempts: immediately, then after 30 s, then after 120 s (3 attempts at most).
- Retried: the classifier categories `connection_refused` and `timeout` (section 5.3), and the statuses `408`, `425`,
  `429`, `5xx`. These are the transient failures OpenAI asks to retry ("Retry transient failures with exponential
  backoff and bounded attempts").
- Not retried: `tls_error` (a certificate or handshake failure does not fix itself in two minutes), and every other
  status. This includes `410` and `413` (OpenAI and the draft forbid retrying them) and the
  other `4xx`, which a retry cannot fix. A `410` does not delete the subscription (the draft: "without affecting the
  subscription itself").
- Each attempt has a fresh timestamp and signature and the same `eventId`.
- A restart loses pending retries. This is the accepted cost of R6.

### 8.7 Authority check

Before each attempt (the first one and every retry):

- **Subscription gone or expired** (no row with this `(id, created_at)` generation — deleted by `events/unsubscribe` or
  cleanup, or re-created since — or `refresh_before` passed): the attempt is not sent. OpenAI: "Stop sending events for the matching subscription".

- **Revoked connection** (`revoked_at` set), or a configured external MCP whose `resource_url()` differs from the
  connection's `resource`: the attempt is not sent, and the subscription is deleted with its monitor.
- **External MCP not configured right now** (`base_url()` empty: setting off, no password, or routing settings
  unavailable, `src/twicc/mcp/oauth/config.py:13-28`): the attempt is not sent, and the subscription is **kept**. The
  condition can be transient; expiry removes the row if it lasts.

The access token's 15-minute expiry is not checked: a subscription outlives access tokens by design, and ChatGPT
refreshes it with fresh tokens.

## 9. Persistence and lifecycle

### 9.1 Model

`McpEventSubscription` in `src/twicc/core/models.py`, next to the other `Mcp*` models:

| Field | Type | Notes |
|---|---|---|
| `id` | `CharField(primary_key, max_length=40)` | `sub_…` (section 5.2) |
| `connection` | `ForeignKey(McpConnection, on_delete=CASCADE)` | the principal |
| `name` | `CharField(max_length=64)` | `session.concluded` |
| `arguments` | `JSONField` | the last arguments received (including `since_line_num`) |
| `session_id` | `CharField(max_length=255, db_index=True)` | copied from `arguments`; no FK (the row may not exist yet) |
| `callback_url` | `URLField(max_length=2048)` | |
| `secret` | `CharField(max_length=128)` | stored in clear (section 10) |
| `previous_secret` | `CharField(max_length=128, blank=True)` | rotation window |
| `previous_secret_until` | `DateTimeField(null=True)` | |
| `cursor_line` | `PositiveIntegerField` | section 7.2 |
| `cursor_at` | `FloatField` | epoch seconds of the conclusion that set the cursor; creation time at first (section 7.3) |
| `initial_last_line` | `PositiveIntegerField` | `L0`, section 7.2 |
| `turn_open` | `BooleanField` | section 7.3 |
| `turn_started_at` | `FloatField(null=True)` | epoch seconds, section 7.3. A float, not a `DateTimeField`: `state_changed_at` is a `time.time()` float, and a datetime rounds it to the microsecond, so the same transition would compare as newer after a reload. |
| `turn_opened_by` | `CharField(max_length=16, blank=True)` | `initial`, `history`, `transition` or `awaiting`; empty before any turn |
| `turn_start_line` | `PositiveIntegerField` | section 7.3; the starting cursor at creation |
| `numbering` | `PositiveIntegerField(null=True)` | the session's `history_epoch` the line values refer to, read at arrival (section 7.6); `NULL` = unknown (created mid-rebuild), a pending rebase; part of the `eventId` key (section 8.4) |
| `data_dir` | `CharField(max_length=4096)` | the resolved data dir (`get_data_dir().resolve()`) of the instance that created the row (below) |
| `refresh_before` | `DateTimeField(db_index=True)` | |
| `created_at`, `updated_at` | `DateTimeField` | |

One migration. Every write goes through `twicc.mcp.oauth.storage.write` (`src/twicc/mcp/oauth/storage.py:23`, the backend
DB write lock). That helper is `async`: the worker thread never writes; it hands writes to the loop with the emission.

**Creation.** The row is written with `cursor_line` = the starting cursor, `initial_last_line` = `L0` (section 7.2),
`cursor_at` = the creation time and the initial turn state (section 7.3). The second classification, the authoritative
limit check, the deletion of an expired or foreign row and the insert run in the same `storage.write` critical section
(section 5.2, step 11), so two concurrent subscribes can neither both pass the limit nor both insert.

**Generation.** The same identity always gives the same `id`, so an unsubscribe followed by a subscribe re-creates a row
with the same primary key. The row's `created_at` is its **generation**. Every monitor, every delivery coroutine and
every state write carries the generation it was created for, and every state write and authority check (section 8.7)
matches on `(id, created_at)`. A late write or a retry of the old generation therefore never touches the new row.

**State writes** go through the single writer task (section 7.1):

- Every change of the turn state (`turn_open`, `turn_started_at`, `turn_opened_by`, `turn_start_line`): written at once, in the order the
  thread produced the changes. The queue order guarantees that a later change is never overwritten by an earlier one.
- A **silent cursor move** (section 7.3): `cursor_line` written at once.
- An **emitted conclusion that moves the cursor**: the delivery runs first (attempts and retries); then `cursor_line`
  and `cursor_at` are written, whatever the delivery outcome.
- A **rebase apply**: the non-monotonic compare-and-set write of section 7.6.

Every cursor write except the rebase CAS is monotonic, applies its `cursor_line` part only in the numbering it was
computed in, and writes `cursor_at` with the same "only forward" rule independently:
`UPDATE … SET cursor_line = CASE WHEN numbering = <tag> THEN MAX(cursor_line, new_line) ELSE cursor_line END,
cursor_at = MAX(cursor_at, new_at) WHERE id = … AND created_at = …` (SQLite's scalar `MAX`). A slow retry of an older
event can never move either value back, a write of an old numbering never touches the cursor of the new one, and an
older conclusion's `cursor_at` still lands when a silent move already pushed `cursor_line` further.

A restart while a delivery is in flight re-detects that conclusion from the persisted cursor and sends it again with the
same `eventId` (deduplicable), **if** the persisted cursor is still before it. It may not be: a silent move or a later
conclusion's write can have moved the persisted cursor past a conclusion whose delivery was still retrying. Such a
conclusion is lost by the restart (section 14). A supervisor rebuild can likewise re-detect a conclusion whose delivery
is still in flight; both carry the same `eventId`.

### 9.2 Boot

**Instance binding.** A row is owned by the data dir that created it (`data_dir`). The runtime and the cleanup consider
only rows whose `data_dir` equals the current resolved data dir. Why: devctl copies the main database and
`settings.json` into a new worktree (`devctl.py:449`, `:388`), so a worktree with the same external MCP settings would
otherwise load the main instance's subscriptions and POST to their callbacks. Its agents are not the main instance's,
so it would see every session as "not working" and could emit a false `ended`. Foreign rows are never delivered from,
never deleted by this instance's cleanup, never counted in its limits, and a subscribe that hits a foreign row's id
replaces it (section 5.2, steps 8 and 11). A worktree can still test events with its own subscriptions.

`EventsRuntime` (and a restarted worker thread) loads every row of this data dir with `refresh_before` in the future and a non-revoked
connection, and starts one monitor per row from `cursor_line`, with `L0 = initial_last_line` (so `first` is
`cursor_line < initial_last_line`, section 7.2), and the persisted `cursor_at`, turn state and `numbering` (compared
with the session's epoch at load, section 7.6).

- A turn that was open when the backend stopped stays open. If its agent died with the backend, `step()` concludes
  `ended` once the flush window passes, and it is emitted (subject to the guard of section 7.3, step 3).
- A conclusion of an earlier turn re-detected after the restart (its cursor was not yet persisted) does not close that
  open turn: its time is before `turn_started_at` (section 7.3, step 4).
- A restart before the first past conclusion was persisted still delivers at most one past conclusion.
- A transcript conclusion that happened during the downtime past the cursor is detected and sent, with the same
  deterministic `eventId` it would have had.
- Exception: a Claude session with crons gets a TwiCC-internal prompt at boot (`providers/claude_code/cron_restart.py`,
  `send_to_session`). Its answer is delivered as `replied` (R2: `replied` is never filtered), and the killed turn's
  `ended` is not: the agent works again, so `step()` never concludes `ended` for it (section 14).

### 9.3 Expiry and cleanup

When `refresh_before` passes, the monitor becomes dormant (section 7.1); the cleanup loop's `remove` stops it. The existing 60-second loop of `start_mcp_task`
(`src/twicc/mcp/endpoint.py:166`) also deletes, among this data dir's rows only, those expired for more than 60 s
(`refresh_before < now − 60 s`: a margin above the 5 s slot wait plus the 10 s verification, so a refresh that arrived in
time and is still verifying finds its row and wakes its dormant monitor instead of re-creating the subscription) and
those of revoked connections (same place as `cleanup()`
in `src/twicc/mcp/oauth/storage.py`), and posts the matching remove commands to the runtime.

### 9.4 Revocation

A revoked connection (owner action in `owner_views.py`, token revocation in `provider.py`, refresh-token reuse in
`storage.exchange`, or `revoke_all`) stops delivery at the next attempt (section 8.7), and the cleanup loop deletes its
subscriptions.

## 10. Security

- **Principal**: the `McpConnection` of the OAuth access token. It is part of the subscription id, so a connection can
  neither see, refresh, nor unsubscribe another connection's subscription.
- **Secret storage**: in clear in SQLite. It is a signing key TwiCC must use, not a bearer credential TwiCC accepts, so
  it cannot be hashed like `McpOAuthCredential`. The DB file is already the trust boundary for session transcripts.
- **Egress**: payloads go to the callback URL the authenticated client supplied, which must be a public `https` address
  that passed the signed challenge. With ChatGPT, that is OpenAI's receiver. The text is the same text
  `session_wait_reply` already returns to the same client over the same OAuth grant.
- **SSRF**: public addresses only, pinned connection, no redirects, for both verification and delivery (section 8.2).
- **Copied data dirs**: a database copied into another instance **at another path** (a devctl worktree) never sends the
  original instance's events (section 9.2, instance binding). A copy restored at the **same** path (another host, a
  cloned container) is not detected (section 14).
- **Amplification**: mandatory challenge before activation, limits per connection and in total, verification
  concurrency cap.
- **Untrusted text**: agent text is data. The payload carries no instruction. ChatGPT's own handling applies.
- **Autonomy**: an event-triggered ChatGPT task acts with the connection's `twicc:full` scope. This is unchanged from
  what the same connection can already do; this spec adds no new capability to act.
- **Feedback loop** (OpenAI: "verify that the resulting events do not create a feedback loop"): a task told "on each
  conclusion, send the next message to that session" produces event → send → conclusion → event, without end. This is
  an **accepted risk**, not filtered: TwiCC cannot tell such a loop from a legitimate orchestration the user asked for;
  every iteration is a ChatGPT task run the user instructed; the user stops it by stopping the monitoring in ChatGPT or
  by revoking the connection. The E2E test checks that both stop it (section 12).

## 11. Code changes

| Area | Change |
|---|---|
| `src/twicc/cli/_wait_reply.py` | None. `_SessionWait` is reused as is. |
| `src/twicc/cli/session.py` | Factor the session-existence lookup of `wait_reply` (`:618-626`, with `_live_session_ids`) into a function the events code also calls, with an option for the events-only `history_epoch > 0` clause (section 5.2, step 6); `session wait-reply` calls it without the option. |
| `src/twicc/providers/claude_code/compute.py` + `sessions_watcher.py` | Factor the slash-command echo test of the watcher (`sessions_watcher.py:661-675`: sniff `<command-name>`, parse, `extract_command`) into one function in `compute.py`, next to `extract_command`; the watcher and the events guard (section 7.3) both call it. |
| `src/twicc/providers/codex/compute.py` | Expose the injected-command test on a stored `USER_MESSAGE` (`_injected_command_text(_restore_private_source(parsed))`, as `history_facts.py:144` already does) as one function, for the events guard. |
| `src/twicc/providers/helpers.py` (provider helpers) | One provider-aware "is this `USER_MESSAGE` a command line" method, routing to the two functions above (the events guard calls it; no provider branching in the events code). |
| `src/twicc/mcp/oauth/storage.py` | The subscription cleanup (expired rows and rows of revoked connections, current `data_dir` only, section 9.3) is a function here, next to `cleanup()`, called from the same 60-second loop. |
| `src/twicc/mcp/pinned_https.py` (new) | The shared pinned HTTPS helper (section 8.2) and the failure classifier (section 5.3). Imported by `oauth/provider.py` and `events/delivery.py`. |
| `src/twicc/mcp/oauth/provider.py` | Move the pinned HTTPS logic of `_fetch_metadata` into `pinned_https.py`; `_fetch_metadata` calls it. |
| `src/twicc/providers/codex/rollout_migration.py` | In `_begin_replace_codex_history`'s existing `UPDATE`: also increment `history_epoch`; post the runtime's `rebase(session_id)` command from a `transaction.on_commit` (a wake-up only; a no-op without a runtime) (section 7.6). |
| `src/twicc/core/models.py` (`Session`) + migration | New column `history_epoch` (`PositiveIntegerField(default=0)`), incremented only by a Codex history rebuild (section 7.6). |
| `src/twicc/mcp/events/` (new package) | `catalog.py` (definition, schemas, wire constants), `methods.py` (params models, three handlers, capability middleware), `runtime.py` (thread, command queue, monitors, Rules A/B, cursor, supervisor), `delivery.py` (data builder from the `step()` block, size fit, signing, retries, verification). |
| `src/twicc/mcp/server.py` | Register the methods and the middleware on `_external_server`. |
| `src/twicc/mcp/endpoint.py` | Start and stop `EventsRuntime` in `mcp_lifespan()`; add subscription cleanup to the 60-second loop. |
| `src/twicc/core/models.py` + migration | `McpEventSubscription`. |
| `pyproject.toml` | `standardwebhooks` dependency (added by the user, section 13). |
| `CLAUDE.md` / `AGENTS.md` | One sentence in the MCP server paragraph: the external MCP exposes MCP Events (`session.concluded`, webhook only). |
| `README.md` ("External MCP access", `:160-164`) | One sentence: a connected client can subscribe to session conclusions; TwiCC then POSTs the session's answer text to the callback URL that client supplied. |

No CLI command, RPC route, MCP tool or skill changes.

## 12. Testing

**Unit tests (pytest).**

- Subscription id: deterministic, independent of key order, includes the connection, excludes `since_line_num`.
- `events/subscribe` validation: each error code and its `data`; params with `_meta` and unknown keys accepted; an
  unpadded secret accepted; a secret with characters outside the base64 alphabet refused; a missing or non-string
  secret refused; `since_line_num` above
  2147483647 refused with `-32602`; a bad `ttlMs` refused before any verification.
- Write-time classification: a refresh whose monitor became dormant during the verification wakes it with its
  in-memory state (no state taken from the row) and a fresh wait, and it delivers again; a refresh whose row was cleaned up during the verification creates a new row; two
  concurrent identical new subscribes give one row, one monitor and no error; at the limit, re-creating an expired row
  of the same id succeeds; foreign rows do not count toward the limits; a refresh while a `replied` is still retrying
  and its turn-state write is still queued changes only `refresh_before`, the secrets and the arguments of the running
  monitor (no duplicate, no `ended`); a subscription without `since_line_num` whose session replies during the
  verification delivers that `replied` (`L0` read at arrival); a subscription without `since_line_num` whose session
  starts a turn during the verification and dies before the insert delivers that turn's `ended`.
- Verification echo: a non-string or non-ASCII `challenge` gives `challenge_failed`, not an exception; a body with a
  lone surrogate is "not JSON".
- Classifier, **with real sockets** (a synthetic `TimeoutError` would not reproduce the chain): a TLS handshake that
  hangs → `timeout`, retried; a hang after the handshake → `timeout`; the outer `wait_for` deadline → `timeout`; TLS to
  a plain-HTTP port → `tls_error`, not retried; a server that reads the ClientHello then closes (`ssl.SSLEOFError`) →
  `connection_refused`, retried; a non-ASCII callback hostname → `-32602` at step 5.
- TTL grant rules; cursor never moved by a refresh.
- `server/discover` output on the external server contains both capability forms (pins the SDK middleware behaviour).
- `data` builder: equals the `session wait-reply` output shape for each outcome, minus `waited_seconds`, plus
  `session_title` / `request_type` / `text_truncated` as specified; `provider_error` with `text: ""`.
- Size fit: a large text gives a body ≤ 262 144 bytes with the longest prefix, and `text_truncated: true`.
- Signature: verified with `standardwebhooks.Webhook.verify`, including the two-key rotation window and a timestamp
  equal to the header.
- Rules A and B with a fake agent registry and fake transcript:
  - idle session: no `ended`, ever;
  - `replied` while the agent still works, then idle: no `ended`;
  - `awaiting_user_input`, then the turn is stopped: **exactly one** `ended` is emitted, even when the message it
    carries is older than the request's `created_at`;
  - a hybrid `/rename` (or another local command) outside a turn: no `ended`;
  - an `ended` always closes the turn: no second `ended` after the flush window;
  - two pending requests: two events, one per tick, then none;
  - an answer written before a block: `replied` first;
  - `since_line_num` with nothing past it (or only a title line): no `ended`; with a user message and a crashed turn
    past it: `ended`;
  - `since_line_num` with three past conclusions: exactly one past event, then only new ones;
  - a request pending across a cursor move, and the first-wait `L0` jump while a request is pending: never reported
    twice;
  - a dropped `ended`, then a new turn: no stale `ended`, and the new turn's crash gives one `ended`;
  - an idle gap shorter than a tick between two turns (transition never observed in the idle state): the second turn's
    crash gives `ended`;
  - watcher lag: the first turn's final is indexed after the second turn started; the second turn's crash gives
    `ended`;
  - a same-state re-set (`previous_state == state`) does not open a turn;
  - two turns that crash without assistant lines: two distinct `eventId`s.
- `wait_background`: an ignored final, then a final with no background: one `replied` carrying the second. Ignored
  interim final, agent idle while the shell runs, reopened turn, final: exactly one `replied`, carrying the reopened
  turn's answer, and the cursor passed the ignored line. An ignored final, the work ends with no answer: no event; then
  a new turn that crashes: `ended`. An ignored final, then the agent dies: `ended` carrying it.
  - a dropped `ended` with a non-final message, then a new turn that crashes with no assistant line: one `ended` with
    `line_num: null` and no text (silent cursor move);
  - watcher lag where the next turn already died before the late index: its `ended` is kept;
  - pseudo-turns opened by a transition with no prompt and no assistant line, agent alive at the end (hybrid local
    command, hybrid adoption, Codex `/compact`, Codex cold `/goal clear`, Codex cold bare `/plan`): no `ended`; a real
    transition-opened turn that crashes after its user message: `ended`;
  - a resumed process that dies before the CLI writes any line: `ended` (agent dead passes the guard);
  - an interrupted turn still inside its flush window when a pseudo-turn transition arrives: its `ended` is still
    emitted (`turn_opened_by` and `turn_start_line` kept while open);
  - a reopened turn that blocks on a question and is then stopped: `awaiting_user_input`, then `ended` (an emitted
    `awaiting_user_input` sets `turn_opened_by = awaiting`);
  - every new turn replaces the wait: an agent that stops right after the transition still gets the full flush
    window;
  - a hybrid turn whose transition is stamped after its own final message (user line and final in two watcher
    batches): one `replied`, no `ended` (the renewed `turn_start_line` puts the turn's own prompt before it);
  - a pseudo-turn transition observed before a late-detected final of the previous turn: one `replied`, no `ended`;
  - `wait_background`: a pseudo-turn during the ignored state, then the background work ends: no event (the old turn is
    closed before the fresh one opens);
  - `cursor_at` never moves back in memory (an `ended` carrying an old line; non-monotonic item timestamps).
- Persistence of the turn state: a turn open at shutdown whose agent died gives `ended` after the restart; a restart
  while an earlier turn's `replied` is still being delivered and a later turn is open: the re-detected `replied` keeps
  the later turn open, and its `ended` is emitted.
- Generation: unsubscribe then subscribe with the same identity while a delivery of the old generation is retrying: the
  retry is not sent, and its cursor write does not touch the new row.
- Shutdown: queued turn-state writes are drained before the writer is cancelled; an `ended` produced in the last tick
  is dropped without persisting `turn_open = false`, and is emitted at the next start.
- Supervisor rebuild: the barrier waits only for the writes posted before it, and lands after the writes the dead
  thread had already scheduled; the command queue is kept, so an `add` committed during the rebuild is applied.
- Failure after `step()`: an exception raised while processing a `replied` before its post (timestamp read, guard
  query, payload build, size fit) → the same `replied` (same `eventId`) is emitted at the next tick; nothing that can
  raise runs after the post.
- Persistent failure: a `step()` that raises on every tick at the same position → bounded logs (one traceback, then a
  summary per minute), backoff up to 60 s, and after 3 failures the monitor moves past the bad batch and delivers later
  conclusions.
- Restart before the background compute: a hybrid turn completed while the backend was down, its lines inserted raw at
  boot → no `ended` while `session_compute_ready()` is false, then one `replied` once the compute is current.
- Renewal never moves `turn_start_line` back (supervisor rebuild re-detecting an older `replied` after an `ended`).
- Rebase durability: restart while a rebase waits for compute; crash between begin and finish, then boot (repair
  pass); supervisor rebuild after the dead thread consumed the `rebase` command; compute failure then restart; a
  dormant monitor during a rebase; a begin committed after the runtime's initial load with the `rebase` command lost —
  in every case the monitor applies the rebase once compute is current (epoch comparison, 5 s backstop).
- Two rebuilds in a row, the second begin committing between the first apply decision and its write, then a restart:
  no re-emission (CAS on the old numbering; epoch and readiness in one snapshot).
- Lookup during a rebuild window (`user_message_count` reset to 0, agent dead): a refresh and a new subscribe succeed
  through the `history_epoch > 0` clause; `session wait-reply` on the same session is unchanged; a refresh of a live row
  whose session fails the lookup for any other reason (subscribed while live, agent died before any user message was
  indexed) still succeeds as a refresh.
- Subscribe whose verification overlaps a rebuild (arrival before begin; arrival between begin and finish →
  `numbering = NULL`): no history flood, no stuck cursor; a Claude session at boot with stale compute is **not**
  affected (no rebase, `since_line_num` kept).
- `eventId` across numberings: a shorter rebuilt numbering where a new conclusion lands on the line of an old emitted
  one gives a different `eventId`, also after unsubscribe and subscribe again (new generation).
- An emission whose `numbering` differs from the session's epoch is not posted.
- An open turn at rebase is closed (no empty `ended` after the rebase).
- Persistent failure on an `awaiting_user_input`: after 3 failures its request id is marked reported and a later
  request is still reported.
- Codex history rebuild under a live subscription, with a new numbering longer and shorter than the old cursor: no old
  `replied` re-emitted, and the next new conclusion is delivered; a delivery of the old numbering finishing after the
  rebase does not write its cursor.
- `ended` with no `Session` row: readiness counts as ready, the `ended` is evaluated normally.
- An idle, non-pending monitor runs no readiness query outside the 5 s epoch backstop.
- No `Session` row: subscribe to a live session before the watcher creates its row → `numbering = 0`, no rebase when
  the row appears, the first `replied` delivered; same with the agent dying before any row → `ended` emitted; a new
  Codex row with `last_offset = 0` and epoch 0 → `numbering = 0`, not `NULL`.
- A begin committed between `step()`'s scan and the emission phase's single session read: the read shows a new
  epoch, no write of the emission phase is posted (no emission, no emission-phase state write; step-1 writes of the
  tick, if any, are superseded by the CAS), and the monitor enters the pending rebase; same for a readiness drop or a
  guard drop in that tick.
- A compute committed between `step()`'s scan and the session read (the scan saw the final unclassified, the read says
  ready): no `ended` (the previous read said not ready), then one `replied` at the next tick.
- Previous read ready, then a raw insert (initial sync at boot), then the scan, then the compute commit, then this
  tick's read ready: no `ended` (`last_line` differs between the two reads), then one `replied`.
- A rebase applied after the agent already started a new turn that step 1 had seen before the pending state began:
  the turn is open after the apply (agent working at apply → `initial`), and its `ended` is delivered; same for a
  subscription created during the rebuild while a send queued during it starts a turn at finish.
- A read with no `Session` row compares as (ready, epoch 0, `last_line` 0): the no-row `ended` is emitted.
- Writer robustness: a `storage.write` that raises for one item does not stop the writer; a barrier is still resolved;
  a dead writer task is restarted by the supervisor, on the same queue; a dead writer followed by a thread rebuild:
  the writer is restarted before the barrier is posted, and the rebuild completes; the writer dying while the
  supervisor awaits the barrier: it is restarted and the barrier still resolves; a writer dead at shutdown: restarted,
  then drained.
- Lost rebase CAS: the CAS raises in the writer; the monitor keeps running in the new numbering, delivers one
  conclusion, and its cursor write does not move the persisted `cursor_line`; then a supervisor rebuild (and, as a
  separate case, a restart): the rebase is pending at the load, applies at the current end, and does not re-emit the
  conclusion already delivered.
- A refresh whose session fails the lookup and whose row is deleted before step 11: `-32011`, no insert.
- Dormant monitor: a turn that starts and stops entirely inside the dormant window → no `ended` before its final
  message (fresh wait on wake-up); the cleanup loop's `remove` deletes a dormant monitor; a refresh that arrived before
  `refresh_before` and verifies past it finds its row (60 s cleanup margin) and wakes the monitor, no re-creation.
- `wait_background`, case 1: a Codex user message during a subagent hold, its final ignored, the hold ends, then a
  Codex `/compact` → no `ended`.
- `history` turn: a `since_line_num` in the middle of a running turn, past it `F1`, `P2`, `F2`, agent dead: exactly one
  past event (`replied F1`), no `ended`.
- Crashed turn then pseudo-turn: a send interrupted before any assistant line (`ended` emitted), then an automatic
  `/rename` or a Codex `/compact`: no second `ended`.
- Renewal with `first`: an `initial` turn of a working agent with `since_line_num`, an earlier past `replied`, then the
  running turn interrupted before an assistant line past `L0`: its `ended` is emitted.
- Restart: a conclusion between the last persisted cursor and the restart is sent once more with the same `eventId`. A
  restart before the first past conclusion was persisted still delivers at most one past conclusion.
- Persisted cursor: a slow retry of an older event never moves it back.
- Retries stop after `events/unsubscribe` and after expiry.
- Runtime robustness: an exception in one monitor's tick does not stop the others; a dead thread is restarted; an `add`
  for an existing id is an update; concurrent subscribes cannot exceed the limits.
- Retry policy per status code; no retry on `410` / `413` / other `4xx`.
- Authority: revoked connection deletes; `base_url()` empty keeps.
- Pinned helper: refuses non-global addresses and redirects; classification of failures, including a certificate
  failure (`ssl.SSLError` in the cause chain) → `tls_error` and a DNS failure → `connection_refused`.
- Callback verification: challenge echoed → success and 24 h cache; `3xx` / `4xx` / `5xx` / bad echo / timeout → the
  `-32015` reasons of section 5.3; two concurrent subscribes on the same `(connection, url)` send one challenge; the
  ninth concurrent verification waits up to 5 s, then returns `-32013` (`concurrent_verifications`); the 61st
  verification in a minute to one host returns `-32013` (`verifications_per_minute`); joiners of a deduplicated
  verification take no slot.
- `events/subscribe` on an expired row or on a foreign `data_dir` row: handled as new (fresh cursor, limits, verification).
- `events/unsubscribe`: idempotent; another connection's identical arguments never match; `delivery.mode` optional;
  malformed params → `-32602`.
- Callback URL: `https:///x` (empty hostname), `https://[::1/x` (`urlsplit` raises) and a non-string URL are refused
  with `-32602`.
- `ttlMs`: a 400-digit integer (clamped to 7 days, no exception); `1e400` (parsed as `inf`, refused); absent, `null`, `0`, negative, above 7 days, `NaN`, `Infinity`, `-Infinity`, a string, a boolean (each of these last five refused with `-32602` at step 0, before
  any verification) (`isinstance(True, int)` is true in
  Python, so booleans are checked first).
- `since_line_num: 3.0` is accepted and stored as the integer `3`.
- Re-subscribe after expiry, before the cleanup removed the row: a new generation, a fresh monitor (new cursor and turn
  state), and deliveries reach the new row.
- Supervisor rebuild during a hold, after a `replied` closed the turn: the same transition is not seen as a new turn
  again (float storage), so no spurious `ended`.
- Monitor commands: an `add` with a new generation replaces the monitor; a late `remove` of an old generation does not
  stop the new one.
- A retry after a secret rotation is signed with the new secret.
- Audit: one `McpOperation` row on creation and on deletion, none on refresh.
- Instance binding: the runtime and the cleanup ignore rows of another `data_dir`.
- `data` matches the `payloadSchema` of section 6.3 for each outcome (validated with `jsonschema`).

**Conformance (manual, optional).** Draft suite `modelcontextprotocol/conformance` PR #521, behind a local proxy that
injects the bearer token (the suite has no `--header` option). Its receiver listens on `http://127.0.0.1:<port>`, which
TwiCC refuses (https and public addresses only). Without a public https tunnel in front of that receiver, only
`events-discovery` and the SSRF-negative checks can run; `events-webhook` and `events-webhook-delivery` need such a
tunnel (`EVENTS_WEBHOOK_CALLBACK_BASE`). The discovery row reads the `extensions` form, which R9 provides.

**E2E with ChatGPT (mandatory before release).** In a Work chat:

1. `server/discover` with both capability forms is accepted; `session.concluded` appears on the plugin page.
2. Subscribe: verification succeeds; the row is stored. Then one request asking to monitor two sessions, and one asking
   to monitor ten sessions: all
   subscriptions succeed.
3. `replied`: ChatGPT uses `reply.text` directly in the chat.
4. A truncated text (`text_truncated: true`): ChatGPT can still reach the full text through the content tools.
5. `awaiting_user_input`: one event per new request; `request_type` is shown correctly.
6. `since_line_num`: a reply that landed before the subscription is delivered at once.
7. Refresh across a backend restart; unsubscribe; connection revocation stops delivery.
8. Feedback loop: a task told to answer every conclusion is stopped by "stop monitoring" and by revoking the connection.

## 13. Operations reserved to the user

- `uv add standardwebhooks`.
- Apply the new migrations on the running instance (the `McpEventSubscription` table and the `Session.history_epoch`
  column; `devctl.py` start applies them).
- Restart the backend after implementation.

## 14. Known limits

- **Polling cost.** Each monitor runs what one CLI wait runs per tick: one indexed `SessionItem` query, plus the
  in-memory activity read, which goes through the agent's `get_info()` (`src/twicc/agent/base_agent.py:537`, psutil
  memory read). Because a dropped conclusion keeps the same `_SessionWait` (section 7.2), an idle monitor in `ended`
  state also does one `refresh_from_db(fields=["last_offset"])` and one file stat per tick (`:569-571`). The
  100-subscription cap bounds the total. The monitor also reads `get_agent_info()` once per tick for Rules A and B. The
  single session read of section 7.6 (one primary-key read: epoch, readiness, `last_line`, title) runs: once per tick
  in which a conclusion reaches the emission phase (an `ended` with a turn open included, so every tick of a
  not-ready window with an open turn, together with the guard query for a transition-opened turn), once every 5 s per
  monitor as a backstop (at most 20 reads per second for 100 monitors), and once per tick while a rebase is pending,
  which can last a whole boot migration pass.
- **Compute not current for a long time**: an `ended` is withheld while the session's compute is not current
  (section 7.3, step 3). That can last until the next backend start: a session whose background compute failed
  (`background_compute_task.py:700-705`), an unreadable Codex rollout (one attempt per start,
  `providers/codex/background_compute.py:31-34`), a Codex rewrite waiting for its agent to die (`:29-30`). The withheld
  `ended` is lost if a new turn starts first. A **pending rebase** (section 7.6) whose compute never becomes current
  suppresses **every** event of that subscription, `replied` included, until the compute is current (for example a
  replacement that failed after begin, retried once per start, `background_compute.py:344-347`).
- **Raw lines at boot**: live sync classifies new lines inline while lines inserted raw at boot stay unclassified until
  the background compute reaches the session. A final written during the downtime (raw) followed by a live final can be
  skipped: `step()` sees only the live one, and its `replied` moves the scan past the raw one. At creation, raw lines
  also do not count for the `history` turn rule. Short boot windows: sessions are computed most recent first.
- **Codex history rebuild**: conclusions that occur between the **rewrite detection** (the watcher defers every event
  of a rewritten path, `providers/codex/sessions_watcher.py:118-122`, `:193`, `:211`) and the applied rebase are not
  delivered, including a final written by a turn after the detection (its turn is closed at rebase, so no empty `ended`
  replaces it). A subscription created across a rebuild discards its `since_line_num` and starts at the new end. A
  `since_line_num` taken from a send made **before** a rebuild, on a subscribe that arrives **after** its finish, is
  taken at face value in the new numbering (undetectable; the CLI `--from` has the same limit): at most one wrong past
  conclusion, with a correct new-numbering `eventId`. The
  rebuild holds the DB writer lease from begin to finish (`rollout_migration.py:343-345`), so a subscribe's step 11 and
  every monitor state write wait for the whole rebuild; on a large history, a subscribe can exceed the client's request
  timeout.
- **Persistent failure on `awaiting_user_input` or `ended`**: after 3 failures an `awaiting_user_input` is marked
  reported without being sent (lost); a failing `ended` is retried at the backoff pace until a new turn replaces the
  wait (section 7.1).
- **Head-of-line latency.** One thread steps the monitors one after another. A `step()` blocked on a SQLite busy wait
  delays every other monitor by that time. Detection is late, never lost.
- **`wait_background` without a new answer** delivers nothing until the next turn or the agent's death. The ignored
  message is not delivered by an event (section 7.5) — except when the wait holding it is replaced: a restart, a
  supervisor rebuild, a tick failure (section 7.1) or a dormant wake (section 7.1). The ignored position is not
  persisted, so the fresh wait reads that message again, and if the background work has ended by then, it is emitted as
  `replied`.
- **Persistent tick failure** on a `replied` / `provider_error`: after 3 consecutive failures at the same position, the
  failing batch is skipped, and the conclusion it held is lost (section 7.1).
- **Answer written before creation** (no `since_line_num`, turn still running): reported as `ended`, not `replied`
  (section 7.3).
- **Rule A misses**, all dropping an `ended` (never a `replied`): turns that start with no state transition (a queued
  Claude message, a Codex user message during a subagent hold, Claude follow-up turns under a hold); a new turn whose
  transition is followed, before any tick sees it, by a same-state `assistant_turn` re-set (`_set_state` always sets
  `previous_state = old_state`, `base_agent.py:183-188`; the Codex hold re-set `codex/agent/agent.py:1510`, the hybrid
  optimistic re-set `hybrid/agent.py:286`), so the observed `previous_state == state`; a turn that starts
  and ends between two ticks, never seen working; a conclusion line without a timestamp, whose time falls back to the
  tick start (section 7.3).
- **Guard misses** (an `ended` dropped by the guard of section 7.3, step 3, for a transition-opened turn interrupted
  with its agent still alive, before its first assistant line): a turn reopened by a task notification or a
  cron-restart prompt, whose first line is a `SYSTEM` or content line, not a prompt
  (`providers/claude_code/compute.py:88-91`, `:1700-1723`); a Codex turn opened by TwiCC's hidden resume instruction
  (`<twicc-resume>`, classified `SYSTEM`, `providers/codex/compute.py:1574`, `:4191`); a turn started by a slash
  command (its only `USER_MESSAGE` is the command line). A turn whose agent died is never dropped by the guard. Under `wait_background` such a turn then
  gives no event at all.
- **Fast crash before a new subscription**: a send, a crash before the CLI writes anything, then a new
  `events/subscribe` with `since_line_num`: nothing is past the cursor and the agent is not working, so no turn is open
  and no `ended` is emitted (`session wait-reply --from` would report `ended`).
- **Hybrid adoption at boot**: an adopted hybrid CLI is set to `user_turn` even mid-turn (`hybrid/agent.py:200`), so an
  `ended` can be emitted while the CLI still works.
- **Pseudo-turn whose agent dies within its flush window** (a manual stop right after a client rename, which triggers the
  hybrid auto `/rename`; a kill-and-relaunch for a startup setting; a backend restart): the guard passes on "agent dead",
  and an empty `ended` is emitted.
- **Pseudo-turn running at creation** (a hybrid local command or a Codex `/compact` in progress when the subscription is
  created): the turn is opened as `initial`, which the guard does not filter, so it ends in an empty `ended`.
- **Cron restart at boot**: the TwiCC-internal prompt's answer is delivered as `replied`, and the killed turn's `ended`
  is not (section 9.2).
- **Two turns that write no line at all** share the `eventId` of their `ended` (section 8.4).
- **Identity excludes `since_line_num`**, a deliberate deviation from the draft (section 5.2, step 7).
- **Subscriptions are bound to the data dir that created them** (section 9.2). Moving the data dir to another path makes
  its rows foreign: they stop delivering until ChatGPT's next refresh replaces them. A copy restored at the same path
  (another host, a cloned container) is not detected and sends like the original.
- **Replay after re-creation.** ChatGPT's refresh re-sends the original `arguments`, `since_line_num` included. When the
  row is re-created (expired before the refresh, foreign after a data dir move), the new subscription starts again from
  that old line and delivers the first conclusion after it, usually one delivered long ago. It carries the same
  `eventId` as the first time (section 8.4), so a receiver that keeps ids that long deduplicates it; whether ChatGPT
  does is unknown.
- **External MCP unconfigured at delivery time** (`base_url()` empty, section 8.7): the attempt is not sent, but the
  cursor still moves and is persisted. That conclusion is lost.
- **Dormant window** (between a subscription's `refresh_before` and the refresh that wakes its dormant monitor): the
  monitor does not tick. Transcript conclusions (`replied`, `provider_error`) that occur in that window are detected
  after the wake-up, late but not lost. Lost: the `ended` of a turn that started and stopped entirely inside the window
  (no transition is seen at wake-up), and a request created and answered inside the window (Rule B reads only the
  requests pending at a tick). A delivery attempt of that subscription that falls in the window fails the authority
  check (section 8.7) and is not sent, while its cursor is still persisted afterwards, so that conclusion is lost too.
  The window lasts the verification time of the waking refresh (up to 15 s) plus its wait for the DB write lock; if the
  cleanup takes that lock first and deletes the row, the refresh finds no row and becomes a new subscription.
- **Limit overshoot**: the limit counts only rows live at the request's arrival, so a dormant row whose timely refresh
  is still verifying is not counted; a new subscription committed meanwhile, then that refresh, can bring a connection
  briefly to 51/50 (or the total to 101/100). The overshoot is bounded by the number of timely refreshes still verifying
  across their expiry.
- **Failed state write**: a turn-state or cursor write that fails (logged), or that was in flight when the writer task
  died, stays stale in the database until the next
  write of that field; it matters only after a restart or a supervisor rebuild (section 7.1). A failed rebase CAS heals
  at the next load, where the rebase applies again at the current end: a conclusion still undelivered at that moment
  is lost.
- **Residual duplicate emission**: only a failure after the first post of a tick could re-emit a conclusion, and
  nothing fallible runs there (section 7.1). Such a duplicate would carry the same `eventId`, except an `ended` without
  a line, whose key reads `Session.last_line` again.
- **Hybrid prompt typed in the terminal right after an `ended`**: the watcher indexes such a prompt before its
  transition (`sessions_watcher.py:725-752`, `hybrid/agent.py:587-594`). If an `ended` is emitted or guard-dropped in
  that window of milliseconds, `turn_start_line` already covers the new prompt, and an interruption of that new turn
  before its first assistant line, with the agent alive, is dropped by the guard.
- **`ended` of a session with no row** (live process, no `Session` row yet, `step()` branch `:589-598`):
  `turn_start_line` keeps its value, so when the row appears later, the dead turn's prompt can be past it and let the
  next pseudo-turn pass the guard. Reachable only when the watcher creates the row more than 5 s after the agent died.
- **Pending retries** are lost on restart (R6). After a restart: a transcript conclusion past the persisted cursor is
  re-detected (same `eventId`); a still-pending request is re-detected (same `eventId`); a turn open at shutdown is still
  open (`turn_open` is persisted), so its `ended` is emitted when its agent died with the backend. An `ended` emitted
  just before a restart, whose cursor was not yet persisted, is not re-detected (`turn_open = false` is already
  persisted). A conclusion still retrying when a silent move or a later conclusion's write pushed the persisted cursor
  past it is not re-detected either (section 9.1). Emissions of the thread's last tick at shutdown are dropped and
  re-detected at the next start, an `ended` included (section 7.1).
- **Draft churn.** The draft can still move the capability key and the error vocabulary. All wire constants live in
  `catalog.py` and `methods.py`, so a realignment touches one place.
