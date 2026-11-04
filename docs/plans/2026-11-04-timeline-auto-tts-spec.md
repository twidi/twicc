# Automatic timeline text-to-speech

Status: adversarial specification review complete. Awaiting user approval. Not approved for implementation.

## Goal

Read new assistant messages aloud from one selected timeline.
The user can navigate anywhere while listening to work across sessions.
Reuse the existing timeline feed and global TTS service. Make the smallest changes that support this behavior.

## Scope and constraints

- Read only existing `assistant_message.sent` events and their complete saved text.
- Do not infer finals, reconstruct messages, or read commentary, tools, thinking, or user messages.
- Support the existing global, workspace, project, and worktree timeline scopes.
- Reuse existing scope membership and hidden-session rules.
- Ignore all timeline display filters. Changing a view, provider filter, or session filter never changes automatic listening.
- Keep one automatic listening scope per browser tab. Different tabs do not coordinate playback.
- Keep listening state and pending messages in memory. A reload or logout disables listening and clears the queue.
- Add no database table, migration, durable queue, language detector, new model call, or new audio player.
- This document specifies behavior, not store structure, API fields, algorithms, or an implementation plan.

## Activation and navigation

Place an audio toggle immediately to the right of the timeline title.
Use the existing TTS availability rules. Do not start listening without an available TTS configuration.
Expose the toggle's active state and action through accessible text.

Activation records the click time as the starting boundary.
Only messages dated strictly after that boundary can qualify.
Initialization or a slow request must not move that boundary forward or lose qualifying arrivals during initialization.
Previously displayed or loaded messages are not read merely because listening starts.

Listening continues when the user leaves the timeline, changes tabs within the application, or opens a session.
It continues to collect and read qualifying events without requiring the timeline component to stay displayed.
It uses the selected timeline's scope, not the currently displayed page.

Clicking the active toggle disables listening, clears the queue, and cancels its current automatic preparation or playback.
It does not stop an unrelated manual playback.
Enabling listening again starts at the new click time. Discarded messages never resume.

Enabling another timeline disables the previous one and discards its pending messages.
Cancel its automatic preparation or playback, but leave an unrelated manual playback running.
The new timeline starts at its own activation time.

## Sidebar rail

Reuse the existing timeline button in the sidebar rail.

| Listening state | Appearance | Destination |
| --- | --- | --- |
| Disabled | Existing timeline icon | All Projects timeline, as today |
| Enabled | Timeline icon with a small overlaid audio icon | The timeline whose listening mode is enabled |

The overlay remains visible between messages and while a manual playback takes priority.
The button remains a navigation action. It does not toggle listening.
Its accessible label and tooltip identify the listening destination.
Do not add a second global listening indicator or a separate global listening control.

## Message eligibility and ordering

Use new live timeline events, including ordinary live catch-up after a connection interruption.
Do not use rendered rows as the arrival signal.
Pagination, historical backfill, or showing a previously hidden session must not replay existing messages.
The initial synchronization can admit qualifying new arrivals since activation; it must not treat its history as new.

A candidate must satisfy all these conditions:

- Its kind is `assistant_message.sent`, with nonempty saved text.
- It belongs to the listening scope and is visible under existing hidden-session rules.
- Its `occurred_at` is strictly after the activation time.
- Its event ID has not already been accepted during this activation.
- Its date is no more than five seconds behind the automatic playback time watermark, if one exists.

The watermark is the greatest `occurred_at` of automatic messages whose audio has started during this activation.
It never moves backwards when a slightly late message plays.
An event exactly five seconds behind the watermark remains eligible.
Manual playback neither advances this watermark nor replaces the activation boundary.

Sort pending messages by `occurred_at` ascending, then event ID ascending for equal dates.
Use event IDs for deduplication and live fetching, not as a substitute for message-date ordering.
Do not intentionally delay playback to wait for potentially earlier messages.
Once a message is selected for TTS preparation, keep that reading ahead of later arrivals.
Do not cancel or restart its preparation merely to restore date order.
A slightly earlier arrival can remain eligible and play afterwards.

