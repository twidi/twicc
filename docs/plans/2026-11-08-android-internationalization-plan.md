# Android Internationalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply one persistent Android interface language to native UI, every embedded instance, and device-rendered notifications.

**Architecture:** Keep the existing JSON catalogs as the editable translation source. Compile an explicit Android subset into packaged, language-neutral token resources. Synchronize installation preference through the existing native bridge; send push presentation through the existing `key`, `params`, `count` descriptor contract.

**Tech Stack:** Existing Kotlin/JVM and Android APIs, GeckoView, Android System WebView, Vue I18n, Node, Django, and UnifiedPush. Use the installed `@intlify/message-compiler` already used by `frontend/src/i18n/index.test.js`. No new runtime library or package installation is required by this plan.

**Spec:** [2026-11-08-android-internationalization-spec.md](2026-11-08-android-internationalization-spec.md)

**Status:** Author self-review and two adversarial review rounds complete. Ready for user review; no implementation starts in this task.

## Global Constraints

- Supported locales: `en`, `de`, `es`, `fr`, and `pt-BR`.
- Initial language: `en`. No Android system-language detection or system language-picker integration.
- One language per installation; independent languages across devices; no locale field in backend requests or records.
- Keep dotted message keys and exact existing equivalent translations. Follow `docs/internationalization.md` and `docs/i18n-glossary.md`.
- Preserve named interpolation, existing French plural behavior, English fallback, profile isolation, and notification policies.
- New Android pushes contain descriptors only. Old APKs require an update after the server upgrade; the user approves this trade-off.
- New APKs render valid legacy text pushes as localized generic notifications with their safe destinations intact.
- Existing posted event notifications need not change. No notification history, replay, or extra background translation work.
- Include both engine flavors. Keep `minSdk = 29`, existing signing identity, and current dependencies.
- Do not change user/provider content, prompts, spoken content, raw external diagnostics, CLI/MCP output, or Apprise rendering.
- Do not restart servers, install packages, or apply migrations without the user's explicit request. No migration is planned.
- Preserve concurrent changes. Stage only the files changed for each task; never use tree-wide Git mutation commands.
- Every command below starts with `cd <checkout>` or its relevant subdirectory. Resolve `<checkout>` to the executing worktree.
- Each task ends with a Conventional Commit, an explanatory body, and the executing model's required co-author trailer.

## Review Focus

1. A delayed selection or resumed page must not overwrite a newer installation choice: Tasks 4–5 test stale revisions and page generations.
2. An instance can serve an older frontend or a newer language list: Tasks 4–6 test both compatibility directions before login.
3. A push can wake a fresh process with no page: Tasks 3 and 9 test persisted language and headless rendering.
4. Source message escaping and plurals differ from Android conventions: Tasks 1–2 compare generated tokens against Vue I18n oracle fixtures.
5. Push parameters can exhaust the encrypted record or mismatch an installed message: Tasks 8–9 test bounded structure and localized generic fallback.

## File and dependency map

| Files | Responsibility |
| --- | --- |
| `android/i18n/messages.json` | Explicit native key subset, presentation parameter formats, and bootstrap resource mappings; no translated prose. |
| `frontend/scripts/generate-android-i18n.mjs` | Compile selected catalog messages using the existing compiler; generate resources and oracle fixtures. |
| `android/app/src/main/assets/i18n/messages.json`, `sources.sha256` | Committed generated token catalog and source fingerprints; regenerated, never hand-edited. |
| `android/app/src/main/res/values/strings.xml` | Only Android bootstrap resources that still need XML; generated mappings preserve required resource IDs. |
| `android/policy/src/main/kotlin/com/twidi/twicc/i18n/UiMessage.kt`, `MessageRenderer.kt`, `AppLanguage.kt` | Framework-free semantic types, token rendering, and language revision decisions. |
| `android/app/src/main/java/com/twidi/twicc/i18n/NativeMessages.kt`, `AndroidLanguageStore.kt`, `UiMessageJson.kt` | Packaged asset loading, persistent preference, platform number formatting, and JSON boundary. |
| `frontend/src/i18n/androidLocale.js` | One page-lifetime language controller; bootstrap, explicit selection, resume, and stale-work handling. |
| `frontend/src/i18n/messageDescriptors.js` | Shared recursive descriptor conversion and explicitly declared numeric parameter formatting. |
| Existing activities, native platform helpers, TTS, and engine adapters | Presentation migration without browser reload or transport redesign. |
| `src/twicc/android_push/messages.py` | Event-to-descriptor builders and bounded descriptor validation; no translation or catalog loading. |
| Existing push delivery, registration, and clients | Version negotiation, structured payloads, safe legacy rendering, and compatibility status. |

Tasks 1–3 establish local rendering. Tasks 4–5 establish language synchronization. Tasks 6–7 migrate presentation.
Tasks 8–10 deliver structured push and registration compatibility. Task 11 completes checks and documentation.
These are one integration plan because APK, embedded page, and push contracts must agree.

## Fixed interfaces

### Messages and generated resources

`UiMessage(key: String, params: Map<String, Any?> = emptyMap(), count: Double? = null)` is the Kotlin semantic descriptor.
Supported parameter values are strings, finite numbers, booleans, and nested `UiMessage` values. Null is allowed only for unused optional data.
The JSON form uses the existing `key`, `params`, and optional `count` fields. A nested descriptor uses that same shape.
Do not add wire fields for translated text, formatter code, or Android resource IDs.

