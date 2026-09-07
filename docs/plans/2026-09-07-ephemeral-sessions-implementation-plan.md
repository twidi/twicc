# Ephemeral Sessions Implementation Plan

**Status:** implemented and independently reviewed; see [validation results](2026-09-07-ephemeral-sessions-validation.md)
**Design:** [Ephemeral Sessions Design](2026-09-05-ephemeral-sessions-design.md)
**Design commit:** `3e51e740`
**Date:** 2026-09-07

## 1. Delivery contract

Implement the complete design for Claude Code and Codex. Keep the current
branch and preserve unrelated untracked documents. Do not restart the user
instance, install dependencies, change provider user configuration, or apply
Django migrations. No Django schema change is required. An IndexedDB version
upgrade is allowed for pending controls. Do not add a changelog entry.

Commit this plan after independent adversarial reviews pass. Use one final
implementation commit after integration and correction. Intermediate code
commits are allowed if they form verified units. Every commit includes a
body and the current Codex model trailer.

The product distinguishes an unsent draft with `ephemeral: true` from a
launched entry (`ephemeral === true && !draft`). Only launched entries lose
the composer and persistent-session actions. Received results remain in the
browser until Discard. Running agents do not survive backend restart.

## 2. Dependency and ownership map

| Task | Depends on | Primary files |
| --- | --- | --- |
| A: admission registry and WS contract | none | new `src/twicc/agent/ephemeral.py`, `asgi.py`, `core/services/session_creation.py` |
| B: agent lifecycle | A | `agent/base_agent.py`, `agent/base_manager.py`, `pending_session_attributes.py` |
| C: provider behavior | B | both `providers/*/agent/agent.py`, both manager files, `agent/system_prompt.py` |
| D: browser lifecycle and storage | A contract | `stores/data.js`, `utils/draftStorage.js`, new `utils/ephemeralSessions.js`, `composables/useWebSocket.js` |
| E: rendering and composer | D | `MessageInput.vue`, `SessionItemsList.vue`, provider helpers, new actions bar |
| F: actions, sidebar, notifications | D/E | session views, lists, headers, notification handling |
| G: verification and correction | A–F | targeted tests, full existing suites, runtime probes |

Keep independent review ownership separate from implementation ownership.
Any parallel edits must have non-overlapping file ownership. Cross-file
interfaces below are agreed before editing shared files.

## 3. Task A — admission and transport

### Registry

Create a small process-local registry with no prompt, attachments, answer,
usage, or SDK objects. Store original draft id, provider, project id, optional
canonical id, and whether admission is pending. Return an opaque owner token
from synchronous `reserve`. Expose ownership checks, canonical binding,
settlement, known-id checks, pending snapshot, and shutdown cleanup.

Reserve before the first await in the ephemeral WS send path. Wrap the
existing handler in an outer admission/finally envelope if needed. Pass the
owner token as a trusted private argument to the service and manager. Direct
trusted service calls reserve synchronously before their first await. A
second request cannot borrow a reservation based on its id alone.

Every creation checks known ids even without `ephemeral`, and across provider
boundaries. Repeat owner validation under the manager lock. Keep known-id
tombstones until backend shutdown. Always settle pending admission in
`finally`, including validation failures and cancellation. Send one generic
`ephemeral_admission_failed` update when admission fails. The correlated
normal error still reaches the sender. Avoid duplicate terminal handling.

### Service and WS

Add trusted `allow_ephemeral=False` to the service. Only the human WS path
sets it true. Ignore the raw flag on the CLI/drop-request path. Validate
hybrid, worktree and spawned-by conflicts before side effects. Reject Codex
hardcoded first-message commands. Preserve provider state gates, project and
settings validation, title checks, and trust enforcement.

Forward `ephemeral=True` through pending attributes and the manager call.
Use the ephemeral prompt addendum and skip layout resolution. Preserve
`SendDeliveryError.code`. Drain draft pending buffers on service errors after
stashing. A request with ephemeral mode for an existing persistent row fails with
`ephemeral_existing_session`. Its provisional reservation is released entirely
when the DB proves the row preexists; it leaves no readonly tombstone and does
not change the existing agent. A subsequent normal send must still work.
This is the only reservation-release exception; failed new runs keep ids.