Evaluate the five-second tolerance when admitting a message.
An accepted message does not expire because it waits behind speech or a manual playback.
The tolerance compares message dates, never message age against the wall clock.

Use the existing event date unchanged. It is the message group's first native timestamp, not browser receipt time.
A message dated before activation remains excluded even when its final capture arrives after activation.

Maintain the existing scope membership semantics while listening.
Scope changes must not replay history or reset the original activation boundary.
Remove pending messages from sessions that become hidden, or projects that leave the scope.
If the listening scope disappears or becomes empty, disable listening rather than falling back to the global scope.

## Shared player and manual priority

Keep the existing single global TTS playback. Never overlap automatic and manual audio.
An occupied player includes both preparation and audible playback.
Automatic playback waits while any unrelated playback occupies the player, including selections and settings tests.
Prepare and generate queued speech only when it is that message's turn to use the player.

| Action or event | Result |
| --- | --- |
| New qualifying event during any playback | Add it to the pending queue |
| Manual playback requested during automatic playback | Cancel automatic playback and start the manual request |
| Manual request replaces another manual request | Keep existing replacement behavior |
| Manual playback ends or is stopped | Continue with pending automatic messages |
| Automatic playback is interrupted by a manual request | Discard the interrupted message; retain other pending messages |
| Active timeline toggle is clicked during manual playback | Disable listening and clear the queue; leave manual playback alone |
| Another timeline is enabled during manual playback | Replace the listening scope; leave manual playback alone |

Manual buttons remain available. Do not disable them because automatic playback is active.
An explicit manual request must acquire priority before the queue can react to the previous playback stopping.
Automatic playback must not slip between the stop and start of a manual replacement.
Canceling an obsolete preparation must prevent its eventual response from starting audio.

The existing rail `Stop reading` action needs one ownership distinction:

- During automatic preparation or playback, stop it, disable listening, and clear its queue.
- During manual preparation or playback, keep its current stop behavior. Automatic listening remains enabled.

The stop action on a manually played message also keeps its existing behavior.
If a message-level stop controls an automatic playback of that message, treat it as stopping automatic listening.
An explicit stop of automatic speech must not immediately launch its next queued message.

## Spoken context

Use project display names and session titles. Do not generate a translated introductory sentence.
Compare project IDs and session IDs, not their names.
Use the message event's project and session identity for its context.
Resolve readable names through existing project and session display conventions.

| Previous audible context | Announcement before the automatic message |
| --- | --- |
| No context yet | Project and session |
| Project changes, session stays the same | Project |
| Both project and session change | Project and session |
| Only session changes | Session |
| Neither changes | No announcement |
| Manual audio has played since the previous automatic message | Project and session |

Announcements precede the message, in project-then-session order when both are needed.
Do not add new naming settings or a new title-generation system.
Preserve existing readable fallbacks for missing names.
Update audible context only when audio starts. A failed attempt without audio establishes no context.
Any manual audio that starts invalidates the previous automatic context, including selections and settings tests.
After a failed or interrupted automatic reading, announce full context for the next automatic message.

Keep the announcement outside message reformulation, so the reformulator cannot omit it.
Use the existing TTS generation pipeline and active voice configuration for both announcement and message.
Treat them as one logical playback for cancellation and ownership.
Announcements work for direct text, adapted text, disabled adaptation, adaptation fallback, and script configurations.
Audio cache reuse must never return a missing, additional, or incorrect announcement.

## Existing TTS behavior and failures

Reuse existing text preparation, reformulation, chunking, scripts, streaming, cache, playback, cancellation, and error reporting.
Reuse existing audio-player preparation for the activation click; do not create another autoplay mechanism.
Activation must not interrupt a manual playback merely to prepare future automatic playback.
Use the active TTS configuration when each queued reading starts.

A message-specific generation or playback error uses existing error reporting.
Discard that reading and continue with the next pending message. Do not automatically retry the failed message.
If TTS becomes unavailable, or browser playback requires a new user gesture, disable listening and clear the queue.
Do not repeatedly attempt queued playback after such a failure.

