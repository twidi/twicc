# Android GeckoView experiment implementation plan

**Goal:** Replace Chromium WebView with stable GeckoView to compare TwiCC rendering and keyboard behavior on the user's Android phone.

**Architecture:** Keep the existing native instance manager and menu. One application-scoped GeckoRuntime owns the engine. Each instance uses a persistent GeckoSession context ID to isolate cookies and Web storage. Close the previous session when switching instances.

**Tech stack:** Kotlin 2.4.20, Android API 29+, GeckoView stable 159.0.20261102144711, Gradle 9.6.0, AGP 9.4.0, compileSdk 37.2, Build Tools 36.0.0, JDK 17.

## Scope and decisions

- The user selects GeckoView and authorizes the complete experiment. Retain the existing application ID and debug signing key for update installation.
- Chromium login/storage cannot be transferred through GeckoView APIs. Preserve saved instance addresses and old Chromium data. Request a normal server login in Gecko.
- Preserve same-origin main-frame navigation, external links, isolated instances, saved URLs, file uploads, refresh controls, and the white launcher icon.
- Keep the settled keyboard resize policy from 0.1.5 for a direct engine comparison. GeckoView must receive consumed keyboard/system insets only once.
- Use Gecko's download response stream directly. Save through Android MediaStore Downloads without refetching or copying cookies into another HTTP client. Report completion in the native UI. No background download service is added.
- Preserve foreground web audio; allow audible and inaudible autoplay for the instance origin. Native background audio, microphone access, and WebSocket persistence remain outside this experiment.
- Build ABI-specific APKs plus a universal debug APK. Deliver ARM64 for the user's phone to reduce download size. Engine binaries increase APK size. Firefox installation is not required.

## Tasks

- [x] Pin the Mozilla Maven dependency and create the process-wide runtime.
- [x] Replace WebKit profiles with persistent Gecko context IDs and adapt removal cleanup.
- [x] Replace browser navigation, progress, errors, lifecycle, refresh, permissions, and file prompts with Gecko delegates.
- [x] Save authenticated download response streams without repeating network requests.
- [x] Update build and behavior documentation, including migration limits.
- [x] Run URL policy tests, Android lint, and debug build. Inspect APK version, supported ABIs, launcher resource, and signing certificate.
- [x] Deliver a copied APK and checksum. Distinguish build verification from device-only fluidity validation.

## Device review focus

1. Two instances on one host with different ports retain separate login and localStorage after restart.
2. External links open the system browser; artifact iframe navigation stays embedded.
3. Force refresh reloads resources and preserves login and preferences.
4. File-picker cancellation, multiple selection, and session disposal never leave a pending prompt.
5. Keyboard opening/closing keeps headers visible and resizes the page once after the animation.
6. Authenticated downloads preserve their response body and incomplete downloads leave no visible partial file.

## Validation record

- Build command: `android/gradlew -p android --no-daemon :policy:test :app:lintDebug :app:assembleDebug` with the isolated JDK/SDK and preserved signing directory.
- Final build: **SUCCESS**, 2m 43s. Log: `/tmp/twicc-gecko/verify-final2.log`.
- Policy tests: **6 passed**, zero failures/errors (4 URL tests, 2 filename tests).
- Android lint: **0 errors**, **12 warnings**. Warnings cover version suggestions, backup configuration, newer/obsolete API attributes, launcher monochrome metadata, and English text internationalization. No detector is disabled.
- APK: `com.twicc.android`, version **0.2.0**, versionCode **7**, minSdk **29**, targetSdk **35**. Built against SDK **37.2**.
- ARM64 APK: **209,408,428 bytes**. Universal APK: **543,920,589 bytes**. Also built armeabi-v7a and x86_64 variants.
- ARM64 signature: `apksigner verify --verbose --print-certs` passes. Certificate SHA-256: `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a`, matching 0.1.5.
- `aapt2 dump resources` confirms the launcher background is `#FFFFFFFF`.
- The Gradle wrapper scripts and JAR are regenerated for Gradle **9.6.0**, with its published distribution SHA-256 retained.
- Independent static review finds no critical or important issue. The review's pending documentation/version notes are resolved here.

### Emulator checks

A temporary Android **15 / API 35**, x86_64 Pixel 7 emulator runs the actual APK. Tests use a disposable page and local JavaScript test controls through Gecko's debug protocol, without changing the TwiCC frontend or touching a real user server.

- APK installation/update and native instance management succeed.
- Gecko loads a valid HTTPS page. Its runtime user agent reports **Firefox/Gecko 159.0**.
- Final APK keyboard checks: height **820 → 522 → 820 CSS pixels**, exactly one window resize per open/close. Header top stays **0**, page scroll stays **0**, textarea bottom matches the new viewport height.
- A private upload selected through Android DocumentsUI returns the original filename `twicc-gecko-upload.txt` and exact contents `twicc-gecko-upload-check` to the page.
- Native JavaScript confirm returns the selected result. A user-initiated AudioContext reaches **running**. This verifies activation, not audible TTS output.
- Native force refresh preserves a persistent test cookie and localStorage. Force-stop/relaunch also preserves both. A session-only test cookie expires on process restart as expected.
- A real HTTPS `application/octet-stream` response downloads through Gecko into MediaStore Downloads with the expected **32-byte** size. No second HTTP client fetches it.
- A blob export is not saved in the tested flow. Keep this limitation explicit rather than claiming support.
- No AndroidRuntime fatal exception is observed during these checks.

On 2026-11-04, the user tests the Gecko APK on their Android phone and confirms substantially better interface fluidity than the Chromium version.

Real TwiCC streaming TTS, multiple file selection, authenticated TwiCC attachment content, and two configured server ports remain device smoke tests. Static review checks the exact Gecko container APIs and response-stream authentication path. These limits do not block delivering the requested experiment.

### Toolchain adjustments

The original Chromium toolchain cannot consume GeckoView 159. AAR metadata requires compileSdk **37.2**, while a transitive dependency requires Kotlin **2.4.20**. The build upgrades to AGP **9.4.0**, Gradle **9.6.0**, SDK **37.2** and Build Tools **36.0.0**. An earlier AGP 9.1.1 lint run stalls inside Kotlin UAST comment traversal and is terminated; the final toolchain completes lint without disabling checks. minSdk and targetSdk remain unchanged.

### Delivery

- Copied ARM64 APK: `/home/twidi/.twicc/artifacts/01a1a409-25e6-7a70-a225-a57963aa2995/twicc-android-0.2.0-geckoview-arm64-debug.apk`.
- SHA-256: `cf8d93d540df6eae3291fa83372590ff5eddbd9d0af900fd9df05e7faa5e3508`.
- Adjacent `.apk.sha256` file contains the same checksum. The copied artifact hash matches the build output.
