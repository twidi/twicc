# Tips authoring guide

Each `.md` file in this folder is a tip the app can show in a toast.
This README documents how to add one.

The tip scanner (`src/twicc/tips_manifest.py`) silently ignores any file
whose name starts with an uppercase letter (this README, `LICENSE.md`,
etc.), so they live happily alongside the actual tips.

> In dev, adding, renaming or removing a tip file is picked up
> automatically: the backend re-scans this folder every 10 s and pushes
> the new manifest over WebSocket. In production the manifest is built
> once at startup, so a change there needs a **backend restart**.

## File naming

Create a file named `<key>.md` where `<key>` is **lowercase**, made of
letters, digits, and dashes only:

    ^[a-z0-9-]+$

The key is the stable identifier used in `seen-tips.json` and in the
manifest — keep it stable once published, since renaming it resets the
seen-state for all users on the next sync.

## Anatomy of a tip

```markdown
---
title: "Short, plain-English title shown in the toast header"
platform: [desktop]
os: [mac, linux, windows]
providers_any: [claude_code]
---

Tip body in **markdown**. Bold, italic, code blocks, lists, blockquotes,
images, tables — anything the existing chat-message markdown pipeline
supports.
```

## Line wrapping

Wrap the body however reads best in your editor — tips render with standard
Markdown paragraph rules:

- A **single line break** inside a paragraph is collapsed to a space, so you
  can hard-wrap long sentences across several source lines without it showing
  up as a line break in the toast.
- A **blank line** starts a new paragraph.
- Need a forced line break inside a paragraph anyway? End the line with **two
  trailing spaces** or a backslash (`\`).

(Tips opt into this via `renderMarkdown(..., { softBreakAsSpace: true })`; the
chat pipeline keeps newlines as `<br>`.)

## Front-matter

| Key | Required | Allowed values | Default when absent |
|---|---|---|---|
| `title` | **yes** | non-empty string | — (tip excluded with a warning) |
| `platform` | no | `mobile`, `desktop` | both platforms |
| `os` | no | `mac`, `linux`, `windows` | all OSes |
| `providers_any` | no | provider keys (`claude_code`, `codex`, …) | no provider requirement |
| `providers_all` | no | provider keys | no provider requirement |

### Rules

- **Absent key = no constraint on that dimension.** Just leave the key
  out entirely; do **not** write `platform: []` to mean "any".
- **Present key = always an array**, even for a single value:
  `platform: [desktop]`, not `platform: desktop`.
- `providers_any: [...]` — tip is shown if **at least one** of the
  listed providers is enabled.
- `providers_all: [...]` — tip is shown only if **all** the listed
  providers are enabled.
- An **empty array** (`platform: []`) means "no value satisfies", so the
  tip is parsed but never available. Handy to **temporarily disable** a
  tip without renaming or removing it.
- If `title` is missing or any constraint is malformed (wrong type,
  unknown value), the tip is silently excluded with a warning in the
  backend log; the other tips keep working.

## Images and links

Drop image files directly in this folder, then reference them from
inside the markdown. The tip renderer rewrites three natural shapes to
the runtime base URL — pick whichever reads best to you:

```markdown
![alt](/tips/screenshot.png)
![alt](./screenshot.png)
![alt](screenshot.png)
```

External URLs (`https://…`, `data:…`, `mailto:…`, etc.) and other
absolute paths are passed through untouched. The same rewrite applies
to `<a href>` links to local files inside the tips folder.

A practical naming convention is `<tip-key>-<n>.<ext>` (e.g.
`drag-files-to-attach-1.webp`) — it keeps asset files grouped near
their tip in directory listings. It's just a convention; the runtime
does not look at filenames.

> HTML is **not** allowed in tip bodies (`html: false` in markdown-it).
> DOMPurify also sanitizes the rendered output. Stick to markdown.

### Linking to a help page

A markdown link whose target is exactly `help/<key>` is intercepted and
opens the matching **help page** (see `frontend/public/help/`) in its
dialog, instead of navigating:

```markdown
New here? See [what artifacts are](help/what-are-artifacts).
```

Use it to point a tip at a longer explanation that lives as a help page.
The `<key>` is the help file's name without `.md`.

## When does a tip appear?

Three constants in `frontend/src/composables/useTipScheduler.js` control
the cadence:

| Constant | Default | Meaning |
|---|---|---|
| `FIRST_TIP_DELAY_MS` | 60 s | Delay before the first tip can appear after the app mounts. |
| `TIP_COOLDOWN_MS` | 2 h | Delay after a tip is **dismissed** before the next one can appear. |
| `SCHEDULER_POLL_MS` | 1 min | How often the scheduler wakes up to check whether it can show a tip. |

The scheduler skips its tick if:

- The user disabled tips from Settings.
- A tip is already showing.
- The cooldown has not elapsed (`Date.now() < nextEligibleTime`).
- Any modal / popover / dropdown is open (`hasBlockingOverlay`).
- The browser tab is hidden.
- **Another toast is up.** App toasts (errors, session events, …) take
  priority — a tip waiting in the wings yields, and a tip already on
  screen is auto-discarded when an app toast appears (without being
  marked seen, so it can come back later).

## Seen state

Dismissing a tip (Close, Escape, ×, or "Next tip") marks it seen: it
won't reappear automatically. The user re-opens it on demand from the
Settings → Tips list, or puts every tip back in the rotation with
"Reset all seen tips". Seen state is synced across devices via
`seen-tips.json` in the TwiCC data directory.

Two ways a tip is **not** marked seen:

1. The tip was auto-discarded by an arriving app toast.
2. The browser tab was closed while a tip was on screen.

## Listing in Settings → Tips

The Settings → Tips panel shows only tips whose constraints are
satisfied by the current environment (platform, OS, enabled providers).
A tip filtered out by constraints is simply absent from the list —
there's no "unavailable" greyed entry. Clicking a tip in this list
opens it in the same toast as the automatic flow.

## Checklist before committing a new tip

- [ ] Filename is `<lowercase-key>.md` matching `^[a-z0-9-]+$`.
- [ ] `title:` is set, short, plain English.
- [ ] Any present constraint is an array (even with a single value).
- [ ] Images referenced from the markdown actually exist in this folder.
- [ ] The body reads well at the toast's roughly 480 px width.
- [ ] The tip is not too long — `max-height: 60vh` scrolls but a giant
      tip is unfriendly. Aim for a few short paragraphs.
- [ ] The tip shows up in Settings → Tips (in dev, within 10 s).
