# Interface languages

Choose **Settings → General → Language** to switch between **English**,
**Deutsch**, **Español**, **Français** and **Português (Brasil)**. The change is immediate. The preference is saved in
this browser's existing `twicc-settings` localStorage entry and restored before
the login screen is mounted. It is deliberately local to the browser, like
the color scheme; it does not change the language on other devices.

English remains the default, including when stored settings are invalid or
unavailable. Choosing a language affects the interface, not agent prompts,
responses, project names, saved snippets, terminal output or file contents.

## Current coverage

The frontend audit includes local interface text in templates, scripts, shared
metadata, provider presentation helpers, generated DOM and HTML, CSS loading
labels, editor controls, and standalone entry points. Both catalogs have the
same message keys. Dates, numbers, lists and plurals use the selected language.
The HTML `lang` attribute follows it too, including Web Awesome controls that
use the document language.

`docs/i18n-checklist.md` records the status of the complete file-by-file
pass and independent reviews. A clean lint result alone does not establish
complete interface coverage. Backend-owned interface messages carry semantic
translation metadata. Help and tip bodies use translated Markdown files.
Their titles and controls use the interface catalogs; images remain shared assets.

The share viewer and artifact shell install the same translation plugin for
reused components. Public viewers force English before rendering titles or mounting
components, regardless of the saved application locale. They have no language selector
and do not change the saved preference. Private artifact pages keep the application locale.

### Pages that deliberately stay English

These pages do not require translation. They are not deferred translation work.

| Page | Source |
| --- | --- |
| Public sharing, including shared session/document/artifact viewers and the recent-share list | `src/twicc/share/` and the public viewers under `frontend/src/share-session/` |
| Share password form | `src/twicc/share/templates/share_auth.html` |
| Unavailable share | `src/twicc/share/html.py`, `_UNAVAILABLE_PAGE` |
| Standalone artifact password form | `src/twicc/artifacts/templates/artifact_auth.html` |
| External MCP authorization waiting page | `src/twicc/mcp/oauth/routes.py`, `WAIT_PAGE`, served at `/mcp/oauth/wait` |
| Telemetry collector information page, outside the application | `telemetry-collector/public/index.html` |
| Standard Django error pages | Framework-generated responses, including 404 and 500 |

Apply these exclusions by role, not by the presence of JavaScript.
The MCP waiting page is independent of the Vue application.
The MCP approval dialog inside TwiCC remains in scope and is translated.
Telemetry dialogs and settings inside TwiCC also remain in scope and are translated.
The application's sharing controls and private artifact viewer remain in scope.
The artifact shell serves both private and public pages; keep this distinction when auditing shared code.

Shared components keep their existing translations for use inside the application.
Public entry points select English; they do not need separate English component copies.
Do not classify these exclusions as missing translations during later passes.

## Implementation

`frontend/src/i18n/index.js` configures Vue I18n in Composition API mode with
English fallback. `locales/en.json` is the source catalog, and
`locales/de.json`, `locales/es.json`, `locales/fr.json` and `locales/pt-BR.json` contain the German,
Spanish, French and Brazilian Portuguese translations. German is standard modern German
addressed with the informal `du` form; it uses the default plural rule, so zero is plural.
Spanish is neutral international Spanish
addressed with the informal `tú` form; it uses the default plural rule, so zero is plural.
Brazilian Portuguese addresses the user with `você`.
French uses the `vous` form and a custom plural rule
(`pluralRules` in `index.js`): zero and one take the singular form. Messages
render as text or Vue component slots, never as HTML strings.

