# Android Notification Sounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Android-embedded web notification audio with native notifications, using the complete selectable TwiCC sound library.

**Architecture:** Share the existing Web Audio sound definitions with an offline asset generator. Serve packaged WAV assets through stable native content URIs for channel defaults. Publish the same assets to MediaStore for system-picker discovery. Keep five categories and open their system settings through the existing Android bridge.

**Tech Stack:** Vue 3, Node 22, Web Audio/OfflineAudioContext, Kotlin, Android ContentProvider and MediaStore, existing UnifiedPush transport.

**Spec:** [Android notification sounds specification](2026-11-09-android-notification-sounds-spec.md).

**Status:** Two-round adversarial review complete. Ready for user review. This document does not authorize product implementation, APK installation, or phone notification tests.

## Global Constraints

- Keep exactly five functional categories. Do not add categories for sounds or saved instances.
- Defaults: `user_turn=chime`, `pending_request=signal`, `extra_usage=sweep`, `peer=ripple`; speech remains silent.
- Keep ordinary browser selectors, previews, and Web Audio behavior unchanged.
- Preserve native foreground suppression, notification eligibility, browser-presence gating, dismissals, and event preferences.
- No web notification audio inside WebView or Gecko, including when Android notifications are blocked.
- All ten sounds must be selectable in the system picker. Packaging assets alone is insufficient.
- No diagnosis or fix for current web truncation or existing category translations.
- No production migration, compatibility protocol, automatic data reset, or recreation of existing channels.
- No backend, SDK, transport, or dependency change. Current Android floor is API 29; target is API 35.
- Use existing i18n catalogs and generation. Do not hand-edit generated Android translations or fixtures.
- Stay in the shared checkout. Preserve other changes. Do not create branches, worktrees, or a CHANGELOG entry.
- Do not install packages, restart servers, install APKs, reset the phone, or send phone test notifications during plan execution without the applicable user instruction.
- Read `AGENTS.md`, `AGENTS.local.md`, `docs/internationalization.md`, and `android/README.md` before execution.

## Review Focus

1. A new channel must get a readable sound URI even before MediaStore publication finishes. Task 2 tests cold reads.
2. Concurrent startup, partial publication, and an app update must not duplicate catalog entries or change selected URIs. Task 2 tests reconciliation.
3. Changing Android sound or silence must survive channel-name refresh and app restart. Task 3 tests existing settings preservation.
4. Backgrounding the last displayed session must allow its next eligible notification. Tasks 4 and 5 preserve and validate the lifecycle boundary.
5. A blocked notification or settings failure must not restore web audio or leave a false success message. Tasks 3 and 4 test failures.

## File and responsibility map

| File | Responsibility |
| --- | --- |
| `frontend/src/utils/notificationSoundDefinitions.js` (new) | DOM-free definitions and common oscillator scheduling |
| `frontend/src/utils/notificationSounds.js` | Existing browser wrapper, labels, browser notifications, Android playback guard |
| `frontend/scripts/generate-android-notification-sounds.mjs` (new) | Chromium offline rendering, PCM WAV encoding, generated manifest and check mode |
| `frontend/src/utils/notificationSoundDefinitions.test.js` (new) | Schedule and source parity checks |
| `frontend/scripts/generate-android-notification-sounds.test.mjs` (new) | WAV encoding and generated-output verification |
| `android/app/src/main/assets/notification-sounds/` (new) | Ten committed WAVs and manifest |
| `android/app/src/main/java/com/twidi/twicc/push/NotificationSounds.kt` (new) | Sound metadata, stable URIs, serialized MediaStore publication and publication state |
| `android/app/src/main/java/com/twidi/twicc/push/NotificationSoundProvider.kt` (new) | Read-only access to the ten packaged assets |
| `android/app/src/main/java/com/twidi/twicc/push/NotificationSettingsLauncher.kt` (new) | Validated category settings intent and app-settings fallback |
| `android/app/src/main/java/com/twidi/twicc/push/AndroidPush.kt` | Initial defaults, sound-library state and new push commands |
| `android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt` | Explicit command allowlist |
| `android/app/src/main/java/com/twidi/twicc/MainActivity.kt` and `BrowserActivity.kt` | Start non-blocking catalog publication; refresh catalog titles after language changes |
| `android/app/src/main/AndroidManifest.xml` | Fixed-asset provider registration; no new permission |
| `android/app/build.gradle.kts` | Uncompressed WAV assets and generated asset freshness verification |
| `frontend/src/utils/androidPush.js` | Settings/retry bridge helpers |
| `frontend/src/utils/androidAppClient.js` and Gecko `android-app-bridge/content.js` | Early native-environment evidence, independent of language and push readiness |
| `frontend/src/components/app/NotificationSettings.vue` | Android settings actions, publication feedback; browser selectors unchanged |
| `frontend/src/i18n/locales/{en,de,es,fr,pt-BR}.json`, `android/i18n/messages.json` | New localized controls, metadata title and error messages |
| New tests listed per task | Product behavior and integration checks |
| `android/README.md` | Sound generation, user controls, manual development-install transition |