`MessageRenderer.render(locale: String, message: UiMessage): String?` returns text or null for an unrenderable descriptor.
Its constructor consumes a generated catalog and a number-format callback, not Android `Context`.
`NativeMessages.text(context: Context, message: UiMessage): String` reads the installation language and resolves packaged resources.
Try the selected locale, then English for missing translations. Use a packaged generic message if identity or parameters are incompatible.
Never expose a raw key. Preserve descriptors in retained state rather than persisting rendered native errors.

The manifest selects keys and declares numeric parameter formatting by key and parameter name.
Unlisted parameters stay verbatim; identifiers and HTTP status codes must not become grouped numbers.
Decimal amounts travel as numbers or canonical ASCII decimal strings, never locale-formatted backend strings.
Only manifest-declared numeric parameters are formatted. Decimal strings preserve significant digits and trailing-zero policy.
Nested descriptors let complete shared sentences form a larger complete notification body without backend rendering.
Use a catalog wrapper for placement and separators. Never construct translated English prefixes or suffixes.

Compiled catalog format version is `1`: locale → dotted key → forms → text/named-parameter tokens.
Literal interpolation becomes a text token. Keep each form's required named parameters and the declared numeric formatting metadata.
Different plural forms may use different parameter subsets; compare each language's whole-message parameter union for parity.
Use the compiler AST; never split messages on `|` or parse Vue syntax with regular expressions.
Initially accept text, named interpolation, literals, and one/two/three-form messages.
Fail generation on linked messages, list interpolation, HTML, unknown nodes, or incompatible parameter sets in the selected subset.
Keep one-form messages unchanged. Mirror the installed Vue default rule and existing custom French rule for two/three forms.
Reject non-finite counts and invalid form selection. Test integer counts and French fractional selection against the oracle.

### Language bridge

Keep bridge envelope `protocol: 1`. Add `capabilities.appLanguage: 1` and `appLanguage` to full native state.
`appLanguage` is `{ locale, revision, supportedLocales }`; `revision` is a persisted nonnegative JavaScript-safe integer.
Initial state is `{ locale: 'en', revision: 0, supportedLocales: ['en', 'de', 'es', 'fr', 'pt-BR'] }`.

Add `locale.get` and `locale.set` commands. `locale.set` takes `locale`, `expectedRevision`, and current `documentToken`.
The native owner validates the active document using existing engine document leases before accepting a mutation.
A stale revision or unsupported locale returns current language state plus a semantic error, without mutation.
A rejected bridge response retains that state on `error.state`; `error.uiMessage` carries its reactive explanation.
A successful change persists before replying, increments revision once, and broadcasts existing `stateChanged` events.
A same-language request is a successful no-op and does not increment revision.
`locale.get` is read-only and available before authentication or TTS attachment.
Old documents cannot mutate the preference; ordinary browsers never acquire a native language controller.

`createAndroidLocaleController({ client, document, applyLocale, readBrowserLocale, onCompatibility })` owns page synchronization.
Methods: `initialize(): Promise<LanguageState>`, `select(locale): Promise<void>`, `reconcile(): Promise<void>`, `dispose(): void`.
`LanguageState` contains `{ mode, locale, revision, supportedLocales }`; mode is `native`, `browser`, or `incompatible`.
The controller is page-lifetime, separate from sidebar menu mounting. Reuse `getAndroidAppClient()`.
Only `select()` represents user intent. Store restoration and native broadcasts never call it.
Keep selected preference separate from temporary English rendering fallback. Sequence asynchronous work so older results cannot apply.
Serialize mutations from one page. Skip a queued selection if a newer local selection supersedes it before dispatch.
Dispatch the latest queued selection with the revision from the preceding completed request.
If another page changes the authoritative revision, reject the stale mutation and reconcile; do not replay stale intent.

### Push contract and versions

Keep `type: 'twicc.notification'`. Add `version: 2`, `title_ui_message`, and `body_ui_message`.
Keep existing `id`, `sent_at`, `kind`, `path`, `action`, and `detail` semantics.
Do not send legacy `title`/`body` fields in version 2. `twicc.dismiss_sessions` stays unchanged.

Keep enrollment configuration `protocol: 1`; add `notification_payload_version: 2`.
Compatible enrollment and native renewal requests carry `notification_payload_version: 2`, never a language.
Persist that capability on the existing device registration, not in Django models.
Missing capability on an existing registration means legacy. New servers send version 2 only to confirmed compatible registrations.
Old subscriptions remain removable and renewable; neither operation can downgrade a confirmed payload capability.
Native state adds `payloadVersion` and compatibility status `update_app` or `update_server` where needed.
Do not conflate configuration protocol, bridge protocol, compiled catalog format, and notification payload version.

Valid unversioned legacy notifications pass all old envelope/content bounds before their application text is discarded.
Render a generic localized title/body and retain safe instance and tap context.
Reject malformed envelopes and unknown envelope versions. Unknown keys or incompatible descriptors inside valid version 2 envelopes render generically.
Both descriptor traversal and nested rendering are bounded to depth 4 and 64 descriptor nodes.
Preserve the existing 4096-byte receive limit and 3900-byte backend plaintext budget.

---

### Task 1: Establish the catalog compiler and shared rendering fixtures

**Files:** Create `android/i18n/messages.json`, `frontend/scripts/generate-android-i18n.mjs`, `frontend/scripts/generate-android-i18n.test.mjs`, `android/app/src/main/assets/i18n/messages.json`, `android/app/src/main/assets/i18n/sources.sha256`, `android/app/src/test/resources/i18n/render-cases.json`. Modify all five `frontend/src/i18n/locales/{en,de,es,fr,pt-BR}.json` catalogs, `frontend/package.json`, and `android/app/src/main/res/values/strings.xml`.

