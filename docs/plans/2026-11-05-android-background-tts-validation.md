# Android background TTS validation

Date: 2026-11-05. Status: **experimental, default-off; physical acceptance pending**.

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
| WebView | `0ffcacac055ab701570e619dc260dffd5d3ce41a6ac7b91a7ea818ae40ce953f` |
| Gecko | `2735453a8bdcc0a04fe25659959f3d5625e30c8db0602b663aef99eb6a033bd8` |

Final experimental APKs use version `0.5.0`, code `11`.
Both signatures use certificate SHA-256 `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a`.
Manifest checks confirm the shared identity, SDK bounds, cleartext rejection, and non-exported persistent service.
The service declares `mediaPlayback|specialUse` (`0x40000002`).
Delivery APKs omit test `networkSecurityConfig`, fixture CA/key/audio, and page-adapter test assets.
Generated `BuildConfig.NATIVE_TTS` is true in both experimental builds.
The delivery universal Gecko APK then WebView APK each install successfully with `adb -s emulator-5580 install -r`.
Package inspection after each replacement confirms version `0.5.0`, code `11`. No uninstall or app data clear occurs.

| APK path | SHA-256 |
| --- | --- |
| `android/dist/webview/app-webview-arm64-v8a-debug.apk` | `272ac65bed283c10cbea541b4deba3bc74c1eee712dc4c116e6ee504a11a9644` |
| `android/dist/webview/app-webview-armeabi-v7a-debug.apk` | `272ac65bed283c10cbea541b4deba3bc74c1eee712dc4c116e6ee504a11a9644` |
| `android/dist/webview/app-webview-universal-debug.apk` | `272ac65bed283c10cbea541b4deba3bc74c1eee712dc4c116e6ee504a11a9644` |
| `android/dist/webview/app-webview-x86_64-debug.apk` | `272ac65bed283c10cbea541b4deba3bc74c1eee712dc4c116e6ee504a11a9644` |
| `android/dist/gecko/app-gecko-arm64-v8a-debug.apk` | `f0d11dba9c6cc8e194e822d17aa352c3dd18f2a003f50b7c8467b12adee14147` |
| `android/dist/gecko/app-gecko-armeabi-v7a-debug.apk` | `a72308126a70d68da03d3c489cff774edbb909ee62d495ad65df24caa1fad65d` |
| `android/dist/gecko/app-gecko-universal-debug.apk` | `621caeb265a0a92181fcae486b2d9c1c83b439b9bfaab7aaf9989a8f1bf6760e` |
| `android/dist/gecko/app-gecko-x86_64-debug.apk` | `23e752fff00b20a3e66b6ce5e4ff77b9633be659653159e78e0bd61b67077399` |

The first experimental Gecko build fails with an internal Kotlin FIR `ClassCastException` during overlapping build/edit work.
A serial run with stable sources passes in 57 seconds. The serial WebView run passes in 48 seconds.
No tool or dependency changes address this compiler failure. Both final runs retain all tests and lint.
Common policy tests pass 41 cases. App unit tests pass 45 cases per flavor: **131 total**, zero failures/errors/skips.

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

Result: **443 passed in 23.18 seconds**.
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
| `manual`, `select` | Native reservation → backend preparation | Reading/generation/source validation; replacement/Skip/Stop all release readers and audio; sessions/timeline tests |
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

## New integration evidence

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

## Thirty-minute screen-off diagnostic

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
