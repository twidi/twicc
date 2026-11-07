# Interface languages

Choose **Settings → General → Language** to switch between **English** and
**Português (Brasil)**. The change is immediate. The preference is saved in
this browser's existing `twicc-settings` localStorage entry and restored before
the login screen is mounted. It is deliberately local to the browser, like
the color scheme; it does not change the language on other devices.

English remains the default, including when stored settings are invalid or
unavailable. Choosing a language affects the interface, not agent prompts,
responses, project names, saved snippets, terminal output or file contents.

## Current coverage

The direct-template migration covers applicable interface text reported by the
official Vue I18n ESLint rules. This includes settings panels, navigation,
dialogs, session controls, tool panels and public-viewer controls. Both catalogs
contain 1,835 messages. Dates rendered by the shared date utility follow the
selected language. The HTML `lang` attribute follows it too, including Web
Awesome controls that use the document language.

This is an incremental migration. Labels and messages built in scripts,
dictionaries, provider helpers or the backend still need a file-by-file review.
Long help pages and tips need a separate content translation workflow. A clean
lint result does not establish complete interface coverage.

The share viewer and artifact shell install the same translation plugin for
reused components. Their language preference belongs to their browser origin;
it does not change the language on another origin or device.

## Implementation

`frontend/src/i18n/index.js` configures Vue I18n in Composition API mode with
English fallback. `locales/en.json` is the source catalog, and
`locales/pt-BR.json` contains Brazilian Portuguese translations. Messages
render as text or Vue component slots, never as HTML strings.

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
semantic key, an English source message and a Brazilian Portuguese translation.
Use complete messages, including punctuation and parameters. Keep links, icons
and emphasis in named `i18n-t` slots. Use a narrow, explained ESLint exception
for a literal product name, URL, keyboard chord or code example when necessary.
Do not suppress interface prose or add a baseline for untranslated text.

Tests, fixtures and generated dependencies are excluded. Long content under
`public/help/` and `public/tips/` needs its separate translation workflow. Their
frontend controls remain included.

A clean result covers the official rules, not the entire interface. The rule
does not trace template variables through dictionaries, computed values, props,
function calls or imports. It also skips some bound expressions. Script-generated
messages and backend-supplied labels/errors still require the later file-by-file
review. User names, provider content, files and terminal output stay unchanged.

Run `node --test eslint.i18n.test.js` to check the configured detection scope.
The existing `src/i18n/index.test.js` also checks catalog key/parameter parity,
message compilation, fallback and reactive locale changes.

## Adding a language

1. Copy the English catalog to a new BCP 47 locale file and translate it.
2. Import the catalog and register it in `createAppI18n()` and
   `SUPPORTED_LOCALES`; use the language's own name for its selector label.
3. Keep catalog keys aligned and preserve interpolation parameter names.
4. Run `cd frontend && node --test src/i18n/index.test.js`, the frontend
   suite (`npm test`) and the production build (`npm run build`).
5. Check the language selector, reload persistence and login in a browser.
   Check narrow layouts and avoid translating user or agent content.

New UI copy should use an English catalog key and a matching Portuguese
translation. Code, comments and contributor documentation remain English;
translated catalogs are the exception to the English source convention.
