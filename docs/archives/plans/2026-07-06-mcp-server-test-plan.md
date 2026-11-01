# TwiCC MCP Server — Manual / Agent Test Plan

**Goal:** validate the `/mcp` MCP server end-to-end on the `mcp` branch — the parts the
pytest suite (903 tests, all green) cannot cover because they need a *live backend* and
*real spawned agent sessions*.

**Who runs this:** an agent (or a human) working **inside the `mcp` worktree** with shell
access. This document is fully self-contained: it assumes no prior context. Follow it top
to bottom, record PASS/FAIL for each step in the results table at the end.

**Design recap (what you are validating):**
- `/mcp` is a Streamable-HTTP MCP endpoint served by TwiCC's own backend, mounted as raw
  ASGI in front of Django. It exposes the skill-covered CLI surface as MCP tools
  (`mcp__twicc__<command>` on Claude; names use `_` for `/` and `-`).
- Every request needs a Bearer token: a per-session token (`twicc_mcp_<sid>.<sig>`) or a
  PAT (`twicc_pat_…`). No token → 401.
- Tools run **in-process**: reads via the RPC invoker, writes through the dual-mode
  drop-request transport straight into the service handlers — **no drop files**.
- Every tool is available in **every permission mode** and **auto-approved** (no prompt) on
  both providers — it is a control plane, orthogonal to the project's permissions (D9).

**Two testing layers — do not confuse them:**
- **Parts A–C = the raw HTTP endpoint** (a low-level harness). You talk to `/mcp` directly
  with `curl`/`httpx` and a hand-minted token. This is *not* how an agent uses the server;
  it exists only to prove the endpoint's mechanics (auth gate, protocol, no drop files).
- **Part D = the real agent flow.** A live agent (you, or a spawned child) calls the
  `mcp__twicc__*` tools **the way it uses any tool** — no shell, no token handling, TwiCC
  injected the connection for it. This is what actually validates the feature. In Part D,
  any `$TWICC …` shell command is **scaffolding only** (to spawn a test agent or read its
  answer); the thing under test is the **MCP tool call the agent makes**, not the CLI (which
  is unchanged by this work).

---

## 0. Preconditions & setup

> ⚠️ Run **every** command from the worktree root, and set `TWICC_DATA_DIR=$PWD` for any
> ad-hoc Python (otherwise it resolves the *prod* `~/.twicc/` data dir — see CLAUDE.md).

### 0.1 — Confirm you are in the worktree on the `mcp` branch

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
git rev-parse --abbrev-ref HEAD      # expect: mcp
pwd                                   # expect: /home/twidi/dev/twicc-poc/.worktrees/mcp
```

### 0.2 — Ensure the worktree backend is running (with the new `mcp` dep)

The `mcp>=1.27` dependency was added to `pyproject.toml`; devctl's editable rebuild picks it
up on `start`. Check if the backend is up, and start it if not:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
uv run ./devctl.py status
# If backend is not running:
uv run ./devctl.py start all
# Then confirm readiness via the backend log (initial sync can delay the port check):
uv run ./devctl.py logs back --lines=40
```

Expected in `backend.log`: a line `MCP server ready at /mcp`. If instead you see
`MCP server disabled (TWICC_NO_MCP)`, unset that env and restart.

> If you are an agent and are **not** authorized to start servers, stop here and ask the
> human to run `devctl.py start all` in the worktree, then resume.

### 0.3 — Resolve the port, the CLI, and export helpers

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/mcp
# Backend port: devctl writes it into the worktree .env (worktrees default to 3501).
export PORT=$(grep -E '^TWICC_PORT=' .env | cut -d= -f2)
echo "PORT=$PORT"                     # expect a number, e.g. 3501
export MCP_URL="http://127.0.0.1:$PORT/mcp"

# Resolve the twicc CLI (do NOT quote $TWICC when using it — it may be multi-word).
export TWICC="uv run --active twicc"
$TWICC whoami >/dev/null 2>&1; echo "twicc resolves (exit $?)"

