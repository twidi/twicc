# Confirmed turn completion specification

## Purpose

TwiCC must announce completion when the assistant finishes its current work interval.
One work interval can contain several provider turns and several messages marked `final`.
An intermediate provider result must not produce a completion notification or release a waiting caller.

This specification covers Claude Code and Codex sessions managed by TwiCC.
The change is prospective. It does not rewrite historical transcripts or timeline events.
Evidence and timing measurements are in [the analysis](2026-11-02-turn-completion-analysis.md).

## User outcomes

- Immediate automatic continuations keep the session in `assistant_turn`.
- The session does not briefly become unread between those continuations.
- One logical work interval produces one `turn.started` and one `turn.ended`.
- CLI and MCP callers receive the answer associated with confirmed completion.
- Existing background shell behavior remains available.
- User input, pending questions, interruptions, and errors remain responsive.

## Terms

| Term | Meaning |
| --- | --- |
| Provider turn | One root turn reported by the Claude or Codex SDK. |
| Logical work interval | The assistant work represented by one public `assistant_turn` interval in TwiCC. |
| Root activity | The main assistant starts or continues a request, tool interaction, or response. |
| Assistant hold | Existing work that keeps the public assistant state active after a provider turn ends. |
| Completion candidate | A possible logical completion awaiting one second of stability. |
| Confirmed completion | The end of a logical work interval after its completion conditions hold. |

Actual SDK activity and public state remain separate facts.
A public `assistant_turn` does not imply that an SDK turn currently exists.

## Chosen approach

Use **early provider activity signals plus a one-second confirmation interval**.
Apply one confirmed completion decision to all completion consumers.
Remove timeline boundaries caused only by background metadata changes.

| Alternative | Trade-off |
| --- | --- |
| Delay alone | First tokens can arrive after the delay, despite an already active root request. |
| Early signals alone | A result can precede the next request signal, leaving a short false completion window. |
| Early signals plus one second | Bridges immediate chaining and recognizes active requests before their first tokens. |

One second is the initial product decision.
It adds approximately one second to ordinary completion reporting.
It does not delay streamed messages or dispatch of user input.
No new user setting is required by this specification.

## Completion rules

### Start and preserve a logical work interval

Real assistant work starts a logical interval when none exists.
Automatic provider turns inside an existing interval do not create additional public turn boundaries.
Messages can appear immediately, including messages marked `final` by the provider.

Their display does not establish logical completion.

Preserve the existing assistant holds for native subagents, Monitors, scheduled wakeups, and goals.
An independent TwiCC child session does not become a hold merely because it exists.
Preserve the current distinction between native work and independently managed sessions.

### Consider completion

A normal provider turn end makes completion eligible only when all these conditions hold:

1. No root request or root tool interaction remains active.
2. No existing assistant hold remains active.
3. No pending request needs user input.

The last assistant hold ending can also make completion eligible after an earlier provider result.
Eligibility starts a one-second completion candidate.
The public state stays `assistant_turn` during this interval.
Do not emit `turn.ended`, mark completion unread, or release default `wait-reply` during this interval.

Starting another candidate cannot close the same logical interval twice.
Background metadata alone neither starts a logical interval nor creates another completion.

### Cancel a candidate on real activity

Any real root activity observed before confirmation cancels the candidate.
Activity observed concurrently with confirmation must prevent a false completion.
The assistant remains active until that root activity reaches its terminal outcome.
Silence after a known request start cannot establish completion.

For Claude, recognize `SystemMessage` with subtype `status` and status `requesting` as an early root activity signal.
For Codex, preserve the existing root `turn/started` signal and actual SDK turn tracking.
Continue recognizing real root conversation and tool traffic for both providers.
Child traffic must not count as root traffic when the provider identifies its child scope.

Do not treat every system event as activity.
Settings acknowledgments, hook metadata, command catalog changes, and unrelated status updates do not cancel completion.

A new assistant hold also cancels the candidate.
When that work later ends, eligibility starts a new full one-second interval.

