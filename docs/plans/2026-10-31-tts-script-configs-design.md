# Text-to-speech: script configs

Follows `docs/plans/2026-10-29-text-to-speech-design.md` (the "TTS spec"),
`docs/plans/2026-10-30-tts-streaming-design.md` (the "streaming spec") and
`docs/plans/2026-10-30-tts-chunking-design.md` (the "chunking spec"), all implemented. Everything
they say stays true for OpenRouter configs. This document adds a second kind of config.

## Problem

Text-to-speech goes only through OpenRouter: its models, its voices, its billing, its key. A user
who wants a local engine (no network, no cost, privacy), another provider's API with their own
key, or their own text preparation before the speech (markdown cleanup, a rewrite by an LLM)
cannot plug it in.

## Intent

A **script config** is a TTS config whose speech comes from a shell command the user writes. TwiCC
gives the command the text on its standard input; the command gives back audio. Everything after
that is the existing pipeline: the audio stream, the players, the splitting, the lookahead, the
paced delivery, the cache, the stop, the validation.

This is an advanced-user feature. TwiCC does not compose the command, adds no arguments and no
speech variables (the command gets the environment TwiCC gives its terminals, before any startup
file runs, and nothing about the speech),
and knows nothing about voices or models: the command holds all of that.

## Goals

- A user can read messages aloud with any engine that a shell command can drive, on Linux and
  macOS (WSL included), the platforms TwiCC supports.
- A command that writes its audio as it is generated streams: the sound starts with the first
  audio bytes, as with a streaming OpenRouter model.
- A command that writes its audio at the end, or that writes a file, still works. With a max
  characters per run set, long texts are split, so the first sound comes after the first part, not
  after the whole text.
- A stop stops the command (pipelines included) and asks for no further part. With a max
  characters per run set and a known byte rate, a command that calls a paid API costs at most what
  the lookahead and one part allow, as with OpenRouter. With no max, one run is the whole text,
  and a stop saves nothing that the command already paid for.
- A broken command gives a clear error in the UI: from the command's own error output when it
  fails, from TwiCC when its output is wrong.
- OpenRouter configs do not change.

## Non-goals

- No arguments, speech variables or placeholders composed by TwiCC. No voice, model, speed
  or instructions fields for a script config.
- No format detection: the user declares the output kind and the format. TwiCC checks that the
  output matches, and fails with a clear message when it does not.
- No audio conversion: no other formats than MP3, WAV (16-bit PCM) and raw 16-bit PCM; no
  resampling; no 24-bit or float WAV.
- No cost figures for a script config: TwiCC cannot know them.
- No check of where a returned file is before its deletion (see *File path rules*).
- No secret handling for the command: it is stored and shown like the other config fields.
- No CLI or MCP command to create or edit a config (none exists today; none is added).
- No CHANGELOG entry (on explicit request only).

## The script config

A config gets a **kind**: OpenRouter (every existing config, and the default) or Script. A script
config has the common fields (name, note, validation, last test run) and these:

| Field | Values | Notes |
|---|---|---|
| Command | A non-empty shell command line | Arguments, pipes, `~` and the login profile's variables work (see *Running the command*). |
| Output | **Audio stream** · **Whole audio** · **File path** | How the command gives its audio, on its standard output. See *Output kinds*. |
| Format | **MP3** · **WAV** · **PCM** | The encoding of the audio, on the standard output or in the file. |
| Sample rate, channels | Integers within the bounds the existing PCM pipeline accepts (the same as for OpenRouter PCM) | PCM only, required (channels default to 1). Raw PCM has no header. The settings refuse other values. |
| Max characters per run | Empty, or a positive integer | Empty: the whole text in one run. Otherwise: the size limit of the parts. See *Splitting*. |
| Delete the file after reading | On (default) · off | File path only. See *File path rules*. |

The kind is fixed when the config is created; a duplicate keeps it.

