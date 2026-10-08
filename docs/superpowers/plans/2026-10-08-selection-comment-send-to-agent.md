# Selection Comment Send to Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Send to Agent` to the shared selection-comment form, using the existing composer and early Chat navigation.

**Architecture:** `MessageInput` owns one transient combined operation and calls its existing insertion and send methods. A small controller manages screenshot waiting, cancellation, and duplicate-operation prevention. `SessionView` provides the action to all form instances and initiates Chat navigation after insertion.

**Tech Stack:** Vue 3 Composition API, existing Pinia stores, Vue Router, existing attachment uploads, and node:test.

**Spec:** `docs/plans/2026-10-08-selection-comment-send-to-agent-spec.md`

**Status:** Adversarial implementation-plan review complete; awaiting user review and execution choice.

## Global Constraints

- `Cancel` keeps its current behavior.
- `Add to message` keeps its current behavior, including focus rules and keyboard shortcuts.
- Selection formatting, source labels, file metadata, and quote modes remain unchanged.
- Send the complete composer, including its existing text and attachments.
- Navigation does not wait for upload completion or successful message dispatch.
- Wait automatically only for the screenshot added by this action.
- The upload toast delay is **3000 ms**.
- A successful manual send, screenshot removal, or clearing the message cancels the pending automatic send.
- New form instances disable `Send to Agent` while their session has a pending operation.
- Do not add a WebSocket send path, a retry system, a new success contract for `handleSend`, or a new keyboard shortcut.
- No dependencies, backend changes, migrations, changelog changes, branch creation, or worktree creation are required.
- Preserve user-owned changes. Use English in code, UI strings, comments, and this plan.
- Do not install packages or restart servers without the user's explicit request.
- If execution creates commits, use Conventional Commits with a descriptive body and the current model's required co-author trailer.

## Review Focus

1. A second form opens during upload: one pending operation per session, with `Add to message` still available.
2. Upload becomes ready as a manual send starts: at most one dispatch, including asynchronous trust or question staging.
3. Text clears and receives a replacement draft in one tick: the pending operation cannot send the replacement.
4. The user changes session or dock focus: the operation retains its owner and never navigates on upload completion.
5. Screenshot staging is slow or fails before insertion: no premature send, lost form, or late misleading toast.

Each condition has tests below. These are behavioral tests, not assertions about source-string spelling.

## File Map and Existing References

| File | Responsibility |
| --- | --- |
| Create `frontend/src/utils/selectionCommentSend.js` | Small, dependency-injected operation controller; no router, store, or component imports |
| Create `frontend/src/utils/selectionCommentSend.test.js` | Deterministic controller tests with fake scheduling and deferred attachment promises |
| Modify `frontend/src/components/message/MessageInput.vue` | Composer ownership, reactive upload observation, current send controls, cancellation hooks |
| Create `frontend/src/components/message/MessageInput.selectionSend.test.js` | Execute compiled component script with stubs; exercise actual insertion and send integration |
| Modify `frontend/src/components/session/detail/SessionItemsList.vue` | Forward the exposed composer action and its availability/pending state |
| Modify `frontend/src/views/SessionView.vue` | Provide action to forms; navigate after insertion using existing session route helpers |
| Create `frontend/src/utils/selectionCommentBridge.js` | Testable thin bridge between form, owning composer, and navigation callback |
| Create `frontend/src/utils/selectionCommentBridge.test.js` | Verify ordering, target capture, and unavailable-composer behavior |
| Modify `frontend/src/components/session/detail/TextSelectionComment.vue` | Third button and dispatch mode, shared formatting and screenshot preparation |
| Modify `frontend/src/components/session/detail/TextSelectionComment.test.js` | Execute form methods using existing compileScript test harness |
| Modify `frontend/src/components/browser/BrowserPane.vue` | Return the new attachment record from `attachScreenshot` |
| Modify `frontend/src/components/files/FilePane.vue` | Return the new attachment record from `attachElementScreenshot` |
| Modify `frontend/src/utils/composerAttachments.js` | Optional upload-start notification before awaiting staging |
| Modify `frontend/src/stores/data.js` | Forward optional upload-start notification to the existing attachment action |
| Modify `frontend/src/utils/attachmentRecovery.test.js` | Verify notification timing without changing attachment recovery |

Read these existing paths before editing:

- `MessageInput.vue`: `insertTextAtCursor`, `handleSend`, `handleSnippetLongPress`, `updateTextareaContent`, `onInput`, `onBeforeUnmount`.
- `MessageInput.vue`: `sendingLocked`, `isDisabled`, `attachmentsReady`, `asyncQuestionSendClassification`, normal Send button.
- `SessionItemsList.vue`: composer `v-if="!parentSessionId && !isEphemeral"` and current exposed forwarding methods.
- `SessionView.vue`: `provide('insertTextAtCursor', ...)`, `sessionTabRouteLocation`, `switchToTab`, dock routing and KeepAlive behavior.
- `composerAttachments.js`: `ATTACHMENT_STATE`, `addAttachment`, record `id`/`bucket`/`sessionId`, and readiness helpers.
- `stores/data.js`: `addAttachment`, `getComposerAttachments`, `localState.attachmentRuntime`, and draft-session alias resolution.
- `TextSelectionComment.test.js`: existing compiled-SFC harness and focus regression tests.

## Task 1: Build the Deferred Operation Controller

**Files:** Create `selectionCommentSend.js` and `selectionCommentSend.test.js` under `frontend/src/utils/`.

**Interfaces:**

- Produce `createSelectionCommentSendController(deps) -> controller`.
- `deps.insert(text)` calls the composer's existing insertion with `focus:false`.
- `deps.attemptSend()` invokes the existing send method or reports current send unavailability through a toast.
- `deps.onPendingChange(boolean)` publishes operation ownership to Vue.
- `deps.notifyUploadWait()` shows the exact upload-wait copy from the spec.
- `deps.schedule(callback, delayMs) -> cancel()` defaults to a timer and permits deterministic tests.
- Produce `controller.start(text, { attachScreenshot = null, onPrepared }) -> Promise<void>`.
- `attachScreenshot({ onUploadStarted })` returns `Promise<AttachmentRecord>` after adding through the existing flow; it does not promise upload readiness.
- `onUploadStarted(record)` gives the controller screenshot identity before staging awaits; start the 3000 ms timer at this notification.
- `onPrepared()` runs after insertion and before waiting or sending; it closes the form and initiates navigation.
- Produce `controller.observeScreenshot({ id, present, state }) -> void`, `controller.cancel() -> void`, and `controller.dispose() -> void`.
- `state` uses existing `ATTACHMENT_STATE` values. An absent state is not ready.
- Expose a read-only `controller.pending` boolean.

- [ ] **Step 1: Write failing operation tests.** Name and assert these behaviors:

```js
// start_without_screenshot_inserts_before_prepared_before_send
assert.deepEqual(events, ['insert:comment', 'prepared', 'send'])
// upload_wait_toast_uses_exact_delay
clock.advance(2999); assert.equal(toasts.length, 0)
clock.advance(1); assert.deepEqual(toasts, ['upload-wait'])
// ready_screenshot_attempts_once
assert.equal(sendCalls, 1)
assert.equal(controller.pending, false)
```

Also test attachment rejection before insertion, already-ready attachment, missing runtime state, failed/missing attachment, and removal.
Test duplicate `start` while staging or waiting: only one attach, insert, and send operation.
Test early upload notification while attachment creation remains unresolved: the timer starts once and readiness cannot send before insertion.
Test failure or screenshot removal during staging followed by a resolved attachment promise: insert and prepare, but never send.
Test cancellation followed by a replacement operation: late results from the first operation cannot affect the second.
Test disposal: timers and late attachment callbacks cannot insert, notify, or send.
These cancellation assertions apply before normal send handoff; they do not cancel a send already accepted by `handleSend`.
Test `onPrepared` completion does not wait for screenshot readiness.