**Interfaces:** Consumes existing catalogs and `baseCompile`. Produces format-1 token assets, source fingerprints, and Vue-rendered fixture expectations.

- [ ] **Step 1: Write failing generator tests.** `compilesLiteralPipesAndNamedValues`, `keepsSharedMessagesExact`, `rejectsUnsupportedSubsetSyntax`, and `detectsMissingLocaleOrParameter` assert decoded literals, unchanged catalog source text, compilation failure, and locale/parameter parity. Include `{provider}`, quotes, apostrophes, `{'|'}`, `{'@'}`, braces, newlines, accents, and emoji.
- [ ] **Step 2: Run the tests before implementation.** Run `cd <checkout>/frontend && node --test scripts/generate-android-i18n.test.mjs`. Expect failure because the generator exports do not exist.
- [ ] **Step 3: Implement `compileAndroidCatalog(catalogs, manifest)` and `generateAndroidI18n({ check = false })`.** Inventory native strings in main, Gecko, WebView, and `:policy`. Map equivalent meanings to existing keys. Add new complete messages in all locales. Record every current native resource mapping; replace prose in XML with generated English bootstrap values where XML remains required. Keep `app_name` as an intentional product name. Read the glossary before translation.
- [ ] **Step 4: Generate oracle cases and verify output.** Add `i18n:android:generate` and `i18n:android:check` scripts. Generate render cases for every selected key and locale, using sample parameters. Include counts `0`, `1`, `2`, `21`, French fractional counts, explicit three-form fixtures, and invalid source cases. Run the generator tests, `npm run i18n:android:generate`, `npm run i18n:android:check`, and `node --test src/i18n/index.test.js`, each from `cd <checkout>/frontend &&`. Expect parity and deterministic output; a second generation changes no bytes.
- [ ] **Step 5: Commit only this task's files.** Subject: `feat(android): generate native messages from shared catalogs`. Explain the supported syntax and committed generated outputs in the body.

### Task 2: Render compiled messages with native and web semantic parity

**Files:** Create `android/policy/src/main/kotlin/com/twidi/twicc/i18n/UiMessage.kt`, `android/policy/src/main/kotlin/com/twidi/twicc/i18n/MessageRenderer.kt`, `android/policy/src/test/kotlin/com/twidi/twicc/i18n/MessageRendererTest.kt`, `android/app/src/main/java/com/twidi/twicc/i18n/UiMessageJson.kt`, `android/app/src/main/java/com/twidi/twicc/i18n/NativeMessages.kt`, `android/app/src/test/java/com/twidi/twicc/i18n/NativeMessagesTest.kt`, and `frontend/src/i18n/messageDescriptors.js`. Modify `frontend/src/i18n/remoteMessages.js`, `frontend/src/i18n/uiMessages.js`, their tests, and `android/app/build.gradle.kts`.

**Interfaces:** Produces `UiMessage`, `MessageRenderer.render`, `NativeMessages.text`, `UiMessageJson.decode(value: JSONObject): UiMessage?`, `UiMessageJson.encode(message: UiMessage): JSONObject`, and `formatMessageParams(key, params, locale)` for manifest-declared numeric fields. Existing `remoteUiMessage` continues to accept existing payloads and fallback text.

- [ ] **Step 1: Write failing tests.** `matchesVueOracleFixtures` checks every generated locale/key/form result. `preservesDescriptorAcrossLocaleChange` checks recursive remote-to-local conversion. `rejectsBrokenDescriptors` checks missing required params, unknown key, infinite count, depth 5, and node 65. `formatsOnlyDeclaredNumbers` asserts a French amount uses its decimal separator while an HTTP status and user string stay unchanged. Test decimal-string precision without float coercion and raw external diagnostic fallback.
- [ ] **Step 2: Run the failing tests.** Run `cd <checkout>/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest` and `cd <checkout>/frontend && node --test src/i18n/remoteMessages.test.js src/i18n/uiMessages.test.js`. Expect missing semantic renderer/converter behavior.
- [ ] **Step 3: Implement the fixed message interfaces.** Compile forms in Node; Kotlin only selects forms and interpolates tokens. Load assets through the app adapter; keep the policy renderer framework-free. Use selected-locale number formatting only for declared numeric parameters. Preserve the recursive semantic descriptor in web errors and retained messages. Retain current fallback behavior for unrelated existing APIs.
- [ ] **Step 4: Pass parity and boundary checks.** Add generated assets/test fixtures as JVM test inputs in Gradle. Run both commands from Step 2, then `cd <checkout>/frontend && npm run i18n:android:check`. Expect all fixture text to match; platform number-format tests separately validate locale-aware separators and decimal precision.
- [ ] **Step 5: Commit.** Subject: `feat(android): render shared semantic messages on device`.

### Task 3: Persist an installation language without system detection

**Files:** Create `android/policy/src/main/kotlin/com/twidi/twicc/i18n/AppLanguage.kt`, `android/policy/src/test/kotlin/com/twidi/twicc/i18n/AppLanguageTest.kt`, `android/app/src/main/java/com/twidi/twicc/i18n/AndroidLanguageStore.kt`, and `android/app/src/androidTest/java/com/twidi/twicc/AppLanguagePersistenceTest.kt`. Modify `NativeMessages.kt` from Task 2.

