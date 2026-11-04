# TwiCC for Android

**Experimental prototype, not a final release.** This first implementation is a
checkpoint for device testing. Keyboard transitions and foreground web audio
still need real-device validation. Background playback is not implemented.

A small native Android shell around your existing TwiCC server. The Vue interface,
backend, agents, and audio players remain unchanged. The APK does not include a
server or a copy of the frontend: server updates appear on the next page load.

The launcher icon reuses `frontend/public/android-chrome-512x512.png`, with a
white background (`#FFFFFF`) and padding for Android adaptive icon masks.

## Requirements

- Android **10 or newer** (API 29).
- A current Android System WebView or Chrome provider with `MULTI_PROFILE` support.
  The app shows an update message instead of sharing credentials if unavailable.
- An existing TwiCC server reachable through a valid **HTTPS tunnel**.
- For building: **JDK 17 or 21**, Android SDK Platform **36**, and Android SDK Build
  Tools **35.0.0**. Android Studio is the easiest way to obtain these tools.
- No Play Store account is required for installing your own APK.

The Gradle wrapper downloads the pinned Gradle distribution and build dependencies
on its first run. Do not commit `local.properties`, signing keys, or passwords.

## Build and install

Open this `android/` directory as a project in Android Studio, select your SDK,
then build the **debug APK**. Alternatively, with an existing Android SDK and JDK:

```sh
cd android
export ANDROID_HOME=/absolute/path/to/Android/Sdk
export ANDROID_USER_HOME="$PWD/.signing"
./gradlew :policy:test :app:lintDebug :app:assembleDebug
```

The installable APK is:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

Transfer it to your phone, open it, and allow installation from that source.
Alternatively, enable USB debugging, connect your phone, and run:

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The command above assumes the current directory is `android/`. Installation by ADB
continues to support personal development builds under Android's developer
verification rules. Other sideloading flows depend on the country and device.

The command above keeps the local debug signing key in the ignored `.signing/`
directory. Keep that directory private and preserve it for future builds.
Updates require the **same application ID and signing key**. An APK built on
a different machine or a fresh CI runner can require uninstalling the old app,
which deletes saved instances and Web storage. Debug signing is for personal
testing; use a private stable release key before distributing maintained builds.

### Optional GitHub Actions build

`ci-build.yml` is an opt-in workflow template, not an enabled repository workflow.
Copy it to `.github/workflows/android.yml` if you want a manually triggered build.
It runs policy tests, Android lint, and the APK build, then uploads the debug APK.
No repository publication or workflow run is performed by adding this template.

### URL policy tests without an Android SDK

The URL policy is a separate Kotlin/JVM module. With a JDK installed:

```sh
cd android
./gradlew --configure-on-demand :policy:test
```

Configuration on demand avoids configuring the Android application for this task.

## First launch

1. Select **Add instance**.
2. Enter an optional name and the server origin, for example
   `https://twicc.example.com` or `https://twicc.example.com:8443`.
3. Select **Save**, then select the instance to open it.
4. Sign in through TwiCC's normal login page.

Use the tunnel root, not `/login` or a session URL. HTTP, embedded passwords,
query strings, and fragments are rejected. Internationalized hostnames must use
their ASCII/punycode form. Android verifies the HTTPS certificate normally.

The last opened instance and its last visited page are restored at launch.
Cookies and Web storage belong to this app, not your normal browser. Each saved
instance has its own WebKit profile, including instances on different ports.
Switching destroys the previous WebView; it does not leave several sessions or
audio players running in the background. Saved login and local preferences remain.

## Native menu

The circular **⋮** button opens the native menu. **Drag it** to move it away from
TwiCC controls. Its position is remembered. There is no address bar or permanent
native toolbar; Android's system bars remain visible.

- **Instances:** return to the list to open, add, edit, or remove an instance.
- **Reload page:** reload the current page normally.
- **Force refresh (keep login):** recreate the WebView, clear its resource cache,
  and bypass that cache for this load. Cookies, localStorage, and IndexedDB remain.
  A confirmation warns that unsaved text can be lost. Normal caching resumes
  after loading finishes or fails.
- **Server home:** return to the configured server root.
- **Open in browser:** open the current address in your normal browser.

Android's **Back** gesture navigates WebView history. At the first page it returns
to the instance list. Rotation keeps the same WebView and page. Android can still
kill the app process; in that case it restores the URL, not unsaved in-memory work.

Renaming an instance preserves its profile. Changing its server address creates a
new profile. Removing an instance removes its entry immediately and schedules its
old Web profile for deletion. WebKit prevents deletion of profiles already loaded
in the current process, so their disk data can remain until the next **cold process
start**. Force-stop and reopen the app to trigger that cleanup immediately.

## Links, files, and audio

- Same-origin top-level links stay inside the app. The origin includes the scheme,
  host, and port.
- External HTTP(S) links open your default browser, including verified app links.
  If no default browser is selected, the app offers the available browsers.
- `target="_blank"` and user-initiated `window.open()` open externally, including
  same-origin pages. The external browser uses its own login state.
