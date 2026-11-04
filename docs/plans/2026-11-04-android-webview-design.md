# Android WebView shell

## Intent and authorization

The user requests implementation in a dedicated project folder, for testing on
their Android phone. They explicitly delegate implementation now, without another
design approval round. Stay on the current branch. Do not modify the web client,
backend, provider configuration, or other agents' changes.

## Scope

- A standalone Kotlin application in `android/`, loading an existing HTTPS tunnel.
- Named instances: add, rename, remove, switch, remember last instance.
- Each instance uses an AndroidX WebKit profile, including separate cookies and
  Web storage. Require a WebView provider with MULTI_PROFILE support.
- Keep the existing Vue UI and web audio. No background service, native TTS,
  microphone, push notifications, embedded server, or iOS project.
- No address bar. Keep Android system bars, keyboard insets, and a movable menu
  button. Back navigates WebView history, then returns to the instance list.
- Internal top-level navigation stays inside; external navigation and new-window
  requests open the system browser. Do not intercept iframe/resource requests.
- Hard refresh clears resource cache, reloads with LOAD_NO_CACHE, then restores
  normal cache behavior. Preserve cookies, localStorage and IndexedDB. Current
  TwiCC has no service worker; offline service-worker caches are outside scope.
- File selection uses the system picker. HTTPS file downloads use DownloadManager
  with same-origin authentication cookies. Blob/data downloads are not supported
  in this first shell and must report that limitation explicitly.
- Retain the current page across rotation; restore a safe URL after process death.
- HTTPS origins only: no credentials, query, fragment, or subpath in instance URLs.
  The server remains remote; no bundled frontend or backend.

## Approaches

Kotlin plus platform WebView is the selected implementation direction from the
discussion: Android only, a small shell, no native UI rewrite. Capacitor would add
a JS/native plugin layer that this scope does not need. A PWA does not provide the
requested native refresh and instance management controls.

## Verification and distribution

Use pure JVM tests for URL validation/navigation policy and Android build/lint
checks when a JDK and SDK are available. Supply a pinned Gradle wrapper and a
manual GitHub Actions build. Do not install missing SDK packages or change the
host environment without authorization. Document exact build/install commands,
debug signing limitations, and the real-device smoke test. Do not claim a phone
test or APK build without running it.

## Implementation decisions

- Android 10 / API 29 is the minimum. This avoids legacy public-download storage
  permission requests and keeps the shell small. The plan initially proposed 26.
- Native Android source compilation can also be smoke-tested against Robolectric's
  Android API jar and the actual WebKit AAR in scratch. This is not an APK build.
- WebKit profiles already loaded in a process cannot be deleted immediately.
  Instance removal queues profile deletion for a cold process start.
