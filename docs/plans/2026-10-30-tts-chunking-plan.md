# Text-to-speech: splitting long texts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split a long speech text into parts that fit the model's limit, send them to OpenRouter one after the other only as the playback needs them, and write their audio into the one audio stream the browser already reads.

**Architecture:** The open step splits the text (`tts/parts.py`) and starts the existing pump, which now walks the parts: it waits for the lookahead before each next part, checks the audio type, strips the MP3 heads (`tts/part_audio.py`), and appends into the same `AudioStream`. The stream gains part accounting, a byte rate, and a paced `deliverable()` limit that every read follows. The browser changes in three small places: a `buffered` flag in the open body, the settings note wording, and the parts figure.

**Tech Stack:** Django 6 async views, `StreamingHttpResponse`, `httpx.AsyncClient.stream`, `asyncio`; Vue 3, Pinia; `pytest` (`httpx.MockTransport`, `django.test.AsyncClient`), `node --test`; `ffmpeg` / `ffprobe` for the spike.

**Spec:** `docs/plans/2026-10-30-tts-chunking-design.md` (read it first). It extends `docs/plans/2026-10-30-tts-streaming-design.md` (the "streaming spec") and `docs/plans/2026-10-29-text-to-speech-design.md` (the "TTS spec"). The streaming plan `docs/plans/2026-10-30-tts-streaming-plan.md` records the shipped decisions (`RANGE_ANSWER = "full"`, `BUFFERED_FORMATS`, the `410` for a read of a failed stream).

## Global Constraints

- Work on `main` in `/home/twidi/dev/twicc-poc`. No branch, no worktree. Prefix every command with `cd /home/twidi/dev/twicc-poc && `.
- Python tests: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q`. Lint: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/tts tests/test_tts_*.py`. Frontend: `cd /home/twidi/dev/twicc-poc/frontend && npm test`; build: `npm run build` (its output is git-ignored).
- Never call OpenRouter in tests or reviews (Task 1 and Task 8 are the only paid checks). Never open or print `~/.twicc/.env` or the key. Never restart servers, never `migrate`, never `npm install`. The backend restart is the user's.
- Other agents share the checkout: `git add` only the files of the task, by name.
- Backend: `orjson`, never stdlib `json`. `NamedTuple` for immutable data. Every endpoint keeps its own JSON errors.
- Commits: Conventional Commits, lowercase summary, a body, trailer `Co-Authored-By: Claude <exact model name> <noreply@anthropic.com>`. No CHANGELOG entry.
- **Part sizes** (`src/twicc/tts/parts.py`): `MAX_PART_CHARS = 2000`; part size limit = `min(MAX_PART_CHARS, max_chars_of(model))`; growing parts start at `FIRST_PART_CHARS = 250` (capped by the limit) and grow by `PART_GROWTH = 2`; streaming formats use the limit from the first part.
- **Lookahead and pacing** (`src/twicc/tts/streams.py`): `LOOKAHEAD_SECONDS = 60.0`; `PACE_MARGIN = 10.0` seconds, never above `LOOKAHEAD_SECONDS`; `PACE_TICK = 1.0` second (a paced read never waits longer than needed for its next byte, so it never idles 30 s while audio is received).
- **Byte rate** (the pacing and lookahead estimate): PCM `rate * channels * 2`; MP3 the frames' common bitrate, read in the first `RATE_SCAN_BYTES = 64 * 1024` bytes only. No frame there, frames with different bitrates there, or a first part whose head holds `Xing` / `VBRI` (variable bitrate): the rate is unknown for good, and the speech is neither paced nor held by the lookahead.
- **Split PCM parts** are trimmed to whole frames (`channels * PCM_SAMPLE_WIDTH` bytes): a partial last frame would shift every later part.
- **Messages (exact):** `There is nothing to read aloud in this text` (spec). `The audio format changed between two parts of the text` (this plan's wording for a part with another type). Settings note: `No streaming: the audio starts once its first part is generated.`; under the MP3 decision rule the old `No streaming: the audio starts once it is fully generated.` stays.
- **Unchanged:** one audio id; the open / read / ending / release contract; the states and the ending table; the cache rules; `UNCLAIMED_TTL = 30.0`, `SAFETY_TTL = 3 * 3600.0`; `RANGE_ANSWER = "full"`; the `410` read of a failed stream; `BUFFERED_FORMATS`; a text that fits in its first part is sent raw, in one request, delivered as it arrives.
- **No** retry (smaller parts or transient errors), **no** parallel part requests, **no** setting, **no** text rewriting.

### Decision rules (applied in Task 1, before Task 2, and in Task 8)

| Check | Result | Change |
|---|---|---|
| Concatenated MP3 of model M (decode, header duration, seams; the joined file in the browsers, Task 1; the live playback, Task 8) | fails for M | add M to `WHOLE_MP3_MODELS` (`model_catalog.py`): its MP3 is sent whole, as today; PCM unaffected |
| Concatenated PCM (HeyGen; decode, seams, the joined WAV in the browsers) | fails | **stop**: the design does not hold; report |
| MP3 of a checked model is VBR (bitrate changes between frames) | VBR | **stop** and report: the pacing estimate assumes a constant bitrate |
| MP3 of a checked model is CBR, but its first frame holds `Xing` or `VBRI` | CBR with `Xing` / `VBRI` | **stop** and report: the runtime would read it as VBR and drop pacing and lookahead (the stop-cost goal) |
| Android, locked screen or background, either format | fails | raise `PACE_MARGIN` (20, 40, then 60 = `LOOKAHEAD_SECONDS`), recheck; still fails: add the format to `BUFFERED_SENDING.android` (`speechOutcome.js`) |
| ElevenLabs v3, 6,000 characters: gap between parts (direct, Vite, tunnel) | gap | no spec rule: report the backend log line and the timings to the user; change nothing without the user |
| A stop sends no new part; OpenRouter shows only the parts sent | a part after the stop | a bug: fix it, with a test |

## Review Focus

1. **A stop while the pump waits for the lookahead** (no part in flight). Expected: the release cancels the wait, no next part is sent, the log says `released` with the parts sent (Task 5 test `test_release_during_the_lookahead_wait_sends_no_next_part`).
2. **An MP3 whose byte rate cannot be read** (no parsable frame header in the first 64 KiB, frames with different bitrates there, or a `Xing` / `VBRI` first part). Expected: no pacing and no lookahead wait (every part as fast as possible), never a read that waits forever, never a rescan of a growing buffer (Task 4 tests `test_an_unknown_byte_rate_disables_pacing_and_lookahead`, `test_an_unreadable_byte_rate_is_given_up_after_the_scan_size`, `test_frames_with_different_bitrates_make_the_rate_unknown`; Task 5 test `test_a_variable_bitrate_first_part_disables_pacing`).
3. **A media element that re-reads a paced stream from the start** (`RANGE_ANSWER = "full"` turns its range read into a plain read). Expected: it gets every byte up to `deliverable()` at once, then the paced rest (Task 6 test `test_a_second_read_of_a_paced_stream_gets_the_deliverable_bytes_at_once`).
4. **A 50,000-character message.** Expected: the split runs in well under a second, before the open step answers, and loses no word (Task 2 tests `test_a_long_text_splits_quickly`, `test_split_keeps_every_word`).
5. **A later part that comes back empty** (a `200` with no audio byte). Expected: the speech goes on with the next part, and the stream completes (Task 5 test `test_an_empty_later_part_does_not_end_the_speech`; Task 3 test `test_a_part_shorter_than_its_head_is_flushed`).

---

## File Structure

**Backend (create)**
- `src/twicc/tts/parts.py`: the text splitter and the per-model part plan.
- `src/twicc/tts/part_audio.py`: the streaming filters of a part's audio: `Mp3HeadFilter` (drops an MP3 part's info frame and, after the first part, its ID3 tag) and `PcmFrameTrimmer` (keeps whole PCM frames only).
- `tests/test_tts_parts.py`, `tests/test_tts_part_audio.py`.

