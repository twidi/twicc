---
title: "OpenRouter"
---

**OpenRouter** is an online service. One account and one API key give access to many AI models. TwiCC can use it as an optional engine in a few places. TwiCC never needs it for the rest of its features.

## Where TwiCC uses it

| Where | What OpenRouter does |
|---|---|
| **Settings → Text to Speech** | Reads messages aloud with an **OpenRouter config**. It also adapts the text before the reading. |
| **Settings → Summaries** | Writes the summaries of messages and selections. |
| **Settings → Titles** | Suggests the title of a session. The rename dialog can also try it for one title. |

For adaptation, summaries and titles, TwiCC uses a fast text model: today, Qwen3.8 27B served by Cerebras.

For speech, you choose the speech model and the voice in **Settings → Text to Speech**. TwiCC loads the lists of models and voices from OpenRouter when you need them. See [Text to speech](help/text-to-speech).

## Turn it on

1. Create an account on [openrouter.ai](https://openrouter.ai) and add credits.
2. Create an API key in the [keys page](https://openrouter.ai/settings/keys).
3. Add the line `TWICC_OPENROUTER_API_KEY=your-key` to the `.env` file of your TwiCC data directory (`~/.twicc/.env` by default).

4. Restart TwiCC. Restart it again each time you change the key.

The key stays on the server. TwiCC never sends it to the browser.

Without the key, the OpenRouter choices are disabled. TwiCC keeps your choice and applies it again when the key is set.

## Privacy and cost

- TwiCC sends the text to OpenRouter: the message for speech and summaries, the user messages for titles.
- You pay on your OpenRouter account. The prices shown in the settings are OpenRouter's list prices. The real cost can differ.
- OpenRouter has its own terms and data policy. Read them before you use it.

## When OpenRouter fails

For adaptation, summaries and titles, an enabled agent takes over: Claude first, then Codex. If no agent can run, the feature shows an error, or the text is read as written.
A speech config that uses OpenRouter has no fallback. Test it in **Settings → Text to Speech** to see the error.
