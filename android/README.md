# TwiCC for Android

**Experimental Android client.** Build one app with either
**Android System WebView** or **Mozilla GeckoView**.
The engine is selected at build time. Both builds use `com.twidi.twicc`, the
same version code, app name, icon, native preferences, and local signing key.
Installing one build replaces the other. There is no runtime engine selector.

The APK opens an existing TwiCC **HTTPS** server, usually through a tunnel.
It does not include the TwiCC frontend or backend. Server updates appear on
page reload. Both Android builds use native TTS when the engine and server
support it. There is no build-time speech selector. Ordinary browser users
keep the Web TTS players. Physical-phone acceptance remains pending.

## Interface language

The installation starts in **English**. Select English, German, Spanish, French,
or Brazilian Portuguese in an instance's interface language control.
The selection updates native controls and every compatible embedded instance.
It persists across process restarts, instance removal, and engine replacement.
Each Android device keeps its own selection. Android's system language does not
select the TwiCC language. Ordinary browsers keep their browser preference.

Native controls and device-rendered notifications use packaged messages offline.
The server receives no language preference. User content, provider content,
spoken text, instance names, and external diagnostics remain verbatim.
Existing event notifications keep their posted text. Later notifications and
normal ongoing speech notification updates use the current language.

Compatible pages require bridge `protocol: 1`, `capabilities.appLanguage: 1`,
and the revisioned `locale.get` / `locale.set` commands. Stale page selections
cannot overwrite a newer installation selection. Old pages keep native controls
in the installation language; update the server for shared page selection.
Update an old APK to enable installation language ownership.

**Push rollout:** update both the server and APK. The new server sends descriptor
payloads only to registrations with `notification_payload_version: 2`.
Old APKs require an update after the server upgrade. The notification settings
show `update_app` or `update_server` compatibility status where applicable.
A new APK renders valid legacy text pushes as localized generic notifications.
It keeps safe tap destinations. Unknown descriptor keys also use generic text.
Malformed envelopes and unknown payload versions remain rejected.
Open each saved instance after upgrading so foreground reconciliation can renew
its registration with the new payload capability.

### Catalog generation and build checks

The editable translations live only in `frontend/src/i18n/locales/*.json`.
`android/i18n/messages.json` selects native keys and numeric parameter formats.
Do not edit generated Android resources or create translated Android XML copies.
After changing catalogs, the manifest, generators, or fixture adapter sources:

```bash
cd frontend && npm run i18n:android:generate
cd frontend && npm run i18n:android:check
cd frontend && npm run lint:i18n:android
```

Run these commands from the checkout root. Existing frontend dependencies are
required for generation. Package installation remains a separate setup action.
Generation also bundles real page adapters into
`android/app/src/androidTest/generated/`. These deterministic modules contain
their i18n dependencies and enter only the instrumentation APK.
Gradle copies the source `frontend/index.html` recovery guard separately.
The fixture generator uses the installed Vite dependency and package lock.

Commit all generated outputs, including `sources.sha256`. It fingerprints all
five catalogs, the manifest, both generators, the locale manager and adapters,
the descriptor helpers, the package lock, and every generated output.
The fingerprint file excludes itself. Gradle's `verifyAndroidI18n` uses Java
SHA-256 before `preBuild`. A source or generated output mismatch fails the build.
Standalone Gradle and `build.sh` do not invoke Node or install npm packages.
The opt-in CI template performs explicit frontend dependency setup and checks.

The Kotlin checker detects direct presentation literals in main, Gecko, WebView,
and policy sources. It does not trace variables, interpolated data flows,
reflection, or dynamic error provenance. Review these paths manually.
Diagnostics and user/provider content stay verbatim. Semantic error producers
can retain English fallback prose beside their descriptor.

See the [execution handoff](../docs/plans/2026-11-08-android-internationalization-execution.md)
for recorded checks and outstanding native/device acceptance.

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

Both builds provide the same native controls. Native TTS uses one
common Android service. It does not require background page JavaScript.
Neither build guarantees a permanent network connection or universal OEM reliability.