**Interfaces:** `AppLanguage(locale: String, revision: Long)` and `LanguageSelection(state: AppLanguage, accepted: Boolean, error: UiMessage? = null)`. `AndroidLanguageStore.read(): AppLanguage`; `select(locale: String, expectedRevision: Long): LanguageSelection`; `observe(listener: (AppLanguage) -> Unit): AutoCloseable`. Storage uses a new shared-preferences file `app-language`, independent from instance and engine profile files.

- [ ] **Step 1: Write failing tests.** `defaultsToEnglishOnFrenchSystem`, `rejectsStaleRevisionAndUnknownLocale`, `sameLocaleIsNoOp`, and `survivesInstanceRemovalAndProcessRecreation` pin these assertions:

```kotlin
assertEquals(AppLanguage("en", 0), freshStore.read())
assertEquals(AppLanguage("fr", 1), freshStore.select("fr", 0).state)
assertFalse(freshStore.select("de", 0).accepted)
assertEquals(AppLanguage("fr", 1), reopenedStore.read())
```

- [ ] **Step 2: Run the policy test.** Run `cd <checkout>/android && ./gradlew :policy:test --tests '*AppLanguageTest'`. Expect missing state/selection types.
- [ ] **Step 3: Implement preference ownership.** Persist synchronously before notifying observers. Serialize mutations; reject unsupported locales and revisions outside the JavaScript-safe range. Invalid stored state resets to English revision zero. Do not read browser storage or `Locale.getDefault()` to select a language. Do not mutate global system locale or register an Android language picker.
- [ ] **Step 4: Verify persistence.** Re-run the policy command. Run `cd <checkout>/android && ./gradlew -PtargetProfile=emulator :app:connectedWebviewDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.twidi.twicc.AppLanguagePersistenceTest` on the x86_64 emulator. Expect preference persistence and English first launch regardless of the test device language. Repeat the class on Gecko during Task 11.
- [ ] **Step 5: Commit.** Subject: `feat(android): persist one installation language`.

### Task 4: Add the revisioned language bridge to both engines

**Files:** Modify `android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt`, `BrowserActivity.kt`, `android/app/src/webview/java/com/twidi/twicc/engines/webview/WebViewBrowserSession.kt`, `android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoBrowserSession.kt`, `android/app/src/gecko/assets/android-app-bridge/content.js`, and `frontend/src/utils/androidAppClient.js`. Create `android/app/src/test/java/com/twidi/twicc/AppLanguageBridgeTest.kt`; modify `frontend/src/utils/androidAppClient.test.js` and `android/policy/src/test/kotlin/com/twidi/twicc/AndroidAppPolicyTest.kt`.

**Interfaces:** Produces `capabilities.appLanguage`, `appLanguage`, `locale.get`, and `locale.set` exactly as defined above. Existing transport identity/origin checks and state-change events stay authoritative.

- [ ] **Step 1: Write failing bridge tests.** `readsLanguageWithoutLoginOrTts`, `acceptsCurrentDocumentSelection`, `rejectsRetiredDocument`, `rejectsStaleRevision`, and `rejectsSubframeAndForeignOrigin` assert the fixed state fields and no mutation on rejection. Assert persisted preference precedes successful reply and only one revision increment occurs.
- [ ] **Step 2: Run failing tests.** Run `cd <checkout>/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest --tests '*AppLanguageBridgeTest'` and `cd <checkout>/frontend && node --test src/utils/androidAppClient.test.js`. Expect unsupported locale commands/capability.
- [ ] **Step 3: Extend bridge dispatch and state.** Add a dedicated language callback to `AndroidAppBridge`; keep language handling separate from `AppCommand` navigation actions. Validate mutations against each engine's current document lease. Extend the existing document-token readiness path to `locale.set`, without requiring speech attachment or permission. Read-only commands must not wait for a TTS session. Marshal mutations and native observers on the activity owner thread; remove observers on destruction.
- [ ] **Step 4: Verify both adapters.** Run targeted app unit tests for WebView and Gecko plus `androidAppClient.test.js`, `androidGeckoTransport.test.js`, and policy frame tests. Verify old TTS, push, menu, and oversized-message replies still work. Full native state events must let the language controller re-read state independently of sidebar heartbeat.
- [ ] **Step 5: Commit.** Subject: `feat(android): synchronize language through the native bridge`.

### Task 5: Apply Android preference before login and route explicit user intent

**Files:** Create `frontend/src/i18n/androidLocale.js` and `frontend/src/i18n/androidLocale.test.js`. Modify `frontend/src/main.js`, `frontend/src/stores/settings.js`, `frontend/src/components/app/SettingsPopover.vue`, `frontend/src/utils/androidEnvironment.js`, `frontend/src/i18n/startup.test.js`, `frontend/src/i18n/login.test.js`, and `frontend/src/i18n/index.test.js`.

**Interfaces:** Implements `createAndroidLocaleController` and its four methods. Export `initializeInterfaceLocale(): Promise<LanguageState>` and `selectInterfaceLocale(locale): Promise<void>` from `androidLocale.js` for the singleton page controller. The first initializes it in `main.js`; the second is the sole selector action. Normal store synchronization calls `applyLocale`, never the native mutation action.