# Resolve THIS worktree's project id (needed to spawn sessions bound to it in Part D).
export PROJECT_ID=$($TWICC projects | TWICC_DATA_DIR=$PWD uv run --active python -c \
  "import sys,json,os; d=json.load(sys.stdin); print(next(p['id'] for p in d if p.get('directory')==os.getcwd()))")
echo "PROJECT_ID=$PROJECT_ID"
```

### 0.4 — Helper: mint a session token for an arbitrary session id

The backend signs tokens with `<data_dir>/mcp-secret`. Minting with `TWICC_DATA_DIR=$PWD`
reads the *same* file, so the token is valid against the running backend.

```bash
mint_token() {  # usage: mint_token <session_id>
  TWICC_DATA_DIR=$PWD uv run --active python -c \
    "import sys; from twicc.mcp.identity import mint_session_token; print(mint_session_token(sys.argv[1]))" "$1"
}
```

---

## Part A — Endpoint auth & protocol

### A1 — Unauthenticated request is 401

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `401`.

### A2 — Bad/tampered token is 401

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H 'authorization: Bearer twicc_mcp_whatever.deadbeef' \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `401`.

### A3 — A PAT (API token) passes auth

```bash
# Create a PAT (prints the one-time secret as plain text). Save it.
$TWICC token create --name mcp-test-plan
# Copy the printed twicc_pat_... value into PAT below:
export PAT='twicc_pat_REPLACE_ME'
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$MCP_URL" \
  -H "authorization: Bearer $PAT" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}'
```
**Expect:** `200`. (Clean up later: `$TWICC token revoke <id>` — list with `$TWICC token list`.)

### A4 — Full protocol roundtrip with a session token

Write and run this helper script (uses a persistent HTTP connection, as a real client does):

```bash
cat > /tmp/mcp_roundtrip.py <<'PY'
import os, sys, httpx, json
url = os.environ["MCP_URL"]
tok = sys.argv[1]
H = {"authorization": f"Bearer {tok}", "content-type": "application/json",
     "accept": "application/json, text/event-stream"}
def rpc(c, method, params=None, _id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None: msg["params"] = params
    if _id is not None: msg["id"] = _id
    r = c.post(url, json=msg, headers=H)
    return r
with httpx.Client(timeout=30) as c:
    r = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "plan", "version": "0"}})
    print("initialize:", r.status_code, r.json()["result"]["serverInfo"]["name"])
    rpc(c, "notifications/initialized", _id=None)
    r = rpc(c, "tools/list", {}, 2)
    names = [t["name"] for t in r.json()["result"]["tools"]]
    print("tools/list:", r.status_code, "count =", len(names))
    print("has whoami:", "whoami" in names, "| has create_session:", "create_session" in names)
    print("settings excluded:", not any(n.startswith("settings") for n in names))
    r = rpc(c, "tools/call", {"name": "workspaces", "arguments": {}}, 3)
    sc = r.json()["result"]["structuredContent"]
    print("tools/call workspaces exit_code:", sc["exit_code"])
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_roundtrip.py "$(mint_token some-plan-session)"
```
**Expect:** `initialize: 200 twicc`; `count = 57`; `has whoami: True | has create_session: True`;
`settings excluded: True`; `tools/call workspaces exit_code: 0`.

### A5 — Tool-set sanity (offline, cross-check)

```bash
TWICC_DATA_DIR=$PWD uv run --active python -c "
from twicc.mcp.tools import iter_mcp_tools
ts = iter_mcp_tools(); names = {t.name for t in ts}
print('total:', len(ts))                       # expect 57
print('settings excluded:', not any(n.startswith('settings') for n in names))
for banned in ('password','token','run','claude','codex'):
    assert banned not in names, banned
