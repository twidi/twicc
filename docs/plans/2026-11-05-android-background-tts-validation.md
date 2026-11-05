# Android background TTS validation

Date: 2026-11-05. Recorded status: **experimental, default-off; physical acceptance pending**.

This record covers the binaries through commit `1eb2eca3e`. Commands and activation
settings below describe those recorded builds. The current Android build uses a
required device profile and enables native TTS without a build-time switch.
See [the Android README](../../android/README.md) for the current interface.

No physical phone is connected. No physical-phone screen-off gate passes in this record.
Ordinary builds omit native TTS. Both experimental builds use the same native service and player.

## Build identity and activation

- Application ID: `com.twidi.twicc` for both engines.
- Replacement version: `0.5.0`, version code `11`. Previous version: `0.4.1`, code `10`.
- Minimum API: 29. Target API: 35. No SDK or engine version changes.
- WebView dependency: AndroidX WebKit `1.14.0`.
- GeckoView dependency: `159.0.20261102144711`.
- Common playback dependency: Media3 `1.9.0`. Common transport: OkHttp `4.12.0`.
- Both builds retain the persistent debug key in `android/.signing/`.
- Native activation requires `-PnativeTts=true`; the build script exposes `--native-tts`.
- Fixture CA trust requires the separate `-PttsBoundaryTests=true` property. Delivery APKs omit that property.

From the repository root:

```sh
./android/build.sh webview
./android/build.sh gecko
./android/build.sh webview --native-tts
./android/build.sh gecko --native-tts
```

Each command runs common policy tests, flavor app unit tests, lint, and assembly.
It collects four APKs and `SHA256SUMS` in `android/dist/<engine>/`.
The experimental commands replace the ordinary files in that engine's output directory.

Both ordinary builds pass. Generated `BuildConfig.java` declares `NATIVE_TTS = false` for both engines.
Ordinary universal APK SHA-256 values before experimental replacement:

| Engine | SHA-256 |
| --- | --- |
| WebView | `481aea10faa3a2a726f399eb7b0a3c0053602964b4c4390f3b5edc61144821ca` |
| Gecko | `e3ae7db7c22508c1f5fe3ba91d129db8c6cc44f2f246a41b5655ca6ed7f8a668` |

Final experimental APKs use version `0.5.0`, code `11`.
Both signatures use certificate SHA-256 `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a`.
Manifest checks confirm the shared identity, SDK bounds, cleartext rejection, and non-exported persistent service.
The service declares `mediaPlayback|specialUse` (`0x40000002`).
Delivery APKs omit test `networkSecurityConfig`, fixture CA/key/audio, and page-adapter test assets.
Generated `BuildConfig.NATIVE_TTS` is true in both experimental builds.
Before final review corrections, prior universal Gecko and WebView APKs install with `adb -s emulator-5580 install -r`.
Those prior installs confirm version `0.5.0`, code `11`; no uninstall or app data clear occurs.
Prior installed universal SHA-256 values:

- WebView: `272ac65bed283c10cbea541b4deba3bc74c1eee712dc4c116e6ee504a11a9644`.
- Gecko: `621caeb265a0a92181fcae486b2d9c1c83b439b9bfaab7aaf9989a8f1bf6760e`.

The table below identifies the **corrected APKs** built from the sources committed with this validation update.
These corrected APKs are not installed or device-tested in this fix wave. The reused version does not identify exact bytes.

| APK path | SHA-256 |
| --- | --- |
| `android/dist/webview/app-webview-arm64-v8a-debug.apk` | `c429639fd0163dabeab7ab0d4bba86c7c00c5cb7eafb8fee039c2383f4eab053` |
| `android/dist/webview/app-webview-armeabi-v7a-debug.apk` | `c429639fd0163dabeab7ab0d4bba86c7c00c5cb7eafb8fee039c2383f4eab053` |
| `android/dist/webview/app-webview-universal-debug.apk` | `c429639fd0163dabeab7ab0d4bba86c7c00c5cb7eafb8fee039c2383f4eab053` |
| `android/dist/webview/app-webview-x86_64-debug.apk` | `c429639fd0163dabeab7ab0d4bba86c7c00c5cb7eafb8fee039c2383f4eab053` |
| `android/dist/gecko/app-gecko-arm64-v8a-debug.apk` | `fff5720adcd11c1a46c9b853f08acc70bce08f5dbc8d3323e2c42822728866cb` |
| `android/dist/gecko/app-gecko-armeabi-v7a-debug.apk` | `a79a37acecca0a77b0cd23a25b4800fc7b85375684d3687e7d92991fad670d64` |
| `android/dist/gecko/app-gecko-universal-debug.apk` | `2c403e6deb286b241448f056b1ab4f171c1a3a1bc871f17b6466c5e17dc6bd96` |
| `android/dist/gecko/app-gecko-x86_64-debug.apk` | `891119090e5f701db5453eec1c24282336389b24abb91495a4a8d7aa6f8683a3` |