- [ ] **Step 2: Run the tests and confirm the intended missing-module failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/selectionCommentSend.test.js`.

- [ ] **Step 3: Implement the controller.**

Reserve ownership synchronously before the first await. Use a monotonically increasing operation token to reject stale continuations.
Keep the pending reservation through insertion, preparation, upload waiting, and the normal send attempt.
Retain screenshot identity so observation applies only to this operation's attachment.
Remember readiness observed before staging resolves; attempt sending only after successful insertion and preparation.
Record failure/removal observed during staging without discarding the pending insertion.
If attachment creation resolves with a failed or removed record, still insert and prepare; then settle without sending.
If attachment creation rejects, retain the form instead. Distinguish rejection from the utility returning a failed record.
Settle on failure, cancellation, or completion; clear the timer and publish `pending:false` exactly once.
Do not interpret an attachment retry as permission to recreate a cancelled operation.
If attachment creation rejects, propagate the error to the form without insertion or navigation.
On a failed upload after insertion, settle without a send; the existing chip supplies recovery.
Call `attemptSend` once; release ownership when that call settles.
The cancellable automatic-wait phase ends when `attemptSend` enters the normal send method.
After that boundary, normal trust-dialog, staging, dispatch, and recovery behavior remain authoritative.
Do not add cancellation semantics to an already-entered or committed normal send.

- [ ] **Step 4: Run the targeted tests.** Expected: all tests pass.
- [ ] **Step 5: Check the task diff.** Commit only this task's files if execution includes commits.

## Task 2: Integrate the Controller into MessageInput

**Files:** Modify `frontend/src/components/message/MessageInput.vue`, `frontend/src/utils/composerAttachments.js`, and `frontend/src/stores/data.js`.
Create `MessageInput.selectionSend.test.js` beside the component; extend `frontend/src/utils/attachmentRecovery.test.js`.

**Interfaces:**

- Consume Task 1's controller.
- Expose `startSelectionCommentSend(text, { attachScreenshot = null, onPrepared }) -> Promise<void>`.
- Expose `selectionCommentSendPending` as a Vue ref, unwrapped through the component expose proxy.
- Expose `selectionCommentSendAvailable` as a computed boolean identifying an actual mounted composer, not current send readiness.
- Keep `insertTextAtCursor` and `handleSend`'s external behavior and void return contract.
- Supply `attemptSelectionCommentSend() -> Promise<void>` internally: reuse current Send controls and then `await handleSend()`.
- Extend `addAttachment(sessionId, file, { onUploadStarted = null } = {}) -> Promise<AttachmentRecord>` in the store and attachment utility.
- The optional callback runs once after successful persistence, immediately before the first `startAttempt(record)` call.
- Existing two-argument attachment calls and retry behavior stay unchanged; retries do not repeat this notification.

- [ ] **Step 1: Write failing component-script tests.** Extend the repository's compiled-SFC testing approach without adding a test framework.

Test exact content insertion with existing text and selected caret range, and appending when collapsed.
Verify `focus:false` for this action and no textarea focus on touch.
Test blocked states independently: sending lock, disconnected socket, unavailable provider, initial sync, starting session, and other unready attachments.
Assert insertion still occurs, dispatch does not occur, and the exact unavailable-send toast appears.
Assert the normal send payload still includes existing settings, text, attachment refs, and async-question handling.
Assert a failed dispatch retains the existing recovery behavior and does not create another retry mechanism.
In the attachment harness, delay staging and assert the upload-start notification occurs before the attachment promise resolves.
Assert persistence rejection produces no upload-start notification, and retries do not repeat it.
Delay persistence beyond 3000 ms: no upload-wait toast. Then delay staging beyond 3000 ms: one upload-wait toast.
Make `startAttempt` publish failure before `addAttachment` resolves: preserve inserted text and early Chat navigation, with no send.
Test screenshot failure/removal, text clearing then replacement in one tick, and normal manual-send success during deferred waiting.
Test a manual-send/automatic-send race across asynchronous trust and question staging: one dispatch only.

- [ ] **Step 2: Run the targeted tests and confirm behavioral failures.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/message/MessageInput.selectionSend.test.js`.

- [ ] **Step 3: Wire composer ownership and screenshot observation.**

Create the controller once per mounted composer. It survives tab hiding and KeepAlive deactivation.
Use reactive observation of `store.getComposerAttachments(props.sessionId)` and `localState.attachmentRuntime[record.id]`.
Check screenshot state immediately after staging resolves, including completion or removal during the staging await.
Forward the optional attachment notification through the store; never infer screenshot identity from the newest composer record.
Use `onUploadStarted(record)` to give the controller its identity and start upload observation before staging resolves.
Start the slow-upload timer at that notification, including while attachment creation is still awaiting.
Do not restart the 3000 ms timer when creation resolves.
Use a synchronous observation path so clearing text and replacing it within one tick cancels the old operation.
Ordinary nonempty draft edits remain eligible and participate in the eventual send.
Apply text/removal cancellation during the upload wait, before normal send handoff.
On component unmount, dispose the controller. Do not cancel merely on session deactivation.
Disposal prevents future automatic attempts; it does not replace existing behavior of an already-entered normal send.

- [ ] **Step 4: Wire the normal send path without a parallel sender.**