print('local-only excluded: True; whoami present:', 'whoami' in names)
"
```
**Expect:** `total: 57`, all `True`.

### A6 — Kill switch `TWICC_NO_MCP` (requires a restart; do LAST or skip)

Only if you can restart the worktree backend with an extra env var. Set `TWICC_NO_MCP=1`
in the worktree `.env`, restart, then repeat A1 — **expect `503`** (`MCP server not available`).
**Revert the `.env` change and restart afterwards.** If you cannot safely restart, mark SKIPPED.

---

## Part B — In-process execution / no drop files

### B1 — An MCP mutation creates no drop file

Uses the backend-log discriminator (see the Part D note: an empty drop dir proves nothing —
the watcher only logs for a *file-based* drop).

```bash
before=$(wc -l < logs/backend.log)
cat > /tmp/mcp_mutation.py <<'PY'
import os, sys, httpx
url = os.environ["MCP_URL"]; tok = sys.argv[1]
H = {"authorization": f"Bearer {tok}", "content-type": "application/json",
     "accept": "application/json, text/event-stream"}
with httpx.Client(timeout=30) as c:
    c.post(url, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}, headers=H)
    c.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)
    r = c.post(url, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_workspace","arguments":{"name":"mcp-smoke-ws"}}}, headers=H)
    print(r.json()["result"]["structuredContent"])
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_mutation.py "$(mint_token some-plan-session)"
tail -n +$((before+1)) logs/backend.log | grep -c "DropRequestsWatcher"   # expect 0
```
**Expect:** the tool result shows `'status': 'created'` and a `workspace_id`; **zero**
`DropRequestsWatcher` lines were logged for it (it ran in-process). Verify the workspace
exists, then delete it:
```bash
$TWICC workspaces | grep -i mcp-smoke-ws
$TWICC delete-workspace mcp-smoke-ws          # local-mode cleanup (uses a drop file — that is Part E)
```

---

## Part C — Identity binding

### C1 — A session token resolves to that session in `whoami`

Pick any real session id (from `$TWICC sessions`), mint a token for it, and call `whoami`:
```bash
SID=$($TWICC sessions | TWICC_DATA_DIR=$PWD uv run --active python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
echo "SID=$SID"
cat > /tmp/mcp_whoami.py <<'PY'
import os, sys, httpx
url=os.environ["MCP_URL"]; tok=sys.argv[1]
H={"authorization":f"Bearer {tok}","content-type":"application/json","accept":"application/json, text/event-stream"}
with httpx.Client(timeout=30) as c:
    c.post(url, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}, headers=H)
    c.post(url, json={"jsonrpc":"2.0","method":"notifications/initialized"}, headers=H)
    r=c.post(url, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}, headers=H)
    sc=r.json()["result"]["structuredContent"]
    print("exit:", sc["exit_code"], "| session:", (sc.get("result") or {}).get("session",{}).get("id"))
