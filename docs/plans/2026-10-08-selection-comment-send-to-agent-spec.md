# Selection Comment: Send to Agent

**Date:** 2026-10-08
**Status:** Adversarial specification review complete; awaiting user review before implementation.
**Scope:** The expanded `TextSelectionComment` form and its existing session composer integration.

## 1. Goal

Add a `Send to Agent` button beside `Cancel` and `Add to message`.
The action adds the selection and comment to the existing composer, then sends through `MessageInput`.
It displays the session's Chat tab as early as possible.

Use the same insertion and normal send behavior as a mobile long press on a message snippet.
Send the complete composer, including its existing text and attachments.
Do not isolate the new comment into a separate message.

## 2. Existing behavior to preserve

- `Cancel` keeps its current behavior.
- `Add to message` keeps its current behavior, including focus rules and keyboard shortcuts.
- The collapsed comment and copy buttons keep their current behavior.
- Selection formatting, source labels, file metadata, and quote modes remain unchanged.
- Screenshot capture stays optional and uses the existing attachment flow.
- Normal composer send controls, session settings, and recovery behavior remain authoritative.

This feature does not introduce a separate WebSocket send path or a separate retry system.
It does not add a shortcut for `Send to Agent`.

## 3. Covered contexts

The action applies wherever the shared form has access to a session composer:

| Context | Comment source |
| --- | --- |
| Chat | Selected conversation text, including code blocks |
| Files | Selected source text or rendered Markdown |
| Git | Selected text in either side of a diff |
| Plan | Selected text in the displayed document |
| Session Artifacts tab | Selected text or a selected HTML element |
| Files HTML preview | Selected HTML element |
| Browser | Selected HTML element or captured page errors |
| Terminal | Selected terminal text |

The sessionless Artifacts browser does not gain a composer or this action.
Public share viewers do not gain this action.

## 4. Button availability

Show `Send to Agent` when the form can access the session's composer action.
A temporary send block does not hide the button.
The user can still insert the comment and navigate to Chat when sending is blocked.

Prevent a second submission while the combined operation is in progress.
Allow at most one pending `Send to Agent` operation per session.
New instances of the form disable `Send to Agent` while that session has a pending operation.
`Add to message` remains available with its existing behavior.
The controls must fit inside the existing form on desktop and mobile.

## 5. Action sequence

1. Accept one submission from the form.
2. Add the optional captured screenshot through the existing composer attachment flow.
3. Insert the formatted selection and optional comment into the existing composer.
4. Display the originating session's Chat tab immediately after insertion.
5. If this action adds a screenshot, wait for that screenshot's upload to become ready.
6. Invoke the normal `MessageInput` send action if sending is available.

Navigation does not wait for upload completion or successful message dispatch.
Do not focus the composer merely to trigger sending or open the mobile keyboard.

Insertion uses the current caret behavior, including appending when the composer is collapsed.
Existing text and attachments participate in the send exactly as they do with a snippet long press.
Normal send controls are evaluated at send time, not only at the initial click.

After insertion, the composer owns the prepared message.
The floating form closes; it is not a second recovery location.
The operation must continue independently of the floating form's lifetime after navigation.
The operation remains attached to the originating session's composer.
Switching sessions does not move the operation or affect another session's composer.
Upload completion does not navigate again if the user has left the originating Chat tab.

## 6. Screenshot upload

Wait automatically only for the screenshot added by this action.
Other attachments remain subject to normal composer send controls.

If the screenshot upload takes more than three seconds, show one informational toast:

> Screenshot upload in progress. Your message will send when the upload completes.

The delay starts when the screenshot enters the upload flow.
Upload completion triggers one normal send attempt, subject to current composer controls.
The upload toast must not appear after upload completion or failure.

If upload fails, retain the prepared text and attachment in the composer.
Use the existing attachment error and retry behavior.
Do not automatically send after a later manual attachment retry.

If screenshot attachment creation fails before insertion, preserve the form's existing attachment-error behavior.
Keep the form available so the user can retry or disable the screenshot.

### Interaction during the upload wait

The composer remains available during the wait.
Edits and additions to the current draft participate in the eventual normal send.
This includes content inserted through `Add to message`.
The action sends the current composer, not a frozen copy of the original insertion.

A successful manual send, removing the action's screenshot, or clearing the message cancels the pending automatic send.
Cancellation leaves any remaining composer content and attachments under normal composer control.
The old operation must never send a replacement draft or cause a duplicate send.
An upload failure also ends the pending automatic send, even if the user later retries the upload.
Cancellation or failure releases the pending-operation block and suppresses any later upload-wait toast.

## 7. Sending blocked or failed

If sending is unavailable, keep the prepared message in the composer and remain on Chat.
Show a toast explaining that the user must send when the composer permits it:

> Cannot send right now. Your message is in the composer. Send it when sending becomes available.

This includes normal temporary blocks such as a pending request, a starting session, or unavailable connection.
Do not automatically send later when an unrelated send block clears.

Dispatch and backend failures use the existing composer and Chat recovery behavior.
Do not replace that behavior with a new success contract, retry flow, or message restoration system.

## 8. Acceptance criteria

- Every covered context exposes the action through the shared form.
- `Add to message`, `Cancel`, copy, and existing shortcuts remain unchanged.
- The new action inserts the same formatted content as `Add to message`.
- An existing draft and attachments are included in the normal send.
- Chat becomes visible after insertion, before any upload wait or send result.
- Sending uses `MessageInput`, including its current settings and recovery behavior.
- A blocked send preserves the composer content and shows the explanatory toast.
- A screenshot delays sending until its upload is ready.
- A slow screenshot upload shows one informational toast after three seconds.
- A failed upload prevents automatic sending and uses existing attachment recovery.
- Repeated clicks during the operation do not duplicate insertion, attachment creation, or dispatch.
- Navigation and form closure do not lose or duplicate the pending operation.
- A new form cannot start another combined operation while the same session has one pending.
- Draft edits during upload participate in the eventual send; clearing the message cancels it.
- A successful manual send or screenshot removal prevents the pending automatic send.
- Switching sessions does not retarget the operation or cause a second navigation on upload completion.
- Desktop and mobile users can access all three form buttons.

## 9. Review boundaries

Review this document as a product and behavior specification.
Identify contradictions, missing user-visible outcomes, and conflicts with existing behavior.
Do not require function signatures, injection keys, watcher designs, or other implementation details.
Those choices belong to implementation planning.

### Review outcome

Two independent internal reviewers complete two rounds of adversarial specification review.
The first round identifies delayed-send cancellation, session targeting, and duplicate operations across new form instances.
The specification adds behavioral requirements for these cases.
Both reviewers report no remaining substantive specification findings in the second round.
