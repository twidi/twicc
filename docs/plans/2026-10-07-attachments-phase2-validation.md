# Attachments phase 2 (CLI, RPC, MCP, peers) — validation record

Date: 2026-10-07. Branch: `attach-any-files` (base of this record: `99390d6a`).

- Design: `docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md` (§7 "Manual")
- Plan: `docs/plans/2026-10-07-attachments-phase2-implementation-plan.md` (Task 9, spec T10)

| Kind | Status |
|---|---|
| Automated tests and build | Run. All pass (§1). |
| Manual matrix, local rows (CLI, `--remote`, hybrid, internal MCP) | Run on the worktree instance (backend `:3501`, Vite `:5174`) with real Claude and Codex. 8 PASS. |
| Manual matrix, external MCP and peer rows | **NOT RUN.** They need a setup that only the user can make (§2.3). |

## 1. Automated results

Each command ran on its own, from the worktree.

| Command | Result |
|---|---|
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run pytest -q` | Exit 0. **9199 passed, 21 skipped**, 70 warnings (8 min 18 s). No failure: the two known flaky `tests/test_migration_process.py` tests passed. |
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files/frontend && npm test` | Exit 0. **2485 tests, 2485 pass, 0 fail.** |
| `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files/frontend && npm run build` | Exit 0. Only the existing Vite "dynamic import will not move module" warnings. |

No hermetic LLM diagnostic ran: this branch changes no model, runtime or SDK.

## 2. Manual matrix

### 2.1 Setup

- **Backend restart.** `uv run ./devctl.py stop back`, port `3501` free after 1 s, then `uv run ./devctl.py start back`. The log shows `Migration success migration=core.0153_peer_message_attachments direction=forward` (11:23:46), then `Uvicorn running on http://0.0.0.0:3501`. No error after the restart. The post-start port check of devctl timed out during the initial sync; the log confirms the start.
- **Files** in `/tmp/phase2-qa/`: the Step 3 commands of the plan (`spec.pdf` starts with `%PDF-`).
- **Sessions** (all in the worktree instance, project `/tmp/phase2-qa` unless noted):

| Name | Session id | Provider |
|---|---|---|
| `<CLAUDE_SID>` "QA phase2 claude" | `63e68537-1152-44a5-b820-e0b05ee96991` | Claude (SDK) |
| `<CODEX_SID>` "QA phase2 codex" | `01a115ad-bb2f-7d11-af49-2b5e3be8129d` | Codex |
| "QA phase2 hybrid" (switched in the UI) | `7160a372-87c3-45bf-b943-8233b102cfb8` | Claude, hybrid |
| `<HYBRID_SID>` (hybrid draft, project = this worktree) | `56593d29-c4c1-4251-ac9f-292dba9dc0ab` | Claude, hybrid |
| "QA phase2 MCP caller" | `17034654-e7ee-4a44-8e1e-c5b5943bb7ba` | Claude (SDK) |

Every command below ran as `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && …`. "Tile" means the attachment strip of the user message in the UI (`.attachment-strip-item`, read in the browser on `:5174`), backed by the `twicc_attachments` metadata of the stored user line.

### 2.2 Results