PY
MCP_URL="$MCP_URL" TWICC_DATA_DIR=$PWD uv run --active python /tmp/mcp_whoami.py "$(mint_token "$SID")"
```
**Expect:** `exit: 0 | session: <SID>` (the whoami tool resolved the identity carried by the token).

---

## Part D — The real agent flow

This is the part that actually validates the feature: **an agent using the `mcp__twicc__*`
tools directly**, the way it uses any tool — no shell, no token, no CLI. The `$TWICC …`
commands that appear here are **scaffolding only** (spawn a test agent, or read its answer);
what is being tested is always the **MCP tool call the agent makes**.

**Verifying "no drop file" — the reliable way.** Do **not** rely on `drop-requests/` being
empty: it looks empty after *any* completed operation, because the CLI deletes its own drop +
status files as soon as it reads the result. The real discriminator is the **backend log** —
the file-based path makes the watcher log `[DropRequestsWatcher] received …` and
`[DropRequestsWatcher] <status> … -> …`, whereas the in-process MCP path never goes through
the watcher. So, around any MCP mutation:

```bash
before=$(wc -l < logs/backend.log)
# … the agent runs the MCP mutation (e.g. mcp__twicc__update_session_title) …
tail -n +$((before+1)) logs/backend.log | grep -c "DropRequestsWatcher"   # expect 0 for MCP
```
Contrast with **Part E**: a CLI mutation from a terminal *does* produce those `DropRequestsWatcher`
lines. Same command, two transports — that difference is the actual proof.

> **How to observe a spawned agent's result:** `$TWICC create-session` prints the new
> `session_id` (the PROMPT is a **positional argument**). Read the agent's answer with
> `$TWICC session <child_id> messages --tail 3`. If the tool's result is present in the
> transcript, the tool ran **and was auto-approved** (no pending approval blocked it).

### D0 — You, the executor, call the tools directly (no scaffolding)

**If the agent running this plan is itself a TwiCC session with the MCP wired** (check: your
tool list contains `mcp__twicc__*` — e.g. `mcp__twicc__whoami`), do the most authentic test
first: just **use the tools**, no `twicc` CLI, no curl.

- Call `mcp__twicc__whoami`. **Expect:** it returns *your own* session id — proving identity
  travels with the connection (no PID tricks, no token you had to supply).
- Call `mcp__twicc__sessions`. **Expect:** the sessions list (a plain read tool).
- Call `mcp__twicc__create_session` with a short prompt to spawn a child, then
  `mcp__twicc__whoami` again. **Expect:** the child is created and recorded with *you* as its
  spawner (`spawned_by`), with no approval step — exactly like invoking a skill.

This single part demonstrates the whole point of the server: the agent drives TwiCC's control
plane through typed tools, autonomously. Everything below only adds *mode/provider coverage*
that D0 cannot reach from a single bypass-mode session.

### D-goal — Goal-oriented (agent chooses the tools itself)

Spawn a child and give it a **goal, without naming any tool**, to confirm the agent reaches
for the MCP tools on its own:

```bash
$TWICC create-session --provider claude_code --permission-mode default --project "$PROJECT_ID" \
  "Without using any shell command, find out your own TwiCC session id and your project id, then spawn a child TwiCC session whose prompt is 'say hello'. Report the ids and the child's id."
```
**Expect** (read with `$TWICC session <child_id> messages --tail 5`): the agent used
`mcp__twicc__whoami` and `mcp__twicc__create_session` to accomplish the goal — it was *not*
told which tools to use. (Optional: apply the backend-log check above to confirm the mutation — if any — ran in-process.)

### D1 — Claude Code (SDK), restrictive mode — auto-approve

The mode-specific checks below *do* name the tool, on purpose: they are deterministic probes
of one behavior (auto-approve in a restrictive mode), not a test of agent autonomy (that is
D0/D-goal).

```bash
# DEFAULT mode (NOT bypass) so the permission callback actually fires.
$TWICC create-session --provider claude_code --permission-mode default --project "$PROJECT_ID" \
  "Call the mcp__twicc__whoami tool, then use mcp__twicc__update_session_title to set this session's title to 'mcp-d1' (session self). Report what each returned."
```
Read with `$TWICC session <child_id> messages --tail 5`; check `$TWICC session <child_id>`
shows the new title. **Expect:** both tools ran with **no approval prompt** in `default` mode; the backend-log check shows **0** `DropRequestsWatcher` lines for the mutation.

### D2 — Claude Code hybrid (tmux) — auto-approve card

Same as D1 but with a **hybrid (tmux) Claude session** (driven by the `terminalUseTmux`
setting / your tmux-CLI launch path, not a `create-session` flag), in `default` mode. **This
is the key check for the hybrid auto-approve fix:** the MCP tools must run with **no approval
card**. Then verify **token survival across a restart:** mid-session, restart the backend
(`devctl.py restart back`), send the session a new prompt to call `mcp__twicc__whoami` — it
must still succeed (the token is deterministic, baked into the session's `mcp-config` file).

### D3 — Codex, non-yolo mode — canonical identity + auto-approve

```bash
$TWICC create-session --provider codex --permission-mode auto --project "$PROJECT_ID" \
  "Call the twicc whoami MCP tool, then use the twicc update_session_title tool to set this session's title to 'mcp-d3' (session self). Report both results."
