# Text-to-speech for chat messages

## Problem

A user wants to listen to a chat message instead of reading it. TwiCC has no audio output for
message content today.

## Intent

Add a "read aloud" action to the toolbar of chat messages, next to the raw-markdown toggle and the
Copy button. The audio comes from the OpenRouter text-to-speech API. Nothing is configured by
default: the user brings an OpenRouter API key, creates one or more speech configurations, tests
them, and activates one.

TwiCC does not decide which model, voice or option works. OpenRouter adds and changes providers
without notice, so TwiCC offers the fields, suggests what the API exposes, and lets the user's test
decide.

## Goals

- A generic frontend service that reads a given text aloud. The message toolbar button is its first
  consumer; other places can use it later.
- Several named speech configurations, stored in their own file, one of them active.
- A configuration becomes usable only after the user tests it and confirms they heard the audio.
- The OpenRouter key never reaches the browser and never leaks to the processes TwiCC launches.
- A global indicator in the sidebar rail shows a pending request or a playback and stops it, from
  every view that shows the rail. When the rail is hidden, the floating sidebar toggle shows that
  something loads or plays. The rail indicator is the entry point for future playback controls.

## Non-goals

- No preparation of the text: the raw markdown source is sent as is.
- No splitting of long texts (see *Later*).
- No streaming playback: the audio plays once fully received.
- No playback controls (pause, seek, speed slider). One click plays, one click stops, from the
  message or from the rail indicator.
- No per-model enforcement of fields (format, speed, instructions, voice). The test decides.
- No price estimate in the UI. The prices the models API lists do not match the real cost (see
  *Verified facts*).
- No voice cloning (`input_references`), multi-speaker input, or `provider` options.
- No CLI command, MCP tool or skill.
- No TTS on public share pages.
- No protection against an agent that reads the `.env` file from disk. Removing the key from
  inherited environments is hygiene, not a security boundary.
- No CHANGELOG entry (on explicit request only).

## Verified facts about the OpenRouter API

Checked with real calls on 2026-10-29. The official guide is
<https://openrouter.ai/docs/guides/overview/multimodal/tts>.

- **Speech:** `POST https://openrouter.ai/api/v1/audio/speech`, bearer key, JSON body with `model`,
  `input`, `voice`, `response_format` (`mp3` or `pcm`, default `pcm`), optional `speed` and
  `instructions`. The success body is raw audio bytes. An error body is JSON:
  `{"error": {"message": "...", "code": 400}}`.
- **MP3:** `Content-Type: audio/mpeg`. A browser plays it directly.
- **PCM:** `Content-Type: audio/pcm;rate=24000;channels=1`. The rate and channel count are in the
  header. The samples are 16-bit little-endian (confirmed by ear after wrapping them in a WAV
  header). A browser cannot play raw PCM.
- **Format support varies:** Gemini TTS rejects `mp3` with an explicit message. MiniMax only
  returns `mp3`. The models API does not say which format a model accepts.
- **Error messages vary:** some are explicit (`Gemini TTS only supports response_format="pcm"`),
  others are generic (`Provider returned 400` for an unknown voice, or for a text that is too long).
- **The official guide is already stale:** its example model `openai/gpt-4o-mini-tts-2025-12-15`
  returns `Model … does not exist`.
- **Model list:** `GET https://openrouter.ai/api/v1/models?output_modalities=speech`, no key needed,
  38 models on 2026-10-29. Each entry has `id`, `name`, `supported_voices` (empty for 6 models) and
  `pricing`. `supported_parameters` is empty for every model: the API does not tell which of
  `speed` / `instructions` a model honours.
- **Advertised limits and prices are wrong:** `hexgrad/kokoro-82m` advertises a 4096-token context
  but accepted 20 000 characters (5 000 tokens). It advertises `0.00000062` per prompt unit but cost
  0.008 $ for 2 000 characters.
- **Long texts:** Kokoro, 20 000 characters: 24 min of audio, 18.9 s before the first byte,
  22.9 MB of MP3. Gemini Flash Lite, 50 000 characters: rejected with `Provider returned 400`.
- **Cost:** the response carries `X-Generation-Id`. `GET /api/v1/generation?id=<id>` (bearer key)
  returns `total_cost`, `latency` and `provider_name`. It answers `404 Generation … not found` for
  about 11 s after the speech response, then the data is available.
- **Abort:** a request cut by the client after 1 s did not show up in the key usage
  (`GET /api/v1/key`) 4 minutes later. Probably not billed; not proven, since usage updates lag.

## The API key

- The user puts `TWICC_OPENROUTER_API_KEY` in the data dir `.env` (`<data_dir>/.env`, loaded by
  `ensure_env_loaded()` in `src/twicc/paths.py`).
- The backend reads the key once at start and keeps it in memory. Adding or changing the key
  requires a backend restart. The settings section says so.
- The key is removed from the environment that TwiCC passes to the processes it launches (agents,
  terminals, tmux, provider CLIs). It joins the existing purge path: `cli/run.py` purges the server
  environment at boot through `ProviderHelpersRegistry.purge_env_vars()`
  (`src/twicc/providers/helpers.py`), which the terminal spawners also call. The backend captures
  the key before that purge.
- A tmux server keeps the environment of the process that started it and outlives backend restarts.
  The key is also removed where TwiCC already cleans such servers: the terminal tmux global
  environment (`purge_tmux_global_env` / `_TERMINAL_ENV_STRIP_NAMES` in `src/twicc/terminal.py`) and
  the hybrid-mode launch environment (`_PURGED_ENV_NAMES` in
  `src/twicc/providers/claude_code/agent/hybrid/tmux.py`).
- The `twicc` CLI loads the `.env` too (`src/twicc/cli/__init__.py`). Every CLI path that launches
  a child process (for example `twicc claude`, `twicc codex`) removes the key first.
- The compute worker (multiprocessing spawn) starts from the purged server environment but loads the
  `.env` again through `settings.py` (`ensure_env_loaded()`), then runs `git` subprocesses
  (`src/twicc/providers/compute_base.py`, `src/twicc/git.py`). It removes the key too.
- Any other process that loads the `.env` and launches children follows the same rule.
- The browser only learns whether a key is present (a boolean), never its value.

## Configuration storage: `tts.json`

A new file `<data_dir>/tts.json`, read and written like `message-snippets.json`
(`src/twicc/message_snippets.py`): whole-file atomic overwrite, a WebSocket update message from the
client, and a broadcast of the new content to every connected client, also sent on connect. Like
`message-snippets.json`, it joins:

- the files covered by the data backups (`COVERED_FILENAMES` in `src/twicc/data_backups.py`);
- the user preference files that `devctl.py` copies into a worktree data dir
  (`SYNCED_CONFIG_FILENAMES`);
- the TwiCC config files ignored by git (`.gitignore`): a worktree data dir is the worktree root,
  so the file appears there.

`devctl.py` never copies the `.env` into a worktree. A worktree gets the key only through the
worktree `.env` catalogue (`.worktrees-env/default.env` in the main repo), like the password hash.

Content:

```json
{
  "active_id": "3f2a…",
  "configs": [
    {
      "id": "3f2a…",
      "name": "Gemini Kore",
      "model": "google/gemini-3.8-flash-lite-tts",
      "voice": "Kore",
      "response_format": "pcm",
      "speed": null,
      "instructions": "",
      "validated": "<marker of the speech fields at the last confirmation, or null>"
    }
  ]
}
```

- The **speech fields** are `model`, `voice`, `response_format`, `speed` and `instructions`. `name`
  is not a speech field.
- A config is **validated** when its current speech fields are the ones the user confirmed. Any
  change to a speech field makes it unvalidated. A name change does not. The exact form of the
  marker (a fingerprint or a snapshot) is the builder's choice.
- `speed: null` and empty `instructions` are not sent to OpenRouter.
- A missing or invalid file reads as `{"active_id": null, "configs": []}`.
- The message that carries the configs to the browser also carries the key-present boolean.

## Backend

### Speech endpoint

A REST endpoint (binary response, not WebSocket) behind the usual access checks. It takes the text
and the speech fields, calls OpenRouter with the key, and returns the audio.

- It takes explicit speech fields, not a config id: the settings test uses values not yet saved,
  and the message button sends the active config's fields.
- The backend decides from the **upstream `Content-Type`**, not from the requested format (a
  provider may answer in another format than the one asked):
  - `audio/pcm…`: wrapped in a WAV header and returned as `audio/wav`. The rate and channels come
    from the `Content-Type` parameters; the sample width is 16 bits. If the header has no rate, it
    uses 24 000 Hz mono and logs a warning.
  - any other audio type (`audio/mpeg`, …): returned unchanged, with the upstream type.
  - a success whose body is not audio (JSON, HTML, …): treated as an upstream error and reported
    with its message, or its status when there is no message.
- Upstream errors are returned with OpenRouter's message as is. When that message is the generic
  `Provider returned 400`, the response adds a hint: check the model, the voice, the format, and the
  text length.
- The endpoint never answers `401` for an upstream error. `401` stays reserved for TwiCC's own
  access check: the frontend (`apiFetch` in `frontend/src/utils/api.js`) treats any `401` as an
  expired TwiCC session and redirects to the login page. A key that OpenRouter refuses (its `401`)
  is reported as an ordinary error, with its message.
- No key: the endpoint refuses with a clear message and makes no upstream call.
- The endpoint always answers with its own error message, never with Django's error page. In
  development TwiCC runs with `DEBUG=True` (devctl sets `TWICC_DEBUG=1`), and Django's debug 500
  page shows the local variables of each frame: it could carry the key to the browser.
- The upstream timeout allows long texts (20 000 characters took 19 s); 120 s at least.
- When the browser cuts the request, Django 6 cancels the view task (`listen_for_disconnect` in
  `django/core/handlers/asgi.py`). This closes the upstream call only if the view is async and
  awaits an async HTTP client in that same task. A sync call (or one under `sync_to_async`) would
  run to the end and may be billed. The speech endpoint is therefore an async view with an async
  upstream call.
- The request body accepts the longest messages TwiCC can display (check Django's request size
  limits).

### Model suggestions endpoint

A REST endpoint that returns the TTS models (`id`, `name`, voices) from the models API above, with
a short backend cache (minutes). It needs no key. If OpenRouter is unreachable, it returns an empty
list and the fields stay free text. `src/twicc/pricing.py` already fetches the full models list
(sync, unfiltered); the builder may share code with it for this endpoint only.

### Logs and cost

- One log line per speech request: model, voice, format, text length, audio size, upstream
  duration, generation id, outcome (ok, upstream error, cancelled).
- After a successful request, a background task fetches the real cost through
  `GET /api/v1/generation?id=…`, starting about 15 s later and retrying on 404 a few times, then
  logs it. It never affects the response and never blocks anything.

## Frontend

### The speech service

A generic service, independent of any component:

- `play(text)` reads the text aloud with the active config. `stop()` stops everything.
- The caller can instead pass explicit speech fields and bypass the cache. The settings test uses
  this.
- The service tells its caller the outcome: the audio started, or the error message. Each caller
  displays errors its own way: the message button shows a toast, and the settings test shows the
  error under the config, without a toast.
- One playback at a time across the whole app. `play()` while something loads or plays stops it
  first (aborting an in-flight request, or stopping the audio).
- It exposes, per text, a state: `idle`, `loading`, `playing`. State is keyed by the text itself:
  two messages with identical text share it.
- It exposes `available`: true when the key is present and the active config is validated.
- An error stops the playback and returns to `idle`.
- When `available` turns false during a request or a playback started from a message (another
  device edits, deactivates or deletes the active config, or the key disappears), the request is
  aborted or the audio stops.
- A short in-memory cache avoids paying twice for a message replayed soon after. Key: the text plus
  the speech fields. Entries expire after 10 minutes. The cache is capped by total size (about
  50 MB), not by entry count: one long message can weigh 23 MB in MP3, about three times more in
  WAV. An audio larger than the cap plays but is not cached. Evicted audio is released.
- It is **one app-wide instance**, owned by no view. The request, the download and the playback
  survive every in-app navigation: scrolling, switching session or project, the artifacts mode, the
  home page. They end only on a page reload, and when the app goes to the login page (logout, or a
  redirect after an expired access): that page has no rail, and the audio of a private message
  must not keep playing there.
- The message button and the rail indicator are views of that state with a stop action, nothing
  more. The home page and the project view each mount their own rail: both show the same state.
- It exposes the global state (`idle`, `loading`, `playing`) for the rail indicator.

### The message button

- In the toolbar of `MarkdownContent` (`frontend/src/components/ui/MarkdownContent.vue`), after the
  Copy button.
- **Opt-in hosts.** `MarkdownContent` shows its toolbar in many places (thinking, reasoning, JSON
  values, workflows, git commit bodies, …). The speech button appears only where a host asks for
  it. In V1 the hosts are the chat text messages, user and assistant (`TextContent.vue`), and the
  compact summaries (`CompactSummary.vue`).
- **Never on public share pages.** They render `TextContent` too, but they have no access to the
  speech endpoint: the button never appears there.
- It appears only when the service is `available`.
- It reads the raw markdown source of the message.

| State | Look | Click |
|---|---|---|
| `idle` | speaker icon, neutral | starts the request |
| `loading` | spinner in place of the icon | aborts the request, back to `idle` |
| `playing` | stop icon, brand colour | stops the audio, back to `idle` |

The end of the audio returns to `idle`.

- **Visible while busy.** The toolbar actions hide when its toggle closes, and it closes on any
  click elsewhere (`useMarkdownToolbar.js`). While its message is `loading` or `playing`, the
  speech button stays visible next to the toggle, even with the toolbar closed and the pointer
  away. Back to `idle`, it hides with the other actions again.
- The toolbar sits at the top of the message and is not sticky: on a long message, the button
  scrolls out of view while the message is still read. The rail indicator covers that case.
- The session list is virtualized: a message scrolled far away unmounts, and its button with it.
  The audio keeps playing; the rail indicator stays reachable. Back on screen, the button shows the
  current state again.

### The rail indicator

A new item in the sidebar rail (`frontend/src/components/sidebar/SidebarRail.vue`, items defined
in `frontend/src/utils/sidebarRail.js`), **first item of the bottom group**, above New session
(the group is New session, command palette, peer inbox, Settings, sidebar toggle). It is on every
view that shows the rail, home included.

| Global state | Indicator | Click |
|---|---|---|
| `idle` | not shown | — |
| `loading` | audio icon with a spinner | aborts the request |
| `playing` | audio icon in green (`--wa-color-success-60`, the green of success callouts and tags) | stops the audio |

- It reflects every playback of the speech service, whatever started it (a message or the settings
  test).
- Its tooltip names the action (cancel the request, stop reading).
- **Hidden rail.** The rail can be hidden when the sidebar is closed (the
  `sidebarRailVisibleWhenClosed` setting). The indicator follows the rail: when the rail is hidden,
  it is hidden too. The floating sidebar toggle (`#sidebar-toggle-button` in
  `frontend/src/views/ProjectView.vue`) then relays the state, as it already relays the peer inbox
  badge:
  - while the global state is `loading` or `playing`, the toggle shows one small audio mark, the
    same for both states;
  - the mark is visual only: a click on the toggle keeps its normal action (it opens the sidebar,
    and the rail with its indicator becomes reachable);
  - the mark and the inbox badge can show together without hiding each other.

### The settings section

A new section **Text to Speech** in `SettingsPopover.vue`, right after **Usage**. Its data is shared
by every device, like `layouts.json`, so it carries the same `synced: true` mark (the cloud icon).

- **Key status** callout:
  - key present: confirmation that `TWICC_OPENROUTER_API_KEY` is loaded;
  - key absent: add `TWICC_OPENROUTER_API_KEY` to `<data_dir>/.env` (the real path is shown), then
    restart TwiCC. Testing is disabled until then.
- A link to the OpenRouter TTS guide and to the models page filtered on speech
  (<https://openrouter.ai/models?output_modalities=speech>).
- The **list of configs**, editable in place like the Apprise targets in `NotificationSettings.vue`.
  Each config shows its name, model, voice, a validated badge and an active mark. Actions:
  - **Activate**: only for a validated config;
  - **Deactivate**: on the active config; it leaves no active config, so the message buttons
    disappear;
  - **Duplicate**: copies the config, not active; it keeps its validation since its speech fields
    are identical;
  - **Delete**: deleting the active config leaves no active config.
- A **New config** button.
- The fields of a config:

| Field | Control |
|---|---|
| Name | text |
| Model | free text, with suggestions from the model suggestions endpoint |
| Voice | free text, with suggestions from the chosen model's voices (none when the list is empty) |
| Format | `SegmentedControl` (`frontend/src/components/ui/SegmentedControl.vue`): MP3 / PCM, MP3 by default |
| Speed | optional number, empty = not sent |
| Instructions | optional text, empty = not sent |

- The suggestions load when the section opens. They never restrict what the user types.

### Testing and validating a config

Same principle as the Apprise targets (a change invalidates the last test), with one more step:
the user confirms by ear.

1. The user edits the test text (a default sentence is provided) and clicks **Test**.
2. The test always calls the API (no cache) with the config's current fields, through the speech
   service.
3. On error, the error message shows under the config. The config stays unvalidated.
4. On success, the audio plays and the UI asks whether the user heard it correctly: **Yes** or
   **No**.
5. **Yes** marks the config validated for exactly the speech fields that were tested. **No** leaves
   it unvalidated.
6. If a speech field changes between the test and the answer, the Yes / No prompt goes away and the
   config stays unvalidated (same guard as the Apprise test, `NotificationSettings.vue`).

The active config can be edited. An edit to a speech field makes it unvalidated: it stays active,
but the message buttons disappear until a new test is confirmed. The section shows this state.

### Visibility rule

The message button shows only when all three are true:

- the key is present;
- a config is active;
- that config is validated.

When the key disappears (restart without it), the buttons disappear. They come back with the key,
without a new test.

## Left to the builder

- Endpoint paths, payload names, WebSocket message names.
- The marker used for validation.
- The exact suggestion control (combobox, datalist, …), as long as free text stays possible.
- The default test sentence, the exact icons, the toast wording.
- The cost lookup schedule, within the facts above.
- How the cache measures size and evicts.

## Later

- **Long texts:** split at sentence boundaries, request the chunks in parallel, and join them in the
  backend into one response (MP3 frames concatenate; PCM samples go under one WAV header). Risk:
  small changes of tone between chunks.
- **Text preparation:** strip markdown, skip or announce code blocks and tables.
