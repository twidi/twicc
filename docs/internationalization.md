# Internationalization

## Interface languages

Choose **Settings → General → Language** to switch between **English**,
**Deutsch**, **Español**, **Français** and **Português (Brasil)**. The change is
immediate.

- The preference is saved in the browser's existing `twicc-settings` localStorage entry.
- It is restored before the login screen is mounted.
- It is local to the browser, like the color scheme. It does not change the language on other devices.
- English is the default, also when stored settings are invalid or unavailable.

These persistence rules describe ordinary browsers. Android uses the
[installation language](#android-installation-language) instead of browser
storage as its authority.

The language list here and in the table below must match `SUPPORTED_LOCALES`.
The language affects the interface only. It does not affect agent prompts,
responses, project names, saved snippets, terminal output or file contents.

## Supported languages

| Language | Code | Register | Plural rule | Web Awesome file | Day.js locale |
| --- | --- | --- | --- | --- | --- |
| English | `en` | n/a | default | built in | built in |
| Deutsch | `de` | informal `du`, standard modern German | default (zero is plural) | `de.js` | `de` |
| Español | `es` | informal `tú`, neutral international Spanish | default (zero is plural) | `es.js` | `es` |
| Français | `fr` | `vous` | custom: zero and one are singular | `fr.js` | `fr` |
| Português (Brasil) | `pt-BR` | `você` | default | `pt.js` (not `pt-BR.js`) | `pt-br` |

The French rule is `pluralRules` in `frontend/src/i18n/index.js`. For
three-form messages it keeps the default zero/one/other selection.

## What is localized and what stays English

Localized:

- Templates, scripts, shared metadata and provider presentation helpers.
- Generated DOM and HTML, CSS loading labels, editor controls and standalone entry points.
- Dates, numbers, lists and plurals, in the selected language.
- The HTML `lang` attribute, including Web Awesome controls that use the document language.
- Backend-owned interface messages, through [semantic metadata](#backend-interface-messages).
- Help and tip bodies, as [translated Markdown files](#help-and-tip-content). Titles and controls use the catalogs. Images are shared assets.

Not localized:

- Agent prompts and responses, provider and user content, files and terminal output.
- Console logs, CLI and MCP responses.
- The pages below.

### Pages that deliberately stay English

These pages need no translation. They are not deferred work. Do not report
them as missing translations.

| Page | Source |
| --- | --- |
| Public sharing, including shared session/document/artifact viewers and the recent-share list | `src/twicc/share/` and the public viewers under `frontend/src/share-session/` |
| Share password form | `src/twicc/share/templates/share_auth.html` |
| Unavailable share | `src/twicc/share/html.py`, `_UNAVAILABLE_PAGE` |
| Standalone artifact password form | `src/twicc/artifacts/templates/artifact_auth.html` |
| External MCP authorization waiting page | `src/twicc/mcp/oauth/routes.py`, `WAIT_PAGE`, served at `/mcp/oauth/wait` |
| Telemetry collector information page, outside the application | `telemetry-collector/public/index.html` |
| Standard Django error pages | Framework-generated responses, including 404 and 500 |

Apply these exclusions by role, not by the presence of JavaScript. The
classification rules, and the parts that stay in scope (MCP approval dialog,
telemetry dialogs and settings, sharing controls, verbatim command labels), are
in [docs/i18n-state.md](i18n-state.md#deliberate-exclusions). Public viewers
and the artifact shell: see [Public viewers](#public-viewers).

## Architecture

`frontend/src/i18n/index.js` configures Vue I18n in Composition API mode with
English fallback. `locales/en.json` is the source catalog. Each other language
has a `<locale>.json` catalog and a small `<locale>.js` adapter that supports
JSON imports in both Node and Vite. Messages render as text or Vue component
slots, never as HTML strings.

### Loading

English is the only catalog imported synchronously. Other catalogs load with
dynamic imports, following the
[Vue I18n lazy-loading pattern](https://vue-i18n.intlify.dev/guide/advanced/lazy).
Each loader in `localeLoaders` loads three things together: the catalog, the
Day.js locale and the Web Awesome translation.

`setLocale()` waits for the requested catalog. Then it updates the composer,
the document language and the CSS loading label.

- Await `setLocale()` for any locale other than English, before login or other interface messages render.
- English is already loaded. Public viewers call `setLocale(DEFAULT_LOCALE)` without waiting.
- Private artifact pages await their saved locale.

Android embedded pages use the installation language before their first mount.
Later native state changes and visible-page resume reconcile in the background.
Language synchronization must not intercept clicks, pointer events, typing,
changes, form submission or file-picker completion, including during downloads.
Revision validation and document/generation checks protect language state without
blocking unrelated actions. An unchanged language reuses the existing rendering.
Ordinary browsers store language in `twicc-settings` in localStorage per origin;
language is excluded from backend-synchronized settings.

### Caching and failures

- A successful import stays cached for the page lifetime.
- Concurrent requests share one download.
- Only the latest selection can change the displayed language.
- A failed import leaves English available and keeps the saved preference.
- The loader clears failed requests, but browsers can keep a failed module until reload. Reload the page to retry the saved language.
- The settings store owns persistence.

### Recovery guard

The inline asset-recovery guard in `frontend/index.html` works when application
modules cannot load. It embeds two messages per language:
`startup.assetsUnavailable.description` and `common.actions.retry`.
`frontend/vite.config.js` injects them from the catalogs.

The guard leaves optional locale-chunk failures to the locale loader. It does
not reload the whole application for them. A stale page with an old
locale-chunk hash falls back to English until the user reloads it.

### Code splitting

Keep code splitting enabled in the SPA, artifact-shell and share-viewer builds.
Otherwise the bundler inlines optional catalogs into their entry bundles.
Standalone viewer chunks stay at the bundle root because backend routes accept
one asset path segment.

Production never preloads every supported locale. Tests that render a
translation call `await loadLocaleMessages('<locale>')` first, for any
non-English locale.

### Public viewers

The share viewer and the artifact shell install the same translation plugin
for reused components.

- Public viewers force English before they render titles or mount components, whatever the saved locale.
- They have no language selector.
- They do not change the saved preference.
- Private artifact pages keep the application locale.

## Adding or changing a message

1. Add the key and the English text to `frontend/src/i18n/locales/en.json`.
2. Add the key to every other catalog, in the same position. Follow the [glossary](i18n-glossary.md).
3. Use the key: `$t('key')` in templates or `useI18n().t` in setup code.
4. Run `npm run lint:i18n` and `node --test src/i18n/index.test.js` from `frontend/`.

### Using messages

Build translated option arrays and labels in `computed()` so that switching
language updates mounted controls. Leave wire values, route names and API
identifiers unchanged.

Use named interpolation and whole-message plural forms. Do not append English
suffixes:

```vue
{{ $t('workspace.projectCount', { count }, count) }}
```

```json
{
    "workspace.projectCount": "{count} project | {count} projects"
}
```

Vue I18n treats `@`, `{`, `}` and `|` as message syntax. Use literal
interpolation when these characters are text, for example `{'@'}`. Keep
identifiers such as `tmux`, Git commands and keyboard chords intact.

### Message key names

Catalogs store flat, dotted keys. A key names the purpose and context of the
message. It does not copy the English text. Examples:
`settings.general.language.label`, `project.edit.icon.errors.tooLarge`,
`session.sharing.unavailableHistory`.

- The first segment names the feature area, for example `session`, `settings`, `project`, `workspace`, `composer`, `backend`. Reuse an existing first segment before you create one. List them with `grep -o '^    "[^.]*' frontend/src/i18n/locales/en.json | sort -u`.
- Use lower camel case in the other segments.
- Segments that come from a machine code keep the spelling of the code, for example `backend.claude.waiting_workflows` and `common.providerStatus.outage.degraded_performance`.
- Use `common` for messages with the same meaning across areas, such as `common.actions.cancel`. Do not share a key only because the English text is identical today.
- Use roles such as `label`, `hint`, `tooltip`, `placeholder` or `errors` when they distinguish messages within a feature.
- Keep related keys together, and in the same order in every catalog. The catalogs are grouped by feature, not sorted alphabetically. No tool enforces the order.

Each message holds a complete sentence, label or instruction.

- Do not concatenate translated prefixes, values and suffixes.
- Do not add English plural endings.
- Put punctuation and named value parameters inside the message.
- Use a complete message for each conditional variant, such as inherited trust with or without a source project.

### Rich text

Use `<i18n-t scope="global">` with named slots. Keep links, icons, code and
emphasis in the component. Put their placement in the catalog. The explicit
global scope prevents parent-scope warnings.

```vue
<i18n-t keypath="workspace.edit.patterns.wildcardHint" scope="global">
    <template #wildcard><code>*</code></template>
</i18n-t>
```

```json
{
    "workspace.edit.patterns.wildcardHint": "Use {wildcard} as wildcard. A plain directory matches all projects inside it."
}
```

### Shortcuts and lists

`i18n/composerPlaceholder.js` selects complete shortcut messages for keyboard,
touch and draft states. `Intl.ListFormat` formats independent shortcut labels
and alternative activation or send keys in the current locale.

Provider helpers return a catalog key for a complete working-state message when
they need provider-specific wording. They do not append untranslated notes.

### Renaming a key

- Inspect every translation call and `<i18n-t keypath>` that uses the key, including conditional keys.
- Update every catalog and the relevant tests.
- Do not change route names, setting values or API identifiers that match the old key.
- A rename alone preserves the message text and parameters.
- A change of sentence structure updates every catalog and the callers.

### Dates, editor and components

- Dates use Day.js with the locale loaded by `localeLoaders`. Components must not import date locales statically.
- A component that formats Day.js dates maps the locale to a Day.js name. For example `frontend/src/components/git/GitLog/table/components/TimestampData.vue` has a hardcoded map.
- CodeMirror phrases come from `editorPhrases()` in `frontend/src/composables/useCodeMirror.js`. Add new editor phrases to `EDITOR_PHRASE_KEYS` there.
- Web Awesome controls use the translation loaded with the locale.

## Display text in code

- Never freeze translated labels at module initialization.
- Shared presentation metadata can expose message keys. Consumers resolve the keys reactively.
- Presentation helpers return a key with named parameters, or translate at call time.
- Keep machine values, stored settings, routes, protocol identifiers, prompts, and user and provider content unchanged.
- Use complete messages for composed sentences, locale-aware plurals and lists, and named rich slots for inline elements.

Persist local validation and notification messages with `localUiMessage()`
from `frontend/src/i18n/uiMessages.js`.

- Resolve them with `resolveUiMessage()` during rendering or in a reactive getter.
- Keep external error prose as plain strings.
- Use `localUiError()` when callers need an `Error`. It keeps a string `message` and a `uiMessage` descriptor.
- Preserve that descriptor across local catch and cache boundaries.
- Never send these descriptors to the backend or a provider.
- Re-render visible cached errors and persistent notifications when the locale changes. Changing only the next message is not enough.

Follow backend-supplied interface prose to its producer. Add semantic
metadata there. See [Backend interface messages](#backend-interface-messages).

## Checks

### Android installation language

Android owns one persisted installation language: `en`, `de`, `es`, `fr`, or
`pt-BR`. It starts in English and does not read Android's system language.
Compatible embedded pages synchronize through the revisioned native language
bridge. Browser storage restoration is not an explicit native selection.
Each device and ordinary browser keeps an independent preference.

Keep editable messages in the existing JSON catalogs. Select the native subset
in `android/i18n/messages.json`. Reuse existing keys for equivalent meanings.
Declare numeric parameter formatting there. Keep identifiers and HTTP codes
verbatim. Add complete messages in all five catalogs for new native meanings.
Do not maintain translated Kotlin or Android XML copies.

From `frontend/`, run `npm run i18n:android:generate`, then
`npm run i18n:android:check` and `npm run lint:i18n:android`.
Commit the compiled token catalog, bootstrap XML, Vue oracle cases, bundled
instrumentation adapters, and `sources.sha256`. The generator uses the existing
compiler and Vite dependencies. The fixture bundles contain the real adapters
and their transitive i18n imports. They enter only the instrumentation APK.

Gradle checks source/output SHA-256 fingerprints with Java before `preBuild`.
Android standalone builds require no Node/npm setup. The opt-in Android CI
template installs frontend dependencies explicitly and checks deterministic
generation, Vue parity, and direct Kotlin source literals.

The direct-literal checker covers text, hints, accessibility text, dialogs,
menus, notification actions/channels, Toasts, and direct owned error producers.
It does not prove complete coverage. Review variable interpolation, dynamic
presentation helpers, and external error provenance manually.
No untranslated-message baseline exempts interface text.

Native controls and notifications render offline from the packaged catalog.
Device language never travels in backend requests or registration records.
New push payloads contain descriptors only. Update old APKs after the server
upgrade. A new APK renders valid legacy pushes with localized generic text and
retains their safe tap destinations. Open saved instances to reconcile their
payload capabilities after either upgrade direction.
Bridge capability and catalog compatibility can require a server or APK update.
See [Android documentation](../android/README.md#interface-language) and the
[execution handoff](plans/2026-11-08-android-internationalization-execution.md)
for rollout details and outstanding native acceptance.

### Browser and backend checks

Run from `frontend/`:

| Command | Checks |
| --- | --- |
| `npm run lint:i18n` | Official Vue I18n ESLint rules on source files and catalogs |
| `node --test eslint.i18n.test.js` | The configured detection scope of the lint |
| `node --test src/i18n/index.test.js` | Catalog key and parameter parity, message compilation, fallback, reactive locale changes |
| `npm test` | The whole frontend suite |
| `npm run build` | Production bundles, recovery messages, separate catalog chunks |

Backend, from the checkout root:

| Command | Checks |
| --- | --- |
| `uv run pytest tests/test_ui_messages.py tests/test_*_ui_messages.py` | Backend UI-message descriptors and producers (or run the full `uv run pytest`) |

The backend tests need `pytest-asyncio`, which the dev dependency group declares.

For a JSON report: `npm run lint:i18n -- --format json --output-file /tmp/twicc-i18n.json`.

### The lint

`npm run lint:i18n` invokes ESLint directly with `eslint.i18n.config.js`. It
checks `src/**/*.{vue,js,ts}` and `src/i18n/locales/*.json`. There is no custom
scanner or report format. ESLint exits 0 without errors, 1 for rule errors and
2 for configuration or execution failures.

- The [`no-raw-text` rule](https://eslint-plugin-vue-i18n.intlify.dev/rules/no-raw-text) of [`@intlify/eslint-plugin-vue-i18n`](https://eslint-plugin-vue-i18n.intlify.dev/) finds direct template text, direct literals in supported template expressions, and configured static text attributes.
- The attributes include `label`, `title`, `placeholder`, `alt`, `help-text`, `description`, `tooltip` and accessibility text, on native elements, Web Awesome controls and local components. Technical props such as `id`, `value`, `name`, `variant` and `href` are not text attributes.
- The catalog rules check missing referenced keys, locale key parity, duplicates, message syntax and embedded HTML.
- Unused-key and dynamic-key bans are off, because the application selects some keys dynamically.
- The official Vue processor and the `vue/comment-directive` rule allow narrow template-comment exceptions.

Tests, fixtures and generated dependencies are excluded. The Markdown bodies
under `public/help/` and `public/tips/` follow the
[content workflow](#help-and-tip-content), outside the template-text detection.
Their frontend controls stay in the lint.

Use a narrow, explained ESLint exception for a literal product name, URL,
keyboard chord or code example. Do not suppress interface prose. Do not add a
baseline for untranslated text.

### Blind spots

A clean lint result does not prove full coverage.

- The rule does not trace template variables through dictionaries, computed values, props, function calls or imports. It skips some bound expressions.
- It does not see script-generated messages or backend-supplied labels and errors.
- No test checks that a backend `ui_message` key exists in `en.json`. A missing key shows the English fallback text.
- The lint scope is `src/**/*.{vue,js,ts}` and `src/i18n/locales/*.json` only. It does not read standalone `frontend/*.html`, CSS, `vite.config.js`, Python files or share and artifact templates.

Review all these paths by hand. User names, provider content, files and
terminal output stay unchanged.

## Backend interface messages

Only messages meant for the browser interface need translation metadata.

- Console logs, terminal output, CLI and MCP responses, prompts, and provider and user content stay English or verbatim.
- Help and tip bodies use the [content workflow](#help-and-tip-content).
- The [pages that stay English](#pages-that-deliberately-stay-english) are excluded, not deferred.

Backend code does not select the browser locale or translate messages.

- `src/twicc/core/ui_messages.py` creates descriptors with `key`, `params` and an optional `count`.
- Keep the existing English text and machine codes.
- Add `ui_message` to browser-facing errors.
- For a named display field, add a companion such as `label_ui_message` or `error_ui_message`.
- Shared services use `ui_message_text()` or `UiMessageError` to keep metadata internally.
- Ordinary CLI and MCP serialization keeps English strings. Browser adapters expose the metadata explicitly.
- These tagged strings and exceptions keep their metadata through worker serialization.
- Add the key to every catalog, under the `backend.` area.

Raw OS, upstream SDK, browser and remote-server diagnostics stay verbatim where
no TwiCC-owned message exists. Known TwiCC-owned messages need semantic
metadata. An English diagnostic is not a substitute for their translation.

The frontend converts wire metadata with helpers in `frontend/src/i18n/remoteMessages.js`:

- `remoteUiMessage()` converts known wire metadata to a private local descriptor.
- `remoteUiError()` keeps a descriptor on `error.uiMessage`.
- `remoteUiErrors()` converts a list of validation errors, or falls back to a single message.
- Resolve messages during rendering with `resolveUiMessage()`, including cached errors, notifications and nested validation lists.
- Unknown keys or malformed metadata keep the original English fallback. Never display a raw key.
- Keep descriptors through REST, WebSocket events, reconnect snapshots, caches and native transport.

### Git failures

Six Git commands report failures: `show`, `diff-tree`, `log`, `add`,
`restore --staged` and `restore`. The keys are `backend.git.git_show_*`,
`git_diff-tree_*`, `git_log_*`, `git_add_*`, `git_restore_staged_*` and
`git_restore_*`. Each command has three keys:

- `_timeout`: the command timed out.
- `_failed`: the command failed with an empty stderr.
- `_failed_detail`: the command failed with a stderr. It has a `{detail}` parameter.

`_git_failed_message()` in `src/twicc/git.py` picks the base key when stderr is
empty, and the `_detail` key otherwise. `{detail}` is the raw Git stderr. It is
English and untranslated. Each language puts its own punctuation around it.

### Persisted provenance

Persist provenance when generated messages survive a reload.

- Peer delivery errors and tool errors keep their descriptor beside the original prose.
- Do not infer provenance by comparing English strings.
- Clear old metadata when its message changes.
- CLI and MCP projections omit presentation-only fields, including persisted timeline metadata.
- Existing records without provenance keep their fallback until a supported refresh or recomputation rebuilds their metadata.

Migration `0171_ui_message_provenance` (history) added peer error provenance
only (`PeerMessage.error_ui_message`). A new migration needs the normal user-run
migration and a backend restart before live use. Bump `CLAUDE_CODE_COMPUTE_VERSION`
or `CODEX_COMPUTE_VERSION` in `src/twicc/settings.py` to refresh historical
generated metadata through background computation.

Discovered command descriptions stay verbatim by product decision, including
the English defaults "Custom command", "Skill" and "Workflow". Do not add
translation metadata or database fields for these labels.

## Help and tip content

English sources keep their paths: `frontend/public/help/<id>.md` and
`frontend/public/tips/<id>.md`. A translation has the same filename under a
locale directory, for example `frontend/public/help/pt-BR/worktrees.md`. The
directory name is the application's locale code. Each English source has one
translated body per language.

Translations hold Markdown bodies only.

- Keep front-matter constraints in the English source.
- Keep translated titles in the interface catalogs.
- Do not duplicate provider, platform or OS constraints, or title translations.
- Stable content IDs, help cross-link targets and seen-state keys do not change with language.

The frontend loads the selected language, with English fallback.

- The fallback applies when the body is missing, empty or unavailable.
- It rejects HTML error or SPA responses, even with HTTP 200.
- If the English source also fails, the localized load-error interface appears.
- An English fallback body carries `lang="en"`.
- An open dialog or tip updates when the locale changes.
- Older asynchronous responses cannot overwrite the current page or language.
- Locale changes do not mark content seen or reset dismissal controls.
- Production caches separate files per language. Development fetches fresh bodies.

Authoring rules:

- Preserve code blocks, inline code, command examples, URLs and `help/<id>` targets.
- Translate prose, link labels and image alt text.
- Keep image paths relative to the shared `help/` or `tips/` root, also in translated bodies.
- Screenshots and other media stay shared.

### When English source changes

- English source changed: review all its translated bodies in the same change.
- Translated body changed: review it against the English source.
- New source: add translated bodies for every supported language.
- Source renamed or deleted: reconcile its translations and `help/<id>` links.
- New language: add a translated body for every English source.

Run from `frontend/`:

```bash
node --test src/utils/localizedContent*.test.js src/utils/contentAssets.test.js src/composables/useLocalizedContentBody.test.js src/components/help/HelpDialog.test.js
```

These tests check loading, language changes, fallback, technical-content
parity and translated-file coverage. They do not replace a review of the
meaning.

## Terminology

[docs/i18n-glossary.md](i18n-glossary.md) fixes one translation per recurring
domain term in each supported language, for example session, peer, workspace,
pane and panel. It keeps catalogs, help bodies and tip bodies consistent.

- Read the glossary before you translate or review interface text.
- Use the listed word whenever the English term has the listed meaning. The glossary works per concept. A word listed as "avoid" for one term can be right for another.
- To add a recurring term, add a row with a translation in **all** supported languages. Never add a row for one language only.
- When you change a row, update the catalogs and the help and tip bodies in the same change.

### Technical terms

TwiCC users are developers. The default is to keep the English word for terms
that developers say in English, such as commit, diff, token and shell. The
[Technical terms](i18n-glossary.md#technical-terms) section of the glossary
holds the rules and the per-language decisions.

- A `(kept)` marker in a cell means the English word stays verbatim in that language.
- Code spans stay verbatim, for example `git log`, `git restore --staged` and `/goal`.
- Key chords and example paths stay in English.
- Translate a term when it is an interface concept, has a natural unambiguous word, or clashes with a word of the language.

No automated check enforces the glossary. Reviewers verify it.

## Reviewing translations and keeping them current

- When English text changes, update every catalog in the same change.
- The parity test checks keys, message compilation and parameter names. It does not check meaning.
- Request an independent review of the meaning for each language.
- Follow the glossary and its register for each language.
- Review the full production file for a display change, not only the diff.

After you merge or rebase `main`:

- Run [How to run a pass](i18n-state.md#how-to-run-a-pass) up to step 7. `main` can add help and tip bodies or glossary rows, so do not skip steps 5 and 6.
- After a merge, the normal baseline works. After a rebase, the new baseline hash hides what `main` added. In step 1, use the `ORIG_HEAD` form (or the saved `i18n-baseline` tag after a second rebase), as [described there](i18n-state.md). Do it before another Git operation overwrites `ORIG_HEAD`. Save the baseline before you rebase again.
- To narrow the review to what `main` added, run `git diff --name-status --find-renames ORIG_HEAD...main -- frontend/ src/twicc/ android/ docs/i18n-glossary.md` right after the rebase or merge. `ORIG_HEAD` is the old branch tip, and the three-dot form diffs from the old `main` tip to `main`. A later Git operation overwrites `ORIG_HEAD`, so run the command first.
- Run `node --test src/i18n/index.test.js` and `npm run lint:i18n`. They fail on keys that `main` added to `en.json` only.
- New strings in `.vue` and `.js` files, in `index.html` and in backend Python files need keys, translations and `ui_message` metadata.
- Lint does not see backend or script-generated strings.

[docs/i18n-state.md](i18n-state.md) records the last complete translation
pass. Its Git history is the baseline. It also holds the full scope, the
deliberate exclusions and the steps to run a new pass.

## Adding a language

1. **Glossary:** in [docs/i18n-glossary.md](i18n-glossary.md), add a column to every table. Also add the language to the register line in *How to use it*. In *Technical terms > Rules*, review every bullet that names languages: *Case*, *Official convention*, *Imperative labels*, *Plural*, *Gender of kept terms* and *Verbs from kept nouns*. Fill each term before you translate the catalog.
2. **Catalog:** copy `en.json` to `frontend/src/i18n/locales/<locale>.json`, with a BCP 47 locale code. Translate it with the glossary words. Add the `<locale>.js` adapter, following `locales/pt-BR.js`.
3. **Registration:** in `frontend/src/i18n/index.js`, add the locale to `SUPPORTED_LOCALES` (use the language's own name as label) and a dynamic import to `localeLoaders`. Do not add a static catalog import.
   - The loader imports the catalog adapter, the Day.js locale and the Web Awesome translation.
   - The Day.js locale is `dayjs/locale/<name>.js`. The name is lowercase (`pt-br`).
   - Web Awesome has no `pt-BR.js`: pt-BR uses the language file `pt.js`. The list is in `node_modules/@awesome.me/webawesome/dist/translations`. If Web Awesome has no translation for the language, omit that import.
   - Keep the `typeof window === 'undefined' ? null : import(...)` guard on the Web Awesome import. Node tests import the loader.
4. **Dates:** add the locale to the Day.js name map in `frontend/src/components/git/GitLog/table/components/TimestampData.vue`.
5. **Recovery messages:** add the locale to the list in `frontend/vite.config.js` (`injectRecoveryMessages`) and to the `saved === ...` checks in `frontend/index.html`. The catalog must hold `startup.assetsUnavailable.description` and `common.actions.retry`. Otherwise the build throws `Missing recovery message`.
6. **Plurals:** add a `pluralRules` entry in `index.js` if the language needs one.
7. **Content:** add one translated body per English source under `frontend/public/help/<locale>/` and `frontend/public/tips/<locale>/`. Use the glossary words.
8. **README and docs:** add the language to the *Interface languages* section of `README.md`, to the list at the top of this page and to the *Supported languages* table.
9. **Tests:** in `frontend/src/i18n/index.test.js`, add the catalog to the imports and to the `{ en, de, es, fr, 'pt-BR' }` object, and add a `loadLocaleMessages('<locale>')` call at the top. The `lint:i18n` globs are generic. `localizedContentFiles.test.js` iterates `SUPPORTED_LOCALES` and needs no edit.
10. **Checks:** run `cd frontend && node --test src/i18n/index.test.js`, then `npm test`, then `npm run build`. Confirm that the build emits a separate catalog chunk, that an English startup does not request it, and that public viewers stay English.
11. **Browser:** check the language selector, persistence after reload and the login screen. Check narrow layouts. Do not translate user or agent content.

The language selector (`frontend/src/components/app/SettingsPopover.vue`)
iterates `SUPPORTED_LOCALES`. It needs no edit.

New interface copy needs an English catalog key and a matching translation in
each supported language. Code, comments and contributor documentation stay
English. Translated catalogs and localized Markdown bodies are the exceptions.