| # | Case | Command / action | Expected | Observed | Status |
|---|---|---|---|---|---|
| 1 | 400 MB video, local CLI → Claude | `TWICC_DATA_DIR=$PWD uv run twicc send-message <CLAUDE_SID> 'List the attachments you got.' --attach /tmp/phase2-qa/video-400.mp4 --timeout 600` | exit 0; tile `video-400.mp4`; artifact present (400 MB) | Exit 0, `"status": "sent"` in 2.7 s. `~/.twicc/artifacts/<CLAUDE_SID>/attachments/video-400.mp4` = 419430400 bytes. Stored line: entry `video-400.mp4`, `kind` video, `mode` file. UI: one tile `video-400.mp4`. Claude listed the file and its folder. | PASS |
| 2 | Same → Codex | same with `<CODEX_SID>` | exit 0; artifact present; no "dropping documents" warning | Exit 0 in 2.1 s. Artifact `video-400.mp4` = 419430400 bytes. `backend.log` after the send: no `dropping`, no `document`, no error. Stored line and UI: one tile `video-400.mp4`. Codex replied "video-400.mp4 — video file". | PASS |
| 3 | Hybrid: image + PDF + text | Session switched to hybrid in the UI, then `… send-message <HYBRID_SID> 'Describe them.' --attach /tmp/phase2-qa/shot.png --attach /tmp/phase2-qa/spec.pdf --attach /tmp/phase2-qa/notes.txt` | exit 0; image native (`@` reference); PDF and text as files in `attachments/`; the text file is not lost | On `<HYBRID_SID>`: exit 0. `attachments/` holds `spec.pdf` and `notes.txt`. The hybrid dir holds `att_7d314d12fe11.png` (152 bytes, the PNG). The CLI wrote the user line (`entrypoint` cli): entries `shot.png` (`mode` inline, `reference` `att_7d314d12fe11.png`), `spec.pdf` (file), `notes.txt` (file). UI: three tiles `shot.png`, `spec.pdf`, `notes.txt`. On `7160a372-…` (the session switched in the UI) the same send also exited 0 with the same files; its CLI then waited on the Claude trust dialog for `/tmp/phase2-qa`, which was not answered (see §2.4). | PASS |
| 4 | Hybrid command refused | `… send-message 7160a372-… '/help' --attach /tmp/phase2-qa/notes.txt` | exit 3, `rejected`, code `attachments_with_command`, field `attachments` | Exit 3: `"status": "rejected"`, field `attachments`, code `attachments_with_command`, "Attachments cannot be sent with a command". No new file in `attachments/`. | PASS |
| 5 | `--remote` 40 MB | `uv run twicc --remote http://localhost:3501 --remote-token <TOKEN> send-message <CLAUDE_SID> 'remote 40' --attach /tmp/phase2-qa/forty.bin` (token from `twicc token create --name phase2-qa`) | exit 0; artifact `forty.bin` keeps its name | Exit 0 in 1.6 s; one `POST /rpc/send-message` 200. Artifact `forty.bin`, byte-identical to the source (`cmp`). UI: tile `forty.bin`. | PASS |
| 6 | `--remote` 60 MB refused | same with `sixty.bin` | exit 2 before any HTTP call; stderr names the 50 MB limit and `remote:` | Exit 2 in 0.7 s. Stderr: "twicc: --attach: The attached files total 60 MB of inline data; the limit is 50 MB. … pass a path the server reads: remote:<absolute path> over --remote, …". `backend.log` has no RPC request for it. | PASS |
| 7 | `--remote` with `remote:` | same with `--attach remote:/tmp/phase2-qa/sixty.bin` | exit 0; artifact `sixty.bin` | Exit 0 in 0.7 s. Artifact `sixty.bin`, byte-identical (`cmp`). UI: tile `sixty.bin`. | PASS |
| 8 | Internal MCP: path + named data URI | A session of this instance (`17034654-…`) was asked to call `mcp__twicc__send_message` to `<CLAUDE_SID>` with prompt `mcp test` and `attach ['/tmp/phase2-qa/notes.txt', 'data:text/plain;name=hello%20world.txt;base64,SGVsbG8=']` | exit 0; tiles `notes.txt` and `hello world.txt` | Tool result `{"exit_code":0,"result":{"status":"sent",…}}`. The worktree `backend.log` shows the `POST /mcp` calls. Stored line: entries `notes.txt` and `hello world.txt` (both `mode` inline: small text files go natively to Claude). UI: tiles `notes.txt`, `hello world.txt`. | PASS |
| 9 | External MCP: named data URI | Only with an external MCP client connected to this instance | exit 0; tile `hello world.txt`; the McpOperation audit row has no `attach` | Not run: no external MCP client can reach this instance. `mcpBaseUrl` is not set in its synced settings, so the external MCP host route is off. The `McpConnection` rows in its database are copies from the main instance database. | NOT RUN — no external MCP client connected; covered by the automated tests (`tests/test_mcp_attach.py`). |
| 10 | Peers, Git patch → Codex and hybrid | Second instance on this branch, paired; `peer-send` `fix.patch`; deliver to `<CODEX_SID>`, redeliver to `<HYBRID_SID>` | review dialog lists `fix.patch`; delivered message carries `fix.patch` as a file in both sessions | — | NOT RUN — needs the user: a second instance on this branch (§2.3). |
| 11 | Peers, 49 MB then 51 MB | `peer-send` `fortynine.bin`, then `fiftyone.bin` | 49 MB: exit 0 within 468 s; 51 MB: exit 1 `attachments_too_large` with the URL hint, nothing staged | — | NOT RUN — needs the user: a second instance on this branch (§2.3). |
| 12 | Older peer, small file | Pair with an instance on `main`; `peer-send` with `notes.txt` | exit 3 `send_failed`, "may be too old to receive attachments"; a text-only send still succeeds | — | NOT RUN — needs the user: pairing with an instance on `main` (§2.3). |
| 13 | Older peer, 40 MB | `peer-send` with `forty.bin` | exit 3, "refused the message size. An older instance also refuses any attachment." | — | NOT RUN — needs the user (§2.3). |
| 14 | Older peer, 35 MB | `peer-send` with `thirtyfive.bin` | exit 3, the 400 text | — | NOT RUN — needs the user (§2.3). |