## Package identity and engine replacement

- The application ID is **`com.twidi.twicc`** for both engines.
- WebView/Gecko builds replace each other with `adb install -r`, if their
  signing certificates match and their version codes permit the update.
- Instance addresses, names, and last pages remain across engine
  replacement. Cookies, localStorage, and IndexedDB are **engine-specific**.
  Sign in separately in each engine. Switching back restores that engine's data.
- Removing an instance queues storage deletion separately for both engines.
  The installed engine requests its cleanup now or at the next cold start.
  The other engine requests its cleanup when that build next runs. A deleted
  instance UUID is never reused.
- The launcher uses the TwiCC robot from
  `frontend/public/android-chrome-512x512.png`, with a white adaptive background.

## Instance manager

The native home screen works offline and uses TwiCC's cyan and tinted-neutral
colors. It follows Android's system light or dark mode, including changes while
the screen is open. Both engine builds share this screen.

Saved instances show their name, address, and a **Last used** marker. Each card
provides Open, Edit, and Remove controls. **Add instance** stays below the
scrollable list. Forms and removal confirmations use the same visual theme.
Theme changes preserve unsaved form values, validation errors, and pending
removal confirmations.

## Requirements

- Android **10+** (API 29), with a valid HTTPS server certificate.
- WebView build: a current Android System WebView provider with AndroidX
  `MULTI_PROFILE` support. Updating Chrome/System WebView updates this engine.
- Gecko build: no Firefox installation required. The APK bundles GeckoView
  **159.0.20261102144711**. Security updates require rebuilding the APK;
  updating Firefox or Android System WebView does not update bundled Gecko.
- Build: JDK **17**, Gradle **9.6.0**, AGP **9.4.0**, Kotlin **2.4.20**, SDK
  Platform **37.2**, Build Tools **36.0.0**. `minSdk = 29`, `targetSdk = 35`.
- Build script: **Bash** and **`sha256sum`** (GNU coreutils), both available on `PATH`.
- SDK setup: Android SDK command-line tools provide `sdkmanager`.
  Network access is required to download the SDK, Gradle, and dependencies.
- AndroidX WebKit **1.14.0** belongs only to the WebView flavor. GeckoView
  belongs only to the Gecko flavor. Dependencies are pinned.
