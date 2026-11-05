# Android Instance Manager Implementation Plan

> **For agentic workers:** Use the existing Claude design session for the native UI task. The coordinating session verifies integration, requests an independent review, and builds the APK. Track progress with the task tool and commit each completed task.

**Goal:** Give the offline Android instance manager the visual style of TwiCC, with automatic Android light/dark mode.

**Architecture:** Keep a native screen shared by both engine flavors. Separate its styled views from MainActivity's existing instance, profile, navigation, and TTS callbacks. Use Android day/night resources derived from TwiCC's existing Web Awesome cyan and tinted-neutral themes.

**Tech Stack:** Android API 29+, Kotlin, platform Views and Dialog, existing Gradle toolchain. No new dependencies.

**Spec:** The requirements and design below capture the user's requests in this session.

## Requirements and Design

- The page works without any TwiCC server, CDN, downloaded font, or network request.
- Follow Android's current system light/dark mode, including changes while the page is visible.
- Reuse the TwiCC robot, system font, cyan accent, tinted neutrals, soft canvas auras, restrained panel borders, and compact spacing.
- Show saved instance names and addresses, with a genuine Last used marker from InstanceStore.last().
- Keep Add instance accessible below the scrollable list. Give Open, Edit, and Remove clear touch targets.
- Style empty state, add/edit forms, errors, and remove confirmation consistently.
- Preserve draft fields and pending confirmation during Activity recreation, including theme changes.
- Preserve HTTPS and duplicate validation, storage isolation, saved URLs, deferred cleanup, and TTS stop behavior.
- Do not add connectivity checks, fabricated status, theme selectors, or new engine abstractions.

Visual target: the Claude session's self-contained prototype at
`/artifacts/9ad7a062-93af-4199-a9c5-f1e54f285bda/instance-manager/index.html`.

### Approach

A packaged web page can share CSS, but the existing BrowserEngine only hosts remote HTTPS pages. It requires new local sessions and bridges for both engines. A separate System WebView introduces another renderer into Gecko. A styled native screen reuses the existing shared activity and callbacks, and has no additional runtime dependency. Use that approach.

## Review Focus

- Theme changes during Add/Edit must preserve unsaved values and validation feedback.
- Removing an instance must still stop its speech and request both engines' storage cleanup.
- Theme recreation must not auto-open the last instance.
- The list and dialogs must remain usable with keyboard, narrow screens, and large fonts.
- Long names and addresses must not crowd Edit/Remove touch targets.

## Task 1: Shared Native Screen and Theme Behavior

Files: `android/app/src/main/java/com/twidi/twicc/MainActivity.kt`, a focused `InstanceManagerUi.kt`, and common resources in `android/app/src/main/res/`.

- [x] Implement the prototype's visual structure using platform Views, shared callbacks, and day/night resources.
- [x] Preserve dialog state with Bundle fields. Let Android recreate MainActivity for uiMode changes.
- [x] Keep all InstanceStore and TtsService behavior in existing callbacks.
- [x] Compile both release flavors and inspect the owned diff.
- [x] Review and commit the completed screen task.

## Task 2: Verification and Installable Release

Files: `android/app/src/androidTest/java/com/twidi/twicc/InstanceManagerUiTest.kt`, `android/app/build.gradle.kts`, `android/README.md`, and this plan.

- [x] Exercise theme changes during editing and removal confirmation on the API 35 emulator.
- [x] Verify HTTPS/duplicate validation and successful save through the real UI and InstanceStore.
- [x] Inspect light/dark screenshots, empty state, narrow layout, keyboard, and large fonts.
- [x] Run policy/app unit tests and lint for both flavors.
- [x] Update version to 0.5.4 (15), update current-state documentation, and build Gecko ARM64 release.
- [x] Request an independent review. Fix any material issue and verify the affected behavior.
- [x] Commit the completed task, publish the signed release APK, remove the previous APK artifact, and stop the emulator.

## Verification Notes

- Both engine release Kotlin compilations pass.
- The API 35 emulator passes four real-UI tests: theme changes preserve edits, validation feedback, and remove confirmation; a selected instance remains readable at 320 dp with large fonts.
- Independent review found one narrow-layout issue. The Last used badge now sits below the address. The regression test fails before that change and passes afterward.

- Policy tests: 56 passing. App unit tests: 51 passing per engine. Release lint passes for both engines. Build script tests: four passing.
- Light/dark screen and keyboard form screenshots are inspected on the API 35 emulator. The Edit vector uses explicit arc flags so Android renders it.
- Instrumentation compiles for both engines. The removal test checks the other engine's pending cleanup, because the installed engine can finish its own cleanup immediately. The corrected test passes on Gecko.
- Gecko ARM64 release 0.5.4 (15) is signed with the existing certificate, has no debuggable flag, and includes no test CA. SHA-256: `bf808f57a493c2b3389c19fa9447e97891e0c921978a6347ebf21525405b7fa6`.
- Physical-phone validation remains for the user. The emulator graphics process can fail after changing display density; UI assertions pass, and visual checks use a fresh emulator at its default density.