The prior integration wave records a Kotlin FIR `ClassCastException` during overlapping build/edit work.
Its stable serial reruns pass without tool or dependency changes.
This final fix wave uses serial Android builds with stable native sources.
Ordinary builds pass: WebView in 65 seconds, Gecko in 66 seconds.
Experimental builds pass: WebView in 64 seconds, Gecko in 68 seconds.
Every build runs tests, lint, and assembly.
Common policy tests pass 47 cases. App tests pass 47 cases per flavor: **141 unique JVM tests**, zero failures/errors/skips.

## Final review corrections

The final review identifies three Important defects. This fix wave corrects all three.

- **I1:** the backend sends `selection_rejected` with the selected reading ID and generation.
  Native releases only a matching unopened automatic loading reservation, without a user failure.
  After visibility filtering, the backend offers the next candidate after the disposition.
  Resume repeats a lost disposition, while an in-flight selection retains its reservation.
- **I2:** native records actual audible end at `PlayerSignal.Settling`, before the ending HTTP request completes.
  Android injects `SystemClock.elapsedRealtime`; deep sleep counts. JVM tests inject a deterministic clock.
  A selected command freezes `silence_reset` only when elapsed silence exceeds 180,000 ms.
  The backend validates the optional strict boolean and freezes its full announcement before preparation.
  An omitted field means false. Exactly 180,000 ms does not reset context. No timer starts playback.
- **I3:** `tts_unavailable` disables the listener and automatic work, preserving unrelated manual playback.
  Delayed automatic summary errors retain their selected reading ID and generation.
  Native ignores stale reading errors in direct frames and state snapshots.
  Session-wide `unauthorized` and `session_lost` behavior remains unchanged.

The shared JSON disposition fixture is asserted against actual backend frames and consumed by native contract tests.
Coverage connects rejected selection, reserved playback, and admission of the next candidate.
It also connects delayed automatic errors and listener configuration loss to preserved manual playback.
Policy coverage includes manual replacement, Skip, new activation, old generation, opened audio, and old connection guards.
Silence tests cover 180,000 ms, 180,001 ms, a held ending response, preparation/replay freeze, and stale/inaudible player feedback.
Backend preparation tests retain audible-start context and manual/failure invalidation.
An asynchronous selection-stop regression also covers configuration loss with yielding summary-reader cleanup.
`disable` cancels other selection tasks while allowing its current selection to finish stop/error/disposition cleanup.
The existing epoch fences listener failures across a newer activation reserved during yielding reader cleanup.
The regression checks the new descriptor during cleanup and completed initialization afterward.
The listener lock serializes initialization; it cannot complete a newer initialization while the old selection holds that lock.

The fixes change selection, error ownership, and context calculation only.
They change no network lifetime, foreground lifetime, wake-lock behavior, or player output.
The prior thirty-minute WebView diagnostic remains evidence for that earlier binary only.
No new thirty-minute diagnostic runs for this fix wave. No Gecko or physical-phone long-duration claim is added.
The corrected delivery keeps version `0.5.0`, code `11`, as the same pre-handoff experimental build.

Minor dispositions remain explicit:

- **M1 deferred:** Gecko principal-capture failure diagnosis/recovery remains nonblocking experimental maintenance.
- **M2 corrected:** the synthetic Markdown toolbar test supplies `SUMMARY_ICON_SRC` and checks its icon binding.
  Production icon paths and Vue warning handling remain unchanged. The covering test produces no undefined-icon warning.
