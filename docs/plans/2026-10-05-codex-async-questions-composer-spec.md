# Codex async questions in the composer

Status: adversarial review complete; awaiting user review. Implementation has not started.

## 1. Purpose

Show completed Codex async questions immediately when they arrive.
Let the user answer these questions and the final assistant message in one send.
Use the existing question widget style inside the composer, directly above the textarea.

The user approves this product direction on 2026-10-05:

- Reuse the current question widget style.
- Integrate questions into the composer instead of a separate footer block.
- Provide an `Other...` choice for free text.
- Make every question optional.
- Send question answers and the main textarea together.
- Write a spec before implementation.

The lifecycle and persistence rules below are proposed decisions for this review.
This document specifies observable behavior. The implementation plan selects storage, APIs, and component structure.

## 2. Observed protocol

Reference session: `01a10a1c-30e4-77f0-80ad-76d0c38e1e3f`.

| Evidence | First question | Second question |
|---|---|---|
| Tool call in JSONL | Line 5673 | Line 5706 |
| Canonical question message | Line 5674 | Line 5707 |
| Tool result | Line 5676 | Line 5709 |
| Later final assistant message | Line 5685 | Line 5720 |
| SDK reception, UTC | 2026-10-05 09:12:06 | 2026-10-05 09:19:55 |

Codex calls `request_user_input_async`. The tool returns `{"accepted":true}`.
The SDK emits an `agentMessage` through `item/started` and `item/completed` notifications.

The SDK item contains:

```json
{
  "type": "agentMessage",
  "id": "call_ttjW3C3wndpxnPwKZ5J55uGd",
  "delivery": "async",
  "phase": "final_answer",
  "text": "Question text and options",
  "questions": [
    {
      "title": "Should clicking a process state open its session?",
      "options": ["Yes, open the session", "No, show its state only"]
    }
  ]
}
```

The JSONL stores the equivalent under `event_msg.payload.item`, with type `AgentMessage`.
Its text lives in `content`, with blocks of type `Text`.
Its envelope carries `thread_id` and `turn_id`.

This question does not suspend the turn. Codex continues tools and emits another final answer.
`phase: "final_answer"` therefore does not establish completion.
The observed SDK log contains no `item/tool/requestUserInput` request.

Current TwiCC code treats these items as assistant text:

- `src/twicc/providers/codex/agent/agent.py`: agent message streaming.
- `src/twicc/providers/codex/compute.py`: assistant message classification.
- `frontend/src/providers/codex/canonical.js`: text extraction.

The existing blocking widget uses a separate pending-request contract.
Its reference component is `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue`.

## 3. Scope

Support structured async questions from the main Codex session.
Detect them from their message fields, regardless of the tool-call representation.
Do not extract questions from arbitrary assistant prose.

Keep the source assistant message in the conversation at its original position.
The composer provides a second, actionable presentation of its structured questions.

Preserve these existing behaviors:

- Blocking Codex `request_user_input` and Claude Code `AskUserQuestion` widgets.
- Approval handling and pending-request locks.
- Process states, background work, unread state, and completion notifications.
- Attachments, settings, shortcuts, and send recovery in the composer.
- Ordinary sending through CLI and MCP.

Do not add an SDK response RPC for async questions.
Do not convert them into `PendingRequest` objects or `awaiting_user_input` process states.
Do not change `phase` in the source JSONL.

## 4. Detection and identity

Accept a completed main-thread message when all conditions hold:

1. Its item type is SDK `agentMessage` or canonical JSONL `AgentMessage`.
2. Its `delivery` equals `async`.
3. Its `questions` contains at least one valid question.
4. Its item ID is a nonempty string.

A valid question has a nonempty string `title`.
Options are an optional array of strings. Ignore malformed options and empty labels.
Preserve valid option order. Remove duplicate labels within one question.
A question without options uses a free-text answer field.

One message creates one question batch.
Batch identity is `(session_id, item_id)`.
Question identity is `(batch_id, original_question_index)`.
Use the original array index even when invalid entries are removed.

Do not deduplicate different messages by their question text.
The same wording can describe a new question in a later turn.
Repeated SDK notifications and JSONL ingestion must produce one batch.
Exclude provider subagent sessions and copied parent history in child rollouts.

## 5. Readiness and lifecycle

### 5.1 While the agent works

Show batches immediately when completed question messages arrive.
Completed batches are ready during main turns, subagent activity, and goal continuations.
Do not interrupt the turn or steal focus from the user's draft.
Do not show a turn-readiness pending indicator.

### 5.2 Submission while the agent works

Answers can send alone or with textarea text whenever a normal message can send.
Use the existing Codex steering, retry, and fallback delivery pipeline.
Do not create a second send mechanism.

A blocking request retains its normal priority and send lock.
Async questions must not bypass that lock or create a second blocking request panel.
When the blocking request clears, show the async questions in the composer.

SDK completion and canonical JSONL completion produce the same ready state.
Late older questions retire when an accepted human submission follows them in the source timeline.
Determine this from the source timeline, not from the time TwiCC discovers the question.

### 5.3 Stops and failures