Reuse the same disabled conditions as the current Send button after insertion; classify commands and async answers normally.
Unavailable sending shows this exact copy once:

> Cannot send right now. Your message is in the composer. Send it when sending becomes available.

Keep `handleSnippetLongPress` behavior unchanged.
Add a shared in-progress guard around asynchronous `handleSend` execution so manual and automatic attempts cannot dispatch concurrently.
Release that guard in `finally`, including blocked returns, cancelled trust, staging failures, and dispatch failures.
The new automatic entry treats an existing in-progress send as unavailable.
At the existing accepted-send points, cancel any deferred selection operation before the composer can receive a replacement draft.
For plain messages, use successful dispatch. For async questions, use the existing committed `onStaged` boundary.
This hook also invalidates the current operation token when its own send succeeds; it must not trigger a second attempt.
Preserve the existing failed-send and async-question recovery code and payload generation.

- [ ] **Step 5: Run component and controller tests.** Expected: all pass, including the asynchronous race tests.
- [ ] **Step 6: Check the task diff.** Commit only this task's files if execution includes commits.

## Task 3: Provide the Session Bridge and Early Navigation

**Files:** Modify `SessionItemsList.vue` and `SessionView.vue`; create `selectionCommentBridge.js` and its test under `frontend/src/utils/`.

**Interfaces:**

- Forward Task 2's method through `SessionItemsList` with the same signature.
- Forward pending and availability through getters; nested exposed refs must not be mistaken for booleans.
- Produce `createSelectionCommentBridge({ getComposer, showChat }) -> bridge`.
- `getComposer()` returns the owning session's exposed composer facade, or null.
- `bridge.available` and `bridge.pending` are getters reading that facade.
- `bridge.send(text, { attachScreenshot = null, onPrepared }) -> Promise<void>` captures the facade before awaiting.
- `showChat()` uses `SessionView`'s existing `switchToTab('main')` without a focus helper.
- Provide the bridge in `SessionView` as `selectionCommentSend`.

- [ ] **Step 1: Write failing bridge tests.**

Assert insertion precedes preparation, preparation initiates navigation, and navigation precedes any upload wait or send attempt.
Assert already-on-Chat is a no-op navigation and does not focus the composer.
Assert switching the mocked current session after staging starts cannot retarget the captured composer.
Assert completion never calls `showChat` a second time.
Assert an absent composer reports unavailable and cannot lose text through a silently ignored insertion.
Assert availability remains true during temporary send blocks; pending state follows the owning composer.

- [ ] **Step 2: Run and confirm the intended missing-module failure.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/selectionCommentBridge.test.js`.

- [ ] **Step 3: Implement the thin bridge and forwarding.**

Use Task 2's method; do not copy insertion or send logic.
Invoke the form's `onPrepared` and initiate `showChat` immediately after composer insertion.
Do not await router navigation before upload observation or sending.
Handle navigation rejection without losing the prepared composer or creating an unhandled promise rejection.
Preserve the existing session route identity, sidebar filter, workspace query, and dock tab routing.
Do not use `focusChatPrimary`, `gotoChatFooterPanel`, or the tab-navigation helper that focuses the composer.
Availability checks use the mounted `MessageInput` facade. Parent/subagent and ephemeral views without a composer hide the action.
Do not add mutual static imports between stores, composables, components, or the router.

- [ ] **Step 4: Run bridge and earlier tests.** Expected: all pass.
- [ ] **Step 5: Check the task diff.** Commit only this task's files if execution includes commits.

## Task 4: Add the Form Button and Screenshot Record Handoff

**Files:** Modify `TextSelectionComment.vue`, its existing test, `BrowserPane.vue`, and `FilePane.vue`.

**Interfaces:**

- Inject Task 3's `selectionCommentSend` bridge with a null fallback.
- Extend `attachScreenshot(dataUrl, { onUploadStarted = null } = {}) -> Promise<AttachmentRecord>` in Browser and FilePane callbacks.
- Add `sendToAgent() -> Promise<void>` to the form.
- Reuse the existing comment formatter and screenshot attachment preparation for both actions.
- `Send to Agent` passes the formatted text plus newline to `bridge.send`, with an optional callback accepting `{ onUploadStarted }`.
- Pass `onPrepared: close` so the form closes after insertion, before the deferred wait.

- [ ] **Step 1: Write failing form tests.**

Execute component methods through the existing SFC harness.
Assert the new action uses the same formatted text and metadata as `Add to message`.
Assert the new action passes the screenshot callback without calling it twice.
Assert attachment creation errors retain the form and display its existing error toast.
Assert preparation closes the form without cancelling the composer-owned wait.
Assert repeated calls while staging make one bridge call.
Assert `Add to message` still uses `insertTextAtCursor` and preserves desktop/touch focus rules.
Assert Ctrl/Meta+Enter still invokes only `Add to message`.
Use compiled-template tests or rendered inspection to verify button availability and pending disabling; avoid relying only on string matches.

- [ ] **Step 2: Run and confirm the new behavior fails.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/session/detail/TextSelectionComment.test.js`.

