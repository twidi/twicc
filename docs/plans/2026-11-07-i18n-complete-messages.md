# Complete existing i18n messages

## Scope

Replace the existing translated sentence fragments with complete messages in
the i18n integration worktree. Keep English and Brazilian Portuguese aligned.
Do not merge into main. Full frontend coverage, backend text and help content
remain subsequent phases of the i18n integration.

## Implementation

1. Inspect each fragment and its callers. Include literal suffixes attached to
   translated text, session counts and the composer shortcut placeholder.
2. Use named interpolation for values, and global-scope `i18n-t` slots for
   links, icons, code and emphasized text. Keep punctuation in the messages.
3. Give the composer complete keyboard/touch messages, with and without
   start-of-message shortcuts. Format alternative keys with `Intl.ListFormat`.
   Select the provider-specific working message before translation.
4. Replace the card session counts with complete plural messages. Preserve
   route names, setting values, provider behavior and trust decisions.
5. Remove unused fragments from both catalogs. Document the message convention.

## Validation

- Verify zero, one and multiple session counts in both locales.
- Verify composer variants, provider notes, localized conjunctions and locale changes.
- Render the actual rich-message templates with their slots. Check escaping,
  slot reordering and global scope without Vue I18n warnings.
- Run the frontend suite and production build.
- Ask the existing Claude browser session to inspect the changed surfaces.
  Use the existing fixture data. Do not send messages, open Rename, or change trust.

## Results

- Removed 26 fragment keys and added 23 complete-message keys in each locale.
  Updated five existing messages. Both catalogs now contain 427 keys.
  No source references to the removed keys remain.
- Targeted i18n tests: 18 pass. Full frontend suite: 4,571 pass, zero failures.
  Updated the existing OpenRouter source assertion to expect interpolation.
- Production build: all six bundles pass. The existing large-chunk warning remains.
- Claude checks Chrome at `http://localhost:5174` in English and Brazilian
  Portuguese. Reachable hints, icons, links, model labels, footer, directory
  tooltips and composer shortcuts pass. The Notifications link still navigates.
  Session and Titles layouts have no horizontal overflow at 455px.
  No raw keys or i18n warnings appear. Locale returns to English; dialogs and
  drafts are closed. No message is sent and trust stays unchanged.
- Browser limits: no workspace card exists; only a zero-session project card
  is available. Inherited trust, the global-directory button, update notice,
  process memory and working-state provider note are unavailable in the fixtures.
  Automated tests cover session counts 0/1/2/21, inherited trust in both states
  with/without a source, escaped source names, rich-slot placement, and composer
  draft/touch/keyboard/starting/working variants and locale changes.

## Next i18n phases

The integration remains unfinished. Inventory and translate the remaining
frontend strings, including observed English URL controls, sidebar group labels,
Notifications content, provider availability reasons and operational status.
Audit backend-generated display text, then long help and Tips content. Finish
the cross-surface initialization, fallback, persistence and formatting review
before considering integration into main.