Extend `active_processes` with `ephemeral_starting`, an array of admission
metadata. After asynchronous enrichment, read live agents and pending
admissions together without an intervening await. Reuse enrichment only for
still-current records. New records can use technical metadata alone.

Frames use the design's exact `ephemeral_result` and
`ephemeral_admission_failed` shapes. Ephemeral info carries
`extra.ephemeral` and `extra.ephemeral_draft_id`. Do not fetch or serialize a
Session row to construct these frames.

### Acceptance

Test duplicate admission, cross-provider reuse, false/absent flags,
validation failures, cancellation, factory suspension during snapshot,
and settlement after reconnect. Assert no starting reservation remains after
failure. Test that a caller using `allow_ephemeral=False` cannot enable ephemeral
mode. An untrusted project may use ephemeral mode with its normal trust clamp.
Test persistent-id rejection followed by successful ordinary delivery.

## 4. Task B — agent lifecycle

Add constructor `ephemeral=False` and result fields to `BaseAgent`: final
text, usage, emitted flag, soft-interrupted flag, and original draft id.
Add the keyword to the abstract factory and provider factories without
changing existing non-ephemeral callers. Forward the admission owner through
manager creation to the shared start path.

Register ephemeral agents normally, but skip ProcessRun creation and all
session lifecycle writes. Bind canonical ids before STARTING. Pop pending
titles before `agent.start`; pop settings/attributes after it. A `finally`
drains all buffers under both ids even when factory, binding or start fails.
Do not run title retry/protection, settings reload, or cron persistence hooks
for ephemeral agents. Keep usage activity, pending-request resolution,
timeout monitoring, shutdown, and context cleanup.

Emit once on USER_TURN, using stopped for a soft interruption. Set the emitted
flag before any await. Schedule kill in a strongly referenced task with error
logging; never await self-cancellation from the agent callback. DEAD-first
emits stopped for deliberate stop, error for failure, and nothing on shutdown.
Do not emit a second result during cleanup. Shutdown drains/cancels detached
cleanup tasks without deadlocks and clears registry data at lifecycle end.

Add manager-level readonly checks to creation and send/resume paths so direct
callers cannot steer or resume ephemeral ids. Stop and permission/question
responses remain supported. Restrict soft interrupt in the UI; retain the
backend stopped fallback if invoked directly.

Test fake Claude-like deferred buffer reads, canonical rebinding, no DB writes,
all terminal statuses, callback return before kill, exact-once emission,
shutdown, and cleanup after each startup failure.

## 5. Task C — providers and addendum

### Claude Code

Pass `no-session-persistence`, `strict_mcp_config=True`, no injected MCP,
plugins or work directories. Do not write MCP configuration or SDK logs.
Suppress raw stderr and payload-bearing error logs for this mode. Keep errors
in memory for the final result without printing their content.

Disable CronCreate/CronDelete/CronList and omit cron persistence hooks. Guard
manager callbacks too. Keep permission/question handling and normal policy
hooks. Capture ResultMessage final text and cost/duration/usage before state
transition. Preserve existing held-turn behavior and error handling. Record
soft interruption before it transitions back to USER_TURN.

### Codex

Use process-local `features.plugins=false`. After initialization call
`config/read` with `includeLayers:false` and the actual cwd. For every existing
MCP server, set `mcp_servers[name].enabled=false` through a nested table in thread config. Preserve
its transport; do not create absent transportless entries. Do not inject
TwiCC MCP config, alias registration, or session context. Start an ephemeral
thread and pass `work_dirs=[]` explicitly. Do not create work directories or
widen the sandbox through them.

