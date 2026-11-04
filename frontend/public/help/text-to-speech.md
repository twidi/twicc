---
title: "Text to speech"
---

TwiCC can **read the chat messages aloud**. Pick a config in **Settings →
Text to Speech**, test it, confirm you heard it, and activate it: a speaker
button then shows on the messages.

There are two kinds of configs:

- **OpenRouter**: a speech model of OpenRouter, with its voices.
- **Script**: a shell command of your own, which turns the text into audio.

## Read new timeline messages

Use the **speaker button beside Timeline** to read new assistant messages
automatically. An available text-to-speech config is required. The pressed
button means that this timeline listens for new messages.

- Listening uses the timeline's scope: all projects, a workspace, a project,
  or one worktree. Muted sessions are hidden and skipped by default. Select
  **Filters → Show muted sessions** to display and read them in that timeline.
  Other display filters, including the selected view and session filter, do
  not affect listening.
- Turning off **Show muted sessions** removes their queued messages. The
  reading already selected can finish. Turning it on does not replay messages
  skipped while it was off. Manual reading remains available in muted sessions.
- Listening continues when you navigate to another page, switch application
  tabs, or open a session. Only one timeline listens per browser tab.
- The timeline button in the rail shows a small speaker while listening is
  enabled. Its label identifies the listening timeline. Click it to open
  that timeline without changing listening.
- Messages must have a native message date **strictly after the activation
  click**. Existing history does not play when you enable listening.
- Late arrivals remain eligible up to **five seconds** behind the latest
  message whose reading starts. Exactly five seconds remains eligible.
  Accepted messages stay queued until their turn.
- Manual reading has priority. Automatic reading waits while a manual request
  or playback runs. The rail's timeline speaker stays visible during this wait
  and between messages.
- Click the pressed timeline speaker to disable listening and clear its queue.
  This stops its automatic reading and leaves unrelated manual reading running.
  Enabling another timeline clears the previous timeline's queue.
- The existing rail **Stop reading aloud** button and the message's **Stop
  reading** button stop the current reading. During automatic reading, they
  also disable listening and clear its queue.
- Listening and its queue stay in memory. A page reload clears them.

Automatic reading uses the active text-to-speech config. In **Settings →
Summaries**, **Read summaries in timeline speech** controls automatic summaries.
It is on by default. Eligible messages contain at least **1,000 content
characters**. Shorter messages use the existing text adaptation setting.
Project and session announcements keep their usual wording.

TwiCC prepares up to two queued text summaries while another reading plays.
It generates audio only when a message reaches its turn. A selected reading
keeps its settings until it finishes. Turning the switch off releases unneeded
preparation and makes pending messages use their originals. A failed summary
shows an error and skips that message. Listening continues with the next message.

## Summarize a message or selection

Use **Summarize** on an eligible assistant message in chat or a timeline message
view. The threshold counts the complete logical message, including its code and
text blocks. It excludes embedded artifact payloads and control markup.
The summary targets about one third of the source, with a maximum of 140 words.

Message summaries are saved. Chat, timeline views, and automatic listening share
the same result when the source and model policy match. Automatic preparation
also saves successful summaries. An older policy result remains available as
**Previous policy**. Regeneration replaces a result only after success.

Use **Original** and **Summary** to select the visible text. Copy and **Read
aloud** use that view. Original chat speech keeps its original block scope.
Original timeline speech uses the assembled timeline snapshot. Manual speech
interrupts automatic speech and works independently of the automatic summary
switch. The usual project and session announcements never identify a summary.

Select text and use **Summarize** in the selection actions for a temporary
summary. Its dialog supports Copy, Read aloud, Retry, Cancel, and Close. Closing
the dialog releases its generation and stops its matching reading. Selection
summaries and the settings test are never saved as message summaries.

Summary text appears as it streams. Speech starts from available summary parts
without a second adaptation call. If generation fails after speech starts,
playback stops with an error. TwiCC does not replay the original or save a
partial summary.

### Summary model

Choose **Auto**, **Claude Haiku 5.5**, **GPT-6 Luna**, or **OpenRouter** in
**Settings → Summaries**. Auto selects an enabled running agent, Claude first,
then Codex. An unavailable selected agent gives way to the other agent.

OpenRouter uses the existing OpenRouter text model and key. Its calls are paid
on your OpenRouter account. Without the key, or above **60,000 source
characters**, TwiCC selects an enabled agent. It keeps the full source.
If OpenRouter fails before summary speech commits, an enabled agent can replace
its provisional text. Without an available model, summarization shows an error.
The automatic timeline switch does not disable manual summaries.

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
what the speech of the test really cost.

## Long messages

A long message is read **in parts**, one after the other, in one audio. Each
part is generated when the playback needs it, about a minute ahead: the sound
starts quickly, and a stop does not generate (nor pay for) the rest. The seams
between parts can be audible.

## Reading a selection

Select text in the chat or in the file viewer: the floating widget shows the
**Read button** (the speaker icon), next to the Comment and Copy buttons. It
does not show in the terminal, in the browser pane or on the element cards.
It shows only when the speech service is available, as the speaker button of
a message does.

- The button reads the selected text with the active config. Its title is
  "Read aloud".
- While it reads, the same button is a **Stop icon** (title "Stop reading"),
  and it stays visible. Click it to cancel the request or to stop the sound.
- The sound goes on when the widget closes. Stop it from the speech indicator
  of the rail, as for a message.
- A text with nothing to read aloud once its formatting is removed is
  refused, as for a message.

## Adapting the text

A small model can **rewrite a text for the ear** before it is read, when the
text is not simple: a table becomes sentences, a list a spoken enumeration, a
code block a short description; hashes, long paths and the Markdown syntax go
away. The content and the language stay the same. A **simple text is read at
once**, with its Markdown marks removed and no model: the sound starts
sooner, and nothing is paid for the rewrite. A Markdown link is read by its
text.

The test of a config never takes the direct reading: it goes through the
model whenever the model can run, so it shows the adaptation, and its last
test run shows the adapted text. With **Off**, or with no model available, it
is read as written.

For example, these texts are **not simple**, and go through the model:

- tables, code and code-like text (anything with code symbols such as `$`,
  `=`, `|` or braces, `snake_case` names, `#123`), and HTML;
- addresses, paths, hashes and long numbers;
- long lists: more than five items, long items or sub-lists;
- a selection with a line break or a tab in the chat, and any selection in the file
  viewer.

When the rules do not recognise a text with certainty, the model decides.

Choose the model in **Adapt the text for speech**, at the top of **Settings →
Text to Speech**: **Claude Haiku 5.5**, **GPT-6 Luna**, **OpenRouter**, or
**Off**, which reads every text as written. With no choice, TwiCC takes
Claude, else Codex. A Claude or Codex model whose provider is disabled is
replaced by the other agent, and your choice comes back when you enable its
provider again.

Claude Haiku 5.5 and GPT-6 Luna run on your Claude or Codex subscription.
**OpenRouter** calls a fast model on OpenRouter: today, Qwen3.8 27B served by
Cerebras. It needs the OpenRouter key, as the OpenRouter configs do. It sends
the text to OpenRouter, and it is paid on your OpenRouter account. With the
key set, it adapts the text even when no provider runs.
Without the key, the default replaces it, and your choice comes back once the
key is set and TwiCC restarted.

If OpenRouter fails before the reading starts, the first agent that runs
(Claude, else Codex) adapts the text. If no model can run, if the text is
too long, or if the model fails before the reading starts (for OpenRouter: if
that agent fails too, or if no provider runs), the text is read **as
written**. If the model fails later, the reading stops with an error.

The last test run of a config shows the model that adapted the text, or each
model that failed and why: a wrong key or an empty OpenRouter account shows
there. The cost of a test is the cost of the speech only: the OpenRouter
adaptation is not in it.

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