### Confirm once

Confirm completion when the full interval expires and the eligibility conditions still hold.
The confirmation belongs to the same process and logical interval that became eligible.
An obsolete candidate cannot close later work or a replacement process.

The confirmation determines the public transition to `user_turn` and its single `turn.ended` event.
Unread state and completion notifications use that decision.
Existing read and notification preferences continue to apply.

All completion consumers must agree about whether the logical interval has ended.
Transport delivery and transcript indexing can finish at different times.
That lag must not cause a consumer to claim an unconfirmed completion or return an earlier intermediate answer.

### Background shells

A background shell alone does not block completion or extend the one-second interval.
Confirmed completion can enter `user_turn` while a shell continues running.
The existing terminal indicator represents this case.

A shell starting, ending, or changing metadata during `user_turn` does not emit another `turn.ended`.
Keep shell information current without creating another logical turn boundary.
If the root actually resumes after a shell notification, root activity follows the normal rules.

## Input and exceptional outcomes

### Input during the confirmation interval

Accept input immediately. Do not wait for the completion interval.
The input cancels the candidate and continues the current logical work interval.
Waiters attached to that interval observe its eventual confirmed completion.

Dispatch uses actual SDK activity, independently of the public `assistant_turn` state.
Codex uses `steer` only when an actual turn supports it.
If the actual turn has ended, use the existing fresh-turn path.

An input after confirmed completion starts a new logical interval.
An older confirmed answer remains associated with its original interval.
A later answer must not replace it for a caller waiting for that older interval.

### Pending user action

A permission request or question cancels a candidate and exposes the existing awaiting-input state immediately.
Default `wait-reply` returns the existing `awaiting_user_input` outcome for that pending action.
An intermediate `final` does not override the pending action.
Handling the pending action continues the existing work according to current provider behavior.

### Interruptions and failures

User interruption, explicit stop, process death, and terminal provider errors bypass the normal confirmation delay.
Preserve their existing distinct outcomes. Do not report an intermediate answer as successful completion of the interrupted interval.
Retryable errors do not terminate work while the provider continues its retry.
Stopping an already completed interval does not produce a second `turn.ended`.

A backend restart or reconnect must not activate an obsolete completion candidate.
Keep existing shutdown and recovery behavior outside this completion change.

## CLI and MCP waiting contract

### Default waiting

Default `wait-reply` succeeds only for a confirmed logical completion with an eligible final answer.
A provider `final` alone is insufficient while its logical interval remains active.
This rule applies to CLI commands, MCP tools, and the MCP event monitor that uses the same waiting behavior.

Return the **last relevant root final within the awaited logical interval**.
Do not return the first intermediate final or a final from a later independent interval.
If several completed intervals match a cursor, preserve their order and select the earliest eligible interval.

Keep the existing strict cursor boundaries, timeout handling, and response outcome names.
An answer at or before the caller's cursor is not eligible.
A normally completed interval without an eligible answer returns `ended`.
For an explicitly associated send, the reply belongs to the interval containing that accepted input.

If the last native child stops without a new root response, an earlier eligible final can be the concluding answer.
Do not require another root final after every child stop.
Do not require one matching root result for every child notification.

If the concluding answer is not indexed yet, keep waiting within the caller's timeout.
Do not substitute an indexed intermediate answer.
Once an interval has completed, a new interval cannot invalidate its answer for an existing waiter.

### Explicit background waiting

`--wait-background` adds its existing explicit background-waiting contract, including shells.
It is not enabled by default and does not define ordinary logical completion.

This option can ignore a final because background work remains.
It can then await an answer in a later logical interval.
The default restriction to the original interval does not prevent this explicit continuation.
For example, a shell can finish after interval A, causing a root response in interval B.
That response can satisfy the explicit background wait after B reaches confirmed completion.

An ignored final remains ignored for that wait when the background work ends without another answer.
Preserve the existing timeout, ignored-final reporting, and resumable cursor behavior for this case.
Pending user action and terminal outcomes remain immediate.

