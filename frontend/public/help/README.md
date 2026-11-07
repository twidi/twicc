# Help authoring guide

Each `.md` file in this folder is a **help page** the app can show in a
modal dialog. This README documents how to add one. It is the sibling of
`frontend/public/tips/README.md`; the **markdown, line-wrapping, images,
and platform/os/provider constraints all work exactly the same way** —
this guide focuses on what differs from tips.

The help scanner (`src/twicc/help_manifest.py`) silently ignores any file
whose name starts with an uppercase letter (this README, `LICENSE.md`,
etc.), so they live happily alongside the actual pages.

## Tips vs. Help — the differences

| | Tips | Help |
|---|---|---|
| Surface | Toast (auto-rotating) | **Modal dialog** |
| What triggers it | A background scheduler picks a random unseen tip | **Explicit code events** (e.g. first tab dock) and **manual** opens (a button, the Settings list, a cross-link) |
| Seen-state file | `seen-tips.json` | `seen-help.json` |
| "Don't show again" switch | always shown | decided per call site (shown by default) |
| Settings panel | Settings → Tips | Settings → Help |

There is **no scheduler** for help. A help page only appears when some
code path calls for it — so adding a page does nothing on its own until a
trigger references its key (see "Wiring a trigger" below).

## Translations

Keep the English source at `help/<key>.md`. Add each translated body at
`help/<locale>/<key>.md`, for example `help/pt-BR/worktrees.md`.
Translated files contain the body only, without YAML front matter.
Keep constraints in the English source and translated titles in the interface catalogs.
The backend scans English files only; the frontend selects the body by locale with English fallback.

Preserve code, URLs and `help/<key>` targets. Translate prose, link labels and image alt text.
Image paths remain relative to the shared `help/` root, not the locale directory.
Use `docs/i18n-checklist.md` for review status: one entry per English source covers
all translations. Git changes to either source or translation reopen that entry.
Do not add source fingerprints or separate checklist entries for translations.

## File naming

Create a file named `<key>.md` where `<key>` is **lowercase**, made of
letters, digits, and dashes only:

    ^[a-z0-9-]+$

The key is the stable identifier used in `seen-help.json`, in the
manifest, and in any `help/<key>` cross-link. Keep it stable once
published — renaming it resets the seen-state and breaks existing links.

## Anatomy of a help page

```markdown
---
title: "Short title shown in the dialog header"
platform: [desktop]
os: [mac, linux, windows]
providers_any: [claude_code]
---

Body in **markdown**. Same pipeline as tips and chat messages: bold,
italic, code blocks, lists, blockquotes, images, tables.
```

## Front-matter

| Key | Required | Allowed values | Default when absent |
|---|---|---|---|
| `title` | **yes** | non-empty string | — (page excluded with a warning) |
| `platform` | no | `mobile`, `desktop` | both platforms |
| `os` | no | `mac`, `linux`, `windows` | all OSes |
| `providers_any` | no | provider keys (`claude_code`, `codex`, …) | no provider requirement |
| `providers_all` | no | provider keys | no provider requirement |

The `platform` / `os` / `providers_*` rules are **identical to tips** — see
the tips README for the full semantics (absent = no constraint, present =
always an array, empty array = never available, etc.).

## The "Don't show this again" switch

There is **no front-matter flag** for this. Whether the switch appears is
decided **at the call site**, and it is **shown by default**:

- **`showHelp('<key>')`** — opens with the switch.
- **`showHelp('<key>', { showDontShowAgain: false })`** — opens without it.
  Use this for on-demand reference pages that shouldn't be dismissible
  (e.g. "What are artifacts?"), and from the Settings → Help list (where
  re-reading a page must not change its seen-state).
- **`helpStore.maybeAutoShow('<key>', env)`** — automatic opens always show
  the switch.

When the switch is shown, closing the dialog commits the seen-state:
leaving it **on** (the default) marks the page seen so it won't auto-open
again; turning it **off** keeps it in rotation. When the switch is hidden,
the seen-state is left untouched.

## Images

Identical to tips: drop image files in this folder and reference them with
any of `![alt](/help/foo.png)`, `![alt](./foo.png)`, or `![alt](foo.png)`.
The help renderer rewrites them to the runtime base URL. External and
absolute URLs are passed through untouched.

> HTML is **not** allowed in help bodies (`html: false` in markdown-it),
> and DOMPurify sanitizes the rendered output. Stick to markdown.

## Cross-links between pages

A markdown link whose target is exactly `help/<key>` opens that help page
instead of navigating:

```markdown
See also [arranging your layout](help/layout-docks).
```

This works **from a help page and from a tip** — so a tip can point at a
help page, and help pages can link to one another. Clicking swaps the
dialog content in place (or opens the dialog when clicked from a tip).

## Wiring a trigger

A help page needs a call site to ever appear. Two kinds:

- **Manual** — call `showHelp('<key>')`
  (`frontend/src/components/help/showHelp.js`) from a button or menu, or
  `showHelp('<key>', { showDontShowAgain: false })` to hide the switch.
  Example: `ArtifactsHelpButton.vue`.
- **Automatic** — call `helpStore.maybeAutoShow('<key>', env)` from the
  code path that represents "the user just reached this feature". It
  no-ops unless the env matches the page's constraints, nothing else is
  open, and the page is not yet seen. Example: the first manual tab dock in
  `composables/useSessionLayout.js` opens `layout-docks`.

## Listing in Settings → Help

The Settings → Help panel lists every page whose constraints match the
current environment. Clicking one opens it in the same dialog without the
switch (re-reading must not change its seen-state). There is intentionally
no enable/disable toggle and no reset button — help is a light automatic
nudge, not something to configure.

## Hot-reload

Like tips, in dev (`TWICC_DEBUG`) a background watcher re-scans this folder
every ~10 s and re-broadcasts the manifest, so adding / renaming / removing
a page does not need a backend restart. Production builds freeze the
manifest at boot.

## Checklist before committing a new help page

- [ ] Filename is `<lowercase-key>.md` matching `^[a-z0-9-]+$`.
- [ ] `title:` is set, short, plain English.
- [ ] The call site passes `showDontShowAgain` intentionally (default shown; pass `false` for on-demand reference pages).
- [ ] Any present constraint is an array (even with a single value).
- [ ] Images referenced from the markdown actually exist in this folder.
- [ ] There is a call site (`showHelp` or `maybeAutoShow`) — or the page is
      only reachable from the Settings list / a cross-link on purpose.
- [ ] The body reads well at the dialog's ~560 px width.
