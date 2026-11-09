# Android notification sounds: validation record

Date: 2026-11-09. Source baseline: Task 4 commit `9f136294d`.

## Result and limits

Host checks pass. Both engines' instrumentation sources compile.
**Instrumentation execution and physical playback remain pending.**
No connected device or configured AVD is available. No emulator is provisioned.
No phone installation, notification, data reset, or Settings change occurs.

The release target is Gecko ARM64, version `0.5.21`, version code `32`.
The existing local build wrapper and signing configuration remain in use.
The APK is a development distribution candidate. Device acceptance is pending.

The Task 4 frontend report records 5,273 passing tests and successful production builds.
Task 5 changes no frontend source. It does not repeat the full frontend suite.

## Host verification

Direct Gradle commands use the existing toolchain:

```bash
export JAVA_HOME=/home/twidi/.local/share/twicc-android/jdk/jdk-17.0.21+9
export ANDROID_HOME=/home/twidi/.local/share/twicc-android/sdk
export GRADLE_USER_HOME=/home/twidi/.local/share/twicc-android/gradle
export ANDROID_USER_HOME=/home/twidi/dev/twicc-poc/android/.signing
export PATH="$JAVA_HOME/bin:$PATH"
```

| Command | Working directory | Result |
| --- | --- | --- |
| `./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :policy:test :app:testWebviewDebugUnitTest :app:testGeckoDebugUnitTest :app:compileWebviewDebugAndroidTestKotlin :app:compileGeckoDebugAndroidTestKotlin :app:lintWebviewDebug :app:lintGeckoDebug` | `android/` | Exit 0. Policy: 93 tests. WebView: 105 tests. Gecko: 105 tests. No failures, errors, or skips. Both instrumentation source sets compile. Both lints pass. |
| `npm run sounds:android:check` | `frontend/` | Exit 0. All ten committed sounds pass freshness checks. |
| `python3 -m unittest discover -s android/tests -v` | Checkout root | Exit 0. Eight build-script tests pass. |
| `./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:compileWebviewDebugAndroidTestKotlin :app:compileGeckoDebugAndroidTestKotlin :app:lintWebviewDebug :app:lintGeckoDebug` | `android/` | Exit 0 after final fixture edits. Both instrumentation source sets compile. Both lints pass. |
| `/home/twidi/.local/share/twicc-android/build.sh` | Checkout root | Exit 0. Policy: 93 tests. Gecko release: 105 tests. No failures, errors, or skips. Gecko release lint and ARM64 assembly pass. |

Debug lint records 73 WebView warnings and 67 Gecko warnings, with no errors.
Warnings include existing SDK/API, dependency, unused resource, and preference-write findings.
The exported read-only sound provider also produces an `ExportedContentProvider` warning.
Its export is required for system playback. Its integration fixture checks read-only access and bounded metadata.
Gradle reports deprecated features that require attention before Gradle 10.
Kotlin reports existing deprecation, unnecessary `lateinit`, and unnecessary non-null assertion warnings.

## APK verification

The release build uses no boundary-test flag. Release lint records 68 warnings and no errors.
The local wrapper preserves the existing signing configuration and keystore.

| Field | Verified value |
| --- | --- |
| APK | `android/dist/gecko/64/release/twicc-gecko-64-v0.5.21-release.apk` |
| Package | `com.twidi.twicc` |
| Version | `0.5.21`, code `32` |
| Size | 212,361,143 bytes |
| SHA-256 | `f93dfded257725e8dfc5018df9b90b8b586519b8fbb7b0df9f875584aa6bc299` |
| ABI | `arm64-v8a` |
| Android floor and target | API 29 and API 35 |
| Signature | `apksigner verify --verbose --print-certs` passes; APK Signature Scheme v2 |
| Signing certificate SHA-256 | `f7e055086fb9bfedf06ed622e65283a3d383d89cecc2cdd860a33f1221ce6b6a` |

