# TwiCC for Android — GeckoView experiment

**Experimental prototype, not a final release.** Version 0.2.0 replaces Android
System WebView with Mozilla GeckoView. The goal is to compare TwiCC rendering,
scrolling, and keyboard transitions on a real Android phone. Background playback
is not implemented.

The APK loads your existing TwiCC HTTPS server. It includes the Gecko engine,
not the TwiCC frontend or backend. Server updates appear on the next page load.
Firefox does not need to be installed. The bundled engine makes this APK much
larger than the earlier Chromium shell. Engine security updates require a new
APK; updating Firefox or Android System WebView does not update this engine.

## Update from the Chromium experiment

- Install this APK over 0.1.5 with the same application ID and signing key.
- Saved instance names, addresses, last pages, and menu position remain.
- **Sign in again.** Gecko does not reuse Chromium cookies, localStorage, or
  IndexedDB. Web preferences must be set again for this engine.
- Chromium storage remains on disk. This experiment does not transfer or delete
  it. Removing an instance in Gecko requests deletion of its Gecko context only.
- The previous Chromium checkpoint is commit `3a8e359`. Android normally blocks
  installing an older version code over a newer APK. Uninstalling clears app data.

The launcher icon remains the TwiCC robot from
`frontend/public/android-chrome-512x512.png`, on a white adaptive background.

## Requirements and pinned dependencies

- Android **10 or newer** (API 29). No Chrome/WebView provider requirement.
- An existing TwiCC server with a valid **HTTPS** certificate, usually a tunnel.
- GeckoView stable **159.0.20261102144711**, from Mozilla's Maven repository.
- Build: JDK **17**, Gradle **9.6.0**, Android Gradle plugin **9.4.0**, Kotlin
  **2.4.20**, Android SDK Platform **37.2**, Build Tools **36.0.0**.
- `minSdk = 29`, `targetSdk = 35`. The compile SDK upgrade does not raise the
  Android version required to install this app.
- No Play Store account is required to build and install this debug APK.