- [ ] **Step 1: Write failing controller tests.** `nativeEnglishOverridesStaleProfileFrenchBeforeLogin`, `browserKeepsOwnPreference`, `selectionPropagatesOnResume`, `olderSelectionCannotWin`, `latestQueuedSelectionUsesNewRevision`, `restorationDoesNotSendLocaleSet`, `failedChunkDoesNotOverwriteNativeChoice`, and `unsupportedApkLanguageRestoresPage` assert the spec outcomes. Use deferred promises to complete older requests last. Include store initialization after native bootstrap and preserved `error.state` after rejected mutation.
- [ ] **Step 2: Run failing tests.** Run `cd <checkout>/frontend && node --test src/i18n/androidLocale.test.js src/i18n/startup.test.js src/i18n/login.test.js`. Expect missing controller or incorrect bootstrap precedence.
- [ ] **Step 3: Implement page-lifetime integration.** Before auth checks, initialize the controller and await native-selected catalog loading. Use a bounded ordinary-browser probe through the existing client's timeout. Set the store's initial locale from controller state before its immediate watcher runs. Route the selector's `change` through `selectInterfaceLocale`; preserve all other setting behavior. Reconcile on native `stateChanged` and visible-page resume before removing an interaction guard. Never auto-retry a stale mutation using a newer revision; reconcile and explain rejection instead. Never write language to backend synced settings.
- [ ] **Step 4: Pass startup and concurrency checks.** Re-run Step 2 and `cd <checkout>/frontend && node --test src/utils/androidAppClient.test.js src/stores/settingsAgentShares.test.js src/i18n/index.test.js`. Replace the existing source assertion for direct `store.locale` assignment with the explicit selection action. Assert no auth/protected API request is needed for language reads. A failed optional chunk keeps native preference, applies the existing English fallback, and retries on the next normal page load. Dispose page listeners without letting sidebar remounts dispose the controller.
- [ ] **Step 5: Commit.** Subject: `feat(i18n): apply the android language across embedded instances`.

### Task 6: Translate native screens, validation, and engine presentation

**Files:** Modify `android/app/src/main/java/com/twidi/twicc/{MainActivity,InstanceManagerUi,InstanceStore,BrowserActivity,NativeUi}.kt`, `android/app/src/main/java/com/twidi/twicc/platform/{NativeRequests,BrowserDownloads,ExternalLinks}.kt`, `android/app/src/main/java/com/twidi/twicc/browser/BrowserEvents.kt`, `android/policy/src/main/kotlin/com/twidi/twicc/InstanceUrl.kt`, both engine `*BrowserSession.kt` files, and `android/app/src/gecko/java/com/twidi/twicc/engines/gecko/GeckoPrompts.kt`. Modify `android/policy/src/test/kotlin/com/twidi/twicc/InstanceUrlTest.kt`, `android/app/src/androidTest/java/com/twidi/twicc/{InstanceManagerUiTest,BrowserRecoveryTest}.kt`, and `android/app/src/main/res/values/ids.xml`. Regenerate Task 1 outputs when keys change.

**Interfaces:** Add `InstanceValidationCode` and `InstanceValidationException(code)` in `:policy`. Add `instanceValidationMessage(code): UiMessage` in app presentation. Change `BrowserEvents.onFailure(message: UiMessage)` for application-owned engine errors. Keep externally supplied prompt titles/messages and picker choices verbatim.

- [ ] **Step 1: Write failing presentation tests.** `instanceValidationIsTyped`, `refreshKeepsFormDraftAndSemanticError`, `nativeMenuUsesSharedWebKeys`, and `engineFailuresUseDescriptors` assert typed URL failure codes, unchanged drafts, shared `navigation.android.*` keys, and current-language failures in both engines. Use IDs for instance buttons, form fields, and dialog actions instead of English text.
- [ ] **Step 2: Run the failing tests.** Run `cd <checkout>/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest`. Run focused `InstanceManagerUiTest` and `BrowserRecoveryTest` instrumentation classes to expose untranslated UI.
- [ ] **Step 3: Migrate presentation calls.** Replace application-owned `getString` and literals with `NativeMessages.text`. Map duplicate-address validation to a typed code too. Store semantic error identity in form drafts and saved state. Refresh native labels/dialog text on preference change and resume, preserving input, focus, pending confirmation, theme handling, and browser node/session identity. Include fixed Add-button labels outside the instance list. Do not recreate the browser, reload its page, or interrupt downloads to change labels. Replace application-owned `android.R.string` button text with shared keys. Use selected-language casing where the UI still uppercases a label.
- [ ] **Step 4: Verify native and mixed-version experience.** Run focused instrumentation in all five locales. Test native English on French Android, German long labels, and Portuguese diacritics. Treat a valid `locale.get` as the current document's language handshake, resetting it on navigation. If a loaded old page never completes that handshake, keep the native fallback menu accessible despite its web-menu lease and show a localized update limitation there. The old page remains independently functional; no old-page code can be assumed. Verify both old and new menu ownership behavior, then regenerate/check the selected catalogs.
- [ ] **Step 5: Commit.** Subject: `feat(android): translate native screens and typed validation`.

### Task 7: Preserve semantic errors through TTS and bridge consumers

**Files:** Modify `android/app/src/main/java/com/twidi/twicc/AndroidAppBridge.kt`, `android/app/src/main/java/com/twidi/twicc/tts/{NativeTtsBridge,TtsServiceOwner,TtsNotification,TtsService,TtsJson,TtsConnection,TtsPlayer}.kt`, `android/policy/src/main/kotlin/com/twidi/twicc/tts/TtsState.kt`, `android/app/src/test/java/com/twidi/twicc/tts/TtsPlaybackOutcomeTest.kt`, other existing TTS unit tests, `frontend/src/utils/androidAppClient.js`, `frontend/src/utils/nativeTtsClient.js`, and `frontend/src/utils/{androidAppClient,nativeTtsClient}.test.js`. Regenerate catalog outputs for new messages.

