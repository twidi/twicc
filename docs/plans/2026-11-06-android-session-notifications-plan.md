# Android Session Notifications Implementation Plan

> **For agentic workers:** Use inline implementation, then an independent code review. Track progress with the task tool.

**Goal:** Keep notifications for other sessions while clearing and suppressing notifications for the session displayed in the foreground.

**Architecture:** Combine native location events and Activity lifecycle callbacks with a frontend readiness report on the existing menu heartbeat. Both browser engines report SPA URL changes to the shared BrowserActivity. Store only the current foreground session in memory. Identify posted notifications by instance, session, and category.

**Tech Stack:** Kotlin, Vue, existing Android NotificationManager, shared policy module, JUnit, existing browser instrumentation fixture.

**Spec:** Accepted behavior below reflects the user's session-by-session requirements.

## Accepted behavior

- A and B keep separate notifications, including notifications of the same category.
- Displaying A removes only A's notifications in the current instance.
- New notifications for A are not posted while A is displayed in the foreground.
- B and other instances can still post notifications.
- Backgrounding or locking the app permits all enabled notifications.
- Normal launch, notification taps, SPA navigation, and return from background all update the displayed session.
- The session's tool tabs use the same session identity. Singular and plural project routes are equivalent.
- Loading, login, error screens, and the instance manager do not suppress a session.
- Sessionless notifications and TTS controls retain their behavior.
- No new service, polling loop, persisted read history, or backend changes.

## Approach

Use existing native location events. The frontend adds `sessionId` to the existing menu heartbeat and sends an immediate report when the displayed session or its readiness changes. A session is ready when the authenticated, connected app has its session row and therefore renders SessionView's content instead of its loading/error fallback. Native checks that the report matches the current URL and Activity. No new command or polling loop is needed. Server-only presence cannot identify exactly what this Android screen displays or clear Android notifications locally.

## Review focus

1. A/B notifications in the same category must coexist and retain independent tap destinations.
2. SPA history changes must trigger cleanup in both Gecko and WebView without reloading the document.
3. Late callbacks from a previous Activity must not clear the successor's visible-session state.
4. Login, renderer failure, background, and lock must not silently suppress notifications.
5. Instance removal must also clear the new session-specific notification identifiers without affecting TTS.

## Task 1: Notification identity and visible-session policy

**Files:** `android/policy/src/main/kotlin/com/twidi/twicc/PushNotificationPolicy.kt`, `NotificationNavigation.kt`, corresponding policy tests, `android/app/src/main/java/com/twidi/twicc/push/PushPayload.kt`.

- [x] Add failing tests for session-specific tags, foreground matching, stale owners, route aliases and nested session tabs.
- [x] Add `PushNotificationPolicy`: `show(owner: Any, instanceId: String, sessionId: String?)`, `hide(owner: Any)`, `suppresses(instanceId: String, sessionId: String?): Boolean`, and `tag(instanceId: String, kind: String, sessionId: String?): String`.
- [x] Add `SessionLink.fromLocation(url: String): SessionLink?` for native URLs, keeping strict push-target parsing unchanged.
- [x] Expose the validated payload's optional session ID and verify policy tests pass.

## Task 2: Shared Android wiring and lifecycle verification

**Files:** `AndroidPush.kt`, `AndroidPushService.kt`, `BrowserActivity.kt`, `AndroidAppBridge.kt`, `PushNotificationNavigationTest.kt`, `frontend/src/App.vue`, `frontend/src/components/sidebar/AndroidAppMenu.vue` and its tests.

- [x] Reproduce category replacement with the existing real notification service fixture.
- [x] Serialize visible-session updates, cleanup and notification posting in AndroidPush. Clear only the exact instance/session tags.
- [x] Report loaded session location when resumed; clear visibility during background, navigation loads, disposal and failures. Check screen/keyguard state when suppressing.
- [x] Update instance-removal cleanup for all session-specific tags.
- [x] Extend the real browser fixture to verify normal SPA navigation, tap navigation, background return, sessionless events and instance isolation.
- [x] Verify HTML-only loading and a SPA error on a session URL retain notifications until the frontend confirms its session. Reuse the heartbeat to reconcile visibility after unlock.
- [x] Verify both engines using the real instrumentation fixture where the existing emulator is available.

## Task 3: Review and delivery

- [x] Dispatch a fresh reviewer with this plan and the changed files. Resolve material findings and add regression tests where needed.
- [x] Run shared policy and both engine host tests; run Android lint and build Gecko ARM64 release.
- [x] Set Android version 0.5.13 (24), verify the release manifest and signing certificate, publish the APK in the session artifacts.
- [x] Retain only the latest Gecko ARM64 APK. Leave unrelated files unchanged. Commit only when the user requests it.

## Review correction

The independent review identifies that HTML load completion does not prove that SessionView has loaded its API data. The readiness report above corrects this gap. It also reconciles unlocks where Android resumes an Activity before the keyguard is fully dismissed. This adds two fields to AndroidAppMenu's props, one optional field to the existing heartbeat, and no new polling or persistent state.

## Verification results

- Frontend suite: 4,538 passed; production build passed.
- Android release host tests: 68 shared policy, 67 Gecko, 67 WebView; no failures.
- Real notification and browser lifecycle scenario: passed on WebView and Gecko, API 35 emulator. Covers two sessions, two categories, instance isolation, SPA tabs, loading/error readiness, background, screen-off/unlock, notification taps and instance removal.
- Release lint: 0 errors; 29 Gecko warnings and 35 WebView warnings.
- Gecko ARM64 release 0.5.13 (24): built, non-debuggable, no test CA configuration, signing certificate unchanged.
- No backend code changes. The user owns server restarts.
