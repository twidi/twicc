# Android translation fingerprint scope

## Decision

Keep the current committed native catalog and Node-free Gradle verification. Replace whole-locale JSON hashes with `path#message.key` hashes. Hash only the decoded UTF-8 string for messages selected in `android/i18n/messages.json`. Continue hashing complete generator code, selection manifest, dependencies lockfile, and generated outputs.

Both the JavaScript checker and Gradle validate the same format. Cache parsed locale files within each verification run. Web-only changes and JSON formatting do not invalidate Android. Changed, missing, or non-string native messages still fail.

## Validation

Regression tests cover web additions/edits, formatting, ordering, native changes/removals/type changes, and generated file changes. Regenerate with the existing dependency installation from another checkout whose lockfile is identical, copied into session scratch space. Do not install packages or change the main checkout's empty node_modules directory.

## Results

- The browser test adapters previously bundled all web catalogs. The fixture generator now filters locale JSON to the native selection as well. This keeps deterministic generation independent of web-only messages.
- All 18 focused JavaScript tests pass in the isolated copy. The main checkout's dependency-free source checker and its 9 tests pass.
- Full generation `--check` and the actual Gradle verification task both accept a web-only addition and JSON reformatting. Both reject a changed native message. These mutations run only in the isolated copy and are restored afterward.
- The main checkout's Gradle i18n verification and both 109-test engine unit suites pass without task exclusions.
- Committed native catalogs and render fixtures remain unchanged. Browser test adapters shrink because they no longer embed unrelated web catalogs.
- No packages are installed. The main node_modules remains unchanged. The copied dependency installation uses an identical package-lock.json.