**Interfaces:** Keep `TtsActionError` machine codes/actions and its existing `uiMessage` field. Every native application-owned rejection fills that field. Top-level app bridge failures include `ui_message`. Existing raw external diagnostics remain fallback data.

- [ ] **Step 1: Write failing tests.** `allOwnedTtsRejectionsCarryDescriptors`, `bridgeErrorsRemainReactive`, `cachedSpeechFailureChangesLanguage`, and `serviceNotificationUsesLatestLanguageOnNormalRefresh` assert semantic metadata through acceptance replies, snapshots, results, catch boundaries, and service notification actions. In `TtsPlaybackOutcomeTest`, test every application-owned `speechEndResult()` failure: premature end, refusal, missing response, unavailable audio, and native fallback. Preserve existing descriptor-bearing backend failures rather than replacing them with generic native errors.
- [ ] **Step 2: Run failing tests.** Run `cd <checkout>/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest` and `cd <checkout>/frontend && node --test src/utils/androidAppClient.test.js src/utils/nativeTtsClient.test.js src/stores/nativeTts.integration.test.js`. Expect missing descriptors in native-owned failures.
- [ ] **Step 3: Implement descriptor preservation and native rendering.** Map each application rejection at its producer. In `TtsPlayer.kt`, attach `uiMessage` to application-owned `PlayerSignal.Ended` failures before `TtsState.finish()` retains them. Preserve backend descriptors and raw external diagnostics. Use `remoteUiError` for top-level app bridge errors and retain `error.uiMessage` in native TTS catch paths. Render native TTS actions and application-owned notification text through `NativeMessages`. Preserve active audio, reading IDs, operation results, and speech language. Update service text only on its normal next refresh; no new refresh timer.
- [ ] **Step 4: Pass boundary and playback regressions.** Repeat Step 2 and existing TTS platform instrumentation. Change interface language during active audio and while an error is visible. Expect translated interface errors and unchanged playback ownership/content. Verify logs and raw upstream errors remain verbatim.
- [ ] **Step 5: Commit.** Subject: `feat(android): localize speech and preserve bridge message identity`.

### Task 8: Produce bounded descriptor-only push messages on the backend

**Files:** Create `src/twicc/android_push/messages.py` and `tests/test_android_push_messages.py`. Modify `src/twicc/android_push/delivery.py`, `src/twicc/external_notifications.py`, `tests/test_android_push.py`, and `tests/test_peer_external_notifications.py`. Update catalog manifest/messages and generated outputs as required.

**Interfaces:** `agent_messages(event: str, info, session_title, project_name, project_parent_name)`, `extra_usage_messages(provider, snapshot)`, `peer_message_messages(message, routing)`, and `peer_request_messages(peer)` return `(title_descriptor, body_descriptor)`. Agent event values are `user_turn`, `question`, and `approval`, chosen from the detector's machine state/request data. `dispatch(kind, title_ui_message, body_ui_message, *, path=None, action=None, detail=None)` and `send` replace string arguments. `bounded_payload(payload: dict) -> dict | None` enforces 3900 serialized UTF-8 bytes.

- [ ] **Step 1: Write failing backend tests.** `test_every_android_event_uses_existing_descriptors`, `test_push_has_no_rendered_prose_or_locale`, `test_multibyte_payload_stays_valid_and_bounded`, and `test_apprise_messages_stay_unchanged` cover all four kinds. Include shell plurals, question versus approval, currency/credits/remaining balance, peer reply/request, absent names, and partial project/session routing. Add `test_completion_and_pending_growth_emit_distinct_descriptors` for one broadcast that triggers both events. Assert serialized payload contains `version == 2`, descriptor fields, and no `title`, `body`, or language field.
- [ ] **Step 2: Run failing tests.** Run `cd <checkout> && uv run pytest tests/test_android_push_messages.py tests/test_android_push.py tests/test_peer_external_notifications.py`. Expect the current rendered-English dispatch contract to fail.
- [ ] **Step 3: Implement semantic producers.** Reuse `ui_message()` from `core/ui_messages.py`. Reuse existing `session.notifications.finished`, `body`, `question`, `approval`, `shells`, `turnFinished`, extra-usage, and peer keys when their meanings match. Move punctuation/placement into complete catalog wrappers where several complete messages compose a body. Represent missing application labels with descriptors, not English `Unknown` or `An instance`. Scale money as arithmetic/canonical decimal data without locale formatting. Preserve Apprise strings and its presence/deferral logic; separate Android descriptor construction from that existing output.
- [ ] **Step 4: Bound and verify every producer.** Bound nested descriptors before sending; shrink only designated user-content parameters on Unicode code-point boundaries. If a valid event still cannot fit, replace presentation with small known generic descriptors while preserving kind and safe destination. Never truncate serialized JSON or key names. Preserve delivery task limits, session checks, and dismissals. Re-run Step 2 and catalog parity/check commands. Assert every emitted descriptor key/required parameter exists in the manifest and catalogs, including nested leaves.
- [ ] **Step 5: Commit.** Subject: `feat(android): send semantic push presentation from the backend`.

### Task 9: Render structured and legacy pushes in the installation language

**Files:** Modify `android/app/src/main/java/com/twidi/twicc/push/{PushPayload,AndroidPushService,AndroidPush}.kt`, `android/app/src/test/java/com/twidi/twicc/{AndroidPushPayloadTest,PushSessionDismissalTest}.kt`, and `android/app/src/androidTest/java/com/twidi/twicc/{PushNotificationNavigationTest,PushSessionDismissalTest}.kt`. Create `android/app/src/androidTest/java/com/twidi/twicc/PushLocalizationTest.kt`.

