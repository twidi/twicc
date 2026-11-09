# Timeline Speech Network Recovery Implementation Plan

> **For agentic workers:** Use focused implementation and verification for each subsystem.

**Goal:** Resume Android timeline speech after network outages and require explicit foreground resume on suspended web pages.

**Architecture:** Preserve user intent independently of connections. Apply a 120-second outage policy to both clients, with native spoken recovery feedback and web foreground controls.

**Tech Stack:** Kotlin policy/service, Django native TTS, Vue/Pinia web coordinator, existing configured TTS and catalogs.

**Spec:** docs/plans/2026-11-09-timeline-speech-network-recovery-design.md

## Global Constraints

- Outages shorter than 120 seconds catch up; outages of 120 seconds or longer skip backlog.
- Stop and pause always override recovery. Avoid duplicate speech and stale asynchronous work.
- Android announcements use installation language and configured TTS.
- Web background suspension requires an explicit foreground resume action.
- No branch, worktree, commits, server restart, migration, package install, or unsolicited APK build.
- Catalog edits and generated artifacts belong to the integration owner.

## Review Focus

- Pause or stop during outage must prevent revival and unsolicited announcements.
- Network flapping must not reset the outage duration prematurely or repeat announcements.
- Old asynchronous audio and feed results cannot populate a fresh playback queue.
- Long web suspension must not play on visibility restoration or socket reconnection.
- Authentication failure, process death, and missing scopes cannot trigger infinite session replacement.

## Task 1: Android recovery

- [x] Add policy and adapter tests for short outage, 120-second boundary, expired-session replacement, one announcement, pause/stop, and stale events.
- [x] Implement recovery in android/policy/src/main/kotlin/com/twidi/twicc/tts/ and android/app/src/main/java/com/twidi/twicc/tts/.
- [x] Extend src/twicc/tts/ only where required for configured announcement playback and fresh listening boundaries.
- [x] Run focused Kotlin and backend tests with local toolchain and isolated Django test settings.

## Task 2: Web recovery

- [x] Add coordinator/control tests for short/long outages, visibility suspension, explicit resume, and cancellation races.
- [x] Implement in frontend/src/stores/timelineSpeech.js and the existing timeline speech control.
- [x] Run timeline speech coordinator, integration, and UI tests.

## Task 3: Integration

- [x] Add all new messages to all five catalogs and the native manifest as needed.
- [x] Generate Android catalogs and fixtures, then run i18n generation and source checks.
- [x] Run combined focused tests and frontend production build.
- [x] Review the complete diff, correct material findings, and report product behavior and required reload/APK/backend restart.

## Verification results

- Web coordinator, integration, timeline control and sidebar tests: 139 passed.
- Native backend protocol, session, timeline and transport tests: 150 passed.
- Android policy tests: 93 passed. Gecko debug unit tests: 101 passed.
- APK build script tests: 8 passed. Shell syntax check passed.
- Android catalog generation and source checks passed for 257 keys and five locales.
- Focused frontend i18n lint and frontend production build passed.
- Independent review verified fixes for capped catch-up, out-of-order checkpoints,
  lost recovery acknowledgements and repeated short/long outages.
- Physical-device smoke checks remain manual. No APK was built or server restarted.