## Execution environment

Run frontend checks from `/home/twidi/dev/twicc-poc/frontend`. Existing dependencies and `/usr/bin/google-chrome` are present at planning time. Chrome reports version `153.0.8010.52`.

Use the non-Snap Chrome executable. The installed Snap Chromium cannot create profiles under hidden-home session scratch because of its AppArmor restrictions.

For direct Gradle commands, use the existing toolchain. Do not install another one:

```bash
export JAVA_HOME=/home/twidi/.local/share/twicc-android/jdk/jdk-17.0.21+9
export ANDROID_HOME=/home/twidi/.local/share/twicc-android/sdk
export GRADLE_USER_HOME=/home/twidi/.local/share/twicc-android/gradle
export ANDROID_USER_HOME=/home/twidi/dev/twicc-poc/android/.signing
export PATH="$JAVA_HOME/bin:$PATH"
```

Read `/home/twidi/.local/share/twicc-android/README.md` before any APK build. The existing wrapper builds Gecko ARM64 and runs its checks.

Use per-session scratch for Chromium profiles and temporary generation outputs. Never regenerate assets during normal Gradle builds.

Instrumented tests require a disposable x86_64 emulator. Planning-time inspection finds no connected device and no configured AVD. Before those steps, inspect `"$ANDROID_HOME/platform-tools/adb" devices -l` and `"$ANDROID_HOME/emulator/emulator" -list-avds`.

Use an existing authorized test emulator if one becomes available. Do not silently provision an AVD, install SDK packages, or use the user's phone instead. If no target exists, mark instrumentation blocked and finish host-side checks.

Set `ANDROID_SERIAL` to the selected emulator's full serial for every connected test/install command. Confirm the serial identifies the disposable emulator before any reset. The commands below assume that selection already exists.

## Task 1: Reproducible packaged sound assets

**Files:** Create the definitions, generator, tests, and asset directory from the file map. Modify `notificationSounds.js`, `frontend/package.json`, and `android/app/build.gradle.kts`.

**Interfaces:**

- `NOTIFICATION_SOUNDS`: retain current exported identifiers, including `NONE`.
- `SOUND_CONFIGS`: DOM-free object with the existing exact ten configurations.
- `getNotificationTones(soundId: string): Array<object>`: normalize current frequency sequences and explicit tones.
- `scheduleNotificationSound(context: BaseAudioContext, soundId: string, startTime: number): number`: schedule current oscillators and envelopes; return the latest stop time.
- Generator CLI: `node scripts/generate-android-notification-sounds.mjs --browser /usr/bin/google-chrome --scratch-dir <session-scratch>/sounds`.
- Generator test exports: `encodePcm16Wav(samples: Float32Array, sampleRate: number): Uint8Array` and `checkGeneratedSounds(outputDir: string): Promise<void>`. Importing the module does not run its CLI.
- Generator `--check`: verify source fingerprint, ten expected files, WAV structure, manifest durations and output hashes. Do not launch Chromium or mutate outputs.
- Generated manifest: format `1`, sample rate `48000`, mono PCM16, source fingerprint, and entries `{id, file, frames, sha256}`.

