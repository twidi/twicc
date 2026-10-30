# Text-to-speech: splitting long texts

Follows `docs/plans/2026-10-30-tts-streaming-design.md` (the "streaming spec") and
`docs/plans/2026-10-29-text-to-speech-design.md` (the "TTS spec"), both implemented. The TTS spec
lists "No splitting of long texts" under *Later*. This document does it. Everything else in both
specs stays true unless this document says otherwise.

## Problem

A speech request sends the whole text to OpenRouter in one request. Every model has an input
limit, and some are low: a message longer than the limit fails with an upstream error (seen in
production: "input must be 1-5,000 characters of speakable text"). Some models accept long texts
but produce wrong audio above a few hundred characters (repeats, early stops). And a request is
billed in full as soon as OpenRouter accepts it, even if the user stops at once: a 10,000-character
message read for two seconds costs 10,000 characters.

## Intent

The backend splits the text into parts, sends them to OpenRouter one after the other, and writes
their audio, end to end, into the one audio stream the browser already reads. The browser sees one
audio, as today.

- Each part fits the model's known limit.
- The audio starts quickly for every model, streaming or not.
- The backend asks for a part only when the playback needs it soon, so a stop does not pay for the
  rest of the text.

## Goals

- No text fails or drifts for being too long, for every model in the model catalog
  (`src/twicc/tts/model_catalog.py`), within what was measured (see *Verified facts*), except
  where the MP3 decision rule of *Risks to check first* applies.
- The time to the first sound does not grow with the length of the text (except in buffered
  playback, where it stays as today: see *Sending the parts*).
- None of these goals, nor the intent above, holds where the MP3 decision rule of *Risks to check
  first* applies: that model's MP3 is then sent whole, as today.
- A stop costs at most the lookahead plus one part ahead of what the user heard: up to about three
  minutes of audio (about 60 s of lookahead plus one part of up to 2,000 characters), instead of the
  whole text today. Exceptions: buffered playback and a playback that ends without a release
  (stated in *Sending the parts*); the buffered sending of the Android decision rule; and, as for
  every goal, where the MP3 decision rule applies. The bound holds for a playback without stalls: a stall (the gaps of
  a slow model, a network stall, a pause by the system such as a phone call) moves the estimate
  ahead of the real position, and adds its length to the cost of a later stop. Accepted. The durations in this document assume an alphabetic language (about 18
  characters per second of speech); in Chinese or Japanese, 2,000 characters are several times
  longer to say, and these figures grow accordingly.
- Almost no change in the browser: one audio id, one read, the same players, states, errors and
  ending step. Three additions: the open step asks for buffered sending when the browser plays
  buffered (or when the Android decision rule below requires it); the
  settings' "No streaming" note under the format now says the audio starts once its first part is
  generated (it said: once fully generated; the old wording stays where the MP3 decision rule
  applies); the last test run figures show the number of parts.

## Non-goals

- No text preparation (markdown, tables, code): the raw text is split, not rewritten.
- No automatic retry: neither with smaller parts when a part is refused (a model whose real limit
  is lower than what TwiCC assumes fails as today; the user picks another model), nor on a
  transient error (429, 5xx) of a later part.
- No parallel requests for several parts.
- No removal of the small audible seams between parts (a slight change of tone, a short silence or
  click): accepted.
- No continuous audio for models that generate near or slower than real time (Sesame CSM-1B, Seed
  Audio): with parts sent one after the other, they have silences between parts. Accepted.
- No setting: the sizes and the lookahead are fixed values.
- No CHANGELOG entry (on explicit request only).

## Verified facts

Measured through OpenRouter on 2026-10-30, now in the model catalog:

- Every model measured at 2,000 characters accepted and completed them, except: Sesame CSM-1B
  refuses more than 200; Gemini (three models), Deepgram Flux and Seed Audio accept more than 500
  but their audio drifts above about 500 characters. Mistral Voxtral (two models) was measured only
  at 1,000 characters (rate-limited at 2,000); its 2,000 is the default, not a measure. The
  catalog's `max_chars` holds these values; a model missing from the catalog gets 2,000.
- A request is billed in full once accepted, even if the client closes it right after the headers.
  A refused request is not billed.
