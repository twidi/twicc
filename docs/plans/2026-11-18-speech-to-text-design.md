# Speech to text (dictation in the composer)

Status: implemented (PR "feat(composer): dictation (speech to text)").

## Goal

Speak instead of typing in the message box, on desktop and on phones, without
an external dictation app (today users run a desktop tool such as Handy, then
paste). It should be accurate, private when wanted, and one tap/one key away.

## UX

- A **microphone button** next to the attach button. Tap = start, tap = stop.
  While listening it shows a pulsing stop icon and the elapsed time; while the
  server transcribes, a spinner. On touch devices the hit area is enlarged and
  the keyboard is not opened.
- **Keyboard (desktop):** `Ctrl+Shift+Space` in the composer toggles; holding
  it is push-to-talk (released after 600 ms → stop). `Escape` cancels and
  discards.
- The text is inserted **at the caret** (replacing a selection), with natural
  spacing. The browser engine writes **partial results live** and refines them
  in place, like the OS dictation; only confirmed text is kept at the end.
- Sending (or clearing) the message during a live dictation detaches it, so it
  never writes into the next message.
- Errors (microphone refused, no key, nothing heard…) show as a short tooltip
  on the button, localized.

## Engines (synced setting `sttEngine`)

| Engine | Where | Pros | Cons |
|---|---|---|---|
| `browser` | Web Speech API | free, live, zero setup | Chrome/Edge/Safari only (no Firefox); Chrome sends audio to Google |
| `openrouter` | `POST /api/stt/transcribe/` → OpenRouter `/audio/transcriptions` (`sttModel`, default `openai/whisper-large-v3`) | best accuracy, every browser, reuses the TTS key | paid, text at the end |
| `script` | same endpoint → the user's command on the TwiCC host | private/free (local whisper.cpp, faster-whisper…), every browser | setup |
| `auto` (default) | script if configured, else OpenRouter if the key is set, else browser | | |
| `off` | button hidden | | |

An explicit choice that cannot run here is reported (no silent switch); only
`auto` falls back. `sttLanguage` (ISO code, empty = UI language for the
browser engine, auto-detection for the others).

## Recording (server engines)

`MediaRecorder` with the first supported type: WebM/Opus (Chrome, Firefox,
Edge, Android) or MP4/AAC (Safari macOS/iOS) — both accepted by OpenRouter
(`webm`, `m4a`). Echo cancellation and noise suppression on. Auto-stop after
5 minutes (OpenRouter's providers stop processing at 60 s; Whisper handles
minutes of audio within that). The server refuses > 25 MB.

## Script contract

Runs like a text-to-speech script (login shell, home directory, the terminals'
environment, own process group, 180 s timeout, killed on cancel):
`TWICC_STT_FILE` (the recording, deleted afterwards), `TWICC_STT_FORMAT`,
`TWICC_STT_LANGUAGE`; standard output = the text. The command line comes from
the synced settings — the browser never sends one. Example:

```sh
ffmpeg -loglevel error -i "$TWICC_STT_FILE" -ar 16000 -ac 1 -f wav - \
  | whisper-cli -m ~/models/ggml-large-v3-turbo.bin -l "${TWICC_STT_LANGUAGE:-auto}" -nt -np -f -
```

## Security / privacy

- The OpenRouter key never leaves the server; neither it nor the script command
  nor an upstream error body reaches a log line or an error message.
- Browsers only expose the microphone to secure contexts (HTTPS/localhost);
  the settings explain it when the page is not secure.

## Follow-ups

- Android app (WebView/GeckoView): grant `RECORD_AUDIO` and the WebView
  permission request so the button works inside the app (mobile browsers and
  the PWA already work).
- Streaming server transcription (partial results with OpenRouter/script).
- A global (not composer-scoped) dictation shortcut.