Questions stay available after interrupts, failures, and backend restarts.
Normal send admission rules still apply during recovery.
Old stored collecting projections become immediately ready on read without a database migration.

### 5.4 Later turns

Ready questions stay ready until sent or dismissed.
A later automatic or agent-driven turn does not erase the user's choices.
New batches append in source order. Existing question controls keep stable identities.

An accepted ordinary human message retires batches that exist and are ready at its submission boundary.
This also applies to plain-text replies from another browser or CLI, and questions discovered late.
It means the user can answer naturally without using the cards.
Internal prompts, context updates, automatic continuations, and agent-to-agent messages do not retire questions.
Accepted steering messages retire eligible older batches at the immutable submission boundary.

Apply this same rule during live operation, reload, historical recovery, and recompute.
Completed messages are immediately ready, independent of the source turn lifecycle or process display state.
A question generated after the submission boundary remains unresolved.
Late ingestion of an older question must not make it reappear after a relevant accepted human reply.

## 6. Composer interaction

Render the question controls inside the composer, immediately above the main textarea.
Use the existing question text, option cards, radio indicators, spacing, colors, and selected states.

Do not embed the blocking widget unchanged. It owns a separate Submit button and requires all answers.
The async presentation uses the same style and question interactions, with the composer's Send action.

Required controls:

- Single selection for each question. No default selection.
- `Other...` for every question with options.
- An auto-growing free-text field when `Other...` is active.
- A direct free-text field for questions without options.
- `Clear answer` to return a selected question to its unanswered state.
- `Dismiss` for a whole batch, without sending a message.

Selecting an option or entering text never sends a message.
The composer has one Send action for answers, textarea text, and attachments.
The user can answer some questions, all questions, or none.
Empty or whitespace-only free-text answers count as unanswered.

Enable Send when any normal sendable content exists or at least one question has an answer.
An answer-only message is valid. An entirely empty message remains invalid.
Applying settings alone must not consume questions.

Expand ready questions when the composer is open.
If the user explicitly collapsed the composer, retain that preference and show its question count in the header.
Question arrival must not move keyboard focus.
Explicitly selecting `Other...` moves focus to that question's input.

Keep the existing option-card keyboard interaction and accessibility labels.
Enter or Space on an option selects it. It does not submit the composer.
The existing composer send shortcut sends the combined message from text inputs.
Blocking widgets retain their existing shortcuts.

Constrain the question area height and allow internal scrolling.
The textarea and Send controls must remain reachable on mobile and short desktop viewports.
Use the existing footer size and scrolling conventions.

## 7. Message construction and delivery

Each submission identifies the questions it answers and the immutable admission boundary for that send.
Validate that the answers belong to the target session and refer to unresolved, ready questions.
Use the original question text and option labels when constructing the ordinary user message.
Include only answered questions, in source order, followed by the main textarea text.
Deliver this combined content through the normal provider send path.

Generated headings are English. Source questions, source options, and user text retain their original language.

Example model-visible text:

```text
Answers to your questions:

Question: Should the tooltip retain the session action menu?
Answer: Yes, retain the menu.

Additional message:
Limit the tooltip width to 24rem.
```

Omit the answer section when there are no answers.
Omit `Additional message:` when the main textarea is empty.
Do not generate a refusal or answer for unanswered questions.
Send attachments through their existing path.

The conversation must display the same combined text that Codex receives.
Reconcile the optimistic message with the accepted message using the existing send recovery behavior.
Do not concatenate the answer section twice during retry or recovery.

Retire eligible batches only when the normal send pipeline confirms acceptance.
Do not retire questions generated after the submission boundary.
Questions ingested late follow the same source-timeline retirement rule as other questions.
If validation or sending fails, preserve the textarea, attachments, answers, and ready questions.
If delivery is uncertain, use existing send recovery before offering a retry.
This feature does not introduce a second delivery or retry mechanism.

Reject stale structured submissions explicitly. Do not silently send a different question or an empty answer.
Keep the user's draft available when another browser already retires the referenced batch.

### 7.1 Answers resolved by another client

If another client sends or dismisses a batch, remove its active question controls from this composer.
If local answers exist, move them into the main textarea as an ordinary question-and-answer text section.
Preserve the existing textarea text and attachments. Do not send or retry automatically.
Show a notice: `These questions were handled elsewhere. Your answers are kept in your draft.`

The user can edit or remove that section, then send the ordinary message without stale question references.
Preserve free-text answers and selected option labels with their original question text.
Unanswered questions create no recovered text.
Repeated updates or reloads must not insert the same recovered answers twice.
If a send is in flight, reconcile its acceptance before applying this recovery.
An accepted send clears its own answers normally. It must not recover those answers as an external resolution.

## 8. Durable state and synchronization

Questions must survive reloads, reconnects, backend restarts, and transcript pagination.
Do not discover them only from the currently loaded frontend messages.

Keep durable source identity, question content, source turn, completion, and resolution evidence.
Distinguish ready, sent, and dismissed batches.
This lifecycle state is outside the agent-settings bundle.

