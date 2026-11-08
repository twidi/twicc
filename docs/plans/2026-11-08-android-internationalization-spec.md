# Android internationalization specification

Status: Approved by the user after adversarial review. Tasks 1–11 are implemented. Native compilation and device acceptance remain outstanding. See [the execution handoff](2026-11-08-android-internationalization-execution.md) for exact results and environment limits. The original decisions and review history remain below.

## Intent

A language selection applies to the whole Android installation and every saved TwiCC instance.
Native screens, embedded application pages, and new notifications use that language.
The backend sends message identity and data. It never selects or renders a language.

## Requirements and decisions

The user requires these behaviors:

- Translate every application-owned Android interface message into every supported interface language.
- Start in English before the user selects a language. Do not detect the system language in this phase.
- Propagate an explicit language selection from an embedded instance to Android and all other embedded instances.
- Use the existing dotted message keys and descriptor model. Reuse existing equivalent messages exactly.
- Support named placeholders and plurals, with results consistent with the web application.
- Keep the backend language-agnostic. Do not send or store an Android language there.
- Localize Android push notifications on the device.
- Do not require existing posted notifications to change language. Add this only if it is trivial.

This specification makes these proposals from the preceding analysis:

- Each Android installation owns an independent preference. Different devices may use different languages for the same servers.
- Existing catalogs remain the translation source. Generate Android presentation resources from their relevant messages.
- Keep both native and web menus. Shared keys prevent translation divergence; menu redesign is outside scope.
- Existing posted push notifications keep their text. Do not add notification history or replay for translation.
- New push production uses descriptors only. Old APKs require an update to restore delivery after the server upgrade.
- An upgraded APK shows valid legacy text pushes as localized generic notifications, without displaying their application-owned English prose.

The user approves these proposals, including the old-APK delivery trade-off, before implementation planning.

## Current behavior

The web application uses flat dotted keys in `frontend/src/i18n/locales/*.json`.
English is the source catalog. The supported locales are `en`, `de`, `es`, `fr`, and `pt-BR`.
The browser saves its language in `twicc-settings`, separately for each origin and engine profile.

`frontend/src/i18n/index.js` provides language loading, fallback, and plural rules.
`src/twicc/core/ui_messages.py` provides descriptors with `key`, `params`, and optional `count`.
Existing browser responses can also contain English fallback text. The backend does not translate them.

Android currently combines `strings.xml` resources and Kotlin string literals.
Some Kotlin messages travel through the native bridge and appear in the web interface.
The `:policy` module also produces validation messages without access to Android resources.

Push delivery already enters `AndroidPushService` before Android constructs each notification.
The backend currently sends rendered English titles and bodies.
Therefore, device-side translation does not require a new push transport or a running embedded page.

The supplied inventory estimates 127 messages, including 18 backend push messages.
This is a starting inventory, not a coverage limit. Review all application-owned presentation paths.

## Language ownership and lifecycle

### Installation preference

- One persistent preference belongs to the Android installation, not to an instance or server account.
- Its initial value is `en`, including upgrades without a saved Android language.
- An existing web-profile preference does not count as a new user selection during startup.
- An unsupported or invalid stored language resolves to English.
- Closing the app, process recreation, and replacing the engine build preserve the preference.
- Removing an instance does not reset the preference. A newly added instance inherits it.
- Changing the Android system language does not select another TwiCC language.
- Another Android installation and an ordinary browser retain their own preferences.

The supported language set matches the web application for the coordinated release.
Adding a language later requires Android resources and compatibility checks as well as web catalogs.
An embedded page cannot select a language unavailable in the installed APK.
Reject that selection with a localized update explanation, retain the installation preference, and restore the page to that preference.

### Embedded pages

The native preference takes precedence before an embedded page displays its localized application interface, including login.
A stored per-instance web preference cannot create a visible language reversal during startup.
The same rule applies after page reload, instance switching, resume, or engine replacement.

An explicit user selection in a compatible embedded page updates the installation preference.
Native controls and the selecting page update without requiring an app restart.
Other running pages update when active; suspended pages reconcile before becoming interactive.
An instance that is added or opened later uses the latest installation preference.

Initialization, localStorage restoration, and notifications of native changes are not user selections.
They cannot overwrite a more recent explicit selection or create a feedback loop.
Delayed work cannot replace a newer language choice with an older one.

Native screens work offline with the stored language and packaged translations.
Synchronization does not depend on authenticating to the backend.
It does not alter any unrelated settings, instance data, or profile isolation.

If a web language download fails, keep the saved preference and follow the existing English fallback behavior.
Do not propagate the temporary fallback as a new language selection.
The next successful load restores the selected language.

## Translation source and rendering

