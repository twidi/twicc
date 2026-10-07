# Attachments phase 2 (CLI, RPC, MCP, peers) — validation record

Date: 2026-10-07. Branch: `attach-any-files` (base of this record: `99390d6a`).

- Design: `docs/plans/2026-10-06-attachments-phase2-cli-rpc-mcp-peer-design.md` (§7 "Manual")
- Plan: `docs/plans/2026-10-07-attachments-phase2-implementation-plan.md` (Task 9, spec T10)

| Kind | Status |
|---|---|
| Automated tests and build | Run. All pass (§1). |
| Manual matrix, local rows (CLI, `--remote`, hybrid, internal MCP) | Run on the worktree instance (backend `:3501`, Vite `:5174`) with real Claude and Codex. 8 PASS. |
| Manual matrix, peer rows 10–11 (two instances on this branch) | Run on 2026-10-07 between this instance and a second worktree instance. 2 PASS (§2.6). |
| Manual matrix, external MCP and older-peer rows (9, 12–14) | **NOT RUN.** They need a setup that only the user can make (§2.3). |
| Manual matrix, main-instance rows 12–19 | Attempted on 2026-10-07 against the main instance (`:3500`). Row 19 PASS. Rows 12–18 **BLOCKED**: the pairing is refused in both directions (§2.7). |

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
| 3 | Hybrid: image + PDF + text | `<HYBRID_SID>` is a hybrid draft created in the UI (the session switched to hybrid in the UI, `7160a372-…`, stopped at the Claude trust dialog, §2.4). Then `… send-message <HYBRID_SID> 'Describe them.' --attach /tmp/phase2-qa/shot.png --attach /tmp/phase2-qa/spec.pdf --attach /tmp/phase2-qa/notes.txt` | exit 0; image native (`@` reference); PDF and text as files in `attachments/`; the text file is not lost | On `<HYBRID_SID>`: exit 0. `attachments/` holds `spec.pdf` and `notes.txt`. The hybrid dir holds `att_7d314d12fe11.png` (152 bytes, the PNG). The CLI wrote the user line (`entrypoint` cli): entries `shot.png` (`mode` inline, `reference` `att_7d314d12fe11.png`), `spec.pdf` (file), `notes.txt` (file). UI: three tiles `shot.png`, `spec.pdf`, `notes.txt`. On `7160a372-…` (the session switched in the UI) the same send also exited 0 with the same files; its CLI then waited on the Claude trust dialog for `/tmp/phase2-qa`, which was not answered (see §2.4). | PASS |
| 4 | Hybrid command refused | `… send-message 7160a372-… '/help' --attach /tmp/phase2-qa/notes.txt` | exit 3, `rejected`, code `attachments_with_command`, field `attachments` | Exit 3: `"status": "rejected"`, field `attachments`, code `attachments_with_command`, "Attachments cannot be sent with a command". No new file in `attachments/`. | PASS |
| 5 | `--remote` 40 MB | `uv run twicc --remote http://localhost:3501 --remote-token <TOKEN> send-message <CLAUDE_SID> 'remote 40' --attach /tmp/phase2-qa/forty.bin` (token from `twicc token create --name phase2-qa`) | exit 0; artifact `forty.bin` keeps its name | Exit 0 in 1.6 s; one `POST /rpc/send-message` 200. Artifact `forty.bin`, byte-identical to the source (`cmp`). UI: tile `forty.bin`. | PASS |
| 6 | `--remote` 60 MB refused | same with `sixty.bin` | exit 2 before any HTTP call; stderr names the 50 MB limit and `remote:` | Exit 2 in 0.7 s. Stderr: "twicc: --attach: The attached files total 60 MB of inline data; the limit is 50 MB. … pass a path the server reads: remote:<absolute path> over --remote, …". `backend.log` has no RPC request for it. | PASS |
| 7 | `--remote` with `remote:` | same with `--attach remote:/tmp/phase2-qa/sixty.bin` | exit 0; artifact `sixty.bin` | Exit 0 in 0.7 s. Artifact `sixty.bin`, byte-identical (`cmp`). UI: tile `sixty.bin`. | PASS |
| 8 | Internal MCP: path + named data URI | A session of this instance (`17034654-…`) was asked to call `mcp__twicc__send_message` to `<CLAUDE_SID>` with prompt `mcp test` and `attach ['/tmp/phase2-qa/notes.txt', 'data:text/plain;name=hello%20world.txt;base64,SGVsbG8=']` | exit 0; tiles `notes.txt` and `hello world.txt` | Tool result `{"exit_code":0,"result":{"status":"sent",…}}`. The worktree `backend.log` shows the `POST /mcp` calls. Stored line: entries `notes.txt` and `hello world.txt` (both `mode` inline: small text files go natively to Claude). UI: tiles `notes.txt`, `hello world.txt`. | PASS |
| 9 | External MCP: named data URI | Only with an external MCP client connected to this instance | exit 0; tile `hello world.txt`; the McpOperation audit row has no `attach` | Not run: no external MCP client can reach this instance. `mcpBaseUrl` is not set in its synced settings, so the external MCP host route is off. The `McpConnection` rows in its database are copies from the main instance database. | NOT RUN — no external MCP client connected; covered by the automated tests (`tests/test_mcp_attach.py`). |
| 10 | Peers, Git patch → Codex and hybrid | Second instance on this branch, paired; `peer-send` `fix.patch`; deliver to `<CODEX_SID>`, redeliver to `<HYBRID_SID>` | review dialog lists `fix.patch`; delivered message carries `fix.patch` as a file in both sessions | Run on 2026-10-07 with new target sessions (§2.6): exit 0; review lists `fix.patch` (19 B); tile `fix.patch` and artifact `attachments/fix.patch` (byte-identical) in the Codex session and in the hybrid session. | PASS |
| 11 | Peers, 49 MB then 51 MB | `peer-send` `fortynine.bin`, then `fiftyone.bin` | 49 MB: exit 0 within 468 s; 51 MB: exit 1 `attachments_too_large` with the URL hint, nothing staged | Run on 2026-10-07 (§2.6): 49 MB exit 0 in 16 s, stored on the receiver with 51380224 bytes; 51 MB exit 1 in 3.4 s, `attachments_too_large` with the URL hint, no new `composer-attachments/` entry. | PASS |
| 12 | Older peer, small file | Pair with an instance on `main`; `peer-send` with `notes.txt` | exit 3 `send_failed`, "may be too old to receive attachments"; a text-only send still succeeds | — | NOT RUN — needs the user: pairing with an instance on `main` (§2.3). The attempt with the main instance is blocked (§2.7). |
| 13 | Older peer, 40 MB | `peer-send` with `forty.bin` | exit 3, "refused the message size. An older instance also refuses any attachment." | — | NOT RUN — needs the user (§2.3). |
| 14 | Older peer, 35 MB | `peer-send` with `thirtyfive.bin` | exit 3, the 400 text | — | NOT RUN — needs the user (§2.3). |
| 15 | Old sender → new receiver, text only | From the main instance: `peer-send <A> …` without `--attach` | A stores the message; it is in A's inbox | — | BLOCKED — no pairing between this instance and the main instance (§2.7). |
| 16 | Old sender → new receiver, PNG + PDF + text | From the main instance: `peer-send <A> … --attach shot.png --attach spec.pdf --attach notes.txt` | A accepts it; stored row has `attachments` (no `images`/`documents`) with default names `attachment-<n>.<ext>`; the review dialog lists 3 files | — | BLOCKED (§2.7). |
| 17 | Row 16 message delivered to Codex, then to Claude | Deliver on A to a Codex session, then redeliver to a Claude session | Codex: image native, PDF and text in `attachments/`; Claude: all three native | — | BLOCKED (§2.7). |
| 18 | Old sender, Git patch → Codex | From the main instance: `peer-send <A> … --attach fix.patch`; deliver on A to a Codex session | `fix.patch` is a file in `attachments/`; the agent reads it | — | BLOCKED (§2.7). |
| 19 | Migration on real data (read-only) | Peer inbox of this instance: old rows copied from the main instance database | Text shown; attachment count, sizes and default names correct; purged rows say so; no error in `backend.log` | All 42 copied rows have the same text, status, resolution and session link as on the main instance. The 4 old rows with attachments (ids 13, 18, 25, 27) have `attachments_meta` rows `{name, media_type, bytes}` with default names (`attachment-1.png` … `attachment-5.png`, `attachment-1.txt`) and the same sizes and order as the old `kind` rows. No row keeps `images`, `documents` or a `kind` key. The UI shows the texts, "5 attachment(s) — bytes purged." (id 13) and "Its 1 attachment(s) were purged — a new delivery carries the text only." (id 18). No error in `backend.log`. | PASS |