Every one of these fields, except *Delete the file after reading*, is a speech field: changing any
of them drops the config's validation,
as a model or voice change does for an OpenRouter config. The rest of the validation flow (Test,
"I heard it", activation only when validated) is unchanged.

## Running the command

- The command runs in the user's shell as a **non-interactive login shell**, in the user's home
  directory, with the environment TwiCC gives its terminals (provider and agent variables removed,
  the configured provider homes set). `claude` and `codex` therefore use this instance's homes.
  - The login profile is read (for example `~/.profile`, `~/.bash_profile`, `~/.zprofile`): its
    `PATH` and exported variables apply.
  - The shell does not read the interactive startup files itself (for example `~/.bashrc`,
    `~/.zshrc`). A profile that sources one (Ubuntu's `~/.profile` sources `~/.bashrc`) gets only
    what runs in a non-interactive shell, usually nothing past the file's interactive guard. So
    their aliases, functions, `PATH` additions and exports are usually absent. A command that relies
    on them works in a TwiCC terminal but not here: it must use full paths, or a script that sets
    what it needs.
  - Anything a profile prints on the standard output is part of the command's output: it breaks
    the audio or the path line. The help says it.
- **The command that runs is always the one stored in `tts.json` for the named config.** The open
  step names the config, and never runs a command it carries: a test sends the script fields only
  for the comparison below. A request cannot run anything that the settings did not store.
- **A test runs what the user sees.** The settings save a config without an acknowledgement, so a
  test also sends the script fields the user is testing. The backend compares them with the stored
  ones: when they differ (an edit not saved yet), the test fails at once with a "not saved yet,
  try again" error and runs nothing. "I heard it" therefore always validates the command that ran.
- A message read names its active config too, but it is not a test: it uses the cache and does not
  touch the config's last test run, as an OpenRouter message read today.
- **A message read runs only a validated command**: the backend runs a script config for a message
  read only when its stored script fields equal its stored validation snapshot. Otherwise (an edit
  that raced the read, a stale tab) the read fails at once with a "not validated" error and runs
  nothing.
- The text goes to the command's standard input, UTF-8, then the input is closed. The text never
  appears in the command line: it cannot change what the shell runs.
- One run per part (see *Splitting*). Runs of one speech never overlap. Runs of different speeches
  can: a new reading can start while a stopped run is still stopping, and a test, a message read or
  a second device can run the command at the same time. A command that allows one instance only
  (one GPU, a fixed port) must handle it itself. Known limit, stated in the help.
- **Size cap**: a speech's audio has a fixed size cap, well above the longest real reading (for
  example 512 MB), for the standard output and for a returned file alike. Past it, TwiCC stops the
  command and fails the run with a clear message. A mistaken command that writes without end (a
  generator with no duration, random bytes declared as PCM) or returns a huge file cannot exhaust
  the backend's memory.
- **Success** of a run: the process exits with status 0, and its output matches the declared output
  kind and format. An output with no audio (no bytes, an empty file, a WAV with no samples) fails
  the run, for every format.
- **The end of a run is the command's exit.** The output written before the exit is kept. TwiCC
  does not wait for processes the command left running in the background (a local engine server,
  for example), even when they keep its output open, and does not stop them, except on a stop.
- **Failure** of a run: a non-zero status, or a reason TwiCC finds itself (an output that does not
  match, no audio, the size cap, no progress, a rejected path, a missing file). The exit status
  decides first:
  - a non-zero status that TwiCC did not cause (TwiCC did not stop the run): the last non-empty
    line of the command's error output (stderr), shortened, else a generic message naming the
    status. The output checks do not run: `sh: piper: not found` shows as such, not as "no audio";
  - a status 0, or a run TwiCC stopped itself: TwiCC's own message (the format message, for
    example), never the stderr text, since tools such as `ffmpeg` and `sox` write to stderr even
    on success.

  A failure before any audio fails the open step; a failure
  after some audio behaves as a failed later part does today (the audio already received plays,
  then the reading stops with an error).
- **No progress**: once an audio stream run has given its first audio byte, a run that gives no new
  audio byte for a fixed time is stopped and fails with a clear message. Before its first byte,
  and for whole audio and file path runs, there is no such limit: a start can be long (a model
  that loads, a text rewrite before the speech), and a silent run's length grows with the text.
  These runs end by their exit, an error, the size cap, the user's stop, a cut of the open request,
  or the expiries of the streaming spec (unclaimed, safety). A closed or reloaded page sends no
  release: as for OpenRouter, the command then runs until one of these ends.
- **Long silent runs**: until the first run gives audio, the open request waits. With whole audio
  or a file path and no max characters, that is the whole text's generation. Behind a proxy that
  cuts idle requests (the chunking spec's tunnel limits), the open can then be cut: the browser
  shows the usual network error, and the cut stops the command. The settings help advises a max
  characters per run for these two kinds.
- **Stop**: a release stops the whole command, pipelines included: first a polite stop, then a
  forced one after a short delay. The backend's shutdown stops running commands too.
- The command line is never written to a log or an error message (it can hold a key). The stderr
  line goes to the UI only: the TTS log line records the exit status, not the stderr text (a
  command can echo a key on stderr). The log line names the config kind instead of a model.

## Output kinds

| Output | The standard output holds | TwiCC | First sound |
|---|---|---|---|
| **Audio stream** | The audio, written while it is generated | Reads it as it comes. | After the first audio bytes |
| **Whole audio** | The audio, written at the end of the run | Reads it as it comes (it simply comes late). | After the first part |
| **File path** | One line: an absolute path to a regular file, unique for each run | Waits for the exit, then reads the whole file. | After the first part |

File path rules:

- The output is exactly one line (a final line break is allowed): an absolute path.
- **Each run must give a new path, never one given before.** TwiCC reads the file once the run
  exits and does not keep it; a command that reuses a path can mix two speeches. TwiCC rejects a
  path already returned earlier in the same speech. The settings help says it.
- A missing, unreadable or non-regular file fails the run. A file that does not match the declared
  format fails the run (see *Formats* for what "matches" means).
- **Deletion**: with *Delete the file after reading* on (the default), TwiCC deletes the file once
  it has read it whole and the run succeeded: its audio is then in memory, and the file has no
  other use. TwiCC checks only that the path is still a regular file (a symbolic link or a
  directory is never deleted), not where it is: the path is the command's responsibility. A failed
  run keeps its file, so the user can see what the command wrote. A failed deletion (permissions,
  a file already gone) is not an error. With the option off, TwiCC never deletes the file: the
  command cleans up itself. A bug in a command that returns the path of a file the user keeps would
  delete that file with the option on: the help says that TwiCC deletes the returned file.
- The format check guards against mistakes; it is not a security boundary. The file goes only to
  the authenticated browser, which can already read the user's files through TwiCC's file viewer
  (`/api/file-content/`).

## Formats

| Format | What TwiCC accepts | What the browser gets |
|---|---|---|
| **MP3** | An MP3 stream or file (with or without an ID3 tag) | `audio/mpeg`, played by the media element, as today |
| **WAV** | A WAV with 16-bit PCM samples; its sizes may be unknown (a WAV written as it goes) | The samples, without the header, as `audio/pcm` with the header's rate and channels: the existing PCM player |
| **PCM** | Raw 16-bit little-endian PCM | `audio/pcm` with the config's rate and channels: the existing PCM player |

- An output that does not match the declared format fails the run with a message that names the
  format (for example: the output is not MP3; the WAV is not 16-bit PCM).
- "Matches" means: MP3 starts with an ID3 tag or an MP3 frame; WAV has a RIFF/WAVE header with
  16-bit PCM samples. Raw PCM has no signature, so its check is only negative: an output that
  starts like a known container (MP3, WAV, Ogg, FLAC, MP4) fails with a message that names it
  ("the output looks like MP3: choose MP3"). Any other bytes play as PCM. A partial last frame is
  handled as for OpenRouter PCM: dropped from each part of a split speech.
- A WAV whose rate or channels are zero or outside the PCM bounds of the config fields fails the
  run with the format message.
- The parts of one speech must give the same audio type, as today (`audio/pcm` parts with the same
  rate and channels). MP3 and PCM parts are joined as the chunking spec joins OpenRouter parts.

## Splitting

- **Max characters per run empty**: the whole text goes to one run.
- **Otherwise**: the chunking spec's splitter applies, with this field as the part size limit
  (instead of `min(2000, model max)`), and the same cut order and nothing-to-say rules.
  - **Audio stream**: every part has the full limit (the rule of a streaming format).
  - **Whole audio** and **File path**: growing parts (250 characters, then doubling, up to the
    limit), so the first sound comes quickly.
- The lookahead of the chunking spec applies between parts: the next run starts only when the
  playback needs it soon. A stop starts no further run.
- **A single run** (no max, or a text within one part) is read as fast as the command writes, as a
  single-part OpenRouter speech is: no lookahead inside the run, no paced delivery. Holding a
  command's output could stall a remote API it streams from, and saves nothing: a paid request is
  billed once accepted.
- A split script speech gets the paced delivery and the buffered sending of the chunking spec, as a
  split OpenRouter speech does, whenever its byte rate is known.
- A WAV config counts as PCM for every choice the browser makes before the open (buffered sending
  on the platforms that buffer PCM): its audio reaches the browser as `audio/pcm`.

## Settings

- The add-config action asks for the kind. A script config shows its own fields (above), the name
  and the note; it shows no model, voice or price.
- The section's introduction names both kinds. The OpenRouter key callout (set or missing, and
  "testing is disabled until then") speaks of OpenRouter configs only: a script config tests and
  plays without the key.
- A short help under the fields states the contract: the text on standard input; the audio (or one
  path) on standard output; the exit status; stderr as the error message; a new path for each run,
  deleted by TwiCC after reading unless the option is off;
  the command runs in a non-interactive login shell (what `~/.bashrc` or `~/.zshrc` sets is usually
  absent, and nothing may print on the standard output but the audio or the path); a max characters per
  run is advised for whole audio and file path.
- The note under the fields says what the output kind does, with the existing wordings: "Streaming:
  the audio starts while it is generated." for an audio stream; "No streaming: the audio starts once
  its first part is generated." for the two other kinds; "No streaming: the audio starts once it is
  fully generated." for those two kinds with no max characters.
- The Test button needs a non-empty command (and, for PCM, a rate). It does not need the OpenRouter
  key.
- The last test run shows its figures without a cost (no "price pending" or "price unknown"). Its
  audio length is computed as for OpenRouter, from a constant bitrate: for a variable-bitrate MP3
  it can be wrong. Known limit (a variable-bitrate MP3 also gets no paced delivery, as today).

## Availability

The read-aloud buttons show when the active config is validated. The OpenRouter key is required
only when the active config is an OpenRouter config.

## Cache

The audio cache works for script speeches. Its key holds the text and the script config's speech
fields: a changed command or format never serves an old audio.

Known limit: the cache and the validation see the command line only, not the files it calls. After
an edit to a script that the command runs (`~/bin/tts.sh`), the config stays validated, and a
message read can serve an audio cached before the edit until the cache entry expires. A test never
uses the cache: it checks the edited script.

## Risks to check first

- **Stopping a pipeline**: a stop must end every process of the command, not only the shell, on
  Linux and macOS. Check with a pipeline before relying on it.
- **A WAV written as it goes**: tools that stream WAV write unknown or maximum sizes in the header.
  Check that the header is parsed from the first bytes alone, with a real streaming tool.

## Later

- More formats (OGG/Opus, FLAC), audio conversion (24-bit, float, resampling).
- A cost figure reported by the command.
- A per-run variable (a unique file name offered by TwiCC) for commands that need one.
