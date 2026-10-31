# Text-to-speech script configs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second kind of TTS config, the script config: a shell command the user writes gives the speech audio (text on stdin; MP3, WAV or raw PCM on stdout, streamed or at the end, or a file path), played through the existing pipeline.

**Architecture:** The backend stores a `kind` per config. A new pump (`src/twicc/tts/script.py`) runs the stored command once per part in a subprocess and writes into the existing `AudioStream` through the same sink calls as `pump_speech`. A new module (`src/twicc/tts/script_audio.py`) checks the declared format and turns WAV into PCM. The open view dispatches on the kind and enforces the "stored and validated command only" rules. The frontend helpers, store and settings card learn the kind.

**Tech Stack:** Django 6 ASGI views, asyncio subprocesses, pytest; Vue 3 `<script setup>`, Pinia, Web Awesome, `node --test`.

**Spec:** `docs/plans/2026-10-31-tts-script-configs-design.md`. It builds on `docs/plans/2026-10-29-text-to-speech-design.md`, `docs/plans/2026-10-30-tts-streaming-design.md` and `docs/plans/2026-10-30-tts-chunking-design.md`.

## Global Constraints

- Work on `main` in `/home/twidi/dev/twicc-poc`. No branch, no worktree. Prefix every command with `cd /home/twidi/dev/twicc-poc && `.
- Python tests: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q`. Lint: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/tts src/twicc/terminal.py tests/test_tts_*.py`. Frontend: `cd /home/twidi/dev/twicc-poc/frontend && npm test`; build: `npm run build` after a frontend change.
- Never call OpenRouter. Never open or print `~/.twicc/.env`. Never restart servers, never `migrate`, never `npm install`.
- Other agents share the checkout: `git add` only the task's files, by name. Never stash.
- Backend: `orjson`, never stdlib `json`. `NamedTuple` for immutable data. Every endpoint keeps its own JSON errors.
- Commits: Conventional Commits, lowercase summary, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`. No CHANGELOG entry.
- Linux and macOS only (TwiCC has no native Windows support).
- **The command line and the stderr text never reach a log line.** The TTS log line of a script speech names `kind=script`, the output kind, the format and the characters.
- **The command that runs is always the one stored in `tts.json`**, read by the backend for the named config. Nothing in a request body is ever run.
- Script config field names (backend and frontend, same names): `kind` (`"openrouter"` | `"script"`), `command`, `output` (`"stream"` | `"whole"` | `"path"`), `audio_format` (`"mp3"` | `"wav"` | `"pcm"`), `pcm_rate` (int or null), `pcm_channels` (int, default 1), `max_chars` (positive int or null). PCM bounds: `0 < pcm_rate <= 384000` (`openrouter.MAX_PCM_RATE`), `0 < pcm_channels <= 8` (`openrouter.MAX_PCM_CHANNELS`).
- Constants (`src/twicc/tts/script.py`): `SCRIPT_MAX_BYTES = 512 * 1024 * 1024`, `STALL_SECONDS = 60.0`, `STOP_GRACE_SECONDS = 2.0`, `EXIT_DRAIN_SECONDS = 0.5`, `EXIT_POLL_SECONDS = 0.05`, `STDERR_TAIL_BYTES = 8192`, `ERROR_LINE_CHARS = 300`, `PATH_OUTPUT_MAX_BYTES = 64 * 1024`, `READ_CHUNK = 64 * 1024`. `script_audio.WAV_HEADER_MAX_BYTES = 64 * 1024`, `script_audio.SNIFF_BYTES = 12`.
- **Messages (exact)**, user-facing:
  - `script_audio`: `NOT_MP3 = "The script output is not MP3"`, `NOT_WAV = "The script output is not WAV"`, `WAV_NOT_16BIT = "The script WAV is not 16-bit PCM"`, `WAV_BAD_FORMAT = "The script WAV has an unsupported sample rate or channel count"`, `NO_AUDIO = "The script gave no audio"`; a PCM lookalike: `"The script output looks like MP3: choose MP3"`, `"The script output looks like WAV: choose WAV"`, `"The script output looks like Ogg audio, which TwiCC does not play"`, `"The script output looks like FLAC audio, which TwiCC does not play"`, `"The script output looks like MP4 audio, which TwiCC does not play"`.
  - `script`: `COULD_NOT_START = "The script could not start"`, `SCRIPT_FAILED = "The script failed (exit status {status})"`, `SCRIPT_KILLED = "The script was stopped by signal {signal}"`, `STALLED = "The script stopped sending audio"`, `TOO_LARGE = "The script audio is too large"`, `NOT_A_PATH = "The script output is not one absolute path"`, `PATH_REUSED = "The script returned the same path twice"`, `FILE_UNREADABLE = "The script file cannot be read"`.
  - `views`: `NOT_SAVED = "This config is not saved yet: try again"` (409), `NOT_VALIDATED = "This script config is not validated: test it again in the settings"` (409), `NO_SUCH_SCRIPT = "This script config does not exist"` (404), `NO_PCM_RATE = "This script config needs a sample rate for PCM"` (400).
- **Settings copy (exact):**
  - Intro: `Read the chat messages aloud with OpenRouter, or with your own script.` followed by the existing OpenRouter guide and models links and the list-price sentence.
  - Missing-key callout: `Add TWICC_OPENROUTER_API_KEY to <path>, then restart TwiCC. Testing OpenRouter configs is disabled until then.` (the success callout is unchanged).
  - New-config buttons: `New OpenRouter config`, `New script config`.
  - Script field labels: `Command`, `Output`, `Format`, `Sample rate (Hz)`, `Channels`, `Max characters per run` (placeholder `Whole text in one run`).
  - Output options: `Audio stream`, `Whole audio`, `File path`. Format options: `MP3`, `WAV`, `PCM`.
  - Help: `TwiCC runs this command in your shell, as a non-interactive login shell in your home directory, and writes the text to its standard input. The command writes the audio to its standard output (for File path: one absolute path, to a new file at each run) and exits with status 0. If it fails, the last line of its error output is shown. What ~/.bashrc or ~/.zshrc sets is usually missing, and nothing else may print to the standard output. Several readings can run the command at the same time. With Whole audio or File path, set a max characters per run: the audio then starts after the first part.`
  - Script Test-button title when it cannot test: `Test: needs a command, a sample rate for PCM, and a test text`.
  - Script auto name: `Script · <program>` (`<program>`: the base name of the command's first word), else `Unnamed script`.

## Review Focus

1. **A command whose profile or itself prints text on stdout** (a banner, a `curl` progress line): the user expects the format message, not noise or silence. Pinned in Task 2 (`test_text_is_not_mp3`) and Task 3 (`test_a_banner_before_the_audio_fails_with_the_format_message`).
2. **A stop in the middle of a pipeline** (`engine | sox …`): every process of the command must end. Pinned in Task 3 (`test_a_release_stops_the_whole_pipeline`).
3. **A command that writes its audio before it reads all of a long text**: no deadlock between stdin and stdout. Pinned in Task 3 (`test_a_long_text_while_the_command_writes_first`).
4. **A command that leaves a background process holding its stdout** (a local engine server): the run ends at the command's exit, and no pipe descriptor leaks. Pinned in Task 3 (`test_a_background_child_keeping_stdout_open_does_not_hold_the_run`, `test_a_failing_command_with_a_background_child_fails_at_once`).
5. **An edit of the command just before a test, or a stale tab reading with an unvalidated config**: nothing unvalidated runs. Pinned in Task 4 (`test_a_test_with_unsaved_fields_is_409_and_runs_nothing`, `test_a_message_read_of_an_unvalidated_script_is_409`).

---

### Task 1: Script configs in tts.json (backend storage)

**Files:**
- Modify: `src/twicc/tts/config.py`
- Test: `tests/test_tts_config.py`

**Interfaces:**
- Consumes: `openrouter.MAX_PCM_RATE`, `openrouter.MAX_PCM_CHANNELS`.
- Produces:
  - `CONFIG_KINDS = ("openrouter", "script")`, `SCRIPT_FIELDS = ("command", "output", "audio_format", "pcm_rate", "pcm_channels", "max_chars")`, `SCRIPT_OUTPUTS = ("stream", "whole", "path")`, `SCRIPT_FORMATS = ("mp3", "wav", "pcm")`.
  - `script_fields(raw: dict) -> dict`: the six fields, normalized (`command`: string, else `""`; `output`: in `SCRIPT_OUTPUTS`, else `"stream"`; `audio_format`: in `SCRIPT_FORMATS`, else `"mp3"`; `pcm_rate`: int within bounds, else None; `pcm_channels`: int within bounds, else 1; `max_chars`: int ≥ 1, else None; `bool` is never a number).
  - Every normalized entry carries `kind` (missing or unknown → `"openrouter"`). An OpenRouter entry keeps today's fields; a script entry has `id`, `name`, `kind`, the six script fields, `note`, `validated`, `last_test` (no OpenRouter speech field).
  - `validated` is normalized per kind: the kind's fields only (a `kind` key in the snapshot is ignored), None when a field is missing.
  - `stored_script_config(config_id: str) -> dict | None`: the normalized entry of that id in tts.json when it is a script config, else None.
  - `script_config_validated(entry: dict) -> bool`: its `validated` equals `script_fields(entry)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_entry_without_kind_is_openrouter():
    assert normalize_tts_config({"configs": [_entry()]})["configs"][0]["kind"] == "openrouter"

