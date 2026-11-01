# Claude session title synchronization

Date: 2026-11-01
Status: adversarial review complete; pending user approval; no implementation authorization.
Revision: 2

## 1. Purpose

Keep the TwiCC title and the active Claude Code process in agreement.
Prevent stale transcript metadata from undoing a title decision.
Keep voluntary renames, including a return to any earlier title.

The prevention guarantee covers processes managed by TwiCC. Unmanaged sessions retain best-effort file synchronization, as specified in section 6.4.

This document specifies behavior and acceptance criteria. It is not an implementation plan.
It does not prescribe classes, lock layouts, database columns, retry constants, or test fixtures.

## 2. Evidence

### Production incident

Session: `4d40cf22-f398-49d8-9077-841cb8e6b520`.
Times below use Europe/Paris, UTC+1.

| Time | Timeline event | Observation |
|---|---|---|
| 2026-11-01 13:07:32.500 | 9264, source `auto` | TwiCC changes the title from a suffix containing `rail` to one containing `reformulation`. |
| 2026-11-01 13:07:32.662 | 9265, source `agent` | Transcript processing restores the preceding title, after about 162 ms. |
| 2026-11-01 13:07:32.838 | Backend log | TwiCC protects the title containing `reformulation`. |
| 2026-11-01 13:07:32.842 | Backend log | TwiCC then protects the restored title containing `rail`. |

The automatic publisher also performs a corrective provider write when the database title differs.
That correction can propagate a stale restoration. A JSONL write cannot always be attributed to the CLI.

### Isolated live experiments

Verified with `claude-agent-sdk==0.2.175` and its bundled Claude Code `2.1.314`.
Experiments use a separate provider home, transcript directory, and session.

| Experiment | Observed result |
|---|---|
| Set A internally; append B through Python `rename_session()`; trigger metadata persistence | The CLI re-appends A. |
| Return C through `UserPromptSubmit.hookSpecificOutput.sessionTitle` | The CLI adopts C and later re-appends C. |
| Send a `rename_session` control request while idle | The CLI acknowledges and persists the title. |
| Send that request during assistant text streaming | The request succeeds without interrupting the response. |
| Trigger metadata persistence after the streaming rename | The CLI re-appends the new title. |
| Set `session_id` to a different session | The CLI rejects the request. |
| Explicitly request a previously used title | The CLI accepts it. |
| Disconnect and resume after a confirmed rename | The title remains correct. |
| Register a Python SDK `SessionStart` callback | The callback does not fire in these experiments. |

The current title test suites pass: 166 tests across automation, compute application, and protection lifecycle.
Their success does not cover the reproduced live failure.

### Root cause

Python `rename_session()` appends a `custom-title` entry. It does not command the active process.
The CLI keeps `currentSessionTitle` and periodically persists session metadata.
External file writes can therefore conflict with the CLI's internal state.

TwiCC currently protects titles after publishing, and full compute bypasses the Claude protection path.
The publisher can mistake a stale database restoration for a newer decision.

## 3. Scope and constraints

In scope:

- Automatic and explicit TwiCC renames of persistent Claude Code sessions.
- SDK sessions, hybrid sessions, startup, shutdown, and sessions running outside TwiCC.
- Live transcript processing and full compute.
- Queued, failed, superseded, and confirmed title publications.
- Compatibility verification when the Claude Agent SDK changes.

Out of scope:

- Changes to title generation, prompts, cadence, eligibility, or model choice.
- Changes to Codex title behavior.
- A blacklist of previous titles, lexical similarity rules, or historical title repair.
- A new persistent title revision counter or a general durable publication queue.
- Coordination between independent Claude processes simultaneously writing the same session.
- A guarantee of atomicity across SQLite, the CLI, and abrupt backend termination.

Keep the existing `title_origin` rules from the automatic-title design.
Explicit TwiCC writers set the origin. Raw provider entries do not establish human intent.
Accepting an external title does not independently change its origin or the automatic-title cadence.

Do not modify historical timeline events or reclassify existing titles.
Ephemeral sessions and sessions without an available transcript keep their existing eligibility behavior.

## 4. Decisions and alternatives

The user accepts a dependency on the private Python SDK API.
Every Claude Agent SDK update must verify this dependency against the real bundled CLI.

