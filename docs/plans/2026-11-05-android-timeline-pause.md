# Android Timeline Pause Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement these tasks in order. Follow test-driven development. Keep unrelated changes untouched.

**Goal:** Pause and resume automatic timeline speech from the Android notification.

**Architecture:** Keep the native listening intent while its backend listener is disabled. Resume through the existing `listen` command with a new activation ID and the click timestamp. Keep the foreground service, socket, and notification during pause. Both browser engines share this implementation.

**Tech Stack:** Kotlin, Android Service and notifications, existing native TTS protocol, Vue/Pinia.

**Spec:** The approved behavior below records the conversation agreement.

## Approved behavior

- Show `Pause timeline` before `Stop all` while timeline listening is enabled.
- Pause skips the current reading, whether manual or automatic.
- Pause blocks subsequent automatic selections and audio generation.
- Manual readings remain available during pause.
- Replace `Pause timeline` with `Resume timeline` while paused.
- Resume keeps the scope and muted-session choice. It uses a fresh activation and timestamp.
- Never replay messages from before Resume, including messages received during pause.
- Keep the notification while paused. Disable in the app or Stop all removes the listener and permits service shutdown.
- Expose pause in the native snapshot. Keep the in-app timeline toggle enabled so it can disable paused listening.

## Global constraints

- Reuse backend `disable`, `listen`, and `skip`; add no backend protocol command.
- Backend listener errors carry their activation ID. A new `listen` clears its predecessor's error before any acknowledgement state.
- Reuse one common controller and service across Gecko and WebView.
- Preserve ordinary browser TTS behavior.
- Do not change battery advice, notification permission, signing, or build profiles.
- Keep only the latest Gecko ARM64 release APK for installation.
- Commit only files owned by this task. All source and UI text use English.

## Review Focus

- A stale Pause or Resume PendingIntent must not change a later listening activation.
- Delayed candidates, audio, and acknowledged backend `listening: null` must not undo pause.
- Pause during connection loss must survive reconciliation; Resume must not admit old candidates.
- Manual playback during pause must keep working and leave pause intact afterward.
- Disable and Stop all must remove paused intent and release foreground ownership.
- Delayed listener errors must not remove paused intent or a newer activation. Intermediate backend `listening: null` states must not override native intent.

### Task 1: Native controller pause state

**Files:**
- Modify: `android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsProtocol.kt`
- Modify: `android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt`
- Test: `android/policy/src/test/kotlin/com/twidi/twicc/tts/TtsStateTest.kt`

**Interfaces:** Add `NativeListening.paused: Boolean = false`. Add internal `PauseTimeline(activationId)` and `ResumeTimeline(activationId, boundary)` commands. They match the current activation and expected pause state. Resume rotates the activation ID.

- [x] Write tests for cancellation, delayed candidates/audio/state, manual playback, fresh Resume, offline reconciliation, stale commands, Disable, and Stop all.
- [x] Run `:policy:test`; observe failures for missing pause behavior.
- [x] Add the commands and state transitions. Disable the backend listener during pause. Preserve paused intent when backend listening is null. Ignore candidates while paused.
- [x] Run `:policy:test`; expect all tests to pass.
- [x] Commit this task with its tests.

### Task 2: Notification controls and page mirror

**Files:**
- Modify: `android/app/src/main/java/com/twidi/twicc/tts/TtsNotification.kt`
- Modify: `android/app/src/main/java/com/twidi/twicc/tts/TtsService.kt`
- Modify: `android/app/src/main/java/com/twidi/twicc/tts/TtsServiceOwner.kt`
- Modify: `android/app/src/main/java/com/twidi/twicc/tts/NativeTtsBridge.kt`
- Modify: `android/app/src/main/res/values/strings.xml`
- Modify: `frontend/src/stores/timelineSpeech.js`
- Modify: `frontend/src/components/timeline/TimelineFilterBand.vue`
- Test: `android/app/src/test/java/com/twidi/twicc/tts/TtsServicePolicyTest.kt`
- Test: `android/app/src/test/java/com/twidi/twicc/tts/NativeTtsBridgeTest.kt`
- Test: `frontend/src/stores/nativeTts.integration.test.js`
- Test: `frontend/src/components/timeline/TimelineFilterBand.test.js`

