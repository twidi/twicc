# Peer Message Threading — Lot 3 Documentation and Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-08-11-peer-threading-design.md` at commit `ba4567ab198d0875af25345929e0255713663c18` is the authority. This plan implements only lot 3 from §18.

**Goal:** Document the shipped peer-threading model and teach agents how to create and inspect replies through the existing CLI and MCP surfaces.

**Architecture:** Repository documentation records the local database relationships and the indeterminate `failed` status. The two existing peer skills mirror the already-shipped CLI and serializer contracts. One minor plugin-version bump refreshes the skill bundle for both providers.

**Tech Stack:** Markdown, Django model comments, Typer command documentation, TwiCC agent skills, JSON plugin metadata, pytest, Ruff.

## Global Constraints

- **Lot boundary:** implement only §18 lot 3, Documentation and skills. Do not change database schema, migrations, serializers, services, CLI behavior, MCP wiring, frontend behavior, package metadata, lockfiles, generated bundles, or `CHANGELOG.md`.
- **Worktree:** every command starts with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && `. Never read or write `/home/twidi/dev/twicc-poc` for this work.
- **Historical design:** read the invariants in `docs/plans/2026-07-24-peer-messaging-design.md`. Never edit that frozen historical file.
- **Settled specification:** do not reopen decisions in `docs/plans/2026-08-11-peer-threading-design.md`. Lots 1, 1.1, and 2 already implement the runtime contract.
- **Subject:** session types outside the peer system remain out of subject. Do not add them to documentation, examples, rejected cases, or skill guidance.
- **Approval gate:** documentation must preserve the receiving human's read-before-delivery gate. `reply_to` never delivers or routes a message.
- **Database invariant:** preserve the `("peer", "direction", "message_id")` unique constraint. Document `(peer_id, thread_id)` only as the local thread key, never as a replacement uniqueness constraint.
- **Identifier contract:** preserve `[A-Za-z0-9_][A-Za-z0-9_-]{0,39}`. A leading hyphen, dot, and colon remain invalid. Do not reopen the settled command-line option-syntax rationale.
- **Reply source:** teach agents to copy the reply id from the header of a delivered peer message. Do not tell the delivery envelope to ask for a reply.
- **Reply target:** `--reply-to` accepts a conforming message id for the selected peer in either direction and any status. It does not accept a thread id or a cross-peer id.
- **Failure semantics:** `failed` means the sender received no confirmed acceptance. The peer may still have stored the message. Never state that `failed` proves the message never reached the peer.
- **Serializer fields:** document `thread_id`, `reply_to`, `reply_to_ref`, and `reply_target` exactly as the existing serializer emits them. Do not change the serializer.
- **Skill conventions:** follow `src/twicc/agent/plugin/README.md`. Keep the shared MCP-preference and `$TWICC` resolver block byte-identical. Do not add a server-prerequisite section or implementation details.
- **Plugin version:** skill changes require one minor bump from `0.68.0` to `0.69.0`. Update the existing version contract in `tests/test_twicc_share_skill.py`. Do not bump more than once.
- **Dependencies:** do not install packages. Use `uv run` for project dependencies. Ruff is absent from project dependencies, so use `uvx` for the focused Ruff check.
- **Language:** all documentation, skill text, comments, test changes, and commit subjects are English.
- **Commits:** one commit per task. Each commit step declares only the worktree, staged files, and Conventional Commit subject. The implementer follows `CLAUDE.md` and `AGENTS.md` for body and trailer rules.

## Existing Interfaces

- Produces: `PeerMessage local end: origin_session for an outbound row; delivered_to_session for an inbound row` from the implemented model and serializer.
- Produces: `PeerMessage reply relation: reply_to_message resolves once within one peer; (peer_id, thread_id) is the local thread key` from the implemented model.
- Produces: `peer-send option --reply-to MESSAGE_ID; local/server errors invalid_reply_to and unknown_reply_to` from the implemented CLI and send service.
- Produces: `peer-message output fields thread_id: string, reply_to: string, reply_to_ref: {message_id, title, direction, status} | null, reply_target: string | null` from the implemented serializer.
- Produces: `failed status: sender received no confirmed acceptance; peer may still have stored the message` from the settled design §11.3.
- Produces: `skill bundle version 0.68.0 and tests/test_twicc_share_skill.py exact version assertion` from the current tree.

## Task map

| Task | Deliverable | Depends on |
|---|---|---|
| 1 | Repository model, CLI, and central CLI documentation | Existing Interfaces |
| 2 | Peer skill guidance, output contract, plugin bump, and focused verification | Existing Interfaces |

---

### Task 1: Document the repository peer-threading contract

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `src/twicc/core/models.py`
- Modify: `src/twicc/cli/peer_message.py`
- Modify: `SKILLS-AND-CLI.md`

**Interfaces:**
- Consumes: `PeerMessage local end: origin_session for an outbound row; delivered_to_session for an inbound row` from Existing Interfaces.
- Consumes: `PeerMessage reply relation: reply_to_message resolves once within one peer; (peer_id, thread_id) is the local thread key` from Existing Interfaces.
- Consumes: `peer-send option --reply-to MESSAGE_ID; local/server errors invalid_reply_to and unknown_reply_to` from Existing Interfaces.
- Consumes: `failed status: sender received no confirmed acceptance; peer may still have stored the message` from Existing Interfaces.
- Produces: repository guidance that points model maintainers and CLI users at the implemented peer-threading contract.

- [ ] **Step 1: Add the detailed `PeerMessage` model note to `CLAUDE.md`**

In `CLAUDE.md`, find this exact line under `## Database Models`:

```markdown
- **`SessionItem`** — one JSONL line; `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind`, `group_head`/`group_tail` for collapsible groups.
```

Insert this bullet immediately after it. Keep the following `ArtifactBookmark` line unchanged:

```markdown
- **`PeerMessage`** — one cross-instance message. The local session link is `origin_session` for an outbound row and `delivered_to_session` for an inbound row. `reply_to_message` resolves the answered row once within the peer relationship. `(peer_id, thread_id)` is the local thread key; `thread_id` never crosses the wire. Design: `docs/plans/2026-08-11-peer-threading-design.md`.
```

- [ ] **Step 2: Add the condensed `PeerMessage` model note to `AGENTS.md`**

In `AGENTS.md`, find the same exact line:

```markdown
- **`SessionItem`** — one JSONL line; `display_level` (ALWAYS/COLLAPSIBLE/DEBUG_ONLY), `kind`, `group_head`/`group_tail` for collapsible groups.
```

Insert this condensed bullet immediately after it:

```markdown
- **`PeerMessage`** — one cross-instance message. Outbound rows link `origin_session`; inbound rows link `delivered_to_session`. `reply_to_message` resolves the parent within its peer. `(peer_id, thread_id)` is the local thread key. Design: `docs/plans/2026-08-11-peer-threading-design.md`.
```

- [ ] **Step 3: Replace the stale model-comment design pointer**

In `src/twicc/core/models.py`, replace this exact comment:

```python
    # LOCAL only, outbound rows: which local session sent it (deferred threading, design §8).
```

with:

```python
    # LOCAL only, outbound rows: which local session sent it (peer threading design §7).
```

Do not modify any model field, `Meta` constraint, or index.

- [ ] **Step 4: Correct the `peer-message` command description**

In `src/twicc/cli/peer_message.py`, replace this exact docstring fragment:

```python
    ``pending`` = awaiting the remote user's approval; ``delivered`` /
    ``refused`` = their resolution; ``failed`` = the send never reached the
    peer (detail in ``error``).
```

with:

```python
    ``pending`` = awaiting the remote user's approval; ``delivered`` /
    ``refused`` = their resolution; ``failed`` = the sender received no
    confirmed acceptance, so the peer may still have stored the message
    (detail in ``error``).
```

This changes Typer help text only. Do not change the query, serializer, status enum, or exit codes.

- [ ] **Step 5: Document `peer-send --reply-to` in the central CLI catalogue**

In `SKILLS-AND-CLI.md`, replace this exact paragraph:

```markdown
Send a titled message to a peer (id or exact local name). `TITLE` is the required subject the remote user triages on — inline text only, one flattened line, 100 chars max (over-long is rejected, never truncated); `PROMPT` is inline text or a file path; `--attach` (repeatable) like `send-message`; `--timeout`. Success returns `{status: "sent", message_id, peer_id, peer_status: "pending"}` — `pending` until the remote user delivers or refuses. Server failures land as `rejected` (exit 3) with the detail in the error code (`peer_broken`, `unreachable`, `send_failed`).
```

with:

```markdown
Send a titled message to a peer (id or exact local name). `TITLE` is the required subject the remote user triages on — inline text only, one flattened line, 100 chars max (over-long is rejected, never truncated); `PROMPT` is inline text or a file path; `--reply-to <MESSAGE_ID>` answers a message of this peer using the id from its delivered-message header; `--attach` (repeatable) works like `send-message`; `--timeout` sets the wait. A malformed id reports `invalid_reply_to`; a conforming id absent from this peer reports `unknown_reply_to`. Success returns `{status: "sent", message_id, peer_id, peer_status: "pending"}` — `pending` until the remote user delivers or refuses. Server failures land as `rejected` (exit 3) with the detail in the error code (`peer_broken`, `unreachable`, `send_failed`).
```