**Interfaces:** `PushPayload` exposes semantic title/body and safe navigation. Legacy content becomes generic descriptors only after its legacy envelope validates. The service calls `NativeMessages.text` and wraps instance display context through a catalog message, not hardcoded text joining.

- [ ] **Step 1: Write failing payload/rendering tests.** `rendersVersion2AfterProcessRecreation`, `legacyPayloadUsesLocalizedGenericText`, `unknownKeyOrParamsUsesGenericWithSameTap`, `rejectsInvalidEnvelopeVersionsAndDepth`, and `languageChangeKeepsSubscriptionAndChannelSettings` cover both formats. Assert invalid IDs, expiry, malicious paths, unknown actions, and oversize legacy text still reject before fallback. Distinguish an unrenderable descriptor from an invalid envelope.
- [ ] **Step 2: Run failing app tests.** Run `cd <checkout>/android && ./gradlew :app:testWebviewDebugUnitTest --tests '*AndroidPushPayloadTest' --tests '*PushSessionDismissalTest'`. Expect rejection of version-2 fixtures or legacy English rendering.
- [ ] **Step 3: Implement both receive paths.** Decode the bounded semantic shape with `UiMessageJson`; retain the old envelope checks. Render selected locale at notification construction, independent of a page. If either title/body descriptor is unrenderable, render packaged generic title/body. Preserve notification tags, instance context, content intents, permission checks, visibility suppression, and dismissal cutoffs. Translate channel labels without replacing channel IDs or resetting importance. Do not re-post existing event notifications on language change.
- [ ] **Step 4: Verify headless rendering and navigation.** Re-run Step 2. Run the three focused instrumentation classes, including delivery after process recreation and a two-device language simulation using isolated installation preference stores. Expect French and German from the same event data. Test posted notifications stay unchanged while new notifications use the new language.
- [ ] **Step 5: Commit.** Subject: `feat(android): render localized push notifications on device`.

### Task 10: Negotiate push compatibility and upgrade existing registrations

**Files:** Modify `src/twicc/android_push/{storage,views,delivery}.py`, `tests/test_android_push.py`, `frontend/src/utils/androidPush.js`, `frontend/src/utils/androidPush.test.js`, `frontend/src/components/app/NotificationSettings.vue`, `frontend/src/components/app/NotificationSettings.android.test.js`, `android/app/src/main/java/com/twidi/twicc/push/{AndroidPush,PushRegistrationJob}.kt`, `android/app/src/main/java/com/twidi/twicc/{MainActivity,BrowserActivity}.kt`, `android/app/src/test/java/com/twidi/twicc/{AndroidPushBridgeTest,PushRegistrationRequestTest}.kt`, and `android/app/src/androidTest/java/com/twidi/twicc/AndroidPushRegistrationTest.kt`. Regenerate compatibility-message catalogs.

**Interfaces:** Implements `notification_payload_version: 2`, native `payloadVersion`, and `update_app`/`update_server` states defined above. Add `AndroidPush.reconcilePayloadSupport(context: Context)` for native startup/foreground reconciliation across all saved registrations. Existing authenticated enrollment and bearer renewal/removal boundaries stay unchanged.

- [ ] **Step 1: Write failing version-matrix tests.** `test_old_registration_does_not_receive_structured_push`, `test_confirmed_version_survives_old_renewal`, `test_upgrade_promotes_registration_without_page`, `newApkOldServerShowsGenericCompatibility`, and `oldApkNewServerShowsUpdateRequired` pin both update directions. Add `apkFirstServerLaterReconfirmsOnNativeResume`: upgrade APK, succeed against old server without version confirmation, upgrade server, resume native app, then confirm version 2 without opening that instance page. Inspect requests and stored JSON to assert no language preference is present.
- [ ] **Step 2: Run failing tests.** Run `cd <checkout> && uv run pytest tests/test_android_push.py`; run `cd <checkout>/frontend && node --test src/utils/androidPush.test.js src/components/app/NotificationSettings.android.test.js`; run the two named Android bridge/request unit tests. Expect missing capability negotiation or unchanged filtering.
- [ ] **Step 3: Implement capability storage and filtering.** Configuration advertises version 2 while protocol remains 1. Enrollment accepts absent legacy capability but marks it incompatible for new delivery. Renewal with capability promotes existing JSON records; absent capability never downgrades confirmed support. `storage.devices()`/delivery select only compatible registrations for version-2 notifications, without deleting old registrations. Preserve silent session dismissals for existing enrolled devices. Validate unsupported capability values without changing auth protections.
- [ ] **Step 4: Implement APK upgrade renewal and UI status.** Call `reconcilePayloadSupport` on native startup and foreground resume in both activities. Inspect all saved push records, not only the active instance. Mark unconfirmed records dirty once per foreground session; use the existing finite job and grants to renew without opening their pages. A successful old-server response clears transport retry state but leaves compatibility unconfirmed for the next native reconciliation. Do not continuously poll unsupported servers. Only a version-2 configuration/renewal response confirms support; persist it and expose `update_server` until then. A future server upgrade becomes confirmed on the next successful native reconciliation. Keep existing retry, permission, deletion, and 401 behavior. Run the full Step 2 matrix plus registration instrumentation; test multiple saved instances and endpoint rotation.
- [ ] **Step 5: Commit.** Subject: `feat(android): negotiate semantic push compatibility`.

### Task 11: Enforce coverage, validate both engines, and document rollout