Gate stderr attachment, stream-event logging, and both approval log calls.
Suppress payload-bearing Codex error/approval exceptions and startup exceptions
in the agent, manager, service and WS path. Do not print exception strings,
tracebacks containing SDK payloads, or stderr tails for ephemeral runs. Test
with a sensitive marker embedded in an SDK exception and inspect captured logs.
Keep MCP server names as literal keys in the nested table, including dots and
quotes. RPC override keys do not interpret TOML quoting.
Capture only parent-thread agent messages. Prefer the latest final_answer
phase; fall back to the latest unphased message. Ignore commentary-only and
child-thread messages. No Codex cost is invented. Native child creation uses
`fork_turns="none"` (inheriting a non-persisted parent fails). State this in the
addendum. Ephemeral holds poll child in-memory `thread/read` state rather than
waiting for watcher updates; cancel polling at hold completion or shutdown.

### Addendum

Replace all static TwiCC/provider addenda with the short ephemeral contract.
Keep project, provider, settings, start time and `ephemeral:true`. Omit session
id and artifact/scratch paths. State no local session transcript; do not
promise no disk writes or remote-provider retention. Document that V1 turns
off inherited MCP for both providers and plugins for Codex.

Test options and logging with debug enabled, no work dirs/MCP files, no cron
rows, provider errors, held turns, and final phase/thread filtering.

## 6. Task D — browser state and IndexedDB

Create a focused helper module for the launched predicate, serializable
fields, prompt summaries, terminal transitions, and ordering. Keep store
orchestration in actions or a dependency-injected action module so tests can
exercise production behavior without static import cycles.

Persist the explicit draft flag, mode, phase, timestamps, original draft id,
prompt summary, result, and any canonical alias needed after reload. Legacy
stored drafts without the flag remain drafts. The writer accepts drafts and
launched ephemeral entries. Hydration rebuilds visible prompt/result items;
it never promotes an unsent draft.

Use one creation preparation helper from MessageInput and FailedSendBanner.
It derives ephemeral/hybrid/layout fields, registers the in-flight snapshot,
and promotes an ephemeral draft before sending. If `sendWsMessage` returns
false, immediately restore the draft and its content; test the offline path. Keep retry attachments in
existing in-flight storage until ack/failure; retain only lightweight names
and MIME types in the launched prompt. Correlated ack clears the in-flight
snapshot without deleting the ephemeral entry or its visible prompt.

Failure recovery creates a fresh draft UUID, preserves ephemeral mode,
settings, title, text and recoverable attachments, and clears launch fields.
Rekey failed/in-flight snapshots, MRU, route, title suggestions and local state.
Do not restore a discarded entry. Late correlated errors target the bound id
through a persisted alias or a rekeyed snapshot.

Special-case binding without waiting for a server Session row. Rekey store,
IndexedDB, process state, items, in-flight/failed sends and control records.
Use lazy router import. For equal Claude ids, still finish local bookkeeping.

Add a dedicated small IndexedDB store for Stop/Discard intentions, with a
version upgrade in the existing open handler. Hydrate it before WebSocket
reconciliation. Discard removes content and send snapshots immediately but
keeps its control tombstone. Binding or snapshot resolves and retries Stop
against the canonical id. Remove intentions on confirmed terminal state or
settled absence; pending admission never counts as absence.

On active snapshot: resolve aliases, retry controls, then mark only unmatched
running entries lost, excluding admissions. Handle admission-failed frames
for a replacement socket. Result frames update only existing launched entries,
persist terminal state, and rebuild the synthetic item. Late frames never
recreate a discarded entry. Ignore streaming frames for launched entries.

Test actual production actions with a fake socket/router and IndexedDB adapter:
unsent reload; promotion; ack; rekey; failure/Retry; pending snapshot; admission
failure after reload; received result reload; lost; Stop/Discard before bind;
late result/error; attachment summaries; complete cleanup. Include persistence
round-trip tests against the actual writer/hydrator field lists.

## 7. Task E — composer and message view

MessageInput gets a draft-only icon, tooltip, explainer dialog and synced
`ephemeralExplainerSeen` setting using the existing settings schema,
validator, getter, setter and synchronization output. Guard bubbling WA events.
Enabling ephemeral disables hybrid, and enabling hybrid disables ephemeral.
Draft settings and sending stay available. No new keyboard shortcut.

SessionItemsList skips server item reads for launched ephemeral entries.
Build synthetic user content from explicit attachment summaries. Add
`SYNTHETIC_ITEM.EPHEMERAL_RESULT` at -400 and propagate syntheticKind through
visual item construction. Add `buildEphemeralResultContent(text)` to provider
helpers using native assistant layouts and `setParsedContent`.

