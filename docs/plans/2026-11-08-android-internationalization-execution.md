# Android internationalization execution handoff

Date: 2026-11-08.

**Implementation is complete. Native acceptance is outstanding.**
The source and generated-resource checks pass. Frontend and targeted backend
tests pass. This environment cannot compile or run Android code.
These results do not authorize a claim that native acceptance passes.

## Implemented contract

- One installation preference owns `en`, `de`, `es`, `fr`, or `pt-BR`.
  Initial state is English, revision zero. Android system language is independent.
- Both engine bridges expose `capabilities.appLanguage: 1`, `locale.get`, and
  revisioned `locale.set`. Document leases and revisions reject stale mutations.
- Embedded pages reconcile native state before login and on resume.
  Browser storage restoration does not represent explicit native selection.
- Native controls, errors, TTS presentation, and push notifications use semantic
  descriptors and packaged messages. User/provider content stays verbatim.
- New Android notifications use payload version 2 and descriptor fields only.
  Confirmed registration capability gates delivery. No locale reaches the server.
- Valid legacy text envelopes render localized generic text in a new APK.
  They retain safe tap destinations. Unknown descriptors also render generically.
  Malformed envelopes and unknown versions remain rejected.
- Existing posted event notifications retain their text. Normal TTS refreshes
  and later event notifications read the current installation language.

## Catalog and fixture maintenance

Editable translations remain in `frontend/src/i18n/locales/*.json`.
The manifest `android/i18n/messages.json` selects **256** keys for five languages.
It declares numeric formats and bootstrap resource mappings. Kotlin and Android
XML do not hold a second editable translation catalog.