The existing JSON catalogs are the authoritative editable source for interface translations.
Android consumes generated presentation resources for its relevant keys.
Contributors do not maintain a second manual copy of shared translations in Android XML or Kotlin.

- Preserve existing dotted keys, meanings, translations, and named parameters when reusing a message.
- Share a key only when the message has the same purpose and meaning.
- Add Android-specific keys to every catalog using the existing naming rules.
- Translate into English, German, Spanish, French, and Brazilian Portuguese.
- Apply `docs/i18n-glossary.md`, including register and retained technical terms.
- Include translations in the APK. Native translation never requires fetching a server catalog.
- Render messages as text. Do not evaluate message data as code or HTML.
- Preserve user and provider content verbatim. It remains data, not translation source.
- Format interface-owned numbers and quantities in the selected language, without backend preformatting.

Named placeholders and plural counts retain their meaning across web and native consumers.
Plural selection, zero handling, literal escapes, and interpolation produce equivalent results for shared messages.
In particular, preserve the existing French two-form and explicit three-form message behavior.
Do not concatenate English suffixes or translate sentence fragments independently.

Android and Vue I18n use different message syntaxes.
Resource generation must reject unsupported message constructs rather than silently changing their meaning.
The implementation plan selects the generation tools and native formatting APIs.
This specification does not prescribe a custom Kotlin parser or a third-party translation library.

## Message coverage

Translate application-owned text in these surfaces:

- Instance management, forms, validation, confirmation dialogs, and empty states.
- Native navigation, recovery controls, download messages, file-selection controls, and external-link prompts.
- Accessibility labels, tooltips, errors, toasts, and other application-owned control text.
- Native TTS controls, status, errors, service notifications, and notification actions.
- Push notification titles, bodies, actions, channel names, and application-owned channel descriptions.
- Native bridge errors and native messages displayed by the web application.

System-owned permission dialogs, browser-engine UI, file-picker UI, and other OS-owned controls remain system-managed.
Their language can differ when the Android system language differs.
Application-owned text surrounding these controls still follows the installation preference.
Speech content, voice selection, and spoken-language detection do not change with interface language.

The web application's documented content exclusions remain in effect.
Prompts, agent output, names, files, terminal output, raw external diagnostics, CLI, and MCP output remain unchanged.
This work does not redesign or localize unrelated external notification services.

## Shared message descriptors

Reuse the existing semantic message contract: `key`, named `params`, and optional numeric `count`.
Do not introduce an Android-specific backend translation protocol or backend catalog renderer.
Application-owned wording in the new Android push payload consists of descriptors, not server-rendered sentences.
Parameter values may contain verbatim user content and external diagnostics. They are not translated by the backend.

Native bridge messages destined for web rendering carry the same semantic metadata.
The web interface resolves that metadata using its existing message helpers.
Preserve metadata through asynchronous responses, cached errors, and local error boundaries.
Visible application errors and persistent in-app messages update when the language changes.

The `:policy` module supplies typed validation outcomes without depending on Android resources.
The presentation layer maps those outcomes to shared keys and parameters.
Do not infer message identity by matching English text.

Existing English fallback fields in unrelated web APIs need no repository-wide removal.
They remain diagnostic and compatibility fields, not a second backend translation system.

## Android push notifications

### Device-side rendering

The backend emits semantic titles and bodies using the shared descriptor contract.
Android renders them with the installation preference when constructing the notification.
This works when no instance page is open and after the app process is recreated for delivery.
Normal Android delivery restrictions still apply; this work does not expand delivery guarantees.

The same event can render in French on one device and German on another.
Changing language requires no server update, push subscription renewal, or per-instance language registration.
Notification content uses the current preference at rendering time, not the language at event creation.

Preserve existing notification kinds, opt-ins, mute behavior, visibility suppression, expiry, dismissal, and tap destinations.
Preserve current transport size and validation limits. Structured content must still fit within them.
Truncation may shorten user-content parameters, but must not corrupt message identity or descriptor structure.

Valid notifications with an unknown key or an incompatible message descriptor use a packaged, localized generic message.
This includes a known key whose supplied parameters cannot satisfy the installed message contract.
They retain safe navigation and instance context. Do not display the raw key or require server-rendered fallback prose.
Malformed or unsupported envelopes follow explicit compatibility and validation rules.

### Already displayed notifications

New push notifications use the current language.
Already posted event notifications may retain their original text after a language change.
Do not add storage, replay, or new background work solely to translate those notifications.
Existing service notifications use the current language when their normal lifecycle next refreshes them.
Application-owned channel labels use the selected language without resetting channel IDs or user notification settings.

## Compatibility and rollout

APK and server updates are independent. Compatibility must be explicit for both bridge and push messages.
Do not assume every saved instance serves the same frontend version.