- No Play Store account is required to build and install these APKs.

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
./android/build.sh webview 64
./android/build.sh gecko 64
# Enable Android and browser debugging when needed:
./android/build.sh gecko 64 --debug
```

The first argument selects the engine. The second argument selects the device profile:

| Profile | Target |
| --- | --- |
| `64` | ARM 64-bit phones and tablets (`arm64-v8a`) |
| `32` | ARM 32-bit phones and tablets (`armeabi-v7a`) |
| `emulator` | Intel/AMD 64-bit emulators (`x86_64`) |
| `universal` | All three supported architectures in one APK |

An ARM64 emulator uses `64`. Both arguments are required. Missing or invalid
arguments print usage and stop before Gradle runs. Use `./android/build.sh --help`
to list the profiles. Universal is generated only when explicitly selected.

The default mode is **release**. It disables Android debugging and browser remote
inspection. Add `--debug` after the profile to enable both. This option works
with either engine and every device profile. TTS and other app features work
in both modes. Release builds currently keep code minification disabled.

The build entry point has standalone regression tests:

```sh
python3 -m unittest discover -s android/tests -v
```

The script also works from another directory through its absolute path. It runs
common policy tests, flavor app unit tests, flavor lint, and flavor assembly.
Each command builds one APK for the selected profile. Native TTS is enabled in
both engines without an extra argument. Both builds share version **0.5.19**,
version code **30**, application ID, native preferences, and signing key.

Gradle downloads its distribution and dependencies on the first build.
The WebView build does not download the Gecko dependency.

The selected output directory is `android/dist/<engine>/<profile>/<mode>/`, where
`<mode>` is `release` or `debug`. It contains `app-<engine>-<profile>-v<versionName>-<versionCode>-<mode>.apk`
and `SHA256SUMS` for that APK only. Engines, profiles, and modes have separate
directories. The script copies the current APK by its exact name;
it does not collect stale APKs from previous Gradle builds.
The version comes from `app/build.gradle.kts`. After successful publication,
the script removes older APK files from the selected output directory only.
A failed build keeps the previous publication. Other targets and session
artifacts are not cleaned by this script.

Gecko APKs are much larger because they include the browser engine. Transfer
the selected APK to the phone and permit installation from that source. Or use
USB debugging:

```sh
adb install -r android/dist/gecko/64/release/app-gecko-64-v0.5.19-30-release.apk
# Replace the same installation with WebView:
adb install -r android/dist/webview/64/release/app-webview-64-v0.5.19-30-release.apk
```

**Preserve `android/.signing/`.** The script sets `ANDROID_USER_HOME` there to
reuse the same ignored local key for release and debug builds. The key comes
from Android's debug signing configuration; using it does not make a release
APK debuggable. All variants can update the same local installation.
Another computer or fresh CI runner normally creates a different key.
A certificate mismatch prevents replacement. This automatic key is for personal
builds; maintained distribution needs a stable private release key and its own
signing configuration. Android Studio/direct Gradle users must set the same
`ANDROID_USER_HOME` to share the script's key.

Direct Gradle commands:

```sh
cd android
export ANDROID_USER_HOME="$PWD/.signing"
./gradlew --no-daemon -PtargetProfile=64 :policy:test :app:testWebviewReleaseUnitTest :app:lintWebviewRelease :app:assembleWebviewRelease
./gradlew --no-daemon -PtargetProfile=64 :policy:test :app:testGeckoReleaseUnitTest :app:lintGeckoRelease :app:assembleGeckoRelease
# Policy tests only, without Android SDK configuration:
./gradlew --configure-on-demand :policy:test
```

Direct APK output is `app/build/outputs/apk/<engine>/<mode>/app-<engine>-<mode>.apk`.
Gradle defaults to profile `64`; set `-PtargetProfile=32`, `emulator`, or `universal`
to choose another profile. Native library filtering happens before packaging.
The Gradle output path is shared between profiles. Use the build script to keep
selected APKs and checksums in separate distribution directories.
Use `Release` tasks for normal builds and `Debug` tasks for debugging.
Open `android/` in Android Studio and select `webviewRelease` or `geckoRelease`.
The `webviewDebug` and `geckoDebug` variants enable debugging.
The opt-in `ci-build.yml` template builds release APKs for both flavors sequentially
for the selected device profile, with one signing key. Copy it to `.github/workflows/android.yml` to enable it.
Each workflow run needs a securely restored signing key for updates.

## Source structure

```text
android/app/src/
  main/
    AndroidManifest.xml, res/                 shared app identity and resources
    java/com/twidi/twicc/
      BrowserActivity.kt                     layout, fixed robot fallback, navigation, Back
      AndroidAppBridge.kt                    versioned app commands and state
      MainActivity.kt, InstanceStore.kt       instance manager and preferences
      NativeUi.kt                            system bars and keyboard layout
      browser/                               engine/session/event contracts
      platform/                              links, dialogs, file picker, downloads
      tts/                                   common service, transport, streaming player, notification
  webview/java/com/twidi/twicc/
    browser/EngineProvider.kt                flavor factory
    engines/webview/                         profiles, settings, WebView callbacks
  gecko/java/com/twidi/twicc/
    browser/EngineProvider.kt                flavor factory
    engines/gecko/                           runtime, sessions, Gecko delegates/uploads
android/app/src/gecko/assets/
  android-app-bridge/                        built-in top-level WebExtension transport
android/policy/                              shared JVM URL/filename/navigation/bridge rules
frontend/src/components/sidebar/
  AndroidAppMenu.vue                         one app-wide Web menu
  AndroidAppMenuAnchor.vue                   rail anchor before Home
