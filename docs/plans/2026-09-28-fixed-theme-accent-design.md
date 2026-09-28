# Fixed theme and accent — design (visual refresh, intermediate step)

## 1. Context

The "Signature" visual refresh (`docs/plans/2026-09-26-visual-refresh-roadmap.md`) turns a
sober, configurable UI into a UI designed and colored by us. Tuning every theme × accent ×
scheme combination is not sustainable: the user tested several accents, and some details
fail in light or in dark depending on the color. Steps 1–4 are done; this step comes before
step 5.

### 1.1 User decisions (2026-09-28) — do not reopen

- **One Web Awesome theme: `default`. One accent: `cyan`.** Both become fixed; the user can
  no longer choose them. The light/dark/auto **color scheme** stays a user choice.
- The word **accent** stays (tokens, comments, docs): it names the UI's brand color, which
  is now fixed, not a user choice.
- Everything that handles the `awesome` and `shoelace` themes or another accent is
  **removed**: it no longer exists and must not remain in the code.
- A user who saved another theme or accent gets `default` + `cyan` **without any
  message**. The stored values are ignored and purged.
- No CHANGELOG entry now: one entry for the whole branch, written at its end.

## 2. Goals

1. The DOM always carries `wa-theme-default wa-palette-default wa-brand-cyan`, from the
   first paint, whatever is stored.
2. No UI, command, setting key, store field, validator, constant, CSS branch or test
   remains for a theme other than `default` or an accent other than `cyan`.
3. Stored `waTheme` / `waBrand` values disappear: from `localStorage` (frontend) and from
   `settings.json` (backend), and an old tab still open cannot write them back.
4. `npm test` and `uv run pytest` stay green.

## 3. Out of scope

- The color scheme (light/dark/auto) and everything wired to it.
- Project and workspace colors (unrelated to the accent).
- The share viewer, the artifact shell, the server-rendered auth pages: they already
  hard-code `default` + `cyan` (`frontend/src/share-session/theme.js`,
  `src/twicc/share/templates/share_auth.html`, `src/twicc/artifacts/templates/artifact_auth.html`);
  only the dead parts named in §4.6 change.
- Historical documents (`docs/plans/*` other than the roadmap, `CHANGELOG.md` released
  sections).

## 4. Frontend

### 4.1 `frontend/src/utils/theme.js`

- The applied classes are constant: `wa-theme-default`, `wa-palette-default`,
  `wa-brand-cyan`. The module no longer reads `waTheme` / `waBrand` from `localStorage`
  (today it does, before the store exists: an old value would still apply at boot).
- Remove `DEFAULT_WA_THEME`, `DEFAULT_WA_BRAND`, `THEME_TO_PALETTE`, `currentWaTheme`,
  `currentWaBrand`, the class lists used to remove other themes/palettes/brands,
  `setWaTheme()`, `setWaBrand()`. `applyWaClasses()` (or its replacement) only adds the
  three classes; `initTheme()` calls it once. The color-scheme handling and
  `recomputeCachedColors()` stay (the cached selection color still derives from
  `--wa-color-brand-50`, now always cyan).
- Stop setting `documentElement.dataset.theme` (its only reader, `App.vue`'s
  `[data-theme="awesome"]` rule, is removed in §4.4).

`frontend/index.html` already has `class="loading wa-theme-default wa-palette-default
wa-brand-cyan"`: unchanged.

### 4.2 `frontend/src/main.js`

Remove the imports of `@awesome.me/webawesome/dist/styles/themes/awesome.css` and
`…/themes/shoelace.css`; keep `webawesome.css` and `themes/default.css`. Update the comment
"all free themes loaded for runtime switching".

### 4.3 Settings, store, commands

- `frontend/src/constants.js`: remove `WA_THEME`, `WA_THEME_LABELS`,
  `WA_THEME_DEFAULT_PALETTE`, `WA_BRAND`, `WA_BRAND_LABELS`; remove `'waTheme'` and
  `'waBrand'` from `SYNCED_SETTINGS_KEYS`.