Totals: **11 PASS, 0 FAIL, 4 NOT RUN (9, 12–14), 4 BLOCKED (15–18)** (rows 10–11 added on 2026-10-07, §2.6; rows 15–19 added on 2026-10-07, §2.7).

Extra observations from the run:

- **Staging release.** After rows 1–8, `composer-attachments/` holds no file above 1 MB written during the run: the 400 MB, 40 MB and 60 MB staged copies were released after delivery.
- **Hybrid materialization before the paste.** On `7160a372-…`, the send already wrote `att_4e1f507e6b0c.png` and the two artifacts while the CLI waited on its trust dialog: the CLI answer is only needed for the paste itself.

### 2.3 Steps for the NOT RUN rows (for the user)

Re-create the test files first (plan Task 9 Step 3):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && mkdir -p /tmp/phase2-qa && head -c 40M /dev/urandom > /tmp/phase2-qa/forty.bin && head -c 49M /dev/urandom > /tmp/phase2-qa/fortynine.bin && head -c 51M /dev/urandom > /tmp/phase2-qa/fiftyone.bin && head -c 35M /dev/urandom > /tmp/phase2-qa/thirtyfive.bin && printf 'diff --git a/x b/x\n' > /tmp/phase2-qa/fix.patch && printf 'hello\n' > /tmp/phase2-qa/notes.txt
```

**Row 9 (external MCP).** On this instance (`:5174`), open Settings → External MCP. Set "Dedicated MCP URL" (`mcpBaseUrl`), then turn on "Accept external MCP clients" (`externalMcpEnabled`; it needs a TwiCC password). Connect an external MCP client to `<mcpBaseUrl>/mcp`. Call `send_message` to `<CLAUDE_SID>` with `attach: ["data:text/plain;name=hello%20world.txt;base64,SGVsbG8="]`. Check the tile `hello world.txt` and that the latest `McpOperation` row stores no `attach` argument.

**Rows 10–11 (a second instance on this branch).**

1. Create a second worktree from this branch, then start it:
   ```bash
   cd /home/twidi/dev/twicc-poc && git worktree add .worktrees/attach-any-files-peer -b attach-any-files-peer attach-any-files
   cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer && uv run ./devctl.py start --empty-db
   ```
2. Pair the two instances in Settings → Peers.
3. Resolve `<PEER>` (this instance, as the second instance knows it): `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer && TWICC_DATA_DIR=$PWD uv run twicc peers`. Use its `id` or its exact `name`.
4. Row 10, from the second instance: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer && TWICC_DATA_DIR=$PWD uv run twicc peer-send <PEER> 'Patch' 'Apply this' --attach /tmp/phase2-qa/fix.patch`. On this instance, open the review dialog: it lists `fix.patch`. Deliver to `<CODEX_SID>`, then redeliver to `<HYBRID_SID>`. Each delivered message shows a `fix.patch` file tile and the file is in that session's `attachments/`.
5. Row 11, from the second instance: `… peer-send <PEER> 'Big' 'Big file' --attach /tmp/phase2-qa/fortynine.bin` (exit 0 within 468 s), then the same with `fiftyone.bin` (exit 1, `attachments_too_large`, the URL hint). Check on the sending instance that the refused send left no new entry: `ls -lt /home/twidi/dev/twicc-poc/.worktrees/attach-any-files-peer/composer-attachments/` shows no bucket created by it.