| Approach | Benefit | Limitation | Decision |
|---|---|---|---|
| Reject a return to the immediately preceding title | Small rule using existing timeline data | Misses older stale values and can reject voluntary `/rename` | Rejected |
| Use `sessionTitle` hooks as the primary publication path | Updates CLI memory through supported hook output | Waits for a hook boundary; Python `SessionStart` delivery is not verified | Not the primary path |
| Command the active CLI and protect the transition | Updates CLI memory immediately, including during streaming | Uses a private Python entry point and requires ordering safeguards | Selected |
| Add persistent revisions and a durable publication subsystem | Broader crash recovery and ordering model | Larger scope than the reproduced defect requires | Deferred |

## 5. Terms

- **Decision:** a title accepted from an explicit TwiCC request or the automatic-title service.
- **Publication:** delivery of that decision to the provider process or its inactive transcript.
- **Confirmation:** evidence that the chosen delivery path applies the requested title to the intended session.
- **Echo:** a provider observation produced by a TwiCC publication. It is not a second decision.
- **Stale observation:** transcript data or computed results that precede a newer accepted decision.
- **External rename:** a new title applied through Claude Code outside TwiCC's explicit title writers.

The title text alone does not identify a decision, an echo, or a stale observation.
An explicit A → B → A sequence contains distinct decisions.

## 6. Publication behavior

### 6.1 Active SDK process

Deliver the title through the connected process's control channel.
Use the `rename_session` subtype and the intended `session_id`.
Await its response. Do not interrupt the assistant response or add a conversation prompt.

The verified request is:

```python
await client._query._send_control_request({
    "subtype": "rename_session",
    "title": title,
    "source": "host",
    "session_id": session_id,
})
```

Encapsulate this private dependency in one provider-specific compatibility boundary.
The protocol's `source` value does not redefine TwiCC's `title_origin` or timeline attribution.

Do not append a title directly to the transcript while a managed SDK process is active.
An unavailable channel, timeout, or rejection must not silently select that unsafe fallback.

### 6.2 Active hybrid process

Keep the existing `/rename <title>` route through the terminal.
The CLI must apply the command before synchronization counts as confirmed.
Scheduling an auto-paste task, or successfully pasting bytes, is not sufficient confirmation.

Keep terminal handling asynchronous. A busy composer must not block WebSocket handlers or state transitions.
Supersede obsolete queued title publications. Do not disturb user input or interrupt an assistant turn.

Do not append directly to the transcript as a fallback while the hybrid process remains active.

### 6.3 Startup and process replacement

If a managed process is starting, retain the latest title for delivery when its channel becomes usable.
Do not treat `STARTING` as proof that no process can overwrite a direct file append.

A confirmation belongs to a specific session and process incarnation.
An old process's response must not complete a newer process's publication.

### 6.4 No active managed process

Without a managed process, use the SDK's file-based `rename_session()` as best-effort transcript persistence.
A lack of manager entry does not prove inactivity. Do not claim that this write updates an external process's internal title.

If no external process writes the session, the title must survive the next managed launch.
If an external process remains active, its subsequent title entries remain authoritative for normal transcript import.
It may therefore restore its internal title after a TwiCC file write. This known limitation is outside the managed-process guarantee.

Do not install indefinite transition protection for an unmanaged session or wait for an unavailable process acknowledgement.
Import must remain able to accept a subsequent external `/rename`, including an earlier title.
Adding control or reliable liveness detection for unmanaged processes is not part of this change.

### 6.5 Title representation

Use one canonical title for the accepted database decision, publication, confirmation, and timeline event.
The CLI must be able to represent that title. Do not treat a known normalization difference as stale metadata.

For new Claude title decisions, strip surrounding whitespace and collapse internal whitespace runs to one ordinary space.
Preserve printable Unicode text, including accents and punctuation, within the existing 200-character validation limit.
If the CLI requires other transformations, validate or canonicalize them before accepting the decision, rather than keeping divergent titles.

Apply this rule consistently to SDK, hybrid, and file publication paths. Do not change Codex validation.
Do not backfill or normalize existing database titles merely because this feature is deployed.

Publication and managed resume must not silently shorten an accepted title to the hybrid launcher's current 100-character limit.
Any temporary launch title must be reconciled to the full accepted title before synchronization counts as complete.

## 7. Ordering and transition safety

### 7.1 A decision must survive its publication window

