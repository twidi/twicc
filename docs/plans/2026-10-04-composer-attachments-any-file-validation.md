# Composer attachments for any file — validation record

Date: 2026-10-04. Branch: `attach-any-files`.

- Design: `docs/plans/2026-10-03-composer-attachments-any-file-design.md`
- Plan: `docs/plans/2026-10-04-composer-attachments-any-file-implementation-plan.md` (Task 19)

This record separates four kinds of evidence. Only the automated results are observed facts.

| Kind | Status |
|---|---|
| Automated tests and build | Run. Results below. |
| UI observations | **None.** No running instance was used. |
| Provider checks (real Claude / Codex) | **None.** No provider call was authorized. |
| Manual matrix | **Not run.** Each row waits for a user-authorized running instance. |

## 1. Automated results

### 1.1 Final commands

Each command ran on its own, from the worktree.

| Command | Result |
|---|---|
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest` | Exit 1. **7395 passed, 8 failed, 21 skipped** (5 min 58 s). All 8 failures are known and unrelated (below). |
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files/frontend && npm test` | Exit 0. **1960 tests, 1960 pass, 0 fail.** |
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files/frontend && npm run build` | Exit 0. Only the existing Vite "dynamic import will not move module" warnings. |

The 8 Python failures, all present before this branch or flaky, none in attachment code:

- `tests/test_wait_reply.py::test_create_session_hands_its_wait_arguments_over` — 5 parameter cases (`args0`…`args4`), `no_provider_configured` baseline.
- `tests/test_ephemeral_providers.py::test_codex_final_answer_ignores_commentary_children_and_sdk_logging` and `::test_codex_ephemeral_file_change_does_not_capture_transcript_diff` — CodexAgent `_active_tools` baseline.
- `tests/test_codex_migration_scheduler.py::test_forced_rebuild_is_discovered_after_older_compute_restores_current_version[False]` — known flaky: the file passes alone (23 passed).

Every attachment suite passes, including the Task 19 additions.

No hermetic LLM diagnostic ran: this branch changes no model, runtime or SDK.

### 1.2 Cross-layer regressions added in Task 19

| Contract | Test |
|---|---|
| Staged mixed files → real planner → real committer → real Claude prompt builder → JSONL user record → real ingestion (`transform_inline`) | `tests/test_attachment_pipeline_roundtrip.py::test_claude_mixed_files_round_trip_to_ordered_metadata` |
| Same chain for Codex (turn input → canonical `UserMessage`) | `tests/test_attachment_pipeline_roundtrip.py::test_codex_mixed_files_round_trip_to_ordered_metadata` |
| All-file follow-up without text is a visible user message (backend) | `tests/test_attachment_pipeline_roundtrip.py::test_claude_file_only_follow_up_without_text_is_a_visible_user_message` |
| Transformed records → history strip, in order, natives as thumbnails, file chips linked to the owner (frontend) | `frontend/src/utils/attachmentStrip.test.js`, tests `round trip (…)` |
| Stored line and optimistic bubble get the same match key (text send and all-file send without text) | `attachmentStrip.test.js`, `round trip (Claude SDK): the stored line matches…`, `round trip (Codex): …` |
| A large file never embeds its bytes in the provider input or the stored record | `test_attachment_pipeline_roundtrip.py` (3 MiB video; size bounds and a base64 marker) |
| A large file never embeds its bytes in the WebSocket frame, the in-flight snapshot or the draft row | `frontend/src/utils/composerAttachments.test.js`, `a large file send never embeds its bytes…` (8 MiB file) |
| Both halves read the same literal records | `tests/fixtures/attachment_roundtrip_records.json`, pinned by `test_shared_frontend_fixture_matches_the_backend_output` (regenerate with `TWICC_UPDATE_FIXTURES=1`) |

The new-session text requirement already has two regressions and needed no new test:

- `tests/test_attachment_only_messages.py::test_create_session_still_requires_text_with_composer_refs`
- `tests/test_composer_attachment_send.py::test_creation_still_requires_text_with_refs`

**Result of the new regressions.** The round-trip and large-file tests passed against the existing code. They found no integration gap in the pipeline. A mutation (strip order reversed in `buildAttachmentStrip`) made 12 strip tests fail, including the three round-trip tests. One fixture observation: a file sent twice into the same session shows its final name `capture (1).mp4` in the strip. This matches the spec: file entries carry the final name on disk.

### 1.3 Integration gaps corrected in Task 19

| Gap | Correction | Test |
|---|---|---|
| API-error **Resend** read the user text with `extractUserMessageText(getParsedContent(it))`. For a message with a manifest and a failed image, it resent the CLI's `[Image could not be processed: …]` placeholder as user text. | New pure helper `userMessageResendText` in `frontend/src/utils/attachmentStrip.js`. It applies `matchableUserMessage` first, like the store's match key. `ApiError.vue` uses it. | `attachmentStrip.test.js`: `resend text leaves out the placeholder…`, `wiring: the API-error Resend…` |
| A promotion failure **after** the claim and **before** the tombstone (Task 7 deferred finding) left the claimed final name in `artifacts/<session>/attachments/`. It was a hard link of the still-ready staged file. A Retry into another session then hard-linked the same inode again: two sessions shared one inode, against spec §7.1. A Retry into the same session produced a duplicate `name (1).ext`. | `staging.promote_entry` gives the claimed name back when the step after the claim fails and `promoted.json` does not name it. A tombstone whose rename landed (only its directory sync failed) keeps its file. A crash at that point still leaves an orphan (spec §16). | `tests/test_composer_attachment_commit.py`: `test_tombstone_failure_keeps_ready_source_and_leaves_no_orphan`, `test_other_session_retry_after_a_tombstone_failure_shares_no_inode`, `test_failure_after_a_durable_tombstone_keeps_the_promoted_file`, updated `test_failure_on_a_later_entry_keeps_earlier_promotions_retryable` |

Coverage added without a code change:

- `attachmentStrip.test.js`: more inline entries than native slots (extra entries are chips without `src`); fewer entries than slots (extra blocks keep the legacy rendering); an unknown `kind` through `buildAttachmentStrip`.
- `tests/test_composer_attachment_planner.py::test_off_loop_staging_race_is_attachment_missing`: a staged file that vanishes during planning becomes `attachment_missing` (Task 5 deferred finding, already handled by `plan_attachments_off_loop`).

### 1.4 Deferred findings checked and closed

| Finding | Verification |
|---|---|
| Task 5: raw `OSError` escapes the planner | `plan_attachments_off_loop` maps it to `attachment_missing`, and the WS and creation paths use that wrapper. Now pinned by a test. |
| Task 15: `FailedSendBanner` Edit vs a concurrent Delete | Task 16 added `guarded()` / `actionInProgress`: Retry, Edit and Delete are disabled while an action runs, and `discard` returns early. |
| Task 15: the store built snapshot entries inline | `registerOutgoingSend` now calls `snapshotAttachments(attachments || [])` (`frontend/src/stores/data.js`). |
| Task 18: `ApiError.vue` Resend placeholder text | Corrected (§1.3). |

## 2. Deferred findings carried to final review

These are real but minor end-state findings. None is a demonstrated integration gap of the delivered pipeline.

| Origin | Finding | Reason for deferral |
|---|---|---|
| Task 17 | Send stays disabled when a legacy draft media cannot be decoded. The badge says "preparing older attachments", which is wrong for a permanent decode failure. | **Fixed in the final review.** The migration reports undecodable rows; the store keeps them in `legacyFailedIds`; the badge turns `danger` and asks the user to remove them. Remove clears the failure. |
| Task 14 | A foreign upload completion that arrives before the `status/` answer can leave a chip `uploading` until the next reconnect. | **Fixed in the final review.** The composer keeps the completions that match no attempt yet; a later `uploading` answer naming one of them makes the chip `ready`. |
| Task 14 | Concurrent hydrate and first-connect reconcile calls are not serialized. | No wrong end state observed. Both paths are idempotent. |
| Task 17 | Two tabs that see `missing` at the same time can show a transient `failed` chip with Retry. | Transient. The next status answer corrects it. Since the final review, a Retry that meets the released entry (creation `410`) ends `missing` (Remove only), never `failed` with Retry again. |
| Task 17 | Parallel readiness cleanups each rewrite the draft message. | Lost `mediaIds` updates are harmless. A draft-text overwrite is theoretical. |
| Task 16 | A legacy-only draft: a chip that turns ready during the trust dialog can give a partial send. | Legacy-only drafts, an edge sequence. Needs a UI check. |
| Task 16 | `readTextPreview` does not guard a `null` body; `dispose()` revokes no object URL. | **Fixed in the final review.** A body-less answer is an empty preview; `dispose()` revokes every held object URL. |
| Task 18 | The fork-parent file chip falls back to the current session's project id when the parent row is not loaded. | A Codex fork shares its parent's project in practice. |
| Task 13 | Every `OSError` from the planner becomes `attachment_missing` (also `EACCES`). | Retry and Remove stay possible. The code is accurate for the common case (a vanished file). |
| Task 12 | The planner can plan inline entries for a hybrid session whose data dir has whitespace; the send is then refused with `attachment_commit_failed` (Retry cannot succeed). | Rare install layout. Ruling recorded in Task 12. |
| Task 12 | Leftover `att_*` files after a `hybrid_composer_busy` refusal. | Same behavior as the legacy hybrid path. |
| Task 10 / 11 | A send cancelled while it waits for the manager lock leaves its promoted files in `attachments/` and its staging entries to retention. | Covered by spec §16 (promoted files of a failed send stay as artifacts) and by the 7-day committed retention. |
| Task 6 | The manifest parser accepts Unicode digits and leading zeros; the builder does not enforce every parser invariant. | **Parser part fixed in the final review** (ASCII digits only, no leading zeros). The builder still does not enforce every parser invariant: it never emits such blocks. |
| Task 4 | `DecompressionBombWarning` reaches stderr for 89.5–100 MP images; the PNG walk can read about 8 MiB; `exif_transpose` is not in place. | **Warning fixed in the final review** (no global Pillow change; the 100 MP limit is checked on the opened size before decode). The PNG walk and `exif_transpose` notes remain: memory cost in rare large images, behavior is correct. |
| Task 3 | The reaper holds the global creation lock while it waits for an upload lock; `content_media_type` opens the file twice. | Latency only. No wrong state. Since the final review, the content endpoint sniffs the type from the descriptor it streams (one open, identity-checked against the validated path). |
| Tasks 1–18 | Test-quality notes (weak assertions, source-regex wiring tests, untested log branches). | No behavior defect. |

## 3. Manual matrix — NOT RUN

No server was started and no provider was called. Every row below is **NOT RUN — requires a user-authorized running instance**.

| Scenario | Status | What to observe |
|---|---|---|
| 400 MB video + images + large PDF, Claude existing/new | NOT RUN — requires a user-authorized running instance | Chips upload; eligible images inline; other files in artifacts; order preserved |
| Same files, Codex existing/new | NOT RUN — requires a user-authorized running instance | Images inline; PDF/video artifacts; canonical binding only after finish |
| Hybrid `/model …` with attachments | NOT RUN — requires a user-authorized running instance | Synchronous `attachments_with_command`; no paste, ack, or release |
| Hybrid ordinary mixed send | NOT RUN — requires a user-authorized running instance | Real names, image references, file chips; no raw manifest in history |
| Ephemeral video | NOT RUN — requires a user-authorized running instance | `attachment_requires_artifacts`; no promotion; Retry/Edit records intact |
| Forced promotion/send failure | NOT RUN — requires a user-authorized running instance | Failed-send banner; Retry reuses refs/tombstones; Edit appends refs |
| Lost ack after actual delivery | NOT RUN — requires a user-authorized running instance | Server releases entries; restored Edit chips show missing |
| Reload during upload | NOT RUN — requires a user-authorized running instance | Status-driven chip; no File resume from chip; interrupted upload offers Remove |
| Two tabs sharing draft | NOT RUN — requires a user-authorized running instance | Current attempt controls readiness; removal never recreates an entry |
| Picker/paste/drop/screenshots/provider switch | NOT RUN — requires a user-authorized running instance | Every file accepted; no client resizing; attachments survive provider switch |
| Web peer PDF into Codex composer | NOT RUN — requires a user-authorized running instance | PDF accepted and becomes file; rollback releases imported entries |
| Shared history | NOT RUN — requires a user-authorized running instance | Same ordered strip; file chips have no owner artifact link |
| Long plan + soft stop + force stop | NOT RUN — requires a user-authorized running instance | Other frames/broadcasts continue; soft waits; force kills the current process immediately |

### Claude document title — NOT RUN

- **Check:** send a PDF with a 230-character name natively to a real Claude session.
- **Contract:** the `document` block `title` is cut to **200 characters** (`tests/test_claude_attachment_delivery.py` pins the cut, not the API acceptance).
- **Observe:** the API accepts the request. If the API rejects 200 characters, record its real limit and reconcile the spec before any contract change.
- **Status:** NOT RUN — requires a user-authorized running instance and authorized provider execution.

## 4. Accepted limitations (spec §16)

- **Numbering** restarts at 1 in each message.
- **Dense or encrypted PDF.** The 512 KiB native threshold does not bound tokens: a dense PDF can reach about 40 pages (about 100k tokens). A password-protected PDF under 512 KiB goes natively and the API refuses it. TwiCC does no PDF parsing, page count or encryption detection.
- **EXIF orientation** is applied only when TwiCC resizes an image. An image within the target keeps its bytes (the API ignores the metadata), as before.
- **Claude platform detection** reads the user and project settings files only. A platform set through managed settings or a gateway counts as first party.
- **Hybrid** manifests show the real file names next to the randomized `att_*` paths.
- **Inline binding** between a native block and its manifest line relies on order, not on a per-block label (hybrid lines carry an explicit `@<path>`).
- **Retention and promotion duplicates.** Files promoted for a send that finally fails and is dismissed stay in `attachments/` as ordinary artifacts. A crash in the middle of a promotion can produce a duplicate artifact on Retry (a handled failure no longer does, §1.3).
- **Draft retention.** Attachments that no browser references for 30 days are removed; their chips then show `missing`.
- **Reload-interrupted uploads** cannot resume from the chip. The user attaches the file again (Remove is offered).
- **CLI media eviction.** The Claude CLI evicts media over the whole request. A message whose own natives exceed 14 MiB after the CLI's image recompression, sent after older media, can see its first natives evicted (their manifest lines stay). This needs about 28+ recompressed images or many PDFs in one message. The 580 / 80 count quota keeps the message itself under the CLI's count eviction; media already in the history can still be pushed out, oldest first.
- **Ephemeral staging traces.** Ephemeral attachments sit in backend staging between upload and delivery. Delivery releases them; an undelivered send keeps them up to 7 days; a discarded draft releases them at once.
- **Lost-ack Edit.** A send that the browser saw as failed but the server delivered has its entries released. Edit then restores chips in the `missing` state.
- **Legacy phase-1 paths.** CLI `--attach`, the MCP tools and backend peer sends keep the legacy `images` / `documents` fields (and the known hybrid text behavior, D18). A legacy failed-send Retry stays available for 7 days and still sends `documents` (Codex drops them, as before). Edit converts a legacy snapshot to staged refs.
- **Protocol limits** recorded by the plan review: an exact protocol-shaped pasted manifest is indistinguishable from a generated one when every §10.1 check passes; force kill targets the current process and does not cancel an admitted send.

## 5. Proposed CHANGELOG entry (not applied)

The repository rule is: no CHANGELOG entry without an explicit request. This is a proposal for `## [Unreleased]`, in the tone of the current entries.