def test_a_script_entry_keeps_only_its_fields():
    raw = {"id": "s", "kind": "script", "name": "Piper", "command": "piper -q", "output": "path",
           "audio_format": "pcm", "pcm_rate": 22050, "pcm_channels": 1, "max_chars": 800,
           "model": "x", "voice": "y", "note": "n", "validated": None}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry == {"id": "s", "name": "Piper", "kind": "script", "command": "piper -q", "output": "path",
                     "audio_format": "pcm", "pcm_rate": 22050, "pcm_channels": 1, "max_chars": 800,
                     "note": "n", "validated": None, "last_test": None}

@pytest.mark.parametrize("field, value, expected", [
    ("output", "other", "stream"), ("audio_format", "ogg", "mp3"), ("pcm_rate", 0, None),
    ("pcm_rate", 384001, None), ("pcm_rate", True, None), ("pcm_channels", 9, 1), ("pcm_channels", 0, 1),
    ("max_chars", 0, None), ("max_chars", 1.5, None), ("command", 3, ""),
])
def test_script_fields_are_normalized(field, value, expected):
    assert script_fields({field: value})[field] == expected

def test_a_script_validation_snapshot_holds_the_script_fields():
    fields = script_fields({"command": "say", "audio_format": "wav"})
    raw = {"id": "s", "kind": "script", **fields, "validated": {**fields, "kind": "script"}}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry["validated"] == fields
    assert script_config_validated(entry)
    assert not script_config_validated({**entry, "command": "other"})

def test_an_openrouter_snapshot_without_kind_stays_valid():
    # A snapshot written before this change: no kind, the five fields.
    speech = {key: _entry()[key] for key in SPEECH_FIELDS}
    entry = normalize_tts_config({"configs": [_entry(validated=speech)]})["configs"][0]
    assert entry["validated"] == speech

def test_stored_script_config(tmp_data_dir):
    write_tts_config({"configs": [_entry(id="o"), {"id": "s", "kind": "script", "command": "say"}]})
    assert stored_script_config("s")["command"] == "say"
    assert stored_script_config("o") is None
    assert stored_script_config("missing") is None