A temporary connection interruption retains the queue and uses existing live catch-up on reconnection.
If the existing feed reports a truncated catch-up, disable listening and report that the catch-up was incomplete.
Do not silently substitute a history reload for the missing live range.
Do not silently discard pending messages to impose a new queue limit.
Browser or operating-system suspension remains subject to existing browser audio limitations.

## Approach and reuse

Extend the global TTS service with a small automatic-listening coordinator.
Reuse timeline scope resolution, live invalidation, incremental fetching, event snapshots, and session metadata.
The coordinator must work independently of timeline display and its filters.
Reuse the existing rail action and navigation helpers.
Add context speech through the common TTS pipeline rather than duplicating either audio backend.

Rejected alternatives:

- Tying listening to visible rows or the mounted timeline: breaks navigation and can replay historical insertions.
- Asking the reformulation agent for every announcement: fails when text takes a direct or fallback path.
- A new language detector, translation call, player, or durable delivery service: adds machinery without serving this scope.
- A reordering delay before every reading: adds latency. The agreed five-second admission tolerance handles small inversions.

Choose exact code boundaries during implementation planning. Do not turn this feature into a generic playback scheduler.

## Specification defaults for user review

These small behavior choices complete the agreed requirements. They remain proposals until this specification is approved.

- If the scope becomes empty or disappears, disable listening. Do not create a second suspended-listening state.
- A message-level stop of automatic speech disables listening, like the rail stop. Do not immediately read the next message.
- Keep a reading already selected for preparation. Sort only messages that remain pending.

## Acceptance examples

| Case | Expected result |
| --- | --- |
| Enable at 10:30:00 with old messages already displayed | Read none of the old messages |
| A 10:30:01 event arrives while activation initializes | Retain and read it once |
| A 10:29:59 message finishes capture after activation | Exclude it because its native event date precedes activation |
| Navigate from the enabled timeline into a session | Continue collecting and reading new messages |
| Change any timeline display filter | Listening scope, queue, and playback remain unchanged |
| Receive 10:30:40, then 10:30:39 while both remain pending | Read 39, then 40 |
| Receive 10:30:39 while 10:30:40 is already preparing | Complete preparation and read 40, then 39; do not restart preparation |
| Start 10:30:40, then receive 10:30:39 | Read 39 after 40; keep the watermark at 40 |
| Watermark is 10:30:40; receive 10:30:35, then 10:30:34 | Accept 35; exclude 34 |
| A hidden session reappears with a 10:20 historical message | Do not enqueue that historical message |
| A project joins the listening workspace | Collect its subsequent qualifying live arrivals; do not replay its existing messages |
| Receive the same eligible event twice | Read it once |
| Queue waits behind a long manual reading | Keep accepted messages and read them afterwards, oldest date first |
| Manual reading interrupts automatic message A with B and C pending | Discard A, read the manual text, then read B and C |
| Resume automatic speech after manual audio | Announce project and session again |
| Consecutive automatic messages share a session and project | Announce context only before the first |
| Same session moves from project A to project B between message events | Announce project B before its next message |
| Switch listening from project timeline to All Projects | Clear the former queue, stop its automatic speech, and use the new click boundary |
| Click the rail timeline button while a workspace timeline listens | Open that workspace timeline; keep listening enabled |
| Click the active timeline toggle or global stop during its automatic speech | Stop automatic speech and clear the queue; no next message starts |
| Disable listening during manual speech, then enable again | Preserve manual speech; collect only messages after the new activation |
| A canceled generation request completes late | Do not start its audio |
| Adaptation is bypassed or fails | Keep the required context announcement and existing message fallback |
| Request cached audio with different context requirements | Hear exactly the required announcement, without stale context |
| Catch-up exceeds the feed's existing cap | Disable listening and report incomplete catch-up |
| Reload or logout | Stop speech through existing teardown and discard automatic listening state |

## Specification review

Two independent internal reviewers completed two review rounds.
Both reviewed behavior, requirement consistency, and minimal reuse. Implementation details were outside the review scope.

The first round found one issue: ordering before audible playback could require restarting an existing TTS preparation.
The correction limits sorting to pending messages and preserves a reading already selected for preparation.
The second round found no blocking specification issues or regressions.
The additional behavior defaults remain explicit proposals for user approval.
