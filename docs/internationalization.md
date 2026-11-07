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

The first translation covers the home page, navigation rail, project and
workspace cards and management dialogs, most general settings controls,
session header controls, session search and selection, and the message
composer's main controls. The navigation rail also translates the global
timeline destination, its active listening label, and speech playback controls.
The Summaries and Text to Speech section names are translated; their detailed
settings panels remain English. Dates rendered by the shared date utility follow
the selected language. The HTML `lang` attribute follows it too, including
Web Awesome controls that use the document language.

This is an incremental migration. Provider-specific settings, detailed help
and keyboard shortcut descriptions, several tool panels, server-supplied
errors and some advanced dialogs still use English. The share viewer
installs the same translation plugin for reused components, with its own
browser preference on the share origin; this does not claim a complete
translation of the public viewer.

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
