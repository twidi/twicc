# Android browser engine flavors

## Approved requirements

The user requests one Android app built with either Android System WebView or GeckoView. Both builds use `com.twidi.twicc`, the user's reverse-domain identifier. They share the same app name, icon, version, signing key, and native preferences. Installing one engine build replaces the other. The old `com.twicc.android` prototype is a different Android installation; its data is not migrated automatically.

Use one `:app` module with `webview` and `gecko` product flavors in the `engine` dimension. Keep shared Kotlin/UI/resources in `src/main/`. Each flavor provides one `EngineProvider` factory and its engine-specific implementation. Add one root-independent `android/build.sh webview|gecko` script that runs checks and collects the chosen APKs and checksums.

## Boundaries

- `BrowserActivity` owns layout, menu, URL restoration, error/progress UI, Back handling, and the unchanged settled IME resize policy.
- Common browser contracts define session commands and normalized events. No WebView/Gecko session API types enter these contracts or shared activities.
- A common navigation policy classifies main-frame/new-window URLs. Subframe navigation remains browser-managed. External handlers and browser selection stay common.
- Common native requests own Android file pickers and dialogs, unique result IDs, exactly-once completion, and cancellation. Adapters translate engine callbacks. Gecko alone copies content URIs to temporary file paths.
- Common downloads accept an HTTP request or an existing response stream. HTTP uses DownloadManager; streams use MediaStore. Authentication acquisition remains inside the adapter. Preserve the documented notification/background differences and unsupported blob/data exports.
- Each adapter owns browser creation/settings, isolated storage, lifecycle translation, progress/error/death callbacks, media permissions, cache-bypassing reload, and closed-session callback suppression.
- The engine interface requests storage deletion and reports whether the request was submitted or must be deferred. It does not claim asynchronous deletion has completed.
- Keep one runtime singleton for Gecko and AndroidX multi-profile enforcement for WebView.

## Scope

Preserve existing foreground web behavior and white launcher icon. Do not change frontend files, add native background audio, start/restart the TwiCC backend, add engine switching inside the APK, or create separate app IDs. Do not make a new branch/worktree. Existing unrelated changes remain untouched.

## Verification

Run common JVM policy tests and both flavor lint/build tasks. Inspect application IDs, versions, certificates, icons, ABIs, and APK dependencies. Verify no Gecko classes/native engine libraries enter the WebView APK. Run emulator checks for both engines, replacement installation, native preference retention, keyboard bounds, refresh storage retention, file-picker cancellation/upload, and available external browser handling. Distinguish emulator validation from physical-phone fluidity and streaming TTS validation.