- [ ] **Step 1: Add failing source and WAV tests.**

```js
assert.deepEqual(soundIds, ['beep', 'chime', 'ping', 'pop', 'bloop', 'tink', 'knock', 'sweep', 'signal', 'ripple'])
assert.equal(scheduleNotificationSound(fakeContext, 'signal', 0), 0.28)
assert.equal(wav.sampleRate, 48000)
assert.equal(wav.channels, 1)
assert.equal(wav.bitsPerSample, 16)
assert.ok(wav.frames > 0)
```

Also assert the original frequencies, tone delays, gains, oscillator types, attack and sweep endpoints survive extraction. Reject missing, extra, modified, or stale assets in check mode.

- [ ] **Step 2: Run the new tests and confirm the intended failures.**

Run `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/notificationSoundDefinitions.test.js scripts/generate-android-notification-sounds.test.mjs`.

Expected: failures for missing definitions/generator, not unrelated setup failures.

- [ ] **Step 3: Extract the shared schedule without changing web playback timing.**

Keep labels and browser notification logic in `notificationSounds.js`. Re-export current constants so existing imports remain valid.

Keep the current web context creation, close timer and error behavior. This extraction must not diagnose or fix web truncation.

- [ ] **Step 4: Implement offline generation and produce its output files.**

Launch installed Chromium headlessly through Node child-process APIs. Use its local DevTools endpoint with Node's built-in WebSocket client. Use a fresh scratch profile, a bounded deadline, and terminate only the generator's owned browser on completion or error.

Evaluate the shared scheduling module in `OfflineAudioContext`, rendering each sound at 48 kHz. Render through the last oscillator stop plus the existing 50 ms end margin. Encode the rendered samples as mono PCM16 WAV. Do not record live WebView playback.

Write `<id>.wav` and `manifest.json` only after all renders succeed. The fingerprint covers definitions, generator and format constants. Run check-mode tests in temporary output directories, never corrupt committed fixtures during tests.

Register npm scripts `sounds:android:generate` and `sounds:android:check`. Add Java-only Gradle task `verifyAndroidNotificationSounds`, following `verifyAndroidI18n`, before `preBuild`. It verifies source fingerprints and each actual WAV's SHA-256 against the manifest, rejects missing or unexpected files/IDs, and runs without Node or browser execution. Test Gradle rejection using a fixture directory selected through `-PnotificationSoundsFixtureDir=<scratch-fixture>`; the normal build always verifies committed assets. Set WAV assets uncompressed so native file-descriptor reads work.

- [ ] **Step 5: Run source tests, generation and check mode.**

Run the Step 2 test command. Then run the generator with the current session's scratch directory. Run `cd /home/twidi/dev/twicc-poc/frontend && npm run sounds:android:check`.

Expected: ten valid non-silent WAVs, no `none.wav`, no generator-owned Chromium process left alive, and a passing freshness check.

- [ ] **Step 6: Commit only Task 1 files.**

Suggested subject: `feat(android): generate native notification sound assets`. Include a change-explaining body and the required current-model trailer.

## Task 2: Stable native defaults and discoverable system sounds

**Files:** Create `NotificationSounds.kt`, `NotificationSoundProvider.kt`, `android/app/src/androidTest/java/com/twidi/twicc/NotificationSoundsTest.kt`. Modify manifest, activities and i18n catalogs/manifest/generated outputs.

**Interfaces:**

- `NotificationSounds.ids: List<String>`: the manifest's ten sound IDs.
- `NotificationSounds.uri(context: Context, soundId: String): Uri`: `content://com.twidi.twicc.notification-sounds/<id>`.
- `NotificationSounds.publishAsync(context: Context): Unit`: single-flight background reconciliation; retry after a previous failure on the next explicit retry or foreground entry.
- `NotificationSounds.state(): String`: `preparing`, `ready`, or `error`. No credentials, instance IDs, or file paths enter bridge state.
- Provider `openAssetFile(uri: Uri, mode: String): AssetFileDescriptor`: read-only, exact allowlisted IDs; `audio/wav` MIME type.

