# Timeline message snapshots

Status: approved by the user.

## Goal

Read new user messages and assistant final messages from project, workspace, and global timelines.
Keep the complete message text available without opening the session.

## Agreed scope

- Record new messages only. Do not backfill existing history, initial imports, or recomputations.
- Use the existing `TimelineEvent` table and its JSON `data` payload.
- Add no table, message model, retention ledger, or separate journal.
- Keep the existing confirmed-turn behavior. A displayed final message does not complete the logical turn.
- Store text snapshots, not thinking, tool calls, tool results, or attachment binaries.
- The future voice assistant is outside this change. Durable message associations support it later.

## Messages to record

Add one event per retained message, with a distinct kind for each role:

- `user_message.sent`: genuine user-message text, including messages sent while the assistant works.
- `assistant_message.sent`: assistant-message text that the provider identifies as final. Preserve every such message, including several finals in one logical turn.
- Use the existing provider classification and native message identities. Join split Claude text blocks into one complete message.
- A missing marker alone does not prove a message is final. Use existing verified final identities where available; do not guess from wording or position.
- Codex asynchronous questions do not become final messages merely because they carry `final_answer`. Preserve their existing classification.
- Do not create blank text events for messages without a textual body.

The Conversation display mode is not the selection rule. Its positional summary can hide provider finals and retain intermediate messages.

Record each retained message once its complete text is available, without waiting for logical completion.
Ingestion may lag provider delivery. Do not capture an earlier message as a substitute.
Optimistic or failed local sends do not become confirmed message events without source evidence.

Preserve each message group's first native timestamp as its event time.
Within one capture batch, source order breaks equal-time ties.
Across separate capture batches with strictly equal native timestamps, the existing event-ID arrival tie-break applies.
A later-completed group can therefore follow an earlier-captured group, even if its first source line precedes it.
Capture complete messages immediately. Keep the existing feed, cursor, and merge ordering.

## Storage and associations

The existing event columns identify the session, provider, and occurrence time.
The event kind identifies the role. Do not duplicate it in a JSON `role` field.
The message event's `data` stores complete text with Markdown and source identity for deduplication.
Keep the full text in this event once. Do not duplicate it in turn events.

Capture each outgoing request's full origin before provider delivery. Match it by the exact native user-message identity.
Claude SDK and Codex reserve those outgoing identities. Their UI messages belong to `You`; agent CLI/MCP inputs retain their sending agent's origin.
Keep unmatched sources under `Unknown`. Never infer the sender from a `user` role, message text, or nearby event.
Hybrid terminal inputs have no exact outgoing identity and remain unmatched unless explicit source evidence identifies their origin.
Carry the exact input origins and source scope in existing turn-event JSON for delayed source indexing.
Read live origins after source indexing, so a send admitted while the watcher reads is included.
Preserve the original message origin across automatic settings restarts; the process restart remains automatic.
Leave existing message events unchanged. No attribution backfill or new table is required.

The existing turn events carry references to their associated message events.
A start may have no triggering user message. A logical turn may contain several user messages and several assistant final messages.
The completion association identifies the ordered messages exchanged during that turn; it does not assert that one answer semantically answers one prompt.

Preserve correct associations when source indexing and turn publication arrive in either order.
Never attach an earlier or later turn's message by choosing the latest transcript line or closest timestamp.
Do not require a user message for an automatic turn.

The text and associations remain readable after the logical journal expires: closed intervals after 48 hours and open intervals after seven days.
Use timeline data for this durable history. Do not extend journal retention.
A live interval can outlast its open journal row.
Its existing verified runtime identities and opening source scope still authorize immediate capture during a logical hold.
Keep the current-source, provider, incarnation, epoch, source-floor, new-only, and exact-terminal checks.
Retry, reconnect, restart, and recompute must not duplicate message events or replay old messages as new.
Message snapshots follow the existing timeline lifecycle. Session source changes do not rewrite their saved text.

## Display

- Show message entries in the existing timeline feed, with session identity, author role derived from kind, and time.
- Label entries `User message` and `Assistant message`. The assistant selection rule still requires provider finals.
- Collapse the preview to one visual line, using the first nonempty plain-text line without a character limit.
- Indicate additional lines with an ellipsis. Clip horizontal overflow to the available width with a CSS ellipsis.
- Show a chevron immediately after the message label only when more lines or horizontal overflow remain.
- Point it down while collapsed and up while expanded. Keep it hidden for a message that fits on one line.
- The label and its chevron form one native toggle button for the complete saved Markdown, including short messages.
- The collapsed preview is also a native button that opens the message, including short messages.
- Assistant entries show the existing `TextToSpeechButton` after the label's chevron, or after the label when no chevron appears.
- Read the complete saved message through the shared speech service. Preserve its availability, playback, cancellation, and error behavior.
- User entries have no speech button. Speech controls do not expand the message or navigate to the session.
- While expanded, hide the preview. Put `Close message` after the complete text.
- Keep native keyboard controls. Move focus to the message label after opening from the preview or closing the message.
- The default All view includes the new message entries. Existing scope and visibility rules still apply.
- Keep message arrival separate from confirmed turn completion, unread state, and completion notifications.
- Do not change the session's Conversation display mode or add a new display-mode system.

## Acceptance examples

| Case | Expected result |
| --- | --- |
| User A, user B during work, final B, final A | Two `user_message.sent` events followed by two `assistant_message.sent` events, in source order within the session, except exact native-time ties across separate capture batches use event-ID arrival order. |
| Equal-time Claude groups complete in separate batches, B before A | Capture B immediately, then A. Preserve both first native timestamps; event IDs retain arrival order. |
| Verified markerless final after open-journal cleanup, with a child hold | Capture the complete current-source message immediately, in either source/proof order; keep the logical interval open. |
| Several provider finals before one confirmed completion | One event per final message and the existing single logical end. |
| Commentary, thinking, and tools between finals | No message entries for that material. |
| Claude final split across text records | One complete assistant snapshot. |
| A turn ends before its final text is indexed | Its eventual message and association remain correct; no earlier answer is substituted. |
| Automatic turn without a new user message | Assistant messages remain visible; no old prompt is reused as the trigger. |
| Send fails before a confirmed user source exists | No confirmed user-message event for that attempt. |
| Journal cleanup, backend restart, or source recompute | Saved message text and associations remain available; no duplicate or historical replay. |

## Approach

Use standalone message events in the existing timeline table, linked to the existing turn events.
Attaching all text only at `turn.ended` delays useful replies during long work.
Selecting messages with Conversation mode loses some finals.
The implementation plan must use the smallest change that meets these behaviors. Do not add generic correlation, relevance scoring, or a second persistence system.
