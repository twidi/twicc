# Android notifications implementation plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation. Track each task below.

**Goal:** Deliver existing browser-notification events as Android push notifications, without depending on an open page.

**Architecture:** Reuse the validated UnifiedPush/embedded FCM transport and notification-tap routing. Reuse existing backend event detection and browser presence windows. A private per-device registration stores notification choices; a scoped grant lets native Android renew its endpoint without page JavaScript.

**Tech stack:** Django, Vue/Pinia, pywebpush 2.5.0, UnifiedPush connector 3.3.5, embedded FCM 3.1.0, Android JobScheduler and OkHttp.

**Spec:** The approved requirements in this conversation: four existing notification families, local event switches, suppress push while any ordinary browser is active, automatic registration, common native code for both engines.

## Global constraints

- Do not restart the user's backend or send a phone test without their readiness signal.
- Keep ordinary browser notifications and Apprise behavior unchanged.
- Reuse the desktop five-minute and mobile thirty-second presence windows. Android presence still counts for Apprise, but never suppresses Android push.
- No Firebase project, relay, database migration, foreground service, silent audio, or notification history.
- Keep private registration files and credentials private; renew subscriptions without a page or login cookie.
- Preserve per-instance profiles and notification navigation. Only retain Gecko ARM64 release APKs.

## Review focus

- Endpoint rotation while JavaScript is suspended: native persisted network job updates the server with a device-scoped grant.
- Preference changes and instance removal during delivery: native checks current choices, removed instances ignore messages, server updates cannot resurrect a removed grant.
- Authentication changes: password-bound grants stop working after password rotation; logout removes registration.
- Browser presence and duplicate alerts: Android never uses browser notifications; Android input cannot extend browser presence.
- Service errors and process restarts: bounded network timeouts, 404/410 subscription retirement, no event replay or persisted error history.

## Task 1: Backend registration and transport

Files: `src/twicc/android_push/` (storage, views, transport), `src/twicc/urls.py`, `pyproject.toml`, `uv.lock`, `tests/test_android_push.py`.

Interfaces: authenticated `GET/POST /api/android-push/`; native grant `PUT/DELETE /api/auth/android-push/`. POST returns `{protocol,public_key,token}`. Registrations contain device UUID, name, four boolean preferences, permission state, password fingerprint, token digest, and subscription. `dispatch(kind,title,body,path=None,action=None,detail=None)` queues a bounded encrypted push.

- [x] Write failing tests for private persistent keys, login gating, preference validation, grant update/delete, password change and expired subscriptions.
- [x] Run targeted pytest; verify failures.
- [x] Implement automatic VAPID initialization and atomic private registration writes. Keep endpoint validation restricted to the selected FCM transport.
- [x] Send through pywebpush with TTL 300, high urgency, timeout 15; never log endpoints or grants. Retire only the failed subscription snapshot on 404/410.
- [x] Verify tests against the real encryption library with HTTP transport intercepted.

## Task 2: Existing event detection and browser presence

Files: `src/twicc/external_notifications.py`, `src/twicc/presence.py`, `src/twicc/asgi.py`, `frontend/src/utils/presence.js`, backend regression tests.

Interfaces: extend `presence.touch(device_class,client_kind,client_id)` and add `is_browser_present()`. Preserve current Apprise presence functions. Dispatch kinds `user_turn`, `pending_request`, `extra_usage`, `peer` before the Apprise-only target gates.

- [x] Test all four event families without Apprise targets, session mute/hidden/resync suppression, event preferences and ordinary-browser presence.
- [x] Implement reuse of existing titles, bodies, rising-edge detection and session links. Carry peer inbox/manager actions and extra-usage home action.
- [x] Classify presence per WebSocket client; prune expired browser observations and exclude Android observations.
- [x] Run event/presence regressions and preserve existing external-notification tests.

## Task 3: Native registration lifecycle and notifications

Files: shared Android `push/`, manifest, bridge, activity routing, notification navigation policy and tests.

Interfaces: bridge `push.state`, `push.preferences`, `push.configure`, `push.permission`, `push.remove`. Configure persists the grant, VAPID key and choices. A single persisted JobScheduler job drains pending registration updates and deletions; it retries transient failures with network constraints.

- [x] Test payload validation, preference suppression, notification destinations and latest-tap acknowledgement.
- [x] Promote prototype classes to production names. Register automatically and upload endpoint rotations from native callbacks.
- [x] Create separate channels for the four event families, independent of TTS. Honor Android permission and local choices on reception.
- [x] Preserve live documents when tapping notifications; route session, home, peer inbox and pairing-manager destinations through the existing bridge.
- [x] Verify both engines compile and run targeted native tests.

## Task 4: Settings, enrollment and acceptance

Files: `frontend/src/utils/androidPush.js`, Android runtime state helper, `AndroidAppMenu.vue`, `NotificationSettings.vue`, notification sound/browser helper, auth logout, tests and Android README.

- [x] Test automatic authenticated enrollment, serialized preference synchronization, denied permission, ordinary-browser isolation, logout and removal.
- [x] Initialize on Android capability discovery; no enrollment button or prototype dialog. Reuse four local browser-notification choices, labeled Android notifications inside the app.
- [x] Request Android permission when a chosen event needs it; provide settings access when blocked. Show status in settings, without recurring error toasts.
- [x] Remove obsolete prototype paths/files and document current setup and limits.
- [x] Run frontend suite/build, relevant backend suites, native suites/lint for both engines, emulator routing/notification checks.
- [x] Generate and publish only Gecko ARM64 release APK. Ask the user to restart the backend and install before phone acceptance.