The provider handles initial channel defaults. MediaStore handles picker discovery. This separation keeps default sounds usable during catalog preparation or a storage failure.

- [ ] **Step 1: Add failing Android integration tests.**

```kotlin
assertEquals("audio/wav", resolver.getType(NotificationSounds.uri(context, "chime")))
assertTrue(readBytes(NotificationSounds.uri(context, "ping")).size > 44)
assertEquals(10, publishedTwiccRows().size)
assertEquals(beforeUris, publishAgainAndReadUris())
```

Test a provider read before any Activity opens. Test after process restart without a loaded page. Reject unknown IDs, extra path segments and write modes. Deny provider mutations. Query results expose only fixed sound metadata, never app-private data.

Inject a failure after inserting a pending row, restart reconciliation, and assert exactly one completed row per ID. Exercise overlapping publication requests and already-completed catalog reconciliation.

- [ ] **Step 2: Run the failing integration class on a disposable emulator.**

Run `cd /home/twidi/dev/twicc-poc/android && ./gradlew -PtargetProfile=emulator :app:connectedWebviewDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.NotificationSoundsTest`.

Expected: tests expose missing native catalog behavior. Use only the emulator selected for testing. Never reset the user's phone.

- [ ] **Step 3: Implement the fixed-resource provider.**

Register authority `${applicationId}.notification-sounds`, exported for system reads. It has no arbitrary filesystem access and no write operations. Serve only packaged allowlisted WAVs through `AssetManager.openFd`. Provider startup must not publish MediaStore content or require an Activity.

Implement read metadata queries for an allowlisted sound, including display name, title and byte size. Return no rows for unknown sound IDs. Unsupported projections must fail clearly rather than expose arbitrary asset metadata.

Do not use numeric `android.resource` IDs for persistent channel sound references.

- [ ] **Step 4: Implement serialized MediaStore publication.**

Publish app-owned audio under `Notifications/TwiCC/<id>/`, with `DISPLAY_NAME="TwiCC — <localized sound label>.wav"`, `MIME_TYPE=audio/wav`, `IS_NOTIFICATION=1`, and `IS_MUSIC=0`. Use `android.push.soundTitle = "TwiCC — {sound}"` plus existing sound-label messages for the display name. These per-sound storage folders do not create notification categories.

Let the media scanner derive `TITLE` from the display name. The generated WAVs contain no embedded title that could override it. Do not depend on direct `TITLE` updates: MediaProvider can ignore them or replace them while scanning.

Use `MediaStore.Audio.Media` on the primary external volume. API 29+ permits creating and updating app-owned media without storage permissions. Do not add storage permissions or change the global default notification tone.

Reconcile by app ownership and the stable per-ID relative path, not translated title or filename. Reuse completed row URIs. Insert new rows as `IS_PENDING=1`, write and close bytes, then publish with `IS_PENDING=0`. Include the app's pending rows in recovery queries, using the API-29 pending-URI facility or the API-30+ pending query flag. Reconcile incomplete rows after interruption. Do not delete/reinsert completed rows on normal startup, language change or update.

After a language change, update `DISPLAY_NAME` in place and retain each row URI. Complete a rescan with `MediaScannerConnection.scanFile` for that app-owned row's actual path before marking its localized title ready. Reading an owned row's path for scanning does not authorize arbitrary filesystem access. Never touch another app's rows.

Cache successful reconciliation by asset fingerprint and installation locale within the current process. Run at most one publication job at a time. If the locale changes during publication, reconcile the latest locale after that job finishes. Return errors as publication state, without blocking native notifications.

Call publication on foreground entry through both shared activities and after their existing language-change callbacks. Do not run MediaStore IO on the Activity thread. Do not create a service, recurring job, polling loop or network request.

- [ ] **Step 5: Add localized catalog messages and regenerate native outputs.**