- **M3 deferred:** existing frontend chunk/dynamic-import and Android/Gradle deprecation warnings remain maintenance work.
  No warning-free validation claim applies.

## Current implementation repairs

The first real service/player completion test exposes `NetworkOnMainThreadException` during acknowledged shutdown.
The stack reaches `TtsConnection.close` → connection-pool eviction → TLS `close_notify` on the main looper.
Transport detachment remains immediate. The existing service worker now closes the transport.
The worker serializes resource DELETE before transport close. Audio open/read and ending retain separate workers.
Local cancellation, foreground removal, and wake-lock release do not wait for transport shutdown.

The first build-script lint run rejects an unacknowledged Media3 unstable API call in `TtsService.onCreate`.
That method now explicitly opts in. Lint remains enabled; no baseline or suppression disables this check.
Pinned dependency and SDK versions remain unchanged.

The first required backend run reports 24 failures and 402 passes.
Baseline commit `cff338e4a5643b76e44247eeac4cc10de8fe7ee2` intentionally combines the announcement and first message part.
Twenty assertions still expect separate prefix parts or the old split flag.
Four cases also expose the inherited script `max_chars` violation for a long combined prefix.

Updated assertions preserve the intentional combined request.
The shared prefix source now applies the existing request splitter to the combined first part.
A fitting short prefix still uses one request. Overlong input respects script limits and retains every word.
Announcements remain outside cleaning/adaptation. Cache identities remain unchanged.
Cancellation also clears queued prefix parts and closes the underlying source.

## Automated verification

All Python tests use `twicc.settings_test`. They use isolated data and fake providers.
No paid speech/model provider, production provider home, migration, or production server restart runs.

From the repository root:

```sh
uv run pytest tests/test_native_tts_protocol.py tests/test_native_tts_sessions.py \
  tests/test_native_tts_transport.py tests/test_native_tts_timeline.py \
  tests/test_tts_service.py tests/test_tts_views.py tests/test_summary_service.py \
  tests/test_tts_part_source.py tests/test_summary_speech.py -q
```

Final-fix result: **466 passed in 22.82 seconds**. The required list runs once after final backend sources stabilize.
Earlier 464- and 465-test runs precede the final selection-stop cleanup and activation-fence regressions.
The prior integrated result is 443 passed in 23.18 seconds.
The added script-prefix regression first fails at the `len(part) <= 30` assertion.
Its short fitting-prefix case already passes before the repair.
The independent announcement-only baseline reproduction reports 24 failures and 10 passes.

From `frontend/`:

```sh
node --test src/utils/nativeTtsClient.test.js src/stores/nativeTts.integration.test.js \
  src/stores/textToSpeech.test.js src/stores/timelineSpeech.test.js \
  src/stores/timelineSpeech.integration.test.js src/components/ui/textToSpeechButton.test.js \
  src/utils/androidAppClient.test.js src/components/session/detail/messageSummary.test.js \
  src/components/session/detail/textSelectionSummary.test.js src/components/timeline/timelineSummary.test.js
npm run build
```

Result: **226 Node tests pass**, with zero failures, cancellations, or skips.
All production frontend bundles build successfully.
These tests include the real selection summary component, frozen source/model transfer, source disposal, and native-only ownership.
They also cover terminal results, muted reattachment, manual priority, cancellation, logout, and visible one-second polling.

No global backend suite runs. Earlier Task 7 audio verification remains evidence: 890 focused backend tests pass.
Earlier real player suites pass 12 cases per flavor, including PCM, WAV-normalized PCM, MP3, focus loss, gaps, and cancellation.
This record distinguishes those earlier checks from new service/engine integration checks.

## Contract review

