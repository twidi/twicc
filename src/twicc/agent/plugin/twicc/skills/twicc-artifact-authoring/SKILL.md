---
name: twicc-artifact-authoring
description: Load before writing any artifact TwiCC renders: an image in your reply, a file in the Artifacts tab (image, PDF, audio/video, Markdown, Mermaid, HTML page), or an HTML page inline in the chat.
---

# TwiCC Artifact Authoring

An artifact is a file you write in your session's artifacts directory for TwiCC to render: an image in your reply, a document or page in the Artifacts tab, or an interactive HTML page inline in the conversation. This file is the index: the common rules, then one file per kind of HTML artifact.

## When to use

- You are about to write an artifact file or publish an inline artifact tag.
- You or the user want a visual or interactive result: chart, dashboard, playground, demo, mockup, data table, formatted report.
- You want the user to make choices in a rich form that you read back later.

A Mermaid diagram renders natively in your reply: keep it there, do not build an HTML page.

## Inline or Artifacts tab

- The user said which: follow them.
- Not HTML: the Artifacts tab (an image may also go in your reply, see *Inline images*).
- An HTML page that serves the current reply and fits the chat width and about 900px of height goes **inline**: a demo, a small chart or calculator, a short form answered now.
- A deliverable the user will reopen, keep editing, or share, or one too large for the chat, goes to the **Artifacts tab** with a link: a dashboard, a report, a multi-screen app, a long form.
- When unsure, publish **inline**: the page is also in the Artifacts tab.

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
- Reference assets and other files with **relative** paths (they load); root-absolute paths (`/style.css`) do NOT, except the TwiCC kit (`/_twicc/artifact-theme/kit.css`). Scripts execute (sandboxed, same origin).
- Give the user a **clickable Markdown link**, e.g. `[Open the demo](/artifacts/{session_id}/demo/index.html)`: TwiCC intercepts it and opens the rendered page in the tab. It is an in-app link (for HTML and subfolder files), not a URL to paste in a browser. Same for a Markdown document (`report.md`).

## Topics

**ALWAYS READ THE TOPIC'S FILE BEFORE YOU WRITE THAT KIND OF ARTIFACT.** An HTML page needs `html.md` and `design.md`; an inline artifact also needs `inline.md`. Images, Markdown, Mermaid, PDFs, and media need nothing more than this file.

| Topic | Purpose | File |
|---|---|---|
| HTML pages | Network calls and saved data (`window.twicc.data`) | `html.md` |
| HTML design | Light/dark, TwiCC design tokens, page backgrounds, the kit, user preferences (font size, reduced motion and effects) | `design.md` |
| Inline HTML artifacts | Publishing a page in the conversation: files, IDs, the tag, blending into the chat, memory | `inline.md` |

## Related commands

- `$TWICC artifacts bookmark <SESSION_ID|self|parent> <PATH>` — bookmark an artifact so the user finds it in the sidebar. Skill: `twicc-artifacts`.
- `$TWICC share create artifact <BOOKMARK_ID>` — public read-only link to a bookmarked artifact. Skill: `twicc-share`.