```
**Expect:** `whoami` returns the **canonical** session id (proves the draft→canonical alias);
the title changes with **no approval prompt** (validates `default_tools_approval_mode="approve"`); the backend-log check shows **0** `DropRequestsWatcher` lines.

### D4 — Codex read-only / strict mode can still call a write tool (D9)

Spawn a Codex session in `read_only`/strict mode and prompt it to run the
`update_session_title` MCP tool on itself. **Expect:** it works — MCP is a control plane
available in every mode, **even though the same session cannot run the `twicc` CLI at all**
(no shell execution in read-only). This is the capability skills/CLI cannot provide.

---

## Part E — CLI regression (local mode still uses drop files)

From a plain terminal (a real `twicc` process talking to the backend — `backend_loop` unset):
```bash
$TWICC create-workspace mcp-cli-smoke      # should print status: created
$TWICC workspaces | grep -i mcp-cli-smoke
$TWICC delete-workspace mcp-cli-smoke      # status: deleted
```
**Expect:** both succeed. (Optional: while a slow command runs, observe a transient
`<uuid>.json` in `drop-requests/` — the local path is unchanged.)

---

## Part F — Codex context mode (`TWICC_MCP_CODEX_DEFER`)

- **Default (`True`, deferred):** in D3, the Codex thread started cleanly and the `whoami`
  tool resolved on demand → PASS by virtue of D3 passing.
- **Eager fallback:** temporarily set `TWICC_MCP_CODEX_DEFER = False` in
  `src/twicc/mcp/__init__.py`, restart the backend, re-run D3. **Expect:** still works (Codex
  loads all schemas eagerly). **Revert the constant and restart afterwards.**

---

## Part G — Existing surfaces intact (regression)

- SPA loads in the browser at `http://127.0.0.1:<frontend_port>/`.
- `/rpc/` still works: `curl -s -X POST "http://127.0.0.1:$PORT/rpc/status" -H "authorization: Bearer $PAT" | head -c 200`.
- Artifacts serving and the terminal WebSocket still work (open a session's Artifacts and
  Terminal tabs).

---

## Results

| ID | Check | Result | Notes |
|----|-------|--------|-------|
| A1 | 401 no token | | |
| A2 | 401 bad token | | |
| A3 | 200 with PAT | | |
| A4 | protocol roundtrip (57 tools, workspaces exit 0) | | |
| A5 | tool-set sanity (offline) | | |
| A6 | TWICC_NO_MCP → 503 | | (skip if no restart) |
| B1 | MCP mutation, no drop file | | |
| C1 | token → whoami identity | | |
| D0 | Executor uses mcp__twicc__* directly (whoami/sessions/create_session) | | |
| D-goal | Goal-oriented: agent picks MCP tools itself | | |
| D1 | Claude SDK default: whoami+mutation, no prompt | | |
| D2 | Claude hybrid: no card + token survives restart | | |
| D3 | Codex auto: canonical id + mutation, no prompt | | |
| D4 | Codex read-only can call a write tool | | |
| E  | CLI local mode still works (drop files) | | |
| F  | Codex defer default + eager fallback | | |
| G  | SPA / /rpc/ / artifacts / terminal intact | | |

**Cleanup checklist:** revert any `.env` (`TWICC_NO_MCP`) and `__init__.py`
(`TWICC_MCP_CODEX_DEFER`) changes + restart; revoke the test PAT (`$TWICC token revoke <id>`);
delete the test **workspaces** you created (`$TWICC delete-workspace <id>`); **hide/archive**
the test **sessions** you spawned (`$TWICC update-session <id> hide` / `$TWICC update-session
<id> archive`) — never delete a session or its JSONL; `rm /tmp/mcp_*.py`.
