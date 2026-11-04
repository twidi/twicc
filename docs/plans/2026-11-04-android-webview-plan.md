# Android WebView Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user requests immediate implementation in this session. Do not create a branch or install host packages.

**Goal:** Deliver an Android shell for multiple TwiCC HTTPS instances with hard refresh.

**Architecture:** Native instance manager plus a WebView activity. Each instance owns a WebKit profile. The existing server supplies all application content.

**Tech Stack:** Kotlin 2.2.21, Android Gradle Plugin 8.13.2, Gradle 8.13, Android SDK 36, minimum API 29, target API 35, AndroidX WebKit 1.14.0.

**Spec:** `docs/plans/2026-11-04-android-webview-design.md`

## Global Constraints

- All implementation stays in `android/`, apart from these plan documents.
- English code, UI, and documentation. Stay on the current branch.
- Preserve host environment and other agents' changes. No package installation.
- Foreground web audio only. No native background subsystem.

## Review Focus

- Different ports or sibling hosts must not be treated as the configured origin.
- New windows and iframe navigation must not replace or escape the main TwiCC page.
- Force refresh must preserve authentication and preferences, including after failure.
- Switching instances must destroy the old WebView without cross-profile restore.
- Missing/old WebView, network failure, rotation, and canceled file selection must be recoverable.

### Task 1: Build and instance model

**Files:** `android/{settings.gradle.kts,build.gradle.kts,gradle.properties,gradlew,gradlew.bat}`, `android/gradle/wrapper/`, `android/app/build.gradle.kts`, `android/policy/`, `android/app/src/main/java/com/twicc/android/InstanceStore.kt`.

- [x] Add pinned build files and official Gradle wrapper.
- [x] Implement HTTPS-origin normalization, navigation policy and instance persistence.
- [x] Verify URL edge cases with JVM tests when toolchain permits.

### Task 2: Android UI and WebView

**Files:** Android manifest/resources and `MainActivity.kt`, `BrowserActivity.kt`.

**Interfaces:** Consume `InstanceUrl.parse`, `allowsNavigation`, and `InstanceStore` for persisted instances.

- [x] Implement instance forms, selection, editing, removal and last-instance launch.
- [x] Implement profiled WebView, foreground audio, external windows and back navigation.
- [x] Implement refresh, errors, file selection, downloads, lifecycle and insets.
- [x] Review cancellation, main-frame guards, URL boundaries, and profile ownership.

### Task 3: Verification and handoff

**Files:** `android/README.md`, `android/.gitignore`, `android/ci-build.yml`.

- [x] Document local build and sideload, signing, version floors, and device smoke tests.
- [x] Supply an opt-in workflow template without enabling repository CI automatically.
- [x] Run available checks; record exact missing-toolchain limits if compilation cannot run.
- [x] Review the final diff and report source/APK/test status accurately.

## Verification record

- URL policy tests: **4 passed**, 0 failures, 0 errors. These cover normalization,
  HTTPS/server-origin input rejection, origin boundaries, ports, and external schemes.
- Kotlin source compile smoke: **passed**, using the actual AndroidX WebKit 1.14.0
  classes and Robolectric Android 15 API classes in a temporary JVM project. All
  native source files compile. The generated BuildConfig is represented by a stub
  in this check. This does not compile Android resources or produce an APK.
- All Android XML files parse. Source whitespace and Gradle launcher shell syntax
  checks pass. The Gradle wrapper JAR checksum matches Gradle's published checksum.
- Independent review finds the popup renderer-exit handler missing; fixed. The
  reviewer confirms the fix. External-link dispatch now explicitly selects browser
  packages so verified app links do not redirect into other native apps.
- The initial full build was blocked by the missing JDK and Android SDK.
  The user then explicitly authorized installing both in an isolated directory.
- Installed Eclipse Temurin **17.0.21+9**, Android command-line tools **15859902**,
  Android SDK Platform **36**, Build Tools **35.0.0**, and the SDK's required
  Platform Tools. JDK and command-line tool archive SHA-256 values were verified.
- Full build command: `./gradlew :policy:test :app:lintDebug :app:assembleDebug`.
  Final result: **BUILD SUCCESSFUL**, 4 URL tests passed, **0 lint errors** and
  13 warnings (target/version suggestions, backup configuration, icon metadata,
  and English text internationalization). No feature-availability warnings remain.