frontend/src/utils/androidAppClient.js         shared Web RPC client
frontend/src/utils/androidMenuPlacement.js     rail registration and fixed placement
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
creates a new UUID. The last safe same-origin URL is saved separately for each instance. Switching
instances and relaunching the app restore that instance's saved page.

Inside the Android app, the **TwiCC robot before Home** opens an app menu
rendered by the server frontend. Without a visible rail, the robot stays at the
**top left** and opens the same Web menu. This includes login and connecting
screens. One app-wide menu moves between a registered rail anchor and its fixed
position. Menu state and its confirmation dialog stay in the same component.
The menu is absent in an ordinary browser. Its empty rail anchor creates no gap.
It provides:

- Version and engine name.
- All saved instances, with an active marker and direct switching.
- **Manage instances**, which opens the native instance manager.
- Normal reload.
- **Force refresh (keep login):** replace the browser view/session and bypass
  resource cache. Cookies, localStorage, and IndexedDB remain. Unsaved text can
  be lost. This does not unregister service workers; TwiCC has none currently.
- Server home and Open in browser.

A fixed native **TwiCC robot at the top left** provides the same actions as a
fallback. It respects Android system-bar insets. It remains available on load
errors, older frontends, or when the Web menu is unavailable. A visible Web menu renews a six-second native lease every two seconds.
If the lease expires, the native button returns. A stopped JavaScript thread can
therefore delay the fallback by up to six seconds. The robot is not draggable.

The bridge accepts only predefined commands from the current instance's HTTPS
origin and top-level frame. WebView uses an origin-scoped AndroidX Web message
listener. Gecko uses a bundled WebExtension and a session-scoped native message
delegate. Both use the same versioned protocol and shared native dispatcher.
No general-purpose JavaScript interface is exposed. If a transport is unavailable,
normal navigation and the native menu remain available.

Back uses browser history, then returns to the instance manager. Rotation keeps
the session. Process death restores the URL, not unsaved in-memory work.
If the renderer crashes or is killed, the app recreates it at the saved URL
when the app returns to the foreground. Automatic recovery is limited to one
attempt per minute. Another failure shows **Retry**; a manual reload resets
that limit. Recovery preserves the browser profile but can lose unsaved text.
Logs distinguish a renderer crash from a killed renderer.

The server frontend also recovers from missing JavaScript or CSS after a new
build. Its inline HTML guard works even when the entry module cannot load.
It reloads the current URL once with a temporary cache-busting parameter and
keeps login and preferences. Repeated failures or an offline device show
**Retry** instead of a reload loop. This frontend recovery also works in browsers.

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
- HTML audio/Web Audio remain engine-managed. Gecko permits autoplay for the
  instance origin. WebView disables the playback gesture requirement.
- Android builds route supported TwiCC speech through the common native service.
  Ordinary browsers retain the existing Web TTS players.
- The app does not provide native microphone access or speech recognition.

## Android notifications

Notification settings show **Android notification** switches for finished work,
questions/approvals, extra usage, and peer messages/pairing requests. Each choice
belongs to this phone and this instance. No browser-notification permission or
manual push enrollment is needed. Android asks for its notification permission
when an enabled event first needs it. Blocked permission does not hide the
choices; settings provide an **Open Android settings** button.

The backend sends notifications when no ordinary TwiCC browser is active.
Desktop input counts as activity for five minutes; mobile-browser foreground
activity counts for thirty seconds. Android app activity does not suppress server
delivery. On the phone, a loaded foreground session suppresses only its own notifications.
Existing browser notifications, in-app toasts, sounds, and Apprise targets keep
their separate settings. The extra-usage master switch still applies.

Both engine builds share the native receiver. UnifiedPush with its embedded FCM
distributor uses Google Play Services on the phone. No Firebase project,
Firebase credentials, custom relay, or Play Store installation is required.
The backend uses encrypted Web Push with a persistent, automatically generated
VAPID key under `<data_dir>/android-push/`. It requires the project's Python
dependencies, including `pywebpush`. The phone must reach the saved HTTPS
instance address without an interactive tunnel challenge.