Establish transition protection together with acceptance of the database decision, before readers can restore older data.
The protection applies before publication, during publication, and while older observations remain eligible for application.
These transition guarantees apply to managed processes. Section 6.4 defines the separate unmanaged-session policy.

Use the same acceptance policy for live processing and full compute.
A queued result computed before a decision must not apply its old title after that decision.

Do not hold the shared database write lock across provider I/O or a terminal wait.

### 7.2 Per-session publication ordering

At most one title publication may control a given session at a time.
A new decision may supersede a queued publication. A running publication must finish or resolve before its successor takes control.

An obsolete completion cannot clear the current protection or publish an obsolete correction.
When publication finishes, synchronize the latest accepted decision if it has changed.
Do not use title-text equality as the sole identity check for concurrent work.

The implementation may reuse existing timeline event identities where sufficient.
No new persistent revision field is required by this specification.

### 7.3 Releasing protection

Acknowledgement alone does not make already queued transcript observations current.
Conversely, reading one matching title does not prove that an asynchronous hybrid command has completed.

Release transition protection only when publication is confirmed and earlier observations cannot undo it.
Do not keep a blanket rejection of differing titles for the entire process lifetime.

The implementation plan must explain how it establishes this boundary for each publication route.
The specification requires the outcome, not a particular offset, token, or queue layout.

### 7.4 Corrections

Only a newer accepted decision may justify publishing a replacement for an in-flight title.
A differing database value alone is not evidence of such a decision.

Transcript application must not trigger corrective JSONL appends behind an active managed process.
Shutdown must not re-append a previously protected title after a later accepted rename.

## 8. Voluntary and external renames

Explicit UI, CLI, and MCP title requests remain accepted, including any previously used title.
A new external `/rename` remains accepted when the session runs outside TwiCC or in hybrid mode.

Do not reject a title because it appears in history, whether immediately preceding or older.
Do not infer human intent merely from a `custom-title` entry or the timeline source `agent`.

After a completed TwiCC publication, a later hybrid `/rename` must update the stored title.
That rename must survive normal process shutdown and later transcript recompute.

Concurrent terminal commands use the CLI's effective command order.
A manual command applied after a TwiCC command must remain the final title unless another decision follows.
Pending TwiCC work must not replay an already completed decision over that later command.

The implementation must demonstrate that transition protection does not discard a later manual command.
Where transcript metadata cannot identify intent, use ordering evidence rather than a value blacklist.

## 9. Failure and recovery

An accepted database title remains visible when provider synchronization fails.
Log the failure with the session, publication route, and completion state.
Do not report successful synchronization on a timeout or unconfirmed paste.

Recovery must use the latest accepted decision. Cancel obsolete work when its process or session changes.
Retries and background tasks must be bounded and must not keep the backend or terminal handler blocked indefinitely.
The implementation plan chooses the retry budget and cleanup mechanism.

During a live backend lifecycle, retain enough pending state to reconcile at a usable channel or a safe process-stop boundary.
Do not keep restoring an earlier title after a newer explicit request.

A hard backend crash between database acceptance and provider confirmation can leave the two stores inconsistent.
This specification does not promise crash-atomic delivery or replay of an in-memory pending publication after restart.
Normal import must still accept external changes made while TwiCC was stopped.
Do not add a boot-time rule that blindly overwrites such changes with a pre-crash database title.

## 10. Timeline and compatibility

Record actual accepted title changes through the existing timeline service.
Keep current first-title, subagent, actor, source, and request-origin conventions.

Publication, acknowledgement, retry, and echo do not create duplicate `title.changed` events.
A rejected stale observation creates no title-change event.
The timeline source `agent` remains a provider-channel label, not proof of a model or human decision.

Do not change Codex behavior through a shared compute refactor.
Preserve existing automatic-title compare-and-set and user-title freezing rules.

## 11. Acceptance scenarios