- [ ] **Step 6: Run focused repository-documentation verification**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_peer_cli.py -q
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uvx ruff check --select E4,E7,E9,F src/twicc/core/models.py src/twicc/cli/peer_message.py
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run twicc peer-message --help
```

Expected: pytest and Ruff exit 0. The help output states that `failed` means no confirmed acceptance and that the peer may still have stored the message. It no longer states that the send never reached the peer.

- [ ] **Step 7: Run the Task 1 scope and diff checks**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && git diff --check && git status --short
```

Expected: `git diff --check` exits 0. Before the task commit, `git status --short` lists only `CLAUDE.md`, `AGENTS.md`, `src/twicc/core/models.py`, `src/twicc/cli/peer_message.py`, and `SKILLS-AND-CLI.md`. Any migration, runtime behavior, frontend, skill, plugin, package, lockfile, generated file, or unrelated file blocks the commit.

- [ ] **Step 8: Commit Task 1**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:

```text
CLAUDE.md
AGENTS.md
src/twicc/core/models.py
src/twicc/cli/peer_message.py
SKILLS-AND-CLI.md
```

Commit subject:

```text
docs(peer): document threading contracts
```

---

### Task 2: Update peer skills and refresh the plugin bundle

**Files:**
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`
- Modify: `tests/test_twicc_share_skill.py`

**Interfaces:**
- Consumes: `peer-send option --reply-to MESSAGE_ID; local/server errors invalid_reply_to and unknown_reply_to` from Existing Interfaces.
- Consumes: `peer-message output fields thread_id: string, reply_to: string, reply_to_ref: {message_id, title, direction, status} | null, reply_target: string | null` from Existing Interfaces.
- Consumes: `failed status: sender received no confirmed acceptance; peer may still have stored the message` from Existing Interfaces.
- Consumes: `skill bundle version 0.68.0 and tests/test_twicc_share_skill.py exact version assertion` from Existing Interfaces.
- Produces: agent guidance for sending a reply from a delivered-message header and interpreting the reply relationship and status fields.
- Produces: `skill bundle version 0.69.0 with the existing focused version assertion updated to 0.69.0`.

- [ ] **Step 1: Teach `twicc-peer-send` the reply option and its errors**

In `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`, find this exact options block:

```markdown
### Options

- `--attach PATH` (repeatable) — attach a file: PNG, JPEG, GIF, WebP, PDF, text/plain; 5 MB per file, 100 files / 32 MB per batch. Local path or base64 data URI.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30).
```

Replace it with:

```markdown
### Options

- `--reply-to MESSAGE_ID` — answer a message of this peer; copy the id from the header of the delivered peer message. The id is case-sensitive and can name an inbound or outbound message in any status.
- `--attach PATH` (repeatable) — attach a file: PNG, JPEG, GIF, WebP, PDF, text/plain; 5 MB per file, 100 files / 32 MB per batch. Local path or base64 data URI.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30).
```

Under `### Local (exit 1)`, add these two bullets immediately after the existing `Invalid title` bullet:

```markdown
- `invalid_reply_to` — the reply id does not match the peer-message identifier grammar.
- `unknown_reply_to` — no message with this id exists for the selected peer.
```

Under `### Server (exit 3)`, add this bullet immediately after the existing `not_found` / `peer_broken` / `not_active` bullet:

```markdown
- `invalid_reply_to` / `unknown_reply_to` — the reply id is malformed or does not exist for this peer, re-checked server-side.
```

Do not add thread ids, session ids, automatic routing, or a reply instruction to the delivery envelope.

- [ ] **Step 2: Add one reply example to `twicc-peer-send`**

In the existing `## Examples` bash block, add this command after the two current send commands and before the output comment:

```bash
$TWICC peer-send David --reply-to pm_1a2b3c4d5e6f7a8b 'Follow-up on the API recap' 'One correction to the recap: the endpoint now returns 202.'
```

The example uses the id copied from a delivered-message header. Do not describe a thread id as an accepted value.

- [ ] **Step 3: Expand the `twicc-peer-message` output example**

In `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`, replace this exact JSON example:

```json
{"id": 12, "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4", "direction": "out",
 "title": "API changes recap", "status": "pending", "error": "", "text_preview": "Here is the recap...",
 "attachments_meta": [{"kind": "image", "media_type": "image/png", "bytes": 48211}],
 "origin": {"sent_at": "2026-07-24T12:00:00+00:00"},
 "recipient_note": "", "origin_session_id": "abc123", "delivered_to_session_id": null,
 "origin_session": {"id": "abc123", "title": "Front revamp", "project_id": "-home-me-app"},
 "delivered_to_session": null,
 "created_at": "...", "resolved_at": null, "purged": false}
```

with:

```json
{"id": 12, "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4", "direction": "out",
 "thread_id": "pm_parent000000001", "reply_to": "pm_parent000000001",
 "reply_to_ref": {"message_id": "pm_parent000000001", "title": "Original API question", "direction": "in", "status": "delivered"},
 "reply_target": "session-receiver", "title": "API changes recap", "status": "pending", "error": "",
 "text_preview": "Here is the recap...",
 "attachments_meta": [{"kind": "image", "media_type": "image/png", "bytes": 48211}],
 "origin": {"sent_at": "2026-07-24T12:00:00+00:00"},
 "recipient_note": "", "origin_session_id": "abc123", "delivered_to_session_id": null,
 "origin_session": {"id": "abc123", "title": "Front revamp", "project_id": "-home-me-app"},
 "delivered_to_session": null,
 "created_at": "...", "resolved_at": null, "purged": false}
```

- [ ] **Step 4: Explain the four reply fields and correct `failed`**

Replace the current `status` bullet:

```markdown
- `status` — `pending` (awaiting the remote user), `delivered`, `refused`, or `failed` (never reached the peer; detail in `error`).
```

with these bullets:

```markdown
- `status` — `pending` (awaiting the remote user), `delivered`, `refused`, or `failed`. For `failed`, the sender received no confirmed acceptance; the peer may still have stored the message. See `error` for the local failure detail.
- `thread_id` — the local thread root id. Its complete local key includes `peer_id`; it never crosses the wire.
- `reply_to` — the answered message id, or `""` for a root message.
- `reply_to_ref` — summary of the resolved parent (`message_id`, `title`, `direction`, `status`), or `null`.
- `reply_target` — id of the parent's local-end session, or `null`; it is not a delivery action or eligibility promise.
```

In `## How to present results`, replace this exact first item:

```markdown
1. Translate the status for the user: `pending` = "their user hasn't reviewed it yet", `delivered`/`refused` = their decision, `failed` = the send never reached the peer.
```

with:

```markdown
1. Translate the status for the user: `pending` = "their user hasn't reviewed it yet"; `delivered`/`refused` = their decision; `failed` = "this sender received no confirmed acceptance, but the peer may still have stored it".
```

- [ ] **Step 5: Bump the skill bundle and its existing version assertion**

In `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, replace:

```json
  "version": "0.68.0",
```

with:

```json
  "version": "0.69.0",
```

In `tests/test_twicc_share_skill.py`, replace:

```python
    assert plugin["version"] == "0.68.0"
```

with:

```python
    assert plugin["version"] == "0.69.0"
```

One minor bump covers both skill edits. Do not change another skill or create a new skill.

- [ ] **Step 6: Run the focused skill-content contract**

Run this exact command:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run python - <<'PY'
from pathlib import Path

import orjson

root = Path.cwd()
peer_send = (root / "src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md").read_text()
peer_message = (root / "src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md").read_text()
plugin = orjson.loads(
    (root / "src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json").read_bytes()
)

assert "`--reply-to MESSAGE_ID`" in peer_send
assert "header of the delivered peer message" in peer_send
assert "`invalid_reply_to`" in peer_send
assert "`unknown_reply_to`" in peer_send
assert "`thread_id`" in peer_message
assert "`reply_to`" in peer_message
assert "`reply_to_ref`" in peer_message
assert "`reply_target`" in peer_message
assert "the peer may still have stored the message" in peer_message
assert "the send never reached the peer" not in peer_message
assert plugin["version"] == "0.69.0"
PY
```

Expected: exit 0. Every required skill contract is present, the stale failure claim is absent, and the plugin version is exactly `0.69.0`.

- [ ] **Step 7: Run focused tests and inspect both command surfaces**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run pytest tests/test_twicc_share_skill.py tests/test_peer_cli.py -q
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run twicc peer-send --help
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run twicc peer-message --help
```

Expected: pytest exits 0. `peer-send --help` exposes `--reply-to` and its delivered-header source. `peer-message --help` uses the corrected indeterminate `failed` wording. The commands only inspect the already-shipped surfaces; no server or peer is contacted.

- [ ] **Step 8: Run final scope and diff checks**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && git diff --check && git status --short
```

Expected: `git diff --check` exits 0. Before the task commit, `git status --short` lists only `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`, `src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md`, `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, and `tests/test_twicc_share_skill.py`. The committed Task 1 files do not appear. Any runtime, migration, frontend, package, lockfile, generated, additional skill, or unrelated file blocks the commit.

- [ ] **Step 9: Commit Task 2**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:

```text
src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md
src/twicc/agent/plugin/twicc/skills/twicc-peer-message/SKILL.md
src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
tests/test_twicc_share_skill.py
```

Commit subject:

```text
docs(peer): update threading agent skills
```