**Rows 12–14 (an older peer).** Pair this instance with an instance that runs `main` before this branch merges (for example the main instance on `:3500`). From this worktree:

- Row 12: `cd /home/twidi/dev/twicc-poc/.worktrees/attach-any-files && TWICC_DATA_DIR=$PWD uv run twicc peer-send <OLD_PEER> 'Note' 'Small file' --attach /tmp/phase2-qa/notes.txt` → exit 3 `send_failed`, "may be too old to receive attachments". Then the same without `--attach` → success.
- Row 13: same with `--attach /tmp/phase2-qa/forty.bin` → exit 3, "refused the message size. An older instance also refuses any attachment."
- Row 14: same with `--attach /tmp/phase2-qa/thirtyfive.bin` → exit 3, the 400 text.

### 2.4 Observations outside the phase 2 scope

- **Hybrid switch race (exists on `main`, now fixed on this branch).** The UI switch of `7160a372-…` sends `set_session_hybrid` then `send_message`. The switch runs detached (kill → `Session.hybrid=True`). The `send_message` created the agent between the kill and the flag write: `backend.log` shows `ClaudeCodeAgent created` at 11:28:24.929 and `switched to hybrid CLI mode` at 11:28:24.931. That first message ran through the SDK although the session is now flagged hybrid, and that SDK agent stayed alive. `main` has the same detached switch and the same frontend order (`MessageInput.vue`, "Commit a staged hybrid switch FIRST"), so the race itself exists on `main`.
  - **Attachment plan mismatch.** Phase 1 (`8542013e`) added the pending-switch set (`is_hybrid_switch_pending`): the attachment planner targeted the hybrid CLI during that window, but the agent factory read only the database flag. A send with attachments in the window was planned for hybrid and delivered by an SDK agent. Phase 2 (`17cfbd7a`) moved that resolution into a shared module, so CLI, RPC and MCP sends had the same mismatch.
  - **Fix (`3b230ef7`).** The switch now kills the agent and writes the flag inside the session's send lane. A send that follows (WS, CLI, RPC or MCP) waits for the flag, so its plan and its agent both target the hybrid CLI. The pending-switch set is removed. Covered by `tests/test_hybrid_switch_send_lane.py`. Not rerun manually.
