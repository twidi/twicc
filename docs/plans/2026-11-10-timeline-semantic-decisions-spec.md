# Timeline message and shell decisions

Date: 2026-11-10
Updated: 2026-11-11
Status: Approved race policies, Claude-only message analysis, validated binary prompts and contexts, DEBUG decision logging

Review: Two independent internal reviewers passed the simplified design after the delivery-uncertainty bound was added.
The later Claude-only message restriction is a user-requested scope correction.
The prompt/context and logging amendments follow subsequent user decisions; they do not claim a new adversarial review.

## Purpose

Filter eligible Claude assistant messages for useful timeline publication.
Confirm real logical-turn completion before switching from `assistant_turn` to `user_turn`.
Keep one logical assistant turn while temporary background work still blocks delivery.
Keep provider facts separate from user-facing completion decisions.

This specification defines behavior and consistency requirements.
Reuse the existing completion controller, logical turns, and immutable input-to-turn associations.
Do not add an observation ledger, a turn-reconstruction reducer, or deferred input ownership.
The implementation plan defines decision storage, prompts, and scheduling.

## Scope

Add exactly two semantic checks:

1. Check Claude assistant messages that already pass the existing timeline eligibility filter.
2. Check active background shells when the existing completion mechanism would confirm a normal turn ending.

The message check controls timeline publication only.
Codex assistant messages keep the existing publication path, without semantic analysis or waiting for a decision.
The shell check applies to both Claude and Codex.
The shell check controls general logical-turn completion, including process state, completion notifications, and existing completion consumers.
Apply it in the existing completion mechanism, before confirming the ending or switching to `user_turn`.
The timeline ending follows that shared decision; it is not a separate timeline-only filter.

A confirmed normal ending forces publication of its last eligible assistant message.
This rule overrides an earlier semantic rejection of that message.

Do not modify TTS, its queues, or its reading policy.
Do not add user settings in this phase.
Do not classify every assistant message, subagent, workflow, monitor, or goal.
Keep their existing eligibility and completion rules.
Do not change provider transcripts or provider-side final markers.
Do not reclassify historical timeline events or remove published messages.

## Existing behavior

- `agent/completion.py` separates provider endings from logical completion.
- Its existing stabilization delay is one second.
- Existing holds prevent completion while certain assistant work remains active.
- Background shells do not currently hold normal completion.
- `core/services/timeline_messages.py` selects eligible messages and writes timeline events.
- Indexed, verified, and recovery paths can all reach message capture.
- `agent/base_manager.py` publishes completion and updates the current process state.
- `agent/timeline_hooks.py` derives turn events from process transitions, using publication-time timestamps.
- `core/services/timeline_feed.py` discovers new events by ID and orders history by `occurred_at` and ID.

The message check must cover every applicable message-publication path.
The shell check must govern normal logical completion and its downstream effects, not only timeline writes.
No recovery path may bypass an existing semantic rejection.

## Terminology

| Term | Meaning |
|---|---|
| Eligible message | A complete assistant message accepted by the existing timeline filter |
| Message candidate | An eligible Claude message awaiting a semantic publication decision |
| Ending candidate | A normal ending eligible under the existing completion rules and stabilization delay |
| Blocking shell | A shell whose result remains necessary for the assistant's current work |
| Independent shell | A shell intentionally allowed to continue after the assistant returns control |
| Confirmed ending | An ending accepted by the existing rules and the additional shell check, when required |
| Occurrence time | The original time of the message or eligible turn boundary |
| Decision time | The later time when analysis or fallback resolves a candidate |

## Message decisions

### Eligibility stays unchanged

Run the existing filter first.
Only eligible Claude assistant messages enter the new decision path.
Codex eligible messages publish through the current path, without creating message decisions or model calls.
User messages keep their existing publication behavior.

Preserve complete message grouping and exact provider references.
Do not classify partial content as a complete candidate.

### Publication rule

The classifier answers: "Does this eligible message contain information worth surfacing now?"

- Publish a useful answer, finding, warning, or request requiring the user's attention.
- Suppress a routine progress statement with no useful answer or action for the user.
- Do not equate continued assistant work with an unimportant message.
- An answer to a user question received during work can deserve publication before completion.

Context contains the complete candidate and up to four immediately preceding normalized conversation entries.
Preserve user/assistant roles and existing automatic notices tagged `system_notice`, as in the validated requests.
These notices count toward the four entries but are not actual user requests.
Include the latest actual user request separately when it is outside that window.
Include available user input received during work, even when absent from ordinary transcript user messages.
Merge captured ordinary inputs with transcript context before selecting that same bounded window; do not append another conversation history.
Do not invent missing input or infer that its absence proves an unsolicited progress report.
The plan fixes the exact input fields, selection, size limits, and prompts.
Do not expand or rewrite that validated contract during implementation.