- [ ] **Step 3: Implement the third button and shared preparation.**

Keep `Cancel` and `Add to message`; append `Send to Agent` with the exact label.
Show it only when `bridge.available` is true.
Disable it during screenshot capture, local submission, or `bridge.pending`.
Do not disable `Add to message` merely because another form has a deferred operation.
Keep screenshot attachment errors under the existing `Couldn't attach screenshot` handling.
Return `store.addAttachment`'s record from both screenshot callbacks; existing Add to message ignores that return value.
Forward `{ onUploadStarted }` to `store.addAttachment`; existing Add to message supplies no callback.
Keep all consumers on the shared form; no changes to their trigger or selection detection are required.
Use existing wrapping actions CSS so three buttons fit the mobile form width.

- [ ] **Step 4: Run form, bridge, controller, and composer tests.** Expected: all pass.
- [ ] **Step 5: Check the task diff.** Commit only this task's files if execution includes commits.

## Task 5: Verify the Complete User Flow

**Files:** No new production files. Correct only defects within the files above.

**Interfaces:** Consume Tasks 1–4's completed integration.

- [ ] **Step 1: Run all frontend tests.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`.
Expected: exit code 0. Investigate new failures; identify existing failures separately.

- [ ] **Step 2: Build the frontend.**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm run build`.
Expected: exit code 0; all existing bundle targets build. Do not install dependencies to run this command without permission.

- [ ] **Step 3: Verify desktop behavior on the user's available running instance.**

Exercise Chat prose/code, Files source/Markdown, both Git diff sides, Plan, Terminal, session Artifacts, Browser elements, and Browser errors.
Verify sessionless artifact and composerless views do not expose the new action.
Verify existing draft insertion, early Chat navigation, normal send payload, blocked-send toast, and existing recovery.
If no instance is available, report that limitation; do not start or restart one without a user request.

- [ ] **Step 4: Verify upload waiting and cancellation.**

Use a controlled slow upload in the browser or integration harness.
Check no toast before 3000 ms, one toast after 3000 ms, and one send when ready.
Check failure, removal, clearing then replacing text, opening another form, and manual send.
Switch session and dock focus while waiting; ensure only the originating composer is affected and no completion navigation occurs.
Confirm a later manual upload retry does not resume automatic sending.

- [ ] **Step 5: Verify mobile layout and focus.**

Use a 375 px viewport and touch settings.
Check all three buttons remain accessible, sending does not open the keyboard, and the existing Add shortcut/focus behavior remains intact.

- [ ] **Step 6: Inspect the final diff and report results.**

Run: `cd /home/twidi/dev/twicc-poc && git diff --check`.
Expected: no whitespace errors. Confirm no changelog, backend, dependency, or unrelated changes.
Record commands, outcomes, and any unavailable browser validation in the implementation handoff.

## Self-Review and Execution Handoff

Spec coverage: Tasks 1–2 own waiting, cancellation, existing send controls, and recovery.
Task 3 owns session identity, composer availability, and early navigation.
Task 4 owns button behavior, unchanged actions, formatting, and screenshot handoff.
Task 5 verifies every covered surface and mobile behavior.

Execute tasks sequentially; their interfaces depend on previous tasks.
No implementation starts until the user reviews this plan and chooses an execution method.
Native execution is recommended because the tasks modify one connected composer flow.
Subagent-driven execution remains available if the user prefers task-level independent reviews.

### Adversarial Review Outcome

Two independent internal reviewers complete two rounds of plan review.
The corrections add early screenshot identity, preserve insertion after staging failure, and define the normal-send cancellation boundary.
Both reviewers report no substantive blockers in the second round.
The author checks spec coverage, interface consistency, task granularity, review-focus tests, and plan proportion.
