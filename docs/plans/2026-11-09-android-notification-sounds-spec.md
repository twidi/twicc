# Android notification sounds specification

Status: Specification review complete. Awaiting user approval. Implementation is not authorized by this document.

## Purpose

Android owns notification sound playback inside the Android application. Sound playback must not depend on page JavaScript remaining active.

Keep the existing notification categories. Supply the complete TwiCC sound library. Let users change category sounds through Android settings.

Ordinary browsers keep their current Web Audio sounds and sound selectors.

## Agreed approach

- Keep one notification category per event family, plus the existing speech playback category.
- Supply native audio versions of all current TwiCC notification sounds.
- Set the agreed defaults when creating notification channels.
- Replace Android-embedded web sound selectors with links to the corresponding Android category settings.
- Remove web notification sound playback inside the Android application.

Rejected alternatives:

| Approach | Reason for rejection |
| --- | --- |
| One channel per event and sound | Multiplies notification categories for a sound-selection feature. |
| One channel per sound | Combines unrelated event controls and loses independent event-category settings. |
| Keep web notification playback alongside native playback | Depends on page execution and can produce duplicate sound. |

## Categories and defaults

Exactly five functional categories remain. Do not add categories for individual sounds or saved server instances.

| Category | Current channel ID | Default sound |
| --- | --- | --- |
| Finished work | `twicc-user_turn` | Chime |
| Question or approval | `twicc-pending_request` | Signal |
| Extra usage | `twicc-extra_usage` | Sweep |
| Peer message or pairing request | `twicc-peer` | Ripple |
| Timeline speech playback | `tts_playback` | None |

Speech playback keeps its existing ongoing notification and controls. Starting, updating, or stopping that notification must not add an alert sound.

Android owns category sounds, volume, vibration, silence, permissions, and Do Not Disturb behavior. TwiCC must not bypass these controls.

The four event categories apply across saved instances on the same Android installation, as they do today.

## Sound library

Provide these ten sounds from the current definitions in `frontend/src/utils/notificationSounds.js`:

`Beep`, `Chime`, `Ping`, `Pop`, `Bloop`, `Tink`, `Knock`, `Sweep`, `Signal`, and `Ripple`.

Use recognizable names such as `TwiCC — Chime` in the Android notification sound picker. Every sound must be selectable for any event category.

`None` means silence. It is not an audio asset.

Preserve the identity of the existing sounds. The current web implementation synthesizes audio; it does not contain source audio files.

Native sound assets must remain available offline, after process restart, and while the embedded page is suspended.

Packaging files alone does not satisfy sound discovery. Users must find and select all ten sounds through the system notification sound picker.

The Samsung picker may group sounds under TwiCC. Exact grouping and picker layout belong to Android and the manufacturer.

Repeated application launches and updates must not create duplicate sound entries. An ordinary application update must preserve selected sound availability.

## Settings experience

Inside the Android application:

- Keep the existing four per-instance event enable switches and existing master switches.
- Replace each event's web sound selector and web preview control with a category-settings action.
- The action opens that event's Android notification category settings.
- Explain that category sound settings apply to this Android installation, across its saved instances.
- Let users choose a TwiCC sound, another available system sound, or silence through Android settings.
- Android settings remain authoritative after returning to TwiCC. Do not overwrite the user's selection.
- Do not display the old web sound preference as the effective Android sound.
- A failed settings action must give clear feedback or offer the application's notification settings as a fallback.

Do not require a new TwiCC sound selector for the speech category. Its existing system settings remain available.

Changing a sound does not enable a disabled event or disabled Android category. Muting a category sound does not disable its visual notifications.

All new application-owned interface text uses the existing i18n system. Sound names identify the sound library consistently.

## Playback and foreground behavior

Android event sounds come only from native notifications. Do not synthesize or play web notification sounds inside either Android browser engine.

This applies to all four event families, including events received while the application is active. Do not fall back to web playback when Android notifications are blocked.

Preserve existing notification eligibility rules, event preferences, session mute behavior, browser-presence gating, and notification dismissals.

For an otherwise eligible new session notification, suppress it only when all these local display conditions hold:

1. The Android browser Activity is active in the foreground.
2. The screen is interactive and unlocked.
3. The matching instance and session are actually loaded and displayed.

