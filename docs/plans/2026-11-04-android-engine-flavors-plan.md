# Android browser engine flavors implementation plan

> **For agentic workers:** Execute the approved design inline, then request an independent review. Track completion below.

**Goal:** Build the same `com.twidi.twicc` app with either WebView or Gecko using shared UI and native services.

**Architecture:** One app module, two engine flavors, common engine/session/event contracts. Shared native requests/downloads translate platform work without engine-specific types.

**Tech Stack:** Existing Kotlin 2.4.20, Gradle 9.6.0, AGP 9.4.0, SDK 37.2, minSdk 29, targetSdk 35. Pinned GeckoView 159 and AndroidX WebKit 1.14.0.

**Spec:** `docs/plans/2026-11-04-android-engine-flavors-design.md`.

## Global constraints

- One application ID and namespace: `com.twidi.twicc`; one name/icon/version/signing key.
- No branch/worktree, unrelated frontend changes, backend restart, background service, or runtime engine selector.
- Preserve settled keyboard resizing, separate instance storage, and authenticated downloads.

## Review focus

- Closed-session callbacks and pending native requests cannot modify a replacement session.
- Force refresh preserves the engine storage context and bypasses resource cache.
- Profiles remain isolated across hosts/ports; deletion may be deferred on WebView.
- File-picker results are routed exactly once and canceled during disposal.
- HTTP and stream downloads preserve their distinct authentication/resource ownership semantics.

## Task 1: Shared shell and browser contracts

- [x] Rename active packages and namespace to `com.twidi.twicc`.
- [x] Add `BrowserEngine`, `BrowserSession`, `BrowserEvents`, and a pure `NavigationPolicy` with tests for same-origin, external, iframe, popup, and unsafe schemes.
- [x] Extract shared external-link handling, file-picker/dialog requests, download targets, and session-neutral Activity UI.
- [x] Keep Gecko URI-to-file conversion outside common code.

## Task 2: Engine adapters

- [x] Move current Gecko delegates/settings into `src/gecko/` with a flavor factory and singleton runtime owner.
- [x] Restore WebView behavior from commit `3a8e359`, adapting it to common contracts and native services in `src/webview/`.
- [x] Ensure close suppresses callbacks, cancels prompts, releases views, and retains persistent storage.
- [x] Route common instance cleanup through the selected engine.

## Task 3: Builds and documentation

- [x] Add the `engine` flavor dimension, flavor-only dependencies, version 0.3.0/code 8, and identical IDs/names/signing.
- [x] Add executable `android/build.sh webview|gecko` with checks, stable signing directory, APK collection and checksums.
- [x] Update CI template and README for both builds, package identity transition, architecture, and limitations.

## Task 4: Verification and delivery

- [x] Run `:policy:test`, both flavor lint tasks, and both flavor assemble tasks.
- [x] Verify build script from another cwd and invalid arguments.
- [x] Inspect certificates/application IDs/versions/icons/ABIs and absence of Gecko in WebView APK.
- [x] Run emulator replacement and behavior smoke tests for both engines.
- [x] Request an independent review, resolve important findings, and record results.
- [x] Copy both signed ARM64 APKs and checksums to session artifacts.

## Execution record

- Architecture: native UI and requests stay shared. Native preferences use one app ID; Web storage stays engine-specific.
- Storage deletion: retain independent tombstones for both engines, including the absent engine. This avoids losing cleanup requests during replacement.
- Initial compilation: both flavor Kotlin tasks pass. All 10 common policy tests pass.
- Build scripts: WebView passes in 56 seconds from `/tmp`; Gecko passes in 85 seconds. Both complete lint and assembly. Invalid/missing/extra engine arguments return status 2; shell syntax and help checks pass.
- APK inspection: both use `com.twidi.twicc`, version 0.3.0/code 8, min 29/target 35, and the preserved certificate SHA-256 `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a`. Both launcher backgrounds are white. WebView contains no native libraries or GeckoSession class; Gecko ARM64 contains 13 native libraries.
- Independent read-only review: no Critical findings. One reported Minor regression: WebView fallback after external POST navigation displays Retry instead of restoring the last safe URL.
- Final: Ruling: grade the external POST return regression Important because this refactor must preserve navigation behavior. Restore the previous automatic safe-URL load; no product behavior change is intended.
- Regression reproduced on emulator Android 15/WebView 124 with `form.method=POST` to an external HTTPS origin. The system browser opens; returning exposes the native error panel. After correction, the same POST opens the system browser and returns to the last safe TwiCC URL without the error panel. The complete WebView build/lint/policy checks pass again in 43 seconds.
- Reviewer declined runtime/build/APK validation because it was in progress: covered by this execution's verification. Physical-phone fluidity and streaming TTS remain manual validation. Background execution and unrelated frontend changes remain outside the authorized task.

- Final verification: 10/10 JVM tests; WebView lint 0 errors/17 warnings, Gecko lint 0 errors/13 warnings. Warnings concern existing API/version/icon/backup/text patterns and cross-method feature checks. No lint baseline or suppression was added.
- Emulator: Android 15/API 35, x86-64, WebView 124 and Gecko 159. WebView → Gecko → WebView replacement succeeds with the same package, version code and certificate. Instance UUID/name/address/last page and floating-menu position remain.
- Engine separation: WebView cookies/localStorage are absent on first Gecko load. Switching back restores WebView data. Gecko persistent cookies/localStorage survive its restart and refresh.
- Per-instance isolation: a test-only preferences fixture gives two UUIDs the same HTTPS origin. Each engine's second profile starts empty. After setting a second token, the first profile retains its original token. The normal UI continues to reject duplicate addresses; the fixture specifically tests the engine storage boundary.
- Native controls: JavaScript confirm resolves correctly in both engines. File picker cancellation and retry pass in both. WebView reads a 24-byte text upload with its filename intact. Gecko reads two selected files (24-byte text and 32-byte binary), with names and sizes intact; temporary upload copies disappear after force refresh.
- Keyboard: WebView CSS height 815→520→815; Gecko 820→522→820. Each has one resize per transition, scrollY 0, and header top 0 throughout the settled states. AudioContext reaches `running` in both engines; the emulator has audio output disabled.
- Downloads: Gecko saves the actual 32-byte HTTPS response via MediaStore as `32 (1).bin`; WebView saves a 32-byte HTTPS download via DownloadManager as `32-1.bin`. Real TwiCC authenticated attachments/POST download bodies and streaming TTS remain phone smoke tests.
- Removal fixture: WebView leaves both engine tombstones after removing its loaded second profile. Cold start submits WebView deletion and retains the Gecko tombstone for the next Gecko build.
- Delivered ARM64 APKs and SHA256SUMS are copied into the session artifacts directory. Gecko is 209,457,750 bytes; final WebView is 4,053,296 bytes. All three Gecko ABI APKs and universal native-library sets match their filenames; WebView APKs contain no native engine libraries.
- Deferred minors: none. The only review finding was corrected and verified by its emulator reproduction.

- Returning to Gecko submits the remaining context deletion, leaving both tombstone sets empty. The first instance's Gecko persistent cookie/localStorage remain intact. This verifies request routing; Gecko's asynchronous API still provides no completion acknowledgement.
- Final artifact SHA-256: WebView `4389d0608159f8f7a3dc528dc20e179d9229d79651dc44114af800a36f74a6e9`; Gecko `b6bf5ca57d37871e6f3bc86c3c85097088a24b2d73bbf6cc3a565e6aeef6e102`.