Add the title template and required existing sound-label keys to `android/i18n/messages.json`. Add new messages to all five catalogs. Use the existing generator; do not repair old category translations.

Run `cd /home/twidi/dev/twicc-poc/frontend && npm run i18n:android:generate`.

- [ ] **Step 6: Re-run integration tests and inspect picker discovery.**

Use the Step 2 command. Verify `RingtoneManager.TYPE_NOTIFICATION` discovers all ten entries, and each returned URI opens the expected WAV. Verify titles after initial publication and a forced media rescan. Change the installation language, reconcile, rescan, and assert localized labels with unchanged URI identity.

Expected: readable provider defaults, ten published entries, no duplicates, no storage permission prompt. Manufacturer grouping remains a physical-Samsung acceptance check in Task 5.

- [ ] **Step 7: Commit only Task 2 files and generated i18n outputs.**

Suggested subject: `feat(android): publish selectable notification sounds`.

## Task 3: Channel defaults and category settings bridge

**Files:** Modify `AndroidPush.kt`, `AndroidAppBridge.kt`; create `NotificationSettingsLauncher.kt`. Extend `AndroidPushBridgeTest.kt`; create `android/app/src/androidTest/java/com/twidi/twicc/NotificationSoundSettingsTest.kt`. Modify catalogs/manifest/generated outputs for native errors.

**Interfaces:**

- `NotificationSettingsLauncher.open(activity: Activity, kind: String): Unit`: accepts only `user_turn`, `pending_request`, `extra_usage`, `peer`.
- Bridge command `push.soundSettings` with `{kind}`: opens category settings, returns existing push state.
- Bridge command `push.prepareSounds` with no extra arguments: starts/retries async publication and immediately returns existing push state.
- `AndroidPush.state()` gains `soundLibrary: "preparing" | "ready" | "error"`; retain existing `status`, permission, protocol `1` and payload version `2`.

- [ ] **Step 1: Add failing bridge and channel behavior tests.**

Assert both commands reach the push handler and unknown commands remain rejected. Assert invalid kinds cannot open settings. For new channels, assert provider URI mappings for all four defaults and notification audio attributes `USAGE_NOTIFICATION`.

On a disposable emulator, set one existing category to a different sound and another to silence. Refresh the manager and category names; assert both settings remain unchanged. Check `tts_playback` remains the existing low-importance silent channel.

Intercept intents: each kind gets `ACTION_CHANNEL_NOTIFICATION_SETTINGS` with TwiCC package and the correct `twicc-<kind>` ID. If unavailable, attempt `ACTION_APP_NOTIFICATION_SETTINGS`. If both fail, return an existing localized bridge error or a new catalog-backed error, never success.

- [ ] **Step 2: Run the relevant failing tests.**

Run `cd /home/twidi/dev/twicc-poc/android && ./gradlew :app:testWebviewDebugUnitTest --tests com.twidi.twicc.AndroidPushBridgeTest`.

Run the Task 2 instrumentation command with class `com.twidi.twicc.NotificationSoundSettingsTest`.

- [ ] **Step 3: Apply defaults only when a category is first created.**

In `AndroidPush.manager()`, use `NotificationSounds.uri()` and `AudioAttributes.USAGE_NOTIFICATION` for new channels. Preserve existing channel objects and update names only, as today. Keep speech behavior unchanged.

Implement the allowlisted settings launcher and commands. Keep the existing permission flow separate. Publication retry must not request notification permission, enable an event, or change category sound settings.

- [ ] **Step 4: Add state and localized failure messages.**

Expose publication state through existing push-state/heartbeat responses. Do not add capability negotiation or old-APK fallback protocols for this unpublished feature. Unknown-command errors from an old APK remain errors; they must not trigger web audio.

Regenerate native i18n outputs after any new error key.

- [ ] **Step 5: Re-run unit/instrumentation tests and confirm existing settings survive.**

Expected: exact default mapping on clean channel creation; stable settings for existing channels; correct category destination; bounded failure feedback.

- [ ] **Step 6: Commit only Task 3 files and generated outputs.**

