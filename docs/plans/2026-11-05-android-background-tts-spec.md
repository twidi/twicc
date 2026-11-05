# Android background TTS

Status: approved for implementation planning after two adversarial review rounds. Product implementation has not started.

## Goal

Continue manual TTS and automatic timeline TTS when the Android app leaves the foreground or the screen turns off.
An Android service owns playback and its network connection. Playback does not depend on the Web page running JavaScript.
Use the same service for foreground and background TTS. Keep both Android browser engine builds supported.

The user calls this behavior **always on**. It is a service lifetime rule, not a separate permanent app-wide toggle.

## Agreed scope

- Support all existing TTS entry points: messages, selections, summaries, configuration tests, and automatic timeline readings.
- Use one native TTS output for the Android app. Never overlap Web and native TTS.
- Keep the existing server voices, scripts, configuration validation, text adaptation, summaries, announcements, cache, and streaming.
- Read every fragment of a logical message before completing that reading.
- Keep listening while timeline auto-TTS is enabled, including long periods without messages.
- Provide **Skip** and **Stop all**. Do not add a user-controlled pause or seek feature.
- Keep ordinary application notification sounds outside this change.
- Do not add iOS support, push infrastructure, an external broker, or a second Android application.

## Current building blocks

The backend already combines generated speech parts into one streaming audio resource with an `audio_id`.
The frontend does not maintain a playlist of separate part URLs.
MP3 part headers and PCM frame boundaries already receive handling before parts enter the combined stream.
The native reader must preserve this behavior, rather than add another splitting or concatenation layer.

| Existing code | Reuse |
| --- | --- |
| `src/twicc/tts/` | Speech preparation, generation, stream delivery, completion, cancellation, and cache |
| `src/twicc/summaries/` | Existing summary preparation, saved snapshots, and speech references |
| `src/twicc/core/services/timeline_*` | Timeline selection, live signals, snapshots, filtering, and catch-up |
| `frontend/src/stores/textToSpeech.js` | Existing controls, reading identity, configuration, priority, and UI state |
| `frontend/src/stores/timelineSpeech.js` | Existing listening behavior and rules that must be preserved |
| `android/app/src/main/` and `android/policy/` | Shared app state, service behavior, commands, instances, and policies |
| Existing Android app bridge and flavor adapters | Origin-scoped frontend control and engine-specific integration |

Reuse the current ordinary-browser players. Android routes supported TTS to its service instead.
The current WebSocket announces timeline changes. Background reading delivery requires an extension; it does not already exist.

## Selected architecture and alternatives

The selected architecture uses a native Android service with its own WebSocket and native audio output.
The WebSocket carries control, reading references, and state. Audio uses the existing HTTP streaming path.
The service can receive, request, and finish later readings without a mounted frontend or an executing JavaScript thread.

The backend owns automatic message selection and preparation for this background listening session.
Reuse existing timeline and TTS services. Move or expose only the coordination needed for this session.
Do not create separate copies of timeline selection rules in Gecko and WebView, or a general workflow engine.

The Android service owns the actual playback state, manual-versus-automatic arbitration, and audible completion.
The backend must receive enough playback feedback to preserve existing ordering, announcements, and cancellation semantics.
Successful generation, delivery, and audible completion are different events.

The frontend controls the service and mirrors its state. It does not run a competing Android automatic-reading coordinator.
The browser's ordinary UI WebSocket may reconnect independently. Its availability must not gate background TTS.

Alternatives considered:

- A foreground service around the existing Web players retains dependence on browser scheduling and page lifetime.
- A native player controlled by the Web coordinator protects an opened reading, but cannot independently start subsequent messages.
- Periodic silent audio might affect a media-player inactivity timer. It does not establish reliable background listening.

Do not use periodic inaudible audio to maintain the service.

## Service lifetime

Start the required native foreground work from the user's TTS or listening action.
Do not wait until audio generation completes or a background message arrives to establish the required Android lifecycle.
Starting manual preparation also reserves playback against automatic selection.
A temporary selection can reserve native speech before its page acquires a summary descriptor.
It transfers captured text, purpose, and the summary-model choice frozen at the action.
The backend derives the source hash and versioned policy, then acquires its own shared-service reader.
When a descriptor already exists, transfer the complete frozen descriptor instead.
Neither form waits for the page's summary promise or depends on its continued ownership.