Totals: **8 PASS, 0 FAIL, 6 NOT RUN.**

Extra observations from the run:

- **Staging release.** After rows 1–8, `composer-attachments/` holds no file above 1 MB written during the run: the 400 MB, 40 MB and 60 MB staged copies were released after delivery.
- **Hybrid materialization before the paste.** On `7160a372-…`, the send already wrote `att_4e1f507e6b0c.png` and the two artifacts while the CLI waited on its trust dialog: the CLI answer is only needed for the paste itself.

### 2.3 Steps for the NOT RUN rows (for the user)

Re-create the test files first (plan Task 9 Step 3):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && mkdir -p /tmp/phase2-qa && head -c 40M /dev/urandom > /tmp/phase2-qa/forty.bin && head -c 49M /dev/urandom > /tmp/phase2-qa/fortynine.bin && head -c 51M /dev/urandom > /tmp/phase2-qa/fiftyone.bin && head -c 35M /dev/urandom > /tmp/phase2-qa/thirtyfive.bin && printf 'diff --git a/x b/x\n' > /tmp/phase2-qa/fix.patch && printf 'hello\n' > /tmp/phase2-qa/notes.txt
```

**Row 9 (external MCP).** Set `mcpBaseUrl` on this instance and connect an external MCP client to it. Call `send_message` to `<CLAUDE_SID>` with `attach: ["data:text/plain;name=hello%20world.txt;base64,SGVsbG8="]`. Check the tile `hello world.txt` and that the latest `McpOperation` row stores no `attach` argument.

**Rows 10–11 (a second instance on this branch).**

1. Create a second worktree from this branch (example: branch `attach-any-files-peer`, folder `.worktrees/attach-any-files-peer`) and start it: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer && uv run ./devctl.py start --empty-db`.
2. Pair the two instances in Settings → Peers.
3. Row 10, from the second instance: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer && TWICC_DATA_DIR=$PWD uv run twicc peer-send <PEER> 'Patch' 'Apply this' --attach /tmp/phase2-qa/fix.patch`. On this instance, open the review dialog: it lists `fix.patch`. Deliver to `<CODEX_SID>`, then redeliver to `<HYBRID_SID>`. Each delivered message shows a `fix.patch` file tile and the file is in that session's `attachments/`.
4. Row 11, from the second instance: `… peer-send <PEER> 'Big' 'Big file' --attach /tmp/phase2-qa/fortynine.bin` (exit 0 within 468 s), then the same with `fiftyone.bin` (exit 1, `attachments_too_large`, the URL hint; no new entry under `composer-attachments/`).

**Rows 12–14 (an older peer).** Pair this instance with an instance that runs `main` before this branch merges (for example the main instance on `:3500`). From this worktree:

- Row 12: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run twicc peer-send <OLD_PEER> 'Note' 'Small file' --attach /tmp/phase2-qa/notes.txt` → exit 3 `send_failed`, "may be too old to receive attachments". Then the same without `--attach` → success.
- Row 13: same with `--attach /tmp/phase2-qa/forty.bin` → exit 3, "refused the message size. An older instance also refuses any attachment."
- Row 14: same with `--attach /tmp/phase2-qa/thirtyfive.bin` → exit 3, the 400 text.