- Lint initially could not recognize some indirect MULTI_PROFILE guards. Feature
  checks now directly guard each relevant call site. The resulting APK is rebuilt.
- `apksigner verify --verbose` verifies the final APK with v2 signing, one signer.
  Local signing key: `android/.signing/debug.keystore` (ignored, private file mode).
  Preserve this key and use the README's ANDROID_USER_HOME setting for updates.
- APK: `android/app/build/outputs/apk/debug/app-debug.apk`, **3,550,351 bytes**.
  Package `com.twicc.android`, version **0.1.0** (code 1), minimum API **29**,
  target API **35**. A copy and SHA-256 file are delivered in the session artifacts.
- APK SHA-256: `84f2b7e4f851348dadf3fe40a5175fef9496fbb39c737f0d776d021ea7849d6f`.
- Installation and real-phone smoke tests remain **unverified**. The user tests
  their HTTPS instance and phone using the README checklist.
- No branch, commit, server restart, migration, frontend change, or provider setting
  change is performed. Existing untracked plan files are left intact.

## Authorized toolchain and APK follow-up

- [x] Install a full JDK and the Android SDK Platform 36 / Build Tools 35.0.0.
- [x] Run `./gradlew :policy:test :app:lintDebug :app:assembleDebug` from `android/`.
- [x] Fix any Android resource, manifest, lint, or packaging issues revealed by that build.
- [x] Provide the resulting APK for installation on the user's phone.
- [ ] Run the README's real-device checks (requires the user's phone and server).

## Launcher icon update (0.1.1)

- User requests the real TwiCC icon instead of the placeholder T.
- Copy `frontend/public/android-chrome-512x512.png` without altering its artwork.
  Use the Web manifest background `#1a1a2e` and a 66/108 adaptive-icon safe area.
- Version code **2**, version name **0.1.1**. Lint and APK assembly pass.
- APK signature verifies and matches the 0.1.0 certificate, allowing an in-place update.
- Delivered APK size: **3,625,918 bytes**.
- SHA-256: `9d0301e5e316dd678d670db0100181039fb416507a5c0b1e305a6874b8293b69`.

## Keyboard inset correction (0.1.2)

- The user's recording shows a page-wide upward offset after the keyboard opens.
  The header disappears and an empty band remains above the keyboard until the
  user manually scrolls the page back. Extracted frames confirm this sequence.
- `fitSystemInsets` applies system bar, display cutout, and IME padding to the root
  but previously forwarded those same insets unchanged to WebView. This matches
  the duplicate inset handling described in Android's WebView documentation:
  https://developer.android.com/develop/ui/views/layout/webapps/understand-window-insets
- The authorized correction keeps root padding and forwards zero values for those
  handled inset types using `WindowInsets.Builder`. It does not return `CONSUMED`,
  so WebView continues to receive updates when the keyboard opens and closes.
  Android 10's existing `fitsSystemWindows` path is unchanged.
- Version code **3**, version name **0.1.2**. Build command:
  `android/gradlew -p android --no-daemon :policy:test :app:lintDebug :app:assembleDebug`.
  Result: **BUILD SUCCESSFUL** in 36 seconds. Kotlin compilation, lint, and APK
  assembly succeed. Lint: **0 errors, 13 warnings**. The unchanged URL policy test
  task is up-to-date; its existing result is 4 tests, 0 failures, 0 errors.
- `apksigner verify --verbose --print-certs` verifies the APK. Its signing certificate
  SHA-256 matches 0.1.1: `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a`.
- Delivered APK: `twicc-android-0.1.2-debug.apk`, **3,626,174 bytes**.
  SHA-256: `0d31a6a078d4e426fb9d29009f6d11541345e4ab3e7d45c25fa9e43e49990694`.
- Real-device confirmation remains pending. Install as an update, open and close
  the keyboard repeatedly, and check header visibility and composer position.
  If the offset remains, collect native scroll and JavaScript viewport measurements
  during the keyboard transition before making further changes.

## Keyboard animation synchronization (0.1.3)

- The user's second recording (`Screen_Recording_20261104_140309_TwiCC.mp4`)
  shows an early blank/grey lower region around 1.1 seconds, followed by a sudden
  page resize around 1.3 seconds. The header stays visible in the settled state.
  Extracted 15-fps frames and a 10-fps contact sheet document this regression case.
