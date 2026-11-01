# OpenRouter text calls: Qwen for titles and for the speech adaptation

Follows the hermetic calls spec (`docs/plans/2026-10-03-hermetic-llm-calls-design.md`), the text
adaptation spec (`docs/plans/2026-11-01-tts-speech-rewrite-design.md`) and the title suggestion
feature, all implemented. Everything they say stays true unless this document says otherwise. In the
adaptation spec, it replaces:

- the non-goal "No OpenRouter chat model for the adaptation";
- the three choices of the option (now four, see *The speech adaptation*);
- the statements that the model is Claude Haiku or Codex Luna through a hermetic call (*Goals*, *The
  adaptation*): the Qwen choice runs through the OpenRouter text call below;
- for the Qwen choice only, the rows of *When the adaptation fails* before the first part goes to
  the speech step ("no second provider", and an empty answer read as written): a failed, stalled or
  empty Qwen call falls back to an agent;
- for the Qwen choice, the two known limits that only hold for an agent: "The adaptation runs on the
  user's Claude or Codex subscription" (Qwen runs on the OpenRouter account) and "Each reading starts
  a new hermetic call" (a Qwen reading starts no process: its first text came in under 1 s);
- for the Qwen call only, the stall limits (about 5 s and 5 s instead of about 30 s and 15 s);
- the values of the cache key's adaptation choice (Haiku, Luna, Qwen or none);
- the settings test display: it also shows why Qwen failed (see *The speech adaptation*);
- the rule "When no provider is enabled, the control shows Off": a stored Qwen choice with the key
  set shows Qwen whatever the providers (see *The speech adaptation*).

## Problem

Titles and the speech adaptation run on a small model through a hermetic Claude or Codex call. Such
a call starts a process: the first text comes after about 2 to 3 seconds, sometimes 6 (Haiku's
adaptive thinking). A live trial (2026-11-01) of Qwen3.8 27B served by Cerebras through OpenRouter,
with the current adaptation prompt, gave the first speech part in about 0.3 s, the whole adaptation
in about 0.35 s, at about $0.001 per reading, with a quality close to Codex Luna.

## Intent

Add **Qwen3.8 27B on OpenRouter** as one more fixed choice, next to Claude Haiku and GPT-6 Luna,
wherever the user picks the small model: the Titles setting and the **Adapt the text for speech**
setting. When it fails, TwiCC falls back to the Claude or Codex agents.

Build it on a **reusable OpenRouter text call**, the OpenRouter counterpart of the hermetic calls: a
later feature that needs a small model can use it as is.

## Goals

- One choice "Qwen3.8 27B (OpenRouter)" in both settings. Never the default.
- A failed, stalled or empty OpenRouter call falls back to a Claude or Codex agent; the user still
  gets a title, or an adapted reading.
- The speech adaptation keeps its streaming: the first part goes to the speech step as soon as the
  text holds it.
- One reusable call, with a streaming mode and a whole-answer mode.

## Non-goals

- No free choice of model or provider: the model, its provider (Cerebras) and its options are fixed
  in the code, as Haiku and Luna are.
- No other OpenRouter model in the settings; no OpenRouter choice anywhere else.
- No tool use, no multi-turn conversation, no attachment in the OpenRouter call: one prompt in, one
  text out.
- No change to the prompts: titles and the adaptation send the same prompts to every model.
- No CHANGELOG entry (on explicit request only).

## The OpenRouter key

`TWICC_OPENROUTER_API_KEY`, in the `.env` of the TwiCC data directory, is the key for **everything
that goes through OpenRouter**: speech today, text calls with this spec. It is read and held as the
speech feature does today: it never leaves the server and never reaches a log line.

The settings must know whether the key is set (not its value), to show the Qwen choice as usable or
not. A change of the key still needs a restart of TwiCC, as today.

## The OpenRouter text call

A server-side call to OpenRouter's chat completions, with:

- **Input**: one prompt (and an optional system prompt), the model, the forced provider, the model
  options.
- **Two modes**: a **streaming** mode that yields the answer's text as it comes, and a
  **whole-answer** mode that returns the full text.
- **The provider is forced**: only the given provider may serve the call; OpenRouter must not route
  it to another one.
- **One failure kind**: a missing key, an HTTP error, a refusal, a network error, a time limit, an
  answer from another provider, an answer cut by the model's output limit, or a stream that ends
  with an error all end the call as a failure, with a short reason. The caller decides what to do (both callers below fall back to the agents).
- **Time limits**, set by the caller: for the first text, then between two pieces of text in the
  streaming mode, and for the whole answer in the other mode.
- **A stop** (the caller cancels) ends the HTTP request.
- **Logs**: one line per call with the model, the provider, the outcome, the times and the cost that
  OpenRouter reports; never the prompt nor the answer. Caller logs keep their rules (title logs
  already show the title).

### Qwen3.8 27B

- Model `qwen/qwen3.8-27b`, provider Cerebras, reasoning off (`effort: none`), as measured.
- Display name: **Qwen3.8 27B (OpenRouter)**.
- It is usable when the key is set. It needs no Claude or Codex provider.

## Titles

- The Titles **Model** setting gains a fourth choice, "Qwen3.8 27B (OpenRouter) for every session",
  next to "Match session provider", Haiku and Luna. It is disabled, with a note, when the key is
  not set.
- With Qwen chosen, a title (a suggestion asked by the user, or an automatic title) is made with the
  same system prompt, the same input and the same checks of the answer as an agent title call,
  through the whole-answer mode, in **one attempt** with a short time limit (about 10 s): the
  fallback below takes the place of a retry.
- **Fallback**: when the Qwen call fails, or its answer fails the checks, the title is made as with
  "Match session provider": the session provider's model first, then the other provider's model, as
  the current fallback does. A suggestion asked without fallback (the rename dialog's "try another
  model") stays without fallback.
- **Nothing works**: Qwen failed and no agent runs, or every one failed: the user sees that no title
  could be generated (the current "generation failed" outcome, not "no provider available", since a
  model was tried).
- **The rename dialog** names the model that really made the title, and marks it as a fallback when
  it is not the model asked for (a Qwen title is not an agent title, even though no provider is
  involved).
- **The key is removed** (or not set): a stored Qwen choice works as "Match session provider"; the
  stored choice is never rewritten and comes back once the key is set and TwiCC restarted, as a
  displaced Haiku or Luna choice does today.
- The rename dialog offers Qwen as an alternative model when the key is set, as it offers the other
  enabled models.

## The speech adaptation

- **Adapt the text for speech** gains a choice "Qwen3.8 27B (OpenRouter)", between GPT-6 Luna and
  **Off**. It is disabled, with a note, when the key is not set. The default does not change: no
  stored choice means the first agent that runs.
- With Qwen chosen, the adaptation uses the streaming mode; the adapted text feeds the splitter as
  the agents' text does. Part sizes, the cache, the size limit and the settings test display stay as
  the adaptation spec says, except for what follows. The cache key holds "qwen" when Qwen adapted the text.
- **Time limits**: the first text must come within a short fixed time (about 5 s; the measured
  first part came in under 1 s); after some text, no new text for a short fixed time (about 5 s) is a
  stall.
- **Fallback before the first part goes to the speech step**: a failed, stalled or empty Qwen call
  (an empty answer, or one with nothing to read aloud) hands the text to the first agent that runs (Claude, else Codex), with the agents' own limits; if
  no agent runs, or the agent fails too, the text is read as written.
- **What the settings test shows**: the engine that really adapted the text, and, after a Qwen
  failure, why Qwen failed (so that a wrong key or an empty OpenRouter account does not go
  unnoticed); when the agent failed too, why each one failed. Its time to the first adapted text
  counts from the start of the reading, the wait for Qwen included: it is the wait the user had, as
  "first audio" is. The log line says the same, never the text.
- **The test's cost**: the cost shown for a test stays the cost of the speech, as today (a script
  config shows none). The Qwen call's cost (about $0.001) is not in it; the log line of the call
  has it.