### Decision precedence

| Condition | Result |
|---|---|
| Analysis approves publication | Publish the message |
| Analysis rejects publication | Retain the rejected candidate without a timeline event |
| Analysis fails or remains inconclusive | Publish under the existing behavior |
| Confirmed ending identifies this candidate as its last eligible message | Publish regardless of analysis |

A forced publication wins over pending or later analysis results.
A message appears at most once, including races between analysis, ending confirmation, indexing, and recovery.

Retain rejected candidates while their turn can still require forced publication.
Never select the session's latest message at decision time as a substitute for exact turn association.
If final source indexing arrives late, retain the publication obligation until the exact eligible message becomes available.
Do not manufacture a message when the existing filter finds no eligible message.

### Publish only after a decision

An unresolved or rejected candidate is not a `TimelineEvent`.
It must not reach timeline feeds or trigger timeline publication signals.
Do not insert and later remove an assistant message.

Candidates for independent messages can resolve independently.
Unrelated sessions must not wait for one session's analysis.

## Shell decisions

### Trigger

Keep the existing completion conditions and stabilization delay.
At the point where those rules permit normal completion, capture the ending candidate.

- No active background shell: confirm without semantic analysis.
- Active background shells: analyze their role before confirming.

During unresolved shell analysis, keep the public process state in `assistant_turn`.
Do not publish `turn.ended` or broadcast an idle transition for that candidate.
Do not block provider events, user input, source indexing, or independent message decisions.

### Classification

Classify all captured active shells together in one call.
Context contains the latest actual user request, the latest complete assistant response, and every active shell.
Each shell includes its identity, owner, full command, description, start time, and available source line.
Include up to two related completed tool observations per shell, plus the last two completed observations.
Use the exact selection and excerpt rules fixed in the plan.
Do not add owner transcripts, output-file reads, tool execution, timeout analysis, or further context without separate approval.

Classify purpose, not predicted duration.
A finite test suite can block completion for minutes.
A persistent server can remain active after a valid delivery.

- `YES`: every captured shell can remain active after handoff; confirm the ending candidate.
- `NO`: reject the ending candidate. One temporary or unfinished shell is sufficient.
- A ready service intentionally left available can allow handoff. Startup or readiness checks still pending cannot.
- An invalid response establishes no trusted classification and invokes failure behavior.

The response is one aggregate YES/NO, not a per-shell JSON classification or an `uncertain` verdict.
A model reason explains the response; it does not determine another process-state outcome.

A rejected ending does not force publication of its last message candidate.
That message keeps its separate semantic decision.
The assistant's automatic continuation belongs to the same logical turn.

### Shell changes and continuation

A decision describes the captured ending and captured shell identities.
A new shell or new provider ending must not inherit approval from an unrelated candidate.
Do not apply an old classification as the current status of a changed shell set.

When blocking work completes, let the existing provider continuation and completion rules run.
Do not close the logical turn merely because a shell completion notification arrives before the assistant resumes.
If no continuation follows, reevaluate completion after completion delivery settles and the existing stabilization delay passes.
Bound delivery uncertainty after blocking work completes.
If decisive continuation evidence remains unavailable, apply fail-open behavior and reevaluate completion with a fresh eligibility timestamp.
Such reevaluation uses the new eligibility time, not the timestamp of a previously rejected ending.
Do not start a new assistant task solely to obtain a cleaner final message.

Existing exceptional termination and user-input request rules remain authoritative.
This feature must not keep a stopped process visibly working or delay a real permission request.

## Delayed decisions and accepted race policies

Capture candidate identity, occurrence time, source scope, references, and bounded decision context before asynchronous analysis.
Keep pending decisions outside the public timeline.
Persist enough candidate state for restart fallback and exact final-message forcing.
Do not wait for inference while holding writer admission or handling provider events.

The user accepts these two policies to retain the current turn model.
Whichever event claims the pending ending first determines its outcome.
Late inference cannot reverse that outcome.

### Policy 1: user input while shell analysis is pending

Use the existing outgoing input path, before it associates an input with a logical turn.
Apply this policy only to explicitly user-origin ordinary messages admitted through that path.
Shell notifications, subagent messages, agent-origin inputs, and commands without ordinary input admission do not trigger it.
Do not wait for the provider to record a transcript message with role `user`.

If the current turn has a stabilized ending with shell analysis still pending:

1. Cancel that analysis and accept the captured ending through fail-open behavior.
2. Close the old turn before associating the new input.
3. Force the old turn's exact last eligible assistant message.
4. Let the new input use the existing new-turn path.

Preserve the original ending time, even if timeline publication occurs later.
Keep any newly active assistant state; do not broadcast an obsolete idle transition or completion notification.
If provider delivery later fails, do not undo the accepted old ending.
The existing delivery-error behavior handles the new input.

This policy does not apply after analysis has already rejected the ending.
The existing open-turn rules then determine input ownership.
It also does not apply after automatic root continuation has canceled that ending.

**Accepted cost:** a new user message can accept a shell-backed ending that analysis might otherwise reject.
The new message does not prove that the earlier work was complete.
This explicit fallback avoids changing an immutable input association afterward.

### Policy 2: automatic root continuation while analysis is pending

When actual root work resumes before the ending decision resolves:

1. Cancel the pending ending candidate and its analysis.
2. Keep the existing logical turn open.
3. Ignore any later verdict for that canceled candidate.

Use existing root-activity detection.
Subagent output and shell status notifications alone do not count as root continuation.
Do not force a message for the canceled ending.
Eligible messages keep their independent usefulness decisions.
A subsequent provider ending creates a fresh candidate under existing completion rules.

**Accepted cost:** an independently useful earlier ending can be merged with automatic continuation.
There is no retrospective split after root work resumes.

### Ordering example

| Time | Observation | Required behavior |
|---|---|---|
| 10:55:00 | Stabilized ending with an active shell | Keep analysis pending and remain in `assistant_turn` |
| 10:55:04 | User-origin message enters the send path first | Accept the old ending at 10:55:00; associate new input with new work |
| 10:55:10 | Old analysis returns | Ignore its verdict |

If actual root continuation arrives first at 10:55:03, cancel the old candidate instead.
The later user input then follows existing open-turn rules.
No old verdict can introduce a boundary between those observations.

For either order, capture facts before awaits.
Do not reconstruct turns or move existing input associations.

## Timestamps and publication consistency

Use the original source timestamp for published assistant messages.
Use the captured eligible boundary timestamp for accepted endings.
Capture start timestamps when the corresponding activity occurs.
Do not replace these timestamps with analysis completion or database insertion time.

Keep captured background-work facts on historical endings.
Do not replace them with the session's background state at decision time.

Commit related accepted boundaries and forced publication obligations consistently.
Use existing timeline recording services and deduplication guarantees.
Publication signals occur only after successful event commit.
Retries must not duplicate messages or boundaries.

## Failure policy

Prefer the existing behavior over silently losing useful information.

- Message failure: publish the eligible candidate.
- Shell failure: accept the ending candidate.
- Apply normal forced-message publication when fallback accepts an ending.
- Never let fallback overwrite newer live assistant activity.

Once an ending decision resolves, a late model response cannot change that decision.
In particular, timeout acceptance cannot later become rejection or reopen the closed logical turn.
A message published through approval, fallback, or forced publication cannot later be withdrawn by classification.
Forced publication remains the only semantic override of a resolved message rejection.

Failures include timeout, unavailable providers, exhausted provider fallback, malformed output, and incomplete decisive context.
Use a bounded total decision deadline, including queueing, model preparation, retries, and provider fallback.
Do not copy the title generation retry duration without evaluating its effect on this deadline.
The exact budget belongs in the implementation plan.

After restart, recover pending candidates or apply this same fallback.
Source replacement, history invalidation, deletion, or unavailable history must still respect existing source eligibility rules.
Failure fallback cannot publish stale or unavailable source content.

Keep enough decision provenance to distinguish semantic decisions, forced publication, and failure fallback.
Record the requested route and effective route for diagnosis.

## Classifier contract and decision logging

Use the two exact system prompts and JSON input contracts in the implementation plan.
Output YES/NO on the first non-empty line and request a short reason on the next line.
No JSON output, third verdict, tool call, or conversation continuation is required.
Keep semantic instructions fixed across cases and routes. Evidence belongs in the separate input payload.

Log every model decision at DEBUG through existing TwiCC logging.
Include session/decision IDs, decision kind, source scope, message references or shell references, model, routes, verdict, and reason.
Shell references must distinguish their owner and captured generation.
Log fallback, forced publication, cancellation, and ignored late results so the applied action remains identifiable.
Do not log candidate text, commands, shell output, prompts, or the input payload.
Keep the model reason bounded and on one log line.
Use existing log levels and retention; add no setting, log file, audit table, or diagnostic worker.
`TWICC_DEBUG` already controls the TwiCC logger level. Do not enable DEBUG automatically.