| ID | Scenario | Required outcome |
|---|---|---|
| A1 | Automatic rename during an SDK response | DB and CLI adopt the new title; response continues. |
| A2 | Explicit TwiCC rename while SDK is idle | CLI acknowledges the correct session; title persists. |
| A3 | CLI persists metadata after A1 or A2 | It writes the new internal title, not its former title. |
| A4 | Old live transcript work completes after a new decision | It cannot restore the preceding title. |
| A5 | Old full-compute work completes after a new decision | It cannot restore an earlier title. |
| A6 | A queued observation arrives after acknowledgement | Protection cannot disappear before that observation is made harmless. |
| A7 | Automatic decision is followed by an explicit user decision during publication | Latest accepted user choice wins; automatic work cannot restore its result. |
| A8 | Explicit A → B → A requests overlap publication | Final A represents the new decision; obsolete completions cannot alter it. |
| A9 | Hybrid composer stays busy | Request stays asynchronous and bounded; no direct JSONL fallback or false success. |
| A10 | Hybrid `/rename` completes, then user manually renames to any older title | Manual title is adopted and survives shutdown and recompute. |
| A11 | Manual hybrid command applies after a TwiCC command during transition cleanup | Its observation is not discarded as a stale value. |
| A12 | New title arrives while an older hybrid auto-paste waits | Obsolete queued title cannot execute after its replacement completes. |
| A13 | Channel rejects a wrong session identity | No other session changes; publication remains failed. |
| A14 | Process exits, restarts, or changes before confirmation | Old completion cannot affect the new session/process. |
| A15 | Provider command times out, with uncertain delivery | DB choice remains; no unsafe fallback; recovery uses the current decision. |
| A16 | Session is inactive and no external process writes it | File-based rename survives the next managed resume. |
| A17 | External terminal performs a voluntary rename | Normal transcript import accepts it, including a previous title; no unmanaged-session protection blocks it. |
| A18 | Echoes and retries repeat the current title | No duplicate title-change events and no origin changes. |
| A19 | Normal stop follows a later accepted manual rename | Shutdown does not restore an obsolete protected title. |
| A20 | Existing Codex and automatic-title behavior | No change to provider semantics, cadence, eligibility, or user-title freezing. |
| A21 | TwiCC renames a session whose process is unmanaged or unknown | File write is best effort; later external title entries remain importable; no false live confirmation or indefinite protection. |
| A22 | New title contains repeated internal whitespace or printable Unicode | All publication routes agree on the canonical title; accents and punctuation remain intact. |
| A23 | Accepted title contains 101 to 200 characters | Publication and managed resume preserve its full canonical text, including hybrid mode. |

Deterministic tests must exercise delayed reads, delayed acknowledgements, and superseded work.
Live compatibility checks must verify internal CLI state through subsequent metadata persistence and resume.
An acknowledgement-only test is insufficient.

## 12. SDK update requirement

Add this check to the maintained Claude Agent SDK update instructions and diagnostic workflow.
The private API dependency is explicitly accepted, but compatibility must be tested for each SDK update.

Verify:

1. The Python control-channel entry point still exists and returns responses correctly.
2. `rename_session` accepts the expected title, `source`, and target session identity.
3. Renaming works idle and during streaming without interrupting the response.
4. Metadata re-append and resume preserve the new title.
5. A wrong session identity is rejected.
6. Voluntary reuse of an old title remains accepted.

These checks supplement the existing required hermetic LLM diagnostic; they do not replace it.
Record the tested SDK and bundled CLI versions.
An SDK update must not ship with a failed or inconclusive title-control compatibility check.

## 13. Implementation-plan responsibilities

After user approval, the implementation plan must identify:

- The compatibility adapter and its error contract.
- The shared title-application policy and all writers affected by it.
- The ordering and confirmation evidence for SDK and hybrid paths.
- Lifecycle cleanup, bounded retry behavior, and unchanged crash-recovery limits.
- Tests for the acceptance scenarios and integration with SDK update checks.

Do not implement this draft or introduce a migration as part of specification review.

## 14. Review record

### Round 1 — revision 1

Two independent internal reviewers receive specification-level review instructions.
They must challenge behavior, scope, and acceptance criteria, not prescribe implementation details.

- Ordering reviewer: approved; no material specification issue.
- Scope reviewer: found an undefined priority policy for unmanaged active sessions (P1).
- Scope reviewer: found an undefined normalization contract across publication and hybrid launch (P2).

Revision 2 resolves both findings in sections 6.4 and 6.5 and scenarios A21–A23.
The unmanaged best-effort boundary and canonicalization rules are author proposals for user approval.
They do not imply that the user previously selected these details.

### Round 2 — revision 2

- The scope reviewer rechecks P1 and P2 and approves both resolutions.
- A fresh independent reviewer challenges the revised specification and approves it without material findings.

Review outcome: two rounds, three internal reviewers, two material findings resolved, no remaining material findings.
User approval is still required. No product code is implemented by this review.
