# TwiCC for Android

**Experimental Android client.** Build one app with either
**Android System WebView** or **Mozilla GeckoView**.
The engine is selected at build time. Both builds use `com.twidi.twicc`, the
same version code, app name, icon, native preferences, and debug signing key.
Installing one build replaces the other. There is no runtime engine selector.

The APK opens an existing TwiCC **HTTPS** server, usually through a tunnel.
It does not include the TwiCC frontend or backend. Server updates appear on
page reload. Foreground audio is web-managed. Background audio and
persistent background WebSockets are not implemented.

## Choosing an engine

| Build | Choose it when | Trade-offs |
| --- | --- | --- |
| **WebView** | You want a small APK and browser engine updates through Android System WebView. | Requires a provider with `MULTI_PROFILE` support. Rendering depends on the installed provider. |
| **Gecko** | You want an independent browser engine or want to improve TwiCC interface fluidity. | Includes the engine, which makes the APK much larger. Engine updates require a new APK. |

Try **Gecko** if WebView scrolling or keyboard interactions feel slow on your phone.
Gecko can improve TwiCC interface fluidity. Performance depends on the device
and engine version; Gecko is not guaranteed to be faster on every phone.
Compare scrolling, keyboard transitions, and foreground audio on your device.

Choose WebView if its interface runs smoothly and you prefer a smaller download.
Choose Gecko if it gives you smoother interactions or you need independence
from the installed WebView provider. Firefox does not need to be installed.

Both builds provide the same native controls. Neither build guarantees
background JavaScript, WebSocket connections, or audio playback.

## Package identity and engine replacement

- The application ID is **`com.twidi.twicc`** for both engines.
- WebView/Gecko builds replace each other with `adb install -r`, if their
  signing certificates match and their version codes permit the update.
- Instance addresses, names, last pages, and menu position remain across engine
  replacement. Cookies, localStorage, and IndexedDB are **engine-specific**.
  Sign in separately in each engine. Switching back restores that engine's data.
- Removing an instance queues storage deletion separately for both engines.
  The installed engine requests its cleanup now or at the next cold start.
  The other engine requests its cleanup when that build next runs. A deleted
  instance UUID is never reused.
- The launcher uses the TwiCC robot from
  `frontend/public/android-chrome-512x512.png`, with a white adaptive background.

## Requirements

- Android **10+** (API 29), with a valid HTTPS server certificate.
- WebView build: a current Android System WebView provider with AndroidX
  `MULTI_PROFILE` support. Updating Chrome/System WebView updates this engine.
- Gecko build: no Firefox installation required. The APK bundles GeckoView
  **159.0.20261102144711**. Security updates require rebuilding the APK;
  updating Firefox or Android System WebView does not update bundled Gecko.
- Build: JDK **17**, Gradle **9.6.0**, AGP **9.4.0**, Kotlin **2.4.20**, SDK
  Platform **37.2**, Build Tools **36.0.0**. `minSdk = 29`, `targetSdk = 35`.
- AndroidX WebKit **1.14.0** belongs only to the WebView flavor. GeckoView
  belongs only to the Gecko flavor. Dependencies are pinned.
- No Play Store account is required to build and install debug APKs.