- `mailto:` and `tel:` open the corresponding Android handler when available.
- Resource requests and iframe navigation remain WebView-managed. TwiCC's existing
  embedded browser and artifacts remain embedded.
- File inputs open Android's file picker. No broad storage permission is needed.
- HTTPS downloads go to **Downloads** through Android's DownloadManager. Same-origin
  downloads include the instance's cookies. Download progress/completion appears
  in Android notifications.
- `blob:`/`data:` exports are not implemented in this first shell. The app reports
  this when a download reaches its download handler. Open TwiCC in your browser
  for those exports. Browser login is separate. Camera capture, native sharing,
  OS deep links, and microphone permissions are not implemented.
- TwiCC's HTML audio and Web Audio players run in the foreground. Autoplay is
  enabled for media. Web Audio activation and supported audio formats still need
  a real-device check. Tap a speech button first if activation is required.
- No background audio guarantee, native TTS, speech recognition, push notification,
  or battery exemption is provided. Switching to another app can suspend WebView.

Force refresh is a resource-cache refresh, not a reset of site data. TwiCC currently
does not register a service worker. This command does not unregister a future
service worker or clear its Cache Storage. WebView's cache clear can affect resource
caches across views; saved authentication and preferences are not erased.

## Phone smoke test

1. Add two HTTPS instances. Sign in to each and confirm login remains separate.
2. Open a project/session, use Android Back, then relaunch the app.
3. Change a local preference, force refresh, and confirm login/preference survive.
4. Type a draft and open force refresh: **Cancel** must preserve the draft.
5. Test a Markdown external link, a `_blank` link, and a `window.open()` link.
6. Open an artifact and the embedded browser; check that they stay embedded.
7. Upload one file and multiple files. Cancel the file picker and retry.
8. Download an authenticated HTTPS attachment and check the Downloads notification.
9. Play both a normal TTS result and a streaming PCM result while the app is visible.
10. Open the keyboard, rotate the phone, and move the menu button. Check the composer
    remains reachable and rotation does not restart a response/audio stream.
    With the keyboard open, the page header and sidebar header must remain visible,
    and no empty band must appear between the composer and keyboard. Close and
    reopen the keyboard several times; check that no manual page scroll is needed.
    On Android 11+, the WebView keeps its layout height while the keyboard opens
    or closes, then resizes once after the animation ends. Check that the keyboard
    moves smoothly and the page does not repeatedly reflow during its animation.
    Interrupt an opening/closing transition and check the final layout recovers.
11. Stop the tunnel, reload, restore it, and select Retry. Check recovery and login.
12. Rename, change the address of, and remove an instance. Force-stop/reopen to
    verify deferred profile cleanup and last-instance fallback.

## Verification status

Version **0.1.5** builds successfully with JDK **17.0.21+9**, Platform **36**,
and Build Tools **35.0.0**. URL policy code is unchanged; its last test result is
four passing tests. These tests are not rerun for this native layout update. Android lint reports
**0 errors**, 13 warnings. The generated debug APK passes `apksigner verify`.

The warnings concern target/dependency update suggestions, backup configuration,
icon metadata, and English text internationalization. Feature-availability checks
pass. The author has not performed a real-phone test.

Version **0.1.2** changes native inset handling for the keyboard: the root still
reserves space for system bars and the keyboard, then forwards zero values for
those handled insets to WebView. This avoids applying the same insets twice.
The user's 0.1.1 recording shows the page shifting above its normal position when
the keyboard opens. Confirmation of this correction requires a real-device test.

Version **0.1.3** introduced following keyboard animation progress on Android 11+ using
`WindowInsetsAnimation.Callback`. It defers the end-state padding sent before
animation starts and applies each intermediate height to the native container.
The header stays anchored while the WebView's actual layout height changes.
Handled insets remain zero for child views during animation. Final insets are
requested again after the animation ends, including when it is interrupted.
Android 10 retains its existing non-animated resize behavior. The user's 0.1.2
recording provides the regression case. The user's later recording and feedback
show that repeated WebView relayouts make this approach stutter.

Version **0.1.4** sets the native adaptive launcher icon background to white
(`#FFFFFF`). The packaged color resource is verified with `aapt2 dump resources`.

Version **0.1.5** replaces per-frame WebView resizing with a single resize after
the IME animation ends. During opening and closing, the existing WebView layout
height stays unchanged. Intermediate insets are still cleared for child views,
and the ordinary end-state inset dispatch is deferred until `onEnd` requests a
fresh dispatch. Non-animated inset changes still apply immediately. There is no
fixed delay or JavaScript injection. This follows the sequence the user prefers
in Firefox; visual behavior still requires real-device confirmation. Android 10
keeps its existing fallback. The white launcher background remains in this APK.

The reported Chrome scrolling issue remains undiagnosed. Both the source HTML
and this checkout's built frontend already contain `interactive-widget=resizes-content`.
This native change does not alter the frontend served to browsers.

See `docs/plans/2026-11-04-android-webview-plan.md` in the repository root for the
full verification record. Do not infer a phone test from an APK build.