The service remains needed while either condition holds:

1. A TTS reading is preparing, loading, buffering, playing, or settling its completion and resource cleanup.
2. Automatic timeline listening is enabled.

Normal network waits and gaps between generated audio fragments are part of the same reading.
They must not stop the service or remove its notification.
When neither condition holds, stop background work and release its resources.
Finishing a manual reading must not stop an enabled automatic listener.

The service lifetime is independent of Activity and Web document lifetime.
In-app navigation, page reload, Activity recreation, and dismissal from recent tasks do not end a healthy native listening session.
Reopening or reloading the frontend retrieves the existing native state without replaying or duplicating audio.
Web page teardown must not release audio resources owned by the native service.

Explicit Android force-stop or system service-stop remains effective. Do not defeat it with automatic resurrection.
There is no requirement to restore a lost reading or listening activation after process death, reboot, or backend restart.
Do not display a lost session as active or replay its old queue automatically.

## Playback and commands

Use one logical playback at a time, including preparation. Preserve existing manual priority.
Manual speech replaces a previous reading. An interrupted automatic message is discarded; other pending messages remain.
Late generation results or transport replies cannot start a cancelled or replaced reading.
Do not replay a completed, skipped, or interrupted reading when its connection reconnects.

| Action | Required behavior |
| --- | --- |
| Skip | Cancel the current reading and its preparation. Discard it. Keep automatic listening and the remaining queue. |
| Stop all | Cancel current preparation/playback, disable automatic listening, and discard pending automatic readings. |
| Manual reading finishes or is stopped | Continue the enabled automatic queue. Otherwise release the service when cleanup completes. |
| Disable the active timeline toggle | Disable its listening and clear its queue. Cancel its automatic reading; preserve unrelated manual speech. |
| Enable another timeline | Replace the automatic scope with its new activation boundary. Preserve unrelated manual speech. |
| Request manual speech during automatic preparation | Reserve manual priority before cancelling the old automatic reading. |

Skip has no effect when no reading is selected. Stop all also works while the listener waits silently.
Both commands take effect locally even when the server cannot be reached. Reconnection must not undo them.
Use the same command meanings in the native notification and Android frontend controls.
The rail's current-reading stop acts as Skip. Existing message-level manual stop remains scoped to its own reading.
This Android command contract overrides older timeline specifications where an automatic current-reading stop disables listening.
Ordinary browser command behavior is outside this change.

Do not mistake a stall, fragment boundary, or completed generation for audible completion.
Check the existing backend completion result and release the audio resource after playback settles.
Support the existing MP3, WAV, and raw PCM configurations. Do not force a voice or format change for background support.
Respect Android audio focus and system media interruptions. The notification must retain a usable Stop all action.

## Automatic timeline behavior

Preserve the current listening behavior, including current summary settings.
The baseline is `2026-11-04-timeline-auto-tts-spec.md` and the current timeline/summary behavior.
This document overrides only Android playback ownership, lifetime, and the command differences stated above.

In particular:

- Read only eligible saved `assistant_message.sent` events, using the selected global, workspace, project, or worktree scope.
- Record activation at the user's action. Do not lose qualifying arrivals during initialization or move the boundary forward.
- Preserve hidden-session rules and the listening scope's **Show muted sessions** option.
- Preserve live membership changes. New scope members do not cause historical replay.
- Preserve message-date ordering, event-ID deduplication, the five-second admission tolerance, and selected-reading stability.
- Preserve manual priority, spoken project/session context, summary selection, preparation settings, and source snapshots.
- A selected reading freezes the same policy as today. Pending readings use the existing live-setting rules.
- Ignore unrelated display filters. Navigation does not move listening to the newly displayed timeline.
- Preserve temporary-disconnection catch-up and the existing incomplete-catch-up failure behavior.