- Streaming (catalog `streams`): with a streaming model, the first audio bytes arrive about 1 s after
  the request, whatever the length; the whole audio arrives faster than it plays, from about 1.4
  times (Deepgram Flux) to about 75 times (MAI Flash). With a non-streaming model, the first byte
  comes when the whole part is generated: about 30 s for 2,000 characters with ElevenLabs v3, a few
  seconds for 250; Sesame and Seed generate near or slower than real time.
- One second of French speech is about 18 characters: 2,000 characters are about 110 s of audio,
  250 are about 14 s.
- Concatenated PCM parts of the same model play as one audio. MP3 files can carry a header (ID3,
  encoder info frame) and a short encoder silence at their start. An encoder info frame (seen in
  Seed Audio's MP3) declares the frame count of its own file: left on the first part, it can make
  a player end the audio at the first part's length.

## Splitting the text

- **Part size limit:** the smaller of 2,000 characters and the model's `max_chars` from the
  catalog. 2,000 even for models that accept more, so that a stop leaves at most one part of
  2,000 characters paid beyond the lookahead.
- **Where to cut:** inside the limit, at the last paragraph end; else the last sentence end; else
  the last clause punctuation; else the last space; a cut inside a word only when nothing else
  fits.
- **Nothing to say:** a part with no letter or digit (a code fence line, a table rule) is not sent,
  wherever it is in the text. A whole text with nothing to say (for example "👍" or "…") fails at
  the open step with its own error ("There is nothing to read aloud in this text"), with no
  upstream call. Today such a text goes to the provider and usually fails there.
- **Growing parts for non-streaming formats:** when the chosen format does not stream for this
  model (catalog `streams`), or the model is not in the catalog, the first part is short (about
  250 characters, one or two sentences) and each next part doubles, up to the part size limit.
  The first sound then comes after the generation of a short part.
- **Full parts for streaming formats:** every part uses the part size limit from the first one.
- A text that fits in the first part is sent in one request, as today.

## Sending the parts

- **The first part** is sent by the open step, as today: the open step answers once its headers
  arrive, with the audio id and the audio type, or with its error.
- **The next parts** are sent one after the other, never two at a time, each after the previous
  one has fully arrived.
- **Lookahead:** the backend sends the next part only while the audio ahead of the playback is less
  than about 60 seconds. The audio ahead is the audio received so far minus the time elapsed since
  the first audio byte was available to the browser (an estimate: the backend does not see the
  playback position). A part already sent is never cancelled for lookahead reasons.
- **Paced delivery (split texts only):** the backend does not hand a whole part to the browser at
  once. Without this, the read would idle for about one part's audio (up to two and a half
  minutes) between parts, and a tunnel or reverse proxy (Cloudflare cuts an idle response after
  100 s, nginx after 60 s by default) would cut every long reading.
  - Pacing is a limit on the stream, shared by every read: the bytes up to about the estimated
    playback position plus a margin of some seconds are available at once to any read, including
    a re-read from the start; the rest is released as the estimated playback advances. The read
    connection never stays without a byte for more than about 30 seconds.
  - It replaces, for split texts only, the read step's rule "it sends each upstream chunk as soon
    as it arrives". A text sent in one part is delivered as it arrives, as today.
  - When a later part fails, the reads already open still deliver the audio already received from
    the earlier parts, at the paced rate, then end abnormally; the ending step answers `failed`
    with that part's message. Audio already paid is not thrown away. A read opened after the
    failure gets the error answer of the current read step, with no audio (the streaming plan's
    browser spike replaced the streaming spec's "bytes received, then abnormal end" with it, so
    that a retrying player does not hang): the audio not yet delivered is then lost. Accepted.
- **Buffered playback:** a browser that plays the whole audio at once (the buffered mode of the
  streaming spec: iOS for both formats, desktop Safari for MP3) asks for buffered sending in the
  open step. For it,
  the backend sends every part as fast as possible, with no lookahead and no pacing: its first
  sound comes when the whole audio is generated, as today. A stop during the download saves the
  parts not sent yet; a stop after the sound starts saves nothing, as today.
- **No release, no stop:** a playback that ends without a release (a vanished page, a network
  drop, a logout) keeps requesting parts to the end of the text, since the lookahead estimate
  follows the clock. It costs the whole text and holds its bytes until the safety expiry, as
  today. Accepted.
- **Same audio type:** every part must come back with the type of the first part. A part with
  another type is a failure. For PCM this covers the rate and channels; for MP3 the type is a bare
  `audio/mpeg`, so a change inside the MP3 stream is not detected. Accepted.
- **MP3 headers:** every part drops its encoder info frame (it declares one file's length), and a
  part after the first one also drops a leading ID3 tag. Any remaining seam is accepted.

## The audio stream across parts

The audio stream of the streaming spec stays one stream:

- it is `running` from the first part's headers until the last part's end, then `complete`;
- `first_audio_ms` is the first audio of the first part; `generation_ms` is the time spent in part
  requests (from each request to its last byte), summed, without the lookahead waits, so that the
  settings figures ("generated in X s", "s / 1k ch") keep measuring generation;
- a failed part makes the stream `failed` with that part's error message; no next part is sent.
  The audio already received is still delivered (see *Paced delivery*); the player may stop at once
  or after it, then the browser gets the late error, as the streaming spec defines;
- a release (stop, newer play) closes the part in flight and sends no next part;
- the ending step, the lifetime and the expiries do not change; the read step changes only by
  the paced delivery of split texts (see *Sending the parts*);
- the bytes an audio id keeps now grow with the text, not with a model limit: about 5 MB per
  minute of 44.1 kHz PCM, so a 50,000-character message (about 45 minutes) holds about 250 MB
  until its release or its safety expiry. Accepted: such texts are rare;
- the streaming spec's accepted limit "the player sees a normal end, nothing is shown" now covers
  the whole playback of a split text, since its read stays open until the last part: a backend
  restart or a network drop in the middle of a long reading can end it early without a message.

## Cache, last test run, cost, logs

- **Cache:** unchanged. The complete audio of all parts enters the cache under the same key (the
  text plus the speech fields). A failed or released stream is not cached. A consequence of the
  lookahead: a long audio becomes complete only one to three minutes before its end, so a long message
  stopped earlier leaves no cache entry, and a replay pays again for its parts. Accepted: the
  lookahead is what saves the rest of the text on a stop.
- **Last test run:** recorded when the stream is complete, as today. It gains the number of parts.
  The settings show it with the other figures when there is more than one part.
- **Cost:** each part has its own generation id. The cost of the run is the sum of the parts'
  costs, recorded once all are known.
- **Logs:** one line per speech, as today, with the number of parts sent.

## Risks to check first

The plan starts with these checks, on real models (paid, small amounts), in Chrome and Firefox on
desktop and on Android (iOS and Safari are out of scope: the owner dropped them; they use the
buffered playback above):

- MP3 parts of a non-streaming model (ElevenLabs), of a streaming one (MiniMax) and of a model
  that writes an encoder info frame (Seed Audio), concatenated, play to the end without an error
  and without a long gap.
- PCM parts concatenated (HeyGen) play without a gap.
- The lookahead and the paced delivery keep the playback fed with a non-streaming model: ElevenLabs
  v3 on a long text (about 6,000 characters, both formats) plays without a gap between parts,
  directly, through the Vite dev proxy, and through a Cloudflare tunnel.
- With the paced delivery, a long reading keeps playing on Android with a locked screen and with
  the page in the background (both formats): the playback now depends on a live read for its whole
  length, while today a text up to the model's limit is downloaded in seconds.
- A stop in the middle of a long text sends no new part (backend log), and OpenRouter's activity
  shows only the parts sent (a released stream records no cost in TwiCC).

**Decision rules:**

- Where concatenated MP3 fails a check for a model, MP3 for that model is not split: the whole text
  is sent in one request, as before this change (a text above the model's limit then fails or
  drifts, as today); PCM is not affected.
- Where the locked-screen or background check fails, the device-side margin of the paced delivery
  grows, up to the whole lookahead (the browser then holds about a minute of audio); the read can
  then idle longer, within the tunnel limits as far as possible. If that is still not enough, the
  open step asks for buffered sending on that platform too (every part as fast as possible, no
  lookahead, no pacing), while the browser keeps its streaming player: the stop saving is lost
  there.

## Left to the builder

- The exact cut rules (abbreviations, punctuation sets per language), the first part size and the
  growth factor within the values above.
- How the backend estimates the audio duration of a part (PCM size, MP3 bitrate), the lookahead
  check frequency, and how it paces the delivery within the stated bounds.
- How the open step asks for buffered sending.
- How the MP3 header of a later part is detected and dropped.
- Where the number of parts shows in the settings.

## Later

- Text preparation for speech (a separate topic, set aside).
- Smarter seams: reuse the previous part's last sentence as context, if a provider supports it.