```markdown
### Added

- **Attach any file** — The message composer accepts any file of any size: videos, archives, PDFs, text, images. Each file uploads at once and shows as a chip; Send waits until every upload is done. TwiCC decides on the server what the model receives directly (images, and small PDFs and text files for Claude) and saves every other file in the session's artifacts, where the agent reads it. Sent messages show their files in order; a file opens in the Artifacts tab.

### Changed

- **Attachment limits** — Files the model cannot take directly are no longer refused: they are saved as artifacts. Attachments are kept up to 30 days in an unsent draft and up to 7 days after a failed send (Retry and Edit keep working). After a page reload, an upload in progress must be added again. A send that reached the agent but showed as failed (lost connection) restores its files as missing when you edit it. Drafts saved before this version are converted automatically. The `--attach` CLI option, the MCP tools and peer messages sent by the backend keep the previous attachment behavior for now.
```

## 6. Post-merge reminders for the user

- **Restart the backend** with `devctl.py` (for example `uv run ./devctl.py restart back`). The compute versions are **not** bumped (Claude 112, Codex 53): no stored JSONL holds a manifest yet, and live ingestion extracts it from new messages, so no session recompute is needed. A session already ingested by an earlier build of this branch keeps its raw manifest block in the user text until the next compute-version bump.
- **No database migration.** No model changed.
- **No package installation.** No dependency changed (Pillow and `tus-js-client` were already declared).
- **Frontend build.** Run `cd frontend && npm run build`: the share viewer bundle (`share-session/`, the history strip in shares) is not HMR'd.
- **New data folder.** Staged uploads live in `<data dir>/composer-attachments/`. A daily cleanup task applies the retention above.
- **Hybrid sessions.** Hybrid CLIs in tmux survive a backend restart and are adopted again; no `kill-tmux` is needed. The new hybrid attachment behavior applies to messages sent after the restart.