From this checkout, regenerate and check explicitly:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/frontend && npm run i18n:android:generate
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/frontend && npm run i18n:android:check
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/frontend && npm run lint:i18n:android
```

Generation uses the installed compiler and Vite dependency. It writes:

- `android/app/src/main/assets/i18n/messages.json`
- `android/app/src/main/res/values/strings.xml`
- `android/app/src/test/resources/i18n/render-cases.json`
- `android/app/src/androidTest/generated/androidAppClient.js`
- `android/app/src/androidTest/generated/nativeTtsClient.js`
- `android/app/src/main/assets/i18n/sources.sha256`

The two test modules bundle the real adapters and their transitive dependencies.
They contain no unresolved external imports. They enter only the instrumentation
APK. Their sizes are 2,013,266 and 2,018,719 bytes, respectively. This duplication
keeps the existing fixture servers independent of a JavaScript dependency graph.
The Node smoke check loads both bundles and exercises a localized client error.
This check does not replace real browser-engine execution.

The fingerprint contract covers 19 source files and five generated outputs.
The sources include the manifest, both generator scripts, five catalogs, four
locale adapters, the locale manager, descriptor helpers, both page adapters,
and `frontend/package-lock.json`. The fingerprint file excludes itself.
Gradle uses Java SHA-256 before `preBuild`, without Node/npm or installation.
`android/build.sh` also requests verification before its test/lint/assembly tasks.
The opt-in CI template performs frontend dependency installation explicitly.
This execution does not install packages or enable that workflow.

The direct Kotlin checker covers main, Gecko, WebView, and policy sources.
It checks direct text, hints, accessibility text, dialogs, menus, Toasts,
notification action/channel text, and selected owned error producers.
It permits English fallback prose with a semantic descriptor on the producer.
It does not use an untranslated-message baseline. It does not trace variables,
reflection, interpolation data flows, or dynamic error provenance.
Manual source review remains required for those paths.

The persistence fixture now isolates both `app-language` and `instances`
preferences. It removes its unique fixture files after each test.
TTS instrumentation selects owned dialog buttons by Android resource ID.
The internal `TwiCC push registration` worker-thread label stays verbatim.
Its unused presentation key is removed from the manifest and all catalogs.

## Coordinated verification results

The table records the coordinated Task 11 checks. The final review fix section
below records the subsequent verification. Frontend tests and production builds
run concurrently. Final fix logs retain complete output, including warnings.

| Directory | Command | Actual result |
| --- | --- | --- |
| `frontend/` | `npm run i18n:android:check` | PASS, exit 0; 256 keys, five locales; deterministic resources and test bundles |
| `frontend/` | `npm run lint:i18n:android` | PASS, exit 0; fingerprints and direct Kotlin literals |
| `frontend/` | `npm run lint:i18n` | PASS, exit 0 |
| `frontend/` | `npm test` | PASS, exit 0; 5,092 tests, zero failures, zero skips; 129,326.132058 ms |
| `frontend/` | `npm run build` | PASS, exit 0; all six configured production bundle builds |
| Checkout root | `uv run pytest tests/test_android_push_messages.py tests/test_android_push.py tests/test_peer_external_notifications.py tests/test_ui_messages.py` | PASS, exit 0; 88 tests in 12.30 seconds |
| Checkout root | `python3 -m unittest discover -s android/tests -v` | PASS, exit 0; six tests in 1.206 seconds |
| `android/` | `./gradlew :policy:test :app:testWebviewDebugUnitTest :app:testGeckoDebugUnitTest` | BLOCKED, exit 1; Java/JAVA_HOME absent; no test task starts |
| Checkout root | `./android/build.sh webview emulator` | BLOCKED, exit 1; `Set JAVA_HOME to a JDK 17 installation.` |
| Checkout root | `./android/build.sh gecko emulator` | BLOCKED, exit 1; same JDK prerequisite |
| `android/` | `./gradlew -PtargetProfile=emulator :app:connectedWebviewDebugAndroidTest` | BLOCKED, exit 1; Java/JAVA_HOME absent; no instrumentation starts |
| `android/` | `./gradlew -PtargetProfile=emulator :app:connectedGeckoDebugAndroidTest` | BLOCKED, exit 1; same Java prerequisite |
| Checkout root | `adb devices -l` | Exit 0; empty device list |

Environment checks find no `java`, `javac`, or `sdkmanager` executable.
`JAVA_HOME`, `ANDROID_HOME`, and `ANDROID_SDK_ROOT` are unset.
`android/local.properties` is absent. `adb` exists at `/usr/bin/adb`.
No JDK, SDK, package, emulator, or device is installed during this execution.

The Gradle wrapper reports:

```text
ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH.
```

The Java fingerprint implementation is source-reviewed but remains unexecuted.
Python build tests verify task wiring with a Gradle stub. Node mutation checks
verify the same fingerprint contract. Neither result proves Gradle execution.

The inherited `VIRTUAL_ENV` warning is expected. The project environment runs
the backend tests. Visible production warnings concern ineffective dynamic
imports for provider WebSocket modules and the public-viewer API shim.
Those source paths do not change in Task 11. No full baseline warning comparison
runs. The complete frontend suite passes; its output tail is not a warning audit.
The temporary fixture-generator deprecation warning is removed before acceptance.

## Final review fixes

All five findings from the final integration review receive source fixes.
Native acceptance remains blocked by the missing toolchain and devices.

| Finding | Correction and regression coverage | Execution status |
| --- | --- | --- |
| I1: failed initial capability probe | Native events and explicit reconciliation permit a bounded, shared capability probe. Recovery reads installation state and never replays browser intent. Tests cover stale browser storage, later native selection, concurrent probes, handshake selection, and events during startup/recovery. | Frontend RED/GREEN verified |
| I2: pending and fallback rendering | Render attempts retain pending work and actual rendered locale. Reconciliation awaits pending work. Explicit selection retries catalog fallback. Passive reads do not repeatedly retry failed downloads. | Frontend RED/GREEN verified |
| M1: retained speech channel label | Normal notification builds refresh the existing channel label. The channel ID, importance, sound, vibration, and visibility remain intact. The existing presenter test now checks those properties across a language change. | Instrumentation authored; uncompiled and unrun |
| M2: channel description property | The Kotlin guard rejects direct `description` assignments and setter literals. A translated description remains allowed. | Scanner RED/GREEN verified |
| M3: retained speech settings error | BrowserActivity retains the dialog and semantic error. Its existing language observer and resume lifecycle refresh title, error, and controls. Dismissal clears retained state. The settings action and raw diagnostic fallback remain unchanged. | Instrumentation authored; uncompiled and unrun |

`setLocale()` now resolves to the locale actually applied, including English
fallback. Superseded work still resolves without applying a language.
Existing callers that ignore the result remain compatible. Controller callbacks
that return no value retain their contract: successful resolution means the
requested locale is applied. Production wiring returns the real manager result.

The controller preserves revision checks, mutation serialization, generation
ordering, and queued-intent cancellation. Ordinary browser selections and
resumes do not probe Android again. Native state events or explicit reconciliation
trigger recovery. No polling or notification replay listener is added.
The existing startup `locale.get` failure still uses the last valid snapshot.

The final fix changes no backend source, push protocol, speech ownership,
spoken content, or permission policy. Targeted backend tests are not repeated.
The 88 passing backend tests above remain Task 11 evidence.

Final verification runs after the last source change:

| Check | Final fix result |
| --- | --- |
| Focused frontend integration and scanner tests | PASS; 124 tests, zero failures/skips |
| Full `npm test` | PASS; 5,102 tests, zero failures/skips; 121,933.704724 ms |
| `npm run build` | PASS; all six configured builds |
| `npm run i18n:android:generate` and `npm run i18n:android:check` | PASS; 256 keys, five locales; both adapter fixtures regenerated |
| `npm run lint:i18n:android` and `npm run lint:i18n` | PASS |
| Native unit and focused instrumentation attempts | BLOCKED before task execution; Java/JAVA_HOME absent |

The earlier 5,092-test result is the Task 11 baseline. The final frontend count
is **5,102**, including ten new executable regressions. Complete final fix logs
retain warnings and expected failure diagnostics. The production build reports
ineffective dynamic-import warnings. No warning-free result is claimed.

Native Gradle attempts stop before task execution because Java/JAVA_HOME is absent.
Neither native RED nor native GREEN execution is claimed. Both engines still
require the native checks below, including the new settings and channel cases.

## Native acceptance still required

Use a dedicated device or x86_64 emulator with the engine prerequisites.
Select `targetProfile=64` for ARM64 devices/emulators, or `32` for ARM32.
Run the two engine instrumentation commands separately. They share an app ID.
The HTTPS boundary fixtures require the existing test-only trust property:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest :app:testGeckoDebugUnitTest
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration && ./android/build.sh webview emulator
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration && ./android/build.sh gecko emulator
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/android && ./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:connectedWebviewDebugAndroidTest
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration/android && ./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:connectedGeckoDebugAndroidTest
```