- **Accidental snippet text.** A misplaced click in the UI inserted the composer snippet "squash" in front of the typed text of the first UI message of `7160a372-…`. The agent answered that `/tmp/phase2-qa` is not a Git repository. No effect on the matrix.
- **Hybrid trust dialog.** `/tmp/phase2-qa` is not trusted by the Claude CLI, so the hybrid CLI of `7160a372-…` waited on its trust dialog. The dialog was not answered. Row 3 was then run on `<HYBRID_SID>`, a hybrid session in this worktree (a folder the CLI already trusts). Its agent then asked a Bash approval (`ls -la` of its `attachments/`), not answered either. Both hybrid agents were stopped at the end.

### 2.5 Cleanup

- Test files in `/tmp/phase2-qa/` deleted (the empty folder stays: it is the project folder of the test sessions).
- `attachments/` of the five test sessions emptied. No session deleted.
- RPC token `phase2-qa` (`tok_e29c796f`) revoked.

### 2.6 Rows 10–11 run (2026-10-07)

**Setup.**

- Instance A: this worktree, backend restarted through `devctl.py stop back` / `start back` (port `3501` free after 1 s, `Uvicorn running` at 12:49:50).
- Instance B: worktree `/home/twidi/dev/twicc-poc/.worktrees/feature-attach-any-files-peer`, branch `feature/attach-any-files-peer` from `6f65f718`, started with `uv run ./devctl.py start --empty-db` (backend `3502`, Vite `5175`).
- Peer addresses: one Cloudflare tunnel hostname per instance, `https://twicc-peer-a.twidi.com` → `localhost:3501` and `https://twicc-peer-b.twidi.com` → `localhost:3502`. Set with `twicc settings set peerBaseUrl …` (and `peerDisplayName` "QA peer A" / "QA peer B") in each worktree. From outside: `/peer/handshake/request/` and `/peer/messages/` answer `405` on GET and `400 invalid_payload` on an empty POST; `/`, `/api/sessions/` and `/static/` answer `404` (origin gate). `localhost:3501` and `localhost:3502` answer `404` on `POST /peer/messages/`.
- Pairing in Settings → Peers: B sent the request to A, A showed the code, B verified it, A accepted. A knows B as `peer_354986b8` ("QA peer B"); B knows A as `peer_8eb9f4a3` ("Peer A"). Both `active`.
- Target sessions on A: "QA peer rows codex" `01a11602-32d1-70f0-89ed-2d6e3a1449b3` (Codex, project `/tmp/phase2-qa`) and "QA peer rows hybrid" `5fb6f8fe-7d56-409f-9b3f-e988495ed220` (Claude, project = this worktree, switched to hybrid in the composer before the send).