### Historical fallback and documentation

Sessions without information about logical completion retain the existing transcript-based fallback.
This includes historical or imported sessions for which TwiCC does not observe the new completion contract.
Do not invent retrospective logical intervals from old `final` markers.

Stopping a managed process does not remove information about its completed or interrupted logical intervals.
A later waiter still uses those known outcomes and associated answers.
The absence of a live process must not turn an intermediate final into a successful reply.

Update CLI help, MCP descriptions, and relevant skill documentation to describe the changed default completion behavior.

## Timeline contract

Record one start and one end per logical assistant interval.
Immediate automatic continuations do not add public start/end pairs.
Metadata-only changes do not create turn boundaries.
Terminal lifecycle events can close an open interval once, using existing outcome semantics.

This change does not require a new background-history event type.
Historical events and provider transcript markers remain unchanged.

## Acceptance scenarios

| Scenario | Required result |
| --- | --- |
| Ordinary final, no work remains | Keep the assistant state for one second. Then confirm one completion. |
| Claude final, `requesting` after 20 ms, first tokens after three seconds | Keep one logical interval. Do not become unread or release a waiter at one second. |
| Codex turn completes, another root turn starts during the interval | Cancel completion and keep one logical interval. |
| Root request starts but remains silent beyond one second | Keep the assistant state until a terminal outcome. |
| Parent final with a native child still working | Preserve the existing assistant hold. Default waiting remains open. |
| Last child stops, then the root resumes during the interval | Cancel completion and wait for the root's later result. |
| Last child stops without another root answer | Confirm after one second and use the earlier eligible root final. |
| Only a background shell remains | Confirm after one second. Show the existing terminal indicator. |
| Shell metadata changes after completion | Refresh the indicator without another `turn.ended`. |
| Explicit background wait ignores final A; shell completion later causes final B in another interval | Permit final B after its confirmed completion and the option's background conditions hold. |
| Explicit background wait ignores a final; background work ends without another answer | Preserve the explicit option's waiting and timeout behavior. |
| Repeated metadata or duplicate completion signals | Do not extend stability or complete the same interval twice. |
| Input arrives while completion is pending | Dispatch immediately, cancel completion, and preserve actual Codex routing. |
| A permission request follows an intermediate final | Expose awaiting input and return `awaiting_user_input`. |
| Stop or terminal error occurs during the interval | Return the corresponding terminal outcome without a successful intermediate reply. |
| Concluding answer reaches indexing late | Keep waiting for that answer. Never return the earlier intermediate final. |
| Another interval starts before an older caller reads its result | Return the older interval's associated answer. |
| A managed process stops before a later caller reads its result | Preserve the known completion or interruption outcome and its answer association. |
| A logical interval completes normally without an eligible final after the cursor | Return `ended` without reusing an excluded or intermediate answer. |
| Cursor excludes an earlier answer | Do not return that answer again. |
| Unrelated system events arrive after a final | Confirm normally if no actual work remains. |
| Obsolete candidate survives into later work or process replacement | It cannot publish a completion. |

## Limits and implementation freedom

No finite delay predicts an unrelated future input or an unannounced future request.
Real activity after a confirmed completion can start another logical interval, even without a human message.
The rule targets immediate continuation and preserves longer waits already explained by assistant holds.

The analysis confirms the early-signal gap on Claude and premature default waiting on both providers.
It does not establish the same unannounced autonomous-resumption pattern on Codex.
Both providers share the completion contract without requiring identical provider internals.

Implementation planning chooses storage, event transport, timer scheduling, synchronization, and transcript correlation.
This specification requires their observable guarantees without prescribing classes, database fields, or locking algorithms.
No SDK upgrade, historical backfill, new UI state, or general background-work redesign is required.

Validate the one-second value using cancellation and later-resumption measurements after implementation.
Separate external inputs, shell activity, and existing holds when interpreting those measurements.