```

Update every existing exact-dict assertion of a normalized entry in `tests/test_tts_config.py` with `"kind": "openrouter"`.

- [ ] **Step 2: Run them, expect FAIL** — `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_config.py -q` (ImportError on `script_fields`).
- [ ] **Step 3: Implement** in `config.py` (the normalization of `normalize_tts_config` branches on the kind; `_validated(value, kind)` checks the kind's fields).
- [ ] **Step 4: Run the TTS suite, expect PASS** — `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q`, then the lint command.
- [ ] **Step 5: Commit** — `git add src/twicc/tts/config.py tests/test_tts_config.py`, `feat(tts): store script configs in tts.json`.

### Task 2: Output checks and WAV to PCM

**Files:**
- Create: `src/twicc/tts/script_audio.py`
- Test: `tests/test_tts_script_audio.py`

**Interfaces:**
- Consumes: `part_audio.mp3_frame`, `openrouter.SpeechError`, `openrouter.MAX_PCM_RATE`, `MAX_PCM_CHANNELS`.
- Produces: `class OutputCheck(audio_format: str, pcm_rate: int | None, pcm_channels: int)` (a `"pcm"` format without `pcm_rate` raises `ValueError`: a programming error, since the open view refuses such a config first):
  - `feed(chunk: bytes) -> bytes`: the audio bytes to append now (held back until the format is decided; a WAV loses its header). Raises `SpeechError` with the format message on a mismatch.
  - `end() -> bytes`: the output ended: the held bytes; raises `SpeechError` on a mismatch decided only now (an output shorter than the sniff), or `NO_AUDIO` when no audio byte ever came.
  - `content_type: str | None`: set once decided: `"audio/mpeg"`; `"audio/pcm;rate=R;channels=C"` (WAV: from its header; PCM: from the config).
- Rules:
  - **MP3**: decided on the first bytes: `b"ID3"` or a valid frame header (`mp3_frame(head) is not None`) → MP3, else `NOT_MP3`. A leading ID3 tag (`part_audio.id3_tag_size`) is held back until a byte after it arrives: a tag alone is not audio, so an output that is only a tag ends in `NO_AUDIO`. While the output starts with `ID3` and `id3_tag_size` is still None (fewer than 10 bytes), everything is held, as `Mp3HeadFilter._read_id3` does; an output that is only a cut tag header ends in `NO_AUDIO` too. The bytes then pass through unchanged, the tag included.
  - **WAV**: `RIFF`, any size, `WAVE`; then chunks (id, little-endian size, payload, a pad byte after an odd size) until `fmt ` and `data`. `fmt `: format 1, or 0xFFFE with a PCM sub-format (its first two GUID bytes `01 00`); 16 bits, else `WAV_NOT_16BIT`; rate and channels within the PCM bounds, else `WAV_BAD_FORMAT`. A `data` size of 0 or `0xFFFFFFFF` is unknown (a WAV written as it goes): every later byte is a sample. Any other `data` size is honoured: the bytes after it (a trailing `LIST` or `id3 ` chunk) are dropped. No `data` within `WAV_HEADER_MAX_BYTES` → `NOT_WAV`.
  - **PCM**: decided at `SNIFF_BYTES` or at `end()`: a start that looks like MP3 (`ID3`, a frame header), WAV (`RIFF`…`WAVE`), Ogg (`OggS`), FLAC (`fLaC`) or MP4 (`ftyp` at offset 4) fails with its lookalike message; anything else is PCM.

- [ ] **Step 1: Write the failing tests**

```python
def _wav(samples: bytes, *, rate=8000, channels=1, bits=16, fmt=1, data_size=None, extra_chunk=b"") -> bytes: ...
    # RIFF and data sizes 0xFFFFFFFF when data_size is None ("written as it goes")

def _fed(check, data: bytes, step: int) -> bytes:
    out = b"".join(check.feed(data[i:i + step]) for i in range(0, len(data), step))
    return out + check.end()

@pytest.mark.parametrize("step", [1, 3, 64])
def test_a_streamed_wav_becomes_pcm(step):
    check = OutputCheck("wav", None, 1)
    wav = _wav(b"\x01\x02" * 50, extra_chunk=b"LIST\x03\x00\x00\x00abc\x00")
    assert _fed(check, wav, step) == b"\x01\x02" * 50
    assert check.content_type == "audio/pcm;rate=8000;channels=1"

def test_an_extensible_pcm_wav_is_accepted(): ...
def test_a_24_bit_wav_is_refused(): ...           # WAV_NOT_16BIT
def test_a_float_wav_is_refused(): ...            # fmt 3 → WAV_NOT_16BIT
def test_a_wav_with_a_bad_rate_is_refused(): ...  # rate 0 and 400000 → WAV_BAD_FORMAT
def test_a_wav_with_no_samples_is_no_audio(): ... # end() raises NO_AUDIO
def test_a_known_data_size_drops_a_trailing_chunk(): ...  # data size 100, then a LIST chunk → exactly the 100 samples bytes
def test_pcm_needs_a_rate(): ...  # OutputCheck("pcm", None, 1) raises ValueError (the open view refuses it first)
def test_text_is_not_mp3():
    with pytest.raises(SpeechError, match="^The script output is not MP3$"):
        _fed(OutputCheck("mp3", None, 1), b"Loading model...\n" + MP3_FRAME, 64)
def test_mp3_with_or_without_id3_passes_through_unchanged(): ...
@pytest.mark.parametrize("step", [1, 64])
def test_an_id3_tag_alone_is_no_audio(step): ...  # feed returns b"" for the whole tag; end() raises NO_AUDIO
def test_a_cut_id3_header_is_no_audio(): ...  # b"ID3\x04\x00" then end() → NO_AUDIO
@pytest.mark.parametrize("head, message", [
    (b"ID3\x04\x00\x00\x00\x00\x00\x00", "The script output looks like MP3: choose MP3"),
    (b"RIFF\x00\x00\x00\x00WAVE", "The script output looks like WAV: choose WAV"),
    (b"OggS" + bytes(8), "The script output looks like Ogg audio, which TwiCC does not play"),
    (b"fLaC" + bytes(8), "The script output looks like FLAC audio, which TwiCC does not play"),
    (bytes(4) + b"ftypM4A ", "The script output looks like MP4 audio, which TwiCC does not play"),
])
def test_pcm_refuses_a_known_container(head, message): ...
def test_pcm_passes_any_other_bytes_with_the_config_type(): ...  # "audio/pcm;rate=22050;channels=2"
def test_a_short_pcm_output_is_decided_at_the_end(): ...       # b"abc" → PCM
def test_an_empty_output_is_no_audio_for_every_format(): ...   # mp3, wav, pcm → NO_AUDIO
```

- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_script_audio.py -q`.
- [ ] **Step 3: Implement `script_audio.py`** (one incremental parser per format; no conversion of the samples).
- [ ] **Step 4: Run, expect PASS**, then the TTS suite and lint.
- [ ] **Step 5: Commit** — `git add src/twicc/tts/script_audio.py tests/test_tts_script_audio.py`, `feat(tts): check script audio output and turn wav into pcm`.

### Task 3: The script runner

**Files:**
- Create: `src/twicc/tts/script.py`
- Modify: `src/twicc/tts/openrouter.py` (`SpeechError` gets `log_message`), `src/twicc/tts/streams.py` (the log line uses it), `src/twicc/terminal.py` (`terminal_environment`)
- Test: `tests/test_tts_script.py`, `tests/test_tts_streams.py` (log message)

**Interfaces:**
- Consumes: Task 2 `OutputCheck`; `parts.split_text`, `parts.FIRST_PART_CHARS`; `part_audio.Mp3HeadFilter`, `PcmFrameTrimmer`; `openrouter.parse_pcm_format`, `PCM_SAMPLE_WIDTH`; the `AudioStream` sink (`begin_part`, `set_headers`, `pace`, `add_part_headers`, `append`, `end_part`, `finish`, `fail`, `wait_for_lookahead`).
- Produces:
  - `SpeechError(message, upstream_status=None, *, log_message=None)`: `log_message` (default: `message`) is what the TTS log line writes. `AudioStream.fail` keeps it; `AudioStream._log` writes it instead of `error`. The ending step and the UI keep `error` (= `message`).
  - `terminal.terminal_environment(base: Mapping[str, str]) -> dict[str, str]`: a copy of *base* with the provider purge, `sanitize_terminal_env` and `provider_env_overlay()` applied, in that order (the steps of the PTY children; they keep their own inline steps).
  - `class ScriptSpeechRequest(NamedTuple)`: `text, command, output, audio_format, pcm_rate, pcm_channels, max_chars` (hashable: the cache key of Task 4).
  - `script_parts(request) -> list[str]`: `[text]` when `max_chars` is None; else `split_text(text, max_chars, first)` with `first = max_chars` for `output == "stream"`, else `min(max_chars, FIRST_PART_CHARS)`.
  - `shell_argv(command: str) -> list[str]`: `[shell, "-l", "-c", command]`, `shell = os.environ.get("SHELL") or "/bin/sh"`. Tests monkeypatch it to `["/bin/sh", "-c", command]`.
  - `async def pump_script_speech(request, stream, parts=None, *, paced=False) -> None`: the same contract as `pump_speech`: parts in order, `await stream.wait_for_lookahead()` before each part after the first, `stream.pace()` after the first headers when *paced*, `finish()` after the last part, a failure fails the stream and runs no next part, a cancellation (release, shutdown) propagates.

**One run** (one part):

1. `stream.begin_part()`. Start `shell_argv(request.command)` with `asyncio.create_subprocess_exec`: stdin, stdout and stderr pipes, `cwd=Path.home()`, `env=terminal_environment(os.environ)`, `start_new_session=True` (its own process group). An `OSError` fails with `COULD_NOT_START`.
2. Concurrently: write the text (UTF-8) to stdin then close it (a command that exits without reading it is not an error: ignore `BrokenPipeError` / `ConnectionResetError`); keep the last `STDERR_TAIL_BYTES` of stderr; read stdout in `READ_CHUNK` reads.
3. **The run ends at the command's exit.** Detect the exit by polling `proc.returncode` every `EXIT_POLL_SECONDS = 0.05`, never by `proc.wait()` or `communicate()`: on Python 3.13 they return only once every pipe is closed (`asyncio/base_subprocess.py`, `_call_connection_lost`), and a background process can hold a pipe open for its whole life. After the exit: keep reading stdout and stderr until EOF or one total deadline of `EXIT_DRAIN_SECONDS` after the exit, whichever comes first (at most one pipe buffer is left: no audio is lost, and a background writer never extends the run); cancel the stdin writer; close the subprocess transport (`proc._transport.close()`, the only close asyncio offers; it does not signal an exited process); then yield once (`await asyncio.sleep(0)`) so that the pipe close callbacks run and no descriptor leaks. The stderr and stdin tasks are never awaited to their EOF.
4. Audio stream and whole audio: each chunk goes through the run's `OutputCheck`, then (split speech only) `Mp3HeadFilter(drop_id3=index > 0)` for MP3 or `PcmFrameTrimmer(channels * PCM_SAMPLE_WIDTH)` for PCM types, then `append`. The headers go out with the **first non-empty audio bytes** that `feed()` or `end()` returns, never when the format is merely decided (a WAV header or an ID3 tag alone is not audio: a header-only output must fail the open step, as the spec requires): first part → `set_headers(content_type, None)` (+ `pace()`), then `append`; later part → `add_part_headers(content_type, None)`, False ends the speech.
5. File path: stdout (at most `PATH_OUTPUT_MAX_BYTES`, else `NOT_A_PATH`) must be one UTF-8 line (one final `\n` allowed), an absolute path, not returned before in this speech (`PATH_REUSED`); after the exit with status 0, the path must be a regular readable file (`FILE_UNREADABLE`) whose size, added to the bytes kept in `stream.data` before this run, fits the cap (`TOO_LARGE`: the cap is per speech, across parts); its bytes go through the same check and filters, read off the event loop.
6. **TwiCC stops the run itself** and fails with its own message for: the size cap (the bytes kept in `stream.data` before this run plus every stdout byte this run has read, appended, held or dropped, above `SCRIPT_MAX_BYTES`: `TOO_LARGE`); an audio stream run silent for `STALL_SECONDS` after its first audio byte (`STALLED`); a file path output above `PATH_OUTPUT_MAX_BYTES` (`NOT_A_PATH`).
   **The stop of a process group**, awaited inside the pump (never scheduled with `call_later`: at shutdown the loop closes and a scheduled signal never fires): `SIGTERM` to the group; then poll `os.killpg(pgid, 0)` every `EXIT_POLL_SECONDS` for up to `STOP_GRACE_SECONDS`; while the group still exists after that (the shell can be gone while a child ignores `SIGTERM`), `SIGKILL` to the group. A cancellation during the grace wait sends `SIGKILL` at once, then propagates. `ProcessLookupError` means the group is gone.
7. **A format mismatch** (`OutputCheck` raises): stop using the output (drain and drop it, the cap still counts), wait for the exit, then apply 8. When the cap stops such a run, the pending format message wins over `TOO_LARGE`: it is the real cause.
8. **The exit status decides first**: a non-zero status that TwiCC did not cause → the last non-empty stderr line (UTF-8, invalid bytes replaced, stripped, cut to `ERROR_LINE_CHARS` with `…`), else `SCRIPT_FAILED` (a negative status: `SCRIPT_KILLED`); its `log_message` is `script exit status <status>`. A status 0 → the pending format message if any, else `OutputCheck.end()` (its `NO_AUDIO` or mismatch), else success: `end_part()`.
9. `finally`, on every path (success, failure, cancellation, error): a process still running is stopped as in 6 (a process that exited is never signalled: its leftovers stay); the stdin and stderr tasks are cancelled; the transport is closed and the loop yields once, as in 3.

- [ ] **Step 1: Write the failing tests** (`tests/test_tts_script.py`; `shell_argv` monkeypatched; helper scripts run with `sys.executable`; every await bounded by `asyncio.timeout`)

```python
def test_script_parts_follow_the_output_kind(): ...
    # max None → [text]; "stream" max 100 → full parts; "whole" / "path" max 1000 → growing (250, 500, …)
def test_the_text_reaches_stdin_unchanged(tmp_path):
    # command "cat", PCM 8000/1, text f"$(touch {tmp_path}/injected) `touch {tmp_path}/injected2`; echo x"
    # → stream.data == the UTF-8 text; neither file exists (never a destructive command in a test)
def test_a_streamed_wav_plays_as_pcm(): ...      # a python helper writes a WAV with unknown sizes in 3 flushes
def test_audio_streams_before_the_command_exits(): ...  # first chunk appended while the process still runs
def test_a_banner_before_the_audio_fails_with_the_format_message(): ...  # "echo hi; cat f.mp3", MP3 → NOT_MP3
def test_a_failing_command_shows_its_last_stderr_line():
    # "echo one >&2; echo 'sh: piper: not found' >&2; exit 127" → error "sh: piper: not found",
    # the log line has "script exit status 127" and no "piper"
def test_a_failing_command_without_stderr_shows_its_status(): ...  # "exit 3" → "The script failed (exit status 3)"
def test_a_mismatch_of_a_failing_command_shows_stderr(): ...  # text on stdout, error on stderr, exit 2 → stderr line
def test_stderr_of_a_successful_command_is_ignored(): ...  # noise on stderr, exit 0, good audio → complete
def test_an_empty_output_is_no_audio(): ...
def test_a_long_text_while_the_command_writes_first():
    # helper writes 1 MB of PCM, then reads all stdin; text of 2 MB → complete, no deadlock
def test_a_background_child_keeping_stdout_open_does_not_hold_the_run():
    # "sleep 30 & cat f.pcm" (PCM) → complete within 2 s; the test process holds no more fds after the run
    # than before it (count os.listdir("/dev/fd"))
def test_a_failing_command_with_a_background_child_fails_at_once():
    # "sleep 30 & echo err >&2; exit 3" → error "err" within 2 s
def test_a_release_stops_the_whole_pipeline(tmp_path):
    # f"echo $$ > {tmp_path}/pgid; sleep 30 | sleep 30" (its shell leads the new group: pgid = $$);
    # wait for the file, cancel the pump → os.killpg(pgid, 0) raises ProcessLookupError within 3 s
def test_a_release_forces_a_child_that_ignores_sigterm(tmp_path, monkeypatch):
    # STOP_GRACE_SECONDS = 0.2, f"echo $$ > {tmp_path}/pgid; ( trap '' TERM; sleep 30 ) | cat": cancel → the group is gone within 1 s
def test_a_header_only_wav_fails_the_open():
    # a WAV header with no sample, exit 0 → stream.ready raises NO_AUDIO (the open step fails, no headers set)
def test_shell_argv(monkeypatch):
    # SHELL=/bin/zsh → ["/bin/zsh", "-l", "-c", "say hi"]; SHELL unset → ["/bin/sh", "-l", "-c", "say hi"]
def test_the_size_cap_stops_the_command(monkeypatch): ...  # SCRIPT_MAX_BYTES = 1000, "yes" as PCM → TOO_LARGE, process gone
def test_the_cap_counts_dropped_output(monkeypatch): ...  # SCRIPT_MAX_BYTES = 1000, "yes" as MP3 → NOT_MP3, process gone
def test_a_silent_audio_stream_is_stopped(monkeypatch): ...  # STALL_SECONDS = 0.2: 100 bytes (≥ SNIFF_BYTES) then sleep → STALLED
def test_a_slow_start_is_not_a_stall(monkeypatch): ...       # STALL_SECONDS = 0.2: sleep 0.5 then bytes → complete
def test_a_file_path_is_read_after_the_exit(tmp_path): ...    # writes an MP3 file, prints its path → MP3 audio
@pytest.mark.parametrize("stdout, message", [
    ("relative.mp3\n", NOT_A_PATH), ("/a\n/b\n", NOT_A_PATH), ("/no/such/file\n", FILE_UNREADABLE),
])
def test_a_bad_path_fails(stdout, message): ...
def test_a_path_returned_twice_fails(tmp_path): ...  # two parts, same path → PATH_REUSED
def test_an_empty_returned_file_is_no_audio(tmp_path): ...
def test_a_returned_file_above_the_cap_is_too_large(tmp_path, monkeypatch): ...  # SCRIPT_MAX_BYTES = 1000, a 2,000-byte file
def test_an_endless_path_output_is_stopped(monkeypatch): ...  # PATH_OUTPUT_MAX_BYTES = 100, "yes /a" → NOT_A_PATH, process gone
def test_the_parts_run_one_after_the_other_into_one_stream(): ...  # 3 parts, MP3 head filter applied, same type
def test_a_later_part_of_another_type_fails(): ...  # WAV 8000 then WAV 16000 → PART_TYPE_CHANGED
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_streamed_wav_is_parsed():
    # "ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=1 -ac 1 -ar 8000 -f wav -", WAV
    # (-nostdin: ffmpeg would read the speech text as keyboard input)
    # → "audio/pcm;rate=8000;channels=1", 16,000 bytes of samples
```

Log assertions (here and in `tests/test_tts_script.py`) route the `twicc.tts.streams` logger to caplog as `tests/test_tts_streams.py:47` does: `settings_test` can disable it. In `tests/test_tts_streams.py`: `test_the_log_line_writes_the_log_message`: `fail(SpeechError("shown", log_message="logged"))` → `ending_of` error `"shown"`, the log line has `error='logged'` and no `shown`.

- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_script.py tests/test_tts_streams.py -q`.
- [ ] **Step 3: Implement** `SpeechError.log_message`, the log line change, `terminal_environment`, then `script.py`.
- [ ] **Step 4: Run the TTS suite three times (timing tests), expect PASS**, then lint.
- [ ] **Step 5: Commit** — `git add src/twicc/tts/script.py src/twicc/tts/openrouter.py src/twicc/tts/streams.py src/twicc/terminal.py tests/test_tts_script.py tests/test_tts_streams.py`, `feat(tts): run script configs as the speech pump`.

### Task 4: The open step of a script speech

**Files:**
- Modify: `src/twicc/tts/views.py`, `src/twicc/tts/audio_cache.py` (the key type annotation and docstring: a `SpeechRequest` or a `ScriptSpeechRequest`)
- Test: `tests/test_tts_views.py`

**Interfaces:**
- Consumes: Task 1 `stored_script_config`, `script_config_validated`, `script_fields`; Task 3 `ScriptSpeechRequest`, `script_parts`, `pump_script_speech`.
- Produces: the open body of a script speech: `{"kind": "script", "text", "script_id", <the six script fields>, "cache"?, "config_id"?, "buffered"?}`. `config_id` (equal to `script_id`) marks a settings test, as today. The OpenRouter path is unchanged.

Rules, in order:
1. `text` as today (`'text' must be a non-empty string`, `NOTHING_TO_SAY`); `script_id` a non-empty string (400 otherwise); a `config_id` other than `script_id` is a 400.
2. The stored entry (`stored_script_config`, off the event loop): None → 404 `NO_SUCH_SCRIPT`.
3. A test: `script_fields(body) != script_fields(entry)` → 409 `NOT_SAVED`, nothing runs. A message read: `not script_config_validated(entry)` → 409 `NOT_VALIDATED`, nothing runs.
3b. A stored PCM config without `pcm_rate` (a stale tab, a direct request) → 400 `NO_PCM_RATE = "This script config needs a sample rate for PCM"`, nothing runs.
4. No OpenRouter key needed. The request is built from the **stored** fields: `ScriptSpeechRequest(text, **script_fields(entry))`.
5. Label: `{"kind": "script", "output": …, "format": audio_format, "chars": len(text)}`.
6. Cache, parts, pacing (`len(parts) > 1 and not buffered`), the open wait and its errors (a `SpeechError` → 502 with its message), the end steps (cache, last test with `generation_id ""` and `cost None`; no cost lookup): as for OpenRouter. Factor the shared tail of `tts_speech` instead of copying it; `_end_steps` takes `api_key: str | None`.

- [ ] **Step 1: Write the failing tests.** A `script_upstream` fixture monkeypatches `views.pump_script_speech` with the `FakeSpeech` pattern and records `(request, parts, paced)`; it also fakes `views._record_last_test` and `views.schedule_cost_log` as the `upstream` fixture does (`tests/test_tts_views.py:137-150`), so nothing writes or broadcasts for real. A local `tts_json` fixture removes `get_tts_config_path()` before and after the test (as `tmp_data_dir` does in `tests/test_tts_config.py:22-28`); script configs are written with `write_tts_config`.

```python
def test_a_script_speech_runs_the_stored_command(client, script_upstream): ...
    # a message read whose body carries command "evil"; stored "say", validated → the pump gets command "say"