**Files:** Modify `android/app/build.gradle.kts`, `android/build.sh`, `android/tests/test_build.py`, `android/ci-build.yml`, `android/README.md`, `docs/internationalization.md`, and `docs/i18n-state.md`. Create `frontend/scripts/check-android-i18n-source.mjs` and `frontend/scripts/check-android-i18n-source.test.mjs`. Modify `frontend/package.json` and native instrumentation tests that still select owned controls by English text.

**Interfaces:** `npm run i18n:android:check` validates full deterministic output. `npm run lint:i18n:android` checks direct Kotlin presentation sinks in main, Gecko, WebView, and policy source. Gradle `verifyAndroidI18n` checks source/output fingerprints with Java SHA-256 before `preBuild`; standalone Android builds need no Node/npm installation.

- [ ] **Step 1: Write failing enforcement tests.** `changedCatalogFailsFingerprintCheck`, `rawKotlinControlTextFails`, `diagnosticAndUserContentDoNotFail`, and `buildEntryRunsI18nVerification` assert stale generated resources cannot ship and obvious direct UI literals fail. Cover `setText`, `.text =`, `contentDescription`, dialog/menu/action/channel text, `Toast`, and owned error producers. Allow only narrowly documented product names or external-data paths; no baseline of untranslated messages.
- [ ] **Step 2: Run failing checks.** Run `cd <checkout>/frontend && node --test scripts/check-android-i18n-source.test.mjs`; run `cd <checkout> && python3 -m unittest discover -s android/tests -v`. Expect missing checker/build prerequisite behavior.
- [ ] **Step 3: Integrate verification and document limits.** Fingerprint all catalogs, manifest, generator source, and generated outputs, excluding the fingerprint file itself. Source or output edits invalidate standalone builds. Add Node generation/parity/source checks to the opt-in Android CI template with explicit frontend dependency setup there, not hidden npm installs in `build.sh`. State that direct-literal scanning is incomplete; manually review interpolated variables and dynamic error flows. Document catalog authoring, generation, independent-device behavior, native bridge requirements, legacy generic messages, and old-APK update requirement. Update Android i18n state; do not edit CHANGELOG.
- [ ] **Step 4: Run coordinated acceptance.** Run the command list below. Verify native screenshots/interaction in all five languages, login in an instance with stale storage, A→French/B→German/A, offline native screens, theme/draft preservation, engine replacement, long labels, TTS playback, and both push upgrade directions. Record exact results and unrun device checks in the execution handoff. Missing emulator/SDK access is a reported validation limit, not a passing result.
- [ ] **Step 5: Commit.** Subject: `test(android): enforce localization coverage and document upgrades`.

## Final verification commands

Run commands once after the relevant tasks pass. Repeat only after changes or unresolved failures.

```bash
cd <checkout>/frontend && npm run i18n:android:check
cd <checkout>/frontend && npm run lint:i18n:android
cd <checkout>/frontend && npm run lint:i18n
cd <checkout>/frontend && npm test
cd <checkout>/frontend && npm run build
cd <checkout> && uv run pytest tests/test_android_push_messages.py tests/test_android_push.py tests/test_peer_external_notifications.py tests/test_ui_messages.py
cd <checkout> && python3 -m unittest discover -s android/tests -v
cd <checkout>/android && ./gradlew :policy:test :app:testWebviewDebugUnitTest :app:testGeckoDebugUnitTest
cd <checkout> && ./android/build.sh webview emulator
cd <checkout> && ./android/build.sh gecko emulator
cd <checkout>/android && ./gradlew -PtargetProfile=emulator :app:connectedWebviewDebugAndroidTest
cd <checkout>/android && ./gradlew -PtargetProfile=emulator :app:connectedGeckoDebugAndroidTest
```

The build scripts run flavor release lint and assembly. These connected commands target an x86_64 emulator with installed engine prerequisites.
For ARM64 emulators or physical devices, explicitly select their matching `targetProfile` instead of `emulator`.
Do not run `npm ci` beside devctl's setup. If dependencies or SDK packages are absent, request the reserved installation separately.
Backend changes require a user-run dev server restart before live acceptance. Do not restart on initiative.
No SDK dependency changes or LLM model changes occur, so hermetic live LLM diagnostics are outside this plan.

## Author self-review

These checks assess the plan, not implemented behavior.

- [x] Every spec acceptance row has an owning task and verification step.
- [x] The plan requires all five catalogs and the native subset to cover each consumed key.
- [x] Planned native/backend producers and consumers use consistent descriptor field names.
- [x] Initialization and native observers stay separate from explicit selection.
- [x] The planned APK/server matrix preserves the approved delivery trade-off.
- [x] No task introduces backend locale storage or changes Apprise output.
- [x] Remaining implementer choices do not change specified user-visible outcomes.

## Adversarial review record

Two internal reviewers independently review the complete plan against the approved specification and current source.
Unlike specification review, this review includes interfaces, task dependencies, concrete producers, and validation commands.

| Round | Findings and result |
| --- | --- |
| 1 | Missing TTS playback-error producer; APK-first/server-later renewal gap; absent agent-event discriminator; emulator ABI and selector-test corrections. |
| Correction | Extend Task 7, reconcile native registrations on foreground sessions, identify machine events, select the test ABI, and update the selector assertion. |
| 2 | Both reviewers re-read the corrected plan and report no remaining material plan blockers. |

No application tests or builds run during plan review. Their commands describe future implementation validation.

Implementation is not authorized by this document alone. Ask the user to review the plan and select execution before coding.
