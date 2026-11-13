# Timeline speech: final messages only

Status: specification reviewed; awaiting user approval.

## Purpose

Reduce interruptions during long agent tasks. Automatic timeline speech should read the final answer after an official turn ending. Intermediate messages can remain visible without being read.

This adds a speech policy. It does not change message classification, shell classification, turn detection, timeline contents, or notifications.

## User control

- Add a switch below the timeline heading, speech button, and help button. Show it when this timeline owns active listening, including paused listening.
- English label: **Read only end-of-turn messages**. French label: **Lire seulement les messages de fin de tour**. All interface text uses i18n.
- Default: on, including installations with no saved value.
- Store one global preference in the existing synced settings. Retain it across reloads and listening activations. Apply it to browser and native Android listening, including background listening without a visible page.
- Off preserves the current automatic speech eligibility rules. Manual speech remains available in either mode.

## Eligibility

With the switch on, a reading requires both:

1. An official, committed `turn.ended` event from the existing completion system.
2. The exact `assistant_message.sent` event selected as that ending's final message by the existing final-message selection/publication path.

Read the saved message text or its existing summary variant. Read at most that one message for the ending.

A provider terminal message or a message classifier YES is insufficient. Claude and Codex use the same speech rule. If shell classification keeps the turn open, no final reading qualifies. If the existing completion system accepts an ending with independent background work, its final message qualifies.

Do not select the nearest visible message, the latest message in the session, or a message based on elapsed time. Do not infer finality from text. A turn ending with no eligible final message produces no reading. An interruption or error does not authorize an arbitrary preceding progress message.

## Authoritative association and late arrivals

The backend must expose a reliable association between the ending and its selected timeline message. Reuse the existing authoritative selection. Do not create a second selection algorithm in either speech reader.

The association identifies the ending, message event, session, and native source scope. A newer turn, another session, or a replaced source history cannot supply a substitute message.

| Arrival order or condition | Required result |
| --- | --- |
| Message is published before its ending | Keep it silent. Admit it only when its matching ending is confirmed. |
| Ending is committed before the final message is indexed or published | Wait for that exact message and association. Its arrival completes eligibility. |
| Final message already appeared through classifier YES | The ending still authorizes that same message; prior observation is not speech consumption. |
| Classifier NO is overridden by a true ending | Read the final message once its existing forced publication completes. |
| A newer turn starts before an older association becomes ready | Retain the older association. Never select the newer turn's message for it. |
| No eligible final message exists | Close the speech obligation without substituting another message. |

An unresolved association must not block ready readings from other endings. Reuse existing committed indexing/publication signals for late resolution. No additional polling or periodic source scans.

## Time, ordering, and deduplication

Keep the original timestamps of both timeline events. Speech eligibility uses the official ending's effective `occurred_at`, not the message timestamp or the later association/publication time.

- The ending must occur strictly after the listening scope's activation boundary. A message can predate activation when its ending occurs after activation.
- An ending before that boundary stays excluded, even if its message or association arrives later.
- The existing long-outage/backlog reset advances this boundary as today. A completion from the discarded range cannot return through late resolution.
- A delayed confirmation or final publication after a qualifying ending must not be rejected by the existing five-second message watermark. This exception belongs to the final-message obligation, including when that obligation survives a switch to ordinary reading mode. Subsequent ordinary message arrivals retain ordinary time eligibility.
- Order ready pending readings by ending time, then ending event ID. A late-ready reading never restarts or displaces a reading already selected.
- Deduplicate by message event across both modes for the listening activation. Feed replay, repeated confirmation, and association updates cannot read the same message twice.
- Merely seeing an intermediate message must not prevent its later admission as the confirmed final message. Existing manual-priority and discard-on-interruption behavior remains unchanged.

## Mode changes and existing playback

Apply a preference change to active listening without changing the selected scope or restarting listening.

Turning the option on removes unselected intermediate readings from the queue and releases their summary/audio preparation. A reading already selected can finish under its frozen policy. Turning the option off resumes ordinary eligibility for subsequent message arrivals; it does not replay earlier skipped messages or scan history.

An outstanding final association can still complete after a mode change. It retains its final-message time eligibility and can authorize its exact message once, provided the ending remains within the activation boundary and the message has not already been consumed by automatic speech.

Keep existing hidden/muted rules, scope membership, stop/pause, manual priority, and network recovery. Recheck visibility and scope before reading. Disabling listening or replacing its scope invalidates outstanding work under the existing activation ownership rules.

Apply eligibility before requesting automatic summaries, adapting text, or generating audio. Intermediate messages must not consume those resources while this option is on.

## Performance and compatibility constraints

- Obtain the message association from final selection/publication already performed for that ending. Do not re-read a transcript to decide which message to speak.
- Reuse known event identities and existing source bounds for any missing-event recovery. Repeated notifications with unchanged indexed data must not repeat source decoding or search.
- Keep source preparation outside DB writer admission. Association writes and validation must remain short.
- Resolve event metadata by known identities. Batch reads for a feed page or pending batch; avoid one query per rendered row or historical message.
- Normal feed refreshes must not revisit all prior turns or decisions. No separate watcher, timer, durable speech delivery queue, or model call.
- No new DB table, schema migration, compute-version bump, or historical timeline rebuild. Exact association storage and transport are implementation-plan choices within these constraints.
- Keep existing appends and timeline event identity rules. Do not rewrite historical events or require retained semantic decision rows to read current associations after ordinary decision cleanup.
- Do not guess associations for legacy events without proof. With this option on, an unproven final remains silent. Off keeps legacy automatic eligibility.

## Acceptance cases

Verify the same policy in browser and native Android paths, including native recovery without an open page:

1. All seven intermediate messages reported in **Forking try 4**, 13 November, 00:05–06:44 Paris time, remain silent without an official matching ending.
2. Message-first and ending-first arrivals each produce one correct reading, including a prior message YES or NO.
3. A Codex terminal message stays silent while shell classification holds the turn open. Accepted completion then reads its exact final message.
4. A message ten minutes older than another session's last spoken message still qualifies when its own ending qualifies and its association resolves late.
5. An older turn resolving after a newer turn starts still uses the older turn's exact message and original ending time.
6. Before-activation endings, discarded outage backlog, repeated feed pages, and repeated association signals never cause replay.
7. Toggling either way preserves the active scope, handles queued/preparing/playing work as specified, and never reads a message twice. A waiting final that resolves after switching off remains eligible even behind a newer ordinary reading's watermark.
8. Default-on and saved-off values survive reload, a new listening activation, and native background/recovery operation.
9. Hidden/muted sessions, scope departure, stop, pause, manual interruption, missing final text, and source replacement preserve their existing guarantees.
10. Instrumented checks show no history scan for association lookup, no repeated decoding while data is unchanged, and no source preparation under the writer lock.

## Approach and boundaries

Use one backend-owned completion-to-message association and apply the preference in both existing speech coordinators. This keeps authoritative final selection in one place and avoids speculative playback.

Rejected alternatives: matching adjacent timeline rows fails with delayed publication and interleaved events; waiting a fixed delay cannot prove completion; a second classifier adds cost without proving an official ending.

The implementation plan chooses fields, transport, indexes already available, and exact code boundaries. This specification does not redesign the completion state machine or the speech scheduler.

Related historical designs: [automatic timeline speech](2026-11-04-timeline-auto-tts-spec.md), [network recovery](2026-11-09-timeline-speech-network-recovery-design.md), and [semantic decisions](2026-11-10-timeline-semantic-decisions-spec.md). This document changes the default automatic eligibility policy; it does not edit those historical documents.