Full synchronization requires a compatible embedded frontend and APK.
An older frontend that lacks the language bridge cannot promise installation-wide synchronization.
A compatible client identifies that limitation through a localized compatibility status; it never treats stale web state as a new selection.
An ordinary browser, and a web frontend without native support, retain their existing browser-local language behavior.
Compatibility notices appear in components capable of displaying them. Release documentation covers older clients without that capability.

Push payloads need an explicit compatibility boundary for structured messages.
Protocol support may be exchanged or stored as capability data; language must not be exchanged or stored.
New Android push production must not add a server-rendered English or per-language parallel path.
An APK that cannot decode structured notifications requires an update to restore push delivery after the server upgrade.
New servers do not send legacy English notifications to preserve delivery to those APKs.
Compatible components show an update-required status rather than claiming that registration is fully supported.
Older clients may be unable to show that status. Release documentation must explain the delivery loss and required APK update.

A compatible APK connecting to an older server shows a localized push-format limitation.
It renders valid legacy text notifications as packaged generic messages in the installation language.
It preserves safe navigation and instance context, but does not translate or display the legacy application-owned prose.
Existing validation, expiry, preference checks, suppression, dismissal, and safe tap handling still apply before generic rendering.
The implementation plan defines the negotiation mechanism, without changing these observable outcomes.
Approval of this specification includes approval of this explicit old-APK delivery trade-off.

## Acceptance criteria

| Scenario | Required result |
| --- | --- |
| Fresh installation on a French-language Android device | Native app and compatible instance pages start in English. |
| Upgrade with French saved only in an old web profile | Android starts in English; stale web storage does not select French. |
| Select French in instance A | A and native controls use French without restarting Android. |
| Add or open instance B | B uses French, including its login screen. |
| Select German in B, then return to A | A uses German; an old callback cannot restore French. |
| Resume a suspended page after a selection elsewhere | It reconciles with the installation language before interaction. |
| Restart the process, work offline, or replace the engine APK | Native screens retain the selected language. |
| Remove the last instance, then add another | The new instance inherits the saved Android language. |
| Same server registered on two devices with different languages | Each device renders the event in its own language. |
| Receive a push without an open page | Android renders it using the stored installation language. |
| Change language after a push is posted | Future pushes change; existing posted text may remain unchanged. |
| Render plural messages with zero, one, and multiple items | Native and web results match each language's existing message rules. |
| Show an Android validation error or bridge failure | Its application-owned text uses the selected language. |
| Change language with a persistent in-app error visible | The error updates without losing semantic metadata. |
| Fail to load a web translation chunk | Existing fallback works; the saved Android preference remains unchanged. |
| Receive an unknown message key in a supported push envelope | Show a localized generic message, not a raw key. |
| Select a web language absent from the installed APK | Keep the installation language, restore the selecting page, and explain the required APK update. |
| Receive a valid legacy text push on an upgraded APK | Show a localized generic notification with safe navigation and instance context. |
| Upgrade the server while keeping an incompatible old APK | Push delivery requires an APK update; release documentation states this limitation. |
| Connect incompatible frontend, APK, or push protocol versions | Expose the compatibility limitation; do not claim full synchronization or translated delivery. |
| Change language while notifications are configured | Channel settings, subscriptions, opt-ins, suppression, and navigation still work. |
| Inspect backend requests and device registration records | No Android locale or language preference appears. |

Coverage includes both Gecko and WebView builds and all five supported languages.
Translation review checks meaning, glossary terms, placeholders, and plural behavior, not only key parity.
Controls must remain usable with longer translations and accessibility text.
Instrumentation checks must not depend on English button text.

## Alternatives and scope boundaries

System-language selection does not meet this phase's explicit English default and shared user preference.
Backend rendering would require language storage and a second translation execution path. It is excluded.
Separate manually maintained Android catalogs would duplicate shared messages. Generated resources avoid that duplication.
Runtime consumption of shared JSON remains a possible implementation alternative if generation cannot preserve required semantics.
Any such choice must keep one editable translation source and equivalent rendering behavior.

Implementation details belong in the subsequent plan: tool choice, APIs, storage names, wire version fields, generation layout, and test files.
Automatic language detection, Android system language-picker integration, menu redesign, and notification-history translation are outside this phase.

## Adversarial review record

Two internal reviewers examine the specification independently, at product and contract level.
The review excludes implementation details.

| Round | Findings and result |
| --- | --- |
| 1 | Clarify push outcomes across independent APK/server updates, unsupported language selections, and incompatible message parameters. |
| Correction | Specify localized generic rendering for valid legacy pushes, the old-APK update requirement, retained language on rejection, and descriptor fallback. |
| 2 | Both reviewers re-read the corrected document and report no blocking specification findings. |

The user explicitly approves the old-APK delivery trade-off after the review.