Every B command ran as `cd /home/twidi/dev/twicc-poc/.worktrees/feature-attach-any-files-peer && TWICC_DATA_DIR=$PWD uv run twicc …`.

**Row 10 — PASS.**

- `peer-send peer_8eb9f4a3 'Patch' 'Apply this patch file …' --attach /tmp/phase2-qa/fix.patch` → exit 0, `"status": "sent"`, message `pm_5344498bff079d01`. It took 59 s: B was still in its initial sync (see the observation below). A stored it with `attachments_meta` `[{"name": "fix.patch", "media_type": "text/x-diff", "bytes": 19}]`.
- A, peer inbox → review: the attachment list shows `fix.patch`, 19 B.
- "Deliver to an existing session" → "QA peer rows codex": the composer holds the peer message and a ready `fix.patch` chip. After Send: `attachments/fix.patch` (19 bytes, byte-identical to the source); the stored user line has `twicc_attachments` entry `fix.patch`, `kind` text, `mode` file; UI: one tile `fix.patch`. Codex read the file and answered `diff --git a/x b/x`.
- Redelivery from the history entry ("This message was already delivered; delivering it again is allowed") → "QA peer rows hybrid", hybrid switch staged, then Send. `backend.log`: `Stopping agent … (reason: switch-hybrid)`, `switched to hybrid CLI mode`, `Hybrid CLI launched`. `attachments/fix.patch` is byte-identical; the stored user line (`entrypoint` cli) has entry `fix.patch`, `mode` file; UI: one tile `fix.patch`. The agent then asked a Bash approval, not answered; the agent was stopped. B reports the message as `delivered`.

**Row 11 — PASS.**

- `peer-send peer_8eb9f4a3 'Big' … --attach /tmp/phase2-qa/fortynine.bin` (49 MB) → exit 0 in 16 s, message `pm_d3ce9fa306140da4`. A stored it `pending` with `attachments_meta` `fortynine.bin`, `application/octet-stream`, 51380224 bytes.
- `peer-send peer_8eb9f4a3 'Too big' … --attach /tmp/phase2-qa/fiftyone.bin` (51 MB) → exit 1 in 3.4 s: `"status": "validation_error"`, code `attachments_too_large`, "The attached files total 51 MB of inline data; the limit is 50 MB. For a larger file, put it on a file storage service and pass its URL in the message text." The listing of B's `composer-attachments/` is identical before and after this send. Each successful send left only an empty bucket with its `.released` marker.
- Text-only sends, both directions, exit 0: B → A `pm_f8bc81cab72e931a`, A → B (`twicc peer-send peer_354986b8 …` from this worktree) `pm_6f8011141b601402`.

**Observation (outside the phase 2 scope).** The first send from B waited 55 s between the drop-request pickup (12:56:57) and the POST on A (12:57:52). In the same window, B's `backend.log` shows `Slow sync operation=executor_run elapsed_ms=86934.3` from its initial sync. Later sends, after the sync, took 16 s for 49 MB.

**Cleanup.** Test files in `/tmp/phase2-qa/` deleted (the empty folder stays: it is the project folder of the test sessions). `attachments/` of the two new sessions emptied. No session deleted. Instance B, its worktree, the pairing, both Cloudflare hostnames and the pending messages stay in place for the user.

### 2.7 Rows 12–19 (main instance), 2026-10-07

**Setup.**

- Instance A: this worktree (backend `3501`, Vite `5174`, peer address `https://twicc-peer-a.twidi.com`, peer name "QA peer A").
- Main instance: `/home/twidi/dev/twicc-poc` on `main` (`6723c8a0`, not merged with this branch; `_PAYLOAD_KEYS = {"text", "images", "documents"}`, request cap 48 MiB), backend `3500`, data dir `~/.twicc`, peer address `https://twicc-peer.twidi.com`. Both tunnels answer `{"error": "unknown_token"}` on an unauthenticated `POST /peer/messages/`.

**Pairing — BLOCKED in both directions.** The database of this instance is a copy of the main instance database. It holds the main instance's own peer "Self" (`peer_45a03880`, `active`), whose address is `https://twicc-peer.twidi.com`: the main instance's peer address. A peer address is unique per instance (`_peers_matching_origin`, `src/twicc/core/services/peer_mutation.py:102`).

