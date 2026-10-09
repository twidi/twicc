---
name: twicc-artifact-authoring
description: How to create artifacts TwiCC renders — inline images, Artifacts-tab pages and documents, inline HTML in the chat — with light/dark support, network, and saved data. Load before writing any artifact.
---

# TwiCC Artifact Authoring

An artifact is a file you write in your session's artifacts directory for TwiCC to render: an image in your reply, a document or page in the Artifacts tab, or an interactive HTML page inline in the conversation. This skill is the full authoring contract.

## When to use

- You are about to write an artifact file or publish an inline artifact tag.
- You or the user want a visual or interactive result: chart, dashboard, playground, demo, mockup, data table, formatted report.
- You want the user to make choices in a rich form that you read back later.

Use an artifact only when rendering or interactivity genuinely helps, instead of a wall of code or text. When a short answer or a Markdown reply does the job, just answer. A Mermaid diagram renders natively in your reply: keep it there, do not build an HTML page.

## The artifacts directory

- Your directory is `{artifacts_base_dir}/{session_id}/`, already created, outside the repo. Both values are in the `Context` block of your system prompt, or in a `<twicc:context>` block with the first user message; else `twicc-whoami` returns it as `artifacts_dir`.
- `{session_id}` in the URLs below is the same full session id.

## Inline images

Write the image to the directory's **top level** and reference it `![label](/artifacts/{session_id}/<file>)`: TwiCC serves it inline in the conversation.

- Images only: `png`, `jpg`, `jpeg`, `webp`, `gif`, `svg`.
- One flat filename, **no subdirectories**, ASCII starting with an alphanumeric or `_`. Anything else returns 404.
- A `YYYY-MM-DD-HH-MM-SS-` prefix is good practice.

## The Artifacts tab

The tab browses the directory as a tree and **renders** images (incl. SVG), PDFs, audio/video, Markdown, Mermaid (`.mmd`), and HTML (sandboxed iframe).

- A page with assets, or one that writes `data/`, MUST live in its **own subfolder** with an `index.html` entry point. That folder is the unit TwiCC bookmarks and shares, and the only thing that keeps the page's assets and `data/` separate from every other artifact. A page left at the top level shares one `data/` with every other top-level page. A single self-contained file may stay at the top level.
- Reference assets and other files with **relative** paths (they load); root-absolute paths (`/style.css`) do NOT. Scripts execute (sandboxed, same origin).
- Give the user a **clickable Markdown link**, e.g. `[Open the demo](/artifacts/{session_id}/demo/index.html)`: TwiCC intercepts it and opens the rendered page in the tab. It is an in-app link (for HTML and subfolder files), not a URL to paste in a browser. Same for a Markdown document (`report.md`).

## HTML pages: light and dark

This applies to **every** HTML artifact, in the tab and inline.

- Inside TwiCC, `prefers-color-scheme` follows the app's current scheme, live — not the operating system's.
- Declare `:root { color-scheme: light dark; }`. Without it, native controls (inputs, selects, scrollbars) stay light, and the browser paints an opaque white background behind the page in dark mode.
- Style both modes with `light-dark()` or `@media (prefers-color-scheme: dark)`. In JS, read `matchMedia('(prefers-color-scheme: dark)')` and listen to its `change` event (charts, canvas).
- Set explicit background and text colors for each mode. Inline pages keep a transparent background in the chat: see *Inline HTML artifacts*.

## HTML pages: network

Call `fetch` / `XMLHttpRequest` normally. Requests run server-side, so there is no browser CORS. The first contact with a host prompts the user to approve it.

## HTML pages: saved data

An HTML artifact can save files under its own `data/` subfolder: `await window.twicc.data.set('config.json', obj)`, `.get`, `.list()`, `.remove` — or plain `fetch('data/x.json', {method: 'PUT', body})`.

