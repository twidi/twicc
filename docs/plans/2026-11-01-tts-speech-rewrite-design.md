# Text-to-speech: adapting the text for speech

Follows the text-to-speech specs (`docs/plans/2026-10-29-text-to-speech-design.md`,
`2026-10-30-tts-streaming-design.md`, `2026-10-30-tts-chunking-design.md`,
`2026-10-31-tts-script-configs-design.md`), all implemented, and the hermetic calls spec
(`2026-10-03-hermetic-llm-calls-design.md`), implemented. Everything they say stays true unless
this document says otherwise.

## Problem

A message is read aloud as written: Markdown. Tables, lists, code blocks, commit hashes, paths and
URLs make a poor speech, read symbol by symbol or as a flat run of cells. A message written for the
eye needs a version written for the ear.

## Intent

Before the speech, TwiCC asks a small model to **adapt the text for speech**: the same content, said
the way a person would say it. The adapted text then goes to the speech step as the text goes today,
whatever the config: OpenRouter or a script. The adaptation is TwiCC's step, not the config's.

The adaptation streams: the speech starts on the first adapted sentences while the model writes the
rest, so the added wait stays short whatever the length of the message.

## Goals

- One global option, on by default, adapts every reading: message reads and config tests alike.
- The model is Claude Haiku or Codex Luna, through the existing hermetic calls (the title calls'
  mechanism), as the user chooses. The default is the first provider that runs.
- The added wait before the first sound is the model's start plus its first sentences, not the whole
  adaptation. Exceptions, where the speech needs the whole text as today: a script config with no
  max characters per run, a model whose MP3 is sent whole (none today), and buffered playback (iOS,
  Safari MP3), which downloads the whole audio before it plays.
- Nothing breaks when the adaptation cannot run: the text is read as written, as today.
- A config test shows how the adaptation went: the model used, its time, and the adapted text.

## Non-goals

- No OpenRouter chat model for the adaptation (later, as an extra choice, for titles too).
- No reusable, long-lived hermetic agent (later, to save the start of each call).
- No adaptation without a model (a rule-based Markdown cleanup): the owner refused it, since a
  table's content can matter.
- No per-config option, no editable prompt.
- No use of the session's provider: a reading is not tied to a session (TTS may later read things
  outside a session).
- No CHANGELOG entry (on explicit request only).

## The option

A global setting, saved with the TTS configs (`tts.json`, synced to every device), in the Text to
Speech section: **Adapt the text for speech** with three choices: the Claude model, the Codex model
(named as in the Titles setting: "Claude Haiku 5.5", "GPT-6 Luna"), and **Off**.

- **Default**: no stored choice means "on, with the first provider that runs": Claude, else Codex.
  A choice the user makes is stored and kept.
- **A stored choice whose provider does not run** (disabled, starting, stopping) falls back to the
  other provider when it runs, as titles do. The stored choice is never rewritten: enabling the
  provider again brings it back.
- **No provider runs**: the reading goes on without adaptation.
- **The settings show what will really run**: a choice whose provider is disabled is shown disabled,
  with the effective choice selected, as the Titles setting does. When no provider is enabled, the
  control shows Off, with a hint that no provider is available; the stored choice stays unchanged.

## The adaptation