The user approves removing benchmark wording and requiring the second-line reason without another live replay.
This exception does not authorize other prompt or context changes.
Any later semantic prompt or context change requires user approval and a full retained-corpus replay unless explicitly waived.

## Model selection

Use the title selection policy in `core/services/title_suggestion.py` as the routing reference.
Reuse its availability and provider fallback semantics.
Use classification-specific prompts and response validation, rather than title generation validation.

Support these internal route choices:

| Choice | Requested route |
|---|---|
| `provider` | The session provider's small model: Claude Haiku or GPT Luna |
| `haiku` | Claude Haiku |
| `luna` | GPT Luna |
| `openrouter` | The existing OpenRouter text model |

There is no separate forced Claude choice.
The default is `provider`, independent of the existing title model setting.
Do not use the session's main working model as the classifier.
Both semantic checks use the same routing policy.

Match title routing behavior:

- Skip disabled or unavailable agent providers.
- Try another available title-capable provider after a provider failure.
- OpenRouter requires its existing configured key, but no running agent provider.
- OpenRouter without a key resolves as `provider`.
- OpenRouter failure falls back to available agent providers, starting with the session provider.
- If no route succeeds within the decision deadline, apply the failure policy.

Selection must be ready for a future user setting.
This phase exposes no setting, UI selector, or public configuration API for that selection.
Classification calls must not create visible sessions, transcript activity, or recursive classification candidates.

## Acceptance cases

| Case | Required result |
|---|---|
| Assistant message fails the existing eligibility filter | No new analysis or publication |
| Eligible Codex assistant message | Existing publication behavior; no message analysis or decision delay |
| Eligible Claude routine progress message during work | Suppressed unless later forced by its confirmed ending |
| Useful Claude answer during ongoing work | Published without waiting for the ending |
| Normal ending without shells | Accepted without shell analysis; last eligible message forced |
| Ending with a test suite required for delivery | Rejected; same logical turn continues |
| Ending with a delivered persistent server | Accepted; background remains visible |
| Independent server plus required tests | Rejected while required work blocks delivery |
| Established blocking tests plus an uncertain shell | Rejected; the established blocker takes priority |
| Shell analysis pending while a useful answer resolves | Message can publish independently |
| Message rejection races with accepted ending | Forced publication wins, exactly once |
| Message indexing follows accepted ending | Exact final eligible message publishes when available |
| User-origin message arrives during pending shell analysis | Cancel analysis; accept captured ending before input association; new turn |
| User-origin message arrives after rejection or root cancellation | Existing open-turn rules; no forced boundary |
| Automatic root work resumes during pending analysis | Cancel candidate; keep the same turn; ignore late approval |
| Shell notice or subagent output arrives during analysis | Do not invoke the user-input shortcut |
| Automatic continuation follows rejected ending | No extra turn boundary |
| Analysis fails or reaches its deadline | Existing behavior, with original occurrence times |
| DEBUG classification log | References, route/model, YES/NO and reason; no candidate or shell payload |
| Final forcing or fallback overrides analysis | Separate diagnostic outcome; preserve the original model verdict |
| Timeout accepts an ending; late analysis reports blocking work | Keep the accepted boundary; ignore the late decision |
| Backend restarts with pending candidates | Recovery or bounded fallback; no duplicates |
| Provider is disabled | Title-equivalent fallback; no disabled provider launch |

Reference session: `57f9168d-e4b8-4f9c-a834-1a8e6afc8874`, "Test orphané et pytest timeout".
Times below use Europe/Paris on 2026-11-09.

- Suppress the 10:39 progress message before "C'est corrigé, sans commit".
- Publish the 10:40 final report.
- Treat work from 10:55 through 11:09 as one logical turn.
- Reject shell-backed endings at 10:56, 11:01, 11:03, and 11:08.
- Suppress their routine progress messages.
- Publish the 11:09 report and accept its shell-free ending.

## Deferred implementation choices

Prompts and context selection are fixed by the completed OpenRouter evaluation and the subsequent user-approved wording change.
The plan records 23 real sessions, 28 simulations, four unscored diagnostics, and the remaining final-acknowledgement mismatch.
Do not present these results as Luna/Haiku comparison or production-state validation.
Any future provider comparison must use these same prompts and input contracts.

The plan fixes persistence layout, scheduling, concurrency, prompt text, context limits, and timeout values.
Keep existing logical-turn and input models unchanged.
No historical recomputation or `compute_version` bump is required.
Use durable decision storage for new live candidates only.
The plan may add one decision table through a schema migration; it must not add an observation ledger.
These choices must satisfy this specification without adding new user-facing classification scope.