Registration and endpoint changes persist in private Android storage. A finite
Android network job updates the backend without browser JavaScript. It retries
network failures and persists across reboot. Logout, instance removal, and an
address change unregister the device; pending server removal retries when the
network returns. A password change invalidates the device grant. Sign in again
to renew it. No notification history is stored by this feature.

Notifications work independently of native speech and do not start a foreground
service. Delivery still depends on network access, Google Play Services, and
Android/OEM restrictions. Android force-stop blocks delivery until the app opens
again. Events have a five-minute push TTL; missed events are not replayed.
Four Android notification channels let users control sound and visibility by
event family. Each session in each instance keeps at most one visible notification
per family. Notifications for different sessions coexist. Events without a session
keep one notification per family and instance.

Opening or returning to a session clears its notifications in that instance.
Changing sessions inside the app does the same, including session tool tabs.
New notifications for the displayed session stay hidden while the app is in the
foreground. Other sessions and instances still notify. Backgrounding or locking
the phone permits notifications for every session. Login, loading, error screens,
and the instance manager do not suppress a session. Speech controls stay separate.

Marking a session as read also clears its notifications, including through bulk
actions or from another device. Viewing a session in a browser does the same.
Other sessions keep their notifications. Marking a session as unread does not
restore cleared notifications.

Archiving or hiding a session clears its notifications in the matching instance.
This also applies to changes made from another device, while Android is in the
background. Other sessions and instances keep their notifications. Unarchiving
or unhiding does not restore cleared notifications.

A tap opens the matching saved instance. A session tap uses Vue Router when that
instance is already open; it preserves the current document and connection.
Session navigation uses the same project filter, all-projects mode, and workspace
rules as other session links. Opening a closed instance restores its saved page
before handling the session target, so its navigation context remains available.
Tapping the current session preserves its tab and workspace. Peer notifications
open the inbox or pairing manager; extra usage opens Home.

## Native speech

**Physical acceptance is pending.** The API 35 emulator diagnostic is not a
physical-phone pass. See the [validation record](../docs/plans/2026-11-05-android-background-tts-validation.md)
for exact APKs, commands, results, and untested conditions.

Both builds negotiate native support with the server. WebView also requires
`GET_COOKIE_INFO`; update Android System WebView if that feature is absent.
Gecko uses its privileged, instance-specific cookie adapter. Cookies never enter
page JavaScript. Both engines use the same service and Media3 **1.9.0** player.

When both sides support native TTS, the service owns messages, selections,
summaries, configuration tests, and automatic timeline readings. The service
reserves manual work before cookie export or generation waits. Accepted speech
survives navigation, reload, Activity recreation, and recent-task dismissal.
The page mirrors state only while visible. It owns no native audio stream.
A supported native failure reports an error; it does not start a second Web player.

Recent failures wait until the page is visible. Displayed failures and completed
operation results are removed from native memory. Reload does not replay them.
Pending outcomes expire after ten minutes; no persistent error history exists.
Matching pending failures share one toast. Skip and Stop all produce no error toast.
An audio interruption reports an interruption, rather than an Android refusal.

Supported speech outputs are:

| Configuration output | Native input |
| --- | --- |
| MP3 | One incremental MP3 stream across all fragments |
| Raw PCM | Signed 16-bit PCM with the declared rate and channel count |
| Script WAV | Existing backend normalization removes WAV headers and sends PCM |

Existing voices, adaptation, summary generation, announcements, scripts, cache,
and streaming remain server-controlled. One reading uses one `audio_id` and one
forward stream. Individual readings provide no pause, seek, retry, or replay.

### Android settings

Activation requires enabled app notifications and the **Speech** notification
channel. Android 13+ also requires notification permission. The first request
opens Android's permission dialog. A denial exposes **Open Settings** guidance.
Permission or Settings return never repeats a rejected activation. Start again.