| Protocol boundary | Producer and consumer | Validation, cancellation, and test ownership |
| --- | --- | --- |
| `open`, `resume` | `TtsController` → `NativeTtsConsumer`/registry | `native_protocol.parse_native_command`; sequence, secret, replay, session-loss tests in protocol/sessions/transport suites |
| `listen`, `set_muted`, `disable` | Native policy → session/timeline coordinator | Scope/activation validation; disable clears automatic work; timeline and native-policy tests |
| `manual`, `select` | Native reservation → backend preparation | Reading/generation/source validation; strict optional frozen `silence_reset`; replacement/Skip/Stop all release readers and audio; sessions/timeline tests |
| `selection_rejected` | Backend selection/resume → native reservation | Exact reading/generation; only unopened automatic loading reservations release; next candidate follows disposition; shared producer/consumer fixture and policy tests |
| `started`, `finished` | Real player → owner/policy → backend | Reading/generation guards; audible output and drain/ending stay distinct; policy and real-player integration tests |
| `skip`, `stop_all` | Page/notification → native policy → backend | Immediate local cancellation; retained sequence replay before resume; policy, transport, and notification tests |
| `ping`, `pong` | Native heartbeat ↔ authenticated consumer | Protocol and acknowledged-sequence checks; authorization reload; transport tests |
| `opened_session` | Registry/consumer → native policy | Protocol, session, and secret checks; no implicit reopen after loss; policy/session tests |
| `candidate`, `audio` | Backend timeline/preparation → native policy/player | Activation/reading/generation guards; delayed old resources cannot claim current output; timeline, policy, and real-service tests |
| `state`, `error` | Backend session/consumer → native policy/UI mirror | Revision/ack/connection guards, bounded terminal results, typed session loss; backend, policy, bridge, and Node tests |

Selection reserves ownership before preparation. Audible start comes only from AudioTrack advancement.
Backend generation completion does not imply audible end. Drain and ending settlement finish a reading.
A resumed reference for a current resource cannot call play twice.
Local cancellation remains authoritative over late state, candidate, audio, or previous-document callbacks.
Silent listening uses the plain foreground service, independent of Media3 player inactivity.

## Emulator configuration

- Device: task-owned `emulator-5580`, AVD `twicc_tts_boundary`.
- System: Android 15, API 35, x86-64; image `system-images;android-35;default;x86_64`.
- Actual WebView provider: `com.android.webview`, version `124.0.6367.219`.
- GeckoView: `159.0.20261102144711`.
- Audio is enabled. Tests use real AudioTrack advancement, not synthetic silence.
- Notifications are granted for playback. Listening tests explicitly add the package to the Doze allowlist.
- The long diagnostic explicitly enables Doze, unplugs simulated charging, turns the screen off, then forces deep idle.
- Fixture traffic uses HTTPS/WSS and the isolated test CA. Real providers and production data remain unused.

Use external tool and AVD directories when reproducing. No test hardcodes this task's temporary workspace.
For example:

```sh
export JAVA_HOME=/absolute/path/to/jdk-17
export ANDROID_HOME=/absolute/path/to/android-sdk
export ANDROID_USER_HOME="$PWD/android/.signing"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
export ANDROID_AVD_HOME=/absolute/path/to/test-avds
"$ANDROID_HOME/emulator/emulator" -avd twicc_tts_boundary -port 5580 \
  -no-window -no-snapshot -gpu swiftshader_indirect
```

Install compatible APKs sequentially with `adb -s emulator-5580 install -r`.
Do not uninstall the app to switch engines. Engine cookie stores remain separate.

## Prior integration evidence (before final corrections)

These device runs precede I1–I3 corrections and the corrected delivery hashes above.
They remain earlier engine/service evidence. They do not establish a device pass for the corrected APK bytes.
No short integration or permission/Settings device run repeats in the final fix wave.


The existing `TtsPlatformBoundaryTest` gains narrow TTS integration cases, using the real service, transport, and player.
Its local HTTPS/WSS fixture replaces backend/provider responses only.
The instrumentation APK packages the actual `androidAppClient.js` and `nativeTtsClient.js` source modules.
Normal application APKs contain neither those fixture assets nor fixture TLS trust.

WebView: **7/7 new integration tests pass in 42.411 seconds**.
WebView runtime notification denial and real Settings interaction: **1/1 passes in 3.815 seconds**.
Gecko runtime notification denial and real Settings interaction: **1/1 passes in 2.972 seconds**.
Gecko: **7/7 new integration tests pass in 50.810 seconds**.

The seven short cases cover:

- Real owner/player completion, ending settlement, one resource DELETE, and service shutdown without Activity.
- Replacement audio starts while the previous real ending request remains blocked; only the replacement completes.
- Notification Stop all releases foreground ownership within five seconds despite blocked ending.
- Offline notification Skip reconciles before later eligible audio; a delayed old audio reply cannot replay.
- Actual page adapters freeze temporary summary source/model before source release and delayed real cookie export.
- An actual same-URL reload rejects delayed previous-document ingress and preserves new listening configuration.
- Visible battery preflight opens real Android Settings; returning does not activate listening.

The separate runtime permission case denies the actual Android notification dialog.
A second visible action opens channel Settings. Returning after permission grant does not start speech.

Reproduce short cases with the selected engine in `webview` or `gecko`:

```sh
cd android
./gradlew :app:assembleWebviewDebug :app:assembleWebviewDebugAndroidTest \
  -PnativeTts=true -PttsBoundaryTests=true
adb -s emulator-5580 install -r app/build/outputs/apk/webview/debug/app-webview-universal-debug.apk
adb -s emulator-5580 install -r app/build/outputs/apk/androidTest/webview/debug/app-webview-debug-androidTest.apk
adb -s emulator-5580 shell am instrument -w \
  -e class com.twidi.twicc.tts.TtsPlatformBoundaryTest \
  -e annotation androidx.test.filters.MediumTest \
  com.twidi.twicc.test/androidx.test.runner.AndroidJUnitRunner
adb -s emulator-5580 shell pm clear-permission-flags com.twidi.twicc android.permission.POST_NOTIFICATIONS user-set user-fixed
adb -s emulator-5580 shell pm revoke com.twidi.twicc android.permission.POST_NOTIFICATIONS
adb -s emulator-5580 shell am instrument -w \
  -e class com.twidi.twicc.tts.TtsPlatformBoundaryTest#visibleRuntimePermissionDenialOpensRealNotificationSettings \
  com.twidi.twicc.test/androidx.test.runner.AndroidJUnitRunner
```

For Gecko, replace `Webview` task segments with `Gecko` and `webview` paths with `gecko`.
Revoke notification permission before instrumentation starts. Revoking it inside the process kills that process.
Same-ID engine replacement preserves Android permission flags. The first Gecko permission run retains `USER_SET` from WebView denial.
That run cannot show a fresh permission dialog. Clear only these task-owned notification flags before testing first-denial behavior.
No app data clear or uninstall performs this reset.
Use compatible SDK/AVD paths from the preceding setup; do not replace fixture APKs with delivery APKs during tests.
Delivery builds omit fixture trust, so they cannot connect to this isolated certificate.

Initial short-suite failures concern fixture dispatch only: server-side socket cancellation lacks a client Call,
an unrelated browser favicon enters audio assertions, and page-source DELETE enters the audio-release count.
The fixture now sends a 1013 close, returns 404 for unrelated paths, and counts only native resource DELETEs.
Those corrections add no product behavior.
The first real owner/player shutdown case passes after the transport-close repair.
The same-player replacement case passes while the first real ending request is held.
The old operation ends aborted; only the replacement emits completed feedback.

The real browser source-transfer fixture releases its page source before native cookie completion.
It then destroys the page and Activity before releasing the real credential result and generated audio.
Backend reader reconstruction remains separately verified by `test_manual_summary_reconstructs_after_page_releases_before_acquisition`.
The descriptorless temporary source/model path remains covered by native-session and summary-service tests.
The local device fixture does not run a paid summary engine or a complete Django server.

## Prior thirty-minute screen-off diagnostic

This diagnostic precedes the final corrections. It is not a new-binary pass.
The fix wave changes no foreground, network, or wake-lock lifetime and does not repeat this diagnostic.


The anchor is `TtsPlatformBoundaryTest#screenOffArrivalAfterThirtySilentMinutes`.
Run it explicitly; it is a long diagnostic, not a normal short suite:

```sh
cd android
./gradlew :app:connectedWebviewDebugAndroidTest -PnativeTts=true -PttsBoundaryTests=true \
  '-Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.tts.TtsPlatformBoundaryTest#screenOffArrivalAfterThirtySilentMinutes' \
  -Pandroid.testInstrumentationRunnerArguments.longScreenOff=true
```