- **Cache after a fallback**: a reading adapted by the agent after a Qwen failure is cached as that
  agent's adaptation. A new reading with Qwen chosen plays a cached Qwen reading if there is one,
  else a cached reading by the agent that Qwen falls back to, else runs Qwen again: a re-read stays
  instant even while Qwen keeps failing. A reading that fell back to the original text stays cached
  as unadapted, as the adaptation spec says.
- **After the first part went to the speech step**: as the adaptation spec says for any engine: the
  received text is read up to its last sentence end, then the reading stops with "The text
  adaptation stopped before the end".
- **The key is removed** (or not set): a stored Qwen choice works as no choice (the first agent that
  runs); the stored choice is never rewritten and comes back once the key is set and TwiCC
  restarted.
- **What the control shows**: a stored Qwen choice with the key set shows Qwen, even when no agent
  runs, without the "no provider available" hint (the adaptation spec's "no provider enabled shows
  Off" applies only when the control falls back to Off).

## Settings and help

- Both settings show what will really run, as they do for a disabled provider.
- The Qwen choice says, in its note or the setting hint, that the text goes to OpenRouter and
  Cerebras and is paid on the OpenRouter account.
- The Titles hints stay true with Qwen: the fallback hint speaks of "another model", not "the other
  provider", and the "your choice comes back" hint covers a Qwen choice displaced by a missing key
  (it comes back once the key is set and TwiCC restarted).
- The command-line description of the title model setting lists the Qwen choice and its fallback to
  the agents.
- The help page `frontend/public/help/text-to-speech.md` gains the Qwen choice and its fallback, and
  its sentences that only hold for the agents become true for every choice: what the adaptation
  runs on (a subscription, or the OpenRouter account for Qwen); what a failure before the reading
  starts does (Qwen falls back to an agent; an agent's failure reads the text as written); what the
  test's cost covers (the speech only, not the Qwen adaptation); when no agent runs (with Qwen chosen
  and the key set, Qwen still adapts the text); and what replaces a choice that cannot
  run (a disabled provider's model gives way to the other agent, a Qwen choice without the key gives
  way to the default).
- The README's description of the key says it is the key for everything that goes through
  OpenRouter: speech, and the Qwen choice for titles and the speech adaptation.

## Known limits

- The text of a message (for a reading) or the user messages a title is made from (for a title)
  are sent to OpenRouter and Cerebras, two third parties beyond Anthropic and OpenAI.
- A change of model (Cerebras retires `qwen/qwen3.8-27b`, or a better model comes) is a code change,
  as for Haiku and Luna; meanwhile every Qwen call fails and falls back to the agents.
- The prompts were tuned on Haiku and Luna. Qwen, with the same prompt, sometimes drops a sentence
  or a label (measured: 6 outputs with a defect in 30, against 2 in 10 for Luna).
- A reading cached by an agent (made before Qwen was chosen, or after a Qwen failure) plays instead
  of a new Qwen adaptation while it stays in the cache; nothing tells the user which engine adapted
  the cached audio.
- A fallback after a Qwen failure adds the Qwen time limits to the wait: up to about 10 s for a
  reading (a first text that comes late, then a stall), about 10 s for a title.

## Risks to check first

- **The call, live**: both modes against the real Qwen endpoint; the forced provider holds (an
  answer from another provider is a failure); a stop ends the request; the cost is reported.
- **Titles with Qwen**: on real sessions (a first prompt, a long session, an automatic title update
  that compares with the current title), Qwen's titles pass the existing checks as often as the
  agents' titles.
- **Fallback, live**: with a wrong key and with a forced stall, a reading and a title still come
  from an agent, in the expected time.