Render phase callouts and normal assistant markdown/copy. Keep the working
placeholder and existing pending-request form while running. Replace the
composer only after launch with EphemeralActionsBar: Stop while running,
Discard always. SessionView renders a direct items pane for launched entries,
without docking, persisted layout, or files/browser/terminal tabs.

Test synthetic shapes and visual item order. Build Vue production assets to
catch component/import/template errors. Inspect UI on an isolated test surface
when available, including narrow width and permission/question widgets.

## 8. Task F — guard audit and notification behavior

| Site | Expected launched-ephemeral behavior |
| --- | --- |
| SessionView command guards | no archive/pin/hide/share/fork/unread/interrupt; Stop and Discard remain |
| SessionView rename dialog | local title only, no REST rename |
| SessionHeader | no persistent actions, settings apply or dock controls |
| SessionListItem | Ephemeral tag, phase dot, no cost/meta, Stop/Discard menu |
| SessionSelectionBar | exclude persistent batches; include in Discard batch |
| SessionList separator/search labels | Ephemeral group before pinned; accurate local label |
| data.js comparator | launched ephemeral first, newest started timestamp first |
| sidebarSessions.js | exclude from cross-filter Active elsewhere, like drafts |
| utils/sessions.js unread | false for ephemeral |
| useWebSocket process transitions | no last_new_content_at or viewed writes |
| notification state handling | local title and Ephemeral session finished; no viewed REST call |
| session_updated | log invariant violation for launched ephemeral; do not overwrite it |
| reconciliation and in-flight audits | no server row/items lookup for ephemeral |
| MessageInput settings apply | unavailable after launch |
| route/session loading | keep local entry; no server 404 removing it |
| app title suggestion auto-apply | local title only for ephemeral |

Search all draft guards in frontend source, including sites outside this
initial list. Preserve unsent-draft behavior. Test comparator/classification,
forbidden-action predicates, and reconciliation skip behavior.

## 9. Task G — acceptance and independent implementation review

Run targeted backend tests for admission, service, manager, provider options,
logging and addendum. Then run relevant existing agent/provider tests and the
full backend suite when practical. Run `cd frontend && npm test` and
`cd frontend && npm run build`. No package installation is part of these steps.
Run `git diff --check` before commit.

Use real bundled provider runtimes with temporary HOME/provider homes and
controlled local model endpoints. Keep all throwaway content in the session
scratch directory or test temporary directories. Never point runtime probes
at the user's provider data. Check the runtime's main and child transcript
locations and TwiCC SDK log locations after a completed run. Exercise multiple
assistant messages, attachment input, tool approval, Stop and failure. Verify
no Session, SessionItem, ProcessRun, SessionCron, artifact or scratch session
folders are created by the ephemeral path.

For Codex, reuse the local Responses-server approach from
`tests/test_codex_goal_steering_integration.py`. For Claude, use the installed
CLI with an isolated Anthropic-compatible local endpoint if supported. A
provider-specific transcript leak is a feature defect: fix suppression or
prevent the leaking capability within the documented contract, then repeat
the runtime check. Do not substitute fake-agent evidence for this runtime gate.

Validate reload, delayed binding, pending requests, Stop/Discard, and backend
restart behavior using an isolated harness. The user's running backend is not
restarted. If a full browser harness is unavailable, construct a temporary
controlled host with the production frontend and fake WS/provider responses;
verify the same behavior and report the exact coverage limit.

Independent internal reviewers inspect the final diff against this plan and
the spec. Correct blockers and repeat until PASS. Finish with a requirement
matrix containing concrete files/test results for every design section and
runtime gate. Do not declare completion with missing runtime evidence.

## 10. Review record

All three reviewers return PASS. The review adds provisional reservation
release for existing persistent sessions, payload-safe Codex exception logs,
literal MCP names, and offline-send rollback. These details also clarify the
approved design. Implementation acceptance and correction records appear in the
[validation report](2026-09-07-ephemeral-sessions-validation.md).
