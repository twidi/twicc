# Interface translation state

This file records the last complete translation pass. Its Git history is the
baseline for the next pass.

**Invariant:** the last commit that changes this file represents a complete,
validated pass. Change this file only when a pass is complete, in the same
commit as the translations. Never commit partial progress here.

Find the baseline with:

```bash
baseline=$(git log -1 --follow --format=%H -- docs/i18n-state.md)
```

Do not move or rename this file. If the command prints nothing, run a full
initial pass. Do not substitute another commit.

A merge of `main` keeps the baseline. The baseline diff includes what `main`
added.

A rebase does not. Git replays the state-file commit after the new commits of
`main`, so the new hash hides what `main` added. After a rebase, take the
baseline from the old branch tip. Do it right after the rebase, before another
Git operation overwrites `ORIG_HEAD`:

```bash
baseline=$(git log -1 --follow --format=%H ORIG_HEAD -- docs/i18n-state.md)
```

Save the baseline before you rebase again. If you did not commit the pass
between two rebases, `ORIG_HEAD` of the second rebase no longer gives it:

```bash
git tag -f i18n-baseline "$baseline"
```

After a second rebase, keep the saved value (`baseline=$(git rev-parse i18n-baseline)`).
Do not recompute it from `ORIG_HEAD`. Delete the tag with
`git tag -d i18n-baseline` once the pass commit exists. The tag also protects
the old commit from garbage collection. If you committed the pass between the
rebases, the `ORIG_HEAD` form is correct again.

A state-file commit claims that everything up to it is verified. Never rebase
over unreviewed changes of `main` and then trust the new hash. Review them
first, or use the pre-rebase baseline.

See [Internationalization](internationalization.md) for the rules that the
translations follow.

## Scope

A pass covers:

- `frontend/` application sources: Vue, JavaScript, TypeScript, HTML and CSS, standalone entry points, display metadata and the catalogs in `frontend/src/i18n/locales/`.
- `frontend/vite.config.js`, because it injects the startup recovery messages into `frontend/index.html`.
- `src/twicc/` Python code that implements or supports browser-facing behavior. Shared dependencies under CLI or MCP directories are included.
- `android/` transport dependencies that carry browser messages. This is not a full native Android interface review.
- Help and tip English sources in `frontend/public/help/` and `frontend/public/tips/`, with their translated bodies under the locale directories.
- `docs/i18n-glossary.md`.

Help and tip interface components and metadata stay in scope. Their controls
and titles are translated. The catalogs hold no Markdown bodies.

### Deliberate exclusions

An excluded file is not a missing translation. Apply these rules to existing
and new files in every pass.