GeckoView uses MPL 2.0 and other bundled component licenses. Mozilla provides the
[versioned source archive](https://maven.mozilla.org/maven2/org/mozilla/geckoview/geckoview/159.0.20261102144711/geckoview-159.0.20261102144711-sources.jar)
and [GeckoView documentation](https://firefox-source-docs.mozilla.org/mobile/android/geckoview/).
This experiment does not modify Gecko sources.

## Build and install

Open `android/` in Android Studio with a compatible SDK. Or run:

```sh
cd android
export ANDROID_HOME=/absolute/path/to/Android/Sdk
export ANDROID_USER_HOME="$PWD/.signing"
sdkmanager 'platforms;android-37.2' 'build-tools;36.0.0'
./gradlew --no-daemon :policy:test :app:lintDebug :app:assembleDebug
```

Use a current SDK command-line tool. Platform 37 uses the package name `37.2`.
Gradle downloads the pinned distribution and dependencies on its first run.
APKs are in `android/app/build/outputs/apk/debug/`:

- `app-arm64-v8a-debug.apk`: modern ARM64 phones.
- `app-armeabi-v7a-debug.apk`: older 32-bit ARM phones.
- `app-x86_64-debug.apk`: x86-64 devices and emulators.
- `app-universal-debug.apk`: all three architectures, with a larger download.

Transfer the APK to your phone and permit installation from that source. Or run
from `android/` with USB debugging enabled:

```sh
adb install -r app/build/outputs/apk/debug/app-arm64-v8a-debug.apk
```

Preserve the ignored `.signing/` directory. An update requires the same
`com.twicc.android` application ID and signing key. Another computer or fresh CI
runner normally creates a different debug key. Debug signing is for personal
testing; keep a stable private release key for maintained distribution.

`ci-build.yml` remains an opt-in template. Copy it to
`.github/workflows/android.yml` to enable a manually triggered build.

URL and filename policy tests can run without an Android SDK:

```sh
cd android
./gradlew --configure-on-demand :policy:test
```

## Instances and native controls

1. Select **Add instance**.
2. Enter an optional name and an origin such as `https://twicc.example.com:8443`.
3. Save, open the instance, and use TwiCC's normal login page.

Use the server root. HTTP, embedded credentials, query strings, fragments, and
paths are rejected. Internationalized hostnames must use ASCII/punycode.

One process-wide `GeckoRuntime` owns the engine. Each instance has a persistent
`GeckoSessionSettings.contextId` based on its UUID. This partitions cookies and
Web storage, including instances on the same host with different ports. Switching
instances closes the previous session. The last page is restored at launch.

The movable **⋮** button provides:

- The app version and engine name.
- **Instances:** open, add, edit, or remove servers.
- **Reload page:** load the current page normally.
- **Force refresh (keep login):** replace the view and session, and load with
  Gecko's `LOAD_FLAGS_BYPASS_CACHE`. The context ID remains unchanged. Login,
  localStorage, and IndexedDB remain. Confirming discards unsaved page state.
- **Server home:** load the configured origin.
- **Open in browser:** open the current URL in the system browser.

Force refresh bypasses the HTTP resource cache. It does not clear site data or
unregister service workers. TwiCC currently does not register a service worker.

Android Back follows the session history, then returns to the instance list.
Rotation keeps the current session. Process death restores the URL, not unsaved
in-memory work. Rename preserves the context. An address change creates a new
UUID/context. Removal closes the previous session and requests asynchronous
context deletion through `StorageController.clearDataForSessionContext`.

## Links, files, and audio

- Same-origin top-level navigation stays in TwiCC. The port is part of the origin.
- External HTTP(S) links open a system browser. `mailto:` and `tel:` use their
  Android handlers. Verified app links still use the selected browser.
- User-initiated new-window links open externally, including same-origin links.
  Unsolicited popups are blocked. Scripts that first open an empty window and
  navigate it later are not supported by this small shell.
- Subframe navigation and resources remain Gecko-managed. Artifacts and TwiCC's
  embedded browser stay embedded.
- File inputs use Android's file picker for single or multiple files. The app
  copies provider content to a private temporary file off the UI thread because
  Gecko's upload prompt requires file paths. Copies remain available for the
  session and are removed after it closes or when the next process starts.
- Native JavaScript alert, confirm, text, and select prompts are supported.
  Folder upload, camera capture, HTTP Basic Auth UI, and custom date/color picker
  prompts are not implemented. TwiCC's own server login form remains supported.
- Downloads use the **actual Gecko response body**, including its authenticated
  context and any POST response. There is no second HTTP request. Files go into
  Android **Downloads** through MediaStore. No broad storage permission is needed.
  HTTPS downloads are supported. `blob:`/`data:` exports are not supported by
  this shell; the tested blob export does not reach a saved file. Use the browser
  for these exports.
- A native message reports download start/completion/failure. There is no
  DownloadManager notification or background service. Downloads can stop if
  Android kills the app process. Failed downloads remove their pending file.
- Foreground HTML audio and Web Audio remain web-managed. Audible and inaudible
  autoplay permissions are allowed for the saved instance origin. Formats and
  Web Audio activation require device validation.
- No native microphone, speech recognition, native TTS, push notifications,
  battery exemption, or background playback guarantee is added. An invisible
  session becomes inactive. GeckoView does not solve background WebSocket/audio
  requirements by itself.

The native layout retains 0.1.5's keyboard policy: on Android 11+, keep the layout
height during keyboard opening/closing, then resize once after the animation.
The root applies system/keyboard padding and consumes those descendant insets.
Gecko also observes decor insets internally. TwiCC's
`interactive-widget=resizes-content` viewport uses the resized surface for its
layout. Android 10 retains its existing resize fallback. This experiment does
not change the frontend or inject viewport JavaScript.

## Phone smoke test

1. Update from 0.1.5. Confirm the instance list remains and sign in again.
2. Open two instances, including two ports on one host if available. Verify
   separate login and localStorage after restart.
3. Open a session, follow internal links, use Back, rotate, and relaunch.
4. Force refresh. Verify login/preferences survive. Cancel confirmation with a
   draft present and verify the draft remains.
5. Open an external link and a `_blank` link. Verify the system browser opens.
6. Open an artifact and embedded browser. Verify they stay embedded.
7. Upload single and multiple files from Downloads and a document provider.
   Cancel the picker and retry. Verify filenames and content.
8. Download an authenticated attachment. Verify bytes and filename in Downloads.
   Test a blob/data export separately if needed.
9. Play normal and streaming PCM TTS in the foreground.
10. Open/close the keyboard repeatedly and interrupt a transition. Check the
    composer, page header, and sidebar header. Compare scrolling and keyboard
    transitions against Firefox and the old Chromium APK on the same phone.
11. Stop the tunnel, reload, restore it, and retry. Verify certificate failures
    cannot be bypassed in the app.
12. Rename, change an address, and remove an instance. Check last-instance fallback.

## Verification

Build, lint, APK inspection, and emulator results are recorded in
`docs/plans/2026-11-04-android-geckoview-plan.md` in the repository root.
On 2026-11-04, the user confirms substantially better interface fluidity on their
Android phone than with the Chromium version. Real TwiCC streaming TTS and the
remaining phone smoke tests still need validation.