`aapt2 dump badging` and `aapt2 dump xmltree` confirm identity, versions, SDK levels, and a non-debuggable manifest.
ZIP inspection confirms ten uncompressed WAVs, byte equality with committed assets, and the sound manifest.
The release APK contains no test certificate, test key, or `tts_boundary` CA reference.
No APK installation or artifact publication occurs in Task 5.

## Acceptance mapping

Host evidence proves source contracts and build correctness. It does not prove Android runtime behavior.

| Criterion | Available evidence | Remaining acceptance |
| --- | --- | --- |
| 1. Four defaults and silent speech | `NotificationSoundSettingsTest` compiles. It checks real channel preservation, default-channel factories, readable packaged URIs, notification audio attributes, and silent speech. | Execute on API 29 and 35. Check all four persisted defaults on a clean installation before the custom-channel fixture. |
| 2. All ten sounds selectable | Generator freshness passes. `NotificationSoundsTest` compiles with RingtoneManager discovery and byte checks for all ten entries. | Execute on API 29 and 35. Browse the Samsung picker. Listen to all ten sounds. |
| 3. Stable catalog and category count | Compiled tests cover interrupted pending writes, redundant pending rows, twelve overlapping publication requests, locale changes, scanner refresh, URI preservation, and five categories. | Execute tests. Compare rows and categories across restart and a same-signature update without clearing data. |
| 4. Correct category actions and system ownership | Compiled settings tests cover four actions, intent extras, invalid kinds, app-settings fallback, and existing custom sound/silence preservation. Task 4 frontend tests pass. | Open all four Samsung categories. Verify independent choices across events and shared scope across instances. |
| 5. Native audio without a page | Compiled provider test reads every WAV without opening an Activity. Packaged defaults do not depend on MediaStore publication. | Execute without a page. Verify native physical playback. |
| 6. No embedded web notification audio | Task 4 playback tests pass for all ten sounds, permission states, bridge/language failures, and both transports. | Confirm silence from web audio when Android blocks notifications. |
| 7. Matching foreground session suppresses notification and sound | Existing navigation fixture retains finished-work and question suppression. Task 4 web audio guard passes. | Execute with each engine. Confirm no native alert on the unlocked, interactive foreground session. |
| 8. Background boundaries and duplicate prevention | Extended navigation fixture covers both event kinds after backgrounding without navigation, screen off, and another foreground session. It retains one document and return dismissal checks. | Execute with each engine. Check locked-screen behavior and physical playback on Samsung. |
| 9. System and event controls | Compiled settings fixture preserves custom sound, silence, importance, event choices, and permission state. Existing lifecycle and eligibility implementation remains unchanged. | Check category silence, event disable, app notification blocking, and Do Not Disturb on Samsung. |
| 10. Ordinary browser behavior | Task 4 reports 5,273 passing frontend tests and successful builds. Browser scheduling, selectors, previews, and preferences remain intact. | No new Task 5 browser source change. |
| 11. Both engines and Samsung | Policy and both app unit suites pass. Both engines' instrumentation sources compile. | Execute on API 29 and 35 for each engine. Complete Samsung acceptance. |

The settings fixture seeds custom channels. It does not prove all persisted clean-install defaults.
Reconstructing a publisher tests interrupted publication. It does not prove process restart or APK-update persistence.
Synthetic push payloads enter after decryption. They test native posting, suppression, dismissal, and navigation.
They do not test FCM transport delivery or audible physical output.

## Instrumentation execution handoff

The following commands are **pending**. Do not use a phone for disposable test resets.
Use an existing authorized x86_64 emulator. Set its full serial explicitly.

```bash
"$ANDROID_HOME/platform-tools/adb" devices -l
"$ANDROID_HOME/emulator/emulator" -list-avds
export ANDROID_SERIAL="<full-disposable-emulator-serial>"
"$ANDROID_HOME/platform-tools/adb" -s "$ANDROID_SERIAL" shell getprop ro.kernel.qemu
"$ANDROID_HOME/platform-tools/adb" -s "$ANDROID_SERIAL" shell getprop ro.build.version.sdk
```

