# Attachments Phase 2 (CLI, RPC, MCP, Peer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every entry point except the web composer (CLI `--attach`, RPC `attach`, MCP `attach`, peer messages) sends any file through the phase 1 pipeline: staging store, planner, committer, manifest.

**Architecture:** The CLI command resolves each `--attach` value, copies the file into the composer staging store as a one-shot entry, and sends refs `{bucket, id}` as `attachments` in the drop payload. Thin drop wrappers take the send lane, plan and commit the refs with the phase 1 code, and release them on every outcome. The peer wire carries `attachments: [{name, media_type, data}]`; the receiver validates and stores them, and the browser composer runs the phase 1 pipeline at delivery.

**Tech Stack:** Django 6 ASGI, Python ≥ 3.13, orjson, httpx, Click/Typer, pytest + pytest-django, Vue 3, node:test.

**Spec:** [Attachments phase 2 design](2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md). It is the source of truth. Phase 1: [design](2026-10-03-composer-attachments-any-file-design.md), [plan](2026-10-04-composer-attachments-any-file-implementation-plan.md). Read the phase 2 spec completely before Task 1.

## Global Constraints

Every task includes these requirements. Values are copied from the spec.

- `INLINE_MAX_BYTES` = **50 × 1024 × 1024** decoded bytes of inline data per request (D5). Inline = data URIs in `attach`, local files sent over `--remote`, peer wire entries. A file read from a disk by the CLI or the server has no limit (D11).
- `INLINE_MAX_REQUEST_BYTES` = **72 × 1024 × 1024**: body cap of the RPC (token scope), the MCP server (both managers) and the peer receiver (D12). A cookie-scope RPC call keeps Django's **12 MB** (`DATA_UPLOAD_MAX_MEMORY_SIZE`, `settings.py:194`).
- `INLINE_MIN_THROUGHPUT` = **256 KiB/s**; `transfer_timeout(n)` = **30 s + n / INLINE_MIN_THROUGHPUT** (318 s for 72 MB).
- `PEER_SEND_TIMEOUT_WITH_FILES` = **468 s** = 318 + 60 + 30 + 30 + 30. It stays below the **600 s** internal MCP tool timeout.
- `ONESHOT_ENTRY_AGE` = **24 h**. The one-shot rule wins over the 7-day and 30-day reaper rules.
- Log redaction: any string longer than **512** characters becomes its first **64** characters followed by `…<N chars>`.
- Data URI form: `data:<media>[;<param>]*,<data>`, parameters in any order, `base64` required in any case, optional `name=<percent-encoded file name>`, unknown parameters ignored. A URI without `name` gets `attachment-<n>` + `mimetypes.guess_extension` of the media type, else `.bin` (`n` = 1-based position among the `--attach` values).
- Error label of a data URI: `data:<media>` (media cut to **255** characters), followed by a space and the normalized name when present. Without a comma: `data:` + `spec[5:40]` + `…`. Never the URI itself.
- Naming (D2): `attach` is the only user-facing name (CLI option, RPC property, MCP parameter). `attachments` is the name of every internal format (drop payloads, peer wire). No drop payload and no peer wire payload carries `images` or `documents` after Task 6.
- Peer wire entry: exactly `{name, media_type, data}`, `data` standard base64. The payload carries `attachments` only when the list is not empty. Stored `attachments_meta` rows: `{name, media_type, bytes}`.
- Migration `0153` follows `0152_async_question_state`. Its logic is inline (never import app code), with a fixed name bound of **255** bytes, and `reverse_code=migrations.RunPython.noop`.
- Plugin version: **`0.107.3` → `0.108.0`** (`src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`).
- Backend code: `orjson`, `NamedTuple`, no cosmetic import aliases (follow the existing `planner as attachment_planner` precedent only where a generic module name needs context), no `json` module.
- No CHANGELOG change. No new dependency. No branch, no worktree. Stay on `attach-any-files`.
- Never run `migrate`, `npm install`, `npm ci`, `uv pip`, or anything with `--active`. Never start or restart servers: the user does it (Task 9 asks them).
- Every Bash command starts with `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && `.
- Backend tests: `TWICC_DATA_DIR=$PWD uv run pytest <paths> -q`. Lint: `uvx ruff check <paths>`. Frontend: `cd frontend && npm test`.
- Lint rule: the repo has no ruff baseline (the project-wide lint pass is deferred). New files must be clean. In an existing file, add no new finding and do not fix findings older than the task: compare `uvx ruff check <file>` before and after the edit.
- Stage files by name (`git add <file> …`), never a directory. Preserve the untracked `docs/plans/2026-10-06-composer-attachments-handoff.md` (do not stage it).

## Review Focus

Inputs the spec implies but its own test list does not pin. Each line names the task that owns the test.

1. **A file name with undecodable bytes** (`b"bad\xffname.txt"` on Linux): staging and the `--remote` forwarder must not crash in `normalize_filename`, `orjson` or `quote`; the name becomes valid UTF-8 with U+FFFD. Tests: Task 1 (`test_an_undecodable_file_name_is_made_valid_utf8`), Task 5 (`test_an_undecodable_local_name_is_sent_as_valid_utf8`).
2. **A data URI `name=` that is a path or a reserved prefix** (`..%2Fetc%2Fpasswd`, `.twicc-upload-x`): the staged file stays a plain name inside `file/`. Test: Task 4 (`test_a_data_uri_name_never_escapes_the_entry`).
3. **Ctrl-C in the middle of staging the second of three files**: the first entry is discarded and nothing is submitted. Test: Task 4 (`test_an_interruption_while_staging_discards_and_submits_nothing`).
4. **A peer payload with an empty `attachments: []`, a legacy `images: null`, or a 0-byte entry**: accepted; no `attachments` key is stored for an empty list; a 0-byte entry is a valid empty file. Test: Task 6 (`test_receiver_edge_shapes`).
5. **`--remote` with a missing local file or a directory in `--attach`**: the size pre-check must not crash on it; the existing `attachment not found` usage error (exit 2) still wins, before any HTTP call. Test: Task 5 (`test_a_missing_local_attach_is_still_a_usage_error`).

## Scope and Interpretation

- **T3/T4 coupling (spec §8 T3 "the plan decides").** Task 3 makes the services accept refs and still accept legacy `images`/`documents` (the old CLI keeps working). Task 4 switches the CLI to refs and, in the same commit, makes the drop wrappers refuse a non-empty `images`/`documents` and removes the legacy fields from the `send_message` service. Every old path works until the task that replaces it.
- **Task numbering.** Tasks 1–6 are spec T1–T6. Spec T7 is merged into T6. Task 7 is spec T8 (docs). Task 8 is spec T9 (retire legacy support). Task 9 is spec T10 (manual matrix). Spec T11 is **out of scope** (optional, deferred): the WS legacy `images`/`documents` path stays.
- **Comments next to changed code** are updated in the task that changes the code (`mcp/server.py:267-271` in Task 5; `peer_messages.py:33-39`, `peer/inbound_views.py:28-30` and `models.py:2025-2036` in Task 6, because Task 6 deletes `cli/_drop_request/attachments.py`, which the model comment names). Task 7 sweeps what remains (`providers/helpers.py:100`, `:106-107`, `:767`; `cli/_batch_runner.py:71`, `:214`) and verifies with `grep`.
- **The release of refs** always goes through `lifecycle.delivery_release(refs)()` (spec §4.3.2 item 1). It is detached and idempotent, so one `finally` covers "release now" and "release after the result is built".
- **Throttled-write test (spec §7 Peer sender).** httpx mock transports bypass the network backend that applies write timeouts. Task 6 therefore has two layers: `test_the_write_timeout_follows_the_body` asserts the `httpx.Timeout` object given to the client, and `test_a_slow_write_longer_than_the_old_timeout_succeeds` posts through the real network backend to a `127.0.0.1` server that reads nothing for 1 s, with the timeouts scaled down (`OUTBOUND_TIMEOUT_SECONDS` = 0.3 s, `TRANSFER_BASE_SECONDS` = 0.3 s, `INLINE_MIN_THROUGHPUT` = 1 MiB/s). Its control test, `test_the_same_slow_write_fails_with_the_old_single_timeout`, proves the server stalls longer than the old single timeout (`WriteTimeout`). The Task 9 row 11 (49 MB between two local instances) does not exercise the write timeout: a loopback transfer never takes 30 s.
- **Line numbers** are those of commit `f6e4b342`. After an earlier task changes a file, locate the edit by the quoted code, not by the number.
- **Data URI label.** The spec says the label is `data:<media>` "followed by the name". The plan separates them with one space: `data:image/png shot 1.png`.
- **One bucket per command invocation.** `send-messages` stages every recipient's copy in the one bucket of the invocation (distinct entries per recipient). D13 "one bucket per request" holds per CLI invocation.
- **Peer receiver threads.** The view reads and parses the body in one worker thread; `receive_peer_message` validates the payload in a second worker-thread call (`prepare_inbound_payload`), so a direct call of the service is off the loop too. Both run off the event loop (§4.8.2).
- **Attachments endpoint loader.** `_attachments_body` loads only the `payload` column of the row (`values_list`), not the deferring summary loader; the answer needs nothing else.
- **Dead image-dimension helpers.** After Task 6 deletes `cli/_drop_request/attachments.py` (its only caller, `:311`), `BaseProviderHelpers.get_effective_image_dimension`, its Claude override, `MAX_IMAGE_DIMENSION`, and the two Claude helpers only that override calls (`selected_model_supports_highres_images`, `_upgrade_retired_model`) have no caller. Task 8 deletes them with the other legacy pieces. The model flag `supports_highres_images` stays: the frontend reads it from the model registry (`frontend/src/providers/claude_code/helpers.js:463`).

## File Structure

| File | Status | Responsibility | Task |
|---|---|---|---|
| `src/twicc/core/services/attachments/inline.py` | create | inline limits, hints, data URI parsing, budget, legacy peer blocks, media type sanitation, transfer timeout. No app import at module level | 1 |
| `src/twicc/core/services/attachments/staging.py` | modify | `name_max_bytes`, `new_bucket`, `stage_path`, `stage_bytes`, `discard_staged`, `oneshot.json` | 1 |
| `src/twicc/uploads/views.py` | modify | use `staging.name_max_bytes` | 1 |
| `src/twicc/composer_attachments_cleanup_task.py` | modify | 24 h one-shot rule | 1 |
| `src/twicc/agent/hybrid_switch.py` | create | the pending hybrid switch set | 2 |
| `src/twicc/core/services/attachments/target.py` | modify | `resolve_session_hybrid`, `resolve_existing_session_plan_target` | 2 |
| `src/twicc/asgi.py` | modify | import the moved names; drop `allow_attachments`; peer snapshot reader | 2, 3, 6 |
| `src/twicc/core/services/attachments/drop.py` | create | drop-request rules: refs to release, attachment codes, legacy fields | 3, 4 |
| `src/twicc/core/services/send_message.py` | modify | drop wrapper, lane, plan, release, error mapping | 3, 4 |
| `src/twicc/core/services/session_creation.py` | modify | drop wrapper, lane, release, error mapping, no `allow_attachments` | 3, 4 |
| `src/twicc/drop_requests_watcher.py` | modify | routes to the wrappers; `peer:send_attachments` | 3, 6 |
| `src/twicc/cli/_drop_request/attach_sources.py` | create | resolve and stage `--attach` values | 4 |
| `src/twicc/cli/_drop_request/help_strings.py` | modify | shared `--attach` / peer `--timeout` help texts | 4, 6 |
| `src/twicc/cli/send_message/command.py`, `send_messages.py`, `create_session/command.py` | modify | stage refs; no `images`/`documents` | 4 |
| `src/twicc/log_redaction.py` | create | `redact_for_log` | 5 |
| `src/twicc/cli/_remote.py` | modify | named data URI, size pre-check, body pre-check, read timeout, peer-send timeout | 5 |
| `src/twicc/rpc/views.py` | modify | 72 MB token-scope cap, off-loop parse, redacted log | 5 |
| `src/twicc/mcp/server.py` | modify | 72 MB cap, redacted log, instructions | 5 |
| `src/twicc/cli/peer_send.py` | modify | refs, minted `message_id`, timeout default, output ids | 6 |
| `src/twicc/core/services/peer_messages.py` | modify | wire, receiver, sender, wrappers, summary loaders | 6 |
| `src/twicc/peer/outbound.py`, `peer/inbound_views.py`, `peer/owner_views.py` | modify | bytes body, write timeout, 72 MB cap, read APIs | 6 |
| `src/twicc/core/serializers.py`, `cli/peer_message.py`, `peer_purge_task.py`, `core/models.py` | modify | summary text, purge, comments | 6 |
| `src/twicc/core/migrations/0153_peer_message_attachments.py` | create | convert stored peer rows | 6 |
| `frontend/src/utils/peerMessageContent.js`, `frontend/src/components/peer/PeerMessageReviewDialog.vue` | modify | entries instead of blocks | 6 |
| `src/twicc/cli/_drop_request/attachments.py` | delete | legacy validation and encoding | 6 |
| `tests/test_async_question_migration_graph.py` | delete | one-time `0151`/`0152` merge test; its leaf assertion breaks with `0153` (user decision) | 6 |
| skills, `plugin.json`, `SKILLS-AND-CLI.md`, `RPC-API.md`, help pages, `CLAUDE.md`, `AGENTS.md` | modify | docs | 7 |
| `providers/helpers.py`, `providers/claude_code/helpers.py`, `providers/claude_code/constants.py`, `providers/codex/helpers.py`, `cli/_drop_request/bootstrap_local.py` | modify | retire `ATTACHMENT_SUPPORT` and the dead backend image-dimension helpers | 8 |
| `docs/plans/2026-10-07-attachments-phase2-validation.md` | create | validation record | 9 |

### Shared contracts (exact names)

```python
# twicc.core.services.attachments.inline  (Task 1)
INLINE_MAX_BYTES: int; INLINE_MAX_REQUEST_BYTES: int; INLINE_MIN_THROUGHPUT: int
INLINE_TOO_LARGE_HINT: str; PEER_TOO_LARGE_HINT: str; PEER_SEND_TIMEOUT_WITH_FILES: int
ERROR_INVALID_DATA_URI = "invalid_data_uri"; ERROR_TOO_LARGE = "attachments_too_large"; OCTET_STREAM: str
class DataUri(NamedTuple): name: str | None; media_type: str; size: int; data: bytes
def is_data_uri(value: str) -> bool
def parse_data_uri(spec: str, *, budget: InlineBudget | None = None) -> DataUri   # AttachmentError
def data_uri_size(spec: str) -> int | None
def data_uri_label(spec: str) -> str
def decoded_size(data: str) -> int                                                # AttachmentError
def default_name(media_type: str, n: int) -> str
class InlineBudget: __init__(self, hint: str, *, limit: int | None = None); add(self, size: int) -> None; total: int
def too_large_message(total: int, limit: int, hint: str) -> str
def format_mb(size: int) -> str
def entries_from_legacy_blocks(images: list | None, documents: list | None) -> list[dict]
def sanitize_media_type(value: object) -> str
def transfer_timeout(body_bytes: int) -> float

# twicc.core.services.attachments.staging  (Task 1, additions)
ONESHOT_MARKER = "oneshot.json"; ORIGIN_CLI = "cli"; ORIGIN_API = "api"
ERROR_STAGE_FAILED = "attachment_stage_failed"; NAME_SUFFIX_ROOM = 8
def name_max_bytes() -> int
def new_bucket(origin: str) -> str
def stage_path(source: str | os.PathLike, *, bucket: str, origin: str, name: str | None = None) -> AttachmentRef
def stage_bytes(data: bytes, name: str, *, bucket: str, origin: str) -> AttachmentRef
def discard_staged(refs: Iterable[AttachmentRef]) -> None

# twicc.agent.hybrid_switch  (Task 2)
_PENDING_HYBRID_SWITCHES: set[str]; def is_hybrid_switch_pending(session_id: str) -> bool
# twicc.core.services.attachments.target  (Task 2, additions)
async def resolve_session_hybrid(session_id: str) -> bool
async def resolve_existing_session_plan_target(*, session_id, provider, effective_settings, directory, ephemeral, live_agent) -> PlanTarget

# twicc.core.services.attachments.drop  (Task 3; Task 4 adds the last two)
def refs_to_release(payload: dict) -> tuple[AttachmentRef, ...]
def is_attachment_code(code: str | None) -> bool
def message_with_names(exc: BaseException) -> str
LEGACY_FIELDS_MESSAGE: str; def has_legacy_fields(payload: dict) -> bool

# services  (Task 3)
async def send_message_from_drop_payload(payload: dict) -> SendMessageResult
async def send_message_to_session_from_payload(payload: dict, *, release_refs_on_outcome: bool = False) -> SendMessageResult
async def create_session_from_drop_payload(payload: dict) -> SessionCreationResult
async def create_session_from_payload(payload, *, allow_hybrid=False, allow_ephemeral=False, ephemeral_admission=None, release_refs_on_outcome=False)

# twicc.cli._drop_request.attach_sources  (Task 4)
class AttachSource(NamedTuple): label: str; name: str; path: str | None; data: bytes | None
def resolve(attach: list[str], *, hint: str, count_paths: bool = False) -> tuple[list[AttachSource], list[ValidationError]]
def stage(sources: list[AttachSource], *, bucket: str) -> tuple[tuple[AttachmentRef, ...], list[ValidationError]]
def staging_origin() -> str; def new_request_bucket() -> str; def as_payload(refs) -> list[dict]

# twicc.log_redaction  (Task 5)
def redact_for_log(value: object) -> object
# twicc.cli._remote  (Task 5)
class Resolved(NamedTuple): path; spec; params; explicit: frozenset[str] = frozenset()
def check_inline_size(resolved: Resolved) -> None          # RemoteUsageError
def apply_peer_send_timeout(argv: list[str], resolved: Resolved) -> tuple[list[str], Resolved]

# peer  (Task 6)
peer_messages.PEER_ATTACHMENT_MAX_TOTAL_BYTES; ERROR_INVALID_MESSAGE_ID = "invalid_message_id"; ERROR_MESSAGE_TOO_LARGE = "message_too_large"
class InboundPayload(NamedTuple): payload: dict; attachments_meta: list
def prepare_inbound_payload(payload: object) -> tuple[InboundPayload | None, list[PeerError]]
def peer_message_summary_queryset(queryset=None); def peer_message_full_queryset()
async def send_peer_message_from_drop_payload(payload: dict) -> PeerSendResult        # kind peer:send
async def send_peer_attachments_from_drop_payload(payload: dict) -> PeerSendResult    # kind peer:send_attachments
outbound.build_message_body(*, message_id, title, reply_to, payload, origin) -> bytes
async outbound.post_message(base_url: str, *, bearer: str, body: bytes) -> tuple[int, dict]
serializers.peer_message_text(message) -> str
```

```js
// frontend/src/utils/peerMessageContent.js  (Task 6)
export function mergePeerAttachments(detail, attachments)        // attachments = {attachments: [...]}
export function peerEntryToFile(entry)                          // File | null
export function peerEntryToMediaItem(entry, index)              // chip item | null
export async function addPeerAttachmentsToDraft(payload, entryToFile, addAttachment)
```

## Verification and Commits

- Every task: write the failing tests, run them and see them fail, implement, run them and see them pass, run the regression suites named in the task, lint the touched Python files with `uvx ruff check`, then commit.
- Commit message: Conventional Commit subject, a body that explains the change, and the trailer `Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>` where `<MODEL>` is the exact model running at commit time (for example `Opus 5.5 (1M context)`). Never copy a model name from this plan.
- Use `git commit -F <file>` with a message file written in `/tmp` to keep the body intact.
- Do not commit this plan unless the user asks.

---

### Task 1 (spec T1): Inline module, one-shot staging, reaper rule

**Files:**
- Create: `src/twicc/core/services/attachments/inline.py`
- Modify: `src/twicc/core/services/attachments/staging.py:10-44` (imports, `__all__`), add new code after `on_upload_completed` (`:374-408`)
- Modify: `src/twicc/uploads/views.py:355-356` (`_NAME_SUFFIX_ROOM`), `:433-448` (delete `_composer_name_max_bytes`), `:463` (call site)
- Modify: `src/twicc/composer_attachments_cleanup_task.py:1-22` (docstring), `:48-53` (constants), `:92-99` (`_entry_is_expired`)
- Create: `tests/test_attachments_inline.py`, `tests/test_attachments_oneshot_staging.py`
- Modify: `tests/test_composer_attachments_cleanup.py` (new tests at the end of "Entry expiry")

**Interfaces:**
- Consumes: `staging.AttachmentError`, `normalize_filename(name, max_bytes)`, `write_marker(entry, name, payload)`, `fsync_dir`, `validate_ref`, `_validate_key`, `_real_entry_dir`, `_staging_root`, `READY_MARKER`, `FILE_DIR`; `uploads.store.COPY_BLOCK_SIZE`, `FILENAME_MAX_BYTES`, `TEMP_FILE_PREFIX`, `now_iso`.
- Produces: everything listed for `inline` and `staging` in "Shared contracts"; `composer_attachments_cleanup_task.ONESHOT_ENTRY_AGE`.

- [ ] **Step 1: Write the failing inline tests.** Create `tests/test_attachments_inline.py`:

```python
"""Inline attachment data: data URIs, the 50 MB budget, legacy peer blocks.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.2.
"""

import base64
from types import SimpleNamespace

import pytest

from twicc.core.services.attachments import inline
from twicc.core.services.attachments.staging import AttachmentError

MIB = 1024 * 1024


def _uri(data: bytes, header: str = "text/plain;base64") -> str:
    return f"data:{header},{base64.b64encode(data).decode()}"


def test_constants_match_the_design():
    assert inline.INLINE_MAX_BYTES == 50 * MIB
    assert inline.INLINE_MAX_REQUEST_BYTES == 72 * MIB
    # base64 of 50 MB fits in the cap with room for JSON, names and text.
    assert 4 * -(-inline.INLINE_MAX_BYTES // 3) == 69_905_068 < inline.INLINE_MAX_REQUEST_BYTES
    assert inline.PEER_SEND_TIMEOUT_WITH_FILES == 468
    assert inline.PEER_SEND_TIMEOUT_WITH_FILES < 600


def test_transfer_timeout_grows_with_the_body():
    assert inline.transfer_timeout(0) == 30.0
    assert inline.transfer_timeout(256 * 1024) == 31.0
    assert inline.transfer_timeout(inline.INLINE_MAX_REQUEST_BYTES) == 318.0


def test_hints():
    assert "file storage service" in inline.INLINE_TOO_LARGE_HINT
    assert "remote:" in inline.INLINE_TOO_LARGE_HINT
    assert "absolute server path" in inline.INLINE_TOO_LARGE_HINT
    assert "file storage service" in inline.PEER_TOO_LARGE_HINT
    assert "remote:" not in inline.PEER_TOO_LARGE_HINT


def test_data_uri_without_name():
    assert inline.parse_data_uri(_uri(b"hello")) == inline.DataUri(None, "text/plain", 5, b"hello")


def test_data_uri_with_a_percent_encoded_name():
    uri = inline.parse_data_uri(_uri(b"x", "video/mp4;name=capture%20%281%29.mp4;base64"))
    assert (uri.name, uri.media_type, uri.data) == ("capture (1).mp4", "video/mp4", b"x")


@pytest.mark.parametrize("header", [
    "text/plain;charset=utf-8;name=a.txt;base64",
    "text/plain;base64;name=a.txt",
    "text/plain;name=a.txt;BASE64",
    " text/plain ; Name=a.txt ; Base64 ",
])
def test_data_uri_parameters_in_any_order_and_case(header):
    uri = inline.parse_data_uri(_uri(b"hi", header))
    assert (uri.media_type, uri.name, uri.data) == ("text/plain", "a.txt", b"hi")


def test_an_empty_name_parameter_is_no_name():
    assert inline.parse_data_uri(_uri(b"x", "text/plain;name=;base64")).name is None


def test_a_badly_encoded_name_is_refused():
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri(_uri(b"x", "text/plain;name=%FF.txt;base64"))
    assert exc.value.code == "invalid_data_uri"


@pytest.mark.parametrize("spec", [
    "data:text/plain,aGk=",          # no base64 parameter
    "data:text/plain;base64",        # no comma
    "data:;base64,aGk",              # length not a multiple of 4
    "data:;base64,a===",             # three padding characters
    "data:;base64,QUJD!!!!",         # not base64
])
def test_malformed_data_uris_are_refused(spec):
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri(spec)
    assert exc.value.code == "invalid_data_uri"


def test_an_empty_payload_is_a_zero_byte_file():
    assert inline.parse_data_uri("data:application/octet-stream;base64,") == inline.DataUri(
        None, "application/octet-stream", 0, b"",
    )


def test_whitespace_inside_the_base64_is_ignored():
    assert inline.parse_data_uri("data:text/plain;base64,aG\nVs bG8=").data == b"hello"


def test_a_missing_media_type_is_octet_stream():
    assert inline.parse_data_uri("data:;base64,aGk=").media_type == "application/octet-stream"


def test_the_label_is_never_the_uri():
    big = _uri(b"x" * 10_000, "image/png;name=shot%201.png;base64")
    assert inline.data_uri_label(big) == "data:image/png shot 1.png"
    assert inline.data_uri_label(_uri(b"x", "image/png;base64")) == "data:image/png"
    assert inline.data_uri_label("data:" + "y" * 100) == "data:" + "y" * 35 + "…"
    assert inline.data_uri_label("data:" + "m" * 1000 + ";base64,AAAA") == "data:" + "m" * 255


def test_the_label_uses_the_normalized_name():
    assert inline.data_uri_label("data:text/plain;name=..%2Fetc%2Fpasswd;base64,") == "data:text/plain .._etc_passwd"
    assert inline.data_uri_label("data:text/plain;name=%FF;base64,") == "data:text/plain"


def test_data_uri_size_does_not_decode():
    assert inline.data_uri_size(_uri(b"x" * 10)) == 10
    assert inline.data_uri_size("data:text/plain;base64") is None
    assert inline.data_uri_size("data:;base64,abc") is None


@pytest.mark.parametrize(("media_type", "n", "expected"), [
    ("text/plain", 1, "attachment-1.txt"),
    ("image/png", 3, "attachment-3.png"),
    ("TEXT/PLAIN; charset=utf-8", 2, "attachment-2.txt"),
    ("application/x-no-such-type", 2, "attachment-2.bin"),
    ("", 1, "attachment-1.bin"),
])
def test_default_name(media_type, n, expected):
    assert inline.default_name(media_type, n) == expected


def test_budget_accepts_exactly_the_limit_and_refuses_one_more_byte():
    budget = inline.InlineBudget(inline.INLINE_TOO_LARGE_HINT)
    budget.add(inline.INLINE_MAX_BYTES)
    assert budget.total == inline.INLINE_MAX_BYTES
    with pytest.raises(AttachmentError) as exc:
        budget.add(1)
    assert exc.value.code == "attachments_too_large"
    assert "the limit is 50 MB" in str(exc.value)
    assert str(exc.value).endswith(inline.INLINE_TOO_LARGE_HINT)


def test_the_peer_budget_carries_the_peer_hint():
    budget = inline.InlineBudget(inline.PEER_TOO_LARGE_HINT, limit=3)
    with pytest.raises(AttachmentError) as exc:
        budget.add(4)
    assert str(exc.value).endswith(inline.PEER_TOO_LARGE_HINT)


def test_the_size_is_refused_before_decoding(monkeypatch):
    decoded = []
    monkeypatch.setattr(inline, "base64", SimpleNamespace(b64decode=lambda *args, **kwargs: decoded.append(args)))
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    budget = inline.InlineBudget(inline.INLINE_TOO_LARGE_HINT)
    with pytest.raises(AttachmentError) as exc:
        inline.parse_data_uri("data:text/plain;base64,aGVsbG8=", budget=budget)
    assert exc.value.code == "attachments_too_large"
    assert decoded == []


def test_legacy_blocks_become_ordered_wire_entries():
    png = base64.b64encode(b"\x89PNG").decode()
    entries = inline.entries_from_legacy_blocks(
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}}],
        [
            {"type": "document", "title": "notes.md",
             "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
            {"type": "document", "name": "clip.mp4",
             "source": {"type": "base64", "media_type": "video/mp4", "data": "AAAA"}},
            {"type": "document", "source": {"type": "base64", "media_type": "application/x-odd", "data": "AAAA"}},
        ],
    )
    assert entries == [
        {"name": "attachment-1.png", "media_type": "image/png", "data": png},
        {"name": "notes.md", "media_type": "text/plain", "data": base64.b64encode("hé".encode()).decode()},
        {"name": "clip.mp4", "media_type": "video/mp4", "data": "AAAA"},
        {"name": "attachment-4.bin", "media_type": "application/x-odd", "data": "AAAA"},
    ]


def test_a_text_block_without_media_type_is_text_plain():
    [entry] = inline.entries_from_legacy_blocks(None, [{"source": {"type": "text", "data": "x"}}])
    assert entry["media_type"] == "text/plain"
    assert entry["name"] == "attachment-1.txt"


@pytest.mark.parametrize(("value", "expected"), [
    ("image/png", "image/png"),
    ("text/x-diff", "text/x-diff"),
    ("application/vnd.api+json", "application/vnd.api+json"),
    ("text/plain; charset=utf-8", "application/octet-stream"),
    ("not a type", "application/octet-stream"),
    ("a/" + "b" * 300, "application/octet-stream"),
    (None, "application/octet-stream"),
    (42, "application/octet-stream"),
])
def test_sanitize_media_type(value, expected):
    assert inline.sanitize_media_type(value) == expected
```

- [ ] **Step 2: Write the failing staging tests.** Create `tests/test_attachments_oneshot_staging.py`:

```python
"""One-shot staging entries of the CLI, the RPC and the MCP.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.3.
"""

import errno
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import orjson
import pytest

from twicc.core.services.attachments import staging
from twicc.core.services.attachments.staging import AttachmentError


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    return staging.get_composer_attachments_dir()


def test_new_bucket_carries_its_origin_and_passes_the_key_rules(root):
    for origin in ("cli", "api"):
        bucket = staging.new_bucket(origin)
        assert bucket.startswith(f"{origin}-")
        assert staging.is_valid_bucket(bucket)
        uuid.UUID(bucket.removeprefix(f"{origin}-"))
    with pytest.raises(ValueError):
        staging.new_bucket("browser")


def test_stage_path_makes_a_ready_real_copy(root, tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"\x89PNG data")
    ref = staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    entry = staging.load_entry(ref)
    assert (entry.filename, entry.size) == ("photo.png", 9)  # len(b"\x89PNG data") == 9
    assert entry.path.read_bytes() == b"\x89PNG data"
    assert os.stat(entry.path).st_ino != os.stat(source).st_ino
    marker = orjson.loads((staging.entry_dir(ref) / "oneshot.json").read_bytes())
    assert marker["origin"] == "cli"
    assert marker["at"]


def test_stage_path_name_overrides_the_base_name(root, tmp_path):
    source = tmp_path / "x.bin"
    source.write_bytes(b"1")
    ref = staging.stage_path(source, bucket=staging.new_bucket("api"), origin="api", name="report.pdf")
    assert staging.load_entry(ref).filename == "report.pdf"


@pytest.mark.parametrize(("name", "expected"), [
    ("a\nb.txt", "a_b.txt"),
    ("../x.txt", ".._x.txt"),
    (".", "attachment"),
    ("", "attachment"),
    (".twicc-upload-x", "_.twicc-upload-x"),
])
def test_stage_bytes_sanitizes_the_name(root, name, expected):
    ref = staging.stage_bytes(b"data", name, bucket=staging.new_bucket("api"), origin="api")
    assert staging.load_entry(ref).filename == expected


def test_a_zero_byte_file_is_ready(root):
    ref = staging.stage_bytes(b"", "empty.txt", bucket=staging.new_bucket("cli"), origin="cli")
    assert staging.load_entry(ref).size == 0


def test_a_long_name_is_truncated_before_its_extension(root):
    ref = staging.stage_bytes(b"x", "a" * 400 + ".pdf", bucket=staging.new_bucket("cli"), origin="cli")
    name = staging.load_entry(ref).filename
    assert name.endswith(".pdf")
    assert len(name.encode()) <= staging.name_max_bytes()


def test_an_undecodable_file_name_is_made_valid_utf8(root, tmp_path):
    source = tmp_path / os.fsdecode(b"bad\xffname.txt")
    source.write_bytes(b"x")
    ref = staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    assert staging.load_entry(ref).filename == "bad�name.txt"


def test_the_marker_exists_before_the_copy(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")
    seen = []
    real_copy = shutil.copyfileobj

    def spy(reader, writer, length=0):
        seen.append((Path(writer.name).parent.parent / "oneshot.json").exists())
        return real_copy(reader, writer, length)

    monkeypatch.setattr(staging.shutil, "copyfileobj", spy)
    staging.stage_path(source, bucket=staging.new_bucket("cli"), origin="cli")
    assert seen == [True]


def test_an_interruption_during_the_copy_removes_the_entry(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")

    def interrupted(reader, writer, length=0):
        raise KeyboardInterrupt

    monkeypatch.setattr(staging.shutil, "copyfileobj", interrupted)
    bucket = staging.new_bucket("cli")
    with pytest.raises(KeyboardInterrupt):
        staging.stage_path(source, bucket=bucket, origin="cli")
    assert list((root / bucket).iterdir()) == []


def test_a_disk_error_removes_the_entry_and_is_attachment_stage_failed(root, tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_bytes(b"abc")

    def disk_full(reader, writer, length=0):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(staging.shutil, "copyfileobj", disk_full)
    bucket = staging.new_bucket("cli")
    with pytest.raises(AttachmentError) as exc:
        staging.stage_path(source, bucket=bucket, origin="cli")
    assert exc.value.code == "attachment_stage_failed"
    assert "No space left on device" in str(exc.value)
    assert list((root / bucket).iterdir()) == []


def test_an_unreadable_source_is_attachment_stage_failed(root, tmp_path):
    with pytest.raises(AttachmentError) as exc:
        staging.stage_path(tmp_path / "missing.txt", bucket=staging.new_bucket("cli"), origin="cli")
    assert exc.value.code == "attachment_stage_failed"


def test_the_stage_error_names_the_normalized_file_never_the_raw_name(root, monkeypatch):
    def disk_full(out):
        raise OSError(errno.ENOSPC, "No space left on device")

    raw_name = "a\nb" + "x" * 5000 + ".txt"  # e.g. an unbounded name= of a data URI over the RPC
    with pytest.raises(AttachmentError) as exc:
        staging._stage(disk_full, raw_name, bucket=staging.new_bucket("api"), origin="api")
    message = str(exc.value)
    assert "\n" not in message
    assert "x" * 300 not in message
    assert len(message.encode()) < staging.name_max_bytes() + 100


@pytest.mark.parametrize("failing", ["marker", "file_dir"])
def test_a_failure_while_creating_the_entry_leaves_no_entry(root, monkeypatch, failing):
    if failing == "marker":
        real_write_marker = staging.write_marker

        def write_marker(entry, name, payload):
            if name == staging.ONESHOT_MARKER:
                raise OSError(errno.ENOSPC, "No space left on device")
            real_write_marker(entry, name, payload)

        monkeypatch.setattr(staging, "write_marker", write_marker)
    else:
        real_mkdir = Path.mkdir

        def mkdir(self, *args, **kwargs):
            if self.name == staging.FILE_DIR:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    bucket = staging.new_bucket("cli")
    with pytest.raises(AttachmentError) as exc:
        staging.stage_bytes(b"x", "a.txt", bucket=bucket, origin="cli")
    assert exc.value.code == "attachment_stage_failed"
    assert list((root / bucket).iterdir()) == []


def test_a_hard_kill_before_the_ready_marker_leaves_a_not_ready_oneshot_entry(root):
    bucket = staging.new_bucket("cli")
    script = textwrap.dedent("""
        import os
        import sys

        from twicc.core.services.attachments import staging

        real_write_marker = staging.write_marker

        def write_marker(entry, name, payload):
            if name == staging.READY_MARKER:
                os._exit(9)
            real_write_marker(entry, name, payload)

        staging.write_marker = write_marker
        staging.stage_bytes(b"data", "a.txt", bucket=sys.argv[1], origin="cli")
    """)
    result = subprocess.run([sys.executable, "-c", script, bucket], env=dict(os.environ), check=False)
    assert result.returncode == 9
    [entry] = list((root / bucket).iterdir())
    assert (entry / "oneshot.json").exists()
    with pytest.raises(AttachmentError) as exc:
        staging.load_entry(staging.validate_ref({"bucket": bucket, "id": entry.name}))
    assert exc.value.code == "attachment_not_ready"


def test_a_bucket_removed_between_the_two_mkdir_calls_is_recreated(root, monkeypatch):
    calls = []
    real_mkdir_entry = staging._mkdir_entry

    def racing(entry):
        calls.append(entry)
        if len(calls) == 1:
            shutil.rmtree(entry.parent)  # the reaper removes the empty bucket
        real_mkdir_entry(entry)

    monkeypatch.setattr(staging, "_mkdir_entry", racing)
    ref = staging.stage_bytes(b"x", "a.txt", bucket=staging.new_bucket("api"), origin="api")
    assert len(calls) == 2
    assert staging.load_entry(ref).size == 1


def test_discard_staged_removes_only_the_given_entries_then_the_empty_bucket(root):
    bucket = staging.new_bucket("cli")
    first = staging.stage_bytes(b"1", "a.txt", bucket=bucket, origin="cli")
    second = staging.stage_bytes(b"2", "b.txt", bucket=bucket, origin="cli")
    staging.discard_staged([first])
    with pytest.raises(AttachmentError):
        staging.load_entry(first)
    assert staging.load_entry(second).size == 1
    staging.discard_staged([second])
    assert not (root / bucket).exists()


def test_discard_staged_never_touches_artifacts(root, tmp_path):
    artifact = Path(os.environ["TWICC_DATA_DIR"]) / "artifacts" / "s" / "attachments" / "kept.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("kept")
    staging.discard_staged([staging.validate_ref({"bucket": "s", "id": str(uuid.uuid4())})])
    assert artifact.read_text() == "kept"


def test_name_max_bytes_lives_in_staging():
    from twicc.uploads import views

    assert not hasattr(views, "_composer_name_max_bytes")
    assert 1 <= staging.name_max_bytes() <= 240
```

- [ ] **Step 3: Write the failing reaper tests.** Append to the "Entry expiry" section of `tests/test_composer_attachments_cleanup.py` (after `test_draft_entries_expire_at_exactly_30_days`):

```python
def make_oneshot_entry(ref, *, age: timedelta, committed=False):
    entry = make_entry(ref, age=age, committed=committed)
    (entry / "oneshot.json").write_bytes(b'{"origin": "cli", "at": "x"}')
    set_mtime(entry, NOW - age)
    return entry


def test_oneshot_entries_expire_at_exactly_24_hours():
    expired, kept = new_ref("cli-bucket"), new_ref("cli-bucket")
    make_oneshot_entry(expired, age=timedelta(hours=24))
    make_oneshot_entry(kept, age=timedelta(hours=24) - SECOND)
    stats = run_pass()
    assert not entry_path(expired).exists()
    assert entry_path(kept).exists()
    assert stats.entries == 1


def test_the_oneshot_rule_wins_over_the_committed_and_draft_rules():
    committed, draft = new_ref("api-bucket"), new_ref("api-bucket")
    make_oneshot_entry(committed, age=timedelta(days=2), committed=True)
    make_oneshot_entry(draft, age=timedelta(days=2))
    run_pass()
    assert not entry_path(committed).exists()
    assert not entry_path(draft).exists()


def test_a_live_upload_still_protects_a_oneshot_entry():
    ref = new_ref("cli-bucket")
    make_oneshot_entry(ref, age=timedelta(days=2))
    make_live_upload(ref)
    set_mtime(entry_path(ref), NOW - timedelta(days=2))
    run_pass()
    assert entry_path(ref).exists()
```

- [ ] **Step 4: Run the new tests and see them fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_attachments_inline.py tests/test_attachments_oneshot_staging.py tests/test_composer_attachments_cleanup.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.core.services.attachments.inline`, `AttributeError: ... has no attribute 'new_bucket'`, the one-shot entries still exist after the pass).

- [ ] **Step 5: Create `src/twicc/core/services/attachments/inline.py`.**

```python
"""Inline attachment data: the one place that knows the inline limit.

"Inline" means bytes that travel inside a request: a ``data:`` URI in ``--attach`` (RPC,
MCP, local CLI, or built by the ``--remote`` forwarder) and every entry of the peer wire.
A file read from a disk (a local path, an absolute server path, ``remote:``) is not inline
and has no limit (D11).

No application import at module level: the ``--remote`` forwarder and the CLI help strings
import this module before Django is set up. Staging helpers are imported inside functions.
Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.2.
"""

import base64
import binascii
import mimetypes
import re
from typing import NamedTuple
from urllib.parse import unquote

MIB = 1024 * 1024
# Total decoded bytes of the inline files of one request (D5).
INLINE_MAX_BYTES = 50 * MIB
# Body cap of an HTTP request that may carry inline data (D12): 4 * ceil(50 MB / 3) =
# 69 905 068 bytes of base64, plus about 5 MB for the JSON, the names and the text.
INLINE_MAX_REQUEST_BYTES = 72 * MIB
# The slowest upload a peer send is sized for (2 Mbit/s).
INLINE_MIN_THROUGHPUT = 256 * 1024
# Today's peer connect / read timeout (``peer.outbound.OUTBOUND_TIMEOUT_SECONDS``).
TRANSFER_BASE_SECONDS = 30.0

INLINE_TOO_LARGE_HINT = (
    "For a larger file, put it on a file storage service and pass its URL in the message text, "
    "or pass a path the server reads: remote:<absolute path> over --remote, an absolute server path "
    "over the RPC or the MCP."
)
PEER_TOO_LARGE_HINT = (
    "For a larger file, put it on a file storage service and pass its URL in the message text."
)

ERROR_INVALID_DATA_URI = "invalid_data_uri"
ERROR_TOO_LARGE = "attachments_too_large"

OCTET_STREAM = "application/octet-stream"
TEXT_PLAIN = "text/plain"
DATA_URI_PREFIX = "data:"
MEDIA_TYPE_MAX_CHARS = 255
# How much of a malformed data URI (no comma) its error label shows.
_LABEL_RAW_END = 40
# ``type/subtype``, RFC 6838 restricted-name characters, no parameters.
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*")


def transfer_timeout(body_bytes: int) -> float:
    """The time budget of an HTTP write of *body_bytes* (§4.2)."""
    return TRANSFER_BASE_SECONDS + body_bytes / INLINE_MIN_THROUGHPUT


# The wait of a caller of the peer send service for a message with files, from the worst case:
# the write of a full body, the local steps (file reads, encoding, the DB write lock), the
# connect, the receiver's answer, and a margin. Below the 600 s MCP tool timeout.
_PEER_SEND_LOCAL_STEPS_SECONDS = 60
_PEER_CONNECT_SECONDS = 30
_PEER_ANSWER_SECONDS = 30
_PEER_SEND_MARGIN_SECONDS = 30
PEER_SEND_TIMEOUT_WITH_FILES = (
    int(transfer_timeout(INLINE_MAX_REQUEST_BYTES))
    + _PEER_SEND_LOCAL_STEPS_SECONDS + _PEER_CONNECT_SECONDS + _PEER_ANSWER_SECONDS + _PEER_SEND_MARGIN_SECONDS
)


def _error(code: str, message: str) -> Exception:
    # Imported here: the staging module is not loaded on the CLI help path.
    from twicc.core.services.attachments.staging import AttachmentError

    return AttachmentError(code, message)


def format_mb(size: int) -> str:
    """``50 MB`` for a whole number of MB, else one decimal (``50.0 MB``)."""
    return f"{size // MIB} MB" if size % MIB == 0 else f"{size / MIB:.1f} MB"


def too_large_message(total: int, limit: int, hint: str) -> str:
    return f"The attached files total {format_mb(total)} of inline data; the limit is {format_mb(limit)}. {hint}"


class InlineBudget:
    """Adds up the decoded sizes of the inline files of one request (D5)."""

    def __init__(self, hint: str, *, limit: int | None = None) -> None:
        self.hint = hint
        self.limit = INLINE_MAX_BYTES if limit is None else limit
        self.total = 0

    def add(self, size: int) -> None:
        """Count *size* bytes; raise ``attachments_too_large`` above the limit."""
        self.total += size
        if self.total > self.limit:
            raise _error(ERROR_TOO_LARGE, too_large_message(self.total, self.limit, self.hint))


class DataUri(NamedTuple):
    name: str | None  # percent-decoded, not normalized; None without a (non-empty) name=
    media_type: str  # declared, only a label
    size: int  # decoded bytes
    data: bytes


def is_data_uri(value: str) -> bool:
    return value.startswith(DATA_URI_PREFIX)


def _decoded_size_or_none(data: str) -> int | None:
    """Decoded size of standard base64 *data* from its length and padding, or None when malformed."""
    if len(data) % 4:
        return None
    padding = len(data) - len(data.rstrip("="))
    if padding > 2:
        return None
    return len(data) // 4 * 3 - padding


def decoded_size(data: str) -> int:
    """Decoded size of *data* without decoding it; ``invalid_data_uri`` when malformed."""
    size = _decoded_size_or_none(data)
    if size is None:
        raise _error(ERROR_INVALID_DATA_URI, "invalid base64 payload in data URI")
    return size


def _split(spec: str) -> tuple[list[str], str] | None:
    """``(parameters, payload)`` of a data URI, or None without a comma."""
    header, sep, payload = spec[len(DATA_URI_PREFIX):].partition(",")
    if not sep:
        return None
    return header.split(";"), payload


def _media_type(params: list[str]) -> str:
    return params[0].strip() or OCTET_STREAM


def _name(params: list[str]) -> str | None:
    """The ``name=`` parameter, percent-decoded; None when absent or empty."""
    for raw in params[1:]:
        key, sep, value = raw.strip().partition("=")
        if sep and key.strip().lower() == "name":
            return unquote(value.strip(), errors="strict") or None
    return None


def parse_data_uri(spec: str, *, budget: InlineBudget | None = None) -> DataUri:
    """Parse and decode ``data:<media>[;<param>]*,<data>`` (§4.2).

    Parameters in any order; ``base64`` required in any case; ``name=`` optional; other
    parameters ignored. The whitespace inside the base64 is removed, then the decoded size
    is computed and counted by *budget* BEFORE any decoding. An empty payload is a 0-byte
    file. Raises ``AttachmentError`` (``invalid_data_uri`` or ``attachments_too_large``).
    """
    parts = _split(spec)
    if parts is None:
        raise _error(ERROR_INVALID_DATA_URI, "malformed data URI (missing comma)")
    params, payload = parts
    if not any(param.strip().lower() == "base64" for param in params[1:]):
        raise _error(ERROR_INVALID_DATA_URI, "only base64 data URIs are supported (data:<mime>;base64,...)")
    try:
        name = _name(params)
    except UnicodeDecodeError:
        raise _error(
            ERROR_INVALID_DATA_URI, "invalid name= encoding in data URI (percent-encoded UTF-8 expected)",
        ) from None
    data = "".join(payload.split())
    size = decoded_size(data)
    if budget is not None:
        budget.add(size)
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise _error(ERROR_INVALID_DATA_URI, "invalid base64 payload in data URI") from None
    return DataUri(name, _media_type(params), size, raw)


def data_uri_size(spec: str) -> int | None:
    """Decoded size of a data URI without decoding it; None when malformed (the server reports it)."""
    parts = _split(spec)
    if parts is None:
        return None
    return _decoded_size_or_none("".join(parts[1].split()))


def data_uri_label(spec: str) -> str:
    """The error label of a data URI: never the URI itself, which can be tens of MB (§4.2)."""
    parts = _split(spec)
    if parts is None:
        return f"{DATA_URI_PREFIX}{spec[len(DATA_URI_PREFIX):_LABEL_RAW_END]}…"
    params = parts[0]
    media = _media_type(params)[:MEDIA_TYPE_MAX_CHARS]
    try:
        name = _name(params)
    except UnicodeDecodeError:
        name = None
    if not name:
        return f"{DATA_URI_PREFIX}{media}"
    from twicc.core.services.attachments.staging import name_max_bytes, normalize_filename

    return f"{DATA_URI_PREFIX}{media} {normalize_filename(name, name_max_bytes())}"


def default_name(media_type: str, n: int) -> str:
    """``attachment-<n>`` plus the extension of *media_type* (``.bin`` when unknown) (D10)."""
    base = media_type.split(";", 1)[0].strip().lower()
    extension = (mimetypes.guess_extension(base) if base else None) or ".bin"
    return f"attachment-{n}{extension}"


def sanitize_media_type(value: object) -> str:
    """*value* when it is a ``type/subtype`` token of at most 255 characters, else octet-stream (§4.8.2)."""
    if isinstance(value, str) and len(value) <= MEDIA_TYPE_MAX_CHARS and _MEDIA_TYPE.fullmatch(value):
        return value
    return OCTET_STREAM


def entries_from_legacy_blocks(images: list | None, documents: list | None) -> list[dict]:
    """Wire entries ``{name, media_type, data}`` from the SDK blocks of an older peer (D16).

    Images first, then documents, in order. Any media type is kept (sanitized later). A
    ``base64`` source keeps its data; a ``text`` source is encoded as UTF-8 then base64. The
    name is the block ``title`` or ``name``, else ``attachment-<n>.<ext>`` (``n`` from 1).
    The caller has checked the block shape.
    """
    entries: list[dict] = []
    for n, block in enumerate([*(images or []), *(documents or [])], start=1):
        source = block.get("source") or {}
        is_text = source.get("type") == "text"
        media_type = source.get("media_type") or (TEXT_PLAIN if is_text else OCTET_STREAM)
        if not isinstance(media_type, str):
            media_type = OCTET_STREAM
        data = source.get("data") or ""
        if is_text:
            data = base64.b64encode(str(data).encode("utf-8")).decode("ascii")
        title = block.get("title") or block.get("name")
        name = title if isinstance(title, str) and title else default_name(media_type, n)
        entries.append({"name": name, "media_type": media_type, "data": data})
    return entries
```

- [ ] **Step 6: Add the one-shot API to `staging.py`.**

Replace the imports at `staging.py:10-22` by:

```python
import errno
import logging
import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO

import orjson

from twicc.core.services.attachments.types import AttachmentRef, PreparedEntry, PromotedEntry, StagedEntry
from twicc.paths import get_artifacts_dir, get_composer_attachments_dir
from twicc.uploads.store import COPY_BLOCK_SIZE, FILENAME_MAX_BYTES, TEMP_FILE_PREFIX, candidate_names, now_iso
```

Add to `__all__` (`staging.py:26-44`, keep it sorted): `"ERROR_STAGE_FAILED"`, `"ONESHOT_MARKER"`, `"ORIGIN_API"`, `"ORIGIN_CLI"`, `"discard_staged"`, `"name_max_bytes"`, `"new_bucket"`, `"stage_bytes"`, `"stage_path"`.

After the marker constants (`staging.py:55-60`), add:

```python
ONESHOT_MARKER = "oneshot.json"
ERROR_STAGE_FAILED = "attachment_stage_failed"
# Origins of a one-shot entry (phase 2 design D13): the CLI process on its own, or a command
# running inside the backend (RPC, MCP).
ORIGIN_CLI = "cli"
ORIGIN_API = "api"
_ORIGINS = frozenset({ORIGIN_CLI, ORIGIN_API})
# Room kept for a `` (n)`` suffix in a final name (the same room as ``uploads.views``).
NAME_SUFFIX_ROOM = 8
# The reaper removes an empty bucket: the creation of an entry is retried this many times.
_CREATE_ATTEMPTS = 3
# Characters of the raw name an error shows before the name is normalized.
_STAGE_LABEL_CHARS = 64
```

Append at the end of the "File names" section (after `normalize_filename`, `staging.py:138-151`):

```python
def name_max_bytes() -> int:
    """Longest file name the staging area accepts, keeping room for a `` (n)`` suffix.

    ``PC_NAME_MAX`` is read on the deepest existing directory of the staging area, so a normalized
    name always passes the later ``PC_NAME_MAX`` check of the target.
    """
    path = os.path.realpath(get_composer_attachments_dir())
    while not os.path.isdir(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    try:
        name_max = os.pathconf(path, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = None
    if name_max is not None and 0 < name_max < 255:
        return max(1, min(FILENAME_MAX_BYTES, name_max - NAME_SUFFIX_ROOM))
    return FILENAME_MAX_BYTES
```

Append a new section after `on_upload_completed` (before `# ── Promotion ──`):

```python
# ── One-shot entries (CLI, RPC, MCP) ──
#
# Phase 2 design §4.3: no tus upload fills them, so no creation lock, no release tombstone
# check and no settle rule. ``oneshot.json`` is written before ``file/`` exists, so an entry
# never exists without it: the reaper removes it after 24 h, ready or not.


def new_bucket(origin: str) -> str:
    """A fresh bucket for one request: ``<origin>-<uuid4>``."""
    if origin not in _ORIGINS:
        raise ValueError(f"Unknown staging origin: {origin!r}")
    return f"{origin}-{uuid.uuid4()}"


def _mkdir_entry(entry: Path) -> None:
    """Create the entry directory (its own function: the race test replaces it)."""
    entry.mkdir()


def _create_oneshot_entry(bucket: str, origin: str) -> tuple[AttachmentRef, Path]:
    """Create ``<bucket>/<new id>/`` with ``oneshot.json``, then ``file/``."""
    _validate_key(bucket, "bucket")
    root = _staging_root()
    for attempt in range(_CREATE_ATTEMPTS):
        ref = AttachmentRef(bucket, str(uuid.uuid4()))
        bucket_dir = root / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        entry = bucket_dir / ref.id
        try:
            _mkdir_entry(entry)
        except FileNotFoundError:
            # The reaper removed the empty bucket between the two ``mkdir`` calls.
            if attempt == _CREATE_ATTEMPTS - 1:
                raise
            continue
        try:
            write_marker(entry, ONESHOT_MARKER, {"origin": origin, "at": now_iso()})
            (entry / FILE_DIR).mkdir()
        except BaseException:
            # The caller never gets the entry: remove it here, or a directory without its
            # one-shot marker would wait for the 30-day draft rule.
            shutil.rmtree(entry, ignore_errors=True)
            raise
        return ref, entry
    raise AssertionError("unreachable")


def _remove_partial(entry: Path | None) -> None:
    if entry is not None:
        shutil.rmtree(entry, ignore_errors=True)


def _stage(write: Callable[[BinaryIO], None], name: str, *, bucket: str, origin: str) -> AttachmentRef:
    """Steps 1-5 of §4.3.1: entry and marker, name, temporary write + fsync + rename, ready marker."""
    if origin not in _ORIGINS:
        raise ValueError(f"Unknown staging origin: {origin!r}")
    entry: Path | None = None
    # The error label: the raw name can be an unbounded ``name=`` of a data URI (RPC, MCP), so
    # it is cut until the normalized name replaces it.
    label = name[:_STAGE_LABEL_CHARS]
    try:
        ref, entry = _create_oneshot_entry(bucket, origin)
        # A name read from the filesystem may hold undecodable bytes (surrogate escapes).
        filename = normalize_filename(os.fsencode(name).decode("utf-8", "replace"), name_max_bytes())
        label = filename
        file_dir = entry / FILE_DIR
        tmp = file_dir / f"{TEMP_FILE_PREFIX}{uuid.uuid4().hex}.tmp"
        with open(tmp, "xb") as out:
            write(out)
            out.flush()
            os.fsync(out.fileno())
        final = file_dir / filename
        os.replace(tmp, final)
        fsync_dir(file_dir)
        write_marker(entry, READY_MARKER, {"filename": filename, "size": os.stat(final).st_size})
        return ref
    except OSError as exc:
        _remove_partial(entry)
        raise AttachmentError(ERROR_STAGE_FAILED, f"Cannot stage {label!r}: {exc.strerror or exc}") from exc
    except BaseException:
        _remove_partial(entry)
        raise


def stage_path(source: str | os.PathLike, *, bucket: str, origin: str, name: str | None = None) -> AttachmentRef:
    """Copy the file at *source* into a new one-shot entry; *name* defaults to its base name.

    Always a real copy, never a hard link of the user's file (D18).
    """
    source = os.fspath(source)

    def write(out: BinaryIO) -> None:
        with open(source, "rb") as reader:
            shutil.copyfileobj(reader, out, COPY_BLOCK_SIZE)

    return _stage(write, os.path.basename(source) if name is None else name, bucket=bucket, origin=origin)


def stage_bytes(data: bytes, name: str, *, bucket: str, origin: str) -> AttachmentRef:
    """Write *data* into a new one-shot entry named *name*."""
    return _stage(lambda out: out.write(data), name, bucket=bucket, origin=origin)


def discard_staged(refs: Iterable[AttachmentRef]) -> None:
    """Remove staged entries, then their bucket when it is empty. Never touches ``artifacts/``."""
    for ref in refs:
        try:
            entry = _real_entry_dir(validate_ref(ref))
        except AttachmentError:
            continue
        if entry is None:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        try:
            os.rmdir(entry.parent)
        except OSError:
            pass
```

Update the module docstring (`staging.py:1-8`): after the layout sentence, add "A one-shot entry (CLI, RPC, MCP) also holds ``oneshot.json``. Phase 2 design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.3."

- [ ] **Step 7: Move `_composer_name_max_bytes` out of `uploads/views.py`.**
  - Delete `_composer_name_max_bytes` (`uploads/views.py:433-448`).
  - Replace `_NAME_SUFFIX_ROOM = 8` (`:356`) by `_NAME_SUFFIX_ROOM = staging.NAME_SUFFIX_ROOM`.
  - Replace the call at `:463` by:

```python
    filename = staging.normalize_filename(body.filename, await asyncio.to_thread(staging.name_max_bytes))
```

- [ ] **Step 8: Add the reaper rule.** In `src/twicc/composer_attachments_cleanup_task.py`:
  - Docstring item 1 (`:7-10`): add the sentence "An entry with ``oneshot.json`` (CLI, RPC, MCP) is removed once :data:`ONESHOT_ENTRY_AGE` old; that rule wins."
  - After `DRAFT_ENTRY_AGE` (`:50-51`) add:

```python
# A one-shot entry (CLI, RPC, MCP): nobody can send it twice (phase 2 design §4.3.2).
ONESHOT_ENTRY_AGE = timedelta(hours=24)
```

  - Replace the body of `_entry_is_expired` (`:92-99`) by:

```python
def _entry_is_expired(ref: AttachmentRef, now: datetime) -> bool:
    """A real entry directory (bucket and entry not symlinks) old enough for its retention rule."""
    entry = _staging_root() / ref.bucket / ref.id
    if _lstat_mtime(entry.parent, stat.S_IFDIR) is None:
        return False
    mtime = _lstat_mtime(entry, stat.S_IFDIR)
    if os.path.lexists(entry / staging.ONESHOT_MARKER):
        return _is_older(mtime, ONESHOT_ENTRY_AGE, now)
    committed = os.path.lexists(entry / staging.COMMITTED_MARKER)
    return _is_older(mtime, COMMITTED_ENTRY_AGE if committed else DRAFT_ENTRY_AGE, now)
```

- [ ] **Step 9: Run the new tests and see them pass.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_attachments_inline.py tests/test_attachments_oneshot_staging.py tests/test_composer_attachments_cleanup.py -q`
Expected: PASS.

- [ ] **Step 10: Run the regression suites and lint.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachments_staging.py tests/test_composer_attachments_uploads.py tests/test_composer_attachments_api.py tests/test_uploads_create.py tests/test_composer_attachment_commit.py -q && uvx ruff check src/twicc/core/services/attachments/inline.py src/twicc/core/services/attachments/staging.py src/twicc/uploads/views.py src/twicc/composer_attachments_cleanup_task.py tests/test_attachments_inline.py tests/test_attachments_oneshot_staging.py tests/test_composer_attachments_cleanup.py`
Expected: PASS, no lint error.

- [ ] **Step 11: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/core/services/attachments/inline.py src/twicc/core/services/attachments/staging.py src/twicc/uploads/views.py src/twicc/composer_attachments_cleanup_task.py tests/test_attachments_inline.py tests/test_attachments_oneshot_staging.py tests/test_composer_attachments_cleanup.py && git commit -F /tmp/phase2-task1.msg
```

Message file `/tmp/phase2-task1.msg`:

```
feat(attachments): add inline limits and one-shot staging entries

Add the inline module, the one place that knows the 50 MB limit of inline
data and the 72 MB request body cap. It parses data URIs with an optional
name= parameter, counts decoded sizes before decoding, converts the SDK
blocks of an older peer into wire entries, and sizes peer write timeouts.

Add one-shot staging entries for the CLI, the RPC and the MCP: a real copy
or bytes written under a oneshot.json marker, then a ready marker. The
reaper removes them after 24 hours. name_max_bytes moves from the uploads
view to the staging module so the CLI never imports a view.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 2 (spec T2): Move the plan-target resolution out of `asgi.py`

**Files:**
- Create: `src/twicc/agent/hybrid_switch.py`
- Modify: `src/twicc/core/services/attachments/target.py:19-42` (imports, `__all__`), append the moved functions at the end
- Modify: `src/twicc/asgi.py:39-46` (imports), delete `:457-517`
- Modify: `tests/test_hybrid_attachment_delivery.py:496-619`

**Interfaces:**
- Consumes: `target.resolve_plan_target(*, provider, effective_settings, directory, hybrid, ephemeral, live_agent)`.
- Produces: `twicc.agent.hybrid_switch._PENDING_HYBRID_SWITCHES`, `is_hybrid_switch_pending(session_id)`; `target._read_session_hybrid(session_id)`, `target.resolve_session_hybrid(session_id)`, `target.resolve_existing_session_plan_target(...)`. `asgi` keeps the names `_PENDING_HYBRID_SWITCHES` and `resolve_existing_session_plan_target` (same objects).

- [ ] **Step 1: Point the tests at the new modules.** In `tests/test_hybrid_attachment_delivery.py`, add at the imports: `from twicc.agent import hybrid_switch` and `from twicc.core.services.attachments import target as target_module`. Then, in `:501-619`:
  - `clean_pending`: `hybrid_switch._PENDING_HYBRID_SWITCHES.clear()` (both lines).
  - Every `asgi.is_hybrid_switch_pending(` → `hybrid_switch.is_hybrid_switch_pending(`.
  - `test_session_hybrid_reads_pending_membership_before_the_database`: `hybrid_switch._PENDING_HYBRID_SWITCHES` instead of `asgi._PENDING_HYBRID_SWITCHES`; `monkeypatch.setattr(target_module, "_read_session_hybrid", …)` (three places); `target_module.resolve_session_hybrid(SESSION_ID)` (three places).
  - `test_existing_session_target_consumes_pending_membership`: drop the local `from … import target as target_module`; `monkeypatch.setattr(target_module, "_read_session_hybrid", …)`; `hybrid_switch._PENDING_HYBRID_SWITCHES.add(SESSION_ID)`; call `target_module.resolve_existing_session_plan_target(...)`.
  - Add one identity test after `clean_pending`:

```python
def test_asgi_and_the_switch_module_share_one_pending_set():
    assert asgi._PENDING_HYBRID_SWITCHES is hybrid_switch._PENDING_HYBRID_SWITCHES
    assert asgi.resolve_existing_session_plan_target is target_module.resolve_existing_session_plan_target
```

- [ ] **Step 2: Run and see it fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_hybrid_attachment_delivery.py -q`
Expected: FAIL (`ImportError: cannot import name 'hybrid_switch'`).

- [ ] **Step 3: Create `src/twicc/agent/hybrid_switch.py`.**

```python
"""Session ids whose switch to hybrid CLI mode is in flight (composer attachments design §6.2).

The WS ``set_session_hybrid`` handler adds an id synchronously before it spawns the detached
switch, and removes it once the switch ends (success, failure or cancellation), always AFTER the
switch wrote ``Session.hybrid``. The composer sends ``set_session_hybrid`` and ``send_message``
back to back, so a send planned while the switch runs must already target the hybrid CLI.
Import-free: the attachment plan target reads it from outside ``asgi.py``.
"""

_PENDING_HYBRID_SWITCHES: set[str] = set()


def is_hybrid_switch_pending(session_id: str) -> bool:
    """True while a switch of *session_id* to hybrid CLI mode is in flight."""
    return session_id in _PENDING_HYBRID_SWITCHES
```

- [ ] **Step 4: Move the resolution into `target.py`.** In `src/twicc/core/services/attachments/target.py`:
  - Add `from twicc.agent.hybrid_switch import is_hybrid_switch_pending` after `from twicc.core.services.attachments.types import PlanTarget`.
  - `__all__` becomes `["PLATFORM_FIRST_PARTY", "PLATFORM_FLAGS", "PLATFORM_THIRD_PARTY", "resolve_existing_session_plan_target", "resolve_plan_target", "resolve_session_hybrid"]`.
  - Append, verbatim from `asgi.py:471-517` with the call made module-local:

```python
async def _read_session_hybrid(session_id: str) -> bool:
    """``Session.hybrid`` from the database (``False`` without a row)."""
    from twicc.core.models import Session

    hybrid = await sync_to_async(
        lambda: Session.objects.filter(id=session_id).values_list("hybrid", flat=True).first()
    )()
    return bool(hybrid)


async def resolve_session_hybrid(session_id: str) -> bool:
    """Whether a message to the existing *session_id* targets the hybrid CLI.

    The pending membership is read BEFORE the database: the switch writes the flag before it
    leaves the set, so a switch that ends between the two reads is still seen through the flag.
    """
    if is_hybrid_switch_pending(session_id):
        return True
    return await _read_session_hybrid(session_id)


async def resolve_existing_session_plan_target(
    *,
    session_id: str,
    provider: str,
    effective_settings: AgentSettings,
    directory: str,
    ephemeral: bool,
    live_agent: BaseAgent | None,
) -> PlanTarget:
    """The composer attachment ``PlanTarget`` of a message to the existing *session_id*.

    ``hybrid`` comes from :func:`resolve_session_hybrid`, so a pending switch to hybrid
    already shapes the plan (spec §6.2). Shared by the WS handler and the send service
    (phase 2 design §4.5.4). See :func:`resolve_plan_target` for the other fields.
    """
    return await resolve_plan_target(
        provider=provider,
        effective_settings=effective_settings,
        directory=directory,
        hybrid=await resolve_session_hybrid(session_id),
        ephemeral=ephemeral,
        live_agent=live_agent,
    )
```

- [ ] **Step 5: Update `asgi.py`.**
  - Delete `asgi.py:457-517` (the comment block, `_PENDING_HYBRID_SWITCHES`, `is_hybrid_switch_pending`, `_read_session_hybrid`, `resolve_session_hybrid`, `resolve_existing_session_plan_target`).
  - Add with the other `twicc.agent` imports (`:44-46`): `from twicc.agent.hybrid_switch import _PENDING_HYBRID_SWITCHES`.
  - Add after `from twicc.core.services.attachments.staging import AttachmentError` (`:41`): `from twicc.core.services.attachments.target import resolve_existing_session_plan_target`.
  - `_handle_set_session_hybrid` and `_run_switch_hybrid` keep using `_PENDING_HYBRID_SWITCHES` unchanged; `_plan_existing_session_attachments` keeps calling `resolve_existing_session_plan_target` unchanged.

- [ ] **Step 6: Run and see it pass, plus regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_hybrid_attachment_delivery.py tests/test_composer_attachment_send.py tests/test_composer_attachment_target.py tests/test_attachment_ws_ordering.py -q && uvx ruff check src/twicc/agent/hybrid_switch.py src/twicc/core/services/attachments/target.py src/twicc/asgi.py tests/test_hybrid_attachment_delivery.py`
Expected: PASS. `asgi.py` may carry findings older than this task: run `uvx ruff check src/twicc/asgi.py` before the edit and after it, and add none.

- [ ] **Step 7: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/agent/hybrid_switch.py src/twicc/core/services/attachments/target.py src/twicc/asgi.py tests/test_hybrid_attachment_delivery.py && git commit -F /tmp/phase2-task2.msg
```

```
refactor(attachments): resolve the plan target outside the websocket module

Move the pending hybrid switch set to twicc.agent.hybrid_switch and the
existing-session plan target resolution to the attachments target module.
The send service needs both and cannot import asgi.py without a cycle.
The websocket handler imports the same objects, so behavior is unchanged;
the tests now patch the module that owns each global.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 3 (spec T3): Services accept refs from drop requests

The drop wrappers take the send lane, the services plan and commit refs, and the backend releases them on every outcome except an undelivered send. The services still accept the legacy `images`/`documents` here (the CLI keeps sending them until Task 4).

**Files:**
- Create: `src/twicc/core/services/attachments/drop.py`
- Modify: `src/twicc/core/services/send_message.py` (whole module, `:1-229`)
- Modify: `src/twicc/core/services/session_creation.py:16-41` (imports), `:62-104` (outer function), `:107-146` (signature, docstring), `:194-208` (refs), `:477-488` (manager errors)
- Modify: `src/twicc/drop_requests_watcher.py:45-54`
- Modify: `src/twicc/asgi.py:1446-1455` (drop `allow_attachments`)
- Create: `tests/test_attachment_drop_requests.py`
- Modify: `tests/test_composer_attachment_send.py:560`, `:644-752`
- Modify: `tests/test_attachment_only_messages.py:337-346`, `tests/test_ephemeral_creation.py:183` (drop `allow_attachments`)

**Interfaces:**
- Consumes (Task 2): `target.resolve_existing_session_plan_target(*, session_id, provider, effective_settings, directory, ephemeral, live_agent)`. Phase 1: `planner.validate_attachment_frame(payload)`, `planner.plan_attachments_off_loop(refs, target, *, text)`, `planner.describe_attachment_error(exc) -> (code, message, names)`, `lifecycle.delivery_release(refs)() -> None`, `send_lanes.send_lane(session_id)`, `manager.get_live_agent(session_id)`, `manager.send_to_session(..., attachment_plan=)`.
- Produces: `drop.refs_to_release`, `drop.is_attachment_code`, `drop.message_with_names`; `send_message.send_message_from_drop_payload(payload)`; `send_message.send_message_to_session_from_payload(payload, *, release_refs_on_outcome=False)`; `session_creation.create_session_from_drop_payload(payload)`; `create_session_from_payload(payload, *, allow_hybrid=False, allow_ephemeral=False, ephemeral_admission=None, release_refs_on_outcome=False)` (no `allow_attachments`).

- [ ] **Step 1: Write the failing tests.** Create `tests/test_attachment_drop_requests.py`:

```python
"""Drop-request sends and creations that carry staged refs.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.5. The planner, the
plan target, the managers and the staging release are faked; the session rows, the send lanes,
the drop routing and the services are real.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from PIL import Image

from twicc.agent import AgentState, SendDeliveryError, send_lanes
from twicc.core.models import ProcessRun, Project, Session, SessionType
from twicc.core.services import send_message as send_message_service
from twicc.core.services import session_creation
from twicc.core.services.attachments import drop as attachment_drop
from twicc.core.services.attachments import lifecycle, planner, staging
from twicc.core.services.attachments import target as target_module
from twicc.core.services.attachments.planner import AttachmentPlanError
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import (
    AttachmentPlan,
    AttachmentRef,
    PlannedEntry,
    PlanTarget,
    StagedEntry,
)
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload

ID_1 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01"
ID_2 = "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a02"
REFS = (AttachmentRef("cli-bucket", ID_1), AttachmentRef("cli-bucket", ID_2))
WIRE_REFS = [ref._asdict() for ref in REFS]
TARGET = PlanTarget("claude_code", False, False, "opus", False, "first_party")
SESSION_ID = "drop-session"
PROJECT_ID = "-tmp-drop"
# Captured before any fixture replaces it: the "real planner" tests restore it.
REAL_PLAN_ATTACHMENTS = planner.plan_attachments


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return buffer.getvalue()


def _plan(refs=REFS, target=TARGET) -> AttachmentPlan:
    entries = []
    for n, ref in enumerate(refs, start=1):
        source = StagedEntry(ref, f"f{n}.txt", 5, Path(f"/staged/f{n}.txt"), None)
        entries.append(PlannedEntry(ref, n, f"f{n}.txt", "text", n, len(refs), "file", source, None))
    return AttachmentPlan(target, tuple(entries))


class FakeManager:
    def __init__(self):
        self.result = True
        self.error: BaseException | None = None
        self.gate: asyncio.Event | None = None
        self.calls: list = []
        self.events: list = []

    def get_live_agent(self, session_id):
        return None

    async def _run(self, kind, text, settings, kwargs, value):
        self.events.append((kind, text))
        self.calls.append(SimpleNamespace(text=text, settings=settings, kwargs=kwargs))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return value

    async def send_to_session(self, session_id, project_id, cwd, text, *, settings, **kwargs):
        return await self._run("send", text, settings, kwargs, self.result)

    async def create_session(self, session_id, project_id, cwd, text, *, settings, **kwargs):
        return await self._run("create", text, settings, kwargs, session_id)


@pytest.fixture
def drop(monkeypatch, transactional_db):
    from twicc.pending_agent_settings import _pending as pending_settings
    from twicc.pending_session_attributes import _pending as pending_attributes
    from twicc.pending_titles import _pending as pending_titles

    send_lanes._reset_for_tests()
    project = Project.objects.create(id=PROJECT_ID, directory="/tmp/drop")
    Session.objects.create(
        id=SESSION_ID, project=project, provider="claude_code", file_path="drop.jsonl", type=SessionType.SESSION,
    )
    h = SimpleNamespace(manager=FakeManager(), released=[], plan_calls=[], target_calls=[], plan_error=None,
                        pending_settings=pending_settings)

    async def release_refs(refs):
        h.released.append(tuple(refs))

    async def resolve_plan_target(**kwargs):
        h.target_calls.append(kwargs)
        return TARGET._replace(hybrid=kwargs["hybrid"], ephemeral=kwargs["ephemeral"])

    def plan_attachments(refs, target, *, text):
        h.plan_calls.append((refs, target, text))
        if h.plan_error is not None:
            raise h.plan_error
        return _plan(refs, target)

    identity = SimpleNamespace(
        resolve_agent_settings=lambda settings: settings,
        enforce_agent_settings_consistency=lambda settings: settings,
    )
    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    monkeypatch.setattr(target_module, "resolve_plan_target", resolve_plan_target)
    monkeypatch.setattr(planner, "plan_attachments", plan_attachments)
    monkeypatch.setattr(send_message_service, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(send_message_service, "get_provider_helpers", lambda provider: identity)
    monkeypatch.setattr(session_creation, "ensure_provider_running", lambda provider: None)
    monkeypatch.setattr(
        "twicc.agent.registry.get_agent_manager_registry", lambda: SimpleNamespace(get=lambda provider: h.manager),
    )
    yield h
    send_lanes._reset_for_tests()
    for store in (pending_titles, pending_settings, pending_attributes):
        store.pop(SESSION_ID, None)


async def _settle() -> None:
    """Wait for the detached releases scheduled by ``delivery_release``."""
    for _ in range(5):
        pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


async def _until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _run(coro):
    async def scenario():
        result = await coro
        await _settle()
        return result

    return asyncio.run(scenario())


def _send(**extra):
    return _run(execute_drop_payload({"session_id": SESSION_ID, "text": "look", **extra}, "session:send_message"))


def _create(**extra):
    payload = {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello", **extra}
    return _run(execute_drop_payload(payload, "session:create"))


# ── Drop rules ───────────────────────────────────────────────────────────────


def test_refs_to_release_keeps_valid_unique_refs_in_order():
    payload = {"attachments": [WIRE_REFS[1], "junk", WIRE_REFS[0], WIRE_REFS[1], {"bucket": "b", "id": "nope"}]}
    assert attachment_drop.refs_to_release(payload) == (REFS[1], REFS[0])
    assert attachment_drop.refs_to_release({"attachments": "x"}) == ()
    assert attachment_drop.refs_to_release({}) == ()


@pytest.mark.parametrize(("code", "expected"), [
    ("attachment_missing", True), ("attachments_with_command", True), ("attachment_commit_failed", True),
    ("agent_starting", False), ("send_failed", False), (None, False),
])
def test_attachment_codes(code, expected):
    assert attachment_drop.is_attachment_code(code) is expected


def test_the_session_kinds_route_to_the_drop_wrappers():
    assert _KIND_HANDLERS["session:send_message"][:2] == (
        "twicc.core.services.send_message", "send_message_from_drop_payload",
    )
    assert _KIND_HANDLERS["session:create"][:2] == (
        "twicc.core.services.session_creation", "create_session_from_drop_payload",
    )


# ── Send to an existing session ──────────────────────────────────────────────


def test_refs_are_planned_then_released_once_delivered(drop):
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "sent", status
    assert drop.manager.calls[0].kwargs["attachment_plan"] == _plan()
    assert drop.plan_calls == [(REFS, TARGET, "look")]
    target_call = drop.target_calls[0]
    assert (target_call["ephemeral"], target_call["live_agent"], target_call["hybrid"]) == (False, None, False)
    assert target_call["directory"] == "/tmp/drop"
    assert drop.released == [REFS]


def test_refs_alone_are_content(drop):
    assert _send(text="", attachments=WIRE_REFS)["status"] == "sent"


def test_a_send_without_refs_plans_nothing(drop):
    assert _send()["status"] == "sent"
    assert "attachment_plan" not in drop.manager.calls[0].kwargs
    assert (drop.plan_calls, drop.released) == ([], [])


def test_a_send_not_delivered_now_keeps_its_refs(drop):
    drop.manager.result = False
    status = _send(attachments=WIRE_REFS)
    assert status["errors"][0]["code"] == "send_failed"
    assert drop.released == []


@pytest.mark.parametrize(("error", "field", "code"), [
    (SendDeliveryError("disk full", code="attachment_commit_failed"), "attachments", "attachment_commit_failed"),
    (SendDeliveryError("Attachments cannot be sent with a command", code="attachments_with_command"),
     "attachments", "attachments_with_command"),
    (SendDeliveryError("The agent is starting", code="agent_starting"), "session", "agent_starting"),
    (RuntimeError("busy"), "session", "manager_busy"),
])
def test_manager_errors_are_mapped_and_release_the_refs(drop, error, field, code):
    drop.manager.error = error
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "rejected"
    assert [(e["field"], e["code"]) for e in status["errors"]] == [(field, code)]
    assert drop.released == [REFS]


def test_a_delivery_error_keeps_the_names_it_carries(drop):
    error = SendDeliveryError("Attachment not found", code="attachment_missing")
    error.names = ("shot.png",)
    drop.manager.error = error
    assert _send(attachments=WIRE_REFS)["errors"][0]["message"] == "Attachment not found: shot.png"


@pytest.mark.parametrize("error", [
    AttachmentPlanError("attachments_with_command", "Attachments cannot be sent with a command"),
    AttachmentPlanError("attachment_requires_artifacts", "Ephemeral sessions only accept native attachments",
                        names=("clip.mp4",)),
    AttachmentError("attachment_missing", "Attachment not found"),
    AttachmentError("attachment_not_ready", "Upload not complete"),
])
def test_plan_errors_reject_release_and_send_nothing(drop, error):
    drop.plan_error = error
    status = _send(attachments=WIRE_REFS)
    expected = str(error) + (": clip.mp4" if getattr(error, "names", ()) else "")
    assert status["errors"] == [{"field": "attachments", "code": error.code, "message": expected}]
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_business_rejection_releases_the_refs(drop):
    status = _run(execute_drop_payload(
        {"session_id": "no-such-session", "text": "look", "attachments": WIRE_REFS}, "session:send_message",
    ))
    assert status["errors"][0]["code"] == "session_not_found"
    assert drop.released == [REFS]


def test_malformed_refs_release_the_valid_ones(drop):
    status = _send(attachments=[WIRE_REFS[0], WIRE_REFS[0], {"bucket": "b", "id": "nope"}])
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert drop.released == [(REFS[0],)]


def test_an_unexpected_exception_releases_then_fails(drop):
    drop.manager.error = ValueError("boom")
    status = _send(attachments=WIRE_REFS)
    assert status["status"] == "failed"
    assert drop.released == [REFS]


# ── Send lane (D15) ──────────────────────────────────────────────────────────


def test_two_drop_sends_to_one_session_keep_their_order(drop):
    async def scenario():
        drop.manager.gate = asyncio.Event()
        first = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "one"},
                                                         "session:send_message"))
        await _until(lambda: drop.manager.events == [("send", "one")])
        second = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "two"},
                                                          "session:send_message"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert drop.manager.events == [("send", "one")]
        drop.manager.gate.set()
        return await asyncio.gather(first, second)

    statuses = asyncio.run(scenario())
    assert [s["status"] for s in statuses] == ["sent", "sent"]
    assert drop.manager.events == [("send", "one"), ("send", "two")]


def test_a_send_waiting_in_the_lane_reads_the_settings_written_before_it(drop):
    async def scenario():
        async with send_lanes.send_lane(SESSION_ID):  # a WS send holds the lane
            task = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "hi"},
                                                            "session:send_message"))
            for _ in range(20):
                await asyncio.sleep(0.01)
            assert drop.manager.calls == []
            await sync_to_async(lambda: Session.objects.filter(id=SESSION_ID).update(selected_model="updated-model"))()
        return await task

    assert asyncio.run(scenario())["status"] == "sent"
    assert drop.manager.calls[0].settings.selected_model == "updated-model"


def test_a_send_waiting_in_the_lane_sees_a_pending_request_raised_before_it(drop):
    async def scenario():
        async with send_lanes.send_lane(SESSION_ID):
            task = asyncio.create_task(execute_drop_payload(
                {"session_id": SESSION_ID, "text": "hi", "attachments": WIRE_REFS}, "session:send_message",
            ))
            for _ in range(20):
                await asyncio.sleep(0.01)
            now = timezone.now()
            await sync_to_async(lambda: ProcessRun.objects.create(
                provider="claude_code", session_id=SESSION_ID, twicc_pid=os.getpid(), started_at=now,
                state=AgentState.USER_TURN.value, last_state_change_at=now, awaiting_user_input=True,
            ))()
        status = await task
        await _settle()
        return status

    status = asyncio.run(scenario())
    assert status["errors"][0]["code"] == "awaiting_user_input"
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_send_right_after_a_drop_creation_waits_for_it(drop):
    async def scenario():
        drop.manager.gate = asyncio.Event()
        create = asyncio.create_task(execute_drop_payload(
            {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello"},
            "session:create",
        ))
        await _until(lambda: ("create", "hello") in drop.manager.events)
        send = asyncio.create_task(execute_drop_payload({"session_id": SESSION_ID, "text": "next"},
                                                        "session:send_message"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert ("send", "next") not in drop.manager.events
        drop.manager.gate.set()
        return await asyncio.gather(create, send)

    created, sent = asyncio.run(scenario())
    assert (created["status"], sent["status"]) == ("created", "sent")
    assert drop.manager.events == [("create", "hello"), ("send", "next")]


# ── The real planner, per provider ───────────────────────────────────────────


@pytest.fixture
def real_plan(drop, monkeypatch, tmp_path):
    """The phase 1 planner on real staged files; only the settings-file reads are skipped."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    monkeypatch.setattr(planner, "plan_attachments", REAL_PLAN_ATTACHMENTS)

    async def resolve_plan_target(**kwargs):
        drop.target_calls.append(kwargs)
        return PlanTarget(kwargs["provider"], kwargs["hybrid"], kwargs["ephemeral"], "opus", False, "first_party")

    monkeypatch.setattr(target_module, "resolve_plan_target", resolve_plan_target)
    return drop


def _staged(name: str, data: bytes) -> dict:
    return staging.stage_bytes(data, name, bucket=staging.new_bucket("cli"), origin="cli")._asdict()


@pytest.mark.parametrize(("provider", "hybrid", "modes"), [
    ("claude_code", False, ["inline", "inline", "file"]),
    ("claude_code", True, ["inline", "file", "file"]),
    ("codex", False, ["inline", "file", "file"]),
])
def test_refs_are_planned_for_the_session_provider(real_plan, provider, hybrid, modes):
    Session.objects.filter(id=SESSION_ID).update(provider=provider, hybrid=hybrid)
    attachments = [_staged("shot.png", _png()), _staged("notes.txt", b"hello"), _staged("clip.mp4", b"\x00\x01")]
    status = _send(attachments=attachments)
    assert status["status"] == "sent", status
    plan = real_plan.manager.calls[0].kwargs["attachment_plan"]
    assert [entry.name for entry in plan.entries] == ["shot.png", "notes.txt", "clip.mp4"]
    # Hybrid: the text document is a file entry, never lost (phase 1 D18).
    assert [entry.mode for entry in plan.entries] == modes


@pytest.mark.parametrize(("provider", "hybrid", "text"), [
    ("claude_code", True, "/help"),
    ("claude_code", True, "!ls"),
    ("codex", False, "/compact"),
])
def test_a_command_carrying_files_is_refused(real_plan, provider, hybrid, text):
    Session.objects.filter(id=SESSION_ID).update(provider=provider, hybrid=hybrid)
    status = _send(text=text, attachments=[_staged("notes.txt", b"x")])
    assert status["errors"][0]["code"] == "attachments_with_command"
    assert status["errors"][0]["field"] == "attachments"
    assert real_plan.manager.calls == []


# ── Session creation ─────────────────────────────────────────────────────────


def test_a_drop_creation_plans_its_refs_and_releases_them_after_success(drop):
    status = _create(attachments=WIRE_REFS)
    assert status["status"] == "created", status
    assert drop.manager.calls[0].kwargs["attachment_plan"] == _plan()
    assert drop.released == [REFS]


def test_a_drop_creation_plan_error_leaves_no_stash_and_releases(drop):
    drop.plan_error = AttachmentPlanError("attachments_with_command", "Attachments cannot be sent with a command")
    status = _create(attachments=WIRE_REFS)
    assert status["errors"][0]["field"] == "attachments"
    assert SESSION_ID not in drop.pending_settings
    assert drop.manager.calls == []
    assert drop.released == [REFS]


def test_a_drop_creation_commit_error_keeps_its_field(drop):
    drop.manager.error = SendDeliveryError("Cannot place 'a.txt'", code="attachment_commit_failed")
    status = _create(attachments=WIRE_REFS)
    assert status["errors"] == [
        {"field": "attachments", "code": "attachment_commit_failed", "message": "Cannot place 'a.txt'"},
    ]
    assert drop.released == [REFS]


def test_the_ws_creation_never_releases_its_refs(drop):
    drop.manager.error = SendDeliveryError("busy", code="agent_starting")
    result = _run(session_creation.create_session_from_payload(
        {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello",
         "attachments": WIRE_REFS},
        allow_hybrid=True, allow_ephemeral=True,
    ))
    assert result.success is False
    assert drop.released == []


def test_the_ws_creation_still_passes_legacy_blocks(drop):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    result = _run(session_creation.create_session_from_payload(
        {"session_id": SESSION_ID, "project_id": PROJECT_ID, "provider": "claude_code", "text": "hello",
         "images": [image]},
        allow_hybrid=True, allow_ephemeral=True,
    ))
    assert result.success, result.errors
    assert drop.manager.calls[0].kwargs["images"] == [image]
```

- [ ] **Step 2: Update the phase 1 tests to the removed switch.** In `tests/test_composer_attachment_send.py`:
  - `:560` becomes `assert "allow_attachments" not in create.await_args.kwargs` followed by `assert "release_refs_on_outcome" not in create.await_args.kwargs`.
  - In the creation tests `:644-735`, delete every `allow_attachments=True` argument (lines 645, 667, 680-681, 691, 698, 705, 714-715, 724, 730-731); keep the other keyword arguments.
  - Delete `test_drop_request_creation_rejects_refs` (`:737-743`) and `test_drop_request_send_rejects_refs` (`:746-752`): the new file covers the drop paths.
  - Delete the import `from twicc.core.services.send_message import send_message_to_session_from_payload` (`:40`): `test_drop_request_send_rejects_refs` was its only user (ruff `F401` otherwise). `create_session_from_payload` (`:41`) stays: the creation tests `:644-735` use it.

  The other callers of the removed keyword: in `tests/test_attachment_only_messages.py`, delete the line `allow_attachments=True,` of `test_create_session_still_requires_text_with_composer_refs` (`:345`) and change its docstring (`:337`) to `"""Composer refs do not stand in for the prompt either."""`; in `tests/test_ephemeral_creation.py:183`, `allow_ephemeral=True, allow_attachments=True,` becomes `allow_ephemeral=True,`. After these edits, `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && grep -rn "allow_attachments" tests` lists only the `"allow_attachments" not in create.await_args.kwargs` assertion of `tests/test_composer_attachment_send.py` (`:560`).

- [ ] **Step 3: Run and see the failures.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_attachment_drop_requests.py tests/test_composer_attachment_send.py tests/test_attachment_only_messages.py tests/test_ephemeral_creation.py -q`
Expected: FAIL. `tests/test_attachment_drop_requests.py` fails at collection (`ModuleNotFoundError: ...attachments.drop`). In the three other files, the creation tests that carry refs fail on their assertions, not with a `TypeError`: the current `create_session_from_payload` still accepts `allow_attachments` with the default `False`, so it answers `invalid_attachments` (`session_creation.py:196-201`, checked before `empty_text`) until Step 6 removes the switch. This includes `test_create_session_still_requires_text_with_composer_refs` (expects `empty_text`, gets `invalid_attachments`) and the ephemeral test of `tests/test_ephemeral_creation.py` (expects `attachment_requires_artifacts`).

- [ ] **Step 4: Create `src/twicc/core/services/attachments/drop.py`.**

```python
"""Rules of the drop-request wrappers of the services that take attachment refs.

For a drop-request caller (CLI, RPC, MCP), the backend owns the refs once it has the payload
(D14): the wrapper releases them on every outcome, except a send the manager did not deliver
now. Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.5.
"""

from twicc.core.services.attachments.staging import AttachmentError, validate_ref
from twicc.core.services.attachments.types import AttachmentRef

# Codes a manager or the committer raises about the attachments themselves (phase 1 §8).
_ATTACHMENT_CODE_PREFIXES = ("attachment_", "attachments_")


def refs_to_release(payload: dict) -> tuple[AttachmentRef, ...]:
    """The valid refs of ``payload["attachments"]``, unique, in order (best effort).

    Used to release the refs of a payload whatever its outcome, a malformed one included.
    """
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return ()
    refs: list[AttachmentRef] = []
    for item in raw:
        try:
            ref = validate_ref(item)
        except AttachmentError:
            continue
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def is_attachment_code(code: str | None) -> bool:
    """True for an error code about the attachments (``attachment_*``, ``attachments_*``)."""
    return isinstance(code, str) and code.startswith(_ATTACHMENT_CODE_PREFIXES)


def message_with_names(exc: BaseException) -> str:
    """The message of *exc*, with the file names it carries (``names``) appended."""
    names = tuple(getattr(exc, "names", ()) or ())
    message = str(exc)
    return f"{message}: {', '.join(names)}" if names else message
```

- [ ] **Step 5: Rewrite `src/twicc/core/services/send_message.py`.** Replace the whole module by:

```python
"""Send a message to an existing agent session from a generic payload.

Called for ``kind="session:send_message"`` (drop-request watcher, and the in-backend transport
of the RPC and the MCP) through :func:`send_message_from_drop_payload`. The session is identified
by ``session_id`` (already existing in DB); provider and project are looked up from it.

The function does NOT raise for business-rule errors (missing session, stale
session, agent awaiting user input, etc.); it returns a
:class:`SendMessageResult` with ``success=False`` and a list of structured
error tuples. Unexpected exceptions propagate normally and are the caller's
responsibility to translate (e.g. to ``status: failed`` in the watcher).
"""

from __future__ import annotations

import os
from typing import NamedTuple
from uuid import uuid4

from asgiref.sync import sync_to_async

from twicc.agent.send_lanes import send_lane
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.services.attachments import drop as attachment_drop
from twicc.core.services.attachments import lifecycle as attachment_lifecycle
from twicc.core.services.attachments import planner as attachment_planner
from twicc.core.services.attachments import target as plan_target
from twicc.core.services.attachments.staging import AttachmentError
from twicc.providers.helpers import AgentSettings, get_provider_helpers
from twicc.providers.state import (
    ProviderDisabledError,
    ensure_provider_running,
)


class SendMessageError(NamedTuple):
    field: str
    code: str
    message: str


class SendMessageResult(NamedTuple):
    success: bool
    session_id: str | None
    provider: str | None
    project_id: str | None
    errors: list[SendMessageError] | None
    # Generic passthrough to the CLI's status payload (see the watcher's
    # ``_RESULT_ID_FIELDS``). Carries ``last_line`` — the transcript cursor a
    # ``--wait-reply`` measures "strictly past" against. NEVER put a "status"
    # key in here.
    status_extra: dict = {}


def _rejected(field: str, code: str, message: str) -> SendMessageResult:
    return SendMessageResult(False, None, None, None, [SendMessageError(field, code, message)])


async def send_message_from_drop_payload(payload: dict) -> SendMessageResult:
    """Drop-request handler for ``kind="session:send_message"`` (phase 2 design §4.5.1).

    Takes the session's send lane before any read of the session row, so two sends to one
    session keep their order whatever their entry point (D15). The backend owns the refs of
    the payload from here (D14).
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return await send_message_to_session_from_payload(payload, release_refs_on_outcome=True)
    async with send_lane(session_id):
        return await send_message_to_session_from_payload(payload, release_refs_on_outcome=True)


async def send_message_to_session_from_payload(
    payload: dict, *, release_refs_on_outcome: bool = False,
) -> SendMessageResult:
    """Send ``text`` (and optional attachments) to an existing session.

    Expected keys in ``payload``:
    - ``session_id``: id of the existing target session (required).
    - ``text``: message body. Required unless the payload carries at least one
      attachment: both providers accept a user message made only of attachments
      (creating a session still demands text — that is where the title comes
      from; see :mod:`twicc.core.services.session_creation`).
    - ``attachments``: staged refs ``[{bucket, id}, ...]``, planned for the
      session's provider once its settings are resolved, then committed by the
      manager (phase 1 pipeline).
    - ``images``, ``documents``: legacy SDK blocks of an older CLI, passed as is.

    With ``release_refs_on_outcome`` (drop-request callers), the refs are released
    on every outcome, except a send the manager did not deliver now: a parked send
    holds them, and the retention reaper removes them otherwise (§4.5.3).

    Business-rule rejections (returned as ``success=False``):
    - ``session_not_found``: no row in DB for that id.
    - ``is_subagent``: the row exists but is a subagent. Subagents cannot be
      messaged directly; the parent session is the right target.
    - ``session_stale``: ``Session.stale=True`` (file gone from disk).
    - ``project_no_directory``: the owning project has no directory set.
    - ``provider_disabled``: the owning provider was disabled in settings.
    - ``awaiting_user_input``: a live ProcessRun for this session is blocked
      on a user click (tool approval, ``AskUserQuestion``, Codex approval).
      Sending a message from the CLI would not unblock it — the user must
      resolve the pending dialog in the UI first.
    - attachment codes (field ``attachments``): ``invalid_attachments``,
      ``attachment_missing``, ``attachment_not_ready``, ``attachments_with_command``,
      ``attachment_commit_failed``, …
    """
    refs = attachment_drop.refs_to_release(payload) if release_refs_on_outcome else ()
    keep_refs = False
    try:
        result, keep_refs = await _send(payload)
        return result
    finally:
        if not keep_refs:
            attachment_lifecycle.delivery_release(refs)()


async def _send(payload: dict) -> tuple[SendMessageResult, bool]:
    """The send itself. The flag is True when the refs must stay (a send not delivered now)."""
    session_id = payload.get("session_id")
    raw_text = payload.get("text") or ""
    text = raw_text.strip()
    images = payload.get("images") or []
    documents = payload.get("documents") or []
    try:
        refs = attachment_planner.validate_attachment_frame(payload)
    except AttachmentError as exc:
        return _rejected("attachments", exc.code, str(exc)), False

    errors: list[SendMessageError] = []
    if not session_id:
        errors.append(SendMessageError("session_id", "missing", "session_id is required"))
    if not text and not images and not documents and not refs and not (
        isinstance(payload.get("async_questions"), dict) and payload["async_questions"].get("answers")
    ):
        errors.append(SendMessageError(
            "text", "empty_text", "text is required (unless the message carries attachments)",
        ))
    if errors:
        return SendMessageResult(False, None, None, None, errors), False

    # --- session lookup -------------------------------------------------
    from twicc.core.models import ProcessRun, Session, SessionType
    session = await sync_to_async(
        lambda: Session.objects.select_related("project").filter(id=session_id).first()
    )()
    if session is None:
        return _rejected("session_id", "session_not_found", f"Session {session_id!r} not found"), False
    if session.type == SessionType.SUBAGENT:
        return _rejected(
            "session_id", "is_subagent",
            f"Session {session_id!r} is a subagent; subagents cannot be messaged directly. "
            "Target the parent session instead.",
        ), False
    if session.stale:
        return _rejected(
            "session_id", "session_stale",
            f"Session {session_id!r} is stale (its JSONL file no longer exists on disk)",
        ), False

    project = session.project
    if project is None or not project.directory:
        return _rejected(
            "session_id", "project_no_directory", f"Session {session_id!r} has no project directory",
        ), False

    # --- provider resolution --------------------------------------------
    try:
        provider = Provider(session.provider)
    except ValueError:
        return _rejected(
            "session_id", "unknown_provider", f"Session {session_id!r} has unknown provider {session.provider!r}",
        ), False

    if provider == Provider.CODEX:
        text = raw_text

    if "async_questions" in payload and provider != Provider.CODEX:
        return _rejected(
            "async_questions", "async_questions_invalid", "Question answers require a Codex session",
        ), False

    try:
        ensure_provider_running(provider)
    except ProviderDisabledError as e:
        return _rejected("provider", "provider_disabled", str(e)), False

    # --- awaiting_user_input guard --------------------------------------
    # A live ProcessRun blocked on a user click won't consume new CLI
    # messages. A question can be answered from the CLI too
    # (``session <ID> answer-questions``); anything else needs the UI. We
    # refuse rather than enqueue silently.
    twicc_pid = os.getpid()
    row = await sync_to_async(
        lambda: ProcessRun.objects
        .filter(twicc_pid=twicc_pid, session_id=session_id)
        .exclude(state=AgentState.DEAD.value)
        .order_by("-started_at")
        .first()
    )()
    if row is not None and row.awaiting_user_input:
        return _rejected(
            "session_id", "awaiting_user_input",
            f"Session {session_id!r} is awaiting user input (tool approval or pending question). See "
            "'twicc session <ID> pending-requests'; a question is answerable with "
            "'twicc session <ID> answer-questions', anything else needs the UI.",
        ), False

    # --- invoke the agent manager ---------------------------------------
    # Rehydrate the AgentSettings bundle from the Session row so the live
    # agent receives the values currently persisted for this session (None
    # columns mean "use the synced default", which ``resolve_agent_settings``
    # then fills in). Passing all-None here would risk a spurious settings
    # change vs the in-memory agent and trigger an unwanted restart.
    helpers = get_provider_helpers(provider)
    agent_settings = AgentSettings(**{
        field: getattr(session, field) for field in AgentSettings._fields
    })
    effective = helpers.resolve_agent_settings(agent_settings)
    effective = helpers.enforce_agent_settings_consistency(effective)

    from twicc.agent.registry import get_agent_manager_registry
    manager = get_agent_manager_registry().get(provider)

    # Staged refs: planned for this session (phase 1 planner, §4.5.2), passed only when
    # there are refs, so a send without files keeps its exact manager call.
    plan_kwargs: dict = {}
    if refs:
        try:
            target = await plan_target.resolve_existing_session_plan_target(
                session_id=session_id,
                provider=provider.value,
                effective_settings=effective,
                directory=project.directory,
                ephemeral=False,
                live_agent=manager.get_live_agent(session_id),
            )
            plan_kwargs["attachment_plan"] = await attachment_planner.plan_attachments_off_loop(
                refs, target, text=text,
            )
        except AttachmentError as exc:
            code, message, _names = attachment_planner.describe_attachment_error(exc)
            return _rejected("attachments", code, message), False

    try:
        delivered = await manager.send_to_session(
            session_id, session.project_id, project.directory, text,
            settings=effective, images=images, documents=documents,
            **plan_kwargs,
            **({"async_questions": payload.get("async_questions"),
                "request_id": payload.get("_send_request_id") or str(uuid4()),
                "send_origin": payload.get("_send_origin", "internal")} if provider == Provider.CODEX else {}),
        )
    except RuntimeError as e:
        code = getattr(e, "code", None) or "manager_busy"
        if attachment_drop.is_attachment_code(code):
            return _rejected("attachments", code, attachment_drop.message_with_names(e)), False
        return _rejected("session", code, str(e)), False

    if delivered is False:
        # Maybe parked by the manager: the parked entry holds the refs and their release.
        return _rejected("session", "send_failed", "The message was not accepted for delivery"), True

    # Read **after** the agent has taken the message, and server-side: it is
    # the highest line the watcher had indexed at that instant, so every line
    # past it was written afterwards. A caller reading it itself, once the
    # command has returned, would be racing the reply it is about to wait for.
    last_line = await sync_to_async(
        lambda: Session.objects.filter(id=session_id)
        .values_list("last_line", flat=True).first()
    )()

    return SendMessageResult(
        success=True,
        session_id=session_id,
        provider=provider.value,
        project_id=session.project_id,
        errors=None,
        status_extra={"last_line": last_line or 0},
    ), False
```

- [ ] **Step 6: Update `src/twicc/core/services/session_creation.py`.**
  - Imports (`:24-30`): delete `from twicc.core.services.attachments.planner import ERROR_INVALID_ATTACHMENTS`; add `from twicc.agent.send_lanes import send_lane`, `from twicc.core.services.attachments import drop as attachment_drop`, `from twicc.core.services.attachments import lifecycle as attachment_lifecycle`.
  - Module docstring (`:3-7`): the drop path is `create_session_from_drop_payload`.
  - Replace the outer function (`:62-104`) by:

```python
async def create_session_from_drop_payload(payload: dict) -> SessionCreationResult:
    """Drop-request handler for ``kind="session:create"`` (phase 2 design §4.5.1).

    Takes the send lane of the new session id (the request uuid): a send that arrives right
    after the row appears queues behind the creation instead of meeting its pending admission.
    The backend owns the refs of the payload from here and releases them on every outcome (D14).
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return await create_session_from_payload(payload, release_refs_on_outcome=True)
    async with send_lane(session_id):
        return await create_session_from_payload(payload, release_refs_on_outcome=True)


async def create_session_from_payload(
    payload: dict, *, allow_hybrid: bool = False, allow_ephemeral: bool = False,
    ephemeral_admission=None, release_refs_on_outcome: bool = False,
) -> SessionCreationResult:
    """Create a session; with ``release_refs_on_outcome`` (drop-request callers), release the refs.

    The WS handler never passes ``release_refs_on_outcome``: it owns its refs with the phase 1
    rules (release on delivery only, so the browser can retry a failure).
    """
    owned_refs = attachment_drop.refs_to_release(payload) if release_refs_on_outcome else ()
    try:
        return await _admit_and_create(
            payload, allow_hybrid=allow_hybrid, allow_ephemeral=allow_ephemeral,
            ephemeral_admission=ephemeral_admission,
        )
    finally:
        attachment_lifecycle.delivery_release(owned_refs)()


async def _admit_and_create(
    payload: dict, *, allow_hybrid: bool, allow_ephemeral: bool, ephemeral_admission,
) -> SessionCreationResult:
    """Admit ephemeral creation before any asynchronous operation or buffer write."""
```

  Keep the body of the old function (`:72-104`) as the body of `_admit_and_create`, with the call at `:95-98` changed to `_create_session_from_payload(payload, allow_hybrid=allow_hybrid, ephemeral=ephemeral, ephemeral_admission=ephemeral_admission)`.
  - `_create_session_from_payload` signature (`:107-110`): drop `allow_attachments`. Docstring line about `attachments` (`:136-139`) becomes: "``attachments``: staged refs ``[{bucket, id}, ...]`` (the WS composer, the CLI, the RPC, the MCP). They are planned once the settings are resolved and enforced, BEFORE any ``set_pending_*`` stash, so a plan error leaves no stash behind."
  - Replace `:194-208` by:

```python
    errors: list[SessionCreationError] = []
    try:
        attachment_refs = attachment_planner.validate_attachment_frame(payload)
    except AttachmentError as e:
        return SessionCreationResult(False, None, None, None, [SessionCreationError("attachments", e.code, str(e))])
```

  - Replace the manager error mapping (`:485-488`) by:

```python
    except RuntimeError as e:
        code = getattr(e, "code", "manager_busy")
        if attachment_drop.is_attachment_code(code):
            names = tuple(getattr(e, "names", ()) or ())
            return SessionCreationResult(
                False, None, None, None,
                [SessionCreationError("attachments", code, attachment_drop.message_with_names(e))],
                error_names=names,
            )
        return SessionCreationResult(False, None, None, None, [
            SessionCreationError("session", code, str(e))
        ])
```

- [ ] **Step 7: Route the drop kinds.** In `src/twicc/drop_requests_watcher.py:45-54`, the two entries become:

```python
    "session:create": (
        "twicc.core.services.session_creation",
        "create_session_from_drop_payload",
        "created",
    ),
    "session:send_message": (
        "twicc.core.services.send_message",
        "send_message_from_drop_payload",
        "sent",
    ),
```

- [ ] **Step 8: Drop the switch in the WS handler.** In `src/twicc/asgi.py`, replace `:1446-1455` as one block (from the comment `# Composer refs, planned by the service (trusted path:` to the closing `)` of the `create_session_from_payload(` call) by the block below. Lines `:1449-1450` and `:1451` (blank) are inside the replaced range and inside the new block, so they appear once:

```python
                    # Composer refs, planned by the service. The WS path keeps owning
                    # them (phase 1 rules: released on delivery only, below).
                    "attachments": [ref._asdict() for ref in attachment_refs],
                    **agent_settings_kwargs_from_frontend_payload(content),
                }

                result = await create_session_from_payload(
                    payload, allow_hybrid=True, allow_ephemeral=True,
                    ephemeral_admission=ephemeral_admission,
                )
```

- [ ] **Step 9: Run and see them pass, plus regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_attachment_drop_requests.py tests/test_composer_attachment_send.py tests/test_drop_transport.py tests/test_drop_request_statuses.py tests/test_wait_reply.py tests/test_send_messages_wait.py tests/test_codex_async_question_send.py tests/test_ephemeral_creation.py tests/test_attachment_only_messages.py tests/test_hybrid_attachment_delivery.py -q && uvx ruff check src/twicc/core/services/attachments/drop.py src/twicc/core/services/send_message.py src/twicc/core/services/session_creation.py src/twicc/drop_requests_watcher.py src/twicc/asgi.py tests/test_attachment_drop_requests.py tests/test_composer_attachment_send.py tests/test_attachment_only_messages.py tests/test_ephemeral_creation.py`
Expected: PASS. For the existing files (`send_message.py`, `session_creation.py`, `drop_requests_watcher.py`, `asgi.py` and the three existing test files), run the same `uvx ruff check <file>` before the edit and after it: no new finding (all are clean at `f6e4b342`).

- [ ] **Step 10: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/core/services/attachments/drop.py src/twicc/core/services/send_message.py src/twicc/core/services/session_creation.py src/twicc/drop_requests_watcher.py src/twicc/asgi.py tests/test_attachment_drop_requests.py tests/test_composer_attachment_send.py tests/test_attachment_only_messages.py tests/test_ephemeral_creation.py && git commit -F /tmp/phase2-task3.msg
```

```
feat(attachments): accept staged refs from drop requests

Route session:create and session:send_message through drop wrappers. They
take the per-session send lane before any read of the session row, so a
CLI, RPC or MCP send keeps its order behind a web send and never applies
stale settings. The send service now plans refs with the phase 1 planner
and passes the plan to the manager. The backend owns drop-request refs:
it releases them on every outcome except a send that was not delivered
now, which a parked send still holds. Attachment error codes keep the
attachments field and their file names.

The allow_attachments switch is gone; the web path keeps its own rules.
Legacy images and documents stay accepted until the CLI stops sending
them.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 4 (spec T4): CLI stages `--attach` files and sends refs

`send-message`, `send-messages` and `create-session` resolve their `--attach` values with a new helper module, stage them as one-shot entries and send refs. The drop wrappers now refuse the legacy fields. `peer-send` and `cli/_drop_request/attachments.py` stay until Task 6.

**Files:**
- Create: `src/twicc/cli/_drop_request/attach_sources.py`
- Modify: `src/twicc/cli/_drop_request/help_strings.py` (add constants after `NO_EXPAND_HELP`, `:46-59`, before the builder functions)
- Modify: `src/twicc/cli/send_message/command.py:7`, `:35-48`, `:139-160`, `:234-327`
- Modify: `src/twicc/cli/send_messages.py:1-30` (docstring), `:38`, `:68-81`, `:236-328`
- Modify: `src/twicc/cli/_drop_request/session_lookup.py:63` (stale docstring)
- Modify: `src/twicc/cli/create_session/command.py:198-211`, `:315-333`, `:481-510`, `:561-587`
- Modify: `src/twicc/core/services/attachments/drop.py` (legacy rule), `src/twicc/core/services/send_message.py` (wrapper, `_send`), `src/twicc/core/services/session_creation.py` (wrapper)
- Create: `tests/test_cli_attach.py`
- Modify: `tests/test_attachment_drop_requests.py` (legacy refusal tests), `tests/test_attachment_only_messages.py:1-7`, `:50-67` (refs as the only content)
- Modify: `tests/test_wait_reply.py:1085-1086` (`_run_send_message` docstring)

**Interfaces:**
- Consumes (Task 1): `inline.parse_data_uri`, `inline.data_uri_label`, `inline.default_name`, `inline.InlineBudget`, `inline.ERROR_TOO_LARGE`, `inline.INLINE_TOO_LARGE_HINT`; `staging.new_bucket`, `stage_path`, `stage_bytes`, `discard_staged`, `ORIGIN_CLI`, `ORIGIN_API`. Existing: `transport._in_backend()`, `_output.in_api_mode()`, `remote_scheme.has_remote_scheme`, `validation.ValidationError(field, code, message)`, `output.emit_validation_errors(errors)`.
- Produces: `attach_sources.AttachSource`, `resolve(attach, *, hint, count_paths=False)`, `stage(sources, *, bucket)`, `staging_origin()`, `new_request_bucket()`, `as_payload(refs)`; `help_strings.ATTACH_HELP`, `ATTACH_EVERY_MESSAGE_HELP`; `drop.LEGACY_FIELDS_MESSAGE`, `drop.has_legacy_fields(payload)`.

- [ ] **Step 1: Write the failing CLI tests.** Create `tests/test_cli_attach.py`:

```python
"""--attach of send-message, send-messages and create-session: resolve, stage, send refs.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4. The transport
is faked (no backend); the commands, the session rows and the staging store are real.
"""

import base64
import os

import orjson
import pytest
from typer.testing import CliRunner

from twicc.cli import app
from twicc.cli._drop_request import attach_sources, bootstrap_local, transport, whoami
from twicc.cli._drop_request.polling import PollOutcome
from twicc.cli._output import _capture, _Sink
from twicc.core.models import Project, Session
from twicc.core.services.attachments import inline, staging
from twicc.core.services.attachments.staging import AttachmentError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture
def sessions(db, tmp_path):
    project = Project.objects.create(id="attach-project", directory=str(tmp_path))
    for sid, provider in (("alpha", "claude_code"), ("beta", "codex")):
        Session.objects.create(
            id=sid, project=project, provider=provider, file_path=f"{sid}.jsonl",
            created_at="2026-10-07T10:00:00Z", user_message_count=1,
        )
    return project


@pytest.fixture
def submitted(monkeypatch, data_dir, sessions):
    """The submitted ``(kind, payload)`` pairs; every request answers ``sent`` / ``created``."""
    calls: list = []

    class _Submission:
        def __init__(self, kind, payload):
            self.request_uuid = f"req-{len(calls)}"
            self.status = "created" if kind == "session:create" else "sent"
            self.session_id = payload.get("session_id", "new-session")

        def outcome(self):
            return PollOutcome(self.status, {
                "session_id": self.session_id, "provider": "claude_code", "project_id": "attach-project",
                "last_line": 3,
            }, True)

        def poll(self):
            return self.outcome()

        def was_received(self):
            return True

        def cleanup(self):
            pass

    def submit(payload, *, kind):
        calls.append((kind, payload))
        return _Submission(kind, payload)

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", lambda sub, timeout_seconds: sub.outcome())
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    # The empty data dir has no settings.json: without ``disabledProviders``, create-session
    # refuses every provider with ``no_provider_configured`` (validation.py:52-57).
    monkeypatch.setattr(bootstrap_local, "read_synced_settings", lambda: {
        "disabledProviders": [], "defaultProvider": "claude_code",
    })
    return calls


def _invoke(*args):
    return CliRunner().invoke(app, list(args))


def _file(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _uri(data: bytes, header: str = "text/plain;base64") -> str:
    return f"data:{header},{base64.b64encode(data).decode()}"


def _staged(payload):
    return [staging.load_entry(staging.validate_ref(item)) for item in payload["attachments"]]


def _staging_entries():
    root = staging.get_composer_attachments_dir()
    if not root.exists():
        return []
    return [entry for bucket in root.iterdir() for entry in bucket.iterdir()]


# ── attach_sources ───────────────────────────────────────────────────────────


def test_resolution_errors_are_validation_errors(tmp_path, data_dir):
    missing = str(tmp_path / "missing.txt")
    sources, errors = attach_sources.resolve(
        [str(tmp_path), missing, "remote:/srv/x.bin", "data:text/plain;base64,aGk"],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert sources == []
    assert [(e.field, e.code) for e in errors] == [
        (f"--attach {tmp_path}", "not_a_file"),
        (f"--attach {missing}", "not_a_file"),
        ("--attach remote:/srv/x.bin", "remote_requires_remote"),
        ("--attach data:text/plain", "invalid_data_uri"),
    ]


def test_a_relative_path_is_refused_over_the_api(data_dir):
    token = _capture.set(_Sink())
    try:
        _sources, errors = attach_sources.resolve(["notes.txt"], hint=inline.INLINE_TOO_LARGE_HINT)
    finally:
        _capture.reset(token)
    assert [e.code for e in errors] == ["relative_path"]


def test_a_data_uri_without_name_is_named_by_its_position(tmp_path, data_dir):
    path = _file(tmp_path, "a.txt", b"a")
    sources, errors = attach_sources.resolve(
        [str(path), _uri(PNG, "image/png;base64"), _uri(b"x", "text/plain;name=n%C3%A9.txt;base64")],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert errors == []
    assert [(s.name, s.path, s.data) for s in sources] == [
        ("a.txt", str(path), None), ("attachment-2.png", None, PNG), ("né.txt", None, b"x"),
    ]


def test_only_inline_data_counts_toward_the_limit(tmp_path, data_dir, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    big_path = _file(tmp_path, "big.bin", b"x" * 20)
    sources, errors = attach_sources.resolve(
        [str(big_path), _uri(b"y" * 6), _uri(b"z" * 6)], hint=inline.INLINE_TOO_LARGE_HINT,
    )
    assert [s.name for s in sources] == ["big.bin", "attachment-2.txt"]
    [error] = errors
    assert error.code == "attachments_too_large"
    assert error.message.endswith(inline.INLINE_TOO_LARGE_HINT)


def test_peer_counting_includes_the_paths(tmp_path, data_dir, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    path = _file(tmp_path, "a.bin", b"x" * 6)
    _sources, errors = attach_sources.resolve(
        [str(path), _uri(b"y" * 6)], hint=inline.PEER_TOO_LARGE_HINT, count_paths=True,
    )
    assert [e.code for e in errors] == ["attachments_too_large"]
    assert errors[0].message.endswith(inline.PEER_TOO_LARGE_HINT)


def test_stage_keeps_the_order_and_the_origin(tmp_path, data_dir):
    sources, _errors = attach_sources.resolve(
        [str(_file(tmp_path, "one.txt", b"1")), _uri(b"2", "text/plain;name=two.txt;base64")],
        hint=inline.INLINE_TOO_LARGE_HINT,
    )
    refs, errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
    assert errors == []
    assert [staging.load_entry(ref).filename for ref in refs] == ["one.txt", "two.txt"]
    assert all(ref.bucket.startswith("cli-") for ref in refs)
    assert attach_sources.as_payload(refs) == [{"bucket": ref.bucket, "id": ref.id} for ref in refs]


def test_the_origin_is_api_inside_the_backend(monkeypatch):
    monkeypatch.setattr(transport, "_in_backend", lambda: True)
    assert attach_sources.staging_origin() == "api"
    assert attach_sources.new_request_bucket().startswith("api-")


# ── send-message ─────────────────────────────────────────────────────────────


def test_send_message_stages_any_file_and_sends_refs(tmp_path, submitted):
    files = [
        _file(tmp_path, "shot.png", PNG),
        _file(tmp_path, "report.pdf", b"%PDF-1.4 x"),
        _file(tmp_path, "notes.txt", b"hello"),
        _file(tmp_path, "clip.mp4", os.urandom(64)),
        _file(tmp_path, "empty.bin", b""),
    ]
    result = _invoke("send-message", "alpha", "look", *[arg for f in files for arg in ("--attach", str(f))])
    assert result.exit_code == 0, result.output
    [(kind, payload)] = submitted
    assert kind == "session:send_message"
    assert "images" not in payload and "documents" not in payload
    staged = _staged(payload)
    assert [e.filename for e in staged] == ["shot.png", "report.pdf", "notes.txt", "clip.mp4", "empty.bin"]
    for entry, source in zip(staged, files, strict=True):
        assert entry.path.read_bytes() == source.read_bytes()
        assert os.stat(entry.path).st_ino != os.stat(source).st_ino
    assert all(e.ref.bucket.startswith("cli-") for e in staged)


def test_a_local_file_above_the_inline_limit_is_accepted(tmp_path, submitted, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    result = _invoke("send-message", "alpha", "--attach", str(_file(tmp_path, "big.bin", b"x" * 20)))
    assert result.exit_code == 0, result.output
    assert _staged(submitted[0][1])[0].size == 20


def test_attachments_alone_need_no_prompt(tmp_path, submitted):
    result = _invoke("send-message", "alpha", "--attach", str(_file(tmp_path, "a.txt", b"a")))
    assert result.exit_code == 0, result.output
    assert submitted[0][1]["text"] == ""


def test_no_prompt_and_no_attach_is_missing_prompt(submitted):
    result = _invoke("send-message", "alpha")
    assert result.exit_code == 1
    assert orjson.loads(result.output)["errors"][0]["code"] == "missing_prompt"


def test_attach_errors_stage_nothing_and_submit_nothing(tmp_path, submitted):
    missing = str(tmp_path / "missing.txt")
    result = _invoke("send-message", "alpha", "hi", "--attach", str(_file(tmp_path, "a.txt", b"a")),
                     "--attach", missing)
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    assert output["errors"] == [{
        "field": f"--attach {missing}", "code": "not_a_file",
        "message": f"file {missing!r} does not exist or is not a regular file",
    }]
    assert submitted == []
    assert _staging_entries() == []


def test_a_staging_failure_discards_the_earlier_entries(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def failing(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise AttachmentError("attachment_stage_failed", "Cannot stage 'b.txt': No space left on device")
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", failing)
    result = _invoke("send-message", "alpha", "hi",
                     "--attach", str(_file(tmp_path, "a.txt", b"a")), "--attach", str(_file(tmp_path, "b.txt", b"b")))
    assert result.exit_code == 1
    assert orjson.loads(result.output)["errors"][0]["code"] == "attachment_stage_failed"
    assert submitted == []
    assert _staging_entries() == []


def test_an_interruption_while_staging_discards_and_submits_nothing(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def interrupted(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", interrupted)
    files = [_file(tmp_path, f"{n}.txt", b"x") for n in "abc"]
    result = _invoke("send-message", "alpha", "hi", *[arg for f in files for arg in ("--attach", str(f))])
    assert result.exit_code != 0
    assert submitted == []
    assert _staging_entries() == []


def test_a_data_uri_name_never_escapes_the_entry(submitted):
    result = _invoke(
        "send-message", "alpha", "hi",
        "--attach", _uri(b"1", "text/plain;name=..%2F..%2Fescape.txt;base64"),
        "--attach", _uri(b"2", "text/plain;name=.twicc-upload-x;base64"),
    )
    assert result.exit_code == 0, result.output
    staged = _staged(submitted[0][1])
    assert [e.filename for e in staged] == [".._.._escape.txt", "_.twicc-upload-x"]
    for entry in staged:
        assert entry.path.parent == staging.entry_dir(entry.ref) / "file"


# ── send-messages ────────────────────────────────────────────────────────────


def test_a_bad_attach_is_one_global_validation_error(tmp_path, submitted):
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi", "--attach", str(tmp_path / "nope"))
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    assert output["errors"][0]["code"] == "not_a_file"
    assert submitted == []


def test_each_recipient_gets_its_own_copy_whatever_its_provider(tmp_path, submitted):
    source = _file(tmp_path, "clip.mp4", os.urandom(32))
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi", "--attach", str(source))
    assert result.exit_code == 0, result.output
    assert [payload["session_id"] for _kind, payload in submitted] == ["alpha", "beta"]
    [alpha], [beta] = (_staged(payload) for _kind, payload in submitted)
    assert alpha.ref != beta.ref
    assert os.stat(alpha.path).st_ino != os.stat(beta.path).st_ino
    for _kind, payload in submitted:
        assert "images" not in payload and "documents" not in payload


def test_the_no_file_branch_writes_no_legacy_keys(submitted):
    result = _invoke("send-messages", "alpha", "--message", "hi")
    assert result.exit_code == 0, result.output
    assert set(submitted[0][1]) == {"session_id", "_send_origin", "_send_request_id", "text"}


def test_a_staging_failure_is_a_per_id_error_and_keeps_earlier_recipients(tmp_path, submitted, monkeypatch):
    real_stage_path = staging.stage_path
    calls = []

    def failing_for_beta(source, **kwargs):
        calls.append(source)
        if len(calls) == 2:
            raise AttachmentError("attachment_stage_failed", "Cannot stage 'a.txt': Permission denied")
        return real_stage_path(source, **kwargs)

    monkeypatch.setattr(staging, "stage_path", failing_for_beta)
    result = _invoke("send-messages", "alpha", "beta", "--message", "hi",
                     "--attach", str(_file(tmp_path, "a.txt", b"a")))
    output = orjson.loads(result.output)
    assert output["results"]["alpha"]["status"] == "sent"
    assert output["results"]["beta"]["status"] == "validation_error"
    assert output["results"]["beta"]["errors"][0]["code"] == "attachment_stage_failed"
    assert [payload["session_id"] for _kind, payload in submitted] == ["alpha"]
    assert len(_staged(submitted[0][1])) == 1  # alpha's copy is kept for its send


# ── create-session ───────────────────────────────────────────────────────────


def test_create_session_stages_refs(tmp_path, submitted):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = _invoke("create-session", "--project", str(project_dir), "--provider", "claude_code",
                     "--attach", str(_file(tmp_path, "notes.txt", b"n")), "hello")
    assert result.exit_code == 0, result.output
    [(kind, payload)] = submitted
    assert kind == "session:create"
    assert "images" not in payload and "documents" not in payload
    assert [e.filename for e in _staged(payload)] == ["notes.txt"]


def test_create_session_hidden_constraint_stays_a_local_error_and_stages_nothing(tmp_path, submitted):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = _invoke("create-session", "--project", str(project_dir), "--provider", "claude_code",
                     "--hidden", "--question-widget", "--attach", str(_file(tmp_path, "a.txt", b"a")), "hello")
    assert result.exit_code == 1
    output = orjson.loads(result.output)
    assert output["status"] == "validation_error"
    # The hidden constraint itself, not an earlier provider error (``no_provider_configured``).
    # A non-interactive-mode error may come with it (the trust floor decides the mode).
    assert "hidden_incompatible_with_question_widget" in [e["code"] for e in output["errors"]]
    assert submitted == []
    assert _staging_entries() == []


# ── help ─────────────────────────────────────────────────────────────────────


def test_the_attach_help_states_the_limit_and_the_hint():
    from twicc.cli._drop_request.help_strings import ATTACH_EVERY_MESSAGE_HELP, ATTACH_HELP

    for text in (ATTACH_HELP, ATTACH_EVERY_MESSAGE_HELP):
        assert "any type" in text
        assert "name=" in text
        assert "50 MB" in text
        assert text.endswith(inline.INLINE_TOO_LARGE_HINT)
    assert "per recipient" in ATTACH_EVERY_MESSAGE_HELP
```

Append the legacy refusal tests to `tests/test_attachment_drop_requests.py`:

```python
# ── Legacy fields (an older CLI) ─────────────────────────────────────────────


@pytest.mark.parametrize("legacy", [{"images": [{"type": "image"}]}, {"documents": [{"type": "document"}]}])
def test_a_drop_send_with_legacy_blocks_is_refused_and_releases(drop, legacy):
    status = _send(attachments=WIRE_REFS[:1], **legacy)
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert "older than the server" in status["errors"][0]["message"]
    assert drop.manager.calls == []
    assert drop.released == [(REFS[0],)]


@pytest.mark.parametrize("legacy", [{"images": [{"type": "image"}]}, {"documents": [{"type": "document"}]}])
def test_a_drop_creation_with_legacy_blocks_is_refused(drop, legacy):
    status = _create(**legacy)
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert drop.manager.calls == []


def test_empty_legacy_lists_are_accepted(drop):
    assert _send(images=[], documents=[])["status"] == "sent"
    assert _create(images=[], documents=[])["status"] == "created"


def test_the_send_service_no_longer_passes_legacy_blocks(drop):
    _send()
    assert "images" not in drop.manager.calls[0].kwargs
    assert "documents" not in drop.manager.calls[0].kwargs
```

- [ ] **Step 2: Run and see the failures.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_cli_attach.py tests/test_attachment_drop_requests.py -q`
Expected: FAIL (`ImportError: cannot import name 'attach_sources'`, legacy fields still accepted). The `tests/test_attachment_only_messages.py` rewrite comes with Step 8, where the behavior it pins changes.

- [ ] **Step 3: Create `src/twicc/cli/_drop_request/attach_sources.py`.**

```python
"""Resolve and stage the ``--attach`` values of a CLI command.

A value is a file path (read by this process), ``remote:<path>`` (meaningful only over
``--remote``, where the forwarder turns it into a bare server path before the call), or a base64
data URI with an optional ``name=``. Every resolved file is copied into the composer staging
store as a one-shot entry; the drop payload carries the refs as ``attachments``.
Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from twicc.cli._drop_request import transport
from twicc.cli._drop_request.remote_scheme import has_remote_scheme
from twicc.cli._drop_request.validation import ValidationError
from twicc.cli._output import in_api_mode
from twicc.core.services.attachments import inline, staging
from twicc.core.services.attachments.staging import AttachmentError
from twicc.core.services.attachments.types import AttachmentRef


class AttachSource(NamedTuple):
    """One resolved ``--attach`` value: a file to copy (*path*) or decoded bytes (*data*)."""

    label: str  # the error label: the value itself, or the short label of a data URI
    name: str  # the staged file name, before normalization
    path: str | None
    data: bytes | None


def _field(label: str) -> str:
    return f"--attach {label}"


def staging_origin() -> str:
    """``api`` for a command running inside the backend (RPC, MCP), else ``cli``."""
    return staging.ORIGIN_API if transport._in_backend() else staging.ORIGIN_CLI


def new_request_bucket() -> str:
    """The staging bucket of one command invocation."""
    return staging.new_bucket(staging_origin())


def as_payload(refs: tuple[AttachmentRef, ...]) -> list[dict]:
    """The ``attachments`` value of a drop payload."""
    return [ref._asdict() for ref in refs]


def resolve(
    attach: list[str], *, hint: str, count_paths: bool = False,
) -> tuple[list[AttachSource], list[ValidationError]]:
    """Turn the ``--attach`` strings into sources; every problem is an error of its value.

    Data URIs are decoded and their decoded sizes counted by one :class:`inline.InlineBudget`
    (a path is counted too with *count_paths*: ``peer-send``, whose files all travel inline).
    *hint* ends the ``attachments_too_large`` message. Reads no file content.
    """
    budget = inline.InlineBudget(hint)
    over_budget = False
    sources: list[AttachSource] = []
    errors: list[ValidationError] = []
    for n, spec in enumerate(attach, start=1):
        if inline.is_data_uri(spec):
            label = inline.data_uri_label(spec)
            try:
                uri = inline.parse_data_uri(spec, budget=None if over_budget else budget)
            except AttachmentError as exc:
                errors.append(ValidationError(_field(label), exc.code, str(exc)))
                over_budget = over_budget or exc.code == inline.ERROR_TOO_LARGE
                continue
            sources.append(AttachSource(label, uri.name or inline.default_name(uri.media_type, n), None, uri.data))
            continue
        if has_remote_scheme(spec):
            # Only meaningful over --remote, where the forwarder strips it before the call.
            errors.append(ValidationError(
                _field(spec), "remote_requires_remote", "remote: paths are only valid with --remote",
            ))
            continue
        if in_api_mode() and not os.path.isabs(spec):
            errors.append(ValidationError(
                _field(spec), "relative_path",
                "relative path not allowed over the API (no caller working directory); "
                "pass an absolute path or a data: URI",
            ))
            continue
        if not os.path.isfile(spec):
            errors.append(ValidationError(
                _field(spec), "not_a_file", f"file {spec!r} does not exist or is not a regular file",
            ))
            continue
        if count_paths and not over_budget:
            try:
                budget.add(os.path.getsize(spec))
            except AttachmentError as exc:
                errors.append(ValidationError(_field(spec), exc.code, str(exc)))
                over_budget = True
                continue
        sources.append(AttachSource(spec, os.path.basename(spec), spec, None))
    return sources, errors


def stage(sources: list[AttachSource], *, bucket: str) -> tuple[tuple[AttachmentRef, ...], list[ValidationError]]:
    """Copy *sources* into one-shot entries of *bucket*, in order.

    On a failure, the entries staged by this call are discarded and the error of the failing
    source is returned. An interruption (Ctrl-C) discards them too, then propagates. Once the
    refs are submitted, the backend owns them: the CLI never deletes them afterwards.
    """
    origin = staging_origin()
    refs: list[AttachmentRef] = []
    try:
        for source in sources:
            try:
                if source.path is not None:
                    ref = staging.stage_path(source.path, bucket=bucket, origin=origin, name=source.name)
                else:
                    ref = staging.stage_bytes(source.data, source.name, bucket=bucket, origin=origin)
            except AttachmentError as exc:
                staging.discard_staged(refs)
                return (), [ValidationError(_field(source.label), exc.code, str(exc))]
            refs.append(ref)
    except BaseException:
        staging.discard_staged(refs)
        raise
    return tuple(refs), []
```

- [ ] **Step 4: Add the help texts.** Add to `src/twicc/cli/_drop_request/help_strings.py`, right after `NO_EXPAND_HELP` (before the builder functions), and add `from twicc.core.services.attachments.inline import INLINE_TOO_LARGE_HINT` to its imports (the inline module imports no app code):

```python
_ATTACH_FORMS = (
    "Each value is a local file path, a base64 data URI "
    "(data:<mime>;name=<percent-encoded file name>;base64,<data>; name= is optional), or, over "
    "--remote, remote:<absolute path> to read a file on the server."
)
_INLINE_LIMIT = (
    "Inline data is limited to 50 MB in total per call: data URIs, and local files sent over "
    "--remote (the forwarder turns them into data URIs; on a local command line, Linux caps one "
    "argument at 128 KiB). A file the CLI or the server reads from its own disk has no limit. "
)
ATTACH_HELP = "File to attach (repeatable), of any type. " + _ATTACH_FORMS + " " + _INLINE_LIMIT + INLINE_TOO_LARGE_HINT
ATTACH_EVERY_MESSAGE_HELP = (
    "File to attach to every message (repeatable), of any type; one copy is staged per recipient. "
    + _ATTACH_FORMS + " " + _INLINE_LIMIT + INLINE_TOO_LARGE_HINT
)
```

  The module docstring (`:9-13`) says `--attach` keeps its inline help; replace that paragraph:

```
The set of builders covers every flag whose help text depends on the user's
current providers / presets / defaults. Flags whose help is purely static
(``--timeout``, ``--attach``, ...) keep their inline help in the calling
command — duplicating a one-liner is cheaper than shipping a tiny helper
per flag.
```

  by:

```
The set of builders covers every flag whose help text depends on the user's
current providers / presets / defaults. Static texts that several commands
share, or that are built from the inline attachment limits (``--no-expand``,
the prompt include hint, the ``--attach`` texts), are module-level constants,
defined before the builder functions. Any other static flag (``--timeout``,
...) keeps its inline help in the calling command — duplicating a one-liner is
cheaper than shipping a tiny helper per flag.
```

- [ ] **Step 5: Rewire `send-message`.** In `src/twicc/cli/send_message/command.py`:
  - `:7`: `from twicc.cli._drop_request.help_strings import ATTACH_HELP, NO_EXPAND_HELP, PROMPT_INCLUDE_HINT`.
  - `:35-48`: the option becomes `attach: list[str] = typer.Option([], "--attach", help=ATTACH_HELP),`.
  - `:139-144`: replace the `attachments` import and `from twicc.cli._drop_request import transport` by `from twicc.cli._drop_request import attach_sources, transport`; delete the `load_local_bootstrap` import (`:144`) and `from twicc.providers.helpers import get_provider_helpers` (`:160`); add `from twicc.core.services.attachments.inline import INLINE_TOO_LARGE_HINT`.
  - After the `lookup_session` block (`:234-240`), insert:

```python
    # --attach: resolved now (a missing file, a bad data URI or more than 50 MB of inline
    # data is a local error); staged only once every other local check passed.
    sources, attach_errors = attach_sources.resolve(attach or [], hint=INLINE_TOO_LARGE_HINT)
    if attach_errors:
        emit_validation_errors(attach_errors)
        raise typer.Exit(1)
```

  - `:244-245`: `if prompt is None:` / `if not sources:`.
  - Delete `:275-316` (bootstrap, support dict, settings read, `validate_and_encode`, its errors).
  - Replace the payload (`:318-327`) by:

```python
    # Payload — minimum required for the ``send`` kind. The watcher derives
    # provider, project, cwd, and current settings from the DB row.
    payload = {
        "session_id": resolved.session_id,
        "_send_origin": send_origin,
        "_send_request_id": str(uuid4()),
        "text": text,
    }
    if sources:
        refs, stage_errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
        if stage_errors:
            emit_validation_errors(stage_errors)
            raise typer.Exit(1)
        payload["attachments"] = attach_sources.as_payload(refs)
```

  - In `tests/test_wait_reply.py`, the `_run_send_message` docstring (`:1085-1086`) describes the removed settings read. Replace "(prompt resolution, attachment validation, the settings lookup on the row)" by "(prompt resolution, the session lookup, the ``--attach`` resolution)". The helper body needs no change: it stubs `lookup_session` and passes `attach=[]`.

- [ ] **Step 6: Rewire `send-messages`.** First, in `src/twicc/cli/_drop_request/session_lookup.py:63` (`lookup_session` docstring, stale once no command picks per-provider caps), "(notably ``provider`` to pick the right attachment caps, and" becomes "(notably ``provider``, and". Then, in `src/twicc/cli/send_messages.py`:
  - Docstring `:17-21`: replace the sentence "The attachments are validated/encoded **per session** … receive the message." by "The ``--attach`` values are resolved once: a missing file, a bad data URI or more than 50 MB of inline data fails the whole command. The files are then staged once per recipient: each recipient's send consumes and releases its own copy, and no file is refused by provider."
  - `:38`: `from twicc.cli._drop_request.help_strings import ATTACH_EVERY_MESSAGE_HELP, NO_EXPAND_HELP, PROMPT_INCLUDE_HINT`; `:68-81`: `attach: list[str] = typer.Option([], "--attach", help=ATTACH_EVERY_MESSAGE_HELP),`.
  - `:238-249`: delete the `attachments`, `load_local_bootstrap` and `get_provider_helpers` imports; add `from twicc.cli._drop_request import attach_sources` and `from twicc.core.services.attachments.inline import INLINE_TOO_LARGE_HINT`.
  - After the message resolution (`:253-264`), replace `bootstrap = load_local_bootstrap()` (`:266`) by:

```python
    # One global check of the --attach values (validation_error, exit 1), before any recipient.
    sources, attach_errors = attach_sources.resolve(attach or [], hint=INLINE_TOO_LARGE_HINT)
    if attach_errors:
        emit_validation_errors(attach_errors)
        raise typer.Exit(1)
    bucket = attach_sources.new_request_bucket() if sources else None
```

  - Replace `_prepare` (`:278-328`) by:

```python
    def _prepare(resolved):
        """Per-id: build the send payload and stage this recipient's own copy of the files (D19)."""
        payload = {
            "session_id": resolved.session_id,
            "_send_origin": send_origin,
            "_send_request_id": str(uuid4()),
            "text": prefix_sender_header(
                text,
                caller,
                recipient_id=resolved.session_id,
                recipient_spawned_by_id=resolved.spawned_by_id,
            ),
        }
        if sources:
            refs, stage_errors = attach_sources.stage(sources, bucket=bucket)
            if stage_errors:
                return stage_errors
            payload["attachments"] = attach_sources.as_payload(refs)
        return payload
```

- [ ] **Step 7: Rewire `create-session`.** In `src/twicc/cli/create_session/command.py`:
  - Help `:198-211`: `attach: list[str] = typer.Option([], "--attach", help=ATTACH_HELP),` and import `ATTACH_HELP` with the module's other `help_strings` names (it imports from `twicc.cli._drop_request.help_strings` near the top; add `ATTACH_HELP` to that import).
  - `:315-319`: delete the `attachments` import; change `from twicc.cli._drop_request import transport` to `from twicc.cli._drop_request import attach_sources, transport`; add `from twicc.core.services.attachments.inline import INLINE_TOO_LARGE_HINT`.
  - Replace `:481-510` by:

```python
    # Resolve effective settings (None → synced default, then consistency
    # demotion) for the hidden-session constraints. The back-end service redoes
    # this for the actual session creation; the duplicated call is cheap and local.
    helpers_obj = (
        get_provider_helpers(provider) if provider in bootstrap.providers else None
    )
    if helpers_obj is not None and not errors:
        effective_settings = helpers_obj.resolve_agent_settings(settings)
        effective_settings = helpers_obj.enforce_agent_settings_consistency(
            effective_settings
        )
        errors.extend(validate_hidden_constraints(provider, effective_settings, hidden=hidden))

    sources, attach_errors = attach_sources.resolve(attach or [], hint=INLINE_TOO_LARGE_HINT)
    errors.extend(attach_errors)
```

  - In the payload (`:561-574`), delete the `"images"` and `"documents"` lines.
  - Just before `sub = transport.submit(payload, kind="session:create")` (`:587`), insert:

```python
    # Staged last, after every local check: a refused command leaves no entry behind.
    if sources:
        refs, stage_errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
        if stage_errors:
            emit_validation_errors(stage_errors)
            raise typer.Exit(1)
        payload["attachments"] = attach_sources.as_payload(refs)
```

- [ ] **Step 8: Refuse the legacy fields in the drop wrappers.**
  - Append to `src/twicc/core/services/attachments/drop.py`:

```python
LEGACY_FIELDS = ("images", "documents")
LEGACY_FIELDS_MESSAGE = (
    "images and documents are no longer accepted: this twicc CLI is older than the server. "
    "Update the CLI."
)


def has_legacy_fields(payload: dict) -> bool:
    """True when ``images`` or ``documents`` is non-empty (an empty list or no key is accepted).

    An explicit check: ``validate_attachment_frame`` looks at the legacy fields only when
    refs are present.
    """
    return any(payload.get(field) for field in LEGACY_FIELDS)
```

  - In `send_message.py`, `send_message_from_drop_payload` starts with:

```python
    if attachment_drop.has_legacy_fields(payload):
        attachment_lifecycle.delivery_release(attachment_drop.refs_to_release(payload))()
        return _rejected(
            "attachments", attachment_planner.ERROR_INVALID_ATTACHMENTS, attachment_drop.LEGACY_FIELDS_MESSAGE,
        )
```

  - In `_send`: delete `images = …` and `documents = …`; the empty-text condition becomes `if not text and not refs and not (…)`; the manager call becomes `settings=effective, **plan_kwargs,` (no `images=`, `documents=`). In the service docstring, replace the `images`, `documents` bullet by "``images`` / ``documents`` (an older CLI) are refused by :func:`send_message_from_drop_payload`."
  - In `session_creation.py`, `create_session_from_drop_payload` starts with:

```python
    if attachment_drop.has_legacy_fields(payload):
        attachment_lifecycle.delivery_release(attachment_drop.refs_to_release(payload))()
        return SessionCreationResult(False, None, None, None, [SessionCreationError(
            "attachments", attachment_planner.ERROR_INVALID_ATTACHMENTS, attachment_drop.LEGACY_FIELDS_MESSAGE,
        )])
```

  The WS path (`create_session_from_payload` called by `asgi.py`) keeps passing the browser's legacy fields.
  - In `tests/test_attachment_only_messages.py`, the two service tests that use legacy blocks as the only content (`:50-67`) call `send_message_to_session_from_payload` directly; with the legacy fields gone from `_send`, they would get `empty_text` instead of `session_not_found`. Replace both by the two tests below: in the first, a staged ref is the only content (the ref format is valid; nothing is planned because the session lookup fails first); the second checks that legacy blocks no longer stand in for text:

```python
@pytest.mark.django_db
def test_send_message_accepts_empty_text_with_attachment_refs() -> None:
    """No ``empty_text`` error: validation moves on to the session lookup."""
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "unknown-session", "text": "",
         "attachments": [{"bucket": "cli-b", "id": "6f1c1f0e-8a8e-4c55-9d1e-0b0c8f6c1a01"}]},
    ))
    assert result.success is False
    assert _error_codes(result) == ["session_not_found"]


@pytest.mark.django_db
def test_send_message_ignores_legacy_blocks_as_content() -> None:
    """The service no longer reads ``images`` / ``documents``: they do not stand in for text."""
    result = asyncio.run(send_message_to_session_from_payload(
        {"session_id": "unknown-session", "text": "", "images": [IMAGE_BLOCK], "documents": [DOCUMENT_BLOCK]},
    ))
    assert result.success is False
    assert _error_codes(result) == ["empty_text"]
```

  Update the module docstring (`:1-7`): "Both providers accept a user message made only of attachments, so TwiCC lets the composer (and the CLI, through staged refs) send one to an EXISTING session." The other tests of the file stay: `test_create_session_still_requires_text` calls the WS-path `create_session_from_payload`, which keeps the legacy fields, and the agent tests (`:110-140`) call the agents directly. A sweep of `tests/` for direct callers of the drop services with legacy fields (`grep -rln "send_message_to_session_from_payload\|send_message_from_drop_payload\|session:send_message\|session:create\|create_session_from_payload" tests | xargs grep -ln '"images"\|"documents"\|images=\|documents='`) finds only this file, `tests/test_composer_attachment_send.py` (WS path and fake-manager signatures, unaffected) and `tests/test_codex_async_question_send.py` (calls the Codex manager directly, unaffected).

- [ ] **Step 9: Run and see them pass, plus regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_cli_attach.py tests/test_attachment_drop_requests.py tests/test_attachment_only_messages.py tests/test_composer_attachment_send.py tests/test_codex_async_question_send.py tests/test_wait_reply.py tests/test_send_messages_wait.py tests/test_sender_header.py tests/test_prompt_includes.py tests/test_drop_transport.py tests/test_mcp_server.py tests/test_rpc_auth.py -q && uvx ruff check src/twicc/cli/_drop_request/attach_sources.py src/twicc/cli/_drop_request/help_strings.py src/twicc/cli/_drop_request/session_lookup.py src/twicc/cli/send_message/command.py src/twicc/cli/send_messages.py src/twicc/cli/create_session/command.py src/twicc/core/services/attachments/drop.py src/twicc/core/services/send_message.py src/twicc/core/services/session_creation.py tests/test_cli_attach.py tests/test_attachment_drop_requests.py tests/test_attachment_only_messages.py tests/test_wait_reply.py`
Expected: PASS. For the existing files, compare `uvx ruff check <file>` before and after the edit: no new finding.

- [ ] **Step 10: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/cli/_drop_request/attach_sources.py src/twicc/cli/_drop_request/help_strings.py src/twicc/cli/_drop_request/session_lookup.py src/twicc/cli/send_message/command.py src/twicc/cli/send_messages.py src/twicc/cli/create_session/command.py src/twicc/core/services/attachments/drop.py src/twicc/core/services/send_message.py src/twicc/core/services/session_creation.py tests/test_cli_attach.py tests/test_attachment_drop_requests.py tests/test_attachment_only_messages.py tests/test_wait_reply.py && git commit -F /tmp/phase2-task4.msg
```

```
feat(cli): stage --attach files and send them as refs

send-message, send-messages and create-session accept any file type. Each
--attach value is a local path, a data URI with an optional name=, or
remote: over --remote; data URIs count toward a 50 MB limit of inline data
whose error says what to do with a larger file. The files are copied into
the staging store as one-shot entries, after every other local check, and
the drop payload carries their refs. send-messages checks the values once
and stages one copy per recipient; a file is never refused by provider.

The drop wrappers now refuse non-empty images or documents (an older CLI),
and the send service no longer reads them.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 5 (spec T5): Remote forwarder, RPC and MCP body caps, log redaction

**Files:**
- Create: `src/twicc/log_redaction.py`
- Modify: `src/twicc/cli/_remote.py:30-52` (imports), `:78-83` (`Resolved`), `:153-261` (`resolve_command`, `_navigate`), `:333-361` (`_inline_one`), `:412-428` (`inline_attachments` docstring), `:431-438` (`_PROMPT_PARAM_NAMES` comment), `:631-699` (timeouts), `:742-809` (`forward`)
- Modify: `src/twicc/rpc/views.py:1-14` (imports), `:61-138` (`dispatch`)
- Modify: `src/twicc/mcp/server.py:34-44` (imports), `:63-69` (`INSTRUCTIONS`), `:226-227` (log), `:267-272` (cap), `:295-299` (`EXTERNAL_INSTRUCTIONS`)
- Create: `tests/test_log_redaction.py`, `tests/test_remote_attach.py`, `tests/test_rpc_attach.py`, `tests/test_mcp_attach.py`
- Modify: `tests/test_wait_reply.py:985`

**Interfaces:**
- Consumes (Task 1): `inline.INLINE_MAX_BYTES`, `INLINE_MAX_REQUEST_BYTES`, `INLINE_TOO_LARGE_HINT`, `PEER_TOO_LARGE_HINT`, `PEER_SEND_TIMEOUT_WITH_FILES`, `data_uri_size`, `too_large_message`, `format_mb`. (Task 4): the commands stage data URIs themselves.
- Produces: `log_redaction.redact_for_log(value)`; `_remote.Resolved.explicit`; `_remote.check_inline_size(resolved)`; `_remote.apply_peer_send_timeout(argv, resolved)`; `mcp.server.MAX_REQUEST_BODY_BYTES == INLINE_MAX_REQUEST_BYTES`.

- [ ] **Step 1: Write the failing tests.**

`tests/test_log_redaction.py`:

```python
"""Log lines never carry attachment bytes (phase 2 design §4.6, §4.7)."""

from twicc.log_redaction import redact_for_log


def test_short_values_are_kept():
    assert redact_for_log("x" * 512) == "x" * 512
    assert redact_for_log(["a", 1, None, True]) == ["a", 1, None, True]


def test_long_strings_are_cut_anywhere_in_a_structure():
    long = "--attach=data:image/png;base64," + "A" * 1000
    cut = long[:64] + f"…<{len(long)} chars>"
    assert redact_for_log(long) == cut
    assert redact_for_log(["send-message", long]) == ["send-message", cut]
    assert redact_for_log({"attach": [long], "n": 3, "t": (long,)}) == {"attach": [cut], "n": 3, "t": (cut,)}
```

`tests/test_remote_attach.py`:

```python
"""The --remote forwarder: named data URIs, inline size checks, timeouts (phase 2 design §4.4.3)."""

import base64
import os

import httpx
import orjson
import pytest

from twicc.cli import _remote
from twicc.core.services.attachments import inline

URL = "http://box:3501"


def _inline(argv):
    return _remote.inline_attachments(list(argv), _remote.resolve_command(list(argv)))


@pytest.fixture
def posted(monkeypatch):
    """Every HTTP call of the forwarder: ``("timeout", httpx.Timeout)`` then the decoded body."""
    calls: list = []

    class FakeClient:
        def __init__(self, *, timeout):
            calls.append(("timeout", timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, content, headers):
            calls.append(orjson.loads(content))
            return httpx.Response(200, json={"exit_code": 0, "result": {"ok": True}, "error": None})

    monkeypatch.setattr(_remote.httpx, "Client", FakeClient)
    return calls


def test_a_local_file_becomes_a_named_data_uri(tmp_path):
    path = tmp_path / "capture (1).mp4"
    path.write_bytes(b"\x00\x01")
    out = _inline(["send-message", "sid", "hi", "--attach", str(path)])
    assert out[-1] == "data:video/mp4;name=capture%20%281%29.mp4;base64,AAE="


def test_an_undecodable_local_name_is_sent_as_valid_utf8(tmp_path):
    path = tmp_path / os.fsdecode(b"bad\xff.bin")
    path.write_bytes(b"x")
    out = _inline(["send-message", "sid", "hi", "--attach", str(path)])
    assert out[-1] == "data:application/octet-stream;name=bad%EF%BF%BD.bin;base64,eA=="


def test_data_and_remote_values_keep_their_meaning():
    uri = "data:text/plain;base64,aGk="
    out = _inline(["send-message", "sid", "hi", "--attach", uri, "--attach=remote:/srv/x.bin"])
    assert out[4] == uri
    assert out[5] == "--attach=/srv/x.bin"


def test_the_local_total_above_the_limit_is_refused_before_any_http(tmp_path, monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    uri = "data:text/plain;base64," + base64.b64encode(b"y" * 6).decode()
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", str(path), "--attach", uri])
    assert exc.value.exit_code == 2
    assert inline.INLINE_TOO_LARGE_HINT in str(exc.value)
    assert posted == []


def test_the_peer_send_hint_names_only_a_url(tmp_path, monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["peer-send", "alice", "T", "text", "--attach", str(path)])
    assert str(exc.value).endswith(inline.PEER_TOO_LARGE_HINT)
    assert "remote:" not in str(exc.value)
    assert posted == []


def test_remote_values_do_not_count(monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    assert _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", "remote:/srv/huge.bin"]) == 0
    assert posted[1]["argv"][-1] == "/srv/huge.bin"


def test_a_missing_local_attach_is_still_a_usage_error(tmp_path, posted):
    for value in (str(tmp_path / "missing.bin"), str(tmp_path)):
        with pytest.raises(_remote.RemoteUsageError) as exc:
            _remote.forward(URL, "tok", ["send-message", "sid", "hi", "--attach", value])
        assert "attachment not found" in str(exc.value)
    assert posted == []


def test_a_body_above_the_request_cap_is_refused_before_the_post(monkeypatch, posted):
    monkeypatch.setattr(inline, "INLINE_MAX_REQUEST_BYTES", 200)
    with pytest.raises(_remote.RemoteUsageError) as exc:
        _remote.forward(URL, "tok", ["send-message", "sid", "x" * 500])
    assert exc.value.exit_code == 2
    assert "once encoded" in str(exc.value)
    assert posted == []


def test_the_read_timeout_is_the_effective_timeout_plus_the_margin():
    resolved = _remote.resolve_command(["send-message", "sid", "hi", "--timeout", "90"])
    assert _remote._request_timeout(resolved).read == 90 + _remote._WAIT_TIMEOUT_MARGIN
    resolved = _remote.resolve_command(["send-message", "sid", "hi"])
    assert _remote._request_timeout(resolved).read == 30 + _remote._WAIT_TIMEOUT_MARGIN


def test_a_read_command_keeps_the_default_timeout():
    resolved = _remote.resolve_command(["sessions"])
    assert _remote._request_timeout(resolved).read == _remote._DEFAULT_TIMEOUT


def test_peer_send_with_files_gets_the_long_timeout(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"a")
    argv = ["peer-send", "alice", "T", "text", "--attach", str(path)]
    argv2, resolved = _remote.apply_peer_send_timeout(argv, _remote.resolve_command(argv))
    assert argv2[:2] == ["peer-send", f"--timeout={inline.PEER_SEND_TIMEOUT_WITH_FILES}"]
    assert _remote._request_timeout(resolved).read == inline.PEER_SEND_TIMEOUT_WITH_FILES + _remote._WAIT_TIMEOUT_MARGIN


@pytest.mark.parametrize("argv", [
    ["peer-send", "alice", "T", "text", "--attach", "remote:/srv/a.bin", "--timeout", "40"],
    ["peer-send", "alice", "T", "text"],
])
def test_an_explicit_timeout_or_no_file_keeps_the_peer_send_argv(argv):
    resolved = _remote.resolve_command(argv)
    argv2, resolved2 = _remote.apply_peer_send_timeout(argv, resolved)
    assert argv2 == argv
    assert resolved2 == resolved


def test_the_forwarder_no_longer_imports_the_legacy_module():
    assert not hasattr(_remote, "_sniff_mime")
```

Change `tests/test_wait_reply.py:985` from `assert plain == _remote._DEFAULT_TIMEOUT` to:

```python
    assert plain == 30 + _remote._WAIT_TIMEOUT_MARGIN
```

`tests/test_rpc_attach.py`:

```python
"""/rpc/: inline attachments reach the services; token calls get the 72 MB body cap (§4.6)."""

import asyncio
import contextlib
import logging
from importlib import import_module

import orjson
import pytest
from django.conf import settings as dj_settings
from django.test import AsyncClient, RequestFactory

from twicc.auth import tokens as api_tokens
from twicc.auth.session_auth import SESSION_AUTH_KEY, SESSION_FINGERPRINT_KEY, compute_fingerprint
from twicc.cli._drop_request import whoami
from twicc.core.models import Project, Session
from twicc.core.services import send_message as send_message_service
from twicc.core.services.attachments import inline, staging
from twicc.core.services.send_message import SendMessageResult
from twicc.rpc import views
from twicc.rpc.invoker import InvocationResult

PASSWORD_HASH = "pbkdf2_sha256$test$deadbeef"


def _post(client, path, body=b"", **extra):
    return asyncio.run(client.post(path, data=body, content_type="application/json", **extra))


@contextlib.contextmanager
def _logs(name: str):
    logger = logging.getLogger(name)
    was_disabled, was_level = logger.disabled, logger.level
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


@pytest.fixture
def open_instance(settings, monkeypatch):
    """No password and no token: every /rpc/ call is full scope."""
    settings.TWICC_PASSWORD_HASH = ""
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: False)
    return settings


@pytest.fixture
def invoke_calls(monkeypatch):
    calls: list[list[str]] = []

    def fake_invoke(argv):
        calls.append(list(argv))
        return InvocationResult(exit_code=0, result={"ok": True}, error=None)

    monkeypatch.setattr(views, "invoke", fake_invoke)
    return calls


def test_a_token_call_is_not_capped_by_django(open_instance, invoke_calls, monkeypatch):
    open_instance.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 4096)
    body = orjson.dumps({"argv": ["sessions", "--project", "x" * 2000]})
    response = _post(AsyncClient(), "/rpc/sessions", body)
    assert response.status_code == 200
    assert invoke_calls == [["sessions", "--project", "x" * 2000]]


def test_a_token_call_above_the_cap_gets_413(open_instance, invoke_calls, monkeypatch):
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 64)
    response = _post(AsyncClient(), "/rpc/sessions", orjson.dumps({"argv": ["sessions", "x" * 100]}))
    assert response.status_code == 413
    assert orjson.loads(response.content) == {"error": "Request body too large"}
    assert invoke_calls == []


def test_a_body_without_content_length_is_read_within_the_cap(invoke_calls, monkeypatch):
    monkeypatch.setattr(views, "INLINE_MAX_REQUEST_BYTES", 64)
    factory = RequestFactory()
    small = factory.post("/rpc/sessions", data=orjson.dumps({"argv": ["sessions"]}), content_type="application/json")
    del small.META["CONTENT_LENGTH"]
    assert asyncio.run(views.dispatch(small, "sessions")).status_code == 200
    big = factory.post("/rpc/sessions", data=b"x" * 100, content_type="application/json")
    del big.META["CONTENT_LENGTH"]
    assert asyncio.run(views.dispatch(big, "sessions")).status_code == 413
    assert invoke_calls == [["sessions"]]


def test_a_cookie_call_keeps_the_django_cap(settings, invoke_calls, monkeypatch, transactional_db):
    settings.TWICC_PASSWORD_HASH = PASSWORD_HASH
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024
    monkeypatch.setattr(api_tokens, "has_tokens", lambda: False)
    client = AsyncClient()
    engine = import_module(dj_settings.SESSION_ENGINE)
    store = engine.SessionStore()
    store[SESSION_AUTH_KEY] = True
    store[SESSION_FINGERPRINT_KEY] = compute_fingerprint(PASSWORD_HASH)
    store.create()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key
    response = _post(client, "/rpc/sessions", orjson.dumps({"project": "x" * 2000}))
    assert response.status_code == 400
    assert invoke_calls == []


def test_a_failing_command_logs_no_attachment_bytes(open_instance, monkeypatch):
    def explode(argv):
        raise RuntimeError("boom")

    monkeypatch.setattr(views, "_run_invoke", explode)
    data = "A" * 4000
    body = orjson.dumps({"argv": ["send-message", "sid", "hi", f"--attach=data:image/png;base64,{data}"]})
    with _logs("twicc.rpc.views") as records:
        response = _post(AsyncClient(), "/rpc/send-message", body)
    assert response.status_code == 500
    text = " ".join(record.getMessage() for record in records)
    assert data not in text
    assert "chars>" in text


@pytest.fixture
def rpc_session(transactional_db, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    project = Project.objects.create(id="rpc-project", directory=str(tmp_path))
    Session.objects.create(id="rpc-session", project=project, provider="claude_code", file_path="r.jsonl")
    seen: list[dict] = []

    async def fake_service(payload, *, release_refs_on_outcome=False):
        seen.append(payload)
        return SendMessageResult(True, "rpc-session", "claude_code", "rpc-project", None, {"last_line": 0})

    monkeypatch.setattr(send_message_service, "send_message_to_session_from_payload", fake_service)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


def test_an_rpc_data_uri_is_staged_by_the_backend_and_reaches_the_service(open_instance, rpc_session):
    body = orjson.dumps({
        "session_id": "rpc-session", "prompt": "hi", "attach": ["data:text/plain;name=n.txt;base64,aGk="],
    })
    envelope = orjson.loads(_post(AsyncClient(), "/rpc/send-message", body).content)
    assert envelope["exit_code"] == 0, envelope
    [ref] = [staging.validate_ref(item) for item in rpc_session[0]["attachments"]]
    assert ref.bucket.startswith("api-")
    assert staging.load_entry(ref).filename == "n.txt"


def test_an_absolute_server_path_does_not_count_toward_the_limit(open_instance, rpc_session, tmp_path, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 4)
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 20)
    body = orjson.dumps({"session_id": "rpc-session", "prompt": "hi", "attach": [str(path)]})
    envelope = orjson.loads(_post(AsyncClient(), "/rpc/send-message", body).content)
    assert envelope["exit_code"] == 0, envelope
    assert staging.load_entry(staging.validate_ref(rpc_session[0]["attachments"][0])).size == 20
```

`tests/test_mcp_attach.py`:

```python
"""MCP tools: attach keeps its schema, inline data reaches the services, logs stay small (§4.7)."""

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest
from mcp import types as mcp_types

from twicc.cli._drop_request import whoami
from twicc.core.models import Project, Session
from twicc.core.services import send_message as send_message_service
from twicc.core.services.attachments import inline, staging
from twicc.core.services.send_message import SendMessageResult
from twicc.mcp import server as mcp_server
from twicc.mcp.identity import ExternalCaller, external_caller
from twicc.mcp.tools import iter_mcp_tools

URI = "data:text/plain;name=n.txt;base64,aGk="


@contextlib.contextmanager
def _logs(name: str):
    logger = logging.getLogger(name)
    was_disabled, was_level = logger.disabled, logger.level
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.disabled = was_disabled
        logger.setLevel(was_level)


def test_the_body_cap_is_the_inline_request_cap():
    assert mcp_server.MAX_REQUEST_BODY_BYTES == inline.INLINE_MAX_REQUEST_BYTES == 72 * 1024 * 1024


@pytest.mark.parametrize("name", ["send_message", "send_messages", "create_session"])
def test_attach_stays_an_array_of_strings_with_the_limit_in_its_description(name):
    tool = next(tool for tool in iter_mcp_tools() if tool.name == name)
    attach = tool.input_schema["properties"]["attach"]
    assert (attach["type"], attach["items"]) == ("array", {"type": "string"})
    assert "50 MB" in attach["description"]
    assert "images" not in tool.input_schema["properties"]
    assert "documents" not in tool.input_schema["properties"]


def test_the_instructions_name_the_forms_and_the_limit():
    assert "data URI" in mcp_server.INSTRUCTIONS
    assert "50 MB" in mcp_server.INSTRUCTIONS
    assert "name=" in mcp_server.EXTERNAL_INSTRUCTIONS
    assert "50 MB" in mcp_server.EXTERNAL_INSTRUCTIONS
    assert "file storage service" in mcp_server.EXTERNAL_INSTRUCTIONS


# Claude Code keeps only the first 2048 characters of a server's instructions. The internal
# instructions are already longer (2378 characters at f6e4b342): the batch lines from
# "A timeout/cancellation ..." on are cut today. The attach addition must not push out the
# batch lines that still fit, nor fall outside the cut itself.
CLIENT_INSTRUCTIONS_CUT = 2048
BATCH_LINES_IN_THE_CUT = 6  # "Batch tools are MCP-only wrappers." ... "No rollback, nested batches, ..."


def test_the_attach_line_does_not_push_the_batch_rules_out_of_the_client_cut():
    kept = mcp_server.INSTRUCTIONS[:CLIENT_INSTRUCTIONS_CUT]
    assert "data URI" in kept and "50 MB" in kept
    batch_lines = mcp_server.BATCH_INSTRUCTIONS.strip().splitlines()
    assert batch_lines[BATCH_LINES_IN_THE_CUT - 1].startswith("No rollback")
    for line in batch_lines[:BATCH_LINES_IN_THE_CUT]:
        assert line in kept, line
    # The attach bullet adds at most 80 characters to today's 2378.
    assert len(mcp_server.INSTRUCTIONS) <= 2378 + 80


def test_the_external_instructions_fit_in_the_client_cut():
    external = mcp_server.EXTERNAL_INSTRUCTIONS + mcp_server.BATCH_INSTRUCTIONS
    assert len(external) <= CLIENT_INSTRUCTIONS_CUT


@pytest.mark.parametrize("name", ["send_message", "send_messages", "create_session"])
def test_the_attach_description_carries_the_hint_the_instructions_point_to(name):
    tool = next(tool for tool in iter_mcp_tools() if tool.name == name)
    assert inline.INLINE_TOO_LARGE_HINT in tool.input_schema["properties"]["attach"]["description"]


@pytest.fixture
def mcp_session(transactional_db, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(data))
    project = Project.objects.create(id="mcp-project", directory=str(tmp_path))
    Session.objects.create(id="mcp-session", project=project, provider="claude_code", file_path="m.jsonl")
    seen: list[dict] = []

    async def fake_service(payload, *, release_refs_on_outcome=False):
        seen.append(payload)
        return SendMessageResult(True, "mcp-session", "claude_code", "mcp-project", None, {"last_line": 0})

    monkeypatch.setattr(send_message_service, "send_message_to_session_from_payload", fake_service)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


def test_an_internal_call_with_a_data_uri_stages_and_reaches_the_service(mcp_session):
    result = asyncio.run(mcp_server.dispatch_tool(
        "send_message", {"session_id": "mcp-session", "prompt": "hi", "attach": [URI]}, session_id=None,
    ))
    assert result["exit_code"] == 0, result
    [ref] = [staging.validate_ref(item) for item in mcp_session[0]["attachments"]]
    assert ref.bucket.startswith("api-")
    assert staging.load_entry(ref).filename == "n.txt"


def test_an_external_call_with_a_data_uri_audits_no_argument(mcp_session, monkeypatch):
    audits: list = []

    async def write(fn):
        audits.append(fn)

    monkeypatch.setattr("twicc.mcp.oauth.storage.write", write)

    async def scenario():
        token = external_caller.set(ExternalCaller("connection", "Client"))
        try:
            return await mcp_server.dispatch_tool(
                "send_message", {"session_id": "mcp-session", "prompt": "hi", "attach": [URI]}, session_id=None,
            )
        finally:
            external_caller.reset(token)

    result = asyncio.run(scenario())
    assert result["exit_code"] == 0, result
    assert len(audits) == 1
    assert mcp_session[0]["attachments"]


def test_the_log_line_of_a_failing_call_has_no_base64(monkeypatch):
    async def explode(prepared, *, session_id, on_start=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server, "execute_prepared", explode)
    data = "A" * 4000
    params = mcp_types.CallToolRequestParams(
        name="send_message",
        arguments={"session_id": "s", "prompt": "hi", "attach": [f"data:image/png;base64,{data}"]},
    )
    with _logs("twicc.mcp.server") as records:
        result = asyncio.run(mcp_server._call_tool(SimpleNamespace(request=None), params))
    assert result.is_error
    text = " ".join(record.getMessage() for record in records)
    assert data not in text
    assert "chars>" in text
```

- [ ] **Step 2: Run and see the failures.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_log_redaction.py tests/test_remote_attach.py tests/test_rpc_attach.py tests/test_mcp_attach.py tests/test_wait_reply.py -q`
Expected: FAIL (`ModuleNotFoundError: twicc.log_redaction`, no `name=` in the data URI, `AttributeError: apply_peer_send_timeout`, 400 instead of 200/413, `MAX_REQUEST_BODY_BYTES` still 48 MB).

- [ ] **Step 3: Create `src/twicc/log_redaction.py`.**

```python
"""Keep request arguments loggable: a data URI in ``--attach`` can be tens of MB.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.6, §4.7.
"""

LOG_VALUE_MAX_CHARS = 512
LOG_VALUE_KEEP_CHARS = 64


def redact_for_log(value: object) -> object:
    """*value* with every string longer than 512 characters cut to ``<first 64>…<N chars>``.

    Walks lists, tuples and dicts. Looks at no prefix: an argv token may be ``--attach=data:…``
    or a bare ``data:…`` after ``--attach``.
    """
    if isinstance(value, str):
        if len(value) > LOG_VALUE_MAX_CHARS:
            return f"{value[:LOG_VALUE_KEEP_CHARS]}…<{len(value)} chars>"
        return value
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_for_log(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_for_log(item) for key, item in value.items()}
    return value
```

- [ ] **Step 4: Update the forwarder `src/twicc/cli/_remote.py`.**
  - Imports (`:30-52`): add `import mimetypes`, `from urllib.parse import quote`, `from click.core import ParameterSource`, `from twicc.core.services.attachments import inline`; delete `from twicc.cli._drop_request.attachments import _sniff_mime` (`:41`).
  - `Resolved` (`:78-83`) gets a fourth field:

```python
class Resolved(NamedTuple):
    """A resolved remote command: its registry path, spec, and bound params."""

    path: str                # registry key, e.g. "session/content"
    spec: CommandSpec        # the matching CommandSpec from build_registry()
    params: dict             # merged Click params across every navigated level (defaults included)
    explicit: frozenset[str] = frozenset()  # params of the leaf given on the command line
```

  - `_navigate` (`:194-261`) returns `(path, merged, explicit)`: initialize `explicit: frozenset[str] = frozenset()` before the loop, and in both `break` branches set

```python
            explicit = frozenset(
                name for name in ctx.params if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE
            )
```

    then `return "/".join(path_tokens), merged, explicit`. Change its signature (`:194`) to `def _navigate(argv: list[str]) -> tuple[str, dict, frozenset[str]]:` and the first line of its docstring (`:195`) to `"""Walk the Click tree, returning the registry path, merged params and the leaf's explicit params.` (the rest of the docstring stays). In `resolve_command` (`:177-191`): `path, params, explicit = _navigate(argv)` and `return Resolved(path=path, spec=spec, params=params, explicit=explicit)`.
  - Replace `_inline_one` (`:333-361`) by:

```python
def _inline_one(value: str) -> str:
    """Rewrite a single attach value for forwarding.

    A value already in ``data:`` form is returned unchanged, and a ``remote:`` value is
    reduced to its bare absolute path so the *server* reads it (see
    :func:`_resolve_remote_path`). Otherwise the value is a local file path (relative paths
    are read against the client's cwd): its bytes become
    ``data:<mime>;name=<percent-encoded base name>;base64,<payload>``, so the server stages
    the file under its own name. The MIME is guessed from the name (``application/octet-stream``
    otherwise) and is only a label: the server detects the kind from the bytes.

    Raises :class:`RemoteUsageError` if a ``remote:`` path is not absolute, or a local file is
    missing or unreadable (a client-side error — no HTTP attempted).
    """
    if value.startswith("data:"):
        return value
    remote_path = _resolve_remote_path(value)
    if remote_path is not None:
        return remote_path
    try:
        with open(value, "rb") as f:
            data = f.read()
    except OSError:
        raise RemoteUsageError(f"attachment not found: {value}")
    # A name read from the filesystem may hold undecodable bytes (surrogate escapes).
    name = os.fsencode(os.path.basename(value)).decode("utf-8", "replace")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{mime};name={quote(name, safe='')};base64,{payload}"
```

  - Replace the `inline_attachments` docstring (`:412-428`, both paragraphs that describe the old form: the bare `data:<mime>;base64,<payload>` URI with a re-sniffed MIME, and "No size pre-check is done here…") by:

```python
    """Return a copy of ``argv`` with each ``--attach <local path>`` inlined.

    Over ``--remote``, the server only sees the forwarded argv — it has no
    access to the client's filesystem. So every ``--attach`` value that names a
    *local* file is rewritten to a ``data:<mime>;name=<percent-encoded base name>;base64,<payload>``
    URI (see :func:`_inline_one`): the server stages the file under its own name, and the MIME,
    guessed from the name, is only a label (the server detects the kind from the bytes). A value
    already in ``data:`` form is left as is; a ``remote:`` value becomes its bare server path.
    Commands without an attach option return ``argv`` unchanged.

    The size pre-check runs before, in :func:`check_inline_size`.

    Raises :class:`RemoteUsageError` if an ``--attach`` file is missing or
    unreadable (a client-side error — no HTTP is attempted).
    """
```
  - After `inline_attachments`, add:

```python
# The registry path whose files all travel inline to a peer, ``remote:`` files included.
_PEER_SEND_PATH = "peer-send"


def _too_large_hint(resolved: Resolved) -> str:
    return inline.PEER_TOO_LARGE_HINT if resolved.path == _PEER_SEND_PATH else inline.INLINE_TOO_LARGE_HINT


def check_inline_size(resolved: Resolved) -> None:
    """Refuse more than 50 MB of inline data before reading any file (phase 2 design §4.4.3).

    Counts the size of every local ``--attach`` file and the decoded size of every data URI the
    user gave. A ``remote:`` value is a server path: not counted here (for ``peer-send`` the
    server-side command counts it). A missing local file is left to :func:`_inline_one`.
    Raises :class:`RemoteUsageError` (exit 2) with the limit and the hint of the command.
    """
    total = 0
    for value in resolved.params.get(_ATTACH_PARAM_NAME) or ():
        if value.startswith("data:"):
            total += inline.data_uri_size(value) or 0
        elif not has_remote_scheme(value):
            try:
                total += os.path.getsize(value)
            except OSError:
                continue
    if total > inline.INLINE_MAX_BYTES:
        raise RemoteUsageError(
            "--attach: " + inline.too_large_message(total, inline.INLINE_MAX_BYTES, _too_large_hint(resolved))
        )


def apply_peer_send_timeout(argv: list[str], resolved: Resolved) -> tuple[list[str], Resolved]:
    """Give a ``peer-send`` with files and no explicit ``--timeout`` the long wait (§4.4.3).

    The server-side command waits ``PEER_SEND_TIMEOUT_WITH_FILES`` for a message with files;
    passing it explicitly lets the read timeout of this call cover that wait, without
    measuring any file (``remote:`` files included).
    """
    if (
        resolved.path != _PEER_SEND_PATH
        or not resolved.params.get(_ATTACH_PARAM_NAME)
        or "timeout" in resolved.explicit
    ):
        return list(argv), resolved
    timeout = inline.PEER_SEND_TIMEOUT_WITH_FILES
    return (
        [argv[0], f"--timeout={timeout}", *argv[1:]],
        resolved._replace(params={**resolved.params, "timeout": timeout}),
    )
```

  - Replace `_request_timeout` (`:668-699`) by:

```python
def _request_timeout(resolved: Resolved) -> httpx.Timeout:
    """Pick the httpx timeout for this command.

    The two answer-waits (``session wait-reply``, ``sessions wait-reply``) hold
    the response up to their ``--wait-timeout``; the local **read** timeout is
    therefore set to that value plus :data:`_WAIT_TIMEOUT_MARGIN` so the client
    never gives up before the server answers.

    ``--wait-reply`` gets the same treatment on top of its own ``--timeout``:
    the server holds the connection for the drop request *and* the wait.
    Every other drop-and-poll command (one with a ``--timeout``) waits up to its
    effective ``--timeout``: the server now stages inline data and the send may
    wait in the session's lane before its final status, so the read timeout is
    that value plus the margin. Read commands use :data:`_DEFAULT_TIMEOUT`.
    The connect timeout is always the short, constant :data:`_CONNECT_TIMEOUT`.
    """
    read = _DEFAULT_TIMEOUT
    if resolved.path in _WAIT_REPLY_PATHS:
        wait_timeout = resolved.params.get("wait_timeout")
        if not isinstance(wait_timeout, (int, float)) or wait_timeout <= 0:
            wait_timeout = _DEFAULT_WAIT_TIMEOUT
        read = float(wait_timeout) + _WAIT_TIMEOUT_MARGIN
    elif resolved.params.get("wait_reply"):
        # ``--wait-reply`` turns an ordinary drop-and-poll command into a long
        # one: the server holds the connection for the drop request *and* the
        # wait that follows. Without this the client gives up at
        # ``_DEFAULT_TIMEOUT`` while the session it just created keeps running,
        # and the caller never learns its id.
        wait_timeout = resolved.params.get("wait_timeout")
        if not isinstance(wait_timeout, (int, float)) or wait_timeout <= 0:
            wait_timeout = _DEFAULT_WAIT_TIMEOUT
        command_timeout = resolved.params.get("timeout")
        base = float(command_timeout) if isinstance(command_timeout, (int, float)) else 0.0
        read = base + float(wait_timeout) + _WAIT_TIMEOUT_MARGIN
    elif "timeout" in resolved.params:
        command_timeout = resolved.params.get("timeout")
        base = float(command_timeout) if isinstance(command_timeout, (int, float)) else _DEFAULT_TIMEOUT
        read = base + _WAIT_TIMEOUT_MARGIN
    return httpx.Timeout(read, connect=_CONNECT_TIMEOUT)
```

  - Replace the comment above `_WAIT_TIMEOUT_MARGIN` (`:637-638`) by:

```python
# Read-timeout margin (seconds) added on top of a command's own wait (its
# ``--wait-timeout`` or its ``--timeout``) so the local read does not race the
# server's own deadline.
```

    and the comment above `_DEFAULT_TIMEOUT` (`:641-642`) by:

```python
# Default read timeout (seconds) for read commands (no ``--timeout``). A
# drop-and-poll command waits its own ``--timeout`` plus the margin instead.
```

  - In `forward` (`:764-773`), replace the four lines from `resolved = resolve_command(argv)` to `body = orjson.dumps({"argv": argv2})` by:

```python
    resolved = resolve_command(argv)
    reject_host_bound(resolved)
    check_inline_size(resolved)
    argv2 = inline_attachments(argv, resolved)
    argv2 = inline_prompt(argv2, resolved)
    argv2, resolved = apply_peer_send_timeout(argv2, resolved)

    endpoint = _endpoint_url(url, resolved.path)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = orjson.dumps({"argv": argv2})
    if len(body) > inline.INLINE_MAX_REQUEST_BYTES:
        # An inlined prompt file can push the body above the cap the server enforces.
        raise RemoteUsageError(
            f"the request is {inline.format_mb(len(body))} once encoded; the limit is "
            f"{inline.format_mb(inline.INLINE_MAX_REQUEST_BYTES)}. {_too_large_hint(resolved)}"
        )
```

  - In the `forward` docstring, add after "missing attachment)": "more than 50 MB of inline attachment data, a request body above the 72 MB cap".
  - In the comment above `_PROMPT_PARAM_NAMES` (`:435-436`), Task 6 deletes `attachments.py`, which the comment names, and `validation.py` holds one error NamedTuple (`ValidationError`, `:37-40`). Replace "(the ``message`` fields on the error NamedTuples in ``attachments.py`` / ``validation.py`` are not Click params, so they never enter a CommandSpec)" by "(the ``message`` field of the ``ValidationError`` NamedTuple in ``validation.py`` is not a Click param, so it never enters a CommandSpec)".

- [ ] **Step 5: Cap and parse the RPC body off the loop.** In `src/twicc/rpc/views.py`:
  - Imports: add `from twicc.core.services.attachments.inline import INLINE_MAX_REQUEST_BYTES` and `from twicc.log_redaction import redact_for_log`.
  - After `_json`, add:

```python
_BODY_TOO_LARGE = "Request body too large"


class _BodyTooLarge(Exception):
    """The body of a token call is above :data:`INLINE_MAX_REQUEST_BYTES`."""


def _content_length(request: HttpRequest) -> int | None:
    try:
        return int(request.headers.get("Content-Length") or "")
    except ValueError:
        return None


def _load_body(request: HttpRequest, scope: str) -> tuple[object, bool]:
    """``(body, malformed)``: read and parse the request body (blocking: a worker thread).

    A token (full-scope) call may carry inline attachments: it is read with
    ``request.read`` and capped at :data:`INLINE_MAX_REQUEST_BYTES` (Django's
    ``DATA_UPLOAD_MAX_MEMORY_SIZE`` is checked only by ``request.body``). A cookie
    (read-scope) call keeps ``request.body`` and its 12 MB cap. A body without
    ``Content-Length`` is bounded by the read itself.
    """
    if scope == RPC_SCOPE_READ:
        raw = request.body
    else:
        raw = request.read(INLINE_MAX_REQUEST_BYTES + 1)
        if len(raw) > INLINE_MAX_REQUEST_BYTES:
            raise _BodyTooLarge
    if not raw:
        return {}, False
    try:
        return orjson.loads(raw), False
    except ValueError:
        return {}, True
```

  - In `dispatch`, replace `:77-90` (from `raw = request.body` to the end of the `except ValueError` block) by:

```python
    if scope != RPC_SCOPE_READ:
        length = _content_length(request)
        if length is not None and length > INLINE_MAX_REQUEST_BYTES:
            return _json({"error": _BODY_TOO_LARGE}, status=413)
    try:
        body, malformed = await asyncio.to_thread(_load_body, request, scope)
    except _BodyTooLarge:
        return _json({"error": _BODY_TOO_LARGE}, status=413)
    # Only an explicit application/json content-type makes an unparseable body an
    # error; otherwise (test-client multipart, stray form posts) it falls back to
    # empty, so a valid JSON body sent without the header is still honored.
    if malformed and (request.content_type or "").startswith("application/json"):
        return _json({"error": "Malformed JSON body."}, status=400)
```

  - `:134`: `logger.exception("RPC command %r failed (argv=%r)", command_path, redact_for_log(argv))`.

- [ ] **Step 6: Update the MCP server `src/twicc/mcp/server.py`.**
  - Imports: add `from twicc.core.services.attachments.inline import INLINE_MAX_REQUEST_BYTES` and `from twicc.log_redaction import redact_for_log`.
  - In `INSTRUCTIONS`, replace the bullet at `:68-69` by the bullet below. It adds 79 characters, no more: Claude Code keeps only the first 2048 characters of the instructions, today's text is already 2378 characters long, and a longer addition pushes the batch line "No rollback, …" out of the cut (`test_the_attach_line_does_not_push_the_batch_rules_out_of_the_client_cut`). The full forms (`name=`), the limit and the hint live in the `attach` parameter description (`ATTACH_HELP`, Task 4); the one-`peer_send`-per-call advice lives in the `peer_send` description (Task 6).

```
- Always pass absolute paths (directories, attachments): tools execute inside
  the TwiCC backend, whose working directory is not yours. `attach` also takes
  base64 data URIs (50 MB per call; larger: see `attach`).
```

  - `:227`: `logger.exception("MCP tool %r failed (arguments=%r)", name, redact_for_log(arguments))`.
  - Replace `:267-272` by:

```python
# A tool call carrying inline attachments (data URIs in ``attach``) holds up to 50 MB of
# decoded files, about 67 MB once base64-encoded, plus the prompt and the JSON-RPC envelope
# (phase 2 design D12). The MCP SDK caps the HTTP body at 4 MiB by default, which would reject
# any real attachment with a 413. The SDK reads and parses this body on the event loop.
MAX_REQUEST_BODY_BYTES = INLINE_MAX_REQUEST_BYTES
```

  - Replace `EXTERNAL_INSTRUCTIONS` (`:295-299`) by:

```python
EXTERNAL_INSTRUCTIONS = """TwiCC external MCP: tools run on the TwiCC host.
Use explicit session IDs; self, parent, and whoami are unavailable.
Paths refer to the server filesystem. To attach a file of your machine, pass a base64 data URI
with name= to keep its file name: data:<mime>;name=<percent-encoded file name>;base64,<data>.
Inline data is limited to 50 MB per call. For a larger file, put it on a file storage service and
pass its URL in the message text, or pass an absolute path when the file is already on the server
(peer_send: a URL only, because every file of a peer message travels inline).
Ordinary results contain exit_code, result, and error. Use info for current models and settings.
"""
```

- [ ] **Step 7: Run and see them pass, plus regressions.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_log_redaction.py tests/test_remote_attach.py tests/test_rpc_attach.py tests/test_mcp_attach.py tests/test_wait_reply.py tests/test_prompt_includes.py tests/test_session_keywords.py tests/test_rpc_auth.py tests/test_mcp_server.py tests/test_mcp_endpoint.py tests/test_mcp_tools.py tests/test_mcp_dispatch.py tests/test_mcp_external.py tests/test_mcp_batch_runtime.py -q && uvx ruff check src/twicc/log_redaction.py src/twicc/cli/_remote.py src/twicc/rpc/views.py src/twicc/mcp/server.py tests/test_log_redaction.py tests/test_remote_attach.py tests/test_rpc_attach.py tests/test_mcp_attach.py tests/test_wait_reply.py`
Expected: PASS (`test_mcp_tools` keeps every description within 1024 characters: Task 5 changes no tool description). For the existing files (`_remote.py`, `rpc/views.py`, `mcp/server.py`, `tests/test_wait_reply.py`), compare `uvx ruff check <file>` before and after the edit: no new finding (all are clean at `f6e4b342`).

- [ ] **Step 8: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/log_redaction.py src/twicc/cli/_remote.py src/twicc/rpc/views.py src/twicc/mcp/server.py tests/test_log_redaction.py tests/test_remote_attach.py tests/test_rpc_attach.py tests/test_mcp_attach.py tests/test_wait_reply.py && git commit -F /tmp/phase2-task5.msg
```

```
feat(remote): carry up to 50 MB of inline attachments over RPC and MCP

The --remote forwarder sends local files as data URIs with their name,
refuses more than 50 MB of inline data or a body above 72 MB before any
HTTP call, waits for the effective --timeout of a drop-and-poll command,
and gives a peer-send with files the long peer timeout. It no longer
imports the legacy attachment module.

/rpc/ reads a token call's body with a 72 MB cap in a worker thread; a
cookie call keeps Django's 12 MB. The MCP server raises its body cap to
72 MB. Its instructions name data URIs and the limit in one short line, so
the batch rules stay within the 2048 characters Claude Code keeps; the
external instructions add name= and what to do with a larger file.
Failure logs of both cut long arguments, so no attachment bytes reach
backend.log.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 6 (spec T6, T7 merged): Peer messages end to end

One commit, so no intermediate state mixes the two wire shapes: `peer-send` stages refs, two drop kinds route to the sender, the wire carries `attachments`, the receiver validates and stores them, the read APIs and the summaries stop loading the payload, the purge and a data migration follow, the review dialog builds files from entries, and the legacy CLI module is deleted.

**Files:**
- Modify: `src/twicc/cli/peer_send.py` (whole command, `:1-199`)
- Modify: `src/twicc/cli/_drop_request/help_strings.py` (`PEER_ATTACH_HELP`, `PEER_TIMEOUT_HELP`)
- Modify: `src/twicc/core/services/peer_messages.py:1-41` (docstring, imports, constants), `:100-112`, `:369-394`, `:414-708` (payload helpers, validation, sender), `:713-802` (receiver), `:805-842`, `:901-933`, `:1053-1061`; add the drop wrappers and the summary loaders
- Modify: `src/twicc/peer/outbound.py:1-49`, `:78-93`
- Modify: `src/twicc/peer/inbound_views.py:16-30`, `:256-287`
- Modify: `src/twicc/peer/owner_views.py:8-51`, `:199-234`, `:277-341`, `:384-415`
- Modify: `src/twicc/core/serializers.py:597-702`
- Modify: `src/twicc/asgi.py:845-865` (peer snapshot)
- Modify: `src/twicc/cli/peer_message.py:26-35`
- Modify: `src/twicc/peer_purge_task.py:1-74`
- Modify: `src/twicc/core/models.py:2025-2036` (comments)
- Modify: `src/twicc/drop_requests_watcher.py:190-194`
- Modify: `src/twicc/cli/_drop_request/__init__.py:9` (package docstring)
- Create: `src/twicc/core/migrations/0153_peer_message_attachments.py`
- Modify: `frontend/src/utils/peerMessageContent.js`, `frontend/src/components/peer/PeerMessageReviewDialog.vue:29-39`, `:104`, `:349-356`, `:640-646`, `:724`, `:819`, `:1035-1050`
- Delete: `src/twicc/cli/_drop_request/attachments.py`
- Delete: `tests/test_async_question_migration_graph.py` (user decision: a one-time merge-history test whose `leaf_nodes("core") == [("core", "0152_async_question_state")]` assertion breaks at every new migration)
- Create: `tests/test_peer_attachments.py`, `tests/test_peer_message_attachments_migration.py`
- Modify: `tests/test_peer_messages.py`, `tests/test_peer_cli.py`, `tests/test_peer_updates_consumer.py`, `tests/test_mcp_attach.py`, `frontend/src/utils/peerMessageContent.test.js`

**Interfaces:**
- Consumes: Task 1 (`inline.*`, `staging.load_entry`, `content_location`, `normalize_filename`, `name_max_bytes`), Task 3 (`drop.refs_to_release`), Task 4 (`attach_sources.*`, `drop.has_legacy_fields`, `drop.LEGACY_FIELDS_MESSAGE`), Task 5 (`_remote` already sends `peer-send` files as named data URIs). Phase 1: `planner.validate_attachment_frame`, `describe_attachment_error`, `lifecycle.delivery_release`; frontend `getDisplayKind`, `formatAttachmentSize` (`composerAttachments.js`), `attachmentKindIcon` (`attachmentStrip.js`).
- Produces: the peer contracts listed in "Shared contracts"; drop kinds `peer:send` → `send_peer_message_from_drop_payload`, `peer:send_attachments` → `send_peer_attachments_from_drop_payload`; `owner_views._attachments_body(pk)`, `_full_detail_body(pk)`; `inbound_views._read_message_body(request)`; migration `0153_peer_message_attachments.convert_peer_messages(apps, schema_editor)`; frontend `peerEntryToFile`, `peerEntryToMediaItem`.

- [ ] **Step 1: Write the failing peer tests.** Create `tests/test_peer_attachments.py`:

```python
"""Peer messages with files: wire, sender, receiver, read APIs, summaries, purge, CLI.

Design: docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md §4.4.2, §4.8.
"""

import asyncio
import base64
import socket
import threading
import time
from datetime import timedelta

import orjson
import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as djtz

from twicc.cli._drop_request import transport, whoami
from twicc.cli._drop_request.polling import PollOutcome
from twicc.core import serializers
from twicc.core.models import Peer, PeerMessage, PeerMessageDirection, PeerMessageStatus, PeerState
from twicc.core.services import peer_messages
from twicc.core.services.attachments import inline, lifecycle, staging
from twicc.core.services.peer_tokens import mint_token
from twicc.drop_requests_watcher import _KIND_HANDLERS, execute_drop_payload
from twicc.peer import inbound_views, outbound, owner_views
from twicc.peer_purge_task import purge_expired_attachment_bytes
from twicc.rpc.invoker import invoke

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
# The unknown-keys rule of a receiver older than this phase (peer_messages.py before Task 6).
OLD_RECEIVER_PAYLOAD_KEYS = frozenset({"text", "images", "documents"})


@pytest.fixture(autouse=True)
def _passthrough(monkeypatch):
    async def _p(factory):
        return await factory()
    monkeypatch.setattr("twicc.core.services.peer_mutation.run_under_db_write_lock", _p)
    monkeypatch.setattr("twicc.core.services.peer_messages.run_under_db_write_lock", _p)


@pytest.fixture(autouse=True)
def _peer_host(monkeypatch):
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings", lambda: {"peerBaseUrl": "https://me.example.com"},
    )


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    async def _record(data):
        pass
    monkeypatch.setattr("twicc.core.services.peer_messages._broadcast", _record)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("TWICC_DATA_DIR", str(path))
    return path


@pytest.fixture
def released(monkeypatch):
    calls: list = []

    async def release_refs(refs):
        calls.append(tuple(refs))

    monkeypatch.setattr(lifecycle, "release_refs", release_refs)
    return calls


@pytest.fixture
def wire(monkeypatch):
    """The decoded bodies posted to the peer; the answer is set with ``wire.answer``."""
    class Wire(list):
        answer = (202, {})

    posted = Wire()

    async def fake(base_url, *, bearer, body):
        posted.append(orjson.loads(body))
        return posted.answer

    monkeypatch.setattr("twicc.peer.outbound.post_message", fake)
    return posted


def _active_peer(**kw):
    defaults = {
        "name": "alice", "base_url": "https://alice.example.com", "state": PeerState.ACTIVE,
        "token_ours": mint_token(), "token_theirs": "their-" + "t" * 30,
        "paired_local_base_url": "https://me.example.com",
    }
    defaults.update(kw)
    return Peer.objects.create(**defaults)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _stage(name: str, data: bytes):
    return staging.stage_bytes(data, name, bucket=staging.new_bucket("cli"), origin="cli")


async def _settle():
    for _ in range(5):
        pending = list(lifecycle._DELIVERY_RELEASE_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)


def _run(coro):
    async def scenario():
        result = await coro
        await _settle()
        return result
    return asyncio.run(scenario())


def _post(client, body, *, bearer):
    return asyncio.run(client.post(
        "/peer/messages/", data=orjson.dumps(body), content_type="application/json",
        headers={"Authorization": f"Bearer {bearer}"},
    ))


def _wire_body(payload):
    return {"message_id": "pm_" + "a" * 16, "title": "Files", "payload": payload,
            "origin": {"sent_at": "2026-10-07T12:00:00+00:00"}}


def _send_files(refs, **extra):
    payload = {"peer": "alice", "title": "Files", "text": "see files", "attachments": [r._asdict() for r in refs],
               **extra}
    return _run(execute_drop_payload(payload, "peer:send_attachments"))


# ── Sender ───────────────────────────────────────────────────────────────────


def test_a_text_only_message_is_text_alone(transactional_db, wire):
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "hi"}))
    assert result.success
    assert wire[0]["payload"] == {"text": "hi"}
    row = PeerMessage.objects.get()
    assert (row.payload, row.attachments_meta) == ({"text": "hi"}, [])


def test_files_travel_with_their_names_in_order(transactional_db, data_dir, wire, released):
    _active_peer()
    refs = [_stage("notes.txt", b"note"), _stage("shot.png", PNG), _stage("empty.bin", b"")]
    status = _send_files(refs)
    assert status["status"] == "sent", status
    assert set(wire[0]["payload"]) == {"text", "attachments"}
    assert wire[0]["payload"]["attachments"] == [
        {"name": "notes.txt", "media_type": "text/plain", "data": _b64(b"note")},
        {"name": "shot.png", "media_type": "image/png", "data": _b64(PNG)},
        {"name": "empty.bin", "media_type": "application/octet-stream", "data": ""},
    ]
    row = PeerMessage.objects.get()
    assert row.attachments_meta == [
        {"name": "notes.txt", "media_type": "text/plain", "bytes": 4},
        {"name": "shot.png", "media_type": "image/png", "bytes": len(PNG)},
        {"name": "empty.bin", "media_type": "application/octet-stream", "bytes": 0},
    ]
    assert released == [tuple(refs)]


def test_an_old_receiver_refuses_files_and_accepts_text_only(transactional_db, data_dir, wire):
    _active_peer()
    _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "hi"}))
    _send_files([_stage("a.txt", b"a")])
    text_only, with_files = (body["payload"] for body in wire)
    assert set(text_only) - OLD_RECEIVER_PAYLOAD_KEYS == set()
    assert set(with_files) - OLD_RECEIVER_PAYLOAD_KEYS == {"attachments"}


@pytest.mark.parametrize(("sizes", "ok"), [((4, 4), True), ((4, 5), False)])
def test_the_staged_total_is_checked_before_reading(transactional_db, data_dir, wire, released, monkeypatch,
                                                    sizes, ok):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_TOTAL_BYTES", 8)
    _active_peer()
    refs = [_stage(f"{n}.bin", b"x" * size) for n, size in enumerate(sizes)]
    status = _send_files(refs)
    assert (status["status"] == "sent") is ok
    if not ok:
        assert status["errors"][0]["code"] == "attachments_too_large"
        assert status["errors"][0]["message"].endswith(inline.PEER_TOO_LARGE_HINT)
        assert wire == []
    assert released == [tuple(refs)]


def test_a_body_above_the_request_cap_is_refused_before_the_post(transactional_db, wire, monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_REQUEST_BYTES", 300)
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload({"peer": "alice", "title": "T", "text": "x" * 400}))
    assert [e.code for e in result.errors] == ["message_too_large"]
    assert "once encoded" in result.errors[0].message
    assert wire == []
    assert PeerMessage.objects.count() == 0


@pytest.mark.parametrize(("status", "files", "expected"), [
    (400, True, "The remote instance rejected the message. It may be too old to receive attachments."),
    (413, True, "The remote instance, or a proxy in front of it, refused the message size. "
                "An older instance also refuses any attachment."),
    (413, False, "The remote instance, or a proxy in front of it, refused the message size."),
    (400, False, "The remote instance rejected the message."),
])
def test_rejection_texts_follow_the_http_status(transactional_db, data_dir, wire, status, files, expected):
    _active_peer()
    wire.answer = (status, {})
    payload = {"peer": "alice", "title": "T", "text": "hi"}
    if files:
        payload["attachments"] = [_stage("a.txt", b"a")._asdict()]
    status_data = _run(execute_drop_payload(payload, "peer:send_attachments" if files else "peer:send"))
    assert status_data["errors"][0]["message"] == expected
    assert PeerMessage.objects.get().error == expected


def test_the_write_timeout_follows_the_body(monkeypatch):
    seen: dict = {}

    class FakeResponse:
        status_code = 202

        def json(self):
            return {"status": "pending"}

    class FakeClient:
        def __init__(self, *, timeout):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, content, headers):
            seen.update(url=url, content=content, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(outbound.httpx, "AsyncClient", FakeClient)
    body = b"x" * (512 * 1024)
    assert asyncio.run(outbound.post_message("https://bob.example.com/", bearer="tok", body=body)) == (
        202, {"status": "pending"},
    )
    assert seen["url"] == "https://bob.example.com/peer/messages/"
    assert seen["content"] is body
    assert seen["headers"] == {"Authorization": "Bearer tok", "Content-Type": "application/json"}
    assert seen["timeout"].write == inline.transfer_timeout(len(body)) == 32.0
    assert (seen["timeout"].connect, seen["timeout"].read) == (outbound.OUTBOUND_TIMEOUT_SECONDS,) * 2


_STALL_SECONDS = 1.0
_SLOW_BODY = b"x" * (12 * 1024 * 1024)  # far above the loopback socket buffers: the write must wait


def _stalling_server():
    """A real 127.0.0.1 HTTP server that reads nothing for ``_STALL_SECONDS``, then the whole body.

    Returns ``(port, thread, received)``; ``received`` gets the body size once a 202 is sent.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    received: list[int] = []

    def serve():
        with listener:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(10)
                data = b""
                while b"\r\n\r\n" not in data:
                    part = conn.recv(65536)
                    if not part:
                        return
                    data += part
                head, _, rest = data.partition(b"\r\n\r\n")
                length = next(int(line.split(b":")[1]) for line in head.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                got = len(rest)
                try:
                    time.sleep(_STALL_SECONDS)  # a slow link: the client's write waits
                    while got < length:
                        part = conn.recv(256 * 1024)
                        if not part:
                            return
                        got += len(part)
                    conn.sendall(b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n"
                                 b"Content-Length: 2\r\nConnection: close\r\n\r\n{}")
                except OSError:
                    return
                received.append(got)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread, received


@pytest.fixture
def short_peer_timeouts(monkeypatch):
    """Scale the timeouts down: 0.3 s for connect / read / the old write; 1 MiB/s for a transfer."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(outbound, "OUTBOUND_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inline, "TRANSFER_BASE_SECONDS", 0.3)
    monkeypatch.setattr(inline, "INLINE_MIN_THROUGHPUT", 1024 * 1024)


def test_a_slow_write_longer_than_the_old_timeout_succeeds(short_peer_timeouts):
    """The real network backend (no mock transport): the write budget follows the body (§4.8.3)."""
    port, thread, received = _stalling_server()
    assert inline.transfer_timeout(len(_SLOW_BODY)) > 10 * _STALL_SECONDS
    assert asyncio.run(outbound.post_message(f"http://127.0.0.1:{port}", bearer="tok", body=_SLOW_BODY)) == (202, {})
    thread.join(10)
    assert received == [len(_SLOW_BODY)]


def test_the_same_slow_write_fails_with_the_old_single_timeout(short_peer_timeouts, monkeypatch):
    """Control: the server really stalls longer than the old write timeout (30 s, scaled to 0.3 s)."""
    monkeypatch.setattr(outbound, "transfer_timeout", lambda size: outbound.OUTBOUND_TIMEOUT_SECONDS)
    port, thread, received = _stalling_server()
    with pytest.raises(outbound.PeerOutboundError, match="WriteTimeout"):
        asyncio.run(outbound.post_message(f"http://127.0.0.1:{port}", bearer="tok", body=_SLOW_BODY))
    thread.join(10)
    assert received == []


def test_the_message_body_is_serialized_once():
    body = outbound.build_message_body(message_id="pm_x", title="T", reply_to="", payload={"text": "a"},
                                       origin={"sent_at": "s"})
    assert orjson.loads(body) == {"message_id": "pm_x", "title": "T", "reply_to": "",
                                  "payload": {"text": "a"}, "origin": {"sent_at": "s"}}


def test_the_minted_message_id_is_used(transactional_db, wire):
    _active_peer()
    result = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "pm_cli0000000000001"},
    ))
    assert result.message_id == "pm_cli0000000000001"
    assert wire[0]["message_id"] == "pm_cli0000000000001"
    assert PeerMessage.objects.get().message_id == "pm_cli0000000000001"


def test_a_bad_or_reused_message_id_is_refused(transactional_db, wire):
    peer = _active_peer()
    bad = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "-bad"},
    ))
    assert [e.code for e in bad.errors] == ["invalid_message_id"]
    PeerMessage.objects.create(peer=peer, direction=PeerMessageDirection.IN, message_id="pm_used", thread_id="pm_used",
                               payload={"text": "x"}, status=PeerMessageStatus.PENDING)
    reused = _run(peer_messages.send_peer_message_from_payload(
        {"peer": "alice", "title": "T", "text": "hi", "message_id": "pm_used"},
    ))
    assert [e.code for e in reused.errors] == ["invalid_message_id"]
    assert wire == []
    assert PeerMessage.objects.count() == 1


@pytest.mark.parametrize("change", [
    {},
    {"text": ""},
    {"peer": "nobody"},
    {"reply_to": "pm_unknown"},
    {"__state__": PeerState.PENDING_SENT},
    {"__http__": 500},
])
def test_refs_are_released_on_every_outcome(transactional_db, data_dir, wire, released, change):
    change = dict(change)
    peer = _active_peer()
    if "__state__" in change:
        Peer.objects.filter(pk=peer.pk).update(state=change.pop("__state__"))
    if "__http__" in change:
        wire.answer = (change.pop("__http__"), {})
    refs = [_stage("a.txt", b"a")]
    _send_files(refs, **change)
    assert released == [tuple(refs)]


def test_peer_send_refuses_refs_and_both_kinds_route_to_one_service(transactional_db, data_dir, wire, released):
    _active_peer()
    ref = _stage("a.txt", b"a")
    status = _run(execute_drop_payload(
        {"peer": "alice", "title": "T", "text": "hi", "attachments": [ref._asdict()]}, "peer:send",
    ))
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert wire == []
    assert released == [(ref,)]
    assert _KIND_HANDLERS["peer:send"][:2] == (
        "twicc.core.services.peer_messages", "send_peer_message_from_drop_payload",
    )
    assert _KIND_HANDLERS["peer:send_attachments"][:2] == (
        "twicc.core.services.peer_messages", "send_peer_attachments_from_drop_payload",
    )


@pytest.mark.parametrize("kind", ["peer:send", "peer:send_attachments"])
def test_legacy_fields_are_refused(transactional_db, wire, kind):
    _active_peer()
    status = _run(execute_drop_payload(
        {"peer": "alice", "title": "T", "text": "hi", "images": [{"type": "image"}]}, kind,
    ))
    assert status["errors"][0]["code"] == "invalid_attachments"
    assert wire == []


def test_an_older_watcher_fails_the_new_kind_and_sends_nothing(transactional_db, data_dir, wire, monkeypatch):
    _active_peer()
    monkeypatch.delitem(_KIND_HANDLERS, "peer:send_attachments")
    status = _send_files([_stage("a.txt", b"a")])
    assert status["status"] == "failed"
    assert "Unknown payload kind" in status["error"]
    assert wire == []


# ── peer-send CLI ────────────────────────────────────────────────────────────


def _in_backend(argv):
    async def scenario():
        token = transport.backend_loop.set(asyncio.get_running_loop())
        try:
            return await asyncio.to_thread(invoke, argv)
        finally:
            transport.backend_loop.reset(token)
    return asyncio.run(scenario())


def test_peer_send_cli_stages_files_and_uses_the_new_kind(transactional_db, data_dir, wire, tmp_path):
    _active_peer()
    path = tmp_path / "fix.patch"
    path.write_bytes(b"diff --git a b")
    result = _in_backend(["peer-send", "alice", "Patch", "see the patch", "--attach", str(path),
                          "--attach", "data:text/plain;name=n%C3%A9.txt;base64," + _b64(b"x")])
    assert result.exit_code == 0, result.error
    row = PeerMessage.objects.get()
    assert result.result["message_id"] == row.message_id == wire[0]["message_id"]
    assert [e["name"] for e in wire[0]["payload"]["attachments"]] == ["fix.patch", "né.txt"]


def test_peer_send_cli_refuses_more_than_50_mb_before_any_copy(transactional_db, data_dir, wire, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(inline, "INLINE_MAX_BYTES", 10)
    _active_peer()
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 6)
    result = _in_backend(["peer-send", "alice", "T", "hi", "--attach", str(path),
                          "--attach", "data:text/plain;base64," + _b64(b"y" * 6)])
    assert result.exit_code == 1
    assert result.result["errors"][0]["code"] == "attachments_too_large"
    assert result.result["errors"][0]["message"].endswith(inline.PEER_TOO_LARGE_HINT)
    assert not staging.get_composer_attachments_dir().exists() or not any(
        staging.get_composer_attachments_dir().iterdir()
    )
    assert wire == []


@pytest.fixture
def fake_peer_transport(monkeypatch, transactional_db, data_dir):
    """A local-mode transport that records the submission and answers ``seen["outcome"]``."""
    seen: dict = {"outcome": PollOutcome(None, None, True)}

    class _Submission:
        request_uuid = "req-peer"

        def cleanup(self):
            pass

    def submit(payload, *, kind):
        seen.update(payload=payload, kind=kind)
        return _Submission()

    def wait(sub, timeout_seconds):
        seen["timeout"] = timeout_seconds
        return seen["outcome"]

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", submit)
    monkeypatch.setattr(transport, "wait", wait)
    monkeypatch.setattr(whoami, "resolve_current_session", lambda: None)
    return seen


@pytest.mark.parametrize(("extra", "with_file", "expected"), [
    ([], True, inline.PEER_SEND_TIMEOUT_WITH_FILES),
    ([], False, 30),
    (["--timeout", "99"], True, 99),
])
def test_peer_send_default_timeout(fake_peer_transport, tmp_path, extra, with_file, expected):
    peer = _active_peer()
    argv = ["peer-send", "alice", "T", "hi", *extra]
    if with_file:
        path = tmp_path / "a.txt"
        path.write_bytes(b"a")
        argv += ["--attach", str(path)]
    result = invoke(argv)
    assert fake_peer_transport["timeout"] == expected
    assert fake_peer_transport["kind"] == ("peer:send_attachments" if with_file else "peer:send")
    # A timeout (exit 5) still names the message and the peer.
    assert result.exit_code == 5
    assert result.result["message_id"] == fake_peer_transport["payload"]["message_id"]
    assert result.result["peer_id"] == peer.id


@pytest.mark.parametrize(("outcome", "exit_code"), [
    (PollOutcome("rejected", {"errors": [{"field": "peer", "code": "unreachable", "message": "x"}]}, True), 3),
    (PollOutcome("failed", {"error": "boom"}, True), 4),
])
def test_peer_send_prints_the_ids_on_rejected_and_failed(fake_peer_transport, outcome, exit_code):
    peer = _active_peer()
    fake_peer_transport["outcome"] = outcome
    result = invoke(["peer-send", "alice", "T", "hi"])
    assert result.exit_code == exit_code
    assert result.result["message_id"] == fake_peer_transport["payload"]["message_id"]
    assert peer_messages.PEER_MESSAGE_ID_PATTERN.fullmatch(result.result["message_id"])
    assert result.result["peer_id"] == peer.id


def test_peer_send_prints_the_id_of_a_sent_status(fake_peer_transport):
    peer = _active_peer()
    fake_peer_transport["outcome"] = PollOutcome(
        "sent", {"message_id": "pm_server00000001", "peer_id": peer.id, "peer_status": "pending"}, True,
    )
    result = invoke(["peer-send", "alice", "T", "hi"])
    assert result.exit_code == 0
    assert result.result["message_id"] == "pm_server00000001"


# ── Receiver ─────────────────────────────────────────────────────────────────


def test_the_receiver_stores_sanitized_entries(transactional_db, data_dir):
    peer = _active_peer()
    payload = {"text": "files", "attachments": [
        {"name": "a/b.txt", "media_type": "text/plain", "data": _b64(b"ab")},
        {"name": "shot.png", "media_type": "not a type", "data": _b64(PNG)},
    ]}
    response = _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours)
    assert response.status_code == 202
    row = PeerMessage.objects.get()
    assert row.payload == {"text": "files", "attachments": [
        {"name": "a_b.txt", "media_type": "text/plain", "data": _b64(b"ab")},
        {"name": "shot.png", "media_type": "application/octet-stream", "data": _b64(PNG)},
    ]}
    assert row.attachments_meta == [
        {"name": "a_b.txt", "media_type": "text/plain", "bytes": 2},
        {"name": "shot.png", "media_type": "application/octet-stream", "bytes": len(PNG)},
    ]


def test_the_receiver_converts_legacy_blocks(transactional_db, data_dir):
    peer = _active_peer()
    payload = {"text": "old", "images": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(PNG)}},
    ], "documents": [
        {"type": "document", "title": "n.md", "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
        # A legacy block is sanitized like a wire entry: its title is a path, its media type is not one.
        {"type": "document", "title": "a/b.md",
         "source": {"type": "base64", "media_type": "not a type", "data": _b64(b"md")}},
    ]}
    assert _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours).status_code == 202
    row = PeerMessage.objects.get()
    assert row.payload == {"text": "old", "attachments": [
        {"name": "attachment-1.png", "media_type": "image/png", "data": _b64(PNG)},
        {"name": "n.md", "media_type": "text/plain", "data": _b64("hé".encode())},
        {"name": "a_b.md", "media_type": "application/octet-stream", "data": _b64(b"md")},
    ]}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": len(PNG)},
        {"name": "n.md", "media_type": "text/plain", "bytes": 3},
        {"name": "a_b.md", "media_type": "application/octet-stream", "bytes": 2},
    ]


@pytest.mark.parametrize("payload", [
    {"text": "x", "attachments": [], "images": []},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "YQ=="}], "documents": []},
    {"text": "x", "extra": 1},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "YQ==", "size": 1}]},
    {"text": "x", "attachments": [{"name": "", "media_type": "text/plain", "data": "YQ=="}]},
    {"text": "x", "attachments": [{"media_type": "text/plain", "data": "YQ=="}]},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": 1}]},
    {"text": "x", "attachments": [{"name": "a", "media_type": "text/plain", "data": "QUJD!!!!"}]},
    {"text": "x", "attachments": "nope"},
])
def test_the_receiver_refuses_bad_payloads(transactional_db, data_dir, payload):
    peer = _active_peer()
    response = _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours)
    assert response.status_code == 400
    assert orjson.loads(response.content) == {"error": "invalid_payload"}
    assert PeerMessage.objects.count() == 0


@pytest.mark.parametrize(("sizes", "status"), [((3, 3), 202), ((3, 4), 400)])
def test_the_receiver_total_boundary(transactional_db, data_dir, monkeypatch, sizes, status):
    monkeypatch.setattr(peer_messages, "PEER_ATTACHMENT_MAX_TOTAL_BYTES", 6)
    peer = _active_peer()
    payload = {"text": "x", "attachments": [
        {"name": f"{n}.bin", "media_type": "application/octet-stream", "data": _b64(b"x" * size)}
        for n, size in enumerate(sizes)
    ]}
    assert _post(AsyncClient(), _wire_body(payload), bearer=peer.token_ours).status_code == status


def test_the_receiver_body_cap_boundary(transactional_db, data_dir, monkeypatch):
    peer = _active_peer()
    body = _wire_body({"text": "x"})
    size = len(orjson.dumps(body))
    monkeypatch.setattr(inbound_views, "PEER_MESSAGE_MAX_REQUEST_BYTES", size)
    assert _post(AsyncClient(), body, bearer=peer.token_ours).status_code == 202
    monkeypatch.setattr(inbound_views, "PEER_MESSAGE_MAX_REQUEST_BYTES", size - 1)
    other = {**body, "message_id": "pm_" + "b" * 16}
    assert _post(AsyncClient(), other, bearer=peer.token_ours).status_code == 413


def test_the_receiver_cap_is_the_inline_request_cap():
    assert inbound_views.PEER_MESSAGE_MAX_REQUEST_BYTES == inline.INLINE_MAX_REQUEST_BYTES
    assert peer_messages.PEER_ATTACHMENT_MAX_TOTAL_BYTES == inline.INLINE_MAX_BYTES


def test_receiver_edge_shapes(transactional_db, data_dir):
    peer = _active_peer()
    cases = [
        ({"text": "a", "attachments": []}, {"text": "a"}, []),
        ({"text": "b", "images": None, "documents": []}, {"text": "b"}, []),
        ({"text": "c", "attachments": [{"name": "e.bin", "media_type": "application/octet-stream", "data": ""}]},
         {"text": "c", "attachments": [{"name": "e.bin", "media_type": "application/octet-stream", "data": ""}]},
         [{"name": "e.bin", "media_type": "application/octet-stream", "bytes": 0}]),
    ]
    for index, (payload, stored, meta) in enumerate(cases):
        body = {**_wire_body(payload), "message_id": f"pm_edge{index:012d}"}
        assert _post(AsyncClient(), body, bearer=peer.token_ours).status_code == 202, payload
        row = PeerMessage.objects.get(message_id=body["message_id"])
        assert (row.payload, row.attachments_meta) == (stored, meta)


def test_the_receiver_parses_and_validates_off_the_event_loop(transactional_db, data_dir, monkeypatch):
    peer = _active_peer()
    threads: dict = {}
    real_read = inbound_views._read_message_body
    real_prepare = peer_messages.prepare_inbound_payload

    def read(request):
        threads["read"] = threading.get_ident()
        return real_read(request)

    def prepare(payload):
        threads["prepare"] = threading.get_ident()
        return real_prepare(payload)

    monkeypatch.setattr(inbound_views, "_read_message_body", read)
    monkeypatch.setattr(peer_messages, "prepare_inbound_payload", prepare)
    assert _post(AsyncClient(), _wire_body({"text": "x"}), bearer=peer.token_ours).status_code == 202
    main = threading.get_ident()
    assert threads["read"] != main
    assert threads["prepare"] != main


# ── Read APIs and summaries ──────────────────────────────────────────────────


def _row(peer, **kw):
    message_id = kw.pop("message_id", "pm_" + "c" * 16)
    defaults = {"peer": peer, "direction": PeerMessageDirection.IN, "message_id": message_id,
                "thread_id": message_id, "title": "Row", "status": PeerMessageStatus.PENDING,
                "payload": {"text": "body", "attachments": [
                    {"name": "a.txt", "media_type": "text/plain", "data": _b64(b"sentinel-bytes")},
                ]},
                "attachments_meta": [{"name": "a.txt", "media_type": "text/plain", "bytes": 14}]}
    defaults.update(kw)
    return PeerMessage.objects.create(**defaults)


@pytest.fixture
def owner_client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def test_the_attachments_endpoint_returns_entries_off_the_event_loop(transactional_db, owner_client, monkeypatch):
    message = _row(_active_peer())
    threads = []
    real = owner_views._attachments_body

    def spy(pk):
        threads.append(threading.get_ident())
        return real(pk)

    monkeypatch.setattr(owner_views, "_attachments_body", spy)
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/attachments/"))
    assert response.status_code == 200
    assert orjson.loads(response.content) == {"attachments": message.payload["attachments"]}
    assert threads and threads[0] != threading.get_ident()
    assert b"body" not in response.content


def test_the_detail_without_bytes_has_text_and_no_entry(transactional_db, owner_client):
    message = _row(_active_peer())
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/?include_attachments=0"))
    row = orjson.loads(response.content)
    assert row["payload"] == {"text": "body", "attachments": []}
    assert b"sentinel" not in response.content


def test_the_detail_with_bytes_has_the_stored_payload(transactional_db, owner_client):
    message = _row(_active_peer())
    response = asyncio.run(owner_client.get(f"/api/peer-messages/{message.pk}/"))
    assert orjson.loads(response.content)["payload"] == message.payload


@pytest.fixture
def summary_spy(monkeypatch):
    """Record, for every serialized summary, whether its payload (and its parent's) stayed deferred."""
    seen: list = []
    real = serializers.serialize_peer_message

    def spy(message, *args, **kwargs):
        if not kwargs.get("include_attachments", True) or not kwargs.get("include_payload"):
            parent = message.reply_to_message
            seen.append((
                "payload" in message.get_deferred_fields(),
                hasattr(message, "payload_text"),
                parent is None or "payload" in parent.get_deferred_fields(),
            ))
        return real(message, *args, **kwargs)

    monkeypatch.setattr(serializers, "serialize_peer_message", spy)
    monkeypatch.setattr(owner_views, "serialize_peer_message", spy)
    return seen


def test_summary_readers_never_load_the_payload(transactional_db, owner_client, summary_spy, monkeypatch):
    async def no_callback(*args, **kwargs):
        return 200, {}

    monkeypatch.setattr("twicc.peer.outbound.post_status", no_callback)
    peer = _active_peer()
    parent = _row(peer, message_id="pm_parent0000000001", direction=PeerMessageDirection.OUT)
    child = _row(peer, message_id="pm_child00000000001", reply_to=parent.message_id, reply_to_message=parent,
                 thread_id=parent.thread_id)
    asyncio.run(owner_client.get("/api/peer-messages/"))
    asyncio.run(owner_client.get("/api/peer-messages/", {"q": "body"}))
    asyncio.run(owner_client.get(f"/api/peer-messages/{child.pk}/?include_attachments=0"))
    asyncio.run(owner_client.post(f"/api/peer-messages/{child.pk}/done/"))
    invoke(["peer-message", parent.message_id])
    assert len(summary_spy) >= 5
    assert all(entry == (True, True, True) for entry in summary_spy), summary_spy


def test_the_delivery_envelope_reads_the_annotated_text(transactional_db, monkeypatch):
    async def no_callback(*args, **kwargs):
        return 200, {}

    monkeypatch.setattr("twicc.peer.outbound.post_status", no_callback)
    message = _row(_active_peer())
    success, envelope, errors = asyncio.run(peer_messages.mark_delivered(message))
    assert success and errors == []
    assert "> body" in envelope


def _add_pair(peer, index):
    parent = _row(peer, message_id=f"pm_par{index:013d}", direction=PeerMessageDirection.OUT)
    _row(peer, message_id=f"pm_chi{index:013d}", reply_to=parent.message_id, reply_to_message=parent,
         thread_id=parent.thread_id)


def _summary_query_count() -> tuple[int, int]:
    """``(queries, rows)`` of loading and serializing every summary, counted in THIS thread.

    Not around an async view: its ORM work runs in a worker thread, which the query capture
    of the test thread never sees (a count of 0 that can never fail).
    """
    with CaptureQueriesContext(connection) as queries:
        rows = list(peer_messages.peer_message_summary_queryset())
        for row in rows:
            serializers.serialize_peer_message(row)
    return len(queries), len(rows)


def test_summary_queries_do_not_grow_with_rows(transactional_db):
    peer = _active_peer()
    _add_pair(peer, 0)
    small, small_rows = _summary_query_count()
    for index in range(1, 11):
        _add_pair(peer, index)
    large, large_rows = _summary_query_count()
    assert (small_rows, large_rows) == (2, 22)
    # One query for the rows (peer, sessions and parent joined), one for the prefetched replies.
    assert small == large == 2


# ── Purge ────────────────────────────────────────────────────────────────────


def test_the_purge_removes_the_attachments_key_once(transactional_db):
    peer = _active_peer()
    now = djtz.now()
    old = now - timedelta(days=8)
    with_files = _row(peer, message_id="pm_files000000001", status=PeerMessageStatus.DELIVERED, resolved_at=old)
    text_only = _row(peer, message_id="pm_text0000000001", status=PeerMessageStatus.DELIVERED, resolved_at=old,
                     payload={"text": "only"}, attachments_meta=[])
    assert purge_expired_attachment_bytes(now=now) == 1
    with_files.refresh_from_db()
    text_only.refresh_from_db()
    assert with_files.payload == {"text": "body"}
    assert with_files.attachments_meta == [{"name": "a.txt", "media_type": "text/plain", "bytes": 14}]
    assert with_files.purged_at is not None
    assert text_only.purged_at is None
    assert purge_expired_attachment_bytes(now=now) == 0


def test_the_purge_loads_one_row_at_a_time(transactional_db):
    peer = _active_peer()
    old = djtz.now() - timedelta(days=8)
    for index in range(3):
        _row(peer, message_id=f"pm_purge{index:010d}", status=PeerMessageStatus.DONE, resolved_at=old)
    with CaptureQueriesContext(connection) as queries:
        assert purge_expired_attachment_bytes() == 3
    loads = [q["sql"] for q in queries if q["sql"].startswith("SELECT") and '"payload"' in q["sql"].split("FROM")[0]]
    assert len(loads) == 3
    assert all("LIMIT 1" in sql for sql in loads)
```

Create `tests/test_peer_message_attachments_migration.py`:

```python
"""Migration 0153: stored peer rows move to the attachment entries shape (design §4.8.5)."""

import base64
import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

MIGRATE_FROM = [("core", "0152_async_question_state")]
MIGRATE_TO = [("core", "0153_peer_message_attachments")]
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


@pytest.fixture
def restore_latest_migration():
    """Bring the schema back to the current leaf, whatever it is."""
    yield
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes("core"))


def _models(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    apps = executor.loader.project_state(target).apps
    return apps, apps.get_model("core", "Peer"), apps.get_model("core", "PeerMessage")


def _make(Peer, PeerMessage, message_id, payload, meta=(), **extra):
    peer, _ = Peer.objects.get_or_create(
        id="peer_old", defaults={"name": "Old", "base_url": "https://old.example.com", "state": "active"},
    )
    return PeerMessage.objects.create(
        peer=peer, direction="in", message_id=message_id, thread_id=message_id, title="T",
        payload=payload, attachments_meta=list(meta), status="pending", **extra,
    ).pk


@pytest.mark.django_db(transaction=True)
def test_old_rows_are_converted(restore_latest_migration):
    _apps, Peer, PeerMessage = _models(MIGRATE_FROM)
    files = _make(Peer, PeerMessage, "files", {"text": "files", "images": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
    ], "documents": [
        {"type": "document", "title": "spec.pdf",
         "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="}},
        {"type": "document", "title": "a/b\nc.md",
         "source": {"type": "text", "media_type": "text/plain", "data": "hé"}},
        {"type": "document", "source": {"type": "base64", "media_type": "not a type", "data": "AAAA"}},
    ]}, meta=[{"kind": "image", "media_type": "image/png", "bytes": 8}])
    text_only = _make(Peer, PeerMessage, "text", {"text": "hello", "images": [], "documents": []})
    purged = _make(Peer, PeerMessage, "purged", {"text": "kept", "images": [], "documents": []}, meta=[
        {"kind": "image", "media_type": "image/png", "bytes": 9},
        {"kind": "document", "media_type": "text/plain; charset=utf-8", "bytes": 4, "name": "x/y.txt"},
    ])

    _apps, _Peer, PeerMessage = _models(MIGRATE_TO)
    row = PeerMessage.objects.get(pk=files)
    assert row.payload == {"text": "files", "attachments": [
        {"name": "attachment-1.png", "media_type": "image/png", "data": PNG},
        {"name": "spec.pdf", "media_type": "application/pdf", "data": "JVBERi0="},
        {"name": "a_b_c.md", "media_type": "text/plain", "data": base64.b64encode("hé".encode()).decode()},
        {"name": "attachment-4.bin", "media_type": "application/octet-stream", "data": "AAAA"},
    ]}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": 8},
        {"name": "spec.pdf", "media_type": "application/pdf", "bytes": 5},
        {"name": "a_b_c.md", "media_type": "text/plain", "bytes": 3},
        {"name": "attachment-4.bin", "media_type": "application/octet-stream", "bytes": 3},
    ]
    row = PeerMessage.objects.get(pk=text_only)
    assert (row.payload, row.attachments_meta) == ({"text": "hello"}, [])
    row = PeerMessage.objects.get(pk=purged)
    assert row.payload == {"text": "kept"}
    assert row.attachments_meta == [
        {"name": "attachment-1.png", "media_type": "image/png", "bytes": 9},
        {"name": "x_y.txt", "media_type": "application/octet-stream", "bytes": 4},
    ]


@pytest.mark.django_db(transaction=True)
def test_rows_are_loaded_one_at_a_time(restore_latest_migration):
    apps, Peer, PeerMessage = _models(MIGRATE_TO)
    for index in range(3):
        _make(Peer, PeerMessage, f"m{index}", {"text": "x", "images": [], "documents": []})
    module = importlib.import_module("twicc.core.migrations.0153_peer_message_attachments")
    with CaptureQueriesContext(connection) as queries:
        module.convert_peer_messages(apps, None)
    loads = [q["sql"] for q in queries if q["sql"].startswith("SELECT") and '"payload"' in q["sql"].split("FROM")[0]]
    assert len(loads) == 3
    assert all("LIMIT 1" in sql for sql in loads)
    assert all(row.payload == {"text": "x"} for row in PeerMessage.objects.all())


@pytest.mark.django_db(transaction=True)
def test_the_reverse_is_a_noop(restore_latest_migration):
    _apps, Peer, PeerMessage = _models(MIGRATE_TO)
    pk = _make(Peer, PeerMessage, "new", {"text": "x"})
    _apps, _Peer, PeerMessage = _models(MIGRATE_FROM)
    assert PeerMessage.objects.get(pk=pk).payload == {"text": "x"}
```

- [ ] **Step 2: Update the existing peer tests to the new shape.**
  - `tests/test_peer_messages.py`:
    - Add after `_image_block` (`:103-111`):

```python
def _entry(data=b"png-bytes", name="shot.png", media_type="image/png"):
    return {"name": name, "media_type": media_type, "data": base64.b64encode(data).decode("ascii")}
```

    - `_wire_body` (`:118`), `_out_message` (`:537`), `_in_message` (`:1131`): default payload `{"text": …}` (same text, no legacy keys).
    - Delete `test_receive_attachment_per_file_boundaries` (`:227-239`), `test_receive_attachment_count_boundaries` (`:259-271`) and `test_receive_oversized_attachment_rejected` (`:274-279`): the per-file and count caps are removed (D5); `test_peer_attachments.py` covers the total.
    - `test_receive_stores_pending_row` (`:293-294`): replace the two `attachments_meta` assertions by

```python
    assert message.attachments_meta == [{"name": "attachment-1.png", "media_type": "image/png", "bytes": 9}]
    assert message.payload["attachments"] == [_entry(name="attachment-1.png")]
```

    - `_patch_post_message` (`:593-608`): the fake becomes

```python
    async def _fake(base_url, *, bearer, body):
        if calls is not None:
            calls.append({"base_url": base_url, "bearer": bearer, **orjson.loads(body)})
        if network_error:
            raise outbound.PeerOutboundError("ConnectError")
        return status, {}
```

    - Replace `test_outbound_post_message_builds_exact_threading_wire` (`:920-958`) by:

```python
def test_outbound_message_body_builds_exact_threading_wire():
    origin = {"sent_at": "2026-07-24T12:00:00+00:00"}
    payload = {"text": "body"}
    for reply_to in ("", "A_-z"):
        wire = orjson.loads(outbound.build_message_body(
            message_id="message-id", title="Subject", reply_to=reply_to, payload=payload, origin=origin,
        ))
        assert wire == {"message_id": "message-id", "title": "Subject", "reply_to": reply_to,
                        "payload": payload, "origin": origin}
        assert "thread_id" not in wire
```

    - `test_legacy_unsafe_id_is_omitted_but_delivery_still_succeeds` (`:1355`, `:1368`): payload `{"text": "legacy body", "attachments": [_entry()]}`; assertion `assert message.payload["attachments"]`.
    - `test_owner_message_list_searches_title_and_complete_text` (`:1738-1754`): the three payloads become `{"text": "ordinary body"}`, `{"text": "x" * 350 + " deep archive phrase", "attachments": [_entry(b"attachment-search-sentinel")]}`, `{"text": "beta"}`.
    - `test_owner_message_light_detail_keeps_full_text_without_attachment_bytes` (`:1872-1895`): `image = _entry(b"attachment-sentinel")`; payload `{"text": "full **message**", "attachments": [image]}`; expected `row["payload"] == {"text": "full **message**", "attachments": []}`; last line `assert image["data"].encode() not in response.content`.
    - `test_owner_message_attachments_endpoint_returns_only_attachment_blocks` (`:1897-1921`): rename to `test_owner_message_attachments_endpoint_returns_only_attachment_entries`; `image = _entry(b"image")`, `document = _entry(b"document", name="note.txt", media_type="text/plain")`; payload `{"text": "must stay out", "attachments": [image, document]}`; expected `{"attachments": [image, document]}`.
    - `test_purge_expired_attachment_bytes` (`:2218-2263`): `payload = {"text": "keep me", "attachments": [_entry()]}`; `attachments_meta = [{"name": "shot.png", "media_type": "image/png", "bytes": 9}]`; `text_only_old` payload `{"text": "no attachments"}`; replace the `images`/`documents` assertion by `assert resolved_old.payload == {"text": "keep me"}`; the last line becomes `assert resolved_recent.payload["attachments"]`.
    - `test_delivery_envelope_without_text_has_no_quote` (`:2661`): payload `{"text": "", "attachments": [_entry()]}`.
    - Every other literal `"images": [], "documents": []` in this file (`:177-179` stay: they test the legacy receive path; `:1825`, `:1860`, `:2624` and the like): remove the two keys.
  - `tests/test_peer_cli.py`: add `import orjson`. The three fakes with the old keyword signature (`:206` in `test_peer_send_end_to_end_in_process`, `:245` in `test_peer_send_omitted_or_empty_reply_to_creates_root`, `:289` in `test_peer_send_conforming_reply_to_reaches_transport_unchanged`) become `async def _fake_post(base_url, *, bearer, body):` with `wire = orjson.loads(body)` and the same recorded values read from `wire` (`wire["message_id"]`, `wire["title"]`, `wire["reply_to"]`). The `**kwargs` fakes (`:325`, `:382`, `:404`) stay. Remove the legacy keys from the literal payloads (`:66`, `:89`, `:99`, `:189`, `:284`, `:321`, `:355`, `:365`).
  - `tests/test_peer_updates_consumer.py`: remove the legacy keys from the three payloads (`:46`, `:58`, `:70`), and add the deferred check inside the test, before `scenario` is defined:

```python
    from twicc.core import serializers

    deferred = []
    real_serialize = serializers.serialize_peer_message

    def spy(message, *args, **kwargs):
        deferred.append(("payload" in message.get_deferred_fields(), hasattr(message, "payload_text")))
        return real_serialize(message, *args, **kwargs)

    monkeypatch.setattr(serializers, "serialize_peer_message", spy)
```

    and after `_run(scenario())`: `assert deferred and all(entry == (True, True) for entry in deferred)`.
  - `tests/test_mcp_attach.py`: add `"peer_send"` to the parametrize of `test_attach_stays_an_array_of_strings_with_the_limit_in_its_description`, and add

```python
def test_the_peer_send_description_advises_one_large_send_per_call():
    tool = next(tool for tool in iter_mcp_tools() if tool.name == "peer_send")
    assert "one" in tool.description and "tool call" in tool.description
    assert "travels inline" in tool.input_schema["properties"]["attach"]["description"]
```

- [ ] **Step 3: Write the failing frontend tests.** In `frontend/src/utils/peerMessageContent.test.js`:
  - Import list: replace `peerBlockToFile` by `peerEntryToFile, peerEntryToMediaItem` (keep `addPeerAttachmentsToDraft` in the dynamic import test or add it to the static import).
  - Replace `merges attachment blocks without mutating the lightweight detail` by:

```js
test('merges the attachment entries without mutating the lightweight detail', () => {
    const detail = { id: 1, payload: { text: 'message', attachments: [] } }
    const attachments = { attachments: [{ name: 'a.txt', media_type: 'text/plain', data: 'YQ==' }] }

    const merged = mergePeerAttachments(detail, attachments)

    assert.notStrictEqual(merged, detail)
    assert.notStrictEqual(merged.payload, detail.payload)
    assert.deepEqual(merged.payload, { text: 'message', attachments: attachments.attachments })
    assert.deepEqual(detail.payload, { text: 'message', attachments: [] })
    assert.deepEqual(mergePeerAttachments(detail, {}).payload.attachments, [])
})
```

  - Replace `peerBlockToFile converts every block kind to a File, keeping its name and type` by:

```js
test('peerEntryToFile keeps the real name and media type', async () => {
    const patch = peerEntryToFile({ name: 'fix.patch', media_type: 'text/x-diff', data: 'ZGlmZg==' })
    assert.ok(patch instanceof File)
    assert.equal(patch.name, 'fix.patch')
    assert.equal(patch.type, 'text/x-diff')
    assert.equal(await patch.text(), 'diff')
    const empty = peerEntryToFile({ name: 'empty.bin', media_type: '', data: '' })
    assert.equal(empty.size, 0)
    assert.equal(empty.type, 'application/octet-stream')
    assert.equal(peerEntryToFile({ name: 'x', media_type: 'text/plain' }), null)
    assert.equal(peerEntryToFile({ name: '', media_type: 'text/plain', data: 'YQ==' }), null)
})

test('the preview shows a thumbnail for images and the kind icon for the others', () => {
    const image = peerEntryToMediaItem({ name: 'shot.png', media_type: 'image/png', data: 'iVBORw==' }, 0)
    assert.equal(image.type, 'image')
    assert.equal(image.src, 'data:image/png;base64,iVBORw==')
    assert.equal(image.state, 'ready')
    assert.equal(image.kind, 'image')
    const pdf = peerEntryToMediaItem({ name: 'spec.pdf', media_type: 'application/pdf', data: 'JVBERi0=' }, 1)
    assert.equal(pdf.type, 'pdf')
    assert.equal(pdf.src, null)
    assert.equal(pdf.icon, 'file-pdf')
    assert.equal(pdf.size, 5)
    const video = peerEntryToMediaItem({ name: 'clip.mp4', media_type: 'video/mp4', data: 'AAAA' }, 2)
    assert.equal(video.type, 'other')
    assert.equal(video.icon, 'file-video')
    assert.notEqual(image.id, pdf.id)
    assert.equal(peerEntryToMediaItem({ name: 'x' }, 3), null)
})
```

  - Replace `reports a draft attachment failure instead of hiding it` by:

```js
test('adds the entries to the draft in order and reports a failure instead of hiding it', async () => {
    const { addPeerAttachmentsToDraft } = await import('./peerMessageContent.js')
    const payload = { attachments: [{ name: 'one.txt' }, { name: 'two.png' }, { name: 'three.mp4' }] }
    const attempted = []

    const error = await addPeerAttachmentsToDraft(
        payload,
        entry => ({ name: entry.name }),
        async file => {
            attempted.push(file.name)
            if (file.name === 'two.png') throw new Error('IndexedDB failed')
        },
    )

    assert.deepEqual(attempted, ['one.txt', 'two.png'])
    assert.equal(
        error,
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.',
    )
    const added = []
    assert.equal(await addPeerAttachmentsToDraft(payload, entry => ({ name: entry.name }), async f => { added.push(f.name) }), '')
    assert.deepEqual(added, ['one.txt', 'two.png', 'three.mp4'])
    assert.equal(await addPeerAttachmentsToDraft({ attachments: [{ name: 'x' }] }, () => null, async () => {}),
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.')
})
```

- [ ] **Step 4: Run and see them fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_attachments.py tests/test_peer_message_attachments_migration.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_updates_consumer.py tests/test_mcp_attach.py -q; cd frontend && npm test`
Expected: FAIL (no `post_message(body=)`, no `peer:send_attachments`, legacy shape stored, migration `0153` missing, `peerEntryToFile` not exported).

- [ ] **Step 5: Help texts and the `peer-send` command.**
  - Add to `src/twicc/cli/_drop_request/help_strings.py`, right after `ATTACH_EVERY_MESSAGE_HELP` (before the builder functions; extend its `inline` import with `PEER_SEND_TIMEOUT_WITH_FILES, PEER_TOO_LARGE_HINT`):

```python
PEER_ATTACH_HELP = (
    "File to attach (repeatable), of any type. " + _ATTACH_FORMS + " Every file travels inline to "
    "the peer, paths included, so all files together are limited to 50 MB. " + PEER_TOO_LARGE_HINT
)
PEER_TIMEOUT_HELP = (
    f"Seconds to wait for the server's final status: 30 by default, {PEER_SEND_TIMEOUT_WITH_FILES} when "
    "the message carries files (a 50 MB send at 2 Mbit/s takes about 5 minutes). The request is not "
    "cancelled. Exit 5, exit 4, and exit 3 with code unreachable or send_failed mean the message may "
    "still have reached the peer: run `peer-message <message_id>` with the id of the output before you "
    "send again. If the backend restarts during the send, the message stays pending, and a pending "
    "status does not prove that the send completed. Agents: send files with the MCP peer_send tool, or "
    "give the shell call a timeout above 8 minutes. A CLI killed by its shell, or exit 7 over --remote, "
    "gives no id: check the Peers outbox in the TwiCC UI or report to your user; do not send again blindly."
)
```

    In the module docstring, replace the paragraph written by Task 4 Step 4 (it starts with "The set of builders covers every flag") by this rewrapped paragraph:

    ```
    The set of builders covers every flag whose help text depends on the user's
    current providers / presets / defaults. Static texts that several commands
    share, or that are built from the inline attachment limits (``--no-expand``,
    the prompt include hint, the ``--attach`` texts, the ``peer-send``
    ``--timeout``), are module-level constants, defined before the builder
    functions. Any other static flag (the ``--timeout`` of the other commands,
    ...) keeps its inline help in the calling command — duplicating a one-liner
    is cheaper than shipping a tiny helper per flag.
    ```

  - Replace `src/twicc/cli/peer_send.py` by:

```python
"""Top-level ``twicc peer-send`` command (write, drop-request)."""

from __future__ import annotations

import typer

from twicc.cli._drop_request.help_strings import PEER_ATTACH_HELP, PEER_TIMEOUT_HELP

# The wait of a message without files; a message with files waits PEER_SEND_TIMEOUT_WITH_FILES.
PEER_SEND_DEFAULT_TIMEOUT = 30


def peer_send_cmd(
    peer: str = typer.Argument(
        ...,
        help="Peer id (peer_...) or its exact local name — see `twicc peers`.",
    ),
    title: str = typer.Argument(
        ...,
        help=(
            "Required subject, shown prominently to the remote user (inbox, "
            "notification, delivery). One line of text (never a file path), "
            "100 characters max — aim shorter, like an email subject."
        ),
    ),
    prompt: str = typer.Argument(
        ...,
        help=(
            "Message text, or path to a file whose content is the message. "
            "The receiving agent shares no memory with this instance — the "
            "message must be fully self-contained."
        ),
    ),
    reply_to: str | None = typer.Option(
        None,
        "--reply-to",
        help=(
            "The peer message this one answers (pm_…), taken from the "
            "header of a delivered peer message."
        ),
    ),
    attach: list[str] = typer.Option([], "--attach", help=PEER_ATTACH_HELP),
    timeout: int | None = typer.Option(None, "--timeout", help=PEER_TIMEOUT_HELP),
) -> None:
    """Send a titled message to a peer TwiCC instance.

    Every send carries a required TITLE — the subject the remote user triages
    on. No confirmation on this side — but delivery requires the REMOTE user's
    approval: the returned peer_status stays "pending" until they deliver it
    to an agent, mark it done (dealt with themselves), or refuse it. Re-check
    later with "twicc peer-message <MESSAGE_ID>".

    Files travel inline to the peer: send one message with large files per
    tool call. A 50 MB send can take minutes, and a tool call times out after
    10 minutes.
    """
    # Lazy imports to keep --help fast (no Django setup until we need it).
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request import attach_sources, transport
    from twicc.cli._drop_request.discovery import ServerDownError
    from twicc.cli._drop_request.output import build_final, emit_validation_errors
    from twicc.cli._drop_request.prompt import PromptError, resolve_prompt
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.cli._output import emit_error, emit_json
    from twicc.core.models import Peer, PeerMessage, PeerState
    from twicc.core.services.attachments.inline import PEER_SEND_TIMEOUT_WITH_FILES, PEER_TOO_LARGE_HINT
    from twicc.core.services.peer_messages import validate_reply_to, validate_title
    from twicc.core.services.peer_tokens import mint_message_id, peer_credentials_are_active
```

  Keep the body from `try: transport.ensure_server_available()` (`:87`) to the prompt resolution (`:147`) unchanged. Replace everything after it (`:149-199`) by:

```python
    # Every file travels inline on the peer wire, paths included: all count toward the
    # 50 MB limit, checked before anything is copied.
    sources, attach_errors = attach_sources.resolve(attach or [], hint=PEER_TOO_LARGE_HINT, count_paths=True)
    if attach_errors:
        emit_validation_errors(attach_errors)
        raise typer.Exit(1)

    # Minted here, so every output of a submitted send can name the message, a timeout included.
    message_id = mint_message_id()
    payload = {
        "peer": peer_row.id,
        "title": clean_title,
        "reply_to": clean_reply_to,
        "text": text,
        "message_id": message_id,
    }
    # Origin: best-effort identity of the calling session — the MCP dispatcher
    # sets the forced_session_id ContextVar on every tool call, and a real CLI
    # subprocess resolves via PID ancestry.
    current_session = resolve_current_session()
    if current_session is not None:
        payload["origin_session_id"] = current_session.id

    kind = "peer:send"
    if sources:
        refs, stage_errors = attach_sources.stage(sources, bucket=attach_sources.new_request_bucket())
        if stage_errors:
            emit_validation_errors(stage_errors)
            raise typer.Exit(1)
        payload["attachments"] = attach_sources.as_payload(refs)
        # A kind of its own: an older backend refuses it instead of sending the text alone.
        kind = "peer:send_attachments"
    if timeout is None:
        timeout = PEER_SEND_TIMEOUT_WITH_FILES if sources else PEER_SEND_DEFAULT_TIMEOUT

    sub = transport.submit(payload, kind=kind)
    outcome = transport.wait(sub, timeout_seconds=timeout)
    sub.cleanup()

    final = build_final(outcome, request_uuid=sub.request_uuid, timeout=timeout)
    # A ``sent`` status names the stored id (an older backend mints its own for a text-only
    # send). Every other outcome names the id minted here, so the caller can check it with
    # ``peer-message`` before sending again.
    if not final.get("message_id"):
        final["message_id"] = message_id
    if not final.get("peer_id"):
        final["peer_id"] = peer_row.id
    emit_json(final)

    if outcome.status == "sent":
        raise typer.Exit(0)
    if outcome.status == "rejected":
        raise typer.Exit(3)
    if outcome.status == "failed":
        raise typer.Exit(4)
    raise typer.Exit(5)  # timeout
```

  The pre-checks at `:87-147` use `emit_error`, `ValidationError`, `Peer`, `PeerMessage`, `PeerState`, `validate_title`, `validate_reply_to`, `peer_credentials_are_active`, `PromptError`, `resolve_prompt`: all still imported above.

- [ ] **Step 6: Outbound client.** In `src/twicc/peer/outbound.py`:
  - Imports: add `import orjson` and `from twicc.core.services.attachments.inline import transfer_timeout`.
  - Replace `_post` (`:37-49`) by:

```python
def _response_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def _post(base_url: str, path: str, json_body: dict, *, bearer: str | None) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {bearer}"} if bearer is not None else {}
    try:
        async with httpx.AsyncClient(timeout=OUTBOUND_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=json_body, headers=headers)
    except httpx.HTTPError as exc:
        raise PeerOutboundError(type(exc).__name__) from exc
    return response.status_code, _response_json(response)
```

  - Replace `post_message` (`:78-93`) by:

```python
def build_message_body(
    *, message_id: str, title: str, reply_to: str, payload: dict, origin: dict,
) -> bytes:
    """The serialized ``POST /peer/messages/`` body (phase 2 design §4.8.1).

    Serialized once by the sender, which checks its size before storing the row.
    """
    return orjson.dumps({
        "message_id": message_id,
        "title": title,
        "reply_to": reply_to,
        "payload": payload,
        "origin": origin,
    })


async def post_message(base_url: str, *, bearer: str, body: bytes) -> tuple[int, dict]:
    """POST a serialized message. Connect and read keep the usual timeout; the write gets
    :func:`transfer_timeout` of the body, because the async httpcore backend bounds the
    whole body write with one deadline (§4.8.3 step 6)."""
    url = base_url.rstrip("/") + "/peer/messages/"
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(OUTBOUND_TIMEOUT_SECONDS, write=transfer_timeout(len(body)))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise PeerOutboundError(type(exc).__name__) from exc
    return response.status_code, _response_json(response)
```

- [ ] **Step 7: Peer service `src/twicc/core/services/peer_messages.py`.**
  - Module docstring `:3-6`: "Send path: the ``peer:send`` (text only) and ``peer:send_attachments`` (with staged files) drop-request kinds (CLI ``twicc peer-send`` → RPC → MCP) land in :func:`send_peer_message_from_drop_payload` / :func:`send_peer_attachments_from_drop_payload`, then :func:`send_peer_message_from_payload`; the owner REST composer calls the latter directly."
  - Imports: add `import mimetypes` and `from twicc.core.services.attachments import inline`.
  - Replace `:33-41` (the three caps, their comment, `_PAYLOAD_KEYS`) by:

```python
# Total decoded bytes of the attachments of one peer message (D5, §4.8.1).
PEER_ATTACHMENT_MAX_TOTAL_BYTES = inline.INLINE_MAX_BYTES

# ``images`` and ``documents`` only exist to accept an older sender (D16).
_PAYLOAD_KEYS = frozenset({"text", "attachments", "images", "documents"})
_LEGACY_PAYLOAD_KEYS = ("images", "documents")
_ENTRY_KEYS = frozenset({"name", "media_type", "data"})

ERROR_INVALID_MESSAGE_ID = "invalid_message_id"
ERROR_MESSAGE_TOO_LARGE = "message_too_large"
```

  - `_resolve_reply_to_message` (`:111`): `candidates = PeerMessage.objects.filter(peer=peer, message_id=reply_to).defer("payload")`.
  - Add, before `# ── Broadcasts`:

```python
def _trimmed_replies():
    from django.db.models import Prefetch

    from twicc.core.models import PeerMessage

    # What the "answered by" line reads: never the payload of a reply.
    return Prefetch("replies", queryset=PeerMessage.objects.only("pk", "reply_to_message", "created_at", "origin"))


def peer_message_summary_queryset(queryset=None):
    """Rows for a summary, without building any payload object (phase 2 design §4.8.4).

    A stored payload can hold about 67 MB of base64; a summary reads only its text. ``payload``
    and the parent's ``payload`` are deferred and the text is annotated as ``payload_text``
    (read by ``serializers.peer_message_text``). A reader of these rows never touches
    ``message.payload``: a lazy load raises in an async context.
    """
    from django.db.models.fields.json import KT

    from twicc.core.models import PeerMessage

    rows = queryset if queryset is not None else PeerMessage.objects.all()
    return (
        rows.select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
        .defer("payload", "reply_to_message__payload")
        .annotate(payload_text=KT("payload__text"))
        .prefetch_related(_trimmed_replies())
    )


def peer_message_full_queryset():
    """Rows with their full payload: only the detail with bytes reads it."""
    from twicc.core.models import PeerMessage

    return (
        PeerMessage.objects
        .select_related("peer", "origin_session", "delivered_to_session", "reply_to_message")
        .defer("reply_to_message__payload")
        .prefetch_related(_trimmed_replies())
    )
```

  - `_serialize_for_broadcast._load` (`:381-387`): `fresh = peer_message_summary_queryset().filter(pk=message.pk).first()`.
  - Replace `:414-507` (`_block_decoded_size`, `_block_name`, `_attachments_meta`, `_validated_block_size`, `_validate_inbound_payload`) by:

```python
# ── Payload helpers ─────────────────────────────────────────────────────────

class InboundPayload(NamedTuple):
    """A validated inbound payload, ready to store: ``{text, attachments?}`` and its summary rows."""
    payload: dict
    attachments_meta: list


def _legacy_block_ok(block) -> bool:
    """The SDK block shape of an older sender: ``source.type`` ``base64`` or ``text``, non-empty data."""
    if not isinstance(block, dict):
        return False
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") not in ("base64", "text"):
        return False
    data = source.get("data")
    return isinstance(data, str) and bool(data)


def _entry_ok(entry) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry) == _ENTRY_KEYS
        and isinstance(entry["name"], str) and bool(entry["name"])
        and isinstance(entry["media_type"], str)
        and isinstance(entry["data"], str)
    )


def _raw_entries(payload: dict) -> tuple[list[dict], PeerError | None]:
    """The wire entries of *payload*: its ``attachments``, or its converted legacy blocks (D16)."""
    if "attachments" in payload:
        if any(key in payload for key in _LEGACY_PAYLOAD_KEYS):
            return [], PeerError("payload", "invalid_attachments",
                                 "attachments cannot be combined with images or documents")
        entries = payload["attachments"]
        if not isinstance(entries, list):
            return [], PeerError("attachments", "invalid", "attachments must be a list")
        if not all(_entry_ok(entry) for entry in entries):
            return [], PeerError("attachments", "invalid_entry", "malformed attachment entry")
        return entries, None
    blocks: dict[str, list] = {}
    for key in _LEGACY_PAYLOAD_KEYS:
        value = payload.get(key, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            return [], PeerError(key, "invalid", f"{key} must be a list")
        if not all(_legacy_block_ok(block) for block in value):
            return [], PeerError(key, "invalid_block", f"malformed attachment block in {key}")
        blocks[key] = value
    return inline.entries_from_legacy_blocks(blocks["images"], blocks["documents"]), None


def prepare_inbound_payload(payload) -> tuple[InboundPayload | None, list[PeerError]]:
    """Validate and normalize an inbound wire payload (phase 2 design §4.8.2).

    Blocking (base64 validation of up to 50 MB): run it in a worker thread. Only ``text``,
    ``attachments`` and the legacy ``images`` / ``documents`` keys are known. The decoded
    sizes are added up before any decoding; the names and media types are sanitized once,
    here, because they are stored forever in ``attachments_meta``.
    """
    from twicc.core.services.attachments.staging import AttachmentError, name_max_bytes, normalize_filename

    if not isinstance(payload, dict):
        return None, [PeerError("payload", "invalid", "payload must be an object")]
    errors: list[PeerError] = []
    unknown = set(payload) - _PAYLOAD_KEYS
    if unknown:
        errors.append(PeerError("payload", "unknown_keys", f"unknown payload keys: {sorted(unknown)}"))
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append(PeerError("text", "empty_text", "text is required"))
    entries, entries_error = _raw_entries(payload)
    if entries_error is not None:
        errors.append(entries_error)
    if errors:
        return None, errors

    budget = inline.InlineBudget(inline.PEER_TOO_LARGE_HINT, limit=PEER_ATTACHMENT_MAX_TOTAL_BYTES)
    sizes: list[int] = []
    try:
        for entry in entries:
            size = inline.decoded_size(entry["data"])
            budget.add(size)
            sizes.append(size)
    except AttachmentError as exc:
        code = "total_too_large" if exc.code == inline.ERROR_TOO_LARGE else "invalid_entry"
        return None, [PeerError("attachments", code, str(exc))]
    for entry in entries:
        try:
            base64.b64decode(entry["data"], validate=True)
        except (binascii.Error, ValueError):
            return None, [PeerError("attachments", "invalid_entry", "attachment data is not valid base64")]

    max_bytes = name_max_bytes()
    clean_entries = [
        {
            "name": normalize_filename(entry["name"], max_bytes),
            "media_type": inline.sanitize_media_type(entry["media_type"]),
            "data": entry["data"],
        }
        for entry in entries
    ]
    clean: dict = {"text": text}
    if clean_entries:
        clean["attachments"] = clean_entries
    meta = [
        {"name": entry["name"], "media_type": entry["media_type"], "bytes": size}
        for entry, size in zip(clean_entries, sizes, strict=True)
    ]
    return InboundPayload(clean, meta), []


def _encode_staged_attachments(refs) -> tuple[list[dict], list[dict]]:
    """Wire entries and summary rows of staged files, in order (blocking: a worker thread).

    The total of the staged sizes is checked before any byte is read (§4.8.3 step 2).
    """
    from twicc.core.services.attachments.staging import (
        ERROR_MISSING,
        AttachmentError,
        content_location,
        load_entry,
    )

    entries = [load_entry(ref) for ref in refs]
    total = sum(entry.size for entry in entries)
    if total > PEER_ATTACHMENT_MAX_TOTAL_BYTES:
        raise AttachmentError(inline.ERROR_TOO_LARGE, inline.too_large_message(
            total, PEER_ATTACHMENT_MAX_TOTAL_BYTES, inline.PEER_TOO_LARGE_HINT,
        ))
    wire: list[dict] = []
    meta: list[dict] = []
    for entry in entries:
        try:
            data = content_location(entry).read_bytes()
        except OSError as exc:
            raise AttachmentError(ERROR_MISSING, "Attachment not found") from exc
        media_type = mimetypes.guess_type(entry.filename)[0] or inline.OCTET_STREAM
        wire.append({"name": entry.filename, "media_type": media_type,
                     "data": base64.b64encode(data).decode("ascii")})
        meta.append({"name": entry.filename, "media_type": media_type, "bytes": len(data)})
    return wire, meta


_REJECTED_WITH_ATTACHMENTS = (
    "The remote instance rejected the message. It may be too old to receive attachments."
)
_TOO_LARGE = "The remote instance, or a proxy in front of it, refused the message size."
_TOO_LARGE_WITH_ATTACHMENTS = _TOO_LARGE + " An older instance also refuses any attachment."


def _rejection_text(http_status: int | None, response_body: dict, *, has_attachments: bool) -> str:
    """The error text of a refused send, chosen on the HTTP status (§4.8.3 step 7).

    A proxy or a tunnel can answer 413 without a JSON body, so the body code is not reliable.
    """
    from twicc.peer import outbound

    if http_status == 413:
        return _TOO_LARGE_WITH_ATTACHMENTS if has_attachments else _TOO_LARGE
    if http_status == 400 and has_attachments:
        return _REJECTED_WITH_ATTACHMENTS
    return outbound.response_error_message(response_body, "The remote instance rejected the message.")
```

  - Replace `send_peer_message_from_payload` (`:512-708`) by the version below, then add the two drop wrappers after it:

```python
async def send_peer_message_from_payload(
    payload: dict, *, author: str = PEER_MESSAGE_AUTHOR_AGENT,
) -> PeerSendResult:
    """Send a peer message (the drop wrappers below, and the owner REST composer).

    Payload: ``{peer: <peer_id or exact local name>, title, reply_to?, text,
    attachments?, message_id?, origin_session_id?, project_id?}``. ``attachments``
    are staged refs ``[{bucket, id}, ...]``, released by the drop wrappers.
    ``message_id`` is the id the CLI minted, so every output of a submitted send
    names the message; without it (the owner composer, an older CLI) the service mints one.
    ``project_id`` is the owner's hand-attached project for a message no session
    sends (the compose dialog); it must exist, and is ignored at read time when an
    origin session is set.

    ``author`` is a keyword-only code path, deliberately NOT read from the
    payload: the drop-request/RPC surface always sends the default
    ``"agent"``, and only the owner REST composer passes ``"human"``.
    """
    from twicc.core.models import Peer, PeerMessage, PeerMessageDirection, PeerMessageStatus, Project, Session
    from twicc.core.services.attachments import planner as attachment_planner
    from twicc.core.services.attachments.staging import AttachmentError
    from twicc.peer import outbound

    peer_ref = (payload.get("peer") or "").strip()
    title, title_error = validate_title(payload.get("title"))
    reply_to, reply_to_error = validate_reply_to(payload.get("reply_to"))
    text = (payload.get("text") or "").strip()
    project_id = (payload.get("project_id") or "").strip() or None
    requested_id = payload.get("message_id")

    errors: list[PeerError] = []
    try:
        refs = attachment_planner.validate_attachment_frame(payload)
    except AttachmentError as exc:
        refs = ()
        errors.append(PeerError("attachments", exc.code, str(exc)))
    if requested_id is not None and (
        not isinstance(requested_id, str) or PEER_MESSAGE_ID_PATTERN.fullmatch(requested_id) is None
    ):
        errors.append(PeerError(
            "message_id", ERROR_INVALID_MESSAGE_ID, "message_id must be a valid peer message id",
        ))
    if not peer_ref:
        errors.append(PeerError("peer", "missing", "peer is required"))
    if title_error is not None:
        errors.append(title_error)
    if reply_to_error is not None:
        errors.append(reply_to_error)
    if not text:
        errors.append(PeerError("text", "empty_text", "text is required"))
    if project_id and not await sync_to_async(lambda: Project.objects.filter(id=project_id).exists())():
        errors.append(PeerError("project_id", "project_not_found", "Project not found."))
    if errors:
        return PeerSendResult(False, None, None, errors, {})

    def _resolve_peer():
        peer = Peer.objects.filter(id=peer_ref).first()
        if peer is None:
            peer = Peer.objects.filter(name=peer_ref).first()
        return peer

    peer = await sync_to_async(_resolve_peer)()
    if peer is None:
        return PeerSendResult(False, None, None, [PeerError(
            "peer", "not_found", f"No peer matches {peer_ref!r} (by id or exact name).",
        )], {})
    if peer_error := _peer_send_error(peer):
        return PeerSendResult(False, None, peer.id, [peer_error], {})

    reply_to_message = await sync_to_async(
        _resolve_reply_to_message
    )(peer, PeerMessageDirection.OUT, reply_to)
    if reply_to and reply_to_message is None:
        return PeerSendResult(False, None, peer.id, [PeerError(
            "reply_to", "unknown_reply_to",
            "No message with this id exists for the selected peer.",
        )], {})

    message_id = requested_id or mint_message_id()
    origin_session = None
    origin_session_id = payload.get("origin_session_id")
    if origin_session_id:
        origin_session = await sync_to_async(
            lambda: Session.objects.filter(id=origin_session_id).first()
        )()

    # The staged files, read and encoded off the event loop (§4.8.3 steps 2-3).
    attachments: list[dict] = []
    attachments_meta: list[dict] = []
    if refs:
        try:
            attachments, attachments_meta = await asyncio.to_thread(_encode_staged_attachments, refs)
        except AttachmentError as exc:
            code, message_text, _names = attachment_planner.describe_attachment_error(exc)
            return PeerSendResult(False, None, peer.id, [PeerError("attachments", code, message_text)], {})

    # Timezone-aware UTC ISO-8601: the receiver renders it in the inbox and in
    # the delivery envelope.
    sent_at = _now().isoformat()
    # The instant plus the authorship are the whole of the provenance, on the
    # wire AND on the row.
    #
    # No session title (decision of 2026-08-10): not on the wire, because it is
    # an LLM summary of private content its owner never agreed to disclose, and
    # the receiver can do nothing with it; not on the row either, because a
    # stored copy goes stale the moment the session is renamed. The sending
    # session is kept as the `origin_session` FK, whose title is read live at
    # serialization — that is what the inbox displays.
    origin = {"sent_at": sent_at, "author": author}
    # Without files the payload is ``{"text": ...}``: an older receiver still accepts it.
    wire_payload: dict = {"text": text}
    if attachments:
        wire_payload["attachments"] = attachments
    body = await asyncio.to_thread(
        outbound.build_message_body,
        message_id=message_id, title=title, reply_to=reply_to, payload=wire_payload, origin=origin,
    )
    # The text has no cap of its own and the entry names are not bounded: the decoded limit
    # alone does not bound the body (§4.8.3 step 4).
    if len(body) > inline.INLINE_MAX_REQUEST_BYTES:
        return PeerSendResult(False, None, peer.id, [PeerError(
            "attachments", ERROR_MESSAGE_TOO_LARGE,
            f"The message (text and attachments) is {inline.format_mb(len(body))} once encoded; "
            f"the limit is {inline.format_mb(inline.INLINE_MAX_REQUEST_BYTES)}. {inline.PEER_TOO_LARGE_HINT}",
        )], {})

    message = PeerMessage(
        peer=peer,
        direction=PeerMessageDirection.OUT,
        message_id=message_id,
        reply_to=reply_to,
        reply_to_message=reply_to_message,
        thread_id=reply_to_message.thread_id if reply_to_message is not None else message_id,
        title=title,
        payload=wire_payload,
        attachments_meta=attachments_meta,
        origin=origin,
        origin_session=origin_session,
        project_id=project_id,
        status=PeerMessageStatus.PENDING,
    )

    def _store_outbound():
        fresh_peer = Peer.objects.filter(pk=peer.pk).first()
        if fresh_peer is None:
            return None, PeerError("peer", "not_found", "Peer no longer exists.")
        if peer_error := _peer_send_error(fresh_peer):
            return fresh_peer, peer_error
        # Defensive: a reused id ends here, never with an IntegrityError.
        if PeerMessage.objects.filter(peer=fresh_peer, message_id=message_id).exists():
            return fresh_peer, PeerError(
                "message_id", ERROR_INVALID_MESSAGE_ID, "This message id is already used for this peer.",
            )
        message.peer = fresh_peer
        message.save(force_insert=True)
        return fresh_peer, None

    peer, peer_error = await run_under_db_write_lock(lambda: sync_to_async(_store_outbound)())
    if peer_error is not None:
        return PeerSendResult(False, None, peer.id if peer else None, [peer_error], {})
```

  From `credential_snapshot = (` (`:628`) to the end of the function, keep the code unchanged except:

```python
    response_body: dict = {}
    detail = ""
    try:
        http_status, response_body = await outbound.post_message(
            peer.base_url, bearer=peer.token_theirs, body=body,
        )
    except outbound.PeerOutboundError as exc:
        http_status, detail = None, str(exc)
```

  and

```python
    error_detail = detail or _rejection_text(http_status, response_body, has_attachments=bool(attachments))
```

  Then add:

```python
def _attachments_rejected(message: str) -> PeerSendResult:
    return PeerSendResult(False, None, None, [PeerError("attachments", "invalid_attachments", message)], {})


async def send_peer_message_from_drop_payload(payload: dict) -> PeerSendResult:
    """Drop-request handler for ``kind="peer:send"``: a message without files.

    Refs travel only in ``peer:send_attachments``: refs here are a bug, refused. Whatever
    the outcome, the refs of the payload are released (§4.5.3).
    """
    from twicc.core.services.attachments import drop as attachment_drop
    from twicc.core.services.attachments import lifecycle as attachment_lifecycle

    refs = attachment_drop.refs_to_release(payload)
    try:
        if attachment_drop.has_legacy_fields(payload):
            return _attachments_rejected(attachment_drop.LEGACY_FIELDS_MESSAGE)
        if payload.get("attachments"):
            return _attachments_rejected("attachments travel only in a peer:send_attachments request")
        return await send_peer_message_from_payload(payload)
    finally:
        attachment_lifecycle.delivery_release(refs)()


async def send_peer_attachments_from_drop_payload(payload: dict) -> PeerSendResult:
    """Drop-request handler for ``kind="peer:send_attachments"``: a message with staged files.

    A kind of its own, so an older backend refuses it instead of sending the text without the
    files. The refs are released on every outcome, the early validation returns included.
    """
    from twicc.core.services.attachments import drop as attachment_drop
    from twicc.core.services.attachments import lifecycle as attachment_lifecycle

    refs = attachment_drop.refs_to_release(payload)
    try:
        if attachment_drop.has_legacy_fields(payload):
            return _attachments_rejected(attachment_drop.LEGACY_FIELDS_MESSAGE)
        return await send_peer_message_from_payload(payload)
    finally:
        attachment_lifecycle.delivery_release(refs)()
```

  - Receiver `receive_peer_message` (`:733-768`): replace `if _validate_inbound_payload(payload): return 400, …` by

```python
    prepared, payload_errors = await asyncio.to_thread(prepare_inbound_payload, payload)
    if payload_errors:
        return 400, {"error": "invalid_payload"}
```

    delete `clean_payload = {…}` (`:753-757`), and build the row with `payload=prepared.payload, attachments_meta=prepared.attachments_meta`.
  - In `_store` (`:778-780`): `.filter(…).only("pk", "status").first()` for the replay check.
  - `apply_status_callback._resolve` (`:820-824`): add `.defer("payload")` before `.first()`.
  - `build_delivery_envelope` (`:933`): `text = peer_message_text(message).rstrip("\n")`, with `from twicc.core.serializers import peer_message_text` added to the function's local imports.
  - `_fresh_message` (`:1053-1061`): `return await sync_to_async(lambda: peer_message_summary_queryset().filter(pk=pk).first())()`.

- [ ] **Step 8: Receiver view.** In `src/twicc/peer/inbound_views.py`:
  - Imports: add `import asyncio` and `from twicc.core.services.attachments.inline import INLINE_MAX_REQUEST_BYTES`.
  - Replace `:28-30` by:

```python
# Up to 50 MB of attachments, base64-encoded, plus the JSON: the inline request cap (D12).
PEER_MESSAGE_MAX_REQUEST_BYTES = INLINE_MAX_REQUEST_BYTES
```

  - Add before `message_receive`:

```python
def _read_message_body(request) -> tuple[dict | None, JsonResponse | None]:
    """Read and parse the message body; blocking (up to 72 MB): run in a worker thread.

    ``request.read()``, NOT ``request.body``: Django's DATA_UPLOAD_MAX_MEMORY_SIZE check fires
    only in the ``body`` property; the per-view cap is enforced here instead.
    """
    body = request.read(PEER_MESSAGE_MAX_REQUEST_BYTES + 1)
    if len(body) > PEER_MESSAGE_MAX_REQUEST_BYTES:
        return None, JsonResponse({"error": "too_large"}, status=413)
    try:
        data = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    if not isinstance(data, dict):
        return None, JsonResponse({"error": "invalid_payload"}, status=400)
    return data, None
```

  - In `message_receive`, replace `:273-285` (from the `request.read()` comment to the `isinstance(data, dict)` check) by:

```python
    data, error_response = await asyncio.to_thread(_read_message_body, request)
    if error_response is not None:
        return error_response
```

- [ ] **Step 9: Read APIs and summary readers.**
  - `src/twicc/peer/owner_views.py`: imports `import asyncio`, `from django.db import close_old_connections`, `from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse`. Replace `_load_message` (`:40-51`) by:

```python
async def _load_message(pk):
    """A summary row: ``payload`` deferred, text annotated (phase 2 design §4.8.4)."""
    message = await sync_to_async(
        lambda: peer_messages.peer_message_summary_queryset().filter(pk=pk).first()
    )()
    if message is None:
        raise Http404("Peer message not found")
    return message


def _full_detail_body(pk) -> bytes | None:
    """The detail with its attachment bytes, loaded and serialized off the event loop (up to ~67 MB)."""
    from twicc.core.models import PeerMessage

    close_old_connections()
    try:
        message = peer_messages.peer_message_full_queryset().filter(pk=pk).first()
        if message is None:
            return None
        effective_project = peer_messages.peer_message_projects_map(
            PeerMessage.objects.filter(peer_id=message.peer_id),
        ).get(message.pk)
        return orjson.dumps(serialize_peer_message(
            message, include_payload=True, include_attachments=True, effective_project=effective_project,
        ))
    finally:
        close_old_connections()


def _attachments_body(pk) -> bytes | None:
    """``{"attachments": [...]}`` of a message, loaded and serialized off the event loop."""
    from twicc.core.models import PeerMessage

    close_old_connections()
    try:
        rows = list(PeerMessage.objects.filter(pk=pk).values_list("payload", flat=True)[:1])
    finally:
        close_old_connections()
    if not rows:
        return None
    payload = rows[0] if isinstance(rows[0], dict) else {}
    return orjson.dumps({"attachments": payload.get("attachments") or []})
```

    - `peer_message_send` parent re-read (`:219-225`): `peer_messages.peer_message_summary_queryset().filter(peer_id=result.peer_id, direction=PeerMessageDirection.IN, message_id=reply_to).first()`.
    - `peer_messages_list._fetch`: the search branch `selected = peer_messages.peer_message_summary_queryset().filter(pk__in=ordered_ids)` (`:315-317`); the normal branch `rows = peer_messages.peer_message_summary_queryset(rows)` (`:323-325`).
    - Replace `peer_message_detail` and `peer_message_attachments` (`:384-415`) by:

```python
async def peer_message_detail(request, pk):
    """GET /api/peer-messages/<pk>/ — message detail, optionally without attachment bytes."""
    from twicc.core.models import PeerMessage

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.GET.get("include_attachments") != "0":
        body = await asyncio.to_thread(_full_detail_body, pk)
        if body is None:
            raise Http404("Peer message not found")
        return HttpResponse(body, content_type="application/json")
    message = await _load_message(pk)
    # The effective project may be inherited from the rest of the thread:
    # resolved over the peer's rows, which hold the whole thread.
    effective_project = await sync_to_async(
        lambda: peer_messages.peer_message_projects_map(
            PeerMessage.objects.filter(peer_id=message.peer_id),
        ).get(message.pk)
    )()
    return JsonResponse(serialize_peer_message(
        message, include_payload=True, include_attachments=False, effective_project=effective_project,
    ))


async def peer_message_attachments(request, pk):
    """GET /api/peer-messages/<pk>/attachments/ — ``{"attachments": [{name, media_type, data}]}``."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    body = await asyncio.to_thread(_attachments_body, pk)
    if body is None:
        raise Http404("Peer message not found")
    return HttpResponse(body, content_type="application/json")
```

  - `src/twicc/core/serializers.py`: add before `serialize_peer_message`:

```python
def peer_message_text(message) -> str:
    """The text of a peer message.

    A summary row carries it as the ``payload_text`` annotation and its ``payload`` is deferred
    (``peer_messages.peer_message_summary_queryset``): ``payload`` is never touched then.
    """
    if hasattr(message, "payload_text"):
        return message.payload_text or ""
    return (message.payload or {}).get("text", "") or ""
```

    In `serialize_peer_message`: `text = peer_message_text(message)` (`:635`), and the payload block (`:695-701`) becomes:

```python
    if include_payload:
        # Only a full row (``peer_message_full_queryset``) reaches the bytes branch.
        data["payload"] = (message.payload or {}) if include_attachments else {
            "text": text,
            "attachments": [],
        }
```

    Update the docstring's first paragraph: "Summary form by default: base64 entries must never transit the channel layer, and a summary row has its payload deferred."
  - `src/twicc/asgi.py:850-865`: import `peer_message_summary_queryset` with `peer_message_projects_map`, and build the rows with

```python
                rows = peer_message_summary_queryset(
                    PeerMessage.objects.exclude(peer__state=PeerState.REVOKED),
                )
```

  - `src/twicc/cli/peer_message.py:26-35`:

```python
    from twicc.cli._output import emit_error, emit_json
    from twicc.core.models import PeerMessage, PeerMessageDirection
    from twicc.core.serializers import serialize_peer_message
    from twicc.core.services.peer_messages import peer_message_summary_queryset

    message = peer_message_summary_queryset(
        PeerMessage.objects.filter(direction=PeerMessageDirection.OUT, message_id=message_id),
    ).first()
```

- [ ] **Step 10: Purge.** Replace `purge_expired_attachment_bytes` in `src/twicc/peer_purge_task.py:50-74` by:

```python
def purge_expired_attachment_bytes(now: datetime | None = None) -> int:
    """Drop the attachment bytes of messages resolved before the retention window.

    Removes the ``attachments`` key (an absent key means no attachment for every
    reader), keeps ``text`` and ``attachments_meta`` (names and sizes survive), and
    stamps ``purged_at``. Only rows with the key are candidates, so a text-only or an
    already purged row is never selected again. The rows are loaded one at a time:
    each can hold about 67 MB of base64. Returns the number of rows purged.
    """
    from twicc.core.models import PeerMessage

    now = now or datetime.now(tz=UTC)
    cutoff = now - PEER_ATTACHMENT_RETENTION
    candidates = list(
        PeerMessage.objects
        .filter(resolved_at__lt=cutoff, purged_at__isnull=True, payload__has_key="attachments")
        .values_list("pk", flat=True)
    )
    purged = 0
    for pk in candidates:
        message = PeerMessage.objects.filter(pk=pk).only("pk", "payload", "purged_at").first()
        if message is None or not isinstance(message.payload, dict) or "attachments" not in message.payload:
            continue
        message.payload = {key: value for key, value in message.payload.items() if key != "attachments"}
        message.purged_at = now
        message.save(update_fields=["payload", "purged_at"])
        purged += 1
    if purged:
        logger.info("Peer purge: dropped attachment bytes from %d message(s)", purged)
    return purged
```

- [ ] **Step 11: Migration.** Create `src/twicc/core/migrations/0153_peer_message_attachments.py`:

```python
"""Convert stored peer messages to the attachment entries shape (phase 2 design D17, §4.8.5).

A row of the old shape (its payload has ``images`` / ``documents``, or an ``attachments_meta``
row has ``kind``) gets ``payload.attachments`` (built from the SDK blocks, set only when not
empty) and ``attachments_meta`` rows ``{name, media_type, bytes}``; ``images`` and ``documents``
are removed and ``text`` is never touched. The rules of ``inline.entries_from_legacy_blocks``
and of the receive-time sanitation are copied here: a migration never imports app code. The
name bound is a fixed 255 bytes; the composer normalizes again at delivery. The rows are read
one at a time (SQLite has no server-side cursor). The old shape is not rebuilt on reverse.
"""

import base64
import binascii
import mimetypes
import re

from django.db import migrations

NAME_MAX_BYTES = 255
MEDIA_TYPE_MAX_CHARS = 255
OCTET_STREAM = "application/octet-stream"
TEXT_PLAIN = "text/plain"
FALLBACK_NAME = "attachment"
TEMP_FILE_PREFIX = ".twicc-upload-"
LEGACY_KEYS = ("images", "documents")
FORBIDDEN_CHARS = re.compile("[\x00-\x1f\x7f\x85  /\\\\]")
MEDIA_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*")


def _truncate(name, max_bytes):
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    ext_bytes = len(ext.encode("utf-8"))
    if ext_bytes >= max_bytes:
        stem, ext = name, ""
        budget = max_bytes
    else:
        budget = max_bytes - ext_bytes
    return stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + ext


def _normalize_name(name):
    result = FORBIDDEN_CHARS.sub("_", name.strip())
    if result.startswith(TEMP_FILE_PREFIX):
        result = "_" + result
    result = _truncate(result, NAME_MAX_BYTES).strip()
    return FALLBACK_NAME if result in ("", ".", "..") else result


def _media_type(value):
    if isinstance(value, str) and len(value) <= MEDIA_TYPE_MAX_CHARS and MEDIA_TYPE.fullmatch(value):
        return value
    return OCTET_STREAM


def _default_name(media_type, n):
    base = media_type.split(";", 1)[0].strip().lower() if isinstance(media_type, str) else ""
    return f"attachment-{n}{(mimetypes.guess_extension(base) if base else None) or '.bin'}"


def _decoded_size(data):
    padding = len(data) - len(data.rstrip("="))
    if len(data) % 4 == 0 and padding <= 2:
        return len(data) // 4 * 3 - padding
    try:
        return len(base64.b64decode(data))
    except (binascii.Error, ValueError):
        return 0


def _blocks(payload):
    blocks = []
    for key in LEGACY_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            blocks.extend(value)
    return blocks


def _entries(payload):
    """The sanitized wire entries of the legacy blocks, images first, in order."""
    entries = []
    for n, block in enumerate(_blocks(payload), start=1):
        block = block if isinstance(block, dict) else {}
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        is_text = source.get("type") == "text"
        media_type = source.get("media_type") or (TEXT_PLAIN if is_text else OCTET_STREAM)
        if not isinstance(media_type, str):
            media_type = OCTET_STREAM
        data = source.get("data") or ""
        if is_text:
            data = base64.b64encode(str(data).encode("utf-8")).decode("ascii")
        elif not isinstance(data, str):
            data = ""
        title = block.get("title") or block.get("name")
        name = title if isinstance(title, str) and title else _default_name(media_type, n)
        entries.append({"name": _normalize_name(name), "media_type": _media_type(media_type), "data": data})
    return entries


def _meta_from_old_rows(rows):
    meta = []
    for n, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        media_type = row.get("media_type") or OCTET_STREAM
        name = row.get("name")
        if not (isinstance(name, str) and name):
            name = _default_name(media_type, n)
        size = row.get("bytes")
        meta.append({
            "name": _normalize_name(name),
            "media_type": _media_type(media_type),
            "bytes": size if isinstance(size, int) and not isinstance(size, bool) else 0,
        })
    return meta


def _is_old_shape(payload, meta):
    if isinstance(payload, dict) and any(key in payload for key in LEGACY_KEYS):
        return True
    return isinstance(meta, list) and any(isinstance(row, dict) and "kind" in row for row in meta)


def convert_peer_messages(apps, schema_editor):
    PeerMessage = apps.get_model("core", "PeerMessage")
    for pk in list(PeerMessage.objects.values_list("pk", flat=True)):
        row = PeerMessage.objects.filter(pk=pk).only("pk", "payload", "attachments_meta").first()
        if row is None or not _is_old_shape(row.payload, row.attachments_meta):
            continue
        payload = dict(row.payload) if isinstance(row.payload, dict) else {}
        entries = _entries(payload)
        if entries:
            meta = [
                {"name": entry["name"], "media_type": entry["media_type"], "bytes": _decoded_size(entry["data"])}
                for entry in entries
            ]
        else:
            meta = _meta_from_old_rows(row.attachments_meta)
        for key in LEGACY_KEYS:
            payload.pop(key, None)
        if entries:
            payload["attachments"] = entries
        row.payload = payload
        row.attachments_meta = meta
        row.save(update_fields=["payload", "attachments_meta"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0152_async_question_state"),
    ]

    operations = [
        migrations.RunPython(convert_peer_messages, migrations.RunPython.noop),
    ]
```

  Then delete `tests/test_async_question_migration_graph.py` (user decision): it was a one-time test of the `0151`/`0152` merge history, its assertion `leaf_nodes("core") == [("core", "0152_async_question_state")]` fails as soon as `0153` exists, and its other checks only exercise ordinary Django migration application.

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git rm tests/test_async_question_migration_graph.py`
Expected: `rm 'tests/test_async_question_migration_graph.py'`; the deletion is staged for the Step 15 commit. No later step runs this file.

- [ ] **Step 12: Model comments, watcher route, legacy module.**
  - `src/twicc/core/models.py:2025-2036`: "the payload is strictly the provider-common SDK block shape `{text, images, documents}` that the delivery composer re-walks" becomes "the payload is strictly `{text, attachments?}` (the wire entries the delivery composer turns into files)"; the `payload` comment becomes `# {text: str, attachments?: [{name, media_type, data}]} — the peer wire entries, base64 data (phase 2 design §4.8.1). No key when there is no attachment.`; the `attachments_meta` comment becomes `# Computed at row creation: [{name, media_type, bytes}], one row per entry, in order; survives the purge.` No migration is needed for comments (`makemigrations --check` stays clean).
  - `src/twicc/drop_requests_watcher.py:190-194`:

```python
    "peer:send": (
        "twicc.core.services.peer_messages",
        "send_peer_message_from_drop_payload",
        "sent",
    ),
    "peer:send_attachments": (
        "twicc.core.services.peer_messages",
        "send_peer_attachments_from_drop_payload",
        "sent",
    ),
```

  - `src/twicc/cli/_drop_request/__init__.py:9`: in the package docstring, "attachment validation+encoding," becomes "attachment resolution and staging," (the module that did the validation and encoding is deleted below; `attach_sources.py` now resolves and stages).
  - Delete `src/twicc/cli/_drop_request/attachments.py`: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git rm src/twicc/cli/_drop_request/attachments.py` (the deletion is staged for the Step 15 commit). Check that nothing imports it:

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && grep -rn "_drop_request.attachments\|_drop_request import attachments\|validate_and_encode\|_sniff_mime\|AttachmentResizeError" src tests`
Expected: exactly one line, the Task 5 guard `tests/test_remote_attach.py:…:    assert not hasattr(_remote, "_sniff_mime")`. Any other line (in `src/` in particular, `core/models.py:2033` included) is a leftover to fix.

- [ ] **Step 13: Frontend.** In `frontend/src/utils/peerMessageContent.js`:
  - Add at the top: `import { attachmentKindIcon } from './attachmentStrip.js'` and `import { formatAttachmentSize, getDisplayKind } from './composerAttachments.js'`.
  - Replace `mergePeerAttachments`, `attachmentBlocks`, `extensionForMediaType`, `peerBlockToFile` and `addPeerAttachmentsToDraft` (`:27-100`) by:

```js
export function mergePeerAttachments(detail, attachments) {
    return {
        ...detail,
        payload: {
            ...(detail?.payload || {}),
            attachments: Array.isArray(attachments?.attachments) ? attachments.attachments : [],
        },
    }
}

function attachmentEntries(payload) {
    return Array.isArray(payload?.attachments) ? payload.attachments : []
}

function base64DecodedSize(data) {
    const padding = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0
    return Math.max(0, Math.floor(data.length / 4) * 3 - padding)
}

/**
 * A `File` from one peer attachment entry `{name, media_type, data}`, for the
 * composer's attachment pipeline: its real name and media type. Null for an
 * entry without name or data.
 *
 * @param {{name: string, media_type: string, data: string}} entry
 * @returns {File|null}
 */
export function peerEntryToFile(entry) {
    if (typeof entry?.data !== 'string' || typeof entry?.name !== 'string' || !entry.name) return null
    const type = typeof entry.media_type === 'string' && entry.media_type ? entry.media_type : 'application/octet-stream'
    const bytes = Uint8Array.from(atob(entry.data), c => c.charCodeAt(0))
    return new File([bytes], entry.name, { type })
}

/**
 * One review-dialog preview row for `MediaThumbnailGroup`, like a ready composer
 * chip: an image thumbnail for an `image/*` entry, the kind icon for the others.
 *
 * @param {{name: string, media_type: string, data: string}} entry
 * @param {number} index - position of the entry in the message
 * @returns {object|null}
 */
export function peerEntryToMediaItem(entry, index) {
    if (typeof entry?.data !== 'string') return null
    const name = typeof entry.name === 'string' && entry.name ? entry.name : `attachment-${index + 1}`
    const mediaType = typeof entry.media_type === 'string' ? entry.media_type : ''
    const kind = getDisplayKind({ name, type: mediaType })
    const size = base64DecodedSize(entry.data)
    const image = kind === 'image' && mediaType.toLowerCase().startsWith('image/')
    return {
        id: `peer-attachment-${index}`,
        name,
        size,
        sizeLabel: formatAttachmentSize(size),
        kind,
        state: 'ready',
        progress: 100,
        retryable: false,
        statusText: '',
        icon: attachmentKindIcon(kind),
        type: image ? 'image' : kind === 'PDF' ? 'pdf' : 'other',
        src: image ? `data:${mediaType};base64,${entry.data}` : null,
        textUrl: null,
    }
}

export async function addPeerAttachmentsToDraft(payload, entryToFile, addAttachment) {
    for (const entry of attachmentEntries(payload)) {
        try {
            const file = entryToFile(entry)
            if (!file) throw new Error('Invalid Peer attachment')
            await addAttachment(file)
        } catch {
            return 'TwiCC could not add all attachments to the draft. '
                + 'The Peer message is still available for delivery to another session.'
        }
    }
    return ''
}
```

  In `frontend/src/components/peer/PeerMessageReviewDialog.vue`:
  - Delete `import { sdkBlockToMediaItem } from '../../utils/fileUtils'` (`:29`); in the `peerMessageContent` import (`:30-40`) replace `peerBlockToFile` by `peerEntryToFile` and add `peerEntryToMediaItem`.
  - `:104`: `const loadedAttachments = ref({ attachments: [] })`; `:819`: `loadedAttachments.value = { attachments: [] }`.
  - `mediaItems` (`:349-356`):

```js
const mediaItems = computed(() => {
    if (attachmentsState.value !== 'ready') return []
    const entries = detail.value?.payload?.attachments
    if (!Array.isArray(entries)) return []
    return entries.map(peerEntryToMediaItem).filter(Boolean)
})
```

  - `summaryShell` (`:644`): `payload: { text: '', attachments: [] },`.
  - `addPeerAttachments` (`:1035-1050`): pass `peerEntryToFile` instead of `peerBlockToFile`; the comment becomes "Add the peer attachments to a composer's draft, one by one, in message order, through the composer attachment pipeline: every entry becomes a File with its real name, whatever the target provider (the server decides at send how each file is sent). Returns the records actually added so a failed delivery can release exactly those."

- [ ] **Step 14: Run and see them pass, plus regressions and the build.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_attachments.py tests/test_peer_message_attachments_migration.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_updates_consumer.py tests/test_peer_external_notifications.py tests/test_peer_handshake.py tests/test_peer_threading_migration.py tests/test_peer_revocation_migration.py tests/test_mcp_attach.py tests/test_mcp_tools.py tests/test_twicc_peer_message_skill.py tests/test_remote_attach.py -q && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test && uvx ruff check src/twicc/cli/peer_send.py src/twicc/cli/_drop_request/help_strings.py src/twicc/core/services/peer_messages.py src/twicc/peer/outbound.py src/twicc/peer/inbound_views.py src/twicc/peer/owner_views.py src/twicc/core/serializers.py src/twicc/cli/peer_message.py src/twicc/peer_purge_task.py src/twicc/core/migrations/0153_peer_message_attachments.py src/twicc/asgi.py src/twicc/core/models.py src/twicc/drop_requests_watcher.py src/twicc/cli/_drop_request/__init__.py tests/test_peer_attachments.py tests/test_peer_message_attachments_migration.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_updates_consumer.py tests/test_mcp_attach.py && cd frontend && npm test && npm run build`
Expected: PASS; `makemigrations --check` reports no change (only comments changed in `models.py`); `npm run build` succeeds. The lint list is every Python file of the commit. For the existing ones, compare `uvx ruff check <file>` before and after the edit: no new finding (all are clean at `f6e4b342`).

- [ ] **Step 15: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git status --short tests/test_async_question_migration_graph.py src/twicc/cli/_drop_request/attachments.py && git add src/twicc/cli/peer_send.py src/twicc/cli/_drop_request/help_strings.py src/twicc/core/services/peer_messages.py src/twicc/peer/outbound.py src/twicc/peer/inbound_views.py src/twicc/peer/owner_views.py src/twicc/core/serializers.py src/twicc/asgi.py src/twicc/cli/peer_message.py src/twicc/peer_purge_task.py src/twicc/core/models.py src/twicc/drop_requests_watcher.py src/twicc/cli/_drop_request/__init__.py src/twicc/core/migrations/0153_peer_message_attachments.py frontend/src/utils/peerMessageContent.js frontend/src/utils/peerMessageContent.test.js frontend/src/components/peer/PeerMessageReviewDialog.vue tests/test_peer_attachments.py tests/test_peer_message_attachments_migration.py tests/test_peer_messages.py tests/test_peer_cli.py tests/test_peer_updates_consumer.py tests/test_mcp_attach.py && git commit -F /tmp/phase2-task6.msg
```

The `git status` line must show both files as `D ` (staged deletions, by the `git rm` of Steps 11 and 12); otherwise run those `git rm` first.

```
feat(peer): send any file to a peer with its name

peer-send stages its files and uses a new peer:send_attachments drop kind,
so an older backend refuses it instead of dropping the files; it mints the
message id and prints it on every outcome of a submitted send, and waits
up to 468 s when the message carries files. The wire carries attachments
entries {name, media_type, data} with a 50 MB total and a 72 MB body; the
sender checks both before the POST and sizes its write timeout on the
body. The receiver validates and sanitizes entries off the event loop,
still converts the blocks of an older sender, and stores no images or
documents. The review dialog adds files with their real names to the
composer.

Summary readers defer the payload and read the annotated text, the purge
handles one row at a time, and migration 0153 converts stored rows. The
legacy CLI attachment module is deleted. The one-time migration graph
test of the 0151/0152 merge is deleted: its leaf assertion breaks at
every new migration.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

Remind the user at the end of the task: migration `0153` is new; `devctl.py start` applies it at backend start (never run `migrate` by hand).

---

### Task 7 (spec T8): Docs, skills, plugin version, stale comments

Read `src/twicc/agent/plugin/README.md` first (section order, one sentence per item, error descriptions only when they add information, each skill self-contained: never point to another skill's file). Every replacement below is exact: use the Edit tool with the quoted old text.

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md:43`, `:45`, `:76`, `:86`, `:100-107`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md:4`, `:35`, `:43`, `:70`, `:74`, `:93-98`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-create-session/session-behavior.md:8`, `:47-49`, `:51-64`; `src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md:46`, `:61-68`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md:45-46`, `:50-57`, `:61-66`, `:70-73`, `:77-84`, `:88-93`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md:47`, `:62`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json:4`
- Modify: `SKILLS-AND-CLI.md:56`, `:58`, `:59`, `:285`, `:294`, `:299`, `:353`, `:357`
- Modify: `RPC-API.md:78-88`, `:90-106`, `:136-138`
- Modify: `frontend/public/help/external-mcp.md:21-33` (new subsection after it), `:225`
- Modify: `frontend/public/help/peers.md:54-58` (after), `:84-86`
- Modify: `CLAUDE.md:80`, `AGENTS.md:81`
- Modify: `src/twicc/providers/helpers.py:100`, `:106-107`, `:767`; `src/twicc/cli/_batch_runner.py:71`, `:214` (stale comments)

**Interfaces:** documentation only. Consumes the codes and limits of Tasks 1–6: `attachments_too_large`, `message_too_large`, `invalid_message_id`, `attachment_stage_failed`, `attachments_with_command`, `attachment_missing`, `attachment_not_ready`, `attachment_commit_failed`, `invalid_attachments`, `invalid_data_uri`, `not_a_file`, `relative_path`, `remote_requires_remote`; 50 MB / 72 MB; 468 s.

- [ ] **Step 1: `twicc-send-message/SKILL.md`.**
  - Replace the `--attach` bullet (`:43`) by:

```markdown
- `--attach VALUE` (repeatable) — attach a file of any type. `VALUE` is a local path, a base64 data URI `data:<mime>;name=<percent-encoded file name>;base64,<data>` (`name=` is optional; without it the file is named `attachment-<n>.<ext>`), or, over `--remote`, `remote:<absolute path>` for a file on the remote server. TwiCC decides per file how the agent gets it: natively (images; PDF and text on Claude Code) or as a file in the session's `attachments/` artifacts folder. Inline data (data URIs, and local files sent over `--remote`) is limited to 50 MB in total per call; a file read from disk has no limit. On a local command line, Linux caps one argument at 128 KiB, so pass a larger local file by its path, not as a data URI. For a larger file, put it on a file storage service and pass its URL in the message text, or pass a path the server reads: `remote:<absolute path>` over `--remote`, an absolute server path over MCP.
```

  - Replace the `--timeout` bullet (`:45`) by:

```markdown
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). A send waits in the session's queue behind any send in progress. If the CLI times out (exit `5`, or exit `7` over `--remote`), the message may still get delivered: read the session (`$TWICC session <ID> messages --tail 1`) before sending again.
```

  - In "### Local (exit 1)", after `invalid_value` (`:76`), add:

```markdown
- `not_a_file` / `relative_path` / `remote_requires_remote` / `invalid_data_uri` — a bad `--attach` value: a missing file or a directory, a relative path over MCP or the RPC, `remote:` without `--remote`, a malformed data URI.
- `attachments_too_large` — more than 50 MB of inline data; the message says what to do instead.
- `attachment_stage_failed` — the copy of a file failed (disk full, permission).
```

  - In "### Server (exit 3)", after `manager_busy` (`:86`), add:

```markdown
- `attachment_missing` / `attachment_not_ready` / `attachment_commit_failed` — a file could not be delivered; send again.
- `attachments_with_command` — files cannot ride a command: a message starting with `/` or `!` to a hybrid session, or a Codex built-in command.
- `invalid_attachments` — the request came from an older `twicc` CLI; update it.
```

  - In "### Exit codes" (`:102-107`), add after the `5` line:

```markdown
- `7` — `--remote` only: transport failure (unreachable, rejected token, timeout); the message may still get delivered.
```

- [ ] **Step 2: `twicc-send-messages/SKILL.md`.**
  - In the `argument-hint` (`:4`) and the usage line (`:35`), replace `[--attach PATH...]` by `[--attach VALUE...]`.
  - Replace the `--attach` bullet (`:43`) by:

```markdown
- `--attach VALUE` (repeatable) — attach a file of any type to every message; one copy is staged per recipient, and no provider refuses a file. `VALUE` is a local path, a base64 data URI `data:<mime>;name=<percent-encoded file name>;base64,<data>` (`name=` optional), or, over `--remote`, `remote:<absolute path>` for a file on the remote server. Inline data (data URIs, and local files sent over `--remote`) is limited to 50 MB in total per call; a file read from disk has no limit. On a local command line, Linux caps one argument at 128 KiB, so pass a larger local file by its path, not as a data URI. For a larger file, put it on a file storage service and pass its URL in the message text, or pass a path the server reads: `remote:<absolute path>` over `--remote`, an absolute server path over MCP.
```

  - After the "Argument-level problems…" paragraph (`:70`), add the paragraph:

```markdown
A bad `--attach` value fails the whole command too, before any recipient, as one JSON `validation_error` (exit 1): `not_a_file`, `relative_path`, `remote_requires_remote`, `invalid_data_uri`, or `attachments_too_large` (more than 50 MB of inline data; the message says what to do instead: a file storage service URL in the text, or a path the server reads).
```

  - Replace the "Per-session problems…" paragraph (`:74`) by:

```markdown
Per-session problems never fail the batch — reported in `results[<id>]` with `status` `validation_error` (local lookup: `session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`; or `attachment_stage_failed`, the copy of the files for that recipient failed) or `rejected` (server: `awaiting_user_input` — the session has a pending UI dialog a CLI message can't unblock; `manager_busy` — transient, retry; `provider_disabled`; `attachment_missing` / `attachment_not_ready` / `attachment_commit_failed` — send again; `attachments_with_command` — files cannot ride a command; `invalid_attachments` — older `twicc` CLI; update it). Same vocabulary as `twicc-send-message`. A per-id `timeout` (or exit `7` over `--remote`) does not prove the send failed: read that session before sending again.
```

  - In "### Exit codes" (`:93-98`), add after the `6` line (`:98`):

```markdown
- `7` — `--remote` only: transport failure (unreachable, rejected token, timeout); the messages may still get delivered.
```

- [ ] **Step 3: `twicc-create-session`.**
  - `session-behavior.md`: in the usage line (`:8`), replace `[--attach PATH]...` by `[--attach VALUE]...`.
  - `session-behavior.md`: replace the `### Attachments` bullet (`:49`) by:

```markdown
- `--attach VALUE` (repeatable) — a file of any type: a local path, a base64 data URI `data:<mime>;name=<percent-encoded file name>;base64,<data>` (`name=` optional), or, over `--remote`, `remote:<absolute path>` for a file on the remote server. TwiCC decides per file how the agent gets it: natively (images; PDF and text on Claude Code) or as a file in the session's `attachments/` artifacts folder. Inline data (data URIs, and local files sent over `--remote`) is limited to 50 MB in total per call; a file read from disk has no limit. On a local command line, Linux caps one argument at 128 KiB, so pass a larger local file by its path, not as a data URI. For a larger file, put it on a file storage service and pass its URL in the prompt, or pass a path the server reads: `remote:<absolute path>` over `--remote`, an absolute server path over MCP.
```

  - In its "## Errors", "Local (exit 1)" list, append:

```markdown
- `not_a_file` / `relative_path` / `remote_requires_remote` / `invalid_data_uri` — a bad `--attach` value.
- `attachments_too_large` — more than 50 MB of inline data; the message says what to do instead.
- `attachment_stage_failed` — the copy of a file failed (disk full, permission).
```

    and in "Server (exit 3)" append:

```markdown
- `attachment_missing` / `attachment_not_ready` / `attachment_commit_failed` — a file could not be delivered; create the session again.
- `attachments_with_command` — files cannot ride a Codex built-in command.
- `invalid_attachments` — the request came from an older `twicc` CLI; update it.
```

  - `SKILL.md:46`: replace "If the CLI times out, the session may still get created." by "If the CLI times out (exit `5`, or exit `7` over `--remote`), the session may still get created: check `$TWICC sessions --limit 5` before creating it again."
  - `SKILL.md` "### Exit codes" (`:61-68`): add after the `5` line (`:68`):

```markdown
- `7` — `--remote` only: transport failure (unreachable, rejected token, timeout); the session may still get created.
```

- [ ] **Step 4: `twicc-peer-send/SKILL.md`.**
  - Replace `:45-46` by:

```markdown
- `--attach VALUE` (repeatable) — attach a file of any type: a local path, a base64 data URI `data:<mime>;name=<percent-encoded file name>;base64,<data>` (`name=` optional), or, over `--remote`, `remote:<absolute path>`. The receiver gets each file with its name, in order. Every file travels inline to the peer, paths included, so all files together are limited to 50 MB. On a local command line, Linux caps one argument at 128 KiB, so pass a larger local file by its path, not as a data URI. For a larger file, put it on a file storage service and pass its URL in the message text.
- `--timeout SECONDS` — seconds to wait for the server's response: 30 by default, 468 when the message carries files (a 50 MB send can take about 5 minutes).

### Sending files

Prefer the MCP `peer_send` tool, with one message with large files per tool call: a tool call times out after 10 minutes. From a shell, give the call a timeout above 8 minutes.
```

  - In "### Local (exit 1)" (`:50-57`), append:

```markdown
- `not_a_file` / `relative_path` / `remote_requires_remote` / `invalid_data_uri` — a bad `--attach` value.
- `attachments_too_large` — more than 50 MB of files in total; put a larger file on a file storage service and pass its URL in the message text.
- `attachment_stage_failed` — the copy of a file failed (disk full, permission).
```

  - Under the "### Server (exit 3)" heading (`:59`, kept), replace the items and the closing sentence (`:61-66`) by:

```markdown
- `not_found` / `peer_broken` / `not_active` — same conditions, re-checked server-side.
- `invalid_reply_to` / `unknown_reply_to` — the reply id is malformed or does not exist for this peer, re-checked server-side.
- `attachments_too_large` / `message_too_large` — the files (50 MB) or the whole encoded message (72 MB) are too large; put a larger file on a file storage service and pass its URL in the message text.
- `invalid_message_id` — the message id is malformed or already used for this peer.
- `attachment_missing` / `attachment_not_ready` — a staged file vanished; send again.
- `invalid_attachments` — the request came from an older `twicc` CLI; update it.
- `unreachable` — the peer instance could not be reached, or did not answer in time; a large message may still have been stored.
- `send_failed` — the peer answered with an error. An older peer refuses files: its user must update TwiCC.

Every server-side failure surfaces as `rejected` (exit 3); the distinction is in the error `code`.

**Exit 5, exit 4, and exit 3 with `unreachable` or `send_failed` do not prove that the message was not sent.** Every output of a submitted send (exit 0, 3, 4, 5) carries the `message_id`: run `$TWICC peer-message <message_id>` before you send again. If the TwiCC backend restarts during a send, the message stays `pending`, and that status does not prove the send completed. Exit 7 (`--remote`), or a CLI killed by its shell, gives no id: check the Peers outbox in the TwiCC UI, or report to your user; never send again blindly.
```

  - Output format (`:70-73`): add after the `rejected` line:

```json
{"status": "rejected", "errors": [{"field": "peer", "code": "unreachable", "message": "..."}], "request_uuid": "...", "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4"}
{"status": "timeout", "received_seen": true, "message": "...", "request_uuid": "...", "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4"}
```

    and delete the older `rejected` example line (`:72`) it replaces.
  - Exit codes: add after the `5` line:

```markdown
- `7` — `--remote` only: transport failure; the message may still have been sent (see above).
```
  - Examples: add `$TWICC peer-send David --attach /home/twidi/fix.patch 'Patch for the login bug' 'The attached patch fixes the login redirect; apply it with git am.'`.

- [ ] **Step 5: `twicc-peer-message/SKILL.md`.**
  - `:47` becomes ` "attachments_meta": [{"name": "screenshot.png", "media_type": "image/png", "bytes": 48211}],`.
  - After the `origin_session` / `delivered_to_session` bullet (`:62`), add:

```markdown
- `attachments_meta` — one row per attached file, in order: `name`, `media_type`, `bytes`. It survives the purge of the bytes.
```

- [ ] **Step 6: Plugin version.** `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: `"version": "0.107.3"` → `"version": "0.108.0"` (minor: a new input form, `name=`, and lifted limits).

- [ ] **Step 7: `SKILLS-AND-CLI.md`.**
  - `:56` (Outcome): append "Exit `7` does not prove that a send failed: check the session (for `peer-send`, the Peers outbox) before sending again."
  - Replace `:58` (Files) by:

```markdown
- **Files.** `--attach <local file>` is read on the client and inlined as a base64 `data:` URI with `name=<file name>`, so a local (even relative) path works without a shared filesystem. Inline data is limited to 50 MB in total per call, checked before any HTTP call (exit `2`, with what to do instead), and the request body to 72 MB. For a larger file, use the `remote:` scheme below for a file already on the server (`peer-send`: no, its files all travel inline to the peer), or a file storage service and a URL in the message text. The read timeout is the command's `--timeout` plus a margin; a `peer-send` with files waits up to 468 s. Path arguments (`--project`, `--directory`) are resolved on the server and must be absolute (or, for `--project`, an id).
```

  - `:59` (`remote:` scheme): replace "and `--attach` (all three)" by "and `--attach` (all four: `create-session`, `send-message`, `send-messages`, `peer-send`)".
  - `:285` (create-session Metadata): replace "`--attach PATH` (repeatable; images/PDF/text up to 5 MB each, 100 files / 32 MB total; over `--remote`, prefix an absolute path with `remote:` to read it on the server)" by "`--attach VALUE` (repeatable; any file type; a local path, a `data:<mime>;name=<percent-encoded name>;base64,<data>` URI, or `remote:<absolute path>` over `--remote`; inline data limited to 50 MB per call, a file read from disk has no limit; for a larger file, a file storage service URL in the text, or a path the server reads: `remote:` over `--remote`, an absolute server path over MCP; errors `attachments_too_large`, `attachment_stage_failed`, then `attachment_missing` / `attachment_not_ready` / `attachment_commit_failed` / `attachments_with_command` server-side, and `invalid_attachments` from an older `twicc` CLI)".
  - `:294` (send-message): replace "`--attach PATH` (repeatable; over `--remote`, prefix an absolute path with `remote:` to read it on the server), plus `--timeout`." by "`--attach VALUE` (repeatable; same forms, limit and errors as `create-session`), plus `--timeout`. A send waits in the session's queue behind any send in progress; exit `5` (or `7` over `--remote`) does not prove the send failed."
  - `:299` (send-messages): replace "`--attach` is validated per session against its provider (a file one provider rejects becomes a per-id error)" by "`--attach` values are checked once (a bad value fails the whole command, exit `1`) and staged once per recipient; no provider refuses a file, a failed copy is a per-id `attachment_stage_failed`, and the server codes come back per id (`attachment_missing`, `attachment_not_ready`, `attachment_commit_failed`, `attachments_with_command`, `invalid_attachments`); a per-id `timeout` or exit `7` does not prove the send failed".
  - `:353` (peer-send): replace "`--attach` (repeatable) works like `send-message`; `--timeout` sets the wait." by "`--attach` (repeatable, any file type, received with its name; every file travels inline, `remote:` files included, so 50 MB in total; for a larger file, a file storage service and its URL in the message text); `--timeout` sets the wait (30 s, or 468 s when the message carries files)." and replace the last sentence by "Server failures land as `rejected` (exit 3) with the detail in the error code (`peer_broken`, `unreachable`, `send_failed`, `attachments_too_large`, `message_too_large`, `invalid_message_id`, `invalid_attachments` from an older `twicc` CLI). Every output of a submitted send (exit 0, 3, 4, 5) carries the `message_id`: exit `5`, `4`, or `3` with `unreachable` / `send_failed` may still have reached the peer, so check `peer-message <id>` before sending again. An older peer refuses files (HTTP 400 or 413): its user must update."
  - `:357` (peer-message): append "`attachments_meta` rows are `{name, media_type, bytes}`."

- [ ] **Step 8: `RPC-API.md`.**
  - Replace the section `### Attachments: absolute server path or base64 data URI` (`:78-88`, from its heading to the line "a file that isn't present on the server's filesystem.") by:

````markdown
### Attachments: absolute server path or base64 data URI

`attach` (the CLI's `--attach`) accepts any file type, as either an absolute path to
a file **on the server**, or an inline base64 data URI when the file only exists on
the client:

```
data:<media-type>;name=<percent-encoded file name>;base64,<base64-payload>
```

`name=` is optional and keeps the file name; without it the file is named
`attachment-<n>.<ext>`. Only the `base64` form is supported.

Inline data (the data URIs of one call) is limited to **50 MB** in total, and the
request body to **72 MB**; a larger body gets `413`. A file read from a server path
has no limit. For a larger file, put it on a file storage service and pass its URL
in the message text, or copy it to the server and pass its absolute path.
`peer-send` sends every file inline to the peer, server paths included: its 50 MB
covers all its files.
````

  - In "### Blocking waits can be cut short — mind the timeouts" (`:90-106`), add a paragraph after the first list (it ends at `:103`):

```markdown
A send (`send-message`, `send-messages`, `create-session`, `peer-send`) holds the
response until its final status, up to its `--timeout`: it may wait in the
session's queue behind another send, and a `peer-send` with files waits up to
468 s by default. A client-side timeout (exit `7` for `--remote`) or an exit `5`
does not prove that the send failed: check the session (for `peer-send`, the
Peers outbox in the UI) before sending again.
```

  - Replace the `--attach <local file>` bullet (`:136-138`) by:

```markdown
- **`--attach <local file>`** is read on the client and inlined as a base64
  `data:` URI with `name=<file name>`, so a local — even relative — path works
  without the file existing on the server. More than 50 MB of inline data, or a
  body above 72 MB, is refused before any HTTP call (exit `2`); the error says
  what to do instead (see "Attachments" above).
```

- [ ] **Step 9: Help pages.**
  - `frontend/public/help/external-mcp.md`: after the "### What a connection can do" section (`:21-33`), add:

```markdown
### Attachments

An external client attaches a file of its own machine with a base64 data URI in
`attach`: `data:<mime>;name=<percent-encoded file name>;base64,<data>`. `name=`
keeps the file name. Inline data is limited to 50 MiB per command. For a larger
file, put it on a file storage service and pass its URL in the message text, or
pass an absolute path to a file that is already on the TwiCC host. A peer message
sends every file inline, so only the URL works there.
```

    and replace `:225` ("The complete request retains the existing 48 MiB limit, including attachments.") by "The complete request is limited to 72 MiB, including attachments; each command accepts at most 50 MiB of inline attachment data (see Attachments above for a larger file)."
  - `frontend/public/help/peers.md`:
    - After the paragraph that ends "`localhost`, never serves peer traffic." (`:54-58`), add:

```markdown
A proxy or a tunnel in front of your peer address must accept request bodies of
72 MiB. One sized for the old 48 MiB limit refuses large messages.
```

    - Replace the paragraph `:84-86`, quoted with its real line breaks:

```markdown
An agent can list active peers and send a titled message to one of them. A
message can contain text and attachments. No extra sender-side confirmation is
required after the user has approved the peer relationship.
```

      by:

```markdown
An agent can list active peers and send a titled message to one of them. A
message can contain text and files of any type; each file keeps its name. All
files of one message are limited to 50 MiB in total; for a larger file, the
agent puts it on a file storage service and passes its URL in the message
text. No extra sender-side confirmation is required after the user has
approved the peer relationship.

A peer that runs an older TwiCC refuses a message with files: its user must
update. A message with text only still reaches it.
```

- [ ] **Step 10: `CLAUDE.md`, `AGENTS.md`, stale comment.**
  - `CLAUDE.md:80` and `AGENTS.md:81`: replace "`composer-attachments/` (staging of the composer's attached files, `<bucket>/<id>/`)" by "`composer-attachments/` (staging of attached files: the composer's, and one-shot entries of the CLI/API, `<bucket>/<id>/`)".
  - `src/twicc/providers/helpers.py:100`: the docstring's first line "Native delivery policy of web composer attachments, per provider." becomes "Native delivery policy of attachments, per provider, for every entry point."; `:767` (`get_attachment_policy` docstring): "Return the native-delivery policy of web composer attachments for this provider." becomes "Return the native-delivery policy of attachments for this provider."
  - `src/twicc/providers/helpers.py:106-107` (same docstring; "A ``None``" ends line 105 and stays), replace:

```
    byte limit means no limit. The legacy ``ATTACHMENT_SUPPORT`` keeps governing the
    CLI / MCP / peer attachment paths.
```

    by:

```
    byte limit means no limit. Every entry point plans with it (web composer, CLI, RPC,
    MCP); a peer message is planned by the receiving composer at delivery.
```

  - `src/twicc/cli/_batch_runner.py:71` (`run_batch` docstring), replace the line:

```
    its provider, or an attachment its provider rejects) — in that case the id
```

    by:

```
    its provider, or a staging failure of its attachments) — in that case the id
```

    and `:214`, the line `            # or an attachment its provider rejects).` by `            # or a staging failure of its attachments).`

- [ ] **Step 11: Check that no stale statement is left.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && grep -rn "5 MB\|32 MB\|100 files\|existing 48 MiB\|48 MB\|validated per session\|its provider rejects\|images only\|_drop_request/attachments\|keeps governing\|web composer attachments\|re-sniffs\|all three)\|attach PATH" src/twicc/agent/plugin SKILLS-AND-CLI.md RPC-API.md frontend/public/help CLAUDE.md AGENTS.md src/twicc/providers/helpers.py src/twicc/mcp src/twicc/peer src/twicc/core/services/peer_messages.py src/twicc/core/models.py src/twicc/cli`
Expected: no output. The pattern is `existing 48 MiB`, not `48 MiB`: the `peers.md` sentence that Step 9 adds ("One sized for the old 48 MiB limit refuses large messages.") is expected and must stay; no other text added by this plan matches the patterns (the "about 5 MB" comment of `inline.py` is outside the searched paths). At `f6e4b342` this grep lists 34 lines (4 of them only for `attach PATH`: the usage lines `session-behavior.md:8` and `twicc-send-messages/SKILL.md:4`, `:35`, and `SKILLS-AND-CLI.md:294`); Tasks 4–6 replace or delete the code ones (the help strings, `mcp/server.py:268`, `peer/inbound_views.py:28` and `:276`, `models.py:2033`, `_remote.py:344` and `:418`, the deleted `attachments.py`), and Steps 1–10 of this task the rest. Any line left is a missed edit: fix it, do not explain it away.

- [ ] **Step 12: Run the documentation tests and lint the touched Python files.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_twicc_peer_message_skill.py tests/test_twicc_share_skill.py tests/test_pending_question_documentation.py tests/test_session_wait_documentation.py tests/test_sessions_wait_reply_documentation.py tests/test_mcp_tools.py tests/test_wait_reply.py tests/test_send_messages_wait.py -q && uvx ruff check src/twicc/providers/helpers.py src/twicc/cli/_batch_runner.py`
Expected: PASS; no new lint finding (both files are clean at `f6e4b342`; compare before and after the edit).

- [ ] **Step 13: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/agent/plugin/twicc/skills/twicc-send-message/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-send-messages/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-create-session/session-behavior.md src/twicc/agent/plugin/twicc/skills/twicc-create-session/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json SKILLS-AND-CLI.md RPC-API.md frontend/public/help/external-mcp.md frontend/public/help/peers.md CLAUDE.md AGENTS.md src/twicc/providers/helpers.py src/twicc/cli/_batch_runner.py && git commit -F /tmp/phase2-task7.msg
```

```
docs(attachments): document any-file attachments for CLI, RPC, MCP and peers

The skills, SKILLS-AND-CLI.md, RPC-API.md and the help pages now describe
the --attach forms (path, data URI with name=, remote:), the 50 MB limit
of inline data with what to do for a larger file, the 72 MB request body,
the new error codes, and the advice for exit 5 and 7. The peer-send skill
covers the longer wait with files, the message id in every output of a
submitted send and older peers. The plugin version moves to 0.108.0.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 8 (spec T9): Retire the legacy attachment support

**Files:**
- Modify: `src/twicc/providers/helpers.py:35-39` (delete `MAX_IMAGE_DIMENSION` and its comment), `:751-764` (delete `get_attachment_support`), `:770-782` (delete `get_effective_image_dimension`), `:1166` (bootstrap key)
- Modify: `src/twicc/providers/claude_code/helpers.py:12` (`date` import), `:24` (`MAX_IMAGE_DIMENSION` import), `:122-132` (delete `ATTACHMENT_SUPPORT`), `:586-597` (`selected_model_supports_highres_images`), `:737-738` (method), `:743-771` (`get_effective_image_dimension`), `:773-802` (`_upgrade_retired_model`)
- Modify: `src/twicc/providers/claude_code/constants.py:31-33` (comment naming `MAX_IMAGE_DIMENSION`)
- Modify: `src/twicc/providers/codex/helpers.py:100-110`, `:391-392`
- Modify: `src/twicc/cli/_drop_request/bootstrap_local.py:30`, `:74`
- Modify: `tests/test_composer_attachment_planner.py:148-152`

**Interfaces:** consumes nothing new; removes `ATTACHMENT_SUPPORT`, `get_attachment_support`, `ProviderBootstrap.attachment_support` and the bootstrap key `attachment_support` (D20). No frontend reader exists (`grep -rn attachment_support frontend/src` is empty). Also removes the image-dimension helpers whose only caller was the deleted `cli/_drop_request/attachments.py:311` (see "Scope and Interpretation"): `MAX_IMAGE_DIMENSION`, `BaseProviderHelpers.get_effective_image_dimension`, `ClaudeCodeHelpers.get_effective_image_dimension`, `ClaudeCodeHelpers.selected_model_supports_highres_images`, `ClaudeCodeHelpers._upgrade_retired_model`. No backend test calls them (`grep -rn "get_effective_image_dimension\|_upgrade_retired_model\|selected_model_supports_highres_images" tests` is empty). The frontend `getEffectiveImageDimension` and `MAX_IMAGE_DIMENSION` of `fileUtils.js` stay (`resizeMediasForSend` uses them, called by the legacy retry of `frontend/src/components/session/detail/items/FailedSendBanner.vue:108`; the phase 1 composer normalizes Claude images on the backend at `CLAUDE_LONG_EDGE = 2000`, `core/services/attachments/images.py:44`); the model flag `supports_highres_images` stays (the frontend reads it).

- [ ] **Step 1: Update the test.** Replace `test_policy_is_exposed_by_the_provider_helpers_and_legacy_support_is_kept` (`tests/test_composer_attachment_planner.py:148-152`) by the two tests below. `get_bootstrap_data()` reads `UsageSnapshot` for every provider with a usage sync (`providers/helpers.py:1170-1180`), so the second test needs the database; the module has no `django_db` mark, so the test carries its own.

```python
def test_policy_is_exposed_by_the_provider_helpers_and_the_legacy_support_is_gone():
    from twicc.cli._drop_request.bootstrap_local import ProviderBootstrap
    from twicc.providers import helpers as base_helpers

    assert get_provider_helpers("claude_code").get_attachment_policy() is claude_helpers.ATTACHMENT_POLICY
    assert get_provider_helpers("codex").get_attachment_policy() is codex_helpers.ATTACHMENT_POLICY
    assert "attachment_support" not in ProviderBootstrap._fields
    for module in (claude_helpers, codex_helpers, base_helpers):
        assert not hasattr(module, "ATTACHMENT_SUPPORT")
        assert not hasattr(module, "MAX_IMAGE_DIMENSION")
    for provider in ("claude_code", "codex"):
        helpers = get_provider_helpers(provider)
        for name in (
            "get_attachment_support", "get_effective_image_dimension",
            "selected_model_supports_highres_images", "_upgrade_retired_model",
        ):
            assert not hasattr(helpers, name), (provider, name)


@pytest.mark.django_db
def test_the_bootstrap_data_has_no_attachment_support_key():
    for provider in ("claude_code", "codex"):
        assert "attachment_support" not in get_provider_helpers(provider).get_bootstrap_data()
```

- [ ] **Step 2: Run and see it fail.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_planner.py -q`
Expected: FAIL (`ATTACHMENT_SUPPORT` still defined; the bootstrap data still has the key).

- [ ] **Step 3: Delete the legacy support.**
  - `providers/helpers.py`: delete the method `get_attachment_support` (`:751-764`) and the line `"attachment_support": self.get_attachment_support(),` (`:1166`).
  - `providers/claude_code/helpers.py`: delete `ATTACHMENT_SUPPORT: dict = {…}` (`:122-132`) and the method `get_attachment_support` (`:737-738`).
  - `providers/codex/helpers.py`: delete `ATTACHMENT_SUPPORT: dict = {…}` (`:100-110`) and the method (`:391-392`).
  - `cli/_drop_request/bootstrap_local.py`: delete the field `attachment_support: dict` (`:30`) and `attachment_support=provider_data.get("attachment_support", {}),` (`:74`).

- [ ] **Step 4: Delete the dead image-dimension helpers.** First confirm they have no caller left: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && grep -rn "get_effective_image_dimension\|_upgrade_retired_model\|selected_model_supports_highres_images\|MAX_IMAGE_DIMENSION" src --include=*.py` must list exactly these 16 lines (numbers at `f6e4b342`; Task 7 and Step 3 shift some of them, so match by content): `providers/helpers.py:36` and `:38` (the comment above the constant), `:39` (the constant), `:770` (the definition), `:777` and `:782` (its docstring and its `return`); `claude_code/constants.py:31` (the comment); `claude_code/helpers.py:24` (the import), `:586` and `:587` (definition and docstring of `selected_model_supports_highres_images`), `:743` and `:754` (definition and docstring of `get_effective_image_dimension`), `:766`, `:767` and `:768` (the calls inside that method, deleted with it), `:773` (the definition of `_upgrade_retired_model`). `cli/peer_send.py:154` and `cli/_drop_request/attachments.py:311` are gone since Task 6. Stop and report if any other line appears.
  - `providers/helpers.py`: delete the comment and the constant `MAX_IMAGE_DIMENSION = 2576` (`:35-39`) and the method `get_effective_image_dimension` (`:770-782`).
  - `providers/claude_code/helpers.py`: delete `MAX_IMAGE_DIMENSION,` from the import of `twicc.providers.helpers` (`:24`); delete the methods `selected_model_supports_highres_images` (`:586-597`), `get_effective_image_dimension` (`:743-771`) and `_upgrade_retired_model` (`:773-802`); delete `from datetime import date` (`:12`), whose only use is `_upgrade_retired_model` (`:783`).
  - `providers/claude_code/constants.py:31-33`: replace the comment by `# Native vision resolution flag, read by the frontend only (frontend/src/providers/claude_code/helpers.js getEffectiveImageDimension: True ⇒ 2576 px, False ⇒ 1568 px).`

- [ ] **Step 5: Check that nothing is left.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && grep -rn "ATTACHMENT_SUPPORT\|get_attachment_support\|attachment_support\|get_effective_image_dimension\|_upgrade_retired_model\|selected_model_supports_highres_images" src tests frontend/src --include=*.py --include=*.js --include=*.vue; grep -rn "MAX_IMAGE_DIMENSION" src --include=*.py`
Expected: only the assertions of `tests/test_composer_attachment_planner.py` for the first command (the frontend `getEffectiveImageDimension` is camelCase and is not matched); no output for the second command.

- [ ] **Step 6: Run the suites.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest tests/test_composer_attachment_planner.py tests/test_cli_attach.py tests/test_wait_reply.py tests/test_provider_home_propagation.py tests/test_composer_attachment_send.py tests/test_claude_attachment_delivery.py tests/test_agent_run_snapshot.py -q && uvx ruff check src/twicc/providers/helpers.py src/twicc/providers/claude_code/helpers.py src/twicc/providers/claude_code/constants.py src/twicc/providers/codex/helpers.py src/twicc/cli/_drop_request/bootstrap_local.py tests/test_composer_attachment_planner.py`
Expected: PASS, no new lint finding (compare with `uvx ruff check` of the same files before the edit; `F401` on `date` or `MAX_IMAGE_DIMENSION` means an import was left).

- [ ] **Step 7: Commit.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add src/twicc/providers/helpers.py src/twicc/providers/claude_code/helpers.py src/twicc/providers/claude_code/constants.py src/twicc/providers/codex/helpers.py src/twicc/cli/_drop_request/bootstrap_local.py tests/test_composer_attachment_planner.py && git commit -F /tmp/phase2-task8.msg
```

```
refactor(providers): retire the legacy attachment support tables

No entry point validates attachments against the per-provider type and
size tables any more: every file goes through the staging store and the
attachment policy. Remove ATTACHMENT_SUPPORT, get_attachment_support and
the attachment_support key of the bootstrap data and of the CLI's local
bootstrap.

Remove the backend image-dimension helpers too (MAX_IMAGE_DIMENSION,
get_effective_image_dimension and the two Claude helpers it alone used):
their only caller was the CLI's legacy attachment encoder. The frontend
keeps its own copy for the legacy retry of a failed send.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

### Task 9 (spec T10): Full suites, manual validation matrix, validation record

The manual matrix needs real binaries and a running worktree instance. **The user restarts the servers**: ask them, and wait. Migration `0153` is new: `uv run ./devctl.py start` (or `restart`) applies it at backend start; never run `migrate` by hand. The worktree backend is on `:3501`, Vite on `:5174`.

**Files:**
- Create: `docs/plans/2026-10-07-attachments-phase2-validation.md`

- [ ] **Step 1: Full automated suites.**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest -q 2>&1 | tail -5; cd frontend && npm test 2>&1 | tail -5 && npm run build 2>&1 | tail -3`
Expected: all pass (two `tests/test_migration_process.py` tests are known to be flaky under load: re-run them alone before reporting a failure). Record the counts.

- [ ] **Step 2: Ask the user to restart the worktree backend.** Message: "Please restart the attach-any-files backend: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && uv run ./devctl.py restart back`. It applies migration 0153." Then check:

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && uv run ./devctl.py status && uv run ./devctl.py logs back --lines=80 | grep -i "0153\|error"`
Expected: backend up on 3501; `Applying core.0153_peer_message_attachments... OK` (or the migration listed as applied); no error.

- [ ] **Step 3: Prepare files and target sessions.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && mkdir -p /tmp/phase2-qa && head -c 400M /dev/urandom > /tmp/phase2-qa/video-400.mp4 && head -c 40M /dev/urandom > /tmp/phase2-qa/forty.bin && head -c 60M /dev/urandom > /tmp/phase2-qa/sixty.bin && head -c 49M /dev/urandom > /tmp/phase2-qa/fortynine.bin && head -c 51M /dev/urandom > /tmp/phase2-qa/fiftyone.bin && head -c 35M /dev/urandom > /tmp/phase2-qa/thirtyfive.bin && printf 'diff --git a/x b/x\n' > /tmp/phase2-qa/fix.patch && printf 'hello\n' > /tmp/phase2-qa/notes.txt
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run twicc create-session --project /tmp/phase2-qa --provider claude_code --title "QA phase2 claude" 'Reply OK'
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run twicc create-session --project /tmp/phase2-qa --provider codex --title "QA phase2 codex" 'Reply OK'
```

Note the two `session_id` values (`<CLAUDE_SID>`, `<CODEX_SID>`). Produce the PNG and the PDF of row 3 (`Image.init()` registers the JPEG encoder the PDF writer needs):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && uv run python -c "from PIL import Image; Image.init(); Image.new('RGB', (64, 64), 'red').save('/tmp/phase2-qa/shot.png'); Image.new('RGB', (200, 100), 'blue').save('/tmp/phase2-qa/spec.pdf')" && head -c 5 /tmp/phase2-qa/spec.pdf
```

Expected: `%PDF-`.

- [ ] **Step 4: Run the matrix (spec §7 "Manual") and record each row.**

| # | Case | Command / action | Expected |
|---|---|---|---|
| 1 | 400 MB video, local CLI → Claude | `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run twicc send-message <CLAUDE_SID> 'List the attachments you got.' --attach /tmp/phase2-qa/video-400.mp4 --timeout 600` | exit 0; the message shows a file tile `video-400.mp4`; `~/.twicc/artifacts/<CLAUDE_SID>/attachments/video-400.mp4` exists (400 MB) |
| 2 | Same → Codex | same with `<CODEX_SID>` | exit 0; artifact file present; no "dropping documents" warning in `backend.log` |
| 3 | Hybrid: image + PDF + text | switch a Claude session to hybrid in the UI (needs `TWICC_CLAUDE_HYBRID_ENABLED=1`), then `… send-message <HYBRID_SID> 'Describe them.' --attach /tmp/phase2-qa/shot.png --attach /tmp/phase2-qa/spec.pdf --attach /tmp/phase2-qa/notes.txt` | exit 0; image native (`@` reference), PDF and text as files in `attachments/`; the text file is not lost (old D18 bug) |
| 4 | Hybrid command refused | `… send-message <HYBRID_SID> '/help' --attach /tmp/phase2-qa/notes.txt` | exit 3, `rejected`, code `attachments_with_command`, field `attachments` |
| 5 | `--remote` 40 MB | `TWICC_DATA_DIR=$PWD uv run twicc token create --name phase2-qa` (note the token), then `uv run twicc --remote http://localhost:3501 --remote-token <TOKEN> send-message <CLAUDE_SID> 'remote 40' --attach /tmp/phase2-qa/forty.bin` | exit 0; artifact `forty.bin` keeps its name |
| 6 | `--remote` 60 MB refused | same with `sixty.bin` | exit 2 before any HTTP call; stderr names the 50 MB limit and `remote:` |
| 7 | `--remote` with `remote:` | same with `--attach remote:/tmp/phase2-qa/sixty.bin` | exit 0; artifact `sixty.bin` |
| 8 | Internal MCP: path + named data URI | in a TwiCC session of this instance, ask the agent: "Call mcp__twicc__send_message to session <CLAUDE_SID> with prompt 'mcp test' and attach ['/tmp/phase2-qa/notes.txt', 'data:text/plain;name=hello%20world.txt;base64,SGVsbG8=']" | exit 0; tiles `notes.txt` and `hello world.txt` |
| 9 | External MCP: named data URI | only if an external MCP client is connected to this instance: same call with the data URI only | exit 0; tile `hello world.txt`; the McpOperation audit row has no `attach` |
| 10 | Peers, Git patch → Codex and hybrid | needs a second instance running this branch, paired in Settings → Peers (the user provides it; do not create a worktree). From the other instance: `twicc peer-send <PEER> 'Patch' 'Apply this' --attach /tmp/phase2-qa/fix.patch`; here, deliver it to `<CODEX_SID>`, then redeliver to `<HYBRID_SID>` | the review dialog lists `fix.patch` with its name; the delivered message carries `fix.patch` as a file in both sessions (the field case of spec §1.1) |
| 11 | Peers, 49 MB then 51 MB | `twicc peer-send <PEER> 'Big' 'Big file' --attach /tmp/phase2-qa/fortynine.bin`, then `fiftyone.bin` | 49 MB: exit 0 within 468 s; 51 MB: exit 1 `attachments_too_large` with the URL hint, nothing staged |
| 12 | Older peer, small file | pair with an instance on `main` (before this branch); `peer-send` with `notes.txt` | exit 3 `send_failed`, text "may be too old to receive attachments"; a text-only send still succeeds |
| 13 | Older peer, 40 MB | `peer-send` with `forty.bin` | exit 3, text "refused the message size. An older instance also refuses any attachment." (413: body above the old 48 MB cap) |
| 14 | Older peer, 35 MB | `peer-send` with `thirtyfive.bin` | exit 3, the 400 text (the body fits the old cap) |

- [ ] **Step 5: Write the validation record** `docs/plans/2026-10-07-attachments-phase2-validation.md` with these sections, in the style of `docs/plans/2026-10-04-composer-attachments-any-file-validation.md`:
  1. **Automated results** — the commands of Step 1 and their counts.
  2. **Manual matrix** — the table of Step 4 with an "Observed" and a "Status" (PASS / FAIL / NOT RUN + reason) column.
  3. **Accepted limitations** (spec §4.4.2, §4.4.3, §4.8.3, §4.2): a backend restart during a peer POST leaves the row `pending`; a proxy that buffers a large peer message can end in `unreachable` while the receiver stored it; two large `peer_send` in one MCP `batch` can exceed 600 s; a very large `remote:` staging can end in exit 7 while the send completes; an old `--remote` client keeps a 30 s read timeout.
  4. **Post-merge reminders** — restart the backend through `devctl.py` (migration `0153`); plugin `0.108.0`; CHANGELOG not written (only on request).

- [ ] **Step 6: Commit the record.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && git add docs/plans/2026-10-07-attachments-phase2-validation.md && git commit -F /tmp/phase2-task9.msg
```

```
docs(attachments): record the phase 2 validation

Record the automated suite results and the manual matrix of the phase 2
design: CLI, --remote, MCP and peer sends of any file, the limits, the
hybrid command refusal, and older peers.

Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
```

---

## Out of Scope

- **Spec T11** (optional, deferrable): converting the legacy `images`/`documents` of the WS frame, then deleting the legacy parameters of the managers and agents and the legacy path of `_materialize_attachments`. Not planned.
- The CHANGELOG (only on an explicit request of the user).


## Self-Review

Checked against the spec after writing the plan.

1. **Spec coverage.** Every section maps to a task (table below). The spec §7 test list is covered line by line: staging (Task 1), reaper (Task 1), inline module (Task 1), CLI local and batch (Task 4), CLI `peer-send` (Task 6), remote forwarder (Task 5), service send and create, lane (Task 3, real planner per provider included), RPC and MCP (Task 5, `peer_send` schema in Task 6), peer sender, receiver, summaries, read API, purge, migration, old-peer behavior (Task 6), frontend (Task 6), manual matrix (Task 9).
2. **Placeholder scan.** No "TBD", "TODO", "similar to Task N" or step without code. `<MODEL>` in the commit trailers is deliberate: the executor writes the model that runs at commit time.
3. **Type consistency.** `attach_sources.resolve/stage/as_payload/new_request_bucket`, `inline.parse_data_uri(spec, *, budget)`, `InlineBudget(hint, *, limit)`, `too_large_message(total, limit, hint)`, `format_mb`, `staging.stage_path/stage_bytes/discard_staged/new_bucket/name_max_bytes`, `drop.refs_to_release/is_attachment_code/message_with_names/has_legacy_fields/LEGACY_FIELDS_MESSAGE`, `outbound.build_message_body/post_message(body=)`, `peer_messages.prepare_inbound_payload/peer_message_summary_queryset/peer_message_full_queryset`, `serializers.peer_message_text` keep one name and one signature from definition to every use.
4. **Review Focus.** Five lines, each with a named test in its owning task (Tasks 1, 4, 5, 6).

## Coverage

| Spec section | Task(s) |
|---|---|
| §1 Goal and non-goals | 3, 4, 5, 6 (non-goals: no composer, tus, planner, committer or manifest change) |
| §2 D1 scope, D2 naming | 3, 4, 5, 6 |
| §2 D3 forms, D10 `name=` | 1 (parser), 4 (CLI), 5 (forwarder) |
| §2 D4 phase 1 pipeline | 3 |
| §2 D5 50 MB, D6 hints, D11 inline only | 1, 4, 5, 6, 7 |
| §2 D7, D12 body caps | 5 (RPC, MCP), 6 (peer receiver) |
| §2 D8 wire, D9 no negotiation, D16 legacy receive, D17 migration | 6 |
| §2 D13 staging identity, D18 real copy | 1, 4 |
| §2 D14 ownership, D15 lane | 3, 6 |
| §2 D19 copy per recipient | 4 |
| §2 D20 retire legacy support | 8 (plus the dead image-dimension helpers) |
| §3 current state | verified while writing; line numbers in each task |
| §4.1 overview | 3–6 |
| §4.2 limits, `parse_data_uri`, `InlineBudget`, labels, legacy blocks, `transfer_timeout`, `PEER_SEND_TIMEOUT_WITH_FILES` | 1 |
| §4.3.1 staging API | 1 |
| §4.3.2 retention and release | 1 (reaper), 3 and 6 (service release), 4 (CLI discard) |
| §4.4.1 `--attach`, `attach_sources` | 4 (peer help in 6) |
| §4.4.2 commands | 4 (`send-message`, `send-messages`, `create-session`), 6 (`peer-send`) |
| §4.4.3 remote forwarder | 5 |
| §4.4.4 CLI failure handling | 4 |
| §4.5.1 drop payloads and wrappers | 3, 4 (legacy refusal), 6 (peer kinds) |
| §4.5.2 send to an existing session | 3 |
| §4.5.3 release rules | 3, 6 |
| §4.5.4 plan target outside `asgi.py` | 2 |
| §4.5.5 send lane | 3 |
| §4.6 RPC | 5 |
| §4.7 MCP | 5 (`peer_send` description: 6) |
| §4.8.1 wire format | 6 |
| §4.8.2 receiver | 6 |
| §4.8.3 sender | 6 |
| §4.8.4 read APIs, purge, UI | 6 |
| §4.8.5 data migration | 6 |
| §4.9 errors | 3, 4, 5, 6; documented in 7 |
| §4.10 concurrency | 1 (bucket race), 3 (lane), 6 (off-loop work) |
| §4.11 security | 1 (names, real copy), 5 (log redaction), 6 (receiver validation, sanitation) |
| §5 backward compatibility | 4 (legacy drop refusal), 5 (old client: no change), 6 (old receiver texts, new kind), 9 (older peer rows of the matrix) |
| §6 docs, skills, plugin | 7 |
| §7 tests | 1–6 (automated), 9 (manual) |
| §8 T1–T10 | Tasks 1–9 (T7 merged into Task 6; T8 → Task 7; T9 → Task 8; T10 → Task 9) |
| §8 T11 | out of scope |