This run uses the WebView flavor and the common service. It is not a Gecko thirty-minute run.
Diagnostic APK: version `0.4.1`, code `10`, with the transport-close repair and explicit fixture trust.
Installed APK SHA-256: `802c32526d228bf613e0d647c6c10a7424e0eef89173be5f4bfec9b42bf6736d`.
The delivery APKs use the later shared replacement version. The diagnostic never changes packages during its silent interval.

Start: Unix UTC milliseconds `1793893707307`; monotonic `SystemClock.elapsedRealtime()` milliseconds `347813`.
Device log timestamp: `2026-11-05 16:48:27.375` in Europe/Paris, or approximately `15:48:27 UTC`.
The measured interval uses `elapsedRealtime`, not wall-clock subtraction or an accelerated test clock.
At start: screen asleep, deep `IDLE`, and one held `TwiCC:NativeTts` partial wake lock.

Independent observation at `16:01:12 UTC` reports:

- `mDeepEnabled=true`, `mForceIdle=true`, `mScreenOn=false`, `mState=IDLE`.
- `mWakefulness=Asleep`; current wake-lock count is one: `PARTIAL_WAKE_LOCK 'TwiCC:NativeTts'`.
- Wake-lock age is approximately 12 minutes 46 seconds.

Additional service observations report `isForeground=true`, `types=0x40000002`, `startCommandResult=2`, and no Activity binding.
Result: **PASS**, one instrumented test, zero failures/errors/skips, 1809.485 seconds.
Gradle reports `BUILD SUCCESSFUL in 30m 17s`.
End: Unix UTC milliseconds `1793895513666`, or `16:18:33.666 UTC`; monotonic elapsed milliseconds `2154171`.
The measured silent interval is **1,800,658 milliseconds**, with no audio request throughout the interval.
After the wait, one eligible event produces exactly **one** real AudioTrack start and a finished frame.
The Activity remains closed. The resource receives exactly one DELETE.
At event completion, deep state remains `IDLE` and the partial wake lock remains held for continuous listening.
The notification exposes only Stop all while waiting. Notification Stop all then removes foreground ownership and the wake lock.
A host check at `16:18:43 UTC` confirms zero wake locks and no running TwiCC service.
Fixture teardown then removes forced idle, restores charging state, and wakes the emulator.

Initial setup attempts are not passes. The first weak output check is corrected before the real interval starts.
A subsequent exact check reports that deep Doze is disabled in the AVD.
The final run explicitly enables it and confirms `IDLE` before starting the clock.

This is an instrumented emulator diagnostic with a loopback HTTPS/WSS fixture in the test process.
It verifies service lifetime, absence of opened audio during silence, and actual AudioTrack start after the wait.
It does not measure acoustic output, a physical radio, external tunnel behavior, or OEM process policy.
Instrumentation can affect process priority. It cannot substitute for the physical-phone gate.

## External physical-phone acceptance

Every row below remains **untested on physical hardware**, for both flavors.
Record phone model, Android/API version, OEM battery controls, APK hash, and observed audio for each run.

| Required condition | Physical status |
| --- | --- |
| Immediately backgrounded manual preparation, including temporary summary | Untested |
| MP3, script WAV normalization, raw PCM, and slow three-fragment playback | Untested |
| Reload, Activity recreation, recent-task dismissal | Untested |
| Skip and Stop all during a real network outage | Untested |
| Manual priority and timeline disable during unrelated manual speech | Untested |
| Actual logout and changed server password | Untested |
| Transient tunnel loss and backend restart/session loss | Untested |
| Independent second device and delayed previous-instance replies | Untested |
| Headphone disconnect, audio-focus interruption, explicit Android service stop | Untested |
| Notification permission and Settings interaction on the target OEM | Untested |
| Battery exemption denied: listening remains inactive | Untested |
| Battery exemption granted: screen locked, at least 30 silent minutes, then a new eligible event without reopening | Untested |

Do not enable native TTS by default or describe physical background support as validated until those required gates pass.
API 29–34 device behavior and bundled Gecko principal-capture error reporting also remain limits.
The existing Gecko `background.js` capture-error diagnostic issue is deferred; this task does not alter that privileged path.