Confirm `ro.kernel.qemu` is `1`. Confirm API 29 or API 35 before any installation reset.
Reset only that disposable installation between clean scenarios and engines.
Both flavors share `com.twidi.twicc`. Install and run one flavor at a time.
Record API, serial, engine, command, and result for each run.

From `android/`, with the toolchain variables above:

```bash
./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:connectedWebviewDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.NotificationSoundsTest,com.twidi.twicc.NotificationSoundSettingsTest,com.twidi.twicc.PushNotificationNavigationTest
./gradlew -PtargetProfile=emulator -PttsBoundaryTests=true :app:connectedGeckoDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.NotificationSoundsTest,com.twidi.twicc.NotificationSoundSettingsTest,com.twidi.twicc.PushNotificationNavigationTest
```

Use separate clean runs for these commands. Repeat the pair on both API levels.
`ttsBoundaryTests=true` enables the fixture HTTPS CA only for debug instrumentation.
Do not pass this flag to release builds.

| API | WebView execution | Gecko execution | Reason |
| --- | --- | --- | --- |
| 29 | Pending | Pending | No connected emulator or configured AVD |
| 35 | Pending | Pending | No connected emulator or configured AVD |

## Clean defaults and update persistence handoff

Run this sequence separately for both engines on a disposable emulator.
Complete it before the settings fixture seeds its custom channels.

1. Start with a fresh disposable installation. Launch it and wait for catalog preparation.
2. Record the four persisted default URIs and the silent `tts_playback` channel.
3. Confirm exactly five functional categories and ten completed MediaStore rows.
4. Record each sound ID, MediaStore URI, localized title, and content hash.
5. Select a published sound for Finished work through system Settings. Set Questions to silence.
6. Select that same published sound for Peer. Confirm independent category settings.
7. Restart the process. Confirm selected sound, silence, URI rows, and category count remain unchanged.
8. Install a higher-version, same-signature APK without clearing data.
9. Launch it. Confirm the same channel sounds, silence, MediaStore URIs, and five-category count.
10. Repeat publication and change installation language. Confirm no duplicate entries and no URI replacement.
11. Test publication failure and retry. Confirm normal notifications and speech controls still work.

**Status: pending.** No system selection, restart persistence, or update persistence is claimed.

Stable defaults use `content://com.twidi.twicc.notification-sounds/<soundId>`.
System picker entries use separate MediaStore URIs. Do not compare these URI forms as equal.
Existing development channels keep their settings. Select their sounds manually.
Do not recreate channels or clear real installation data to apply defaults.

## Samsung acceptance handoff

No phone action occurs during this task. Complete this table when the user is ready.

| Check | Expected result | Status |
| --- | --- | --- |
| Open each of four event actions | Correct category opens; no extra channel appears | Pending |
| Browse the picker | All ten TwiCC-labelled sounds are selectable; vendor grouping may differ | Pending |
| Select one sound for two events | Both categories use it independently | Pending |
| Background without navigation; receive a new event | Notification posts; selected sound follows system settings | Pending |
| Display the matching loaded session, interactive and unlocked | No notification or event sound | Pending |
| Display another session; then test screen off and locked screen | Eligible notifications post; Android controls sound | Pending |
| Silence a category, disable an event, block notifications, enable Do Not Disturb | Every control remains effective; web audio never substitutes | Pending |
| Resume, restart, and update | No duplicated entries or categories; selected sound still works | Pending |
| Start, update, and stop timeline speech | Speech controls work without an extra alert sound | Pending |
| Listen to every generated sound as native audio | Complete sound plays; record any native playback failure | Pending |

## Exclusions

Existing web-player truncation and category-language diagnostics remain deferred.
No diagnosis or fix for either issue belongs to this native sound task.
A native playback failure remains a native acceptance finding. Do not redirect it into web-player diagnosis.
No backend, SDK, transport, dependency, notification eligibility, or lifecycle policy changes occur in Task 5.