### 2.4 Observations outside the phase 2 scope

- **Hybrid switch race (exists on `main`).** The UI switch of `7160a372-…` sends `set_session_hybrid` then `send_message`. The switch runs detached (kill → `Session.hybrid=True`). The `send_message` created the agent between the kill and the flag write: `backend.log` shows `ClaudeCodeAgent created` at 11:28:24.929 and `switched to hybrid CLI mode` at 11:28:24.931. That first message ran through the SDK although the session is now flagged hybrid. `main` has the same detached switch and the same frontend order (`MessageInput.vue`, "Commit a staged hybrid switch FIRST"). On this branch, `is_hybrid_switch_pending` makes the attachment planner target the hybrid CLI during that window, but the agent factory reads only the database flag. So a send with attachments in that window can be planned for hybrid and delivered by an SDK agent. Stopping the agent (`twicc session <id> stop`) made the next send start the hybrid CLI. Not run with attachments in the race window.
- **Accidental snippet text.** A misplaced click in the UI inserted the composer snippet "squash" in front of the typed text of the first UI message of `7160a372-…`. The agent answered that `/tmp/phase2-qa` is not a Git repository. No effect on the matrix.
- **Hybrid trust dialog.** `/tmp/phase2-qa` is not trusted by the Claude CLI, so the hybrid CLI of `7160a372-…` waited on its trust dialog. The dialog was not answered. Row 3 was then run on `<HYBRID_SID>`, a hybrid session in this worktree (a folder the CLI already trusts). Its agent then asked a Bash approval (`ls -la` of its `attachments/`), not answered either. Both hybrid agents were stopped at the end.

### 2.5 Cleanup

- Test files in `/tmp/phase2-qa/` deleted (the empty folder stays: it is the project folder of the test sessions).
- `attachments/` of the five test sessions emptied. No session deleted.
- RPC token `phase2-qa` (`tok_e29c796f`) revoked.

## 3. Accepted limitations

From the design (§4.2, §4.4.2, §4.4.3, §4.8.3, §5):

- **Backend restart during a peer POST.** The outbound row stays `pending`.
- **Buffering proxy.** A proxy that buffers a large peer message can end the send in `unreachable` while the receiver stored the message.
- **Two large `peer_send` in one MCP `batch`.** Their transfer timeouts add up and can exceed 600 s.
- **Large `remote:` staging.** A very large `remote:` file can end the CLI in exit 7 (`--remote` read timeout) while the send completes on the server.
- **Old `--remote` client.** A client from before this branch keeps its 30 s read timeout.

## 4. Post-merge reminders

- **Restart the backend** through `devctl.py` (for example `uv run ./devctl.py restart back`). It applies migration `core.0153_peer_message_attachments`. Never run `migrate` by hand.
- **Plugin** version is `0.108.0`: providers reload the updated skills.
- **CHANGELOG** not written. Only on an explicit request.