- A → main: Settings → Peers → Add a peer, "QA main", `https://twicc-peer.twidi.com` → "A peer with this address already exists." (`create_peer_and_request`, `peer_mutation.py:219-221`). No request leaves A.
- main → A: Settings → Peers → Add a peer, "QA attach-any-files", `https://twicc-peer-a.twidi.com` → "The remote instance already has a Peer relationship for this address." A's `backend.log`: `POST /peer/handshake/request/ HTTP/1.1" 409` at 15:18:19 (`already_related`, `register_incoming_request`, `peer_mutation.py:807`). The main instance deleted its pending row: its peer list is unchanged (David, Dimitri, Self, Test instance).

This is not a bug of this branch: the collision comes from the copied database. "Self" was not touched. "Revoke" on it would notify the main instance with the shared token and revoke the main instance's own "Self" relationship. Rows 12–18 need one of these, decided by the user:

1. Remove the copy of "Self" from this instance's database only (a local delete, no outbound call), then pair again.
2. Pair this instance with another instance that runs `main` with an empty database, on its own peer address.

**Row 19 — PASS (read-only).** Nothing was delivered, refused, replied to or marked done. The rows were opened in the peer inbox only (the review dialog sends GET requests only).

- Database comparison (both opened read-only with `sqlite3 …?mode=ro`): the main instance has 42 peer messages, this instance 46 (the 42 copies + 4 rows of §2.6). For the 42 copies, `payload.text`, `status`, `resolved_at`, `delivered_to_session_id`, `project_id`, `purged_at` and the attachment count are identical. No row of this instance keeps an `images` or `documents` key, and no `attachments_meta` row keeps a `kind` key.
- The old rows with attachments are all purged: ids 13 (David, outbound, 5 PNG), 18 (Test instance, inbound, 1 text file), 25 and 27 (David, outbound, 1 PNG each). On the main instance their meta rows are `{kind, media_type, bytes}` without a name. On this instance: `GET /api/peer-messages/<id>/?include_attachments=0` returns `{name, media_type, bytes}` rows with `attachment-1.png` … `attachment-5.png` (307001, 290343, 294980, 299977, 261041 bytes), `attachment-1.txt` (1680000 bytes, `text/plain`), `attachment-1.png` (122464 and 124355 bytes): same sizes, same order. `GET /api/peer-messages/18/attachments/` returns `{"attachments": []}`.
- UI (peer inbox on `:5174`): 46 rows; the four rows show "5 (purged)" and "1 (purged)". Id 13: the full Markdown text and "5 attachment(s) — bytes purged.". Id 18: the text and "Its 1 attachment(s) were purged — a new delivery carries the text only." Same texts as on `main` (`PeerMessageReviewDialog.vue`).
- `backend.log`: no `ERROR`, `CRITICAL` or traceback during the run. Migration `core.0153_peer_message_attachments` ran at 11:23:46 (§2.1) without error.

**Browser note.** The Chrome tabs of this run were hidden (`document.visibilityState === "hidden"`): Web Awesome dialogs only opened after a page script finished the frozen Web Animations, and page timers were throttled. No effect on the results.

**Cleanup.** No test file was created and no session was created for this run. The pairing does not exist, so no message was sent in either direction. No session, peer or message was deleted.

## 3. Accepted limitations

From the design (§4.2, §4.4.2, §4.4.3, §4.8.3, §5):

- **Backend restart during a peer POST.** The outbound row stays `pending`.
- **Buffering proxy.** A proxy that buffers a large peer message can end the send in `unreachable` while the receiver stored the message.
- **Two large `peer_send` in one MCP `batch`.** Their transfer timeouts add up and can exceed 600 s.
- **Large `remote:` staging.** A very large `remote:` file can end the CLI in exit 7 (`--remote` read timeout) while the send completes on the server.
- **Old `--remote` client.** A client from before this branch keeps its 30 s read timeout.

## 4. Post-merge reminders

- **Restart the backend** through `devctl.py` (for example `uv run ./devctl.py restart back`). It applies migration `core.0153_peer_message_attachments`. Never run `migrate` by hand.
- **Plugin** version is `0.108.1`: providers reload the updated skills.
- **CHANGELOG** not written. Only on an explicit request.