**Interfaces:** Notification actions include session epoch and listening activation ID. Service creates Resume's UTC boundary with exactly six fractional digits at receipt. Snapshot JSON exposes `listening.paused`; the store mirrors it as `paused`. The timeline toggle still disables paused listening and its tooltip identifies pause.

- [x] Write failing service tests for current/stale notification commands and retained foreground resources. Write frontend tests for paused state and disabling it.
- [x] Implement action routing, labels, paused status, and snapshot mirroring. Keep at most three notification buttons.
- [x] Run both flavor release unit suites and targeted frontend tests; expect all to pass.
- [x] Commit this task with its tests.

### Task 3: Device validation and installation APK

**Files:**
- Modify: `android/app/src/androidTest/java/com/twidi/twicc/tts/TtsPlatformBoundaryTest.kt`
- Modify: `android/README.md`
- Modify: `android/app/build.gradle.kts`
- Modify: `android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt`
- Test: `android/policy/src/test/kotlin/com/twidi/twicc/tts/TtsStateTest.kt`
- Modify: `src/twicc/tts/native_timeline.py`
- Modify: `src/twicc/tts/native_sessions.py`
- Test: `tests/test_native_tts_timeline.py`
- Test: `tests/test_native_tts_sessions.py`

- [x] Exercise real notification PendingIntents: pause an automatic reading, reject old candidates, allow manual audio, resume with fresh boundary, and stop while paused. Check labels/order, snapshot JSON, and retained notification.
- [x] Update previous notification assertions for the added Pause control. Document current pause semantics, including manual reads and ignored messages.
- [x] Bump Android version to 0.5.2, code 13. Run frontend build and suite. Report unrelated existing failures separately.
- [x] Build Gecko ARM64 release with unit tests and lint. Run WebView release unit tests and lint for common code.
- [x] Perform one independent final review. Correct confirmed defects with focused regression tests.
- [x] Verify APK identity, architecture, signature, fixture exclusion, non-debuggable manifest, and checksum.
- [x] Commit owned changes, publish the new APK, remove superseded APKs and temporary test APKs, and stop the test emulator.

## Review correction

The independent review identifies delayed listener errors that remove paused intent. Inspection also shows that `listen` can publish acknowledged state before the new backend activation exists. The correction keeps native intent authoritative, ignores paused or obsolete activation errors, tags backend listener errors, and clears previous errors before a new listen acknowledgement. No new protocol command or dependency is needed. Tests cover the paused state, manual playback, reconnect, resumed activation, and genuine failure of that new activation.


## Validation

- Controller: 56 policy tests pass.
- App: 50 release unit tests pass for each engine. Both flavor lint checks pass.
- Frontend: 57 targeted tests and the production build pass.
- Backend: 117 native TTS tests and Ruff checks pass.
- Device: the notification Pause/manual/Resume/Stop all scenario passes on the Android 15 emulator with the Activity closed.
- Review correction: three native regressions and two backend regressions fail before the fix and pass afterward. No review findings remain open.
- Full frontend suite: 4499 pass; six pre-existing failures remain in SessionEventNotice.test.js (missing sessions dependency), SidebarRail.touch.test.js (three focus cases and their parent, missing skip mock), and androidGeckoTransport.test.js (missing crypto in its VM).
- Release APK: version 0.5.2/code 13, com.twidi.twicc, ARM64 only, non-debuggable, same persistent signature, no test CA or audio fixtures. SHA256: 8c6551421c13eb4623cfe87fbaec6ba7a1bb8252ecbcbf8848399a9ef31f8ec8.
- Backend restart remains a user operation. No migration or server restart runs during this task.