SDK reception, JSONL ingestion, and recompute must converge without duplicating batches.
Recompute preserves send and dismiss decisions. It must never recreate a resolved batch as ready.

For existing history, apply section 5.4 using the actual source order of completed question messages and human submissions.
Accepted human steering retires older completed questions, including after reload.
A human reply retires eligible older completed questions, including questions discovered later.
Internal and automatic messages never act as human replies.

All browsers receive current unresolved batches and their resolution changes.
Session switching, reconnects, and transcript pagination must not hide unresolved questions or revive resolved questions.
Use source order consistently even when SDK and watcher updates race.

Persist draft choices with stable session, batch, and question identities.
Choices remain local, like the main textarea draft. Lifecycle dispositions synchronize across browsers.
Clear local choices after their own accepted send or explicit dismissal.
For external resolution, recover local answers into the textarea before removing their question controls.
Preserve unresolved in-flight drafts until delivery recovery finishes.

Retain enough resolution evidence to prevent old questions from reappearing during the session's lifetime.
Delete this feature's session-specific durable state when the session is deleted.

## 9. Compatibility and boundaries

Honor `question_widget=False`: retain transcript text, but do not show async question controls.
Question ingestion can retain lifecycle data so a later setting change can reconcile unresolved questions.
Do not alter provider tool exposure as part of this feature.

Claude Code behavior is unchanged.
Public session shares show the original assistant messages and no interactive composer controls.
Async questions do not enter `pending-requests`, `answer-questions`, or `cancel-questions` CLI contracts.
They do not trigger blocking-question notifications or prevent session shutdown.

Existing attachment-only messages remain valid and retire eligible ready batches on acceptance under section 5.4.
Slash-command dispatch must remain explicit. Do not silently prepend answers to a slash command.
If answers are selected, require a normal message or clearing those answers before invoking a slash command.
A settings-only action and a command-only action do not retire question batches.

## 10. Implementation context

Implementation follows review of this spec and a separate implementation plan.
These existing files are references, not required implementation boundaries.
The plan selects models, payloads, synchronization mechanisms, and shared components.

| Area | Existing reference |
|---|---|
| Durable model and migration | `src/twicc/core/models.py` |
| SDK detection and real turn completion | `src/twicc/providers/codex/agent/agent.py` |
| Canonical detection and recovery | `src/twicc/providers/codex/compute.py`, `canonical.py`, `history_facts.py` |
| Send validation and acceptance | `src/twicc/providers/codex/ws.py`, shared send services |
| Session hydration and updates | Session serializers, WebSocket payloads, `frontend/src/stores/data.js` |
| Composer and send snapshot | `frontend/src/components/message/MessageInput.vue` |
| Shared question controls | `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue` |
| Shared visual styles | `frontend/src/styles/option-cards.css` |
| Draft and failure recovery | `frontend/src/utils/draftStorage.js`, `inflightStorage.js` |

## 11. Acceptance and verification

Use recorded protocol fixtures from the two observed questions, without live LLM calls.
Verify these behavior groups:

1. **Detection:** both observed messages produce one batch each. Ordinary assistant prose produces none.
2. **Deduplication:** SDK start/completion, watcher ingestion, reload, and recompute do not duplicate questions.
3. **Readiness:** completed async messages open questions immediately, before parent turn completion.
4. **Continuations:** automatic turns, goals, and subagent activity preserve ready questions and choices.
5. **Recovery:** late JSONL ingestion, historical sessions, interrupts, failures, and backend restarts reconcile correctly.
6. **Interaction:** no default choice; Other, clear, partial answers, dismiss, and answer-only sends work.
7. **Combined send:** selected answers, textarea, and attachments reach Codex through one ordinary send.
8. **Failure:** send rejection restores every draft part. Uncertain delivery does not duplicate answer text.
9. **Concurrency:** newly generated questions survive an older send. External resolution preserves local answers as editable draft text.
10. **History:** accepted human steering and replies retire older completed questions, including late ingestion.
11. **Compatibility:** blocking questions, approvals, commands, settings-only actions, and disabled widgets retain their rules.
12. **Visual QA:** existing widget style, keyboard access, mobile scrolling, collapsed composer, and focus behavior remain correct.

After model implementation, remind the user to migrate their running instance.
After backend implementation, remind the user to restart through `devctl.py`.
Do not run migrations or restart servers during spec work.

## 12. Adversarial review record

Two independent internal reviewers review this document on 2026-10-05.
Their mandate covers spec behavior, contradictions, and missing product decisions.
It excludes implementation details.

| Round | Findings | Outcome |
|---|---|---|
| 1 | Historical retirement conflicts with live steering rules. External resolution leaves local answer recovery undefined. | Unify source-timeline retirement in sections 5 and 8. Define draft-text recovery in section 7.1. |
| 1 | Model, payload, and component prescriptions exceed spec scope. | Keep behavioral requirements. Defer implementation mechanisms to the plan. |
| 2 | Both reviewers recheck the revised spec and the previous findings. | Both report no remaining spec blockers or new blocking contradictions. |

Reviewer convergence does not replace user approval of the proposed product decisions.