| File type | Recognition rule or example | Reason |
| --- | --- | --- |
| Images and video | PNG, ICO, SVG, WebP, MP4; icons, screenshots and release media | Media is outside the pass. |
| Help/tip authoring guides | `frontend/public/help/README.md`, `frontend/public/tips/README.md` and equivalent contributor guides | Contributor documentation stays English. The Markdown content itself is in scope. |
| Translated content entries | Locale subdirectories such as `frontend/public/help/pt-BR/` and `frontend/public/tips/pt-BR/` | Review them as dependencies of their English source. |
| Static public assets | Public media and `frontend/public/site.webmanifest` | Asset metadata has no interface messages. |
| Tests and harnesses | `*.test.*`, `*.spec.*`, `frontend/tests/`, `__tests__/` | Test text is not production interface copy. |
| Test fixtures | `__fixtures__/`, fixture-only data such as `GitLog/__tests__/fakeData.ts` | Synthetic data is not interface copy. |
| Benchmark data | `frontend/src/data/modelBenchmarks.json` and equivalent datasets | Values, model identifiers and reference URLs are not interface prose. |
| Package metadata | `package.json`, `package-lock.json` and equivalent manifests and lockfiles | Package metadata supplies no interface messages. |
| Tooling and build configuration | TypeScript/ESLint configuration and Vite bundle configuration without interface messages | These files configure development or bundling. |
| Backend-only outputs | Console logs, terminal output, CLI/MCP replies, prompts and provider transcripts | Keep English prose and original content. |
| CLI/MCP-only sources | Command wrappers and protocol-only modules without a browser consumer | Exclude by role. Shared browser dependencies stay in scope. |
| Backend tests and migrations | `tests/`, test settings, migration files and test fixtures | Verify them separately. |
| Deliberately English pages | Public sharing; standalone share and artifact password forms; unavailable-share page; `/mcp/oauth/wait` | No translation is required. Sources and boundaries: [Pages that deliberately stay English](internationalization.md#pages-that-deliberately-stay-english). |
| Pages outside the application | `telemetry-collector/public/index.html` and standard Django error pages | Outside the translation scope. |
| Generated files and dependencies | Build output, caches and `node_modules/` | Review application sources instead. |

Classification rules:

- Classify by role, not by extension or directory.
- Do not exclude runtime display dictionaries as fixtures or benchmark data.
- Do not exclude application code because it contains no interface text today.
- Public executable code or configuration that supplies interface messages is in scope.
- The explicit English-page exclusions take precedence over the rule that public executable code supplying interface messages is in scope.
- Shared modules stay in scope when they also supply application interface messages.
- MCP approval, telemetry dialogs and settings, and sharing controls inside TwiCC stay in scope.
- Translations in public Vue viewers do not create a requirement to localize public sharing. See [Public viewers](internationalization.md#public-viewers).
- Discovered command descriptions and their default labels stay verbatim. Review their source files normally. See [Backend interface messages](internationalization.md#backend-interface-messages).
- If a file changes role, reassess its exclusion.
- Inspect an unknown file type before you classify it.
- Never add an excluded file only because it is absent from a listing.

## Last complete pass

Pass: 2
Date: 2026-11-08
Languages: fr, es, de, pt-BR

Each pass changes these three lines. The change gives the pass its own commit,
even when no other file in the pass differs. The history of this file lists
the earlier passes: run `git log --follow -- docs/i18n-state.md`.

## How to run a pass

1. Set `baseline` with the first command above. After a rebase, use the `ORIG_HEAD` form or the saved tag instead.
2. List the files that changed since `$baseline`: committed, staged and unstaged.
   Add renames and deletions. Add untracked files, which the diff cannot show.
   Limit the lists to `frontend/`, `src/twicc/`, `android/` and `docs/i18n-glossary.md`.
   Also list any other new file with browser-facing text, even at the repo root or outside these paths:

   ```bash
   git diff --name-status --find-renames "$baseline" -- frontend/ src/twicc/ android/ docs/i18n-glossary.md
   git ls-files --others --exclude-standard -- frontend/ src/twicc/ android/ docs/i18n-glossary.md
   git status --short   # new files outside these paths
   ```

   On a first pass `baseline` is empty and `git diff` fails. List the in-scope files instead:
   `git ls-files -- frontend/ src/twicc/ android/ docs/i18n-glossary.md`.

3. Apply the scope and exclusion rules to the list.
4. Find the changed English strings with `git diff "$baseline" -- frontend/src/i18n/locales/en.json`.
5. Reopen help and tip content:
   - English source changed: reopen all its translations.
   - Translation changed: review it against the English source.
   - New source: add the translations for every supported language.
   - Renamed or deleted source: reconcile translations and `help/<id>` links.
6. Glossary diff: run `git diff "$baseline" -- docs/i18n-glossary.md` and reopen the affected languages.
7. Review the full production file for each in-scope change, not only its diff.
   Follow every display path, and every backend producer of interface text.
8. Keep the working list in a temporary scratch file. Do not commit it.
9. Request one independent review per language.
10. Run the checks from [Checks](internationalization.md#checks). When the UI changed, also run the browser checks from step 11 of [Adding a language](internationalization.md#adding-a-language).
11. Increase `Pass`, set `Date` and `Languages` in [Last complete pass](#last-complete-pass). Commit the file with the translations.