**Backend (modify)**
- `src/twicc/tts/model_catalog.py`: `WHOLE_MP3_MODELS`, `whole_formats` in `speech_model_facts`.
- `src/twicc/tts/openrouter.py`: `bytes_per_second`; `pump_speech` over parts; `schedule_cost_log` over the parts' generation ids.
- `src/twicc/tts/streams.py`: part accounting, pacing, lookahead, the log line.
- `src/twicc/tts/views.py`: nothing-to-say, the part plan and the `buffered` flag in the open step, the paced read body, the parts and the summed cost in `on_end`.
- `src/twicc/tts/config.py`: `parts` in `_last_test`.
- `tests/test_tts_model_catalog.py`, `tests/test_tts_openrouter.py`, `tests/test_tts_streams.py`, `tests/test_tts_views.py`, `tests/test_tts_config.py`.

**Frontend (modify)**
- `frontend/src/utils/speechOutcome.js` (+ `.test.js`): `BUFFERED_SENDING`, `buffersSending`.
- `frontend/src/stores/textToSpeech.js` (+ `.test.js`): `buffered: true` in the open body.
- `frontend/src/utils/ttsConfig.js` (+ `.test.js`): the note wording, the parts figure. `TextToSpeechSettings.vue` already renders both helpers: no change.

---

### Task 1: Concatenation checks on real parts (paid spike)

The spec's MP3 and PCM concatenation checks, before any code: an automatic decode check, then the joined files in the user's browsers. Task 8 keeps the paced, stop, tunnel and lock-screen checks. Nothing from this task is committed except the decisions section of this plan.