def test_a_script_speech_needs_no_key(client, script_upstream, monkeypatch): ...  # openrouter_api_key → None: 200
def test_an_unknown_script_is_404(client, script_upstream): ...
def test_a_test_with_unsaved_fields_is_409_and_runs_nothing(client, script_upstream): ...
def test_a_test_of_unvalidated_saved_fields_runs(client, script_upstream): ...
def test_a_message_read_of_an_unvalidated_script_is_409(client, script_upstream): ...
def test_a_config_id_other_than_the_script_id_is_400(client, script_upstream): ...
def test_a_script_test_records_its_run_without_cost(client, script_upstream): ...  # cost None, no cost lookup
def test_a_script_message_read_uses_the_cache(client, script_upstream): ...  # second read: no pump call
def test_a_script_test_never_reads_the_cache(client, script_upstream): ...  # a cached read, then a test: the pump runs
def test_a_changed_command_misses_the_cache(client, script_upstream): ...
def test_a_pcm_script_without_a_rate_is_400(client, script_upstream): ...
def test_script_parts_and_pacing_reach_the_pump(client, script_upstream): ...  # max_chars 300 on 900 chars → paced; buffered → not
def test_the_log_label_has_no_command(client, script_upstream, streams_log): ...
def test_an_openrouter_body_with_kind_openrouter_is_unchanged(client, upstream): ...
```

- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_views.py -q`.
- [ ] **Step 3: Implement** in `views.py` (dispatch on `isinstance(data, dict) and data.get("kind") == "script"` before `parse_speech_request`: a non-object body still reaches `parse_speech_request` and its 400).
- [ ] **Step 4: Run the TTS suite, expect PASS**, then lint.
- [ ] **Step 5: Commit** — `git add src/twicc/tts/views.py src/twicc/tts/audio_cache.py tests/test_tts_views.py`, `feat(tts): open script speeches from the stored config`.

### Task 5: Frontend helpers

**Files:**
- Modify: `frontend/src/utils/ttsConfig.js`
- Test: `frontend/src/utils/ttsConfig.test.js`; `frontend/src/stores/textToSpeech.test.js` (expected values only)

`speechFieldsOf` now adds `kind`, so every open body and every validated snapshot carries `kind: 'openrouter'`. Update the expected values that this breaks: `ttsConfig.test.js:41-61` (the new-config shape, "speechFieldsOf keeps only the speech fields"), `textToSpeech.test.js:167-175` (the message read body), `:366-381` and `:383-400` (the iPhone and desktop Safari bodies), `:779` (the validated snapshot). Add `kind: 'openrouter'` to these expected values; change no behaviour test.

**Interfaces:**
- Produces:
  - `SCRIPT_FIELDS = ['command', 'output', 'audio_format', 'pcm_rate', 'pcm_channels', 'max_chars']`, `MAX_PCM_RATE = 384000`, `MAX_PCM_CHANNELS = 8`.
  - `kindOf(config) -> 'openrouter' | 'script'` (`'script'` only for `kind === 'script'`).
  - `speechFieldsOf(config)`: `{ kind, ...the fields of its kind }`.
  - `sameSpeechFields(a, b)`: false when either is missing; else compares the fields of one kind, taken from whichever argument carries `kind` (`a.kind ?? b.kind`, then `kindOf`): a stored snapshot has no `kind`, and the callers pass it first (`shouldAskAfterTest`) or second (`isConfigValidated`).
  - `createTtsConfig(id)` adds `kind: 'openrouter'`. `createScriptConfig(id)`: `{ id, kind: 'script', name: '', command: '', output: 'stream', audio_format: 'mp3', pcm_rate: null, pcm_channels: 1, max_chars: null, note: '', validated: null }`.
  - `isTtsAvailable`: the active config is validated, and the key is present unless it is a script config.
  - `ttsConfigAutoName`: the script auto name of *Global Constraints*.
  - `scriptStreamingNote(config)`: `'Streaming: the audio starts while it is generated.'` for `stream`; for the two other kinds `'No streaming: the audio starts once its first part is generated.'` with a `max_chars`, else `'No streaming: the audio starts once it is fully generated.'`.
  - `SCRIPT_OUTPUT_OPTIONS`, `SCRIPT_FORMAT_OPTIONS` (`{ value, label }`, the copy of *Global Constraints*).
  - `requestedFormat(fields) -> 'mp3' | 'pcm'`: a script config → `'mp3'` for MP3, else `'pcm'` (WAV and PCM reach the browser as PCM); an OpenRouter config → its `response_format`.
  - `canTestScript(config) -> boolean`: a non-empty trimmed command, and a `pcm_rate` when the format is PCM.
  - `positiveIntegerInput(text, max) -> number | null | undefined`: `''` (after trim) → null; an integer within `1..max` → that number; anything else → undefined (the caller keeps the stored value).
  - `ttsTestStats(lastTest, { priced = true } = {})`, `ttsLastTestFacts(lastTest, { priced = true } = {})`: with `priced: false`, no cost part at all (no price, no "price pending…" / "price unknown").

- [ ] **Step 1: Write the failing tests** — one `test(...)` per produced function: the kind default; the per-kind fields and comparison (an OpenRouter snapshot without `kind` still validates; a script snapshot without `kind` passed first, as `shouldAskAfterTest` does, is compared on the script fields: a different command is not the same); a script field edit through `patchTtsConfig` drops the validation; the script config shape; a duplicate keeps the kind and the script fields; availability without the key for a script; the auto names (`'piper --model x'` → `'Script · piper'`, `'/usr/local/bin/say -v x'` → `'Script · say'`, `''` → `'Unnamed script'`); the three notes; `requestedFormat` for the three script formats and OpenRouter; `canTestScript`; `positiveIntegerInput` (`''`, `'  '`, `'22050'`, `'0'`, `'1.5'`, `'abc'`, `'384001'` with max 384000); the stats and facts without a price.
- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/ttsConfig.test.js`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm test`, expect PASS.**
- [ ] **Step 5: Commit** — `git add frontend/src/utils/ttsConfig.js frontend/src/utils/ttsConfig.test.js frontend/src/stores/textToSpeech.test.js`, `feat(tts): frontend rules for script configs`.