Do not add the trust property to delivery APKs. Direct Gradle users must retain
the signing identity documented in `android/README.md` for installation updates.
The release build scripts run flavor-specific unit tests, lint, and assembly.
Their successful execution remains required before shipping an APK.

All checks below remain **unrun** on both engines:

- Native screenshots and interaction in all five languages, including long labels.
- Fresh installation on a French Android system: English native UI and login.
- Login with stale browser storage: native preference remains authoritative.
- Instance A selects French; instance B selects German; return to A.
- Native dialogs, validation errors, offline screens, notification channels,
  and ongoing speech presentation update to the installation selection.
- Theme changes preserve the language and open form drafts.
- Removing the last instance and adding another preserves language ownership.
- Engine replacement preserves language, profiles, authentication, and signing.
- Real TTS playback, errors, controls, background behavior, and unchanged spoken content.
- Push delivery without an open page, encrypted delivery, safe generic fallback,
  unchanged notification destinations, and independent device preferences.
- APK-first/server-later and server-first/APK-later capability renewal.
- Old APK update guidance; old server guidance; newer unsupported catalog fallback;
  failed translation download without saved-preference mutation.
- Real Gecko delayed first content-script registration across navigation.
- Real browser-engine failure callback. `BrowserRecoveryTest` injects its consumer.
- True `MainActivity` → `BrowserActivity` lifecycle transition. Existing focused
  coverage uses two `MainActivity` instances for the transition scenario.

These are validation limits, not confirmed production defects.

## Real process-death notification check

This check is **unrun**. Ordinary instrumentation class execution skips it.
Install one engine's app APK and its matching instrumentation APK first.
Run prepare, force-stop, and verify as separate invocations:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration && adb shell am instrument -w -e class com.twidi.twicc.PushLocalizationTest#rendersVersion2AfterProcessRecreation -e pushProcessPhase prepare com.twidi.twicc.test/androidx.test.runner.AndroidJUnitRunner
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration && adb shell am force-stop com.twidi.twicc
cd /home/twidi/dev/twicc-poc/.worktrees/feature-i18n-integration && adb shell am instrument -w -e class com.twidi.twicc.PushLocalizationTest#rendersVersion2AfterProcessRecreation -e pushProcessPhase verify com.twidi.twicc.test/androidx.test.runner.AndroidJUnitRunner
```

The prepare phase persists the PID, fixture instance, and previous locale.
The verify phase asserts a changed PID, reads persisted French without an
Activity, and renders the version-2 push. It removes the fixture and restores
the previous locale. A failed/interrupted prepare requires fixture cleanup
before another preparation. Repeat the procedure with the other engine and
its matching test APK. No simulated object recreation substitutes for this check.

## Rollout and reserved operations

1. Complete the native compilation, release lint/build, and device checks above.
2. Deploy the updated server and install the updated APK with the existing signing
   identity. Either upgrade direction needs compatibility validation.
3. Open each saved instance after both sides update. Foreground reconciliation
   renews its payload capability. A confirmed version-2 registration cannot
   downgrade through an old renewal request.
4. Old APKs require an update after the server upgrade. The new server does not
   send structured pushes to an unconfirmed old registration.
5. New APKs accept valid legacy envelopes from old servers with localized generic
   text. Safe instance/tap context remains intact.

No database migration is introduced. No migration, dev-server restart, package
installation, or CHANGELOG edit runs in this task. Earlier backend changes need
a **user-run backend restart** before live acceptance. Package/JDK/SDK setup
also remains a separate user-authorized operation.
No SDK/model dependency changes require hermetic LLM diagnostics in this plan.

The previous full translation-pass baseline remains
`85f6fd78f95013ea768b575cf522f1eadab92bdf`. The Android status update does not
claim a new complete translation pass or completed native acceptance.