- The root only handles discrete `onApplyWindowInsets` events in 0.1.2. It has no
  animation callback. Android dispatches end-state insets before animation starts;
  `WindowInsetsAnimation.Callback.onProgress` supplies the intermediate heights:
  https://developer.android.com/reference/android/view/WindowInsetsAnimation.Callback
- On Android 11+, register a native animation callback in `fitSystemInsets`.
  Track prepared IME animations, defer the premature end-state padding, and apply
  the progress insets to the root padding every frame. Resize the actual WebView
  instead of translating the whole page, which would displace the header.
- Continue forwarding zero values for handled insets in both the ordinary and
  animation paths. Request final insets when the last tracked IME animation ends.
  Android 10 retains the existing `fitsSystemWindows` fallback. There are no new
  dependencies, frontend edits, or manual animation durations.
- Version **0.1.3** (code **4**). Same build command as 0.1.2:
  **BUILD SUCCESSFUL** in 41 seconds, **0 lint errors**, **13 warnings**.
  The unchanged URL policy tests are up-to-date. `apksigner verify` succeeds;
  its certificate matches 0.1.2, permitting an in-place update.
- Delivered APK: `twicc-android-0.1.3-debug.apk`, **3,628,258 bytes**.
  SHA-256: `2c92a196313c680841980afc692a16e7a752edebd62a824b2afe178e6b9fe14c`.
- Visual smoothness is not proven by compilation. The user must repeat keyboard
  opening and closing on the phone, including interrupted transitions and rotation.
  Check that the composer follows the keyboard, the header remains visible, and
  no blank band or final jump remains. WebView rendering is asynchronous, so a
  frame-perfect transition is not guaranteed by the native callback alone.

## White launcher background (0.1.4)

- The user requests a white launcher icon background. Set
  `ic_launcher_background` to `#FFFFFF`; preserve the existing robot artwork.
- Version **0.1.4**, version code **5**. Build command:
  `android/gradlew -p android --no-daemon :app:lintDebug :app:assembleDebug`.
  **BUILD SUCCESSFUL** in 37 seconds; lint: **0 errors, 13 warnings**.
- `aapt2 dump resources` verifies the packaged `ic_launcher_background` is
  `#ffffffff`. `apksigner verify` succeeds and its certificate matches 0.1.3.
- Delivered APK: `twicc-android-0.1.4-debug.apk`, **3,628,254 bytes**.
  SHA-256: `267325c31446290a760cbbf9d03afbc0af8717c55fca2066a9cd6838525a677b`.

## Resize after the keyboard settles (0.1.5)

- The user's third keyboard recording (`Screen_Recording_20261104_142314_TwiCC.mp4`)
  shows the per-frame approach producing repeated, delayed page adjustments during
  keyboard opening. A 15-fps contact sheet records the behavior. The user explicitly
  prefers Firefox's sequence: animate the keyboard, then resize the page once.
- Remove `applyPadding` from `onProgress`. Keep the existing layout throughout the
  IME animation, continue forwarding zeroed handled insets, and let the final
  `requestApplyInsets` in `onEnd` perform the resize. Opening and closing use the
  same sequence. Non-animated inset changes still resize immediately. No timer,
  JavaScript scroll manipulation, or frontend changes are added.
- The white adaptive icon background from 0.1.4 remains unchanged.
- Version **0.1.5**, code **6**. Build command:
  `android/gradlew -p android --no-daemon :app:lintDebug :app:assembleDebug`.
  **BUILD SUCCESSFUL** in 42 seconds. Lint: **0 errors, 13 warnings**.
  `apksigner verify` succeeds and the certificate matches 0.1.4.
  No policy test rerun; the policy code and its previous passing results are unchanged.
- Delivered APK: `twicc-android-0.1.5-debug.apk`, **3,628,162 bytes**.
  SHA-256: `4b2d938c3a249deb8a47756c6f15e6bdc10c79c6fc39cbcf217265d2574fc980`.
- Real-device verification remains necessary: keyboard animation must no longer
  cause repeated page relayouts. At rest, the header stays visible and the composer
  sits above the keyboard. Closing restores the full height after animation.
- The separately reported Chrome page-scroll behavior remains undiagnosed. Both
  `frontend/index.html` and the local built `src/twicc/static/frontend/index.html`
  already specify `interactive-widget=resizes-content`, the documented Chrome
  opt-in for resizing both viewports. No recent edit to that setting is found.
  Reference: https://developer.chrome.com/blog/viewport-resize-behavior
  The APK-only correction cannot change Chrome's page layout behavior.
