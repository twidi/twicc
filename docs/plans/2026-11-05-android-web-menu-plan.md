# Android Web menu implementation plan

> **For agentic workers:** Use executing-plans inline, then request one independent review. The user authorizes implementation with “let's go”; do not request another approval.

**Goal:** Render the Android controls inside TwiCC and switch saved instances with persisted URLs.

**Architecture:** One Vue menu and client; one native dispatcher; two flavor transports. A visibility lease controls the native fallback.

**Tech Stack:** Existing Vue/Web Awesome, Kotlin, AndroidX WebKit 1.14.0, GeckoView 159. No new dependencies.

**Spec:** `docs/plans/2026-11-05-android-web-menu-design.md`.

## Global constraints

- Preserve unrelated work and staged changes. Stay in this checkout. Do not commit unless requested.
- Keep com.twidi.twicc, the signing key, both engine flavors, and keyboard behavior. Version 0.4.0/code 9.
- Native instance management stays. No background service or backend changes/restart.

## Review focus

- An iframe, other origin, stale document, or closed session must not gain native command access.
- Switching must save the outgoing URL under the outgoing UUID, including lifecycle callbacks.
- Login, old frontend, renderer failure, and hidden rail must retain native controls.
- Replies/actions must not mutate a replacement session; menu-owner cleanup must not revoke another owner.
- Gecko extension installation failure must retain normal navigation and the native fallback.

## Task 1: Protocol, client, and native dispatcher

- [x] Write failing JS client tests for request correlation, version/origin/frame rejection, unavailable transport, and native errors.
- [x] Write failing JVM tests for the command allowlist, frame/origin checks, and fallback lease ownership/expiry.
- [x] Implement `androidAppClient.js`, `AndroidAppPolicy.kt`, and shared native `AndroidAppBridge.kt`.
- [x] Run targeted Node tests and `:policy:test`; expect all pass.

## Task 2: Native integration and flavor transports

- [x] Add `BrowserEvents.onAppMessage(String): String` and bridge handling to both flavor sessions.
- [x] Add the bundled Gecko extension with top-level-only messaging; initialize it before the first page load.
- [x] Integrate Activity commands, instance switch, persisted safe URLs, fallback lease, and native fallback instance selection.
- [x] Compile both flavors; expect success. Test native controls and protocol on emulator pages.

## Task 3: Web menu

- [x] Write failing rendered-menu tests for absence outside Android, active instance, action values, and first-rail placement.
- [x] Implement `AndroidAppMenu.vue`, with one shared client, visibility reporting, Web Awesome menu/confirmation, and error feedback.
- [x] Insert before Home in SidebarRail and update its structural test for the added child.
- [x] Run client/menu/rail tests and the frontend production build; expect success.

## Task 4: Review and delivery

- [x] Update the current-state Android README and build version to 0.4.0/code 9.
- [x] Run both APK lint/assemble tasks, policy tests, and relevant frontend tests.
- [x] Request one independent read-only review; fix important findings and rerun relevant checks.
- [x] Emulator-test both transports, fallback, instance switching/URLs after restart, and closed/iframe requests.
- [x] Verify signatures/package identity; deliver ARM64 APKs and checksums. Record checks and remaining device limitations.

## Execution record

- User approves the Web menu, native instance manager, direct switching, URL restoration, and native floating fallback when Web UI is unavailable.
- Pre-flight: both transports consume the same JSON protocol through BrowserEvents; the menu consumes the same state snapshot regardless of engine.

### Verification results

- Focused frontend suite: 51 tests pass. Production frontend build succeeds.
- JVM policy suite: 14 tests pass, including the old-owner lease deadline regression.
- Both APK flavors assemble and pass Android lint. Package: com.twidi.twicc, version 0.4.0, code 9.
- One independent read-only review finds one fallback timer issue. The regression fails before remainingMs exists, then passes after the fix. The Activity schedules the remaining lease time.
- API 35 emulator, both engines: bridge state, rendered menu, active instance, direct switch, independent saved URLs, restart restoration, hidden-rail fallback, force-refresh confirmation, and retained cookies/localStorage pass. An iframe receives no native response. Gecko's unattended lease expiry and WebView's old-owner cleanup deadline pass.
- ARM64 APK signatures match the existing debug certificate, SHA-256 f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a.
- Physical-phone fluidity, renderer death, actual extension installation failure, and delayed transport delivery across same-origin navigation are not reproduced.
- The user asks about a fixed top-left logo on pages without a rail. This is feasible. Placement changes are not part of this implementation.