The active native session retains its last accepted **Show muted sessions** value while the page is absent.
A reloaded frontend restores this value for the listening scope before sending later explicit user changes.
Do not persist unrelated timeline display filters as part of this change.

Keep one listening scope per Android installation and one source instance for its active TTS session.
Other phones, desktop browsers, and browser tabs keep their independent playback and listening state.
Do not disable another device's listener or delete its audio when this device stops.

## Connection, state, and failures

The native connection authenticates to the selected HTTPS instance without requiring JavaScript to relay requests or renew connectivity.
Native authentication must work with both engine profiles and the configured HTTPS access path.
Reuse existing authentication boundaries. Do not introduce an unrestricted command channel or require a second password entry for normal activation.
Loss of authorization stops the affected native session and surfaces the error.

Temporary connection loss retains the current in-memory activation and accepted pending messages where continuation remains valid.
Reconnect without advancing its activation boundary, duplicating accepted events, or restarting already heard audio.
A reading cut by a network failure follows existing TTS error behavior; automatic readings are not blindly retried from the beginning.
If continuity cannot be established, stop the affected listening session and explain the loss.
Do not silently replace missing live events with a historical replay.

Native state remains authoritative while the frontend disconnects.
On return, the frontend shows the current reading, automatic scope, pending state, and relevant error.
Cancellation from the notification remains effective while the page is unavailable.
Obsolete state or commands cannot revive a stopped session.
Abandoned backend preparations and audio resources must eventually be cancelled or expire when their client cannot return.
This does not require immediate server cancellation during an outage or durable storage of offline commands.

A message-specific preparation or playback failure reports the failure and permits the next eligible automatic reading.
Unavailable TTS configuration disables automatic listening under existing rules.
An activation that cannot establish native background support must not appear successfully enabled.

## Android foreground work and screen-off operation

Display a native notification while the service is active.
It identifies the source instance and whether the service is preparing, reading, waiting, or reconnecting.
Provide Skip when a reading exists, Stop all while active, and an action to reopen TwiCC.
Use existing frontend indicators rather than adding a second in-page always-on control panel.

Playback uses an appropriate Android media foreground-service lifecycle.
Continuous silent waiting requires its own justified foreground-service lifetime, independent of Media3's idle-playback timeout.
`specialUse` is the proposed Android classification for this user-requested waiting function; confirm the valid declaration during planning.
Do not substitute a time-limited data-sync service, pretend to use a microphone, or play synthetic silence.

Screen-off operation is an acceptance requirement, including long silent waits before the next message.
Use necessary wake-lock handling and provide access to battery-optimization exemption when required for direct real-time network delivery.
Keep the display off. Explain a required battery setting and any failed activation; do not promise an exemption the app cannot grant.
Release resources when their purpose ends. Continuous listening can consume more battery than an inactive app.

The service must reconnect after ordinary network and tunnel interruptions.
It cannot guarantee an unbroken socket, survival after explicit stop, or operation when the user denies required platform conditions.

## Minimal integration and compatibility

Put the service, network client, native playback, and shared state in the common Android source set.
Keep only genuinely engine-specific bridge or browser integration in the Gecko and WebView source sets.
Do not leak engine API types into shared audio contracts or implement two native audio stacks.

Keep the existing frontend entry points and status identities. Route them through a small native-control adaptation on supported Android clients.
All supported Android TTS uses the service, including foreground playback. No foreground/background handover restarts the audio.
The ordinary Web players and ordinary browser timeline listening remain supported.

Detect incompatible server/app capabilities before starting a native session.
Older combinations may retain existing foreground Web TTS, but must not claim native background listening is active.
Do not silently fall back to a Web player after an established native reading fails.

Keep session coordination and queues in memory. Reuse the existing database, caches, and server process.
Keep existing bounded summary preparation. Do not pre-generate an unbounded future audio playlist.
Do not add durable delivery, a new queue database, a broker, or a general job scheduler.
Do not expand this change into app-wide background synchronization or guaranteeing the UI WebSocket stays connected.

## Proposed defaults requiring no new product surface

These are specification proposals, not additional user requests:

- Switching to a different instance ends the old instance's native TTS and listening session. The new instance starts with listening disabled.
- Opening instance management without switching does not itself interrupt speech.
- Logout ends the affected session. Web page reload or force refresh retains a healthy native session.
- Process death or lost backend state requires explicit reactivation. This version does not add automatic playback restoration.

## Acceptance examples

Run applicable cases on both Android engine builds. Validate battery behavior on a physical phone as well as an emulator.

| Case | Expected result |
| --- | --- |
| Start manual speech, then immediately leave the app during preparation | The service finishes preparation and reads the message. |
| Read a long message whose later fragments arrive slowly | Hear all fragments in order. Gaps do not complete the reading. |
| Play MP3, WAV, and PCM through current supported configurations | Preserve the chosen voice, format, and streaming behavior. |
| Move between foreground and background during one reading | One continuous native playback; no duplicate or restart. |
| Reload or recreate the Web page during native playback | Playback continues. The new page retrieves the current state. |
| Reload while native listening includes muted sessions | Preserve that listening option and restore its matching frontend control. |
| Enable timeline listening, then suspend page JavaScript | Native arrivals, preparation, reading, and notification commands still work. |
| Lock the phone; receive nothing for at least thirty minutes; then send an eligible message | Read it without reopening TwiCC, under the documented battery conditions. |
| Press Skip during automatic speech | Stop that reading. Keep listening and read the next eligible message. |
| Press Stop all during preparation or while waiting silently | Cancel current work and clear listening. No late result starts speech. |
| Press Skip or Stop all during a network outage | Apply it locally. Reconnection cannot restart the discarded reading or activation. |
| Request manual TTS while automatic speech has messages pending | Manual speech takes priority. Retain other pending automatic readings. |
| Finish manual speech while automatic listening remains enabled | Keep the notification and resume the automatic queue. |
| Disable the timeline toggle during unrelated manual speech | Clear listening; let manual speech finish. |
| Lose Wi-Fi or the tunnel, then reconnect | Apply existing live catch-up rules without duplicate playback or historical replay. |
| Lose a session beyond recoverable continuity or restart the backend | Surface stopped listening; require explicit reactivation. |
| Switch instances while speech is preparing or playing | Stop old-instance work. Late replies cannot affect the new instance. |
| Another device listens to the same instance | This device's stop and scope changes do not control the other device. |
| Dismiss the Activity from recent tasks | A healthy native reading/listening session continues with notification controls. |
| Explicitly stop the service through Android | Stop it. Do not silently recreate the session. |
| Use an ordinary browser or an older unsupported combination | Preserve existing behavior without claiming native background support. |

## Details reserved for implementation planning

Choose exact service classes, Media3 integration, PCM adaptation, endpoint paths, message schemas, and authentication handoff during planning.
Also choose reconnect timing, session-expiry mechanisms, state transport, and internal module boundaries there.
They must meet this behavioral contract. This specification does not require a particular library version or class hierarchy.

## Platform references

- [Media3 background playback](https://developer.android.com/media/media3/session/background-playback).
- [MediaSessionService inactivity timeout](https://developer.android.com/reference/androidx/media3/session/MediaSessionService#setForegroundServiceTimeoutMs(long)).
- [Foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types).
- [Foreground-service background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start).
- [Doze and battery exemptions](https://developer.android.com/training/monitoring-device-state/doze-standby).

## Specification review

Two independent internal reviewers complete two adversarial review rounds on 2026-11-05.
They review behavior and existing-rule compatibility separately from Android feasibility, lifecycle, and minimal reuse.
Their mandate excludes implementation-level decisions reserved for planning.

The first round finds no material blocker. The author then clarifies three boundary cases:

- Restore the active listener's muted-session option after frontend reload, without persisting unrelated filters.
- Apply Skip and Stop all locally during network loss. Reconnection cannot undo them.
- Expire abandoned backend work and preserve bounded summary preparation without adding durable delivery or unbounded audio pre-generation.

Both reviewers inspect the revised specification in the second round.
Both report no remaining material specification issue and consider it ready for user review.
The specification review includes no product implementation or dependency installation.