GeckoView uses MPL 2.0 and bundled component licenses. This app does not modify
Gecko sources. See the [versioned source archive](https://maven.mozilla.org/maven2/org/mozilla/geckoview/geckoview/159.0.20261102144711/geckoview-159.0.20261102144711-sources.jar)
and [GeckoView documentation](https://firefox-source-docs.mozilla.org/mobile/android/geckoview/).

## Build and install

Install the SDK components with current Android command-line tools:

```sh
export JAVA_HOME=/absolute/path/to/jdk-17
export ANDROID_HOME=/absolute/path/to/Android/Sdk
export PATH="$JAVA_HOME/bin:$PATH"
sdkmanager 'platforms;android-37.2' 'build-tools;36.0.0'
```

From the repository root:

```sh
./android/build.sh webview
./android/build.sh gecko
```

The script also works from another directory through its absolute path. It runs
common policy tests, flavor lint, and flavor assembly. It collects APKs and
`SHA256SUMS` in `android/dist/webview/` or `android/dist/gecko/`.
Gradle downloads its distribution and dependencies on the first build.
The WebView build does not download the Gecko dependency.

Each directory contains these files, with `<engine>` replaced by the flavor:

- `app-<engine>-arm64-v8a-debug.apk`: modern ARM64 phones.
- `app-<engine>-armeabi-v7a-debug.apk`: older 32-bit ARM phones.
- `app-<engine>-x86_64-debug.apk`: x86-64 devices and emulators.
- `app-<engine>-universal-debug.apk`: all three architectures.

Gecko APKs are much larger because they include the browser engine. Transfer
the selected APK to the phone and permit installation from that source. Or use
USB debugging:

```sh
adb install -r android/dist/gecko/app-gecko-arm64-v8a-debug.apk
# Replace the same installation with WebView:
adb install -r android/dist/webview/app-webview-arm64-v8a-debug.apk
```

**Preserve `android/.signing/`.** The script sets `ANDROID_USER_HOME` there to
reuse the same ignored debug key. Another computer or fresh CI runner normally
creates a different key. A certificate mismatch prevents replacement. Debug
signing is for personal tests; maintained distribution needs a stable private
release key. Android Studio/direct Gradle users must set the same
`ANDROID_USER_HOME` to share the script's key.

Direct Gradle commands:

```sh
cd android
export ANDROID_USER_HOME="$PWD/.signing"
./gradlew --no-daemon :policy:test :app:lintWebviewDebug :app:assembleWebviewDebug
./gradlew --no-daemon :policy:test :app:lintGeckoDebug :app:assembleGeckoDebug
# Policy tests only, without Android SDK configuration:
./gradlew --configure-on-demand :policy:test
```

Direct APK outputs are in `app/build/outputs/apk/<engine>/debug/`.
Open `android/` in Android Studio and select `webviewDebug` or `geckoDebug`.
The opt-in `ci-build.yml` template builds both flavors sequentially in one job,
with one signing key. Copy it to `.github/workflows/android.yml` to enable it.
Each workflow run needs a securely restored signing key for updates.

## Source structure

```text
android/app/src/
  main/
    AndroidManifest.xml, res/                 shared app identity and resources
    java/com/twidi/twicc/
      BrowserActivity.kt                     layout, menu, navigation policy, Back
      MainActivity.kt, InstanceStore.kt       instance manager and preferences
      NativeUi.kt                            system bars and keyboard layout
      browser/                               engine/session/event contracts
      platform/                              links, dialogs, file picker, downloads
  webview/java/com/twidi/twicc/
    browser/EngineProvider.kt                flavor factory
    engines/webview/                         profiles, settings, WebView callbacks
  gecko/java/com/twidi/twicc/
    browser/EngineProvider.kt                flavor factory
    engines/gecko/                           runtime, sessions, Gecko delegates/uploads
android/policy/                              shared JVM URL/filename/navigation rules
```

The two factories have the same qualified name. Gradle compiles exactly one.
Shared activities never import engine APIs. `BrowserEngine` creates a
`BrowserSession` and requests storage deletion. `BrowserSession` exposes a view,
load, Back, visibility, and close. `BrowserEvents` carries normalized data.
Adapters translate browser callbacks and suppress events after close.
Gecko owns one process-wide `GeckoRuntime`. Instance UUIDs select
`GeckoSessionSettings.contextId` or the AndroidX `WebViewCompat` profile.

`NativeRequests` owns Android dialogs, DocumentsUI, cancellation, and unique
request codes. Gecko alone copies selected content URIs into private temporary
files off the UI thread. Closing a session cancels pending requests. Temporary
Gecko files are removed after session close or at the next process start.

## Instances and controls

Add an optional name and a server origin, for example
`https://twicc.example.com:8443`. HTTP, embedded credentials, paths, query strings,
and fragments are rejected. Internationalized hostnames use ASCII/punycode.
Each instance UUID gets its own persistent WebView profile or Gecko context.
The port is part of the origin. Renaming preserves storage; changing an address
creates a new UUID. The last safe same-origin URL is restored after relaunch.

The movable **⋮** menu provides:

- Version and engine name.
- Instance manager.
- Normal reload.
- **Force refresh (keep login):** replace the browser view/session and bypass
  resource cache. Cookies, localStorage, and IndexedDB remain. Unsaved text can
  be lost. This does not unregister service workers; TwiCC has none currently.
- Server home and Open in browser.

Back uses browser history, then returns to the instance manager. Rotation keeps
the session. Process death restores the URL, not unsaved in-memory work.
WebView profile deletion can wait until cold start. Gecko context deletion is
asynchronous; its API exposes no completion callback.

Both engines use the same keyboard policy. Android 11+ keeps the layout height
during keyboard animation and resizes once at the end. The root handles and
consumes system/IME insets. Android 10 uses a resize fallback. No viewport
JavaScript is injected. Rendering and scrolling can differ by engine.

## Links, files, and audio

- Same-origin main-frame links stay inside. Other origins and user-requested
  new windows open externally. Unsolicited popups are blocked. Opening an empty
  window and navigating it later is not supported by this shell. HTTP(S) links use
  a system browser, including verified app links. `mailto:`/`tel:` use handlers.
- Embedded browsers/artifact iframes are engine-managed.
- Single/multiple file inputs use DocumentsUI without broad storage permission.
  Folder upload and camera capture are not implemented. Gecko supports native
  alert/confirm/text/select prompts; WebView handles select controls itself.
  Gecko HTTP Basic Auth UI and custom date/color prompts are not implemented.
  TwiCC's own server login form is supported.
- WebView downloads use DownloadManager with the adapter's User-Agent and
  same-origin cookies. Android shows progress/completion notifications.
  This is a second HTTP GET; POST response download bodies are not preserved.
- Gecko downloads save the actual authenticated response stream to Downloads
  through MediaStore, including POST bodies. Native messages report progress.
  No DownloadManager notification or background service exists for this path.
  Process death can stop a stream download; failed saves remove pending files.
- HTTPS downloads are supported. Use a full browser to save `blob:`/`data:` exports.
- Foreground HTML audio/Web Audio are engine-managed. Gecko allows autoplay
  for the instance origin. WebView disables the playback gesture requirement.
  Formats and streaming TTS require validation on your phone.
- The app does not provide native microphone access, speech recognition, native
  TTS, push notifications, battery exemption, or guaranteed background WebSocket/audio execution.

## Phone smoke test

1. Install one flavor, add two instances, and sign in. Check isolation by port.
2. Replace it with the other flavor. Check native settings remain. Sign in again.
3. Switch back. Check the first engine's cookies/preferences return.
4. Test internal/external/new-window links, embedded artifacts, Back, and rotation.
5. Force refresh. Check login/preferences remain and unsaved-state confirmation.
6. Upload one/multiple files. Cancel the picker, retry, and verify names/bytes.
7. Download an authenticated attachment and verify its bytes/name in Downloads.
8. Test foreground normal/streaming TTS, scrolling, and keyboard transitions.
9. Stop the tunnel and retry. Check invalid certificates cannot be bypassed.
10. Rename/change/remove an instance. Switch engines and check cleanup behavior.

Run these checks on a physical phone to validate fluidity and TwiCC streaming TTS.
