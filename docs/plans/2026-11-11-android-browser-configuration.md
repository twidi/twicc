# Android browser configuration continuity

## Design

Keep BrowserActivity and its browser session alive across every public configuration change supported by compileSdk 37.2. Update native resources in place. Never rebuild, detach, or reload the browser for configuration changes. Preserve prompt input, selection, pending callbacks, and file picker requests. Keep MainActivity's native-screen recreation behavior.

The approved approach extends configChanges and handles native updates. Retaining an Activity-bound browser across destroyed activities adds lifecycle and context ownership risks. Reloading and restoring only the URL loses document state.

Process death and renderer crashes still require recovery. Diagnostic logs identify lifecycle recreation, renderer failure, and explicit native reloads without recording URLs or document content.

## Implementation plan

- [x] Add configuration coverage and browser/prompt continuity instrumentation tests.
- [x] Extend the BrowserActivity manifest declaration.
- [x] Add native configuration adaptation for dimensions, fonts, direction, colors, and open dialogs without replacing views.
- [x] Handle configuration callbacks without touching the browser session. Add lifecycle and reload diagnostics.
- [x] Compile and run available unit checks for both engines. Compile instrumentation tests; run device tests if a device is available.

## Review focus

Bluetooth keyboard reconnect, repeated density/font changes without cumulative drift, open text prompt selection, pending file picker, system theme change, and renderer recovery behavior. Verify browser instance and document request counts remain unchanged during configuration callbacks.

## Verification

- Both engines compile. Unit tests pass: 109 per engine. Policy tests pass (93 tests, cached).
- Both instrumentation suites compile. Added tests cover manifest flags, browser identity, pending prompt input/selection/callbacks, and density/font round trips.
- Lint passes for both engines, with existing warnings. `git diff --check` passes.
- The standard verification initially fails because committed Android i18n fingerprints are stale (`frontend/src/i18n/locales/de.json`). Generation cannot run because the frontend dependency `@intlify/message-compiler` is absent. No dependencies or catalogs are changed.
- The successful verification command excludes only `:app:verifyAndroidI18n`. This is compilation/test validation, not release publication.
- No device is connected. Instrumentation tests and physical Bluetooth/system configuration changes remain unverified at runtime.
- Configuration callbacks dismiss the transient native PopupMenu because its public API cannot update its styling in place. Dialogs remain the same objects.
- Diagnostic tag: `BrowserLifecycle`. Inspect with `adb logcat -s BrowserLifecycle`; messages contain no URLs or page content.

Follow-up: the i18n fingerprint issue is resolved in `2026-11-11-android-i18n-fingerprints.md`. Gradle verification and both engine unit suites now pass without task exclusions.