In that state, the displayed session has no native notification and no notification sound. Existing in-app visual feedback remains unchanged.

Keeping a session as the last displayed page does not suppress later notifications while Android is in the background.

| Android state | Otherwise eligible notification for the last displayed session |
| --- | --- |
| Foreground, unlocked, matching session loaded | Suppressed; silent |
| Background, including while another app is open | Posted; sound follows Android category settings |
| Screen off or locked | Posted; sound follows Android category settings |
| Foreground, another instance or session displayed | Posted; sound follows Android category settings |
| Foreground, loading, login, or error screen | Posted; sound follows Android category settings |

Extra-usage and peer events have no displayed-session suppression unless existing eligibility rules independently exclude them.

Native controls and background speech playback do not count as displaying a session in the foreground.

## Existing behavior verified during specification

Source inspection confirms the intended local foreground boundary:

- `BrowserActivity.reportPushSession()` requires the foreground flag and a loaded, matching displayed session.
- `BrowserActivity.onPause()` clears that flag and reports the change. `onStop()` also removes visible-session ownership.
- `AndroidPush.showSession()` and `postNotification()` check screen interactivity and keyguard state.
- `PushNotificationPolicy` matches both the instance and session. It does not retain background read history.

References:

- `android/app/src/main/java/com/twidi/twicc/BrowserActivity.kt`
- `android/app/src/main/java/com/twidi/twicc/push/AndroidPush.kt`
- `android/policy/src/main/kotlin/com/twidi/twicc/PushNotificationPolicy.kt`
- `docs/plans/2026-11-06-android-session-notifications-plan.md`

This is a source-level finding. New device validation remains necessary before claiming the feature works on the phone.

## Unpublished installation

Only the user's development installation exists. This feature needs no legacy compatibility protocol or production migration system.

Android does not let application code replace the sound of an already-created channel. A normal update therefore cannot promise new defaults for existing channels.

For the current development installation, manually selecting the new sounds is sufficient. A clean installation validates the default assignments.

Do not automatically reset application data, recreate channels, or overwrite existing user settings to apply defaults.

## Deferred and excluded

- Diagnose or fix the current web sound truncation, including Ping and Knock.
- Diagnose or repair existing notification category translations.
- Redesign sound synthesis or select new sound identities.
- Change notification transport, backend eligibility, notification destinations, or speech playback.
- Implement per-instance Android sound categories or synchronization of sounds across devices.

Do not make either deferred diagnostic a prerequisite for this feature. Generate native assets from the intended sound definitions.

## Acceptance criteria

1. A clean Android installation has the four agreed alert defaults and a silent speech playback notification.
2. All ten named TwiCC sounds appear in the system notification sound picker and can serve different event categories.
3. Reopening or updating the app does not multiply sound entries or functional categories.
4. Each of the four Android settings actions opens the correct category. Returning preserves the system choice.
5. For an eligible event, Android plays the selected sound with the page suspended or the application in the background.
6. Android-embedded event handling produces no web notification sound, including when native notifications are disabled.
7. The foreground-state table holds, including a new event after backgrounding the same session without navigating away.
8. Another session can notify while the app remains in the foreground. No event has duplicate web and native playback.
9. Silence, event switches, blocked notifications, and Do Not Disturb remain effective under Android's rules.
10. Ordinary browser sound settings, previews, and event playback keep their existing behavior.
11. Both WebView and Gecko builds satisfy the same behavior. Verify actual picker discovery and native playback on the user's Samsung.

## Implementation freedom

The implementation plan selects audio format, asset generation, system sound publication, stable sound references, and bridge commands.

It also selects focused automated checks and device validation steps. These details must satisfy this specification without multiplying categories.

## Platform references

- [Android notification channels](https://developer.android.com/develop/ui/compose/notifications/channels)
- [NotificationChannel.setSound](https://developer.android.com/reference/android/app/NotificationChannel#setSound(android.net.Uri,%20android.media.AudioAttributes))
- [Android audio media catalog](https://developer.android.com/reference/android/provider/MediaStore.Audio.Media)

## Specification review

Two independent internal reviewers perform adversarial review on 2026-11-09. Both report no blocking specification findings.

Their mandate excludes implementation details and the two deferred diagnostics. Both verify the foreground claims against the current sources.

No product implementation or device validation occurs during this specification task.