The battery-optimization exemption is optional for both timeline listening and
manual speech. Before the first timeline activation without an exemption, a
native dialog explains that Android can delay or interrupt background or
screen-off speech. **Continue without changes** starts listening and remembers
the choice for this installation. **Open Settings** opens Android's battery
settings without activating listening. Return and enable listening again.
Dismissing the dialog leaves listening inactive and does not remember a choice.
Notification permission remains required. The foreground service and its
notification stay active during listening, even without a battery exemption.

An exemption improves background reliability but does not guarantee it.
Without it, long silent waits have weaker screen-off guarantees. Find the
setting later in Android Settings > Apps > TwiCC > Battery. Device labels and
OEM settings can differ. The service holds a partial wake lock during
preparation, output, settlement, and enabled silent listening. Listening therefore
uses battery even while no audio plays. It uses no silent audio or microphone.

### Notification and lifetime

The notification shows the instance and Preparing, Reading, Waiting,
Timeline reading paused, Reconnecting, or Stopping. Its controls work without
an Activity or page execution:

- **Skip** cancels the current reading. It preserves enabled listening and later work.
- **Pause timeline** appears before Stop all while timeline listening is enabled.
  It skips the current reading, including a manual reading, and suspends automatic
  speech. The notification stays present. New manual readings still work.
- **Resume timeline** replaces Pause timeline during pause. It keeps the listening
  scope and muted-session choice. Only messages after the Resume click are eligible;
  messages received during pause are never replayed. Pause disables the backend
  listener while retaining native intent, so it generates no automatic audio.
- **Stop all** cancels the current reading, clears queued work, and disables listening.
  Local cancellation and wake-lock release occur immediately, including offline.
  Foreground cleanup has a five-second bound while cancellation reconciles.
- Tapping the notification opens the current instance when that session still exists.

The page mirrors paused listening as enabled. Its timeline toggle disables it;
it does not resume it. Disabling paused listening removes the notification once
no unrelated manual reading remains. Pause retains the foreground service and
wake lock, with the same background limits described above.

Manual speech has priority over automatic preparation. Disabling timeline
listening preserves unrelated manual speech. Each installation has one active
instance and listening scope. A second device has independent ownership.

The service reconnects after a temporary network loss. Retained cancellations
reconcile before resumed work, so reconnect cannot replay cancelled speech.
Backend resources expire after 120 disconnected seconds. Network reachability,
tunnels, OEM power rules, and explicit Android service stops can still end speech.

Logout, changed credentials, authorization expiry, interactive tunnel challenges,
and backend-session loss stop native ownership. Sign in when needed, then activate
again. Instance switch/removal also stops the previous instance. Process death,
reboot, and explicit Android stop never automatically restore playback or listening.
Native TTS does not make the app-wide WebSocket permanently reliable.

## Phone smoke test

1. Install one flavor, add two instances, and sign in. Check isolation by port.
2. Replace it with the other flavor. Check native settings remain. Sign in again.
3. Switch back. Check the first engine's cookies/preferences return.
4. Check the robot appears before Home. Open the Web menu and check its active marker.
   Switch instances, navigate each, stop/relaunch, and check both saved URLs.
   Hide the rail or open the login page. Check the Web menu stays at the top left.
   Show the rail again. Check one robot returns before Home.
   Open an unreachable instance. Check the native robot opens its fallback menu.
5. Test internal/external/new-window links, embedded artifacts, Back, and rotation.
6. Force refresh. Check login/preferences remain and unsaved-state confirmation.
7. Upload one/multiple files. Cancel the picker, retry, and verify names/bytes.
8. Download an authenticated attachment and verify its bytes/name in Downloads.
9. Test foreground normal/streaming TTS, scrolling, and keyboard transitions.
10. Stop the tunnel and retry. Check invalid certificates cannot be bypassed.
11. Rename/change/remove an instance. Switch engines and check cleanup behavior.

Run these checks on a physical phone to validate fluidity and TwiCC streaming TTS.