- **Input**: the text that is read today (the message's Markdown, or the settings test text).
- **Call**: one hermetic call (no tool, one turn, the neutral working directory, the guards of the
  hermetic spec) to the chosen model, in the **streaming mode** below. Claude Haiku; Codex Luna at
  its lowest effort.
- **Prompt** (fixed in the code). The text is given as delimited content to rewrite, **never as
  instructions to follow or questions to answer**: a user message is often a request to an agent,
  and an assistant message often ends with a question. The model:
  - keeps the language of the text, and every piece of its content: it adapts, it does not summarize
    nor add;
  - turns a table into sentences, a list into a spoken enumeration;
  - replaces a code block with a short description of what it is, without reading it;
  - shortens or leaves out what nobody reads aloud: hashes, UUIDs, long paths, long numbers; an URL
    becomes its site;
  - drops the Markdown syntax and decorative symbols;
  - answers with the adapted text only: plain text, no preamble, no comment.
- **Size limit**: a text longer than a fixed limit (a few thousand words; the model's output limit
  must hold the whole adaptation) is read without adaptation.

## The hermetic streaming mode

The hermetic calls gain a mode that yields the answer's text **as it is generated**:

- Claude: the SDK's partial messages (text deltas).
- Codex: the turn's agent-message deltas, which the hermetic runner ignores today.

The mode changes nothing else: the same options, the same guards (an `init` or thread check, no tool
activity, one turn), the same failure semantics (a guard failure ends the call). One consequence:
some guards can only fail at the end of the turn, after text was already yielded, and maybe spoken.
Such a failure counts as a failure at the moment it happens, on either side of the boundary of *When
the adaptation fails* (before or after the first part went to the speech step). This is
accepted: the guards protect against actions, and the hermetic configuration still allows none. The
title calls keep their current mode. The hermetic diagnostic (`scripts/diagnose_hermetic_llm.py`) covers the
streaming mode for both providers, and runs before this ships.

## From adapted text to speech

- The adapted text feeds the splitter of the chunking spec **as it arrives**. A part is cut as soon
  as the received text holds it whole (up to a cut point of the usual order: paragraph, sentence,
  clause, space). The end of the adaptation releases the last part.
- **Part sizes**: the first parts grow from about 250 characters, for every config and format (the
  text itself arrives over time, so a full-size first part would wait for it); the limits of the
  config apply as today (the model's limit for OpenRouter, *Max characters per run* for a script).
- **A script config with no max characters per run** gets the whole text in one run, as today: it
  waits for the whole adaptation.
- **Split or not**: the part count is not known at the start. An adapted reading counts as split
  (paced delivery, part audio filters, lookahead) as soon as a part is cut before the adaptation
  ends; it is a one-part reading, as today, when the whole adaptation fits the first part.
- **Lookahead and pacing**: otherwise unchanged. The adaptation runs ahead on its own (it is fast);
  the speech of each part still waits for the playback to need it.
- **A stop** ends the adaptation call too.

## When the adaptation fails

A call **stalls** when its first text does not come within a fixed time (about 30 s: a hermetic
call's start includes the model's process start), or when, after some text, no new text comes for a
shorter fixed time (about 15 s). A stall is a failure. The boundary between the two failure cases below is the **first part
sent to the speech step**: until then nothing was spoken, so the original text can still be read
whole.

| When | What happens |
|---|---|
| The chosen provider does not run | The other provider, if it runs; else no adaptation |
| The call fails or stalls before the first part goes to the speech step | The text is read as written (no second provider: the wait stays bounded) |
| The model's answer is empty, or has nothing to read aloud | The text is read as written |
| The call fails or stalls after the first part went to the speech step | The adapted text received is read, up to its last sentence or paragraph end; then the reading stops with a clear error ("The text adaptation stopped before the end"): there is no way to know where to go on in the original text |

A reading that falls back to the original text plays exactly as today. The TTS log line says whether
the text was adapted, by which model, and why not when it was not; never the text.

## Cache

The audio cache key holds the adaptation choice that really ran (Haiku, Luna or none). A reading
that fell back to the original text is cached as an unadapted reading: the next reading with the
option on tries the adaptation again. A second reading of the same message with the same choice is
instant and runs no model.

## Settings

- The **Adapt the text for speech** control sits at the top of the Text to Speech section, before
  the configs, with one line of hint ("A small model rewrites the message for the ear before it is
  read").
- **Config test**: the last test run also shows the adaptation: the model used (or "not adapted",
  with the reason), the time to the first adapted text, and the adapted text itself (folded, one
  click to open), so the adaptation is judged together with the voice.
  - The existing figures keep their meaning for the user: the characters are those of the original
    text (the per-1,000-characters figures then describe a message of that length: the cost covers
    the speech of the adapted text, the times include the adaptation); "first audio" is the wait the
    user had, adaptation included.
- The help page (`frontend/public/help/text-to-speech.md`) gains a short paragraph on the option.

## Known limits

- The adaptation runs on the user's Claude or Codex subscription.
- Each reading starts a new hermetic call: its start adds to the wait before the first sound. The
  owner sees titles (a hermetic call of the same kind) answer in under 5 seconds today; the measure
  in `providers/claude_code/title_suggest.py` (median 17 s) predates the hermetic mode. A long-lived
  agent would remove this start (later).
- Since the adapted text arrives over time, a medium message (a few hundred to 2,000 characters)
  that a streaming model read in one request is now read in a few parts: a few seams, and paced
  delivery.
- A model can still drop or reorder a detail, or, despite the prompt, answer the text instead of
  adapting it; the user can turn the option off.
- A message beyond the size limit is read without adaptation.

## Risks to check first

- **Streaming in the hermetic calls**: check with the diagnostic that both providers stream the
  text and that the guards still hold, before relying on it.
- **The added wait**: measure, on a long real message, the time to the first adapted part with Haiku
  and with Luna, and check it against the first-text limit of *When the adaptation fails*.
- **Adapt, not answer**: check both models on a user request ("Refactor X, then run the tests") and
  on an assistant message that ends with a question: the output must be the same content, adapted.