Suggested subject: `feat(android): open notification sound category settings`.

## Task 4: Replace embedded web audio and selectors

**Files:** Modify `notificationSounds.js`, `androidPush.js`, `androidAppClient.js`, `androidAppClient.test.js`, `androidGeckoTransport.test.js`, `NotificationSettings.vue`, `NotificationSettings.android.test.js`, `androidPush.test.js`, and all five locale catalogs. Modify `android/app/src/gecko/assets/android-app-bridge/content.js`. Create `frontend/src/utils/notificationSounds.test.js`. Update generated Android i18n files after catalog changes.

**Interfaces:**

- Keep `playNotificationSound(soundType)` public. Return before creating an AudioContext when `isAndroidApp.value` is true.
- `openAndroidNotificationSoundSettings(kind: string): Promise<void>`: use the existing client to request `push.soundSettings`; surface rejection to the caller.
- `prepareAndroidNotificationSounds(): Promise<void>`: request `push.prepareSounds`; merge returned state through existing state handling.
- The component uses current `androidPushState.soundLibrary`; it does not infer effective native sound from web preferences.
- Gecko environment acknowledgment: `{channel: "twicc.android.environment", protocol: 1, id: <getState request id>}`. This is transport evidence, not a permission or capability protocol.

- [ ] **Step 1: Add failing playback and rendering tests.**

With mocked AudioContext creation, assert zero contexts for all ten sounds inside Android, even when native permission is denied or push status is an error. Assert ordinary browsers still schedule a selected sound. Keep `none` silent everywhere.

Test zero contexts before menu handshake when locale data is incompatible or `locale.get` fails. Test a timed-out native `getState` on both engines: transport evidence still marks Android, so a later event remains silent. An ordinary browser with no native transport still times out normally and keeps web sound playback.

Extend the component SSR fixture: Android renders four independent event switches and four category-settings actions, without web sound selectors/previews. Ordinary browser rendering keeps selectors, previews, and permission behavior. Keep toast and extra-usage controls intact.

Test each action sends the matching kind. On bridge rejection, show localized error feedback and do not claim success. `soundLibrary=preparing` shows a preparation hint. `error` shows a retry action. `ready` removes that hint; publication status never overwrites push registration status.

- [ ] **Step 2: Run failing frontend tests.**

Run `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/notificationSounds.test.js src/utils/androidPush.test.js src/utils/androidAppClient.test.js src/utils/androidGeckoTransport.test.js src/components/app/NotificationSettings.android.test.js`.

- [ ] **Step 3: Guard playback at the common entry point.**

Use the existing `isAndroidApp` signal. Keep detection independent from notification permission or registration readiness. Do not add user-agent sniffing or a web-audio fallback.

In `createAndroidAppClient`, mark Android immediately when the native WebView `TwiccAndroid.postMessage` transport exists. Mark Android after a valid matching native response independently of locale/push state.

For Gecko, acknowledge an origin/source-validated `getState` request in the document-start extension before awaiting `sendNativeMessage`. The frontend accepts the environment acknowledgment only for an outstanding `getState` request ID and the current source/origin. Store the command with each pending request for this check. This evidence marks Android even if the native reply later times out or its language data is incompatible.

Keep `main.js`'s existing await of locale initialization before router/bootstrap. The early transport evidence therefore arrives during startup probing. Do not add another startup timeout or gate ordinary browsers on the menu handshake. No native transport evidence means the normal browser path; no guessed user-agent state.

Keep existing notification call sites and visual effects unchanged. No change to close timers, gains, envelopes or the deferred truncation issue.

- [ ] **Step 4: Replace Android-only controls and add bridge helpers.**

In each existing event group, render the settings action for Android and the current selector/preview for ordinary browsers. Use one shared explanatory hint: settings apply across this installation's saved instances.

Keep category actions usable when permission is denied. Do not add a speech selector. Do not show an unverified native sound label. Preserve web preferences for ordinary browsers rather than overwriting them.