- `frontend/src/stores/settings.js`: remove the imports, the schema entries, the
  validators, the getters `getWaTheme` / `getWaBrand`, the actions `setWaTheme` /
  `setWaBrand`, the two DOM watchers, the two keys in `collectAllSyncedSettings`. The
  store's existing dirty-key check then rewrites `localStorage` without the two keys
  (unknown keys are dropped): nothing else is needed for the local purge.
- `frontend/src/components/app/SettingsPopover.vue`: remove the "Theme" and "Accent color"
  selects of the General section, their options, computeds, handlers and imports. The
  "Color scheme" select stays.
- `frontend/src/commands/staticCommands.js`: remove the commands `display.wa-theme`
  ("Change Theme…") and `display.wa-brand` ("Change Brand Color…") and their imports.
  `display.color-scheme` stays.
- `frontend/src/composables/useCodeMirror.js` and `useTerminal.js`: remove `getWaTheme`
  from their watch sources (the color scheme stays).

### 4.4 CSS — remove every theme branch

- `frontend/src/App.vue`: remove the `&[data-theme="awesome"] { --divider-size: 4px }`
  rule; `--divider-size: 1px` stays.
- `frontend/src/styles/motion.css`: remove the `:where(.wa-theme-awesome wa-button)` block
  and its comment; the press rule keeps only the default-theme branch, without the
  `:root:not(.wa-theme-awesome)` prefix; remove the `:root.wa-theme-awesome …` branch. In
  the "Exclusions:" comment above it, keep the still-valid exclusions (inert buttons, group
  segments, the full-width sidebar rows, with their reasons) and drop only the part about
  the awesome theme.
- `frontend/src/styles/depth.css`: drop the `:root:not(.wa-theme-awesome)` prefix from the
  raised-button, accent-button and recessed-field rules. In the comment above the global
  rules, keep "Global rules below are wrapped in :where() (specificity 0 + ::part), so any
  unlayered component rule … wins. Being unlayered they beat every layered Web Awesome
  rule." and drop only the part of that sentence about the awesome theme keeping its hard
  shadows. Exact results (the `…` parts stay character for character):
  - `:where(\n    wa-button:is([appearance*='outlined'], [appearance*='filled']):not(…)\n)::part(base)`
  - `:where(\n    wa-button[appearance='accent']:not(.wa-invert wa-button, .layout-winbtn)\n)::part(base)`
  - `:where(:is(wa-input, wa-textarea))::part(base),` / `:where(wa-select)::part(combobox)`
- `frontend/src/styles/motion.css`, press rule, exact result (keep the multi-line
  `:where(\n` form: `motion.test.js` locates the rule with `indexOf(':where(\n')`):
  `:where(\n    wa-button:not([disabled], [loading], wa-button-group wa-button, .session-item, .bookmark-item):active\n)::part(base)`.
- `frontend/src/components/message/MessageInput.vue`: the rule becomes
  `.message-input wa-textarea::part(base)`; remove the awesome comment.
- **Specificity:** every depth/motion selector above sits inside `:where()`, so dropping
  the prefix changes nothing (0 before, 0 after). The one unwrapped rule
  (`MessageInput.vue`) still beats its only competitor, `depth.css`'s recessed-field rule
  (scoped, about (0,2,2) against (0,0,1)). No Web Awesome default-theme rule competes
  (`themes/default.css` has no component or `::part` selector). **Do not add `:root`** or
  any other weight: the global rules must stay weaker than component overrides
  (the comment above the global rules in `depth.css`).
- `frontend/src/components/message/MessageSnippetsBar.vue`: remove `transform: none` from
  `.snippet-btn:active` and from `.snippet-btn.snippet-disabled:active`, and the comment
  "Cancels the awesome theme's press on native buttons". The default theme's native
  button `:active` sets no transform (`native.css`); the awesome theme's 4px translate was
  the only one.