- Use it to let the user make choices you read back later: seed `<artifact dir>/data/config.json` yourself, have the page load it, edit it, save it — then Read the file.
- This turns an artifact into a rich form for decisions, settings, selections on ANY topic: hand the user the link, ask them to tell you when they are done, then read their choices from `data/`.
- Writes are silent, confined to `data/`, and capped (10 MB per file, 100 MB total). The user sees the files in the Artifacts tab.

## Inline HTML artifacts

Publish an interactive HTML page directly in a regular session's conversation when this helps the user.

Only the main session agent creates and publishes inline artifacts. Do not delegate inline artifact generation or publication to native subagents. If you are a native subagent, do not create or publish inline artifacts. Native subagent tags remain ordinary text. A separately spawned regular TwiCC session owns its own artifacts; it cannot publish into another session.

### Files and IDs

- Write files first under `{artifacts_base_dir}/{session_id}/inline-artifacts/<id>/`. Every inline page needs its own folder, including a single self-contained file. The entry is a direct child of that folder, with `.html` or `.htm` extension. Use relative asset paths. The Artifacts tab also exposes this folder.
- IDs match `[a-z][a-z0-9_-]{0,63}` and identify the folder in this session. Use distinct IDs for independent widgets.
- Use the same ID and folder for corrections, then insert the tag again. The latest valid tag replaces the previous placement. File edits alone do not reload an inline page. Its Reload control reloads the current files.

### The publication tag

Insert the tag in a finalized assistant reply as a standalone top-level Markdown block. Use double-quoted attributes. Do not put the publication tag in a code fence, list, blockquote, tool result, reasoning block, or HTML comment. The fenced examples below show syntax; remove the fences when publishing.

Single self-contained page: write `inline-artifacts/calculator/calculator.html`, then publish:

```text
<twicc:inline-artifact id="calculator" src="inline-artifacts/calculator/calculator.html" />
```

Page with assets: write `inline-artifacts/preferences/index.html`, `inline-artifacts/preferences/app.js`, and `inline-artifacts/preferences/style.css`. Then publish:

```text
<twicc:inline-artifact
  id="preferences"
  src="inline-artifacts/preferences/index.html"
  title="Preferences"
  height="360"
/>
```

- `src` is relative to this session's artifacts root and must match the ID folder. Do not use URLs, absolute paths, traversal, backslashes, query strings, or fragments.
- `title` is optional plain text, at most 200 characters; its default is the ID.
- `height` is optional, defaults to 360, and stays within 160–900 CSS pixels. Automatic height also stays within these limits.
- Controls are Full screen and Reload.

### Blending into the chat

Support light and dark (see *HTML pages: light and dark*), and keep `html` and `body` transparent while the root carries `data-twicc-display="inline"`: the conversation shows through. The attribute switches to `"fullscreen"` in Full screen and is absent elsewhere (Artifacts tab, browser tab): paint your own background there.

```css
:root { color-scheme: light dark; background: light-dark(#fff, #1b1c1f); }
:root[data-twicc-display="inline"] { background: transparent; }
```

### Memory and saved data

- Input values and page state stay in iframe memory during scrolling, cached session switches, and dock changes. Memory is lost on refresh, Reload, code correction, cache eviction, or view teardown.
- Opening the same page in the Artifacts tab uses independent iframe memory. Both views access the same files and optional saved data.
- Use window.twicc.data only when saved data serves the widget. For example, `await window.twicc.data.set('config.json', choices)` writes `inline-artifacts/<id>/data/config.json`; `.get`, `.list()`, and `.remove` also work. Saved data is separate from iframe memory and survives a page reload.
- Do not save every interface change automatically.
- Do not add a Submit to discussion control.

## Related commands

- `$TWICC artifacts bookmark <SESSION_ID|self|parent> <PATH>` — bookmark an artifact so the user finds it in the sidebar. Skill: `twicc-artifacts`.
- `$TWICC share create artifact <BOOKMARK_ID>` — public read-only link to a bookmarked artifact. Skill: `twicc-share`.