Add localized controls, preparation/error/retry hints, and error callouts. Let Android's category page provide preview and selection. Retry publication explicitly through its helper; existing heartbeat refreshes status without new polling.

- [ ] **Step 5: Run focused tests, generation and frontend verification.**

Run the Step 2 tests. Then run:

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm run i18n:android:generate
cd /home/twidi/dev/twicc-poc/frontend && npm run i18n:android:check
cd /home/twidi/dev/twicc-poc/frontend && npm run lint:i18n:android
cd /home/twidi/dev/twicc-poc/frontend && npm test
cd /home/twidi/dev/twicc-poc/frontend && npm run build
```

Expected: generated outputs current, tests pass, production bundles build. Run ordinary i18n lint on touched controls/catalogs if needed; unrelated failures remain separately identified.

- [ ] **Step 6: Commit only Task 4 files and generated outputs.**

Suggested subject: `feat(android): replace web notification sound controls`.

## Task 5: Integrated validation and execution handoff

**Files:** Extend `android/app/src/androidTest/java/com/twidi/twicc/PushNotificationNavigationTest.kt` only where its existing scenarios miss acceptance. Modify `android/README.md`. Create `docs/plans/2026-11-09-android-notification-sounds-validation.md` during execution.

**Interfaces:** Consume the final provider, catalog, channel, settings action and frontend contracts from Tasks 1–4. Change no notification eligibility or lifecycle policy.

- [ ] **Step 1: Map acceptance to existing integration coverage.**

The navigation fixture already checks a new question for the same session after `moveTaskToBack(true)`, then a screen-off notification. Retain those scenarios. Add missing finished-work coverage for the same transitions, plus foreground other-session delivery, if not already covered.

Use synthetic native payloads on the disposable emulator. Existing lifecycle behavior should already pass; do not force artificial failures or rewrite that policy for this sound feature.

- [ ] **Step 2: Run both engines' checks on the disposable emulator.**

Run Gradle `:policy:test`, `:app:testWebviewDebugUnitTest` and `:app:testGeckoDebugUnitTest`. Run the two new native integration classes and `PushNotificationNavigationTest` through both `connectedWebviewDebugAndroidTest` and `connectedGeckoDebugAndroidTest` with `-PtargetProfile=emulator -PttsBoundaryTests=true`.

The boundary flag enables the fixture HTTPS CA only in debug tests. Never add it to release builds. Example WebView run:

```bash
cd /home/twidi/dev/twicc-poc/android && ./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:connectedWebviewDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.NotificationSoundsTest,com.twidi.twicc.NotificationSoundSettingsTest,com.twidi.twicc.PushNotificationNavigationTest
```

Run the matching Gecko task with the same class filter and flags.

Use separate engine install runs: both flavors share the package ID. Before clean-default tests, reset only the disposable emulator installation. Never reset the real device.

Record the command, device API, engine, result, and any blocked step. Automated posting tests prove notification selection and policy; they do not prove audible physical playback.

- [ ] **Step 3: Verify clean defaults and update persistence.**

On the emulator, prove the four default sound URIs and silent speech channel from clean creation. Select a different published sound for one category and silence another through system settings. Restart and install a same-signature updated APK without clearing data. Assert channel settings, published MediaStore URIs and the five-category count remain unchanged.

Include concurrent/repeated catalog reconciliation. Keep publication failures from breaking normal native notifications or TTS controls.

- [ ] **Step 4: Build the phone artifact when implementation validation is ready.**

Re-read the local build README. For a distributable APK, re-read current version fields and choose the next unused patch version/versionCode; do not overwrite another agent's version change. Update `android/README.md` consistently.

Run `/home/twidi/.local/share/twicc-android/build.sh`. Expected: policy/unit/lint checks and Gecko ARM64 release assembly pass. Preserve the signing key. No CHANGELOG update.

Do not install or send test notifications until the user is ready. If an artifact is published, first load the TwiCC artifact-authoring skill.

- [ ] **Step 5: Complete Samsung acceptance with the user.**

| Check | Expected result |
| --- | --- |
| Open each of four category actions | Correct Android category; no extra channel |
| Browse the system sound picker | All ten TwiCC-labelled sounds are selectable; exact grouping may differ |
| Select one sound, then select it for another event | Both event categories can use that sound independently |
| Background app without navigating away; receive a new event | Notification posts and its selected sound plays under system settings |
| Foreground matching loaded session, interactive/unlocked screen | No notification and no event sound |
| Foreground another session, screen off, and locked screen | Eligible notification posts; system rules control sound |
| Set category silence; disable event; block app notifications; enable Do Not Disturb | Each setting remains effective; web audio never substitutes |
| Resume, restart, and update app | No duplicated sound entries or categories; selected sound still works |
| Start, update and stop timeline speech notification | No additional alert sound; existing speech controls work |

Listen to each generated sound as native audio. Record a native playback failure if one occurs, without opening the deferred web-player diagnosis.

If the user is unavailable, mark phone acceptance pending. Do not claim complete physical validation.

- [ ] **Step 6: Update documentation and the validation record.**

Document generation/check commands, the shared canonical definitions, system category controls, system-wide per-installation scope, and manual sound selection for the user's existing channels. Document the distinction between stable default URIs and picker catalog entries.

Record which spec criteria pass and which remain pending. Include deferred web truncation and category-language diagnostics as exclusions, not blockers for native sound implementation.

- [ ] **Step 7: Commit only Task 5 changes.**

Suggested subject: `test(android): validate native notification sound behavior`. Include the release version change only if a distributable APK is produced.

## Self-review and acceptance mapping

| Spec acceptance | Owning tasks |
| --- | --- |
| 1: Defaults and silent speech | 2, 3, 5 |
| 2: All sounds selectable | 1, 2, 5 |
| 3: Stable catalog and category count | 2, 3, 5 |
| 4: Category navigation and system ownership | 3, 4, 5 |
| 5: Native audio without page execution | 2, 3, 5 |
| 6: No embedded web audio, including blocked notifications | 4, 5 |
| 7–8: Foreground/background boundary and duplicate prevention | 4, 5 |
| 9: System and event controls | 3, 4, 5 |
| 10: Ordinary browser behavior | 1, 4 |
| 11: Both engines and Samsung | 5 |

Dependencies: Task 1 → Task 2 → Task 3 → Task 4 → Task 5. Each task ends with focused verification before its commit.

Commit subjects are suggestions. Every commit requires an explanatory body and the exact current-model co-author trailer from project instructions. Stage explicit files only.

## Platform references

- [Channel sound ownership and settings intents](https://developer.android.com/develop/ui/compose/notifications/channels)
- [Persistent sound URI guidance](https://developer.android.com/reference/android/app/NotificationChannel#setSound(android.net.Uri,%20android.media.AudioAttributes))
- [App-owned media, pending writes and scoped storage](https://developer.android.com/training/data-storage/shared/media)

## Plan review record

Two independent internal reviewers inspect the plan on 2026-11-09. Both re-review the corrected plan. Both report no remaining blocking findings in their respective scopes.

| Initial finding | Correction |
| --- | --- |
| Snap Chromium cannot use hidden-home session scratch | Select installed non-Snap Chrome `153.0.8010.52` |
| Published MediaStore titles are scanner-owned | Use localized display filenames, stable per-ID paths and rescan; preserve row URIs |
| Android environment can remain unknown after language errors or native timeout | Use early transport evidence independent of language readiness; add startup regression checks |
| Gradle freshness check must validate WAV content | Verify actual output hashes, expected IDs and missing/extra files in both check paths |
| HTTPS navigation fixture needs its test CA | Add `-PttsBoundaryTests=true` to the affected debug instrumentation commands |

The plan also records the missing emulator prerequisite, requires explicit target serial selection, and corrects the integration-class count.

This is implementation-plan review. Concrete implementation decisions are in scope. Deferred web-player and existing translation diagnostics remain out of scope.

No product code, asset generation, build, or instrumented test runs during this planning task. Environment inspection and document verification do run.