English is the only catalog imported synchronously. Other catalogs and their
Web Awesome and Day.js translations use dynamic imports, following the
[Vue I18n lazy-loading pattern](https://vue-i18n.intlify.dev/guide/advanced/lazy).
`setLocale()` waits for the requested catalog before updating the composer,
document language and CSS loading label. Await it during startup, before login
or other interface messages render. Public viewers select English without reading
the stored locale; private artifact pages await their saved locale.

Successful imports remain cached for the page lifetime. Concurrent requests share
one download, and only the latest selection can change the displayed language.
A failed import leaves English available and preserves the saved preference.
The loader clears failed requests; browsers can retain a failed module until reload.
Reload the page to retry the saved language. The settings store owns persistence.
The inline asset-recovery guard embeds only
two messages per language so it still works when application modules cannot load.
It leaves optional locale-chunk failures with the locale loader instead of
reloading the whole application. A stale page with an old locale-chunk hash
therefore falls back to English until the user reloads it.

Keep code splitting enabled in the SPA, artifact-shell and share-viewer builds.
Otherwise the bundler inlines optional catalogs into their entry bundles.
Standalone viewer chunks stay at the bundle root because backend routes accept
one asset path segment. Tests that render a translation explicitly call
`await loadLocaleMessages('pt-BR')`, `'de'`, `'es'` or `'fr'`; production never preloads every supported locale.

Components use `$t('key')` in templates or `useI18n().t` in setup code. Build
translated option arrays and labels in `computed()` so switching languages
updates mounted controls. Leave wire values, route names and API identifiers
unchanged. Use named interpolation and whole-message plural forms rather
than appending English suffixes:

```vue
{{ $t('workspace.projectCount', { count }, count) }}
```

```json
{
    "workspace.projectCount": "{count} project | {count} projects"
}
```

Vue I18n treats `@`, `{`, `}` and `|` as message syntax. Use literal
interpolation when these characters are text, for example `{'@'}`.
Keep identifiers such as `tmux`, Git commands and keyboard chords intact.

### Message key names

Catalogs store flat, dotted keys. Each key names the message's purpose and
context, rather than copying the English text. Use lower camel case within
each segment, for example `settings.general.language.label`,
`project.edit.icon.errors.tooLarge` or `session.sharing.unavailableHistory`.
Keep related keys together in alphabetical order in both catalogs.

The first segment identifies the domain: `auth`, `composer`, `help`, `home`,
`navigation`, `project`, `session`, `settings` or `workspace`. Use `common` for
messages with the same meaning across domains, such as `common.actions.cancel`.
Do not share keys only because their current English text is identical.

Use roles such as `label`, `hint`, `tooltip`, `placeholder` or `errors` when
they distinguish messages within a feature. Each message must contain a complete
sentence, label or instruction. Do not concatenate translated prefixes, values
and suffixes, or add English plural endings. Put punctuation and named value
parameters inside the message. Use a complete message for each conditional
variant, such as inherited trust with or without a source project.

For rich text, use `<i18n-t scope="global">` with named slots. Keep links,
icons, code and emphasis in the component; put their placement in the catalog.
The explicit global scope prevents parent-scope warnings. For example:

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

`i18n/composerPlaceholder.js` selects complete shortcut messages for keyboard,
touch and draft states. `Intl.ListFormat` formats independent shortcut labels
and alternative activation/send keys in the current locale. Provider helpers
return a catalog key for a complete working-state message when they need
provider-specific wording. They do not append untranslated notes.

When renaming a key, inspect every translation call and `<i18n-t keypath>` that
uses it, including conditional keys. Update both catalogs and relevant tests.
Do not change route names, setting values or API identifiers that happen to
match an old key. A key rename alone must preserve message text and parameters.
Changing sentence structure requires updating both locales and the callers.

## Checking direct interface text

Run the official Vue I18n ESLint rules from the checkout's `frontend/` directory:

```bash
npm run lint:i18n
npm run lint:i18n -- --format json --output-file /tmp/twicc-i18n.json
```

These commands invoke ESLint directly with `eslint.i18n.config.js`. There is no
custom scanner or report format. Text detection and catalog checks use official
[`@intlify/eslint-plugin-vue-i18n`](https://eslint-plugin-vue-i18n.intlify.dev/)
rules. The official Vue processor and `vue/comment-directive` rule apply narrow
template-comment exceptions. The config checks source files and both catalogs.
ESLint exits 0 without errors,
1 for rule errors and 2 for configuration or execution failures.

The [`no-raw-text` rule](https://eslint-plugin-vue-i18n.intlify.dev/rules/no-raw-text)
finds direct template text, direct literals in supported template expressions,
and configured static text attributes. The attributes include `label`, `title`,
`placeholder`, `alt`, `help-text`, `description`, `tooltip`, and accessibility
text on native elements, Web Awesome controls and local components. Technical
props such as `id`, `value`, `name`, `variant` and `href` are not text attributes.

The catalog rules check missing referenced keys, locale key parity, duplicates,
message syntax and embedded HTML. Unused-key and dynamic-key bans are disabled
because the application intentionally selects some keys dynamically.

Review findings file by file. Confirm each message's purpose before adding a
semantic key, an English source message and a translation for each supported language.
Use complete messages, including punctuation and parameters. Keep links, icons
and emphasis in named `i18n-t` slots. Use a narrow, explained ESLint exception
for a literal product name, URL, keyboard chord or code example when necessary.
Do not suppress interface prose or add a baseline for untranslated text.

Tests, fixtures and generated dependencies are excluded. Markdown bodies under
`public/help/` and `public/tips/` use the content workflow below, outside ESLint's
template-text detection. Their frontend controls remain included in lint.

A clean result covers the official rules, not the entire interface. The rule
does not trace template variables through dictionaries, computed values, props,
function calls or imports. It also skips some bound expressions. Script-generated
messages and backend-supplied labels/errors still require the later file-by-file
review. User names, provider content, files and terminal output stay unchanged.

Run `node --test eslint.i18n.test.js` to check the configured detection scope.
The existing `src/i18n/index.test.js` also checks catalog key/parameter parity,
message compilation, fallback and reactive locale changes.

## File-by-file interface audit

`docs/i18n-checklist.md` records frontend and backend application sources and their audit states.
Its current working copy stays uncommitted until the complete pass finishes.
A completed checklist is committed with the validated translations. That commit
becomes the reference for a later pass. At the start of that pass, uncheck all
items in the working copy. Commit the checklist again only after completing the
new pass. The last commit that changes this file must always represent a
completed audit, never partial progress.

Find the baseline from Git, not from a date or the current branch tip:

```bash
baseline_commit=$(git log -1 --follow --format=%H -- docs/i18n-checklist.md)
git diff --name-status --find-renames "$baseline_commit" -- frontend/ src/twicc/ android/
git ls-files --others --exclude-standard -- frontend/ src/twicc/ android/
```

The diff includes committed, staged and unstaged changes since the baseline.
The second listing identifies untracked files, which the diff cannot show.
If no completed checklist commit exists, perform the initial full source audit.
Do not substitute an unrelated commit as the baseline.

Reconcile the current application sources with the inventory using its scope rules.
Inspect recorded native transport dependencies too; this is not a complete Android interface audit.
Add every new in-scope file as unchecked, including new files outside existing source directories.
Remove deleted entries and update renamed paths. Reassess files whose roles change.
An absent file is not automatically an omission: deliberate exclusions stay excluded.
Never add them as missing or unverified files just to match the directory listing.

Start from a complete frontend application source inventory. Include standalone
entry points, Vue components, JavaScript, TypeScript, HTML, CSS, display metadata
and translation catalogs. Include configuration only when it directly supplies
interface messages: `frontend/vite.config.js` injects startup recovery messages
into `frontend/index.html`.

Keep help/tip interface components and metadata sources in this source inventory.
Their controls and titles are translated. The catalogs contain 12 help titles
and 54 tip titles per locale. Include one content entry per English Markdown
source; that entry covers the source and all its translated bodies.

Do not add checkboxes for images, icons, screenshots, videos, public assets,
help/tip authoring guides, tests, harnesses, fixtures or benchmark data.
Help/tip Markdown bodies belong to the content inventory described below.
Translated bodies are dependencies of their English entry, not separate checkboxes.
Exclude package manifests, lockfiles and tooling/build configuration without
interface messages. List these exclusions once in the scope, not as checked files.
The checklist's **Deliberate exclusions** table defines recognition rules for every later pass.
Apply them to new files too. Generated output and dependencies also stay outside the inventory.
Classify by role: runtime display dictionaries remain in scope even when they resemble fixture data.
Public executable code and configuration that supplies interface messages also remain in scope.
Inspect unknown file types before classifying them. Retain application helpers even when they contain no interface text.

Read each production file in full. Identify all output paths: template text and
expressions, bound attributes and component props, structured options, slots,
DOM writes, generated HTML, toasts, notifications, titles, validation messages,
formatting and library configuration. Official lint supplements this review.

Follow displayed values through local expressions, helpers, imports and shared
metadata. Record unresolved dependencies against the originating consumer and
the owning source file. A dependency lookup is not a complete audit of that
source file. Group requests for the same shared definition so it is handled
once. Verify affected consumers after changing a shared display contract.

Keep machine values, stored settings, routes, protocol identifiers, prompts and
user/provider content unchanged. Shared presentation metadata can expose message
keys; consumers resolve those keys reactively. Presentation helpers can translate
at call time or return a key with named parameters. Never freeze translated
labels at module initialization. Use complete messages for composed sentences,
locale-aware plurals and lists, and named rich slots for inline elements.

Persist local validation and notification messages with `localUiMessage()`
from `src/i18n/uiMessages.js`. Resolve them with `resolveUiMessage()` during
rendering or in a reactive getter. Keep external error prose as plain strings.
Use `localUiError()` when callers require an `Error`: it retains a string
`message` and a `uiMessage` descriptor. Preserve that descriptor across local
catch/cache boundaries. Never send these UI descriptors to the backend or a
provider. Recheck visible cached errors and persistent notifications while
changing locale; changing only the next-created message is insufficient.

Each file receives an implementation audit and an independent review. Reviewers
read the whole production file, not only its diff. Check an item only after its
findings and relevant dependencies are resolved. If a later change invalidates
a reviewed file or its display dependencies, reopen the affected checks. Add new
files and account for renamed or removed files before declaring completion.
Follow backend-supplied interface prose to its producer and add semantic presentation metadata.
Keep provider content and raw external diagnostics separate from TwiCC-owned interface messages.

Before completion, verify catalog key/parameter parity, message compilation,
official lint, relevant tests, production bundles and browser locale changes.
Future passes compare code with the last completed checklist commit. Unchanged
files can carry their prior review forward only when relevant dependencies and
translation contracts are unchanged.

## Adding a language

1. Add a column for the language to every table in `docs/i18n-glossary.md`.
   Fill each term with the glossary meaning before you translate the catalog.
2. Copy the English catalog to a new BCP 47 locale file and translate it with
   the glossary words. Use the same words in the help and tip bodies.
3. Register the locale in `SUPPORTED_LOCALES` and add a dynamic import to
   `localeLoaders` in `src/i18n/index.js`. Add a small JavaScript catalog entry,
   following `locales/pt-BR.js`, to support JSON imports in both Node and Vite.
   Use the language's own name for its selector label.
   Load its Web Awesome translation in the same loader when available.
   Load its Day.js locale there too; components must not import date locales statically.
   Do not add a static catalog import or preload every locale.
4. Keep catalog keys aligned and preserve interpolation parameter names.
5. Run `cd frontend && node --test src/i18n/index.test.js`, the frontend
   suite (`npm test`) and the production build (`npm run build`).
6. Check the language selector, reload persistence and login in a browser.
   Check narrow layouts and avoid translating user or agent content.
7. Add the locale to the recovery-message subset in `vite.config.js`. The inline
   guard in `index.html` must recognize it before application modules load.
8. Confirm the production build emits a separate catalog chunk. Confirm an
   English startup does not request it and public viewers remain English.

New UI copy should use an English catalog key and a matching translation in
each supported language. Code, comments and contributor documentation remain English;
translated catalogs and localized Markdown bodies are the exceptions.

## Glossary

`docs/i18n-glossary.md` fixes one translation per recurring domain term in
each supported language, for example session, peer, workspace, pane and panel.
It keeps terminology consistent across catalogs, help bodies and tip bodies.

- Read the glossary before you translate or review any interface text.
- Use the listed word whenever the English term has the listed meaning.
  The glossary works per concept: a word that is listed as "avoid" for one
  term can still be right for another term.
- When you add a recurring term, add a row with a translation in **all**
  supported languages. Do not add a row for one language only.
- When you change a row, update the catalogs and the help and tip bodies in the
  same pass.

No automated check enforces the glossary. Reviewers verify it.

## Backend interface messages

Only messages intended for the browser interface require translation metadata.
Console logs, terminal output, CLI/MCP responses, prompts and provider/user content remain English or verbatim.
Help/tip bodies use the localized Markdown workflow below.
The pages listed under **Pages that deliberately stay English** are excluded, not deferred.
Mixed HTML modules stay in scope for their application-facing paths; preserve the excluded pages.

Backend code does not select the browser locale or translate messages.
`src/twicc/core/ui_messages.py` creates descriptors containing `key`, `params` and optional `count`.
Keep the existing English text and machine codes. Add `ui_message` to browser-facing errors.
For a named display field, add a companion such as `label_ui_message` or `error_ui_message`.
Shared services can use `ui_message_text()` or `UiMessageError` to retain metadata internally.
Ordinary CLI/MCP serialization keeps English strings; browser adapters explicitly expose metadata.
These tagged strings and exceptions preserve metadata through worker serialization.

Frontend `remoteUiMessage()` converts known wire metadata to private local descriptors.
`remoteUiError()` retains a descriptor on `error.uiMessage`. Resolve messages during rendering
with `resolveUiMessage()`, including cached errors, notifications and nested validation lists.
Unknown keys or malformed metadata retain the original English fallback; never display a raw key.
Keep descriptors through REST, WebSocket events, reconnect snapshots, caches and native transport.

Persist provenance when generated messages survive a reload.
Peer delivery errors and tool errors retain their descriptor beside the original prose.
Do not infer provenance by comparing English strings. Clear old metadata when its message changes.
CLI/MCP projections must omit presentation-only fields, including persisted timeline metadata.
Existing records without provenance keep their fallback until supported refresh or recomputation rebuilds their metadata.

Discovered command descriptions stay verbatim by product decision, including the
English defaults "Custom command", "Skill" and "Workflow". Do not add translation
metadata or database fields for these labels.

Migration `0170_ui_message_provenance` adds peer error provenance only.
It requires the normal user-controlled migration and backend restart before live use.
The compute versions refresh historical generated tool-error metadata through normal background computation.

## Help and tip content

English sources keep their existing paths: `frontend/public/help/<id>.md`
and `frontend/public/tips/<id>.md`. Each translation has the same filename
under a locale directory, for example `frontend/public/help/pt-BR/worktrees.md`.
The locale directory matches the application's supported locale identifier.

Translations contain Markdown bodies only. Keep front-matter constraints in
the English source and translated titles in the existing interface catalogs.
Do not duplicate provider/platform/OS constraints or title translations.
Stable content IDs, help cross-link targets and seen-state keys do not change with language.

The frontend loads the selected language, with English fallback when its body
is missing, empty or unavailable. It rejects HTML error/SPA responses, even
with HTTP 200. If the English source also fails, the existing localized load-error
interface appears. An English fallback body carries `lang="en"`.
An open dialog or tip updates when the application locale changes.
Older asynchronous responses cannot overwrite the current page or language.
Locale changes do not mark content seen or reset dismissal controls.
Production caches separate files by language; development fetches fresh bodies.

Preserve code blocks, inline code, command examples, URLs and `help/<id>` targets.
Translate prose, link labels and image alt text. Keep image paths relative to
the shared `help/` or `tips/` root, even in translated bodies. Screenshots and
other media remain shared and excluded from the source checklist.

### Follow-up audits use Git

Use the last completed `docs/i18n-checklist.md` commit as the baseline, exactly
as for application code. Add no source hashes, fingerprints or parallel status files.
Keep one checkbox for each English source. That checkbox covers all its translations.

- When an English source changes, reopen its entry and review all translated bodies.
- When a translated body changes, reopen its English entry and review that translation.
- When a new source appears, add its unchecked entry and translations for supported languages.
- When adding a language, reopen the content entries and add their translated bodies.
- When a source is renamed or deleted, reconcile its entry, translations and help links.
- Include untracked sources and translations when reconciling the Git diff.

Check the entry only after the source and affected translations pass implementation
review and independent review. Commit the completed inventory with the validated
translations when requested. Authoring guides and media remain deliberate exclusions.

`node --test src/utils/localizedContent*.test.js src/utils/contentAssets.test.js src/composables/useLocalizedContentBody.test.js src/components/help/HelpDialog.test.js`
from `frontend/` checks loading, language changes, fallback, technical-content parity
and translated-file coverage. These checks do not replace the meaning review.