### Task 6: The store

**Files:**
- Modify: `frontend/src/stores/textToSpeech.js`
- Test: `frontend/src/stores/textToSpeech.test.js`

**Interfaces:**
- Consumes: Task 5 `kindOf`, `createScriptConfig`, `requestedFormat`, `speechFieldsOf`.
- Produces:
  - `addConfig(kind = 'openrouter')`: a script kind creates `createScriptConfig(id)`.
  - The open body: `{ text, ...fields, cache, config_id?, script_id?, buffered? }`; `script_id` is set for a script config (the active config's id for `play()`; the `configId` for `playWith()` when `kindOf(fields) === 'script'`); `buffered` follows `buffersSending(requestedFormat(fields), currentPlatform())`.
  - `available` follows the new `isTtsAvailable`.

- [ ] **Step 1: Write the failing tests** — a script message read sends `kind: 'script'`, its fields, `script_id`, `cache: true`, no `config_id`; a script settings test sends `config_id` and `script_id` (equal) and `cache: false`; a WAV script on iPhone sends `buffered: true` (PCM rule), and so does an MP3 script on desktop Safari; `addConfig('script')` adds a script config; a script active config is available with no key.
- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc/frontend && node --test src/stores/textToSpeech.test.js`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm test`, expect PASS.**
- [ ] **Step 5: Commit** — `git add frontend/src/stores/textToSpeech.js frontend/src/stores/textToSpeech.test.js`, `feat(tts): play script configs from the store`.

### Task 7: The settings card

**Files:**
- Modify: `frontend/src/components/settings/TextToSpeechSettings.vue`
- Test: `frontend/src/components/settings/textToSpeechSettings.test.js`

**Interfaces:**
- Consumes: Tasks 5 and 6.

Layout of a script card body (the summary line, the last test block, the name, the note, the Test row, the error and the active-warning callout stay as they are):
- `Command`: a `wa-textarea` (`rows="2"`, `resize="auto"`, monospace), saved trimmed on `change`.
- `Output`: a `SegmentedControl` with `SCRIPT_OUTPUT_OPTIONS`; then the note `scriptStreamingNote(config)` as a `setting-group-hint`.
- `Format`: a `SegmentedControl` with `SCRIPT_FORMAT_OPTIONS`.
- PCM only: `Sample rate (Hz)` and `Channels`, `wa-input` with `inputmode="numeric"`, saved on `change` through `positiveIntegerInput` (an undefined result keeps the stored value and restores it in the input, as the speed fix of `53742cefb` did).
- `Max characters per run`: a `wa-input`, same input rule (max: `Number.MAX_SAFE_INTEGER`), placeholder `Whole text in one run`.
- The help paragraph (exact copy), as a `setting-group-hint`.
- No model, voice, price, OpenRouter format switch, or "View on OpenRouter".
- `canTest(config)`: OpenRouter → today's rule; script → `canTestScript(config)`, a test text and no loading test (no key needed). `testButtonTitle`: the script copy when it cannot test.
- The summary stats and the last test facts pass `{ priced: kindOf(config) !== 'script' }`. The summary stats `title` (today "Last test run: cost per 1,000 characters, per minute of audio, generation time per 1,000 characters, and time to the first audio", `TextToSpeechSettings.vue:330`) becomes, for a script card, `Last test run: generation time per 1,000 characters, and time to the first audio`.
- The section intro and the missing-key callout use the new copy. The single `New config` button becomes `New OpenRouter config` and `New script config` (`addConfig('openrouter')`, `addConfig('script')`), both opening the new card.

- [ ] **Step 1: Write the failing source tests** (the file's pattern: `readFileSync` + regex): the script card branches on `kindOf(config) === 'script'`; the two new-config buttons call `addConfig('openrouter')` and `addConfig('script')`; the help copy and the callout copy are present verbatim; `canTest` calls `canTestScript` for a script; the script branch has no `FilterableSelect` and no `openRouterModelUrl`; the stats calls pass `priced`.
- [ ] **Step 2: Run, expect FAIL** — `cd /home/twidi/dev/twicc-poc/frontend && node --test src/components/settings/textToSpeechSettings.test.js`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `npm test`, then `npm run build`, expect PASS.**
- [ ] **Step 5: Commit** — `git add frontend/src/components/settings/TextToSpeechSettings.vue frontend/src/components/settings/textToSpeechSettings.test.js`, `feat(tts): edit script configs in the settings`.

### Task 8: Live checks (user checkpoint)

After the user restarts the backend (`cd /home/twidi/dev/twicc-poc && uv run ./devctl.py restart back`, the user's operation), the controller asks the user to run these in the UI and reads the backend log after each:

| Check | Command (example) | Expected |
|---|---|---|
| Audio stream, WAV | a streaming engine the user has, or `ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=440:duration=5 -ac 1 -ar 22050 -f wav -` | the sound starts at once; a `kind=script` log line without the command |
| File path, MP3 | a command that writes a unique file in `/tmp` and prints its path | plays after the run; the file stays |
| Split text | max characters 300, a long message | parts in the log, lookahead held |
| Stop a pipeline | `sleep 600 \| cat` (or an engine piped into `sox`) during a reading | no process left (`pgrep -f` on the command) |
| Errors | a typo in the program name; a banner on stdout | the stderr line; the format message |

A check that fails is a bug: fix it with a test, then recheck. Nothing to commit when all pass.