- `frontend/src/components/ui/SegmentedControl.vue`: remove `box-shadow: none`,
  `transform: none` and their comment (they only cancelled the awesome theme's rules), and
  the paragraph of its comments about the awesome/shoelace themes if any.
- `frontend/src/components/session/detail/SessionItem.vue`: the bottom margin becomes
  `calc(var(--depth-card-reach) + 1px)`: the `max()` only existed because the awesome
  shadow offset could exceed `--depth-card-reach`; in `default`, `--main-shadow-size`
  (`--wa-shadow-offset-y-s`, 0.125rem) is below it (0.1875rem). The comment keeps its
  first sentence (virtual-scroller cropping) and drops the `max()` sentence.
- `frontend/src/utils/panelInsets.js`: `FLUSH_TOLERANCE_PX` stays `6`; its comment becomes
  "the card border plus rounding, with margin" (no theme mention).
- Comments that refer to a user-chosen theme or accent, rewritten without it:
  - `frontend/src/styles/glass.css`: the comment on `--glass-tint` that mentions a gray
    accent (`wa-brand-gray`) → keep "derived by mixing palette steps, never by forcing a
    chroma on the accent hue", drop the gray-accent reason.
  - `frontend/src/utils/theme.js`: "Recomputed only when the color scheme, WA theme, or
    brand accent changes" → "Recomputed when the color scheme changes".
  - `frontend/src/components/app/SettingsPopover.vue`: the comment mentioning "the
    theme/brand selects" → rephrase for the remaining Color scheme select (or remove if it
    only described the removed selects).
  - `frontend/src/components/**/AgentSettingsMatrix.vue`: "whatever the score fill or the
    user's brand color" → "whatever the score fill".
  - `frontend/src/components/session/layout/SessionLayout.vue` (two comments): "the
    theme's divider thickness" → "the divider thickness"; and the comment "The visible line
    stays var(--divider-size), which varies with the theme; …" → "The visible line stays
    var(--divider-size); …" (drop "which varies with the theme").
  - "every theme" / "the themes'" wording: `ProjectBadge.vue` and `CommandPalette.vue`
    ("adapts to light/dark and every theme on its own" → "adapts to light/dark on its
    own"); `glass.css` ("beat the themes' @layer wa-theme declarations" → "beat the theme's
    @layer wa-theme declarations"; "The themes declare it in @layer wa-theme on :root and
    .wa-dark" → "The theme declares it…"); `depth.css` ("every theme re-declares
    --wa-shadow-* there" → "the theme re-declares --wa-shadow-* there"); `motion.css` ("The
    themes declare these tokens in @layer wa-theme…" → "The theme declares these
    tokens…"); `SessionLayout.vue` ("a line the thickness of the theme's divider" → "a line
    the thickness of the divider").
  - Rule for any other occurrence found while editing: a comment that implies several Web
    Awesome themes (plural "themes", "every theme", "the theme's divider thickness") is
    rewritten in the singular or without the word; a comment about the Web Awesome
    `default` theme itself (its tokens, its layers) stays.
  - `frontend/src/styles/motion.test.js`: the test 2 title "awesome buttons restated as
    0s" loses that part.
  - `frontend/src/composables/useTerminal.js`: "Switch theme live when the user toggles
    dark/light mode or WA theme" → "…when the user toggles dark/light mode".
  - `frontend/src/components/ui/SegmentedControl.vue`: "beats Web Awesome's :host(...)
    rules and the themes' layered ones" → drop "and the themes' layered ones".
  - Any other comment the §4.5 guard flags.

### 4.5 Tests

- `frontend/src/styles/motion.test.js`:
  - test 2: remove the assertion on the `:where(.wa-theme-awesome wa-button)` block;
  - the press-rule assertions: expect the single default-theme branch (no
    `:not(.wa-theme-awesome)`, no `.wa-theme-awesome` branch);
  - test 9 (snippets): drop the two `transform === 'none'` assertions and assert that no
    snippet rule declares `transform` (the list of rules with a `transform` is `[]`).
- `frontend/src/styles/glide.test.js`: remove the assertion on `box-shadow: none` /
  `transform: none` in `SegmentedControl.vue`.
- New guard, `frontend/src/styles/fixed-theme.test.js`: walks every file under
  `frontend/src` except `*.test.js`, and fails on any match of:
  `/\bshoelace\b/i`, `/(?<!web )awesome theme/i`, `/wa-theme-(?!default)/`,
  `/wa-palette-(?!default)/`, `/wa-brand-(?!cyan)/`, `dataset.theme`, `data-theme`,
  `THEME_TO_PALETTE`, `/watheme/i`, `/wabrand/i` (case-insensitive, so `getWaTheme`,
  `setWaBrand`, `onWaThemeChange`, `currentWaTheme`… are caught; the hyphen keeps
  `wa-theme-default` out), `WA_THEME`, `WA_BRAND`, `themes/awesome.css`,
  `themes/shoelace.css`.
  - **Negative controls** in the test: the patterns are run against these legitimate lines
    and must not match them — `depth.css (--depth-3) and the Web Awesome theme tokens.`
    (`glass.css`), `// CodeMirror search panel overrides (Web Awesome themed)`
    (`main.js`), `— Web Awesome themed overrides` (`codemirror-search.css`),
    `@awesome.me/webawesome`, `Font Awesome`, `wa-theme-default`, `wa-brand-cyan`.
  - It also asserts that `utils/theme.js` contains the three fixed classes.
  - Each pattern is proven with a mutant (re-inserting one occurrence turns it red),
    including one per casing of the camel-case identifiers (`getWaTheme`, `setWaBrand`).

### 4.6 Share viewer

- `frontend/src/share-session/theme.js`: stop setting `dataset.theme` (two places).
- `frontend/src/share-session/shims/settingsStoreShim.js`: remove `getWaTheme` and the
  `waTheme` / `waBrand` getters once nothing reads them (check with a grep; the guard of
  §4.5 covers it).

## 5. Backend

- `src/twicc/synced_settings.py`: remove `"waTheme"` and `"waBrand"` from
  `_GENERIC_SYNCED_SETTINGS_DEFAULTS`; add them to `_GENERIC_OBSOLETE_SYNCED_SETTINGS_KEYS`
  with a comment ("theme and accent are fixed since the visual refresh"). The existing
  migration drops them from `settings.json` on first read and persists the file.
- **Write-back guard:** today the mutation service stores any key the client sends
  (`src/twicc/core/services/settings_mutation.py`, `existing_settings.update(normalized_patch)`)
  and broadcasts the raw `patch` to every client (`{**patch, **result["corrections"]}`), so
  a tab opened before the upgrade could write the keys back and relay them. Expose the
  aggregated obsolete-key set from `synced_settings.py` (one helper used by both
  `_migrate_legacy_settings` and the mutation service), and at the **top of
  `update_synced_settings()`**, before `_merge_and_write` and the broadcast, rebuild
  `patch` without any obsolete key. Silently: no error, no correction entry. A patch that
  held only obsolete keys goes through as an empty patch (normal flow).
- `src/twicc/cli/settings/_keys.py`: remove `waTheme`, `waBrand` from `EXCLUDED_KEYS`;
  update the docstring of `src/twicc/cli/settings/command.py`. They become unknown keys
  for the CLI (whatever the classifier already returns for an unknown key).
- Tests:
  - `tests/test_settings_cli.py`: lines 35 and 76 (`waTheme` next to an existing
    `defaultLayoutId` assertion) are deleted; line 132 (`_validate_settable_key`) and the
    `info` `build()` test (lines 743–750) switch to `defaultLayoutId` (still excluded, in
    `SYNCED_SETTINGS_DEFAULTS`); add one assertion that `classify_key("waTheme")` is now
    `"unknown"`.
  - `tests/test_changelog_versions.py:74`: replace the stand-in `{"waTheme": "default"}`
    by a neutral key.
  - New tests:
    - `tests/test_public_origin.py` (the file that already drives
      `_migrate_legacy_settings`): a settings dict holding `waTheme` / `waBrand` loses
      them and the migration reports a change.
    - `tests/test_settings_mutation.py`: a patch `{"waTheme": "awesome", <a valid key>:
      <value>}` is accepted; the valid key is stored, `waTheme` is not in the stored file,
      and the broadcast payload has no `waTheme`. That file's helper calls the service
      with `broadcast=False`, so this test calls `update_synced_settings(...,
      broadcast=True)` with `settings_mutation._apply_transitions_and_broadcast`
      monkeypatched to record the `patch` it receives, and asserts on that recorded
      `patch`.

## 6. Docs

- `README.md`: remove "several visual themes with a customizable accent color" (rephrase
  the sentence around it; keep the light/dark mention if present).
- `SKILLS-AND-CLI.md`: remove `waTheme`, `waBrand` from the excluded keys list.
- `docs/plans/2026-09-26-visual-refresh-roadmap.md`: add an intermediate row to the §1
  status table, before step 5 ("Fixed theme `default` + accent `cyan`", with this spec and
  the commit once made); record the decision in §4 (fixed
  theme `default`, fixed accent `cyan`); remove the "possible removal of the theme and
  accent-color choices (undecided)" line of §7; in §2, the constraint "the user may later
  remove the theme choice and the accent-color choice" is now done, and "a user-chosen
  accent color, must both keep working" no longer applies (rephrase: light and dark must
  keep working). Every lesson that exists only because of the awesome/shoelace themes is
  marked obsolete (the theme no longer exists; a later step must not re-add the code):
  - §6b.2 (step 2): keeping the awesome theme's hard controls by excluding
    `.wa-theme-awesome`;
  - §6c.2 (step 3): "Theme widths vary: `--wa-border-width-s` is 2px in the awesome
    theme";
  - §6d.2 (step 4a): the unitless `0` transition tokens of the awesome theme, and the
    snippet chips keeping `transform: none`;
  - §6f.2 (step 4c): "an unlayered override must reset `box-shadow` and `transform`" (the
    awesome theme's button radios).
- No agent skill changes: `twicc-info/settings.md` describes the `excluded` owner in
  general terms only (no plugin version bump).

## 7. Invariants

1. The three classes are on `<html>` from the static HTML to the end of the session;
   nothing removes or replaces them.
2. No stored value can bring back another theme or accent (frontend boot, store sync,
   backend read, backend write).
3. The color scheme behaves exactly as before.
4. The `default`-theme rendering is unchanged (only dead branches are removed).

## 8. Browser checks (http://localhost:5174, Firefox first)

1. Settings → General: no Theme, no Accent color; Color scheme works (light, dark, auto).
2. Command palette: no "Change Theme…" / "Change Brand Color…"; "Color scheme" works.
3. Put `"waTheme":"awesome","waBrand":"red"` in `localStorage['twicc-settings']`, reload:
   cyan + default at first paint, and the two keys are gone from `localStorage`.
4. Buttons (raised, accent), text fields, the composer, the split-panel dividers, the
   segmented controls: identical to before in light and dark.
5. A share link and an artifact page: unchanged. Their bundles are not hot-reloaded:
   run `cd frontend && npm run build` before this check.

## 9. Delivery

One commit after the user's browser review and explicit "commit":
`refactor(ui): fix the theme to default and the accent to cyan`. The CHANGELOG entry is
written at the end of the branch, with the rest of the redesign.
