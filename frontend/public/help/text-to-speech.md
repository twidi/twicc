---
title: "Text to speech"
---

TwiCC can **read the chat messages aloud**. Pick a config in **Settings →
Text to Speech**, test it, confirm you heard it, and activate it: a speaker
button then shows on the messages.

There are two kinds of configs:

- **OpenRouter**: a speech model of OpenRouter, with its voices.
- **Script**: a shell command of your own, which turns the text into audio.

## Test, confirm, activate

**Test** reads the test text aloud with the config, then asks **"Did you hear
it correctly?"**. Answer **Yes** to validate the config. Only a validated
config can be activated, and the active config is the one the messages use.

Changing what makes the sound (the model, the voice, the format, the command…)
drops the validation: test the config again.

## OpenRouter configs

They need an OpenRouter key: add `TWICC_OPENROUTER_API_KEY` to the `.env`
file of your TwiCC data directory, then restart TwiCC. The key never leaves
the server.

The prices shown are OpenRouter's list prices. After a test, the config shows
what the test really cost.

## Long messages

A long message is read **in parts**, one after the other, in one audio. Each
part is generated when the playback needs it, about a minute ahead: the sound
starts quickly, and a stop does not generate (nor pay for) the rest. The seams
between parts can be audible.

## Adapting the text

Before a message is read, a small model can **rewrite it for the ear**: a
table becomes sentences, a list a spoken enumeration, a code block a short
description; hashes, long paths and the Markdown syntax go away. The content
and the language stay the same. The test of a config is adapted too, and its
last test run shows the adapted text.

Choose the model in **Adapt the text for speech**, at the top of **Settings →
Text to Speech**: **Claude Haiku 5.5**, **GPT-6 Luna**, or **Off**. With no
choice, TwiCC takes Claude, else Codex. A model whose provider is disabled is
replaced by the other one, and your choice comes back when you enable its
provider again.

The adaptation runs on your Claude or Codex subscription. If no provider runs,
if the message is too long, or if the model fails before the reading starts, the
message is read **as written**. If the model fails later, the reading stops
with an error.

## Script configs

TwiCC runs the **Command** in your shell, as a non-interactive login shell in
your home directory, and writes the text to its **standard input**. The
command writes the audio to its **standard output** and exits with status 0.
The text never goes into the command line.

| Output | The command writes | The sound starts |
|---|---|---|
| **Audio stream** | the audio, while it is generated | at its first bytes |
| **Whole audio** | the audio, at the end | when the run ends |
| **File path** | one absolute path, to a new file at each run | when the run ends |

| Format | What the command gives |
|---|---|
| **MP3** | MP3 |
| **WAV** | 16-bit PCM WAV (a WAV written as it goes, with unknown sizes, is fine) |
| **PCM** | raw 16-bit little-endian PCM: give its sample rate and channels |

**Max characters per run**: empty, the whole text goes to one run. Otherwise
the text is split into parts of at most this size, one run each. With **Whole
audio** or **File path**, set it: the sound then starts after the first part,
not after the whole text.

**Delete the file after reading** (File path only, on by default): TwiCC
deletes the file once it has read it and the run succeeded. It only checks
that the path is a regular file, not where it is: make sure the command
returns its own new file. A failed run keeps its file.

### When it fails

- A command that exits with a non-zero status shows the **last line of its
  error output**.
- Otherwise TwiCC says what is wrong: an output that is not the chosen format,
  no audio, a path that is not one absolute path…
- **Nothing else may print to the standard output**: a message from your shell
  profile, or a progress line, breaks the audio.
- What `~/.bashrc` or `~/.zshrc` sets is usually missing (aliases, `PATH`
  additions): use full paths, or a script that sets what it needs.

### Good to know

- A stop ends the whole command, pipelines included.
- Several readings can run the command at the same time (a test and a message,
  two devices).
- An audio stream that sends nothing for a minute after its first audio is
  stopped. The audio of one reading is limited to 512 MB.
- TwiCC sees the command line only: after you edit a script that the command
  runs, test the config again (a message may otherwise play an audio cached
  for up to 10 minutes).

### Examples

A test tone, which ignores the text (**Audio stream**, **WAV**):

```
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=3 -ac 1 -ar 22050 -f wav -
```

[eSpeak NG](https://github.com/espeak-ng/espeak-ng) (**Audio stream**,
**WAV**):

```
espeak-ng --stdin --stdout -v en
```

The same, through a file (**File path**, **WAV**):

```
f=$(mktemp) && espeak-ng --stdin -v en -w "$f" && echo "$f"
```

[Piper](https://github.com/rhasspy/piper) (**Audio stream**, **PCM**, the
voice's sample rate, often 22050 Hz, 1 channel):

```
piper --model ~/voices/en_US-lessac-medium.onnx --output-raw
```

## Known limits

- On an iPhone or an iPad (and in Safari for MP3), the audio is downloaded
  whole before it plays: the sound starts later.
- On Android, a long reading in PCM (or WAV) can stop shortly after the screen
  locks: the browser freezes the page.
