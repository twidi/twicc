# Inline HTML artifacts — publish a page in the conversation

How to publish an interactive HTML page in the chat. Read `SKILL.md` first, then `html.md` and `design.md`: an inline artifact is an HTML page.

Publish an interactive HTML page directly in a regular session's conversation when this helps the user.

Only the main session agent creates and publishes inline artifacts. Do not delegate inline artifact generation or publication to native subagents. If you are a native subagent, do not create or publish inline artifacts. Native subagent tags remain ordinary text. A separately spawned regular TwiCC session owns its own artifacts; it cannot publish into another session.

## Files and IDs

- Write files first under `{artifacts_base_dir}/{session_id}/inline-artifacts/<id>/`. Every inline page needs its own folder, including a single self-contained file. The entry is a direct child of that folder, with `.html` or `.htm` extension. Use relative asset paths. The Artifacts tab also exposes this folder.
- IDs match `[a-z][a-z0-9_-]{0,63}` and identify the folder in this session. Use distinct IDs for independent widgets.
- Use the same ID and folder for corrections, then insert the tag again. The latest valid tag replaces the previous placement. File edits alone do not reload an inline page. Its Reload control reloads the current files.

## The publication tag

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

## Blending into the chat

Support light and dark (see `design.md`), and keep `html` and `body` transparent while the root carries `data-twicc-display="inline"`: the conversation shows through. The attribute switches to `"fullscreen"` in Full screen and is absent elsewhere (Artifacts tab, browser tab): paint your own background there. The kit does all this; without it:

```css
:root { color-scheme: light dark; background: var(--twicc-canvas); color: var(--twicc-text); }
:root[data-twicc-display="inline"] { background: transparent; }
```

## Memory and saved data

- Input values and page state stay in iframe memory during scrolling, cached session switches, and dock changes. Memory is lost on refresh, Reload, code correction, cache eviction, or view teardown.
- Opening the same page in the Artifacts tab uses independent iframe memory. Both views access the same files and optional saved data.
- Use window.twicc.data only when saved data serves the widget. For example, `await window.twicc.data.set('config.json', choices)` writes `inline-artifacts/<id>/data/config.json`; `.get`, `.list()`, and `.remove` also work. Saved data is separate from iframe memory and survives a page reload.
- Do not save every interface change automatically.
- Do not add a Submit to discussion control.