**Files:**
- Create (outside the repo): `<scratch>/tts-chunk-spike/spike.py` and its outputs. `<scratch>` is the session's scratch directory.
- Create (outside the repo): the browser-playable copies in `/home/twidi/.twicc/artifacts/4d40cf22-f398-49d8-9077-841cb8e6b520/tts-chunking-spike/` (the controller session's artifacts directory: the running TwiCC serves it in its Artifacts tab, no backend restart).
- Modify: this plan, section *Check decisions*.

- [ ] **Step 1: Write `spike.py`**

- Run from the repo environment, without `TWICC_DATA_DIR`, so the key comes from TwiCC's own loader: `ensure_env_loaded()` (`twicc.paths`), then `openrouter_api_key()` (`twicc.tts.config`). The script never prints the key, a header, or an exception text that could hold it.
- Text: one fixed French prose text of about 1,000 characters, cut by the script at sentence ends into three parts of about 250, 500 and 250 characters (the growing sizes of a non-streaming model).
- Cases, each with the first voice of the model in `fetch_speech_models()` (free call):

  | `<case>` | Kind | Model | Format |
  |---|---|---|---|
  | `elevenlabs` | non-streaming MP3 | `elevenlabs/eleven-v3` | `mp3` |
  | `minimax` | streaming MP3 | `minimax/speech-2.8-turbo` | `mp3` |
  | `seed` | MP3 with an encoder info frame | `bytedance-seed/seed-audio-1-0` | `mp3` |
  | `heygen` | PCM | `heygen/heygen-voice-1-turbo:free` | `pcm` |

- **Spend cap:** the script refuses to start when the planned total exceeds 4,000 characters, and sends the parts one after the other (never in parallel).
- Per part, record: status, `Content-Type`, `x-generation-id`, time to the headers, time to the last byte, byte count, and for PCM the byte count modulo the frame size (`channels * 2`). Save `<case>/part-<i>.mp3` or `.pcm`.
- **MP3 facts per part** (a small parser in the script): a leading ID3 tag and its size; the first frame's version, bitrate, sample rate, channels; whether that frame holds `Xing`, `Info` or `VBRI`, and a `LAME` string; the set of bitrates over all frames (CBR check).
- Build `<case>/raw.mp3` (plain concatenation) and `<case>/joined.mp3` (the design: drop each part's info frame, and the ID3 tag of parts 2 and 3).
- For `raw` and `joined`:
  - `ffmpeg -v error -i <file> -f null -`: stderr must be empty;
  - the decoded duration (the last `time=` of `ffmpeg -i <file> -f null -`) against the sum of the parts' decoded durations;
  - `ffprobe -v error -show_entries format=duration -of json <file>` (it trusts an info frame);
  - `ffmpeg -i <file> -af silencedetect=noise=-50dB:d=0.5 -f null -`: the silences, and which ones sit at a seam.
- **PCM:** the three `Content-Type` values must be equal; `joined.pcm` is the concatenation of the parts, each trimmed to whole frames (as Task 5 does); decode it with `ffmpeg -v error -f s16le -ar <R> -ac <C> -i joined.pcm -f null -`; its duration is `bytes / (R * C * 2)`, against the sum; silences at seams as above.
- **Cost:** 20 s after the last part, look up every generation id (`GET /api/v1/generation?id=…`, the retry rule of `_log_generation_cost`), print each cost and the total.
- Write `<scratch>/tts-chunk-spike/report.json` and print a summary table.
- Copy to the artifacts directory above: `<case>-joined.mp3` for each MP3 case, and `heygen-joined.wav` (`ffmpeg -f s16le -ar <R> -ac <C> -i joined.pcm heygen-joined.wav`; the WAV only makes the joined PCM playable in a browser). Also copy `seed-raw.mp3`, the plain concatenation, to show the info-frame effect.

- [ ] **Step 2: Run it**

Run: `cd /home/twidi/dev/twicc-poc && uv run python <scratch>/tts-chunk-spike/spike.py --out <scratch>/tts-chunk-spike/out`
Expected: the summary table, a total cost under $1.00.

- [ ] **Step 3: Ask the user to play the joined files; record the results here (plays to the end / gap / error / not tested)**

The user opens the Artifacts tab of the controller session and plays each file to its end.

| File | Chrome | Firefox | Chrome Android | Firefox Android |
|---|---|---|---|---|
| `elevenlabs-joined.mp3` | | | | |
| `minimax-joined.mp3` | | | | |
| `seed-joined.mp3` (and its length against `seed-raw.mp3`) | | | | |
| `heygen-joined.wav` | | | | |

- [ ] **Step 4: Apply the decision rules and write *Check decisions***

A case passes when, for `joined`: no decode error; the decoded and the `ffprobe` durations are within 0.3 s per seam of the sum; no silence longer than 1.0 s at a seam; and every tested browser plays it to the end, with no gap and no error. Then, before Task 2:
- a failing MP3 case: its model goes into `WHOLE_MP3_MODELS` (Task 2 uses the list);
- the MP3 facts per model (ID3, info frame kind, bitrate, CBR): Task 3's filter must handle each kind seen; a CBR model with a `Xing` / `VBRI` first frame hits the stop gate below;
- the PCM parts' byte counts modulo the frame size;
- the ElevenLabs time to the first byte of the 250-character part (expected: a few seconds);
- the total cost.

**Stop gate.** Stop and report when the PCM case fails, when a checked MP3 model is VBR, or when a checked CBR model's first frame holds `Xing` / `VBRI` (see *Decision rules*). Otherwise go on.

---

### Task 2: Text splitting and the whole-MP3 rule

**Files:**
- Create: `src/twicc/tts/parts.py`, `tests/test_tts_parts.py`
- Modify: `src/twicc/tts/model_catalog.py`, `tests/test_tts_model_catalog.py`, `tests/test_tts_openrouter.py` (`_copy_models` spreads `speech_model_facts` into every listed model)

**Interfaces:**
- Consumes: `KNOWN_SPEECH_MODELS`, `max_chars_of` (existing); `SpeechRequest` (`openrouter.py`); the Task 1 list of whole-MP3 models.
- Produces:
  - `model_catalog.WHOLE_MP3_MODELS: frozenset[str]` (Task 1's list; empty when every MP3 case passed).
  - `speech_model_facts(model_id)` gains `"whole_formats": list[str]`: `["mp3"]` for a model in `WHOLE_MP3_MODELS`, else `[]` (also for an unknown model: TwiCC splits it).
  - `parts.MAX_PART_CHARS = 2000`, `parts.FIRST_PART_CHARS = 250`, `parts.PART_GROWTH = 2`.
  - `parts.has_speech(text: str) -> bool`: true when the text holds a letter or a digit (`str.isalnum()` on some character).
  - `parts.split_text(text: str, limit: int, first: int) -> list[str]`.
  - `parts.speech_parts(request: SpeechRequest) -> list[str]`.

**Splitting rules (`split_text`).**
- `len(text) <= first`: return `[text]`, unchanged (sent as today).
- Otherwise walk the text. The size of part *k* (from 0) is `min(limit, first * PART_GROWTH ** k)`. When the rest fits in the size, it is the last part. Else take the window of the next `size + 1` characters (the extra one lets a lookahead see the character after the limit), and cut at the **last** match inside it, in this order (the first rule with a match at a position above 0 wins):
  1. paragraph end `\n[ \t]*\n`: cut at the match start;
  2. sentence end: `[.!?…]` plus closing quotes or brackets, followed by whitespace; or `[。！？]` plus closing marks: cut after it;
  3. clause end: `[,;:—–]` plus closing marks, followed by whitespace; or `[、，；：]`: cut after it;
  4. the last whitespace: cut at it;
  5. else cut at `size` (inside a word).
- The cut never passes `size`. The part is the cut text stripped; the rest is left-stripped.
- A part without speech (`has_speech` false) is dropped, wherever it is.
- No abbreviation list: a cut after "e.g. " is accepted (spec: *Left to the builder*).
- Scan only the window, never the whole rest: the split stays linear in the text length.

**`speech_parts(request)`:** `[request.text]` when the format is `mp3` and the model is in `WHOLE_MP3_MODELS`. Else `limit = min(MAX_PART_CHARS, max_chars_of(model))`; `first = limit` when the model is known and its `streams` holds the format, else `min(limit, FIRST_PART_CHARS)`; then `split_text(text, limit, first)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_parts.py`:
- `test_has_speech`: true for `"a"`, `"3"`, `"é"`, `"你"`; false for `"👍"`, `"…"`, `"| --- |"`, `"```"`, `" \n "`.
- `test_a_text_within_the_first_part_is_one_raw_part`: `split_text("  Hello.  ", 2000, 2000) == ["  Hello.  "]`.
- `test_cut_order`, parametrized, with `limit = first = 20`:
  - `"Aaaa. Bbbb.\n\nCccc dddd eeee"` → `["Aaaa. Bbbb.", "Cccc dddd eeee"]` (paragraph);
  - `"Aaaa, bbbb. Cccc, dddd eeee ffff"` → `["Aaaa, bbbb.", "Cccc, dddd eeee ffff"]` (sentence);
  - `"Aaaa bbbb, cccc dddd eeee"` → `["Aaaa bbbb,", "cccc dddd eeee"]` (clause);
  - `"Aaaa bbbb cccc dddd eeee"` → `["Aaaa bbbb cccc dddd", "eeee"]` (space);
  - `"A" * 25` → `["A" * 20, "A" * 5]` (inside a word).
- `test_cjk_sentence_ends_cut_without_spaces`: `split_text("你好世界。再见朋友。", 6, 6) == ["你好世界。", "再见朋友。"]`.
- `test_parts_without_speech_are_dropped_anywhere`: `split_text("```\n\nHello there.\n\n```\n\n| --- |\n\nBye now.", 15, 15) == ["Hello there.", "Bye now."]`.
- `test_growing_parts`: a 6,000-character text of short sentences, `split_text(text, 2000, 250)`: part *k* is at most `min(2000, 250 * 2 ** k)` characters and more than half of it, except the last part.
- `test_split_keeps_every_word`: for that text, `" ".join(parts).split() == text.split()`.
- `test_a_long_text_splits_quickly`: 200,000 characters of prose split in under 1 s (`time.perf_counter`).
- `test_speech_parts_follow_the_model`, on a 3,000-character text, with `WHOLE_MP3_MODELS` monkeypatched to `frozenset()` (Task 1 may list some of these models):
  - `sesame/csm-1b` `pcm`: every part ≤ 200;
  - `heygen/heygen-voice-1-turbo:free` `pcm` (streams): the first part is more than 250 and at most 2,000;
  - `minimax/speech-2.8-hd` `mp3` (streams, `max_chars` 9999): every part ≤ 2,000;
  - `elevenlabs/eleven-v3` `mp3` (no streaming): the first part ≤ 250;
  - `someone/new-tts` `mp3` (unknown): the first part ≤ 250, the others ≤ 2,000.
- `test_whole_mp3_models_are_sent_whole`: with `WHOLE_MP3_MODELS` monkeypatched to `frozenset({"elevenlabs/eleven-v3"})`: `mp3` gives `[text]`, `pcm` gives several parts.

`tests/test_tts_model_catalog.py`:
- `test_every_known_model_is_consistent`: also every id of `WHOLE_MP3_MODELS` is a known model whose `formats` hold `mp3`.
- `test_known_model_facts_for_the_browser` and `test_an_unknown_model_claims_nothing_but_the_default_length`: the expected dicts gain `"whole_formats": []`; the first one monkeypatches `model_catalog.WHOLE_MP3_MODELS` to `frozenset()`.
- `test_whole_mp3_models_reach_the_browser`: with the monkeypatched set above, `speech_model_facts("elevenlabs/eleven-v3")["whole_formats"] == ["mp3"]`.
- `test_the_model_list_carries_the_facts`: `whole_formats` is `[]` for both models.

`tests/test_tts_openrouter.py`: the exact model dicts gain `"whole_formats": []`: the `unknown` dict of `test_models_are_shaped_cached_and_failures_not_cached`, and the expected list of `test_models_callers_cannot_corrupt_the_cache`.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_parts.py tests/test_tts_model_catalog.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.tts.parts`, missing key `whole_formats`).

- [ ] **Step 3: Implement `parts.py` and the catalog changes**

`speech_parts` reads `WHOLE_MP3_MODELS` through the module (`model_catalog.WHOLE_MP3_MODELS`), so the monkeypatch reaches it.

- [ ] **Step 4: Run them, expect PASS, then lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS (the whole TTS suite), no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/parts.py src/twicc/tts/model_catalog.py tests/test_tts_parts.py tests/test_tts_model_catalog.py tests/test_tts_openrouter.py
git commit  # feat(tts): split long speech texts into parts that fit the model
```

---

### Task 3: Part audio filters (MP3 heads, PCM frames) and the audio byte rate

**Files:**
- Create: `src/twicc/tts/part_audio.py`, `tests/test_tts_part_audio.py`
- Modify: `src/twicc/tts/openrouter.py`, `tests/test_tts_openrouter.py`

**Interfaces:**
- Consumes: `parse_pcm_format`, `_mp3_bitrate`, `PCM_SAMPLE_WIDTH` (existing, `openrouter.py`); the MP3 facts of Task 1.
- Produces:
  - `class Mp3HeadFilter(*, drop_id3: bool)` with `feed(chunk: bytes) -> bytes` (the bytes to append now), `flush() -> bytes` (what it still holds, at the end of the part), and `info_frame: bytes | None` (`b"Xing"`, `b"Info"` or `b"VBRI"` once it dropped one, else `None`; set before `feed` returns the bytes that follow the frame).
  - `class PcmFrameTrimmer(frame_size: int)` with `feed(chunk: bytes) -> bytes` (whole frames only; it holds the partial frame) and `end() -> int` (drops the held bytes at the end of the part, and returns their count).
  - `openrouter.RATE_SCAN_BYTES = 64 * 1024`.
  - `openrouter.bytes_per_second(content_type: str, head: bytes | bytearray) -> float | None`: PCM `rate * channels * PCM_SAMPLE_WIDTH` from the type; MP3 `_mp3_bitrate(head[:RATE_SCAN_BYTES]) / 8`; `None` for another type, an unusable PCM type, or no readable MP3 frame in the scanned bytes. `audio_duration_seconds` keeps using it as today.
  - `openrouter.mp3_frame_bitrates(head: bytes | bytearray) -> list[int]`: the bitrates (bit/s) of the consecutive MPEG Layer III frames from the first one (past an ID3v2 tag), walking frame by frame by frame length (the length formula of the filter rules below), within `head[:RATE_SCAN_BYTES]`. It stops at the end of the scanned bytes, at a truncated frame, or at bytes that are not a frame header.
  - `audio_duration_seconds` keeps its behaviour, on top of `bytes_per_second`.

**Filter rules (`Mp3HeadFilter`).** It holds back only the head of the part, then passes every byte through.
- A leading ID3v2 tag (`ID3`, size = 10 + the syncsafe size of bytes 6–9, + 10 when the footer flag `0x10` of byte 5 is set): dropped when `drop_id3`, else passed through. A dropped tag is skipped by count, not buffered.
- The first MPEG audio frame after it: wait for the whole frame (length `144 * bitrate / sample_rate + padding` for MPEG-1, `72 * …` for MPEG-2 and 2.5, Layer III). Drop it when it holds `Xing` or `Info` at its side-info offset (MPEG-1: 36 stereo, 21 mono; MPEG-2/2.5: 21 stereo, 13 mono), or `VBRI` at offset 36. Else pass it.
- No valid frame header where one must start: pass everything through, unchanged.
- Cover every info frame kind Task 1 saw.

**Trimmer rule (`PcmFrameTrimmer`).** The pump gives it `channels * PCM_SAMPLE_WIDTH` from the part's type. Each part of a split PCM text gets a fresh trimmer, so a partial last frame never shifts the next part (the browser decoder carries bytes only inside one stream).

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_part_audio.py`, with a helper `_frame(tag: bytes | None = None, bitrate_index: int = 9) -> bytes` that builds one MPEG-1 Layer III mono frame at 44.1 kHz (index 9: 128 kbit/s, header `FF FB 90 C4`, 417 bytes; index 5: 64 kbit/s, 208 bytes), with `tag` at offset 21, and `_id3(size: int) -> bytes`:
- `test_audio_frames_pass_untouched`: three plain frames come out unchanged.
- `test_first_part_keeps_its_id3_and_drops_its_info_frame`: `drop_id3=False` on `_id3(30) + _frame(b"Info") + _frame()` gives `_id3(30) + _frame()`.
- `test_a_later_part_drops_its_id3_and_its_info_frame`: `drop_id3=True` on `_id3(30) + _frame(b"Xing") + _frame()` gives `_frame()`.
- `test_a_vbri_frame_is_dropped_too` (tag at offset 36).
- `test_the_head_can_arrive_byte_by_byte`: feeding one byte at a time, then `flush()`, gives the same bytes as one `feed`.
- `test_a_part_shorter_than_its_head_is_flushed`: half of a frame, then `flush()`, gives that half; an empty part gives `b""`.
- `test_not_mp3_passes_through`: `b"abcdefgh" * 100` comes out unchanged.
- `test_the_filter_reports_the_info_frame_kind`: `info_frame` is `b"Xing"`, `b"Info"`, `b"VBRI"` for the three dropped kinds, and `None` for plain frames.
- `test_pcm_trimmer_keeps_whole_frames_and_drops_the_tail`: `PcmFrameTrimmer(4)`: `feed(b"abc") == b""`, `feed(b"defgh") == b"abcdefgh"`, `feed(b"ij") == b""`, then `end() == 2`; a part of whole frames ends with `end() == 0`.

`tests/test_tts_openrouter.py`:
- `test_bytes_per_second`: `audio/pcm;rate=44100;channels=1` → `88200.0`; `audio/pcm;rate=24000;channels=2` → `96000.0`; `audio/mpeg` with `_frame()` → `16000.0`, also after an ID3 tag; `audio/mpeg` with `b"xxxx"` → `None`; `audio/mpeg` with `b"x" * RATE_SCAN_BYTES + _frame()` → `None` (beyond the scan); `audio/pcm` → `None`; `audio/ogg` → `None`.
- `test_mp3_frame_bitrates`: `_id3(30) + _frame() + _frame(bitrate_index=5) + _frame()` → `[128000, 64000, 128000]`; a truncated last frame is not counted; `b"xxxx"` → `[]`.
- The existing `test_audio_duration_of_mp3`, `test_audio_duration_of_pcm` and `test_audio_duration_reads_a_bytearray` stay green.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_part_audio.py tests/test_tts_openrouter.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.tts.part_audio`, `AttributeError: bytes_per_second`).

- [ ] **Step 3: Implement `part_audio.py` and `bytes_per_second`**

- [ ] **Step 4: Run them, expect PASS, then lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_part_audio.py tests/test_tts_openrouter.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS, no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/part_audio.py src/twicc/tts/openrouter.py tests/test_tts_part_audio.py tests/test_tts_openrouter.py
git commit  # feat(tts): filter the audio of speech parts and measure its byte rate
```

---

### Task 4: Parts, pacing and lookahead in the audio stream

**Files:**
- Modify: `src/twicc/tts/streams.py`, `tests/test_tts_streams.py`

**Interfaces:**
- Consumes: `bytes_per_second`, `mp3_frame_bitrates`, `RATE_SCAN_BYTES` (Task 3), `SpeechError` (existing).
- Produces, in `streams.py`:
  - Constants `LOOKAHEAD_SECONDS = 60.0`, `PACE_MARGIN = 10.0`, `PACE_TICK = 1.0`, `PART_TYPE_CHANGED = "The audio format changed between two parts of the text"`.
  - New `AudioStream` attributes: `generation_ids: list[str]` (the non-empty ids of every part, in order); `parts_sent: int`; `paced: bool` (default `False`). `generation_id` stays: the first part's id.
  - `begin_part() -> None`: called by the pump just before each part request; increments `parts_sent`. Allowed in `opening` (the first part comes before any headers) and `running`, checked with `_expect` like the other sink methods.
  - `end_part() -> None`: called after the part's last byte; adds the part's request-to-last-byte time to the generation time.
  - `set_headers(content_type, generation_id)` (first part, unchanged contract) also appends the id to `generation_ids`.
  - `add_part_headers(content_type: str, generation_id: str | None) -> bool`: a later part's headers. A type other than `content_type` calls `fail(SpeechError(PART_TYPE_CHANGED))` and returns `False`; else it appends the id and returns `True`.
  - `finish()`: `generation_ms` is the summed part time when `parts_sent > 0`, else the time since creation (a pump that marks no part is one part, as before).
  - `pace() -> None`: sets `paced`.
  - `byte_rate() -> float | None`: PCM: `bytes_per_second(content_type, data)`, kept once known. MP3: decided once, when `len(data) >= RATE_SCAN_BYTES`, or at the `end_part()` of a part once `t0` is set (some audio arrived: an empty first part leaves it undecided until a later part ends); before that it returns `None` without deciding (the stream is not paced yet). The decision: `mp3_frame_bitrates(data)` all equal → `bytes_per_second(content_type, data)`, kept; no frame, or different bitrates → `None` for good (no rescan of a growing buffer). `mark_variable_rate()` also makes it `None` for good.
  - `mark_variable_rate() -> None`: the pump calls it when the first part's head holds `Xing` or `VBRI` (a variable bitrate: the first frame's bitrate would mislead the estimate).
  - `deliverable() -> int`: the bytes any read may send now (formula below).
  - `async wait_for_more(offset: int) -> None`: returns when `deliverable() > offset`; or when the state is `released`; or when the state is `complete` / `failed` and `offset >= len(data)`.
  - `async wait_for_lookahead() -> None`: the wait before a next part (formula below); returns at once when not paced, when `byte_rate()` is `None`, or when `t0` is unset (no audio byte yet: for example a first part that came back empty).
  - The log line gains `parts=<parts_sent>` right after the label fields, and `generation=` prints the ids joined by `,` (`None` when there is none).

**Formulas.** `t0` is the time of the first non-empty `append`; `elapsed = now - t0`; `rate = byte_rate()`.
- `deliverable()` = `len(data)` when not paced, `rate` is `None`, or `t0` is unset; else `min(len(data), int((elapsed + PACE_MARGIN) * rate))`. It applies in every state, `complete` and `failed` included: the received audio of a failed stream is still delivered at the paced rate.
- `wait_for_more(offset)`, paced, with `len(data) > offset`: sleep on the wake event with a timeout of `max(PACE_TICK, (offset + 1) / rate - PACE_MARGIN - elapsed)`, then check again. Otherwise wait on the wake event alone, as today. Every `append`, `finish`, `fail` and release still wakes it.
- `wait_for_lookahead()`: `ahead = len(data) / rate - elapsed`; when `ahead > LOOKAHEAD_SECONDS`, one `asyncio.sleep(ahead - LOOKAHEAD_SECONDS)`. No part is in flight, so `data` does not grow during the sleep. A release cancels it (the pump task is cancelled).

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_streams.py`. A PCM type `audio/pcm;rate=1000;channels=1` gives 2,000 bytes per second. Where pacing is tested, monkeypatch `PACE_MARGIN = 0.1`, `PACE_TICK = 0.05`, `LOOKAHEAD_SECONDS = 0.2`. `FakePump` already calls any sink method by name (`pump.send("begin_part")`, `pump.send("pace")`).
- `test_parts_sum_their_generation_time`: two parts of about 50 ms each, with a 200 ms pause between them: `generation_ms` is at least 100 and below 250.
- `test_every_part_brings_its_generation_id`: `begin_part()`, `set_headers(…, "g1")`, `begin_part()`, `add_part_headers(…, "g2")`, `begin_part()`, `add_part_headers(…, None)`: `generation_ids == ["g1", "g2"]`, `generation_id == "g1"`, `parts_sent == 3`.
- `test_begin_part_is_allowed_while_opening_or_running_only`: the first `begin_part()` in `opening` passes; after `finish()`, `begin_part()` raises `RuntimeError`.
- `test_a_later_part_with_another_type_fails_the_stream`: first `audio/pcm;rate=44100;channels=1`, then `add_part_headers("audio/pcm;rate=24000;channels=1", None)` returns `False`; the state is `failed`, `ending_of` gives `{"state": "failed", "error": PART_TYPE_CHANGED}`.
- `test_an_unpaced_stream_delivers_everything_received`: `deliverable() == len(data)`.
- `test_a_paced_stream_delivers_up_to_the_position_plus_the_margin`: 2,000 bytes appended at once: `deliverable()` is between 200 and 400 at once, and 2,000 after 1 s.
- `test_a_paced_reader_wakes_as_the_position_advances`: with 2,000 bytes received and no new append, `wait_for_more(deliverable())` returns within 0.3 s.
- `test_a_finished_paced_stream_still_paces_then_ends`: after `finish()`, `deliverable() < len(data)` at once; `wait_for_more(deliverable())` returns only as the position advances; `wait_for_more(len(data))` returns at once (nothing more comes).
- `test_wait_for_lookahead_sleeps_until_the_audio_ahead_drops_below_the_lookahead`: 1,000 bytes (0.5 s) appended: the wait lasts between 0.25 and 0.6 s.
- `test_an_unknown_byte_rate_disables_pacing_and_lookahead` (Review Focus 2): a paced `audio/mpeg` stream with `b"xxxx" * 1000`: `deliverable() == len(data)`, `wait_for_lookahead()` returns at once, `wait_for_more(len(data) - 1)` returns at once.
- `test_an_unreadable_byte_rate_is_given_up_after_the_scan_size` (Review Focus 2): `streams.mp3_frame_bitrates` monkeypatched with a counting wrapper; a paced `audio/mpeg` stream gets `b"x" * RATE_SCAN_BYTES`, then a valid frame: `byte_rate()` stays `None`, and the wrapper ran once over all calls.
- `test_a_variable_rate_stream_is_not_paced`: a paced `audio/mpeg` stream with valid frames, after `mark_variable_rate()`: `byte_rate()` is `None`, `deliverable() == len(data)`.
- `test_frames_with_different_bitrates_make_the_rate_unknown` (Review Focus 2): a paced `audio/mpeg` stream gets `_frame(bitrate_index=5) + _frame()` (from `tests.test_tts_part_audio`), then `end_part()`: `byte_rate()` is `None`, `deliverable() == len(data)`; with two `_frame()` instead, `byte_rate() == 16000.0`.
- `test_an_mp3_rate_is_not_decided_before_the_scan_size_or_the_part_end`: one `_frame()` received, the first part still running: `byte_rate()` is `None`; after `end_part()`: `16000.0`. An empty first part and its `end_part()`: `None`, still undecided; then a part with `_frame()` and its `end_part()`: `16000.0`.
- `test_wait_for_lookahead_without_audio_returns_at_once`: a paced PCM stream with headers and no byte.
- `test_one_log_line_per_speech_without_the_key`: also asserts `parts=` in each line, and `generation=g1,g2` for a two-part speech.
- The existing tests stay green (the fallback of `finish()` keeps `test_first_audio_and_generation_times_are_measured` valid).

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_streams.py -q`
Expected: FAIL (`AttributeError: begin_part`, `deliverable`, …).

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the whole TTS suite, expect PASS, then lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS, no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/streams.py tests/test_tts_streams.py
git commit  # feat(tts): pace split audio streams and wait for the lookahead
```

---

### Task 5: The pump over parts, and the summed cost

**Files:**
- Modify: `src/twicc/tts/openrouter.py`, `tests/test_tts_openrouter.py`

**Interfaces:**
- Consumes: Task 3 (`Mp3HeadFilter`, `PcmFrameTrimmer`), Task 4 (`begin_part`, `end_part`, `add_part_headers`, `pace`, `mark_variable_rate`, `wait_for_lookahead`).
- Produces:
  - `async def pump_speech(request: SpeechRequest, api_key: str, stream: AudioStream, parts: Sequence[str] | None = None, *, paced: bool = False) -> None`. `parts=None` means `[request.text]`: today's behaviour, byte for byte.
  - `schedule_cost_log(generation_ids: list[str], api_key: str, on_cost=None) -> None`: one `_log_generation_cost` per id, in parallel (`asyncio.gather`), each logging its own line; `on_cost(total)` once, only when every lookup found a cost.

**Pump rules.** One `_make_client(SPEECH_TIMEOUT)` for all parts. For each part, in order:
- before a part after the first: `await stream.wait_for_lookahead()`;
- `stream.begin_part()`, then the request with `build_payload(request._replace(text=part))`;
- an upstream error or a non-audio success fails the stream as today (`upstream_error_message`, with the status), and sends no next part: before the headers for the first part, while `running` for a later one;
- first part: `stream.set_headers(type, id)`, then `stream.pace()` when `paced`; later part: `stream.add_part_headers(type, id)`, and stop when it returns `False`. The PCM type is made explicit (`pcm_content_type`) before both;
- with more than one part, every chunk goes through a fresh filter per part, and a single part is never filtered:
  - `audio/mpeg`: `Mp3HeadFilter(drop_id3=index > 0)`; its `flush()` is appended at the part's end. On the first part, once `info_frame` is `b"Xing"` or `b"VBRI"`, call `stream.mark_variable_rate()` before appending the bytes `feed` returned;
  - `audio/pcm`: `PcmFrameTrimmer(channels * PCM_SAMPLE_WIDTH)`, with `channels` from `parse_pcm_format` of the explicit type; `end()` at the part's end (a non-zero count is logged at debug level);
- `stream.end_part()` after the part's last byte;
- `httpx.TimeoutException` / `httpx.HTTPError`: `_fail_if_live` with the two current messages; a `CancelledError` propagates (the release closes the part in flight).
- `stream.finish()` after the last part.

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_openrouter.py`, with `_mock_transport` and `_QueueBody`. The handler records each request's `input`.
- `test_pump_sends_the_parts_one_after_the_other`: three parts; the second request arrives only after the first body closes; `stream.data` is the three bodies joined; `parts_sent == 3`; `generation_ids` holds the three ids; the state is `complete`.
- `test_pump_paces_only_when_asked`: `paced=True` sets `stream.paced`; `paced=False` does not.
- `test_pump_waits_for_the_lookahead_before_each_next_part`: `stream.wait_for_lookahead` replaced by a recorder: called twice for three parts, never before the first request.
- `test_a_later_part_error_fails_the_running_stream`: part 2 answers `429 {"error":{"message":"Rate limited"}}`: the state is `failed`, `error == "Rate limited"`, `upstream_status == 429`, `data` holds part 1, and part 3 is never requested.
- `test_a_later_part_network_error_fails_with_its_message` (`httpx.ConnectError` on part 2 → `Could not reach OpenRouter`).
- `test_a_later_part_with_another_type_fails`: part 1 `audio/pcm;rate=44100;channels=1`, part 2 `audio/pcm;rate=24000`: `failed` with `PART_TYPE_CHANGED`, no part 3.
- `test_pump_strips_mp3_heads_of_split_texts_only`: with `from tests.test_tts_part_audio import _frame, _id3`: two parts each `_id3(30) + _frame(b"Info") + _frame()` give `_id3(30) + _frame() + _frame()`; the same body as a single part comes out unchanged.
- `test_a_variable_bitrate_first_part_disables_pacing` (Review Focus 2): `paced=True`, two parts whose first holds `_frame(b"Xing")`: `stream.byte_rate()` is `None` and `deliverable() == len(data)`; with `_frame(b"Info")` instead, `byte_rate() == 16000.0`.
- `test_pump_trims_split_pcm_parts_to_whole_frames`: type `audio/pcm;rate=24000;channels=2` (4-byte frames), part 1 sent as chunks of 3 and 4 bytes, part 2 of 8 bytes: `data` is part 1's first 4 bytes, then part 2's 8 bytes; a single part of 7 bytes comes out unchanged.
- `test_release_during_the_lookahead_wait_sends_no_next_part` (Review Focus 1): `wait_for_lookahead` replaced by a wait on an event that never fires; `release_stream(stream.id)` during it: the pump sees `CancelledError`, one request only, one log line with `outcome=released` and `parts=1` (route `twicc.tts.streams` to `caplog`, as the `streams_log` fixture of `tests/test_tts_streams.py` does). The stream comes from `start_stream`, so `release_stream` finds it.
- `test_an_empty_later_part_does_not_end_the_speech` (Review Focus 5): part 2 is a `200` `audio/mpeg` with an empty body; part 3 arrives; the state is `complete`.
- `test_schedule_cost_log_runs_in_the_background`: updated to `schedule_cost_log(["gen-1"], "k")`.
- `test_schedule_cost_log_sums_the_parts`: fake costs `0.001`, `0.002`, `0.003`: `on_cost` gets `pytest.approx(0.006)`, once.
- `test_schedule_cost_log_gives_no_total_when_a_cost_is_missing`: one lookup returns `None`: `on_cost` is not called; the other two were looked up.
- The existing pump tests stay green with `parts=None`.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_openrouter.py -q`
Expected: FAIL (`TypeError` on the `parts` argument, the list signature of `schedule_cost_log`).

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the whole TTS suite, expect PASS, then lint**

`views.py` still calls `schedule_cost_log(generation_id, …)` until Task 6: update that one call here to `schedule_cost_log([generation_id], …)`, and the `Upstream.schedule_cost_log` fake of `tests/test_tts_views.py` to the list signature (record `tuple(generation_ids)`, and update the assertions on `upstream.cost` to that form), so the suite stays green.

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS, no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/openrouter.py src/twicc/tts/views.py tests/test_tts_openrouter.py tests/test_tts_views.py
git commit  # feat(tts): send split speech texts part by part
```

---

### Task 6: The open and read steps of split texts

**Files:**
- Modify: `src/twicc/tts/views.py`, `src/twicc/tts/config.py`, `tests/test_tts_views.py`, `tests/test_tts_config.py`

**Interfaces:**
- Consumes: Task 2 (`has_speech`, `speech_parts`), Task 4 (`deliverable`, `wait_for_more`, `parts_sent`, `generation_ids`), Task 5 (`pump_speech(..., parts, paced=...)`, `schedule_cost_log(list, ...)`).
- Produces, the HTTP contract for Task 7:
  - **Open** body gains `buffered: bool` (default `false`). A text without speech is a `400 {"error": "There is nothing to read aloud in this text"}`, with no upstream call.
  - The last test record gains `parts: int`. `config._last_test` keeps `parts` as a non-negative int, else `None`.
  - `views.NOTHING_TO_SAY = "There is nothing to read aloud in this text"`.

**Rules.**
- `parse_speech_request` raises `ValueError(NOTHING_TO_SAY)` when `has_speech(text)` is false (after the non-empty check). A whitespace-only text keeps its current message.
- Open: `buffered = data.get("buffered") is True`; `parts = speech_parts(speech_request)`; `paced = len(parts) > 1 and not buffered`; the pump is `pump_speech(speech_request, api_key, sink, parts, paced=paced)`. The cache lookup and key are unchanged (the whole text plus the speech fields).
- `_audio_body`: it sends up to `stream.deliverable()` instead of `len(stream.data)` (a range body keeps its fixed `last`). It ends on `released`; on `complete` once `offset >= len(stream.data)`; on `failed` once `offset >= len(stream.data)`, with the current `ConnectionAbortedError`. The range branch (`RANGE_ANSWER == "partial"`) counts `deliverable()` as the received bytes.
- `on_end` of a complete stream: `parts = max(stream.parts_sent, 1)` (a pump that marks no part is one part, the rule of `finish()` in Task 4). The record adds `"parts": parts`. The cost lookup runs when `stream.generation_ids` is not empty, with `list(stream.generation_ids)`; its scheduling-error log line prints the ids joined by `,`, like the stream log line (`TTS cost lookup for gen-3 could not be scheduled` for one id, so `test_on_end_steps_fail_independently` keeps its assertion); its `on_cost` (a settings test only, and only when `len(stream.generation_ids) == parts`) records `{"generation_id": stream.generation_id, "cost": total}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tts_views.py`. The `upstream` fixture's `pump_speech` lambda takes `(request, api_key, stream, parts=None, *, paced=False)` and records `(parts, paced)` in `fake.pump_args`. Pacing tests monkeypatch `streams.PACE_MARGIN = 0.1` and `streams.PACE_TICK = 0.05`, use `audio/pcm;rate=1000;channels=1`, and drive the fake with `send("pace")`.
- `test_parse_speech_request_rejects`: a new case `{"text": "👍", …}` → `NOTHING_TO_SAY`.
- `test_a_text_with_nothing_to_say_is_400_without_upstream`: `"…"` → `400 {"error": "There is nothing to read aloud in this text"}`; `upstream.pump.calls == []`.
- `test_open_passes_the_parts_and_pacing_to_the_pump`: `views.speech_parts` monkeypatched to return `["a", "b"]`: `(["a", "b"], True)`; with `buffered: true`: `(["a", "b"], False)`; one part: `([text], False)`.
- `test_a_paced_read_delivers_the_audio_as_the_playback_advances`: 2,000 bytes then `finish`: the first chunk holds between 200 and 600 bytes; the whole body arrives within 2 s and equals the 2,000 bytes.
- `test_a_second_read_of_a_paced_stream_gets_the_deliverable_bytes_at_once` (Review Focus 3): after 0.5 s of a paced read, a second plain read gets at least `deliverable()` bytes (as of its start) within 0.1 s.
- `test_a_later_part_failure_delivers_the_received_audio_then_cuts`: 2,000 bytes, then `fail(SpeechError("Rate limited", upstream_status=429))`: the open read yields the 2,000 bytes, then raises `ConnectionAbortedError`; `ending` gives `{"state": "failed", "error": "Rate limited"}`; a new read is `410` with the cut message.
- `test_a_split_run_records_its_parts_and_the_summed_cost`: a settings test, three parts (`begin_part`, `set_headers(…, "g1")`, …, `add_part_headers(…, "g2")`, `add_part_headers(…, "g3")`): the record has `parts == 3` and `generation_id == "g1"`; `upstream.cost == [(("g1", "g2", "g3"), "k")]`; calling the stored `on_cost(0.006)` records `("cfg-1", {"generation_id": "g1", "cost": 0.006})`.
- `test_a_part_without_generation_id_records_no_cost`: three parts, ids for two: the lookup runs for two, its `on_cost` is `None`.
- `test_a_settings_test_records_its_run_then_its_cost`: the scenario sends `("begin_part",)` before `set_headers` (as the real pump does); the expected record keys gain `"parts"`, with `run["parts"] == 1`; the cost assertions keep working.
- `test_a_pump_without_part_marks_counts_as_one_part`: the same scenario without `begin_part`: `run["parts"] == 1`, and the stored `on_cost` is not `None`.

`tests/test_tts_config.py`:
- `LAST_TEST` gains `"parts": 3`.
- `test_last_test_keeps_parts`: `3` kept; `-1` and `"x"` give `None`; a record without it gives `None` and stays valid.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_views.py tests/test_tts_config.py -q`
Expected: FAIL (no `NOTHING_TO_SAY`, no `pump_args`, no `parts` key).

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the whole TTS suite, expect PASS, then lint**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q && uvx ruff check src/twicc/tts tests/test_tts_*.py`
Expected: PASS, no ruff error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/tts/views.py src/twicc/tts/config.py tests/test_tts_views.py tests/test_tts_config.py
git commit  # feat(tts): open, pace and record split speeches
```

---

### Task 7: Frontend: buffered sending, settings note, parts figure

**Files:**
- Modify: `frontend/src/utils/speechOutcome.js`, `frontend/src/utils/speechOutcome.test.js`, `frontend/src/stores/textToSpeech.js`, `frontend/src/stores/textToSpeech.test.js`, `frontend/src/utils/ttsConfig.js`, `frontend/src/utils/ttsConfig.test.js`

**Interfaces:**
- Consumes: the Task 6 contract (`buffered`, `parts`), `whole_formats` (Task 2), `BUFFERED_FORMATS`, `currentPlatform` (existing).
- Produces:
  - `speechOutcome.BUFFERED_SENDING = {}`: the platforms (keys of `currentPlatform`) and formats that ask for buffered sending while they keep their streaming player (the Android decision rule; Task 8 fills it if needed).
  - `speechOutcome.buffersSending(responseFormat, platform) -> boolean`: true when `BUFFERED_FORMATS[platform]` or `BUFFERED_SENDING[platform]` holds `responseFormat`. It reads the requested format: the open step must ask before it knows the audio type.
  - The open body gains `buffered: true` only when `buffersSending(fields.response_format, currentPlatform())`; otherwise the body is unchanged (no `buffered` key).
  - `ttsStreamingNote(model, format)`: streaming → unchanged; not streaming → `No streaming: the audio starts once its first part is generated.`, except when `model.whole_formats` holds the format → `No streaming: the audio starts once it is fully generated.`; unknown model → `''`.
  - `ttsLastTestFacts(lastTest)`: with `parts > 1`, `· N parts` right after the characters. `ttsTestStats` is unchanged.

- [ ] **Step 1: Write the failing tests**

`speechOutcome.test.js`:
- `test('buffersSending follows both tables')`: `BUFFERED_SENDING` deep-equals `{}`; `buffersSending('pcm', 'ios')` and `('mp3', 'safari')` are true; `('pcm', 'safari')`, `('mp3', 'android')`, `('pcm', 'desktop')` are false; with `BUFFERED_SENDING.android = ['mp3']` (deleted in a `finally`), `('mp3', 'android')` is true and `playbackMode('audio/mpeg', 'android')` is still `'media'`.

`textToSpeech.test.js`:
- `test('a buffered platform asks for buffered sending')`: inside `onIphone`, the open body has `buffered: true`.
- The existing `'sends the text, the active speech fields and cache: true'` stays green (no `buffered` key on desktop).

`ttsConfig.test.js`:
- `'ttsStreamingNote describes the chosen format, nothing when unknown'`: `ttsStreamingNote(PARTIAL, 'mp3')` → `'No streaming: the audio starts once its first part is generated.'`; `ttsStreamingNote({ ...ELEVEN, whole_formats: ['mp3'] }, 'mp3')` → `'No streaming: the audio starts once it is fully generated.'`, and `'pcm'` → the first-part wording; the streaming and unknown cases unchanged.
- `test('the last test facts show the parts of a split run')`: `ttsLastTestFacts({ ...RUN, parts: 4 })` → `'2,000 chars · 4 parts · 2 min 23 s of audio · generated in 2.2 s · $0.0080'`; `parts: 1` and `parts: null` give the current string.

- [ ] **Step 2: Run them, expect failures**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: FAIL in the new assertions.

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run the tests and the build, expect PASS**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test && npm run build`
Expected: every test passes; the build ends without error.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/speechOutcome.js frontend/src/utils/speechOutcome.test.js frontend/src/stores/textToSpeech.js frontend/src/stores/textToSpeech.test.js frontend/src/utils/ttsConfig.js frontend/src/utils/ttsConfig.test.js
git commit  # feat(tts): ask for buffered sending and show the parts of a test run
```

---

### Task 8: Browser, Android, tunnel and stop-cost checks (user checkpoint)

These checks need the user's devices and a backend restart, which only the user does. Wait for the user to say the backend runs the new code.

**Files:**
- Create (outside the repo): `<scratch>/tts-chunk-e2e/long-6000.txt` (about 6,000 characters of French prose, with paragraphs), `long-3000.txt`, `long-1500.txt`.
- Modify (only when a decision rule applies): `src/twicc/tts/model_catalog.py`, `src/twicc/tts/streams.py`, `frontend/src/utils/speechOutcome.js`, their tests; this plan, section *Check decisions*.

- [ ] **Step 1: Prepare and hand over**

Write the texts. Give the user the steps: paste a text as the settings test text (or read a message holding it); watch the speech log lines in `~/.twicc/logs/backend.log` (`grep "TTS speech"`; reading the log is allowed, the `.env` is not).

- [ ] **Step 2: The user runs the matrix; record the results here (✅ / ❌ / not tested)**

Desktop checks run directly on the backend port, through the Vite dev port, and through the Cloudflare tunnel.

| Check | Chrome | Firefox | Chrome Android | Firefox Android |
|---|---|---|---|---|
| ElevenLabs v3 MP3, 6,000 ch: plays to the end, no gap between parts | | | | |
| ElevenLabs v3 PCM, 6,000 ch: same | | | | |
| MiniMax MP3, 3,000 ch: plays to the end | | | | |
| Seed Audio MP3, 1,500 ch: plays to the end (not cut at the first part) | | | | |
| HeyGen PCM, 6,000 ch: no gap | | | | |
| Locked screen, both formats, ElevenLabs v3 6,000 ch | n/a | n/a | | |
| Page in the background, both formats, same | n/a | n/a | | |
| A stop after about 1 min: the log line shows `outcome=released` and its `parts=`; no later line for that speech; OpenRouter's activity shows only those parts; no cost in the last test run | | | | |
| A text `👍`: the error "There is nothing to read aloud in this text" | | | | |
| Settings: the note wording under the format; the last test run shows "N parts" | | | | |

- [ ] **Step 3: Apply the decision rules, then write *Check decisions***

Use the *Decision rules* table. For each change: update the constant, add or update the test that pins the value, run `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_tts_*.py -q` and `cd /home/twidi/dev/twicc-poc/frontend && npm test && npm run build`, and ask the user to rerun the failing cells. Untested cells keep the defaults; the final report lists them.

- [ ] **Step 4: Commit (only when a decision changed code)**

```bash
cd /home/twidi/dev/twicc-poc && git add <the changed files, by name>
git commit  # fix(tts): apply the speech splitting check decisions
```

---

## Check decisions (filled in Task 1 and Task 8)

Task 1 ran on 2026-10-30: one French text of 972 characters, cut into parts of 252, 467 and 251 characters, sent part after part to each model with its first listed voice (ElevenLabs `george`, MiniMax `English_expressive_narrator`, Seed none listed, HeyGen `nadine`). Every part answered `200`.

- **Stop gate hit (MiniMax).** `minimax/speech-2.8-turbo` is CBR (every frame of the three parts at 128 kbit/s), but the first frame of each part holds `Xing`, not `Info`. Its Xing counts are empty (frames 0, bytes 0): an encoder that writes to a stream cannot fill them in. Under the *Byte rate* rule, the runtime reads it as VBR and drops pacing and lookahead for this model. Decided: Ruling R2 (VBR only from differing frame bitrates; the info frame is still dropped).
- Automatic checks of `joined` (decode errors; decoded and `ffprobe` durations against the sum of the parts, tolerance 0.6 s for 2 seams; longest silence at a seam, limit 1.0 s):

  | Case | Decode errors | Sum of parts | Decoded | `ffprobe` (PCM: bytes) | Longest seam silence | Automatic result |
  |---|---|---|---|---|---|---|
  | `elevenlabs` | none | 61.84 s | 61.96 s | 61.96 s | none | pass |
  | `minimax` | none | 61.17 s | 61.23 s | 61.24 s | none | pass (stop gate above) |
  | `seed` | none | 53.33 s | 53.42 s | 53.42 s | 0.58 s (seam 2) | pass |
  | `heygen` (PCM) | none | 55.26 s | 55.26 s | 55.26 s | none | pass |

  The plain concatenation (`raw.mp3`) of each MP3 case gives `Header missing` / `Invalid data found` decode errors (the ID3 tags of parts 2 and 3 inside the stream). Its `ffprobe` duration is near the sum (53.48 s for Seed): `ffprobe` does not stop at the first part's info frame.
- `WHOLE_MP3_MODELS`: none.
- MP3 facts per checked model (ID3, info frame, bitrate, CBR), the same in all three parts:

  | Model | ID3v2 tag | First frame | Info frame | Encoder | Bitrates | Tail |
  |---|---|---|---|---|---|---|
  | `elevenlabs/eleven-v3` | 44 bytes | MPEG-1 Layer III, 128 kbit/s, 44,100 Hz, mono | `Info` (counts filled) | `Lavf` | 128 kbit/s only: CBR | none |
  | `minimax/speech-2.8-turbo` | 45 bytes | MPEG-1 Layer III, 128 kbit/s, 32,000 Hz, mono | `Xing` (frames 0, bytes 0) | `Lavf` | 128 kbit/s only: CBR | none |
  | `bytedance-seed/seed-audio-1-0` | 45 bytes | MPEG-1 Layer III, 128 kbit/s, 44,100 Hz, stereo (joint stereo frames) | `Info` (counts filled) | `Lavc59.37` | 128 kbit/s only: CBR | none |

  No `LAME` string in any first frame (the info frames carry `Lavf` / `Lavc`). No CRC on the first frames. No ID3v1 or APE tag at the end of a part. Task 3's filter sees two kinds: `Info` at offset 21 (MPEG-1 mono) or 36 (MPEG-1 stereo), and `Xing` at offset 21 (MPEG-1 mono).
- PCM parts' byte counts modulo the frame size: 0, 0, 0 (`audio/pcm;rate=44100;channels=1`, frame 2 bytes; parts of 1,262,782, 2,310,784 and 1,300,670 bytes). The three `Content-Type` values are equal.
- Joined files in the browsers (Task 1 Step 3): all four joined files OK in Chrome and Firefox, desktop and Android. Files in the controller session's Artifacts tab, folder `tts-chunking-spike/`: `elevenlabs-joined.mp3`, `minimax-joined.mp3`, `seed-joined.mp3` (with `seed-raw.mp3` to compare), `heygen-joined.wav`.
- ElevenLabs v3, 250 characters, time to the first byte: 4.62 s (headers and first byte together, last byte at 4.65 s). 467 characters: 7.83 s. Other models, first byte of the 252-character part: MiniMax 0.77 s, Seed 15.04 s, HeyGen 1.00 s.
- Task 1 spend: $0.2688 (OpenRouter generation lookups, 12 parts, 3,880 characters): ElevenLabs $0.0776, MiniMax $0.0582, Seed $0.1330, HeyGen $0.
- `PACE_MARGIN`:
- `BUFFERED_SENDING`:
- Cells not tested:
