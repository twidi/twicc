# Text to speech: read a selection, and skip the model for simple text

Follows the text-to-speech specs (`docs/plans/2026-10-29-text-to-speech-design.md` and the ones
after it), the text adaptation spec (`2026-11-01-tts-speech-rewrite-design.md`) and the OpenRouter
text calls spec (`2026-11-01-openrouter-text-calls-design.md`), all implemented. Everything they
say stays true unless this document says otherwise.

## Problem

1. A user who selects a passage anywhere in the app can comment it or copy it, but cannot have it
   read aloud. Reading is reachable only from the toolbar of a message.
2. Every reading goes through the adaptation model when the option is on, even for a text the model
   would leave as it is (a plain sentence, a paragraph with a bit of bold). The model adds 0.3 s
   (OpenRouter) to 3 s (Claude, Codex) before the first sound, and a call on a subscription or an
   account, for nothing. This is felt most on a short selection.

## Intent

- A **Read** button in the floating selection widget, next to Comment and Copy, when the speech
  service is available.
- A **decision step** before the adaptation, for every reading: a text that is simple enough is
  cleaned of its Markdown by TwiCC itself and read at once; any other text goes through the
  adaptation as today.

## Goals

- Select a passage, click Read, hear it.
- A plain or lightly formatted text starts to sound with no model call, so as soon as the speech
  step allows.
- The decision never makes a text worse: when in doubt, the text goes through the model.
- The decision applies to every reading of a message or a selection, and to every kind of config
  (OpenRouter and script). The settings test of a config is the exception (see *The decision
  step*).

## Non-goals

- No change to the adaptation itself (prompt, engines, fallbacks), nor to its global option.
- No per-reading choice and no editable rules: one fixed decision.
- No rule-based cleanup of a complex text: a table, a code block, a path, an URL, a hash or a long
  list is never cleaned by rules (the content of a table can matter; the model handles them).
- No CHANGELOG entry (on explicit request only).

## The Read button

- **Where**: in the collapsed selection widget, the row of Comment and Copy: the selection in a
  session and in a file (the browser pane has no collapsed widget). The widgets that open straight on their comment
  panel (the terminal, the browser page errors) and the persistent cards (the element card of
  the browser pane, the area card of the file pane) have no Read button.
- **When**: only while the speech service is available (the active config validated, and for an
  OpenRouter config the key set), as the read-aloud button of a message.
- **What it reads**: the selected text, as the comment widget captures it, through the same
  reading path as a message: the active config, the cache, the adaptation decision below. The
  button shows Stop only for a reading started from a selection of that text, and a message
  button only for a reading started from that message, even when the two texts are equal.
- **Nothing to read**: a selection that holds no word (only symbols), or that cleaning leaves
  empty, is refused as a text without speech is today, with the same toast.
- **While it reads**: the button turns into Stop, with the states and titles of the message button
  (loading: cancel the request; playing: stop reading). It is a view of the one app-wide speech: a
  second reading, from anywhere, replaces the first.
- **The widget closes**: the sound goes on. The user stops it from the rail indicator, as for a
  message. Clicking Read does not close the widget by itself, and does not clear the selection.
- **An error** (the speech request fails) shows as it does for a message: a toast.

## The decision step

### What it decides

For a text to be read, with the adaptation option not Off, the step decides **direct** or
**adapt**:

- **direct**: TwiCC removes the Markdown syntax itself and the speech step gets that text. No model
  call. The reading is **not adapted**.
- **adapt**: the text goes through the adaptation as today, with its limits: a text over the size
  limit, or with no engine available, is read as written.

The decision comes first: a simple text is read directly even when it is over the size limit or
when no engine is available. The option **Off** keeps its meaning: the text is read as written,
with no cleanup, and no decision.

**The settings test of a config always goes through the adaptation** (when the option is not Off
and an engine is available), with no decision: its purpose is to let the user judge the engine and
the voice together, and to show why the model failed. It keeps the behaviour the adaptation spec
and the help page describe.

### Simple and complex

A text is **simple** when everything in it is plain prose or one of: emphasis (bold, italic,
strikethrough), headings, quotes, links with a text (`[text](url)`), a **short list**. Cleaning
removes the syntax and keeps the words (a heading and each list item become a line of their own;
a link keeps its text).

A text is **complex**, and goes through the model, when it holds any of:

- a table;
- a code block, or code in a line;
- a diagram;
- a list that is not short;
- a bare URL, a file path, a hash or UUID, or a long run of digits.

A **short list** has at most 5 items, each under 150 characters, with no sub-list; numbered and
bulleted lists alike.

The rules read the text only; they are instant. The thresholds are fixed values.

**A selection from a file** is source text, not Markdown: it always goes through the model.

**A selection from rendered content** (a session) arrives as plain text, without the
Markdown syntax: a rendered table, code block or list shows no pipe, fence or bullet. The rules
cannot tell them from prose. So a selection that holds a line break or a tab is complex; a
selection on one line is judged by the rules. A message read from its toolbar is judged on its
Markdown source.

**In doubt**: a construct the rules do not recognise with certainty is treated as complex. Wrongly
sending a simple text to the model costs a delay; wrongly reading a complex text directly costs a
bad listening.

### What the user sees

- The TTS log line says that the text was read directly (never the text itself).
- The audio cache keeps a direct reading apart from a reading of the same text as written (the
  option Off, a text over the limit, no engine, a model failure): neither serves the other. A
  second reading of the same kind is instant, as today.
- The help page of text to speech tells how a text is chosen for the model.

## Known limits

- The rules are approximate: a text that is simple for the rules can still sound better after the
  model (a very long paragraph, unusual symbols). The user has no way to force the model for one
  reading: the rules alone decide.
- A selection of rendered content with a line break, and any selection from a file, always goes
  through the model (see above): a multi-paragraph selection does not get the fast start.
- A direct reading keeps the words as written: a number stays a number, an acronym stays an
  acronym, as for a reading without adaptation today.

## Risks to check first

- **The rules against real messages**: on a sample of real assistant and user messages, the share
  read directly, and a listening check of the direct ones (nothing read aloud that should not be:
  no symbol, no syntax).
- **Selections of every origin**: a selection from a session and a file read
  through the button, on both kinds of config, including a one-line selection from a rendered
  table cell or code line, and a one-line selection from a source file.
