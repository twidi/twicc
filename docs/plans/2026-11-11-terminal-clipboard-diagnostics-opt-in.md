# Local Terminal Clipboard Diagnostics

**Goal:** Keep one shared APK. Enable terminal clipboard diagnostics only after a local hidden gesture.

**Approved design:** The user chooses local activation over a build flag and authorizes implementation with “let's go”.
Diagnostics default to disabled, including upgrades from the previous always-enabled implementation.
The installation keeps only the enabled preference. Error records remain in process memory.
Seven taps on the native instance-manager title toggle the preference.
Each tap must follow the previous tap within one second. The sequence resets after a toggle.
The confirmation and diagnostic menu remain English, as requested for this personal diagnostic feature.
Disabling clears recorded errors. Both menus, native collection, and native export follow the preference.
The frontend requires the advertised capability before collecting error context.
Normal web clipboard operations and their results remain unchanged.

**Alternatives:** A build flag needs a special APK and can be forgotten. Local activation works with the public APK.

**Architecture:** Android preferences are authoritative. A process-local journal gates writes and clears on disable.
The existing native bridge advertises the capability only when enabled.
The existing browser client caches capabilities from successful getState replies for synchronous clipboard checks.
The instance manager closes the browser activity, so returning opens a fresh document and reads the new capability.

**Tech stack:** Kotlin, Android SharedPreferences, Vue, existing Android app bridge, Node tests, JUnit.

## Implementation Plan

### Task 1: Gate native diagnostics and add the hidden gesture

- [x] Add regression tests for default-off collection, enable/disable, clearing and late errors.
- [x] Run the focused Kotlin tests and confirm the new behavior fails before implementation.
- [x] Add the local preference, seven-tap gesture, and English confirmation.
- [x] Gate the native capability, menu, recording, and export.
- [x] Add and compile an instrumentation test for seven taps, timeout, recreation, and disabling. No device is connected.

### Task 2: Honor the native capability in the terminal

- [x] Add client tests for absent, enabled, and revoked capabilities on both transports.
- [x] Cache capabilities only from correlated successful getState replies; clear them on close.
- [x] Require the capability in useTerminal before observing clipboard failures.
- [x] Run focused frontend tests and the frontend suite.

### Task 3: Verify and deliver

- [x] Document the activation path in android/README.md.
- [x] Review the diff for clipboard behavior changes, disabled exports, and retained error data.
- [x] Run npm run build and the documented local Android build wrapper.
- [x] Keep only the published Gecko APK in android/dist. Preserve the signing key and unrelated changes.

## Review Focus

- Previous installs have no preference: diagnostics must become disabled.
- Errors that finish after disabling must not repopulate the journal.
- Hidden menu actions called directly must not export when disabled.
- Non-state bridge replies and foreign replies must not enable diagnostics.
- Changing instances must not reset the installation preference.

## Validation Record

- RED: the new client tests fail because supports() does not exist; Kotlin tests fail because enabled/configure do not exist.
- Frontend suite: 5,308 tests pass. After adding the terminal wiring regression, all 39 focused tests pass.
- Frontend production build passes. Android i18n source and fingerprints pass verification.
- The documented local wrapper passes policy tests (94), Gecko app unit tests (112), lint, and release assembly.
- Android lint reports zero errors and 70 warnings.
- The gesture instrumentation test compiles. Execution and physical menu/export acceptance remain pending: no device is connected.
- Independent review finds no actionable defect. Additional tests cover bridge writes while disabled and the tap timeout.
- APK manifest confirms versionName 0.5.30, versionCode 41, ARM64. Checksum and the existing signing certificate verify.
- Exactly one Gecko APK remains: android/dist/gecko/64/release/twicc-gecko-64-v0.5.30-release.apk.
