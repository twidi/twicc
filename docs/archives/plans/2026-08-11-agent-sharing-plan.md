# Agent-Created Shares Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-08-10-agent-sharing-design.md` (the authority for every behaviour; section references like §7.2 point there).

**Goal:** Open TwiCC's share feature (read-only public links to sessions/artifacts) to agents behind two global opt-in settings, with a spawn-subtree scope rule, a provenance column, an agent-payload shape contract — and fix the six pre-existing share defects the feature exposes.

**Architecture:** The gate lives server-side in the six `*_from_payload` wrappers of `core/services/share_mutation.py` (the only entry for CLI/MCP/`/rpc/` mutations); the owner REST UI keeps bypassing it. Caller identity is `resolve_current_session()` carried as a `caller_session_id` payload field (best-effort guardrail, not a security boundary — §5.2). Reads redact tokens client-side in `cli/share.py`. New pure modules: `core/services/share_agent_gate.py` (shape contract), `core/services/spawn_scope.py` (descendants BFS), `core/services/share_url.py` (URL parity builder mirrored in JS).

**Tech Stack:** Django 6 ASGI + SQLite, Typer CLI, drop-request transport, Vue 3 + Pinia, pytest + node:test.

## Global Constraints

- **ONE single lot.** The whole plan ships in one pass (spec §1, user instruction). Tasks below are internal structure with per-task commits — they are NOT independently shippable increments and must not be re-split into sub-lots.
- **Worktree:** everything runs in `/home/twidi/dev/twicc-poc/.worktrees/peer-system` (branch `peer-system`). Prefix EVERY shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && `. For any `python -m django` invocation also set `TWICC_DATA_DIR=$PWD` and sanity-check the DB path first (CLAUDE.md "Worktrees").
- **Tests:** backend tests normally use `uv run --active pytest <file> -x -q` (the `--active` flag is required in this worktree — without it the main checkout's venv wins and tests run against `main`'s sources). A step may explicitly omit `-x` when it must show several expected outcomes in one run; Tasks 1 and 5 do this. Frontend commands start with the mandatory worktree prefix, then enter the package: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test` (node:test, auto-discovers `src/**/*.test.js`).
- **Never** run `devctl` start/stop/restart, never `migrate` a database, never `npm install`/`npm ci` (user-reserved operations). Creating a migration *file* with `makemigrations` is required (Task 11); applying it is the user's.
- **Never** touch `CHANGELOG.md` (explicit user rule: no entry without an explicit ask).
- **Language:** every written artifact — code, comments, docstrings, skill text, docs — in English.
- **Commits:** one per task. Each declarative commit step gives only the worktree `cd`, the files to stage, and a Conventional Commit subject. The implementer follows the repository conventions in `CLAUDE.md` / `AGENTS.md`. Never mention the plugin version in a commit subject.
- **Historical documents are frozen:** never edit `docs/plans/2026-07-05-sharing-design.md` or any dated CHANGELOG release section.
- **Error type:** every business refusal is a `ShareError(field, code, message)` (`src/twicc/core/services/share_mutation.py:42`) — no new transport, no new status (§7.5).
- **Settings reads:** always through `read_synced_settings()` at call time — never snapshot per session (§4).
- **Copy rules:** the two settings switches carry the exact consent wording of §4 (quoted verbatim in Task 2). The `twicc-share` skill must NOT document `--max-display debug` (§12).
- **Plugin:** any skill add/edit requires bumping `version` in `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` — done once, in Task 17 (0.67.0 → 0.68.0, minor).

## Task map (dependency order)

| # | Task | Depends on |
|---|---|---|
| 1 | Backend settings keys + CLI descriptions | — |
| 2 | Frontend settings plumbing, switches, help page | 1 |
| 3 | `spawn_scope.py` extraction | — |
| 4 | URL parity builder pair + shared fixture | — |
| 5 | Shared repairs: artifact title options + strict expiry | — |
| 6 | `share_id` in `build_final` | — |
| 7 | Cross-kind list filters | 4 (continues the `cli/share.py` URL-builder integration) |
| 8 | Tri-state `--live/--frozen` | — |
| 9 | `caller_session_id` CLI plumbing | 8 (appends to Task 8's test file, reuses its `captured_drop`/`_invoke`) |
| 10 | `share_agent_gate.py` shape contract (pure) | — |
| 11 | `created_by_session` column, migration, serializer | 5, 7 (continues the repaired mutation/owner-API state and cross-kind query state) |
| 12 | Gate wiring in the six wrappers | 1, 3, 5, 8, 9, 10, 11 |
| 13 | Read redaction in `cli/share.py` | 1, 4, 7, 10, 11 (appends to Task 7's test file and continues Task 11's eager creator query state) |
| 14 | Owner UI creator badge | 11 |
| 15 | `self`/`parent` keywords + remote preflight | 7, 8, 11, 13 (continues Task 8's tri-state declaration; the composed test extends Task 7's file, uses Task 13's settings fixture, and marks the artifact with Task 11 provenance) |
| 16 | MCP exposure + bare-group fix + stale MCP docs | 6, 8, 9, 12, 15 (proves Task 6's result on the MCP surface; gate must exist before tools ship; bare-group test preserves Task 9's caller cases in Task 8's file) |
| 17 | Skills, docs, plugin bump, final sweep | all |

---

### Task 1: Backend settings — two gate keys

**Files:**
- Modify: `src/twicc/synced_settings.py` (defaults dict, entry `"shareBaseUrl"` currently ends at line ~107)
- Modify: `src/twicc/cli/settings/_keys.py:22-37` (`GENERIC_KEY_DESCRIPTIONS`)
- Test: `tests/test_settings_cli.py`

**Interfaces:**
- Produces: synced settings keys `allowAgentSessionShares` / `allowAgentArtifactShares`, both `False` by default, classified "generic" (settable via `twicc settings set` — the A17 self-flip surface). Read later via `read_synced_settings().get("allowAgentSessionShares", False)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_settings_cli.py`:

```python
def test_agent_share_settings_default_off():
    """The two agent-sharing gate keys exist, default OFF (spec §4/A2)."""
    from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS

    assert SYNCED_SETTINGS_DEFAULTS["allowAgentSessionShares"] is False
    assert SYNCED_SETTINGS_DEFAULTS["allowAgentArtifactShares"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_settings_cli.py -x -q`
Expected: FAIL with `KeyError: 'allowAgentSessionShares'`.

- [ ] **Step 3: Add the defaults**

In `src/twicc/synced_settings.py`, locate this exact block (the `shareBaseUrl` entry and the first line of the `peerBaseUrl` comment):

```python
    "shareBaseUrl": "",
    # Public base URL advertised to peer instances (peer messaging). Empty
```

and insert between those two lines (comment wording from spec §4):

```python
    # Let agents create and manage session shares (skill + MCP + CLI from inside a
    # session). Off: those calls are refused with `agent_sharing_disabled`.
    "allowAgentSessionShares": False,
    # Same, for artifact shares. The two kinds are independent.
    "allowAgentArtifactShares": False,
```

- [ ] **Step 4: Run the suite — expect the invariant guard to fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_settings_cli.py -q`
Expected: `test_agent_share_settings_default_off` PASSES, while `test_generic_key_descriptions_match_generic_keys` FAILS (a new generic key without a `--help` description — the exact-equality guard the spec names in §4). The command omits `-x` so both outcomes are visible despite the guard appearing earlier in the file.

- [ ] **Step 5: Add the CLI descriptions**

In `src/twicc/cli/settings/_keys.py`, append to `GENERIC_KEY_DESCRIPTIONS` — insert after this exact line (the dict's current last entry, just before the closing `}`):

```python
    "telemetryNoticeSeen": "One-time telemetry notice acknowledged.",
```

the two new entries:

```python
    "allowAgentSessionShares": "Let agents create session shares in their own spawn subtree, revoke any session share, and read every session share URL.",
    "allowAgentArtifactShares": "Let agents create artifact shares in their own spawn subtree, revoke any artifact share, and read every artifact share URL.",
```

(These one-liners disclose the same three powers as the §4 switch copy: create/manage in subtree, revoke-anything, read-all.)

- [ ] **Step 6: Run the full settings test file**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_settings_cli.py -q`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/synced_settings.py src/twicc/cli/settings/_keys.py tests/test_settings_cli.py`
Subject: `feat(share): add agent sharing settings`
```

---

### Task 2: Frontend settings plumbing, switches, help page

**Files:**
- Modify: `frontend/src/constants.js` (`SYNCED_SETTINGS_KEYS`, ~line 204)
- Modify: `frontend/src/stores/settings.js` (`SETTINGS_SCHEMA` ~line 24, `SETTINGS_VALIDATORS` ~line 107, getters ~line 315, actions ~line 546, `collectAllSyncedSettings` ~line 1120)
- Modify: `frontend/src/components/app/SettingsPopover.vue` (Sharing section, ~line 1565)
- Modify: `frontend/public/help/sharing.md`
- Test: `frontend/src/stores/settingsAgentShares.test.js` (create)

**Interfaces:**
- Consumes: Task 1's synced settings keys `allowAgentSessionShares: bool` and `allowAgentArtifactShares: bool`.
- Produces: store state `allowAgentSessionShares` / `allowAgentArtifactShares`, getters `isAllowAgentSessionShares` / `isAllowAgentArtifactShares`, actions `setAllowAgentSessionShares(enabled)` / `setAllowAgentArtifactShares(enabled)`, the exact §4 switch consent copy, and the updated `frontend/public/help/sharing.md` agent-setting disclosures.

**Note on the test shape:** `settings.js` cannot be imported under `node --test` (extensionless imports like `from '../constants'` fail outside Vite — verified). `constants.js` IS importable. So the test imports `SYNCED_SETTINGS_KEYS` for a real assertion and scans `settings.js` source text for the five registration points — a registration guard in the spirit of the backend's exact-equality test, covering §14 "Settings plumbing" (schema, validators, sync round-trip registration).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/stores/settingsAgentShares.test.js`:

```js
// Registration guard for the two agent-sharing gate keys (agent-sharing
// design §4 "Plumbing"). settings.js is not importable under node --test
// (extensionless imports), so the store-side registration points are
// asserted on the source text; constants.js is imported for real.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { SYNCED_SETTINGS_KEYS } from '../constants.js'

const KEYS = ['allowAgentSessionShares', 'allowAgentArtifactShares']
const settingsSource = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

test('both keys are synced', () => {
    for (const key of KEYS) assert.ok(SYNCED_SETTINGS_KEYS.has(key), key)
})

test('both keys are registered at every store point', () => {
    for (const key of KEYS) {
        // SETTINGS_SCHEMA placeholder (synced keys use null).
        assert.match(settingsSource, new RegExp(`${key}: null,`), `${key} in SETTINGS_SCHEMA`)
        // Boolean validator.
        assert.match(settingsSource, new RegExp(`${key}: \\(v\\) => typeof v === 'boolean'`), `${key} validator`)
        // Outgoing sync payload.
        assert.match(settingsSource, new RegExp(`${key}: store\\.${key},`), `${key} in collectAllSyncedSettings`)
    }
    // Getters and setters.
    assert.match(settingsSource, /isAllowAgentSessionShares/, 'session getter')
    assert.match(settingsSource, /isAllowAgentArtifactShares/, 'artifact getter')
    assert.match(settingsSource, /setAllowAgentSessionShares\(enabled\)/, 'session setter')
    assert.match(settingsSource, /setAllowAgentArtifactShares\(enabled\)/, 'artifact setter')
})

test('the switch copy carries both consent disclosures, per switch (§4/§14)', () => {
    const popoverSource = readFileSync(
        new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
    for (const kind of ['session', 'artifact']) {
        assert.match(popoverSource,
            new RegExp(`revoke any existing ${kind}\\s+share, including links created by you`),
            `${kind}: revoke-anything disclosure`)
        assert.match(popoverSource,
            new RegExp(`read the URL of every existing\\s+${kind} share, including links created by you or by another agent`),
            `${kind}: read-all disclosure`)
    }
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/stores/settingsAgentShares.test.js`
Expected: all three tests FAIL — neither the registration nor the switch copy exists yet.

- [ ] **Step 3: Register the keys**

1. `frontend/src/constants.js` — in `SYNCED_SETTINGS_KEYS`, insert after this exact line (~line 212):
   ```js
    'notifyOnExtraUsageStart',
   ```
   the line:
   ```js
    'allowAgentSessionShares', 'allowAgentArtifactShares',
   ```
2. `frontend/src/stores/settings.js`:
   - `SETTINGS_SCHEMA`, in the synced block — insert after this exact line (~line 77):
     ```js
    notifyOnExtraUsageStart: null,
     ```
     the block:
     ```js
    // Agent-created shares (design 2026-08-10): opt-in gates, default off.
    allowAgentSessionShares: null,
    allowAgentArtifactShares: null,
     ```
   - `SETTINGS_VALIDATORS` — insert after this exact line (~line 122):
     ```js
    notifyOnExtraUsageStart: (v) => typeof v === 'boolean',
     ```
     the block:
     ```js
    allowAgentSessionShares: (v) => typeof v === 'boolean',
    allowAgentArtifactShares: (v) => typeof v === 'boolean',
     ```
   - Getters — insert after this exact line (~line 315):
     ```js
        isAutoUnpinOnArchive: (state) => state.autoUnpinOnArchive,
     ```
     the block:
     ```js
        isAllowAgentSessionShares: (state) => state.allowAgentSessionShares === true,
        isAllowAgentArtifactShares: (state) => state.allowAgentArtifactShares === true,
     ```
   - Actions — insert after this exact block (~line 546):
     ```js
        setAutoUnpinOnArchive(enabled) {
            if (SETTINGS_VALIDATORS.autoUnpinOnArchive(enabled)) {
                this.autoUnpinOnArchive = enabled
            }
        },
     ```
     the block:
     ```js
        setAllowAgentSessionShares(enabled) {
            if (SETTINGS_VALIDATORS.allowAgentSessionShares(enabled)) {
                this.allowAgentSessionShares = enabled
            }
        },
        setAllowAgentArtifactShares(enabled) {
            if (SETTINGS_VALIDATORS.allowAgentArtifactShares(enabled)) {
                this.allowAgentArtifactShares = enabled
            }
        },
     ```
   - `collectAllSyncedSettings()` dict — insert after this exact line (~line 1170):
     ```js
            shareBaseUrl: store.shareBaseUrl,
     ```
     the block:
     ```js
            allowAgentSessionShares: store.allowAgentSessionShares,
            allowAgentArtifactShares: store.allowAgentArtifactShares,
     ```

- [ ] **Step 4: Run the test — registration tests pass, copy test still fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/stores/settingsAgentShares.test.js`
Expected: the two registration tests ("both keys are synced", "both keys are registered at every store point") PASS; the third test ("the switch copy carries both consent disclosures") still FAILS — the copy only lands in `SettingsPopover.vue` in Step 5.

- [ ] **Step 5: Add the two switches to the Sharing section**

In `frontend/src/components/app/SettingsPopover.vue`, Sharing section (`<!-- Sharing Section -->`, ~line 1565), locate this exact block (the "Shared links" `setting-group`, right after the share-host group's closing `</div>`):

```html
                    <div class="setting-group">
                        <wa-button size="small" variant="neutral" appearance="accent" @click="showShareManager = true">
                            <wa-icon name="share-nodes" slot="start"></wa-icon>
                            Shared links
                        </wa-button>
                    </div>
```

and insert BEFORE it (the §4 hint copy below is **required wording — verbatim**, only `<kind>` substituted):

```html
                    <div class="setting-group">
                        <label class="setting-group-label">Agent sharing <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="allowAgentSessionShares"
                            @change="onAllowAgentSessionSharesChange"
                            size="small"
                        >Session shares</wa-switch>
                        <span class="setting-group-hint">
                            Allows agents to create session shares whose target belongs to their own
                            spawn subtree, and to manage session shares created by agents in their own
                            spawn subtree. When enabled, agents can also revoke any existing session
                            share, including links created by you, and read the URL of every existing
                            session share, including links created by you or by another agent.
                        </span>
                        <wa-switch
                            :checked="allowAgentArtifactShares"
                            @change="onAllowAgentArtifactSharesChange"
                            size="small"
                        >Artifact shares</wa-switch>
                        <span class="setting-group-hint">
                            Allows agents to create artifact shares whose target belongs to their own
                            spawn subtree, and to manage artifact shares created by agents in their own
                            spawn subtree. When enabled, agents can also revoke any existing artifact
                            share, including links created by you, and read the URL of every existing
                            artifact share, including links created by you or by another agent.
                        </span>
                    </div>
```

In the `<script setup>`, insert the computeds after this exact line (~line 457):

```js
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
```

and the handlers next to `onAutoUnpinOnArchiveChange` (~line 1048) — copy that exact pattern:

```js
const allowAgentSessionShares = computed(() => store.isAllowAgentSessionShares)
const allowAgentArtifactShares = computed(() => store.isAllowAgentArtifactShares)

function onAllowAgentSessionSharesChange(event) {
    store.setAllowAgentSessionShares(event.target.checked)
}

function onAllowAgentArtifactSharesChange(event) {
    store.setAllowAgentArtifactShares(event.target.checked)
}
```

Then re-run the Step 1 test file — all three tests must now PASS:
`cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/stores/settingsAgentShares.test.js`

- [ ] **Step 6: Extend the in-product help page**

In `frontend/public/help/sharing.md`, add a section at the end of "## Your links" — insert between these two exact lines (the section's last bullet and the next heading):

```markdown
- **Delete** removes it permanently, including its view logs.

## Artifacts and network access
```

the new section (§12: the one human-facing document reachable from the switches; content must be consistent with the switch copy):

```markdown
## Agents and share links

By default, only you can create or manage share links — agents cannot.
Two switches in **Settings → Sharing** change that, one per kind (session
links, artifact links). Both are **off** until you enable them.

Enabling **Session shares** lets agents create session links for their own
session or any session in their spawn subtree. Enabling **Artifact shares**
lets agents create artifact links for bookmarks owned by their own session
or any session in their spawn subtree.

For the enabled kind, agents can also:

- **manage** (update, delete, re-publish) links created by themselves or by
  agents in their spawn subtree;
- **revoke** any existing link of that kind — including links you created
  yourself (un-publishing is always considered safe);
- **read the URL** of every existing link of that kind, including yours.

Agents can never clear a link's password, never share with the `debug`
display mode, and their new session links are frozen snapshots unless they
explicitly ask for a live link.
```

- [ ] **Step 7: Run the full frontend suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `frontend/src/constants.js frontend/src/stores/settings.js frontend/src/components/app/SettingsPopover.vue frontend/public/help/sharing.md frontend/src/stores/settingsAgentShares.test.js`
Subject: `feat(share): add agent sharing controls`
```

---

### Task 3: `spawn_scope.py` — descendants BFS extraction

**Files:**
- Create: `src/twicc/core/services/spawn_scope.py`
- Modify: `src/twicc/cli/_drop_request/whoami.py` (`resolve_descendants_filter`, ~lines 201-296)
- Test: `tests/test_spawn_scope.py` (create)

**Interfaces:**
- Produces: `descendant_ids(session_id: str) -> set[str]` — sync ORM, proper descendants only (target excluded), unknown id → `set()`. The gate (Task 12) calls it via `sync_to_async`; `resolve_descendants_filter` delegates to it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spawn_scope.py`:

```python
"""descendant_ids: the spawn-subtree resolver shared by the CLI filters and
the share agent gate (design §6)."""

import pytest

from twicc.core.models import Project, Session, SessionType


def _mk(project, sid, *, spawned_by=None, spawn_root=None, parent_session=None):
    return Session.objects.create(
        id=sid, project=project, provider="claude_code",
        file_path=f"{sid}.jsonl",
        type=SessionType.SUBAGENT if parent_session else SessionType.SESSION,
        spawned_by=spawned_by, spawn_root=spawn_root, parent_session=parent_session,
    )


def _tree(db):
    project = Project.objects.create(id="-tmp-scope", directory="/tmp/scope")
    root = _mk(project, "root")
    root.spawn_root = root
    root.save(update_fields=["spawn_root"])
    a = _mk(project, "a", spawned_by=root, spawn_root=root)
    b = _mk(project, "b", spawned_by=a, spawn_root=root)
    c = _mk(project, "c", spawned_by=root, spawn_root=root)
    return project, root, a, b, c


def test_descendants_of_root(transactional_db):
    _tree(transactional_db)
    from twicc.core.services.spawn_scope import descendant_ids
    assert descendant_ids("root") == {"a", "b", "c"}


def test_descendants_of_mid_tree_branch_only(transactional_db):
    _tree(transactional_db)
    from twicc.core.services.spawn_scope import descendant_ids
    assert descendant_ids("a") == {"b"}


def test_leaf_and_lone_session_have_no_descendants(transactional_db):
    project, *_ = _tree(transactional_db)
    _mk(project, "lone")
    from twicc.core.services.spawn_scope import descendant_ids
    assert descendant_ids("b") == set()
    assert descendant_ids("lone") == set()


def test_unknown_id_is_empty(transactional_db):
    from twicc.core.services.spawn_scope import descendant_ids
    assert descendant_ids("nope") == set()


def test_claude_subagent_is_not_a_descendant(transactional_db):
    """Subagents carry parent_session, not spawned_by — outside the spawn
    tree by design (§6 'Subagents are out')."""
    project, root, *_ = _tree(transactional_db)
    _mk(project, "sub", parent_session=root)
    from twicc.core.services.spawn_scope import descendant_ids
    assert "sub" not in descendant_ids("root")


def test_resolve_descendants_filter_delegates(monkeypatch):
    """The explicit-id branch calls the shared helper directly."""
    calls = []

    def fake_descendant_ids(session_id):
        calls.append(session_id)
        return {"sentinel"}

    monkeypatch.setattr(
        "twicc.core.services.spawn_scope.descendant_ids", fake_descendant_ids,
    )
    from twicc.cli._drop_request.whoami import resolve_descendants_filter
    assert resolve_descendants_filter("root") == {"sentinel"}
    assert calls == ["root"]


def test_resolve_descendants_filter_keyword_branches(transactional_db, monkeypatch):
    """Both public keywords resolve a target before common delegation."""
    _project, root, a, b, _c = _tree(transactional_db)
    from twicc.cli._drop_request.whoami import resolve_descendants_filter

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: a,
    )
    assert resolve_descendants_filter("self") == {"b"}

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: b,
    )
    assert resolve_descendants_filter("parent") == {"b"}

    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: root,
    )
    with pytest.raises(RuntimeError, match="no spawned_by"):
        resolve_descendants_filter("parent")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_spawn_scope.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'twicc.core.services.spawn_scope'`.

- [ ] **Step 3: Create the module**

Create `src/twicc/core/services/spawn_scope.py`:

```python
"""Spawn-subtree resolution shared by the CLI ``--descendants`` filter and the
share agent gate (agent-sharing design §6).

Sync ORM — async callers wrap in ``sync_to_async``. On consistently
denormalised ``spawn_root`` data, this preserves the resolver's
proper-descendant contract; unknown ids return an empty set.
"""

from __future__ import annotations


def descendant_ids(session_id: str) -> set[str]:
    """Proper spawn-tree descendants of ``session_id`` (the session itself is
    never included). Unknown id → empty set. Claude subagents (``parent_session``
    edge, ``spawned_by`` NULL) are not spawn-tree members and never appear."""
    from twicc.core.models import Session

    try:
        session = Session.objects.only("id", "spawn_root_id").get(pk=session_id)
    except Session.DoesNotExist:
        return set()
    target_id = session.id
    tree_key = session.spawn_root_id or session.id

    rows = list(
        Session.objects.filter(spawn_root_id=tree_key).only("id", "spawned_by_id")
    )

    if tree_key == target_id:
        # Target is the tree root → every other row is a descendant.
        return {r.id for r in rows if r.id != target_id}

    # Target is mid-tree → BFS its branch to drop sibling/parent rows.
    adj: dict[str, list[str]] = {}
    for r in rows:
        if r.spawned_by_id:
            adj.setdefault(r.spawned_by_id, []).append(r.id)

    out: set[str] = set()
    stack = list(adj.get(target_id, ()))
    while stack:
        node = stack.pop()
        if node in out:
            continue
        out.add(node)
        stack.extend(adj.get(node, ()))
    return out
```

- [ ] **Step 4: Delegate from `resolve_descendants_filter`**

In `src/twicc/cli/_drop_request/whoami.py`, `resolve_descendants_filter`, keep the whole keyword-resolution / error-handling head unchanged and make four content-anchored edits:

1. In the `"self"` branch, delete this exact line (dead after delegation):
   ```python
            tree_key = session.spawn_root_id or session.id
   ```
   (the one directly below `target_id = session.id` inside the `if value == "self":` block).
2. In the `"parent"` branch, delete this exact block (dead after delegation):
   ```python
            # The current session shares its spawn_root with its parent (the
            # denormalization propagates at creation), so we can derive the
            # tree key without loading the parent row.
            tree_key = session.spawn_root_id or session.spawned_by_id
   ```
3. Replace this exact block (the explicit-id branch and the whole BFS tail, currently the end of the function):
   ```python
    else:
        try:
            session = Session.objects.only("id", "spawn_root_id").get(pk=value)
        except Session.DoesNotExist:
            return set()
        target_id = session.id
        tree_key = session.spawn_root_id or session.id

    rows = list(
        Session.objects.filter(spawn_root_id=tree_key).only("id", "spawned_by_id")
    )

    if tree_key == target_id:
        # Target is the tree root → every other row is a descendant.
        return {r.id for r in rows if r.id != target_id}

    # Target is mid-tree → BFS its branch to drop sibling/parent rows.
    adj: dict[str, list[str]] = {}
    for r in rows:
        if r.spawned_by_id:
            adj.setdefault(r.spawned_by_id, []).append(r.id)

    out: set[str] = set()
    stack = list(adj.get(target_id, ()))
    while stack:
        node = stack.pop()
        if node in out:
            continue
        out.add(node)
        stack.extend(adj.get(node, ()))
    return out
   ```
   with:
   ```python
    else:
        target_id = value

    from twicc.core.services.spawn_scope import descendant_ids

    return descendant_ids(target_id)
   ```
4. Delete this exact function-local import, which no branch uses after the explicit-id probe and inline BFS are gone:
   ```python
    from twicc.core.models import Session
   ```

Finally, replace this exact current docstring block:

```python
    Algorithm:
    1. Resolve ``target_id`` and ``tree_key`` from ``value`` (the target
       is the resolved session for ``"self"`` / an explicit id, or the
       current session's spawner for ``"parent"``). ``tree_key`` is the
       resolved session's ``spawn_root_id`` (with a defensive fallback
       to the target's id).
    2. Fetch the universe with a single ``filter(spawn_root_id=tree_key)``
       projection on ``(id, spawned_by_id)``.
    3. If ``tree_key == target_id`` (the target IS the tree root), every
       row except the target itself is a descendant — shortcut, no BFS.
    4. Otherwise, BFS the ``spawned_by_id`` edges from the target to
       isolate its branch within the tree.
```

with:

```python
    Algorithm:
    1. Resolve ``target_id`` from ``value``: the current session for
       ``"self"``, its spawner for ``"parent"``, or the explicit value.
    2. Delegate the common lookup and BFS to
       ``core.services.spawn_scope.descendant_ids(target_id)``.
```

For the `"self"`/`"parent"` branches, `target_id` is already set; explicit ids pass straight through. `descendant_ids` validates every target and re-derives the tree key itself, so the old explicit-id existence probe would only duplicate its query. Behaviour is identical on consistently denormalised data. For an inconsistent `parent` row, the fallback shifts from the child's `spawned_by_id` to the parent's own `spawn_root_id or id`, which is at least as authoritative.

- [ ] **Step 5: Run the new tests + the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_spawn_scope.py -q && uv run --active pytest -q`
Expected: PASS everywhere. `resolve_descendants_filter` had no pre-existing direct test; the delegation test above is its first direct coverage.

- [ ] **Step 6: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/spawn_scope.py src/twicc/cli/_drop_request/whoami.py tests/test_spawn_scope.py`
Subject: `refactor(core): extract spawn subtree resolver`
```

---

### Task 4: URL parity — mirrored builder pair + shared fixture

**Files:**
- Create: `src/twicc/core/services/share_url.py`
- Create: `tests/fixtures/share_url_parity.json`
- Create: `frontend/src/utils/shareUrlCore.js` (dependency-free builder pair — see the note below)
- Modify: `src/twicc/cli/share.py` (`_base_url`, URL assembly in `list_main`/`show_main`, module docstring)
- Modify: `frontend/src/utils/shareUrl.js`
- Test: `tests/test_share_url_parity.py` (create), `frontend/src/utils/shareUrl.test.js` (create)

**Interfaces:**
- Produces: Python `normalize_share_base(value) -> str` and `build_share_url(base_value, url_path) -> str`; JS `normalizeShareBase(value)` and `buildShareUrl(baseValue, urlPath)` — defined in `shareUrlCore.js`, re-exported by `shareUrl.js` so app code keeps one import point. Contract (§7.4): **parity** — same algorithm, byte-identical output on both surfaces; enforced by the fixture, not by comments. Also produces the `cli/share.py` URL-builder integration used by `list_main` and `show_main`.
- **Why the split:** `frontend/package.json` is `"type": "module"` and `npm test` is plain `node --test`; `shareUrl.js` imports the Pinia store via the extensionless `'../stores/settings'`, which fails under node (`ERR_MODULE_NOT_FOUND` — verified). The parity test file must therefore import from a dependency-free module, like every other node-tested util (`browserUrl.js`, `layoutResolver.js`, …). `shareUrlCore.js` has **zero imports**; `shareUrl.js` keeps `shareAbsoluteUrl` (store-dependent) and re-exports the pair, so the existing consumers (`ShareListPanel.vue`, `ShareDialog.vue`) are untouched.
- Algorithm (§7.4, normative): (1) strip leading/trailing characters of the set {TAB, LF, VT, FF, CR, SPACE} then strip trailing `/` — native `str.strip()` / `String.trim()` are **forbidden** (their Unicode sets differ: JS trims U+FEFF, Python trims U+001C); (2) if the value contains no `://`, prefix `https://`; (3) append `url_path`. Empty base is out of the parity scope: CLI prints the relative path, frontend returns `null` (deliberate split, unchanged).

- [ ] **Step 1: Write the shared fixture**

Create its absent parent directory first:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && mkdir -p tests/fixtures
```

Create `tests/fixtures/share_url_parity.json`:

```json
{
    "_comment": "Parity cases for the mirrored share URL builders (agent-sharing design §7.4). Consumed by tests/test_share_url_parity.py AND frontend/src/utils/shareUrl.test.js — same input, byte-identical expected output on both surfaces.",
    "url_path": "/share/tok123/",
    "cases": [
        {"name": "bare host", "stored": "share.example.com", "expected": "https://share.example.com/share/tok123/"},
        {"name": "bare host with port", "stored": "share.example.com:8443", "expected": "https://share.example.com:8443/share/tok123/"},
        {"name": "https origin", "stored": "https://share.example.com", "expected": "https://share.example.com/share/tok123/"},
        {"name": "http origin with port", "stored": "http://share.example.com:3500", "expected": "http://share.example.com:3500/share/tok123/"},
        {"name": "trailing slash", "stored": "share.example.com/", "expected": "https://share.example.com/share/tok123/"},
        {"name": "many trailing slashes", "stored": "https://share.example.com///", "expected": "https://share.example.com/share/tok123/"},
        {"name": "ascii whitespace trimmed", "stored": "\t share.example.com \r\n", "expected": "https://share.example.com/share/tok123/"},
        {"name": "raw CLI-written value trimmed", "stored": "  share.example.com  ", "expected": "https://share.example.com/share/tok123/"},
        {"name": "U+FEFF not trimmed (JS trim would)", "stored": "\ufeffshare.example.com", "expected": "https://\ufeffshare.example.com/share/tok123/"},
        {"name": "U+001C not trimmed (Python strip would)", "stored": "\u001cshare.example.com", "expected": "https://\u001cshare.example.com/share/tok123/"},
        {"name": "path kept", "stored": "https://share.example.com/base", "expected": "https://share.example.com/base/share/tok123/"},
        {"name": "query kept", "stored": "https://share.example.com?x=1", "expected": "https://share.example.com?x=1/share/tok123/"},
        {"name": "credentials kept", "stored": "https://u:p@share.example.com", "expected": "https://u:p@share.example.com/share/tok123/"},
        {"name": "mixed case preserved", "stored": "Share.Example.COM", "expected": "https://Share.Example.COM/share/tok123/"},
        {"name": "exotic scheme passes through", "stored": "ftp://share.example.com", "expected": "ftp://share.example.com/share/tok123/"},
        {"name": "pathological ://x passes through, no absoluteness promised", "stored": "://x", "expected": "://x/share/tok123/"}
    ]
}
```

- [ ] **Step 2: Write the failing Python test**

Create `tests/test_share_url_parity.py`:

```python
"""The §7.4 parity fixture, Python side. The SAME file drives
frontend/src/utils/shareUrl.test.js — never edit one side's expectations."""

from pathlib import Path

import orjson
import pytest

FIXTURE = orjson.loads(
    (Path(__file__).parent / "fixtures" / "share_url_parity.json").read_bytes()
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[c["name"] for c in FIXTURE["cases"]])
def test_build_share_url_parity(case):
    from twicc.core.services.share_url import build_share_url

    assert build_share_url(case["stored"], FIXTURE["url_path"]) == case["expected"]


def test_normalize_empty_stays_empty():
    from twicc.core.services.share_url import normalize_share_base

    assert normalize_share_base("") == ""
    assert normalize_share_base("   ") == ""
    assert normalize_share_base(None) == ""
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_url_parity.py -x -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Create the Python builder**

Create `src/twicc/core/services/share_url.py`:

```python
"""Share URL building — the parity contract of the agent-sharing design §7.4.

Mirrored with ``buildShareUrl`` in ``frontend/src/utils/shareUrlCore.js``: SAME
algorithm, byte-identical output for the same stored ``shareBaseUrl``. The
normative trim set below is part of the contract — do NOT switch to
``str.strip()`` (its Unicode set differs from JS ``trim()``). Parity is
enforced by the shared fixture ``tests/fixtures/share_url_parity.json``.

No validity guarantee: a bare ``host`` / ``host:port`` becomes an absolute
HTTPS URL; any other non-empty stored value passes through the algorithm
unchanged (a pre-existing configuration defect stays visible identically on
both surfaces).
"""

from __future__ import annotations

# Normative trim set (§7.4): ASCII whitespace only — TAB LF VT FF CR SPACE.
_TRIM_CHARS = "\t\n\x0b\x0c\r "


def normalize_share_base(value: str | None) -> str:
    """Trim the normative set, then strip trailing slashes. Empty stays empty."""
    return (value or "").strip(_TRIM_CHARS).rstrip("/")


def build_share_url(base_value: str | None, url_path: str) -> str:
    """Absolute share URL for a NON-EMPTY stored ``shareBaseUrl``.

    Callers handle the empty base themselves (CLI: relative path; frontend:
    ``null`` — the deliberate unset-host split, §7.4)."""
    base = normalize_share_base(base_value)
    if "://" not in base:
        base = "https://" + base
    return base + url_path
```

- [ ] **Step 5: Wire the CLI reads onto it and fix the module docstring**

In `src/twicc/cli/share.py`:

1. Replace the module docstring (the current one promises a "note" no output carries and describes the pre-parity URL behaviour — §12 stale-docs item 2). Old (current file head, verbatim):

```python
"""``twicc share list`` / ``show`` — read-only, direct DB (works with the server
down). Prints full URLs from the shareBaseUrl synced setting; when it is unset,
prints the relative ``/share/<token>/`` path with a note (sharing has no configured
host — links only resolve on the dedicated share origin, §12)."""
```

New:

```python
"""``twicc share`` (list) / ``show`` — read-only, direct DB (works with the server
down). ``url`` follows the §7.4 parity contract of the agent-sharing design:
byte-identical to the URL the owner UI shows for the same share (mirrored
builder ``core/services/share_url.py`` ↔ ``frontend/src/utils/shareUrlCore.js``).
With ``shareBaseUrl`` unset, prints the relative ``/share/<token>/`` path
(links only resolve on the dedicated share origin)."""
```

2. Replace `_base_url`. Old (verbatim):

```python
def _base_url() -> str:
    from twicc.synced_settings import read_synced_settings
    return (read_synced_settings().get("shareBaseUrl") or "").strip().rstrip("/")
```

New:

```python
def _base_url() -> str:
    from twicc.core.services.share_url import normalize_share_base
    from twicc.synced_settings import read_synced_settings
    return normalize_share_base(read_synced_settings().get("shareBaseUrl"))
```

3. In `list_main`, replace this exact line, including its 12-space indentation:

```python
            data["url"] = (base + data["url_path"]) if base else data["url_path"]
```

with:

```python
            data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

In `show_main`, replace this distinct exact line:

```python
    data["url"] = (base + data["url_path"]) if base else data["url_path"]
```

with:

```python
    data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

Add `from twicc.core.services.share_url import build_share_url` once at the top of each function body, next to the existing lazy imports.

- [ ] **Step 6: Write the failing JS test, then mirror the builder**

Create `frontend/src/utils/shareUrl.test.js` — it imports from `./shareUrlCore.js`, NOT from `./shareUrl.js`: `shareUrl.js` keeps its Pinia-store import (`'../stores/settings'`, extensionless), which does not resolve under `node --test` (`"type": "module"`, no bundler) — importing it would error the whole frontend suite at discovery. Same constraint as Task 2's note.

```js
// The §7.4 parity fixture, JS side — driven by the SAME file as
// tests/test_share_url_parity.py. Never edit one side's expectations.
// Imports the dependency-free core module: shareUrl.js pulls the Pinia
// store and is not importable under node --test.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

const fixture = JSON.parse(
    readFileSync(new URL('../../../tests/fixtures/share_url_parity.json', import.meta.url), 'utf8'),
)

for (const c of fixture.cases) {
    test(`parity: ${c.name}`, () => {
        assert.equal(buildShareUrl(c.stored, fixture.url_path), c.expected)
    })
}

test('empty base stays empty after normalization', () => {
    assert.equal(normalizeShareBase(''), '')
    assert.equal(normalizeShareBase('   '), '')
})
```

Run it (expected: FAIL — `shareUrlCore.js` does not exist), then create `frontend/src/utils/shareUrlCore.js` (**zero imports** — the node-importable half of the mirror):

```js
// The §7.4 parity contract (agent-sharing design): SAME algorithm as
// src/twicc/core/services/share_url.py, byte-identical output, enforced by
// tests/fixtures/share_url_parity.json. The normative trim set is part of the
// contract — do NOT switch to String.prototype.trim() (its Unicode set
// differs from Python's str.strip()). This module must stay dependency-free:
// shareUrl.test.js imports it under plain `node --test`, where the store's
// extensionless imports do not resolve.
const TRIM_RE = /^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g

/** Trim the normative ASCII set, then strip trailing slashes. */
export function normalizeShareBase(value) {
    return String(value ?? '').replace(TRIM_RE, '').replace(/\/+$/, '')
}

/** Absolute share URL for a NON-EMPTY stored shareBaseUrl (callers handle
 *  the empty base — the Share UI is disabled without a host). */
export function buildShareUrl(baseValue, urlPath) {
    let base = normalizeShareBase(baseValue)
    if (!base.includes('://')) base = 'https://' + base
    return base + urlPath
}
```

then replace the complete current `frontend/src/utils/shareUrl.js`. Old (verbatim):

```js
import { useSettingsStore } from '../stores/settings'

/** Absolute share URL from a serialized share's url_path. Requires the
 *  `shareBaseUrl` setting — sharing is served only on the dedicated share host and
 *  has no fallback origin (§12). Returns null when it isn't configured; callers gate
 *  the Share UI on `settings.getShareBaseUrl` (empty ⇒ Share entry points disabled). */
export function shareAbsoluteUrl(share) {
    const settings = useSettingsStore()
    const base = (settings.getShareBaseUrl || '').replace(/\/+$/, '')
    if (!base) return null
    return base + share.url_path
}
```

New:

```js
import { useSettingsStore } from '../stores/settings'

import { buildShareUrl, normalizeShareBase } from './shareUrlCore.js'

// Re-export the parity pair so app code keeps one import point; the
// algorithm lives in shareUrlCore.js (dependency-free, node-testable).
export { buildShareUrl, normalizeShareBase }

/** Absolute share URL from a serialized share's url_path, or null when the
 *  `shareBaseUrl` setting is unset (sharing disabled — callers gate the
 *  Share UI on `settings.getShareBaseUrl`). */
export function shareAbsoluteUrl(share) {
    const settings = useSettingsStore()
    const base = normalizeShareBase(settings.getShareBaseUrl)
    if (!base) return null
    return buildShareUrl(base, share.url_path)
}
```

The existing consumers (`ShareListPanel.vue`, `ShareDialog.vue`) import `shareAbsoluteUrl` from `'../../utils/shareUrl'` — untouched.

- [ ] **Step 7: Run both suites**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_url_parity.py -q && cd frontend && npm test`
Expected: PASS on both sides.

- [ ] **Step 8: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/share_url.py tests/fixtures/share_url_parity.json tests/test_share_url_parity.py src/twicc/cli/share.py frontend/src/utils/shareUrlCore.js frontend/src/utils/shareUrl.js frontend/src/utils/shareUrl.test.js`
Subject: `fix(share): align backend and frontend URLs`
```

---

### Task 5: Shared repairs — artifact title options + strict expiry

**Files:**
- Modify: `src/twicc/core/services/share_mutation.py` (`create_share` artifact branch ~line 271, `propagate_share` ~line 350, `_parse_expires` ~line 398, `create_share_from_payload` ~line 417, `patch_share` expiry branch ~line 298)
- Test: `tests/test_share_mutation.py`, `tests/test_share_owner_api.py`

**Interfaces:**
- Produces: `_parse_expires(payload) -> tuple[datetime | None, ShareError | None]` (signature change — both existing call sites updated in this task; no other caller exists, verify with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && rg -n "_parse_expires" src/`). Artifact shares now persist `show_title`/`display_title` alongside `snapshot_at`.
- These are **shared human-path repairs** (§7.2 "Shared repairs"): they change behaviour for humans too, on the documented surfaces only (drop-request create/update + REST create for expiry; every artifact create/propagate for title options). The owner REST PATCH keeps its pre-existing in-view `datetime.fromisoformat` raise — do NOT touch `owner_views.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_mutation.py` (reuse the existing fixtures `session`, `bookmark`, `artifacts_root`, helpers `_run`, `_write`):

```python
# ── Shared repairs (agent-sharing design §7.2 / §8) ─────────────────────────

def test_artifact_create_preserves_title_options(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": False, "display_title": "Custom"},
    ))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    assert share.options["show_title"] is False
    assert share.options["display_title"] == "Custom"
    assert "snapshot_at" in share.options
    # Served by the public serializer: show_title off ⇒ no title at all.
    from twicc.core.serializers import serialize_share_public_meta
    assert "title" not in serialize_share_public_meta(share)


def test_artifact_create_serves_custom_title(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": True, "display_title": "Custom"},
    ))
    assert result.success
    share = Share.objects.get(id=result.share_id)
    from twicc.core.serializers import serialize_share_public_meta
    assert serialize_share_public_meta(share)["title"] == "Custom"


def test_artifact_propagate_preserves_title_options(session, bookmark, artifacts_root):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    result = _run(share_mutation.create_share(
        "artifact", bookmark=bookmark,
        options={"show_title": True, "display_title": "Kept"},
    ))
    share = Share.objects.get(id=result.share_id)
    first_snapshot_at = share.options["snapshot_at"]
    result2 = _run(share_mutation.propagate_share(share))
    assert result2.success
    share.refresh_from_db()
    assert share.options["show_title"] is True
    assert share.options["display_title"] == "Kept"
    assert share.options["snapshot_at"] >= first_snapshot_at
    from twicc.core.serializers import serialize_share_public_meta
    assert serialize_share_public_meta(share)["title"] == "Kept"


def test_create_invalid_expiry_is_rejected_not_silent(session):
    """§7.2 expiry defect fix: a typo must NOT create a never-expiring link."""
    result = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "not-a-date",
    }))
    assert not result.success
    assert result.errors[0].field == "expires_at"
    assert result.errors[0].code == "invalid"
    assert Share.objects.count() == 0


def test_update_invalid_expiry_preserves_existing(session):
    from datetime import datetime, timezone
    result = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "2030-01-01T00:00:00+00:00",
    }))
    share = Share.objects.get(id=result.share_id)
    upd = _run(share_mutation.update_share_from_payload({
        "share_id": share.id, "fields": {"expires_at": "garbage"},
    }))
    assert not upd.success
    assert upd.errors[0].code == "invalid"
    share.refresh_from_db()
    assert share.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_valid_and_empty_expiry_unchanged(session):
    ok = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None,
        "expires_at": "2030-06-01T12:00:00+00:00",
    }))
    assert ok.success
    none1 = _run(share_mutation.create_share_from_payload({
        "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None, "expires_at": "",
    }))
    assert none1.success
    assert Share.objects.get(id=none1.share_id).expires_at is None
```

Add the import `from twicc.core.models import Share` if the file's existing import line does not already include it (it does — extend only if needed).

- [ ] **Step 1b: Add the REST-surface failing tests**

In `tests/test_share_owner_api.py`, replace this exact import boundary:

```python
import asyncio

import orjson
```

with:

```python
import asyncio
from pathlib import Path

import orjson
```

Replace these exact application imports:

```python
from twicc.core.models import Project, Session, SessionType, Share, ShareAccess
from twicc.core.services.share_tokens import mint_token
```

with:

```python
from twicc import paths
from twicc.core.models import (
    ArtifactBookmark, PinMode, Project, Session, SessionType, Share, ShareAccess,
)
from twicc.core.services.share_tokens import mint_token
```

Insert these fixtures and helper immediately after the existing `share_host` fixture:

```python
@pytest.fixture
def bookmark(session):
    return ArtifactBookmark.objects.create(
        session=session, project_id=session.project_id,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


def _write(artifacts_root: Path, session_id: str, name: str, payload: bytes) -> Path:
    target = artifacts_root / session_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target
```

Append these complete REST-surface tests:

```python
@pytest.mark.parametrize("bad_expiry", ["junk", False, 0, [], {}])
def test_rest_create_invalid_expiry_rejected(
        bad_expiry, client, session, share_host):
    body = {
        "kind": "session", "session_id": session.id,
        "options": {"mode": "live"}, "expires_at": bad_expiry,
    }
    res = _run(client.post(
        "/api/shares/", data=orjson.dumps(body), content_type="application/json",
    ))
    assert res.status_code == 400
    errors = orjson.loads(res.content)["errors"]
    assert [(e["field"], e["code"]) for e in errors] == [("expires_at", "invalid")]
    assert Share.objects.count() == 0


def test_rest_artifact_create_preserves_title_options(
        client, session, bookmark, artifacts_root, share_host):
    _write(artifacts_root, session.id, "demo/index.html", b"<html/>")
    body = {
        "kind": "artifact", "bookmark_id": bookmark.id,
        "options": {"show_title": False, "display_title": "Custom"},
    }
    res = _run(client.post(
        "/api/shares/", data=orjson.dumps(body), content_type="application/json",
    ))
    assert res.status_code == 201
    data = orjson.loads(res.content)
    assert data["options"]["show_title"] is False
    assert data["options"]["display_title"] == "Custom"
    assert "snapshot_at" in data["options"]


def test_rest_patch_invalid_expiry_keeps_existing_raise(client, session, share_host):
    """Accepted §7.2 limitation: REST PATCH parses in-view and still raises."""
    share = _share(session)
    with pytest.raises(ValueError):
        _run(client.patch(
            f"/api/shares/{share.id}/",
            data=orjson.dumps({"expires_at": "junk"}),
            content_type="application/json",
        ))
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_mutation.py tests/test_share_owner_api.py -q`
Expected: 11 new pytest cases FAIL: five mutation cases, five parametrized REST invalid-expiry cases, and one REST artifact-options case. The valid/empty mutation control and the existing REST PATCH raise control already pass.

- [ ] **Step 3: Fix at the cause**

In `src/twicc/core/services/share_mutation.py`:

1. `create_share`, artifact branch — in this exact block:
   ```python
    if kind == ShareKind.ARTIFACT.value:
        # Take the snapshot BEFORE the row lands so a copy failure aborts creation.
        err = await sync_to_async(snapshot_artifact_share)(share)
        if err:
            return ShareMutationResult(False, None, [ShareError("bookmark", "snapshot_failed", err)])
        share.options = {"snapshot_at": _now().isoformat()}
   ```
   replace the last line with:
   ```python
        share.options = {**opts, "snapshot_at": _now().isoformat()}
   ```
2. `propagate_share`, artifact branch — in this exact block:
   ```python
    else:
        err = await sync_to_async(snapshot_artifact_share)(share)
        if err:
            return ShareMutationResult(False, share.id, [ShareError("bookmark", "snapshot_failed", err)])
        share.options = {"snapshot_at": _now().isoformat()}
   ```
   replace the last line with:
   ```python
        share.options = {**share.options, "snapshot_at": _now().isoformat()}
   ```
3. Replace `_parse_expires`. Old (verbatim):
   ```python
   def _parse_expires(payload: dict) -> datetime | None:
       raw = payload.get("expires_at")
       if not raw:
           return None
       try:
           return datetime.fromisoformat(raw)
       except (ValueError, TypeError):
           return None
   ```
   New:
   ```python
   def _parse_expires(payload: dict) -> tuple[datetime | None, ShareError | None]:
       """Strict expiry parse (§7.2 defect fix): absent/None/"" → no expiry; a
       non-empty value must parse under ``datetime.fromisoformat`` or the caller
       gets ``expires_at``/``invalid`` — never a silently never-expiring link."""
       raw = payload.get("expires_at")
       if raw is None or raw == "":
           return None, None
       try:
           return datetime.fromisoformat(raw), None
       except (ValueError, TypeError):
           return None, ShareError(
               "expires_at", "invalid",
               f"invalid expires_at {raw!r}: use an ISO 8601 datetime, "
               f"e.g. 2026-12-31T23:59:00+00:00",
           )
   ```
4. `create_share_from_payload` — replace this exact line of the `create_share(...)` call:
   ```python
        expires_at=_parse_expires(payload),
   ```
   with `expires_at=expires_at,`, and add the pre-step above the `return await create_share(` line:
   ```python
    expires_at, exp_err = _parse_expires(payload)
    if exp_err:
        return ShareMutationResult(False, None, [exp_err])
   ```
5. `patch_share`, the `"expires_at" in fields` branch — replace this exact block:
   ```python
        raw_exp = fields["expires_at"]
        share.expires_at = _parse_expires({"expires_at": raw_exp}) if isinstance(raw_exp, str) else raw_exp
   ```
   with:
   ```python
        raw_exp = fields["expires_at"]
        if isinstance(raw_exp, str):
            parsed, exp_err = _parse_expires({"expires_at": raw_exp})
            if exp_err:
                return ShareMutationResult(False, share.id, [exp_err])
            share.expires_at = parsed
        else:
            share.expires_at = raw_exp
   ```
   (indentation as in the surrounding branch; keep the existing "REST passes a parsed datetime|None…" comment above it, updating its wording to mention the strict parse).

- [ ] **Step 4: Run the full suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_mutation.py tests/test_share_owner_api.py -q && uv run --active pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/share_mutation.py tests/test_share_mutation.py tests/test_share_owner_api.py`
Subject: `fix(share): preserve options and validate expiry`
```

---

### Task 6: `share_id` in the CLI/MCP result

**Files:**
- Modify: `src/twicc/cli/_drop_request/output.py` (`build_final`, ~lines 28-66)
- Test: `tests/test_share_cli_output.py` (create)

**Interfaces:**
- Existing input: the watcher already copies `share_id` onto the status payload (`_RESULT_ID_FIELDS`, `src/twicc/drop_requests_watcher.py`) — no watcher change needed.
- Produces: every share mutation's final CLI/MCP JSON is `{"status": <status>, "share_id": "shr_…", "request_uuid": "…"}` (§8 "Success output"). Creation deliberately returns **no token and no URL** — the flow is `share create …` then `share show <share_id>`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_share_cli_output.py`:

```python
"""build_final dispatch for share results (agent-sharing design §8): without a
share branch the formatter falls through to the project projection and the
share_id is lost — the two-call create→show flow would be impossible."""

from types import SimpleNamespace

import orjson
from typer.testing import CliRunner

from twicc.cli import app
from twicc.cli._drop_request.output import build_final


def _outcome(status, data):
    return SimpleNamespace(status=status, data=data, received_seen=True)


def test_share_create_result_carries_share_id():
    final = build_final(
        _outcome("created", {"status": "created", "share_id": "shr_ab12cd34"}),
        request_uuid="u-1", timeout=30,
    )
    assert final == {"status": "created", "share_id": "shr_ab12cd34", "request_uuid": "u-1"}


def test_share_update_and_delete_results_carry_share_id():
    for status in ("updated", "deleted"):
        final = build_final(
            _outcome(status, {"status": status, "share_id": "shr_x"}),
            request_uuid="u-2", timeout=30,
        )
        assert final["share_id"] == "shr_x"
        assert "project_id" not in final


def test_other_families_unchanged():
    final = build_final(
        _outcome("updated", {"status": "updated", "bookmark_id": 3,
                             "session_id": "s1", "project_id": "p1"}),
        request_uuid="u-3", timeout=30,
    )
    assert final["bookmark_id"] == 3
    assert "share_id" not in final


def test_real_cli_create_surface_carries_only_result_ids(monkeypatch):
    """Cross the Typer command and real _run_drop/emit_final path."""
    from twicc.cli._drop_request import transport

    class Submission:
        request_uuid = "u-cli"

        def cleanup(self):
            pass

    monkeypatch.setattr(transport, "ensure_server_available", lambda: None)
    monkeypatch.setattr(transport, "submit", lambda payload, *, kind: Submission())
    monkeypatch.setattr(
        transport,
        "wait",
        lambda submission, *, timeout_seconds: _outcome(
            "created", {"status": "created", "share_id": "shr_cli"}),
    )
    # Task 9 wraps this command with caller discovery. Keep this Task 6
    # boundary test on the human path and independent of the ORM.
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )
    result = CliRunner().invoke(app, ["share", "create", "session", "s1"])
    assert result.exit_code == 0, result.output
    assert orjson.loads(result.stdout) == {
        "status": "created", "share_id": "shr_cli", "request_uuid": "u-cli",
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_output.py -x -q`
Expected: FAIL — both create-shape tests lose `share_id` after `build_final`.

- [ ] **Step 3: Add the dispatch branch**

In `src/twicc/cli/_drop_request/output.py`, insert after this exact line (the last of the field tuples):

```python
_PEER_SEND_ID_FIELDS = ("message_id", "peer_id", "peer_status")
```

the new tuple:

```python
_SHARE_ID_FIELDS = ("share_id",)
```

and in `build_final`'s dispatch chain, locate this exact block:

```python
        if "message_id" in d:
            id_fields = _PEER_SEND_ID_FIELDS
        elif "bookmark_id" in d:
```

and insert between the `message_id` branch and the `bookmark_id` line:

```python
        elif "share_id" in d:
            id_fields = _SHARE_ID_FIELDS
```

Extend the dispatch comment above the chain with one line: `share_id` is share-mutation only.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_output.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/_drop_request/output.py tests/test_share_cli_output.py`
Subject: `fix(share): return share ids from mutations`
```

---

### Task 7: Cross-kind list filters

**Files:**
- Modify: `src/twicc/cli/share.py` (`list_main` filters, ~lines 23-30)
- Test: `tests/test_share_cli_reads.py` (create)

**Interfaces:**
- Consumes Task 4's `cli/share.py` URL-builder integration used by `list_main` and `show_main`; this task preserves it while replacing the list queryset filters.
- Produces (§8 "Cross-kind filter semantics"): `list_main(*, session, project, …)` makes `--session X` match `Q(session_id=X) | Q(artifact_bookmark__session_id=X)`; `--project` applies `project_scope_ids(project)` to both target relations via the bookmark's denormalised raw `project` FK. Artifact shares (`session` NULL by the CheckConstraint) stop being silently excluded. Also produces the `tests/test_share_cli_reads.py` scaffold (`project`, `session`, `bookmark`, `one_share_each`, `_run`, `_list`) consumed by Tasks 13 and 15.

- [ ] **Step 1: Write the failing test**

Create `tests/test_share_cli_reads.py`:

```python
"""CLI share list/show behaviour (direct-DB reads). Extended later by the
redaction task; here: the §8 cross-kind filter repair."""

import asyncio

import pytest
from django.utils import timezone as djtz

from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType
from twicc.core.services import share_mutation


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-reads", directory="/tmp/reads")


@pytest.fixture
def session(project):
    now = djtz.now()
    return Session.objects.create(
        id="sess-reads", project=project, provider="claude_code",
        file_path="sess-reads.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=5,
    )


@pytest.fixture
def bookmark(session, project):
    return ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def one_share_each(session, bookmark, tmp_path, monkeypatch):
    """One session share + one artifact share of the same session/project."""
    from twicc import paths
    data_dir = tmp_path / "data"
    (data_dir / "artifacts" / session.id / "demo").mkdir(parents=True)
    (data_dir / "artifacts" / session.id / "demo" / "index.html").write_bytes(b"<html/>")
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    s1 = _run(share_mutation.create_share("session", session=session, options={}))
    s2 = _run(share_mutation.create_share("artifact", bookmark=bookmark, options={}))
    assert s1.success and s2.success
    return s1.share_id, s2.share_id


def _list(**kwargs):
    from twicc.cli import share as cli_share
    captured = []
    import twicc.cli.share
    orig = twicc.cli.share.emit_json
    twicc.cli.share.emit_json = captured.append
    try:
        cli_share.list_main(**kwargs)
    finally:
        twicc.cli.share.emit_json = orig
    return captured[0]


def test_session_filter_returns_both_kinds(one_share_each, session):
    rows = _list(session=session.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_returns_both_kinds(one_share_each, project):
    rows = _list(project=project.id)
    assert {r["kind"] for r in rows} == {"session", "artifact"}


def test_project_filter_expands_downward_to_worktree_for_both_kinds(
        one_share_each, project):
    """A main project includes child worktrees; a worktree stays local."""
    from twicc import paths

    worktree = Project.objects.create(
        id="-tmp-reads-wt", directory="/tmp/reads-wt", worktree_of=project,
    )
    now = djtz.now()
    wt_session = Session.objects.create(
        id="sess-reads-wt", project=worktree, provider="claude_code",
        file_path="sess-reads-wt.jsonl", type=SessionType.SESSION,
        created_at=now, last_line=8,
    )
    wt_bookmark = ArtifactBookmark.objects.create(
        session=wt_session, project=worktree,
        relative_path="demo/index.html", name="Worktree demo", scope=PinMode.PROJECT,
    )
    source = paths.get_data_dir() / "artifacts" / wt_session.id / "demo" / "index.html"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"<html>worktree</html>")
    wt_session_result = _run(
        share_mutation.create_share("session", session=wt_session, options={}))
    wt_artifact_result = _run(
        share_mutation.create_share("artifact", bookmark=wt_bookmark, options={}))
    assert wt_session_result.success and wt_artifact_result.success
    worktree_ids = {wt_session_result.share_id, wt_artifact_result.share_id}

    main_rows = _list(project=project.id)
    assert worktree_ids <= {row["id"] for row in main_rows}
    worktree_rows = _list(project=worktree.id)
    assert {row["id"] for row in worktree_rows} == worktree_ids
    assert {row["kind"] for row in worktree_rows} == {"session", "artifact"}


def test_unrelated_session_filter_returns_nothing(one_share_each):
    assert _list(session="other-session") == []
```

(Use `monkeypatch` for `emit_json` if preferred over the manual swap — either way restore it.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_reads.py -x -q`
Expected: FAIL — the artifact share is missing from both filters.

- [ ] **Step 3: Fix the filters**

In `src/twicc/cli/share.py` `list_main`, after `django.setup()` add `from django.db.models import Q` and replace this exact block:

```python
    if session is not None:
        qs = qs.filter(session_id=session)
    if project is not None:
        from twicc.projects import project_scope_ids
        qs = qs.filter(session__project_id__in=project_scope_ids(project))
```

with:

```python
    if session is not None:
        qs = qs.filter(Q(session_id=session) | Q(artifact_bookmark__session_id=session))
    if project is not None:
        from twicc.projects import project_scope_ids
        ids = project_scope_ids(project)
        # Both kinds: an artifact share has session NULL (CheckConstraint), its
        # project comes from the bookmark's denormalised raw project FK.
        qs = qs.filter(Q(session__project_id__in=ids) | Q(artifact_bookmark__project_id__in=ids))
```

- [ ] **Step 4: Run the tests + full suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_reads.py -q && uv run --active pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/share.py tests/test_share_cli_reads.py`
Subject: `fix(share): apply filters across both kinds`
```

---

### Task 8: Tri-state `--live/--frozen`

**Files:**
- Modify: `src/twicc/cli/__init__.py` (`_share_create_session`, ~line 647)
- Modify: `src/twicc/cli/share_mutation.py` (`run_create_session`)
- Test: `tests/test_share_cli_payloads.py` (create)
- Run-only regression guard: `tests/test_share_mutation.py` (no edit)

**Interfaces:**
- Produces (§7.2 "The CLI must be able to produce an absent mode"): `run_create_session(..., mode: str | None, options: dict, ...)` omits `options.mode` when `mode is None`; `--live` supplies `"live"`; `--frozen` supplies `"snapshot"`. Server side: mode absent + human → `live` (existing `_validate_session_options` default, untouched here); mode absent + agent → `snapshot` (Task 12). The MCP tool inherits the fix for free (`render_argv` omits an absent option).
- Produces the tri-state `_share_create_session` declaration: `live: bool | None = typer.Option(None, "--live/--frozen", ...)`, preserved by Task 15.
- Produces the `tests/test_share_cli_payloads.py` scaffold: module-level `runner`, fixture `captured_drop`, and helper `_invoke(args)`, extended by Tasks 9 and 16.

- [ ] **Step 1: Write the failing test**

Create `tests/test_share_cli_payloads.py`:

```python
"""Payloads the `twicc share` CLI produces (agent-sharing design §7.2): the
tri-state --live/--frozen flag must let `options.mode` be ABSENT, or the
server-side frozen default for agents can never fire."""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def captured_drop(monkeypatch):
    calls = []

    def fake_run_drop(payload, *, kind, success_status, timeout):
        calls.append({"payload": payload, "kind": kind})

    monkeypatch.setattr("twicc.cli.share_mutation._run_drop", fake_run_drop)
    return calls


def _invoke(args):
    from twicc.cli import app
    return runner.invoke(app, args)


def test_no_flag_omits_mode(captured_drop):
    result = _invoke(["share", "create", "session", "sess-1"])
    assert result.exit_code == 0
    options = captured_drop[0]["payload"]["options"]
    assert "mode" not in options


def test_explicit_live_and_frozen(captured_drop):
    _invoke(["share", "create", "session", "sess-1", "--live"])
    _invoke(["share", "create", "session", "sess-1", "--frozen"])
    assert captured_drop[0]["payload"]["options"]["mode"] == "live"
    assert captured_drop[1]["payload"]["options"]["mode"] == "snapshot"
```

Note: with `_run_drop` monkeypatched, the command performs no Django setup and no server contact — the exit code is Typer's own (0).

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_payloads.py -x -q`
Expected: FAIL — `options["mode"] == "live"` is always present today.

- [ ] **Step 3: Make the flag tri-state**

1. `src/twicc/cli/__init__.py` `_share_create_session` — replace this exact option line:
   ```python
    live: bool = typer.Option(True, "--live/--frozen", help="Live-follow or snapshot."),
   ```
   with:
   ```python
    live: bool | None = typer.Option(None, "--live/--frozen", help="Live-follow or snapshot. Default: live for a human caller, frozen for an agent caller."),
   ```
   and this exact `mode=` line of the `run_create_session` call:
   ```python
        mode="live" if live else "snapshot",
   ```
   with:
   ```python
        mode=None if live is None else ("live" if live else "snapshot"),
   ```
2. `src/twicc/cli/share_mutation.py` `run_create_session` — replace the whole function. Old (verbatim):
   ```python
   def run_create_session(*, session_id: str, label: str, password: str | None,
                          expires_at: str | None, mode: str, options: dict, timeout: int) -> None:
       _run_drop(
           {"kind_target": "session", "session_id": session_id, "label": label,
            "password": password, "expires_at": expires_at,
            "options": {**options, "mode": mode}},
           kind="share:create", success_status="created", timeout=timeout,
       )
   ```
   New:
   ```python
   def run_create_session(*, session_id: str, label: str, password: str | None,
                          expires_at: str | None, mode: str | None, options: dict,
                          timeout: int) -> None:
       opts = dict(options)
       if mode is not None:
           opts["mode"] = mode
       _run_drop(
           {"kind_target": "session", "session_id": session_id, "label": label,
            "password": password, "expires_at": expires_at, "options": opts},
           kind="share:create", success_status="created", timeout=timeout,
       )
   ```

- [ ] **Step 4: Verify pass + human default regression**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_payloads.py tests/test_share_mutation.py -q`
Expected: PASS. (No existing test pins the server-side no-`mode` default — every session-share create in `tests/test_share_mutation.py` either passes an explicit `mode` or is rejected before the default applies, and no validation test asserts the defaulted `mode`. The "human create with no mode → live" behaviour gets its dedicated coverage in Task 12, group 5. Running the file here only guards against regressions in the existing explicit-mode tests.)

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/__init__.py src/twicc/cli/share_mutation.py tests/test_share_cli_payloads.py`
Subject: `feat(share): make share mode tri-state`
```

---

### Task 9: `caller_session_id` CLI plumbing

**Files:**
- Modify: `src/twicc/cli/share_mutation.py` (all four `run_*` functions)
- Test: `tests/test_share_cli_payloads.py` (extend)

**Interfaces:**
- Consumes Task 8's `run_create_session(..., mode: str | None, options: dict, ...)` and its `tests/test_share_cli_payloads.py` scaffold (`runner`, `captured_drop`, `_invoke(args)`).
- Produces (§5.1): every share mutation payload the CLI submits carries `caller_session_id: <id>` when `resolve_current_session()` resolves, and no key at all otherwise — exactly the `peer-send` `origin_session_id` pattern (`src/twicc/cli/peer_send.py`). The MCP path gets it for free: the dispatcher sets the `forced_session_id` ContextVar, which `resolve_current_session()` honours first.
- Produces the caller-identity cases and the autouse human-caller boundary in `tests/test_share_cli_payloads.py`. Task 16 preserves these cases when it appends the bare-group case to the same file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_cli_payloads.py`:

```python
@pytest.fixture(autouse=True)
def _default_human_caller(monkeypatch):
    """Keep Task 8's CLI-only tests out of the real ProcessRun query."""
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )


class _FakeSession:
    id = "caller-1"


def test_mutations_carry_caller_session_id(captured_drop, monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _FakeSession(),
    )
    results = [
        _invoke(["share", "create", "session", "sess-1"]),
        _invoke(["share", "create", "artifact", "3", "--label", "x"]),
        _invoke(["share", "update", "shr_1", "--label", "y"]),
        _invoke(["share", "revoke", "shr_1"]),
    ]
    assert all(result.exit_code == 0 for result in results)
    assert len(captured_drop) == 4
    for call in captured_drop:
        assert call["payload"]["caller_session_id"] == "caller-1"


def test_human_payload_has_no_caller_key(captured_drop, monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None,
    )
    _invoke(["share", "create", "session", "sess-1"])
    assert "caller_session_id" not in captured_drop[0]["payload"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_payloads.py -x -q`
Expected: FAIL with `KeyError: 'caller_session_id'`.

- [ ] **Step 3: Add the helper and wrap every payload**

In `src/twicc/cli/share_mutation.py` add:

```python
def _with_caller(payload: dict) -> dict:
    """Stamp the resolved caller session id (design §5.1) — best-effort
    identity, same pattern as peer-send's origin_session_id. The server-side
    gate treats a missing key as a human caller; this is a guardrail, not a
    security boundary (§5.2)."""
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    if current is not None:
        payload["caller_session_id"] = current.id
    return payload
```

and wrap the payload (first positional argument) of each of the four `_run_drop` calls in `_with_caller(...)`:

1. `run_create_session` — its body is the Task 8 rewrite; its `_run_drop` call becomes:
   ```python
       _run_drop(
           _with_caller({"kind_target": "session", "session_id": session_id, "label": label,
                         "password": password, "expires_at": expires_at, "options": opts}),
           kind="share:create", success_status="created", timeout=timeout,
       )
   ```
2. `run_create_artifact` — current call (verbatim):
   ```python
    _run_drop(
        {"kind_target": "artifact", "bookmark_id": bookmark_id, "label": label,
         "password": password, "expires_at": expires_at, "options": options},
        kind="share:create", success_status="created", timeout=timeout,
    )
   ```
   → wrap the dict: `_run_drop(_with_caller({...same dict...}), kind=..., ...)`.
3. `run_update` — current call (verbatim):
   ```python
    _run_drop({"share_id": share_id, "fields": fields}, kind="share:update",
              success_status="updated", timeout=timeout)
   ```
   → `_run_drop(_with_caller({"share_id": share_id, "fields": fields}), kind="share:update", ...)`.
4. `run_simple` — current call (verbatim):
   ```python
    _run_drop({"share_id": share_id}, kind=kind, success_status=success, timeout=timeout)
   ```
   → `_run_drop(_with_caller({"share_id": share_id}), kind=kind, success_status=success, timeout=timeout)`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_payloads.py -q`
Expected: PASS. Django is configured by the test settings, so `_with_caller`'s `django.setup()` is idempotent. The autouse fixture prevents its otherwise-unconditional `resolve_current_session()` call from querying `ProcessRun` in Task 8's non-DB tests; each Task 9 test's own monkeypatch runs later and overrides it.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/share_mutation.py tests/test_share_cli_payloads.py`
Subject: `feat(share): stamp mutation caller identity`
```

---

### Task 10: `share_agent_gate.py` — the shape contract (pure module)

**Files:**
- Create: `src/twicc/core/services/share_agent_gate.py`
- Test: `tests/test_share_agent_gate.py` (create)

**Interfaces:**
- Existing input: `ShareError(field, code, message)` from `share_mutation.py` (top-level import here; `share_mutation` imports THIS module lazily inside its wrappers in Task 12, which avoids a cycle).
- Produces:
  - `SETTING_KEYS = {"session": "allowAgentSessionShares", "artifact": "allowAgentArtifactShares"}` and `setting_key_for(kind) -> str`.
  - `caller_type_error(payload) -> ShareError | None` — §7.1 step 1 typing (present ⇒ must be a JSON string), no ORM.
  - Layer 1 only: `validate_create(payload) -> list[ShareError]`, `validate_update(payload) -> list[ShareError]`, and `validate_simple(payload) -> list[ShareError]`. These validate keys, required presence, and JSON types before target/share resolution.
  - All three validators are pure logic with no ORM. The module is not transitively Django-free because `ShareError` comes from `share_mutation`; every importer already runs under configured Django.
- Semantics locked by the spec (§7.2), restated here as the module's contract:
  - The envelope = application fields + `kind` + `caller_session_id`. Any other key → `field_forbidden`. A listed key with a wrong JSON type → `field_forbidden`. Absent optional keys take server defaults; required keys: `kind`, `caller_session_id`, the target id (`session_id`/`bookmark_id`/`share_id`), and create's `kind_target`.
  - Server-owned / non-CLI keys (`frozen_at_line`, `snapshot_at`, `show_timestamps`, `notify_on_view`, the legacy `share_kind` alias, anything else) are rejected **whatever the value** — they are simply not in the allowed sets.
  - Booleans are **literal JSON booleans** (`isinstance(v, bool)`); `bookmark_id` is an int that is NOT a bool; `"false"` is a wrong type, never coerced.
  - Create `password`: absent/`null`/`""` → fine (no password); any non-string → Layer-1 `field_forbidden`. Update `password`: any non-string → Layer-1 `field_forbidden`; Task 12 owns the post-scope `""` value refusal and its human-surface message.
  - Create `expires_at`: `null`/`""`/absent fine, else must be a string (validity is Task 5's strict parse). Update `expires_at`: absent → unchanged, `null` → explicit clear, `""` → `field_forbidden` (not CLI-producible on update — `--expires ""` normalises to `null`).
  - Task 12 owns the post-scope `options.max_display_mode == "debug"` value refusal and its `display_mode_forbidden` message.
  - Operation values are exact: create requires `kind == "share:create"`; update requires `kind == "share:update"`; simple accepts only `share:revoke`, `share:unrevoke`, `share:delete`, or `share:propagate`.
  - `kind_target` must be a string. `"session"`/`"artifact"` select their exact envelopes. An unknown string still receives union-envelope key/type validation; when shape-clean, target resolution rejects it with the existing `kind`/`invalid` before any target ORM lookup (§7.1 step 3).
  - The wrappers call these validators only for a resolved agent caller. An absent or well-typed unknown `caller_session_id` is the human bypass and does not enter this module's envelope validators.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_agent_gate.py` with this structure (every case from §14's "Shape contract", "Update shape" and "Value rules" payload-only rows). Use plain dict payloads; no DB fixtures needed:

```python
"""Layer-1 shape contract for agent share payloads (design §7.2).
Pure-module tests — the ORM-dependent gate wiring is tested in
tests/test_share_gate_wiring.py."""

from copy import deepcopy

import pytest

from twicc.core.services import share_agent_gate as gate


def _create_session_payload(**over):
    p = {
        "kind": "share:create", "caller_session_id": "caller-1",
        "kind_target": "session", "session_id": "sess-1",
        "label": "", "password": None, "expires_at": None,
        "options": {"max_display_mode": "normal", "include_subagents": True,
                    "show_title": True, "display_title": ""},
    }
    p.update(over)
    return p


def _codes(errors):
    return [(e.field, e.code) for e in errors]


# ── caller typing (§7.1 step 1) ─────────────────────────────────────────────

def test_caller_absent_is_human():
    assert gate.caller_type_error({"kind": "share:create"}) is None


@pytest.mark.parametrize("bad", [["x"], {"id": "x"}, True, False, 3, None])
def test_caller_wrong_type_rejected(bad):
    err = gate.caller_type_error({"caller_session_id": bad})
    assert err is not None and err.field == "caller_session_id" and err.code == "field_forbidden"


# ── create: genuine payloads pass ───────────────────────────────────────────

def test_genuine_session_create_passes():
    assert gate.validate_create(_create_session_payload()) == []


def test_genuine_artifact_create_passes():
    p = {"kind": "share:create", "caller_session_id": "c", "kind_target": "artifact",
         "bookmark_id": 3, "label": "", "password": None, "expires_at": None,
         "options": {"show_title": False, "display_title": "T"}}
    assert gate.validate_create(p) == []


def test_absent_optional_keys_pass():
    p = {"kind": "share:create", "caller_session_id": "c",
         "kind_target": "session", "session_id": "s"}
    assert gate.validate_create(p) == []


def test_mode_live_and_snapshot_pass_absent_mode_passes():
    for opts in ({}, {"mode": "live"}, {"mode": "snapshot"}):
        assert gate.validate_create(_create_session_payload(options=opts)) == []


# ── create: unknown / server-owned keys, any value ──────────────────────────

def test_one_extra_top_level_key_fails():
    errors = gate.validate_create(_create_session_payload(extra=1))
    assert ("extra", "field_forbidden") in _codes(errors)


def test_legacy_share_kind_alias_rejected_for_agents():
    """§7.2: the alias dies as an UNKNOWN KEY — the error names share_kind
    itself, not only the missing kind_target."""
    p = _create_session_payload()
    del p["kind_target"]
    p["share_kind"] = "session"
    errors = gate.validate_create(p)
    assert ("share_kind", "field_forbidden") in _codes(errors)
    assert ("kind_target", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    "value", [True, False, "false", 1, 37, None, "", 0, [1], {}],
)
def test_frozen_at_line_rejected_whatever_value(value):
    errors = gate.validate_create(
        _create_session_payload(options={"frozen_at_line": value}))
    assert ("frozen_at_line", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("value", [True, False, "false", 1, None, "", 0, [1], {}])
def test_notify_on_view_rejected_whatever_value(value):
    errors = gate.validate_create(_create_session_payload(notify_on_view=value))
    assert ("notify_on_view", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("key", ["snapshot_at", "show_timestamps"])
@pytest.mark.parametrize("value", [True, False, "false", 1, None, "", 0, [1], {}])
def test_other_server_owned_option_keys_rejected(key, value):
    errors = gate.validate_create(_create_session_payload(options={key: value}))
    assert (key, "field_forbidden") in _codes(errors)


# ── create: wrong JSON types ────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [["s"], {"id": "s"}, True, 5])
def test_session_id_wrong_type(bad):
    errors = gate.validate_create(_create_session_payload(session_id=bad))
    assert ("session_id", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [["3"], "3", True, False, None, {}])
def test_bookmark_id_wrong_type_incl_bool(bad):
    p = {"kind": "share:create", "caller_session_id": "c", "kind_target": "artifact",
         "bookmark_id": bad}
    errors = gate.validate_create(p)
    assert ("bookmark_id", "field_forbidden") in _codes(errors)


def test_non_object_options_rejected():
    errors = gate.validate_create(_create_session_payload(options=["x"]))
    assert ("options", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("key", ["include_subagents", "show_title"])
def test_boolean_options_reject_boolean_looking_strings(key):
    errors = gate.validate_create(_create_session_payload(options={key: "false"}))
    assert (key, "field_forbidden") in _codes(errors)


def test_display_title_non_string_rejected():
    errors = gate.validate_create(_create_session_payload(options={"display_title": 5}))
    assert ("display_title", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [123, [], True, False, 0, {}])
def test_create_password_non_string_rejected(bad):
    errors = gate.validate_create(_create_session_payload(password=bad))
    assert ("password", "field_forbidden") in _codes(errors)


def test_create_password_none_and_empty_pass():
    assert gate.validate_create(_create_session_payload(password=None)) == []
    assert gate.validate_create(_create_session_payload(password="")) == []


def test_create_expires_empty_and_none_pass():
    assert gate.validate_create(_create_session_payload(expires_at="")) == []
    assert gate.validate_create(_create_session_payload(expires_at=None)) == []


# ── create: kind_target handling ────────────────────────────────────────────

def test_missing_kind_target_rejected():
    p = {"kind": "share:create", "caller_session_id": "c", "session_id": "s"}
    errors = gate.validate_create(p)
    assert ("kind_target", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    ("kind_target", "target_key"),
    [("session", "session_id"), ("artifact", "bookmark_id")],
)
def test_missing_target_id_rejected(kind_target, target_key):
    errors = gate.validate_create({
        "kind": "share:create", "caller_session_id": "c",
        "kind_target": kind_target,
    })
    assert (target_key, "field_forbidden") in _codes(errors)


def test_non_string_kind_target_rejected():
    errors = gate.validate_create(_create_session_payload(kind_target=5))
    assert ("kind_target", "field_forbidden") in _codes(errors)


def test_unknown_kind_target_shape_clean_is_left_to_resolution():
    """A shape-clean unknown value reaches the kind/invalid resolver."""
    p = {"kind": "share:create", "caller_session_id": "c", "kind_target": "bogus"}
    assert gate.validate_create(p) == []


def test_unknown_kind_target_still_validates_union_shape():
    p = {
        "kind": "share:create", "caller_session_id": "c",
        "kind_target": "bogus", "extra": 1,
        "options": {"notify_on_view": True},
    }
    errors = gate.validate_create(p)
    assert ("extra", "field_forbidden") in _codes(errors)
    assert ("notify_on_view", "field_forbidden") in _codes(errors)


# ── update ──────────────────────────────────────────────────────────────────

def _update_payload(**fields):
    return {"kind": "share:update", "caller_session_id": "c",
            "share_id": "shr_1", "fields": fields}


def test_genuine_update_passes():
    assert gate.validate_update(_update_payload(label="x", password="pw",
                                                expires_at=None)) == []


def test_update_fields_options_and_notify_rejected():
    for key in ("options", "notify_on_view"):
        errors = gate.validate_update(_update_payload(**{key: {}}))
        assert (key, "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("bad", [None, False, 0, [], {}])
def test_update_password_non_string_is_layer_one_error(bad):
    errors = gate.validate_update(_update_payload(password=bad))
    assert ("password", "field_forbidden") in _codes(errors)


def test_update_expires_empty_string_rejected_null_passes():
    errors = gate.validate_update(_update_payload(expires_at=""))
    assert ("expires_at", "field_forbidden") in _codes(errors)
    assert gate.validate_update(_update_payload(expires_at=None)) == []


@pytest.mark.parametrize("bad", [["id"], {}, True, 4])
def test_update_share_id_wrong_type(bad):
    p = {"kind": "share:update", "caller_session_id": "c", "share_id": bad, "fields": {}}
    errors = gate.validate_update(p)
    assert ("share_id", "field_forbidden") in _codes(errors)


def test_absent_update_fields_is_valid_shape():
    assert gate.validate_update({
        "kind": "share:update", "caller_session_id": "c", "share_id": "shr_1",
    }) == []


# ── simple ops ──────────────────────────────────────────────────────────────

def test_simple_op_genuine_passes_and_extra_key_fails():
    p = {"kind": "share:revoke", "caller_session_id": "c", "share_id": "shr_1"}
    assert gate.validate_simple(p) == []
    p["surprise"] = 1
    errors = gate.validate_simple(p)
    assert ("surprise", "field_forbidden") in _codes(errors)


@pytest.mark.parametrize(
    ("validator", "payload", "missing", "accepted"),
    [
        (gate.validate_create,
         {"caller_session_id": "c", "kind_target": "session", "session_id": "s"},
         "kind", "a JSON string"),
        (gate.validate_create,
         {"kind": "share:create", "kind_target": "session", "session_id": "s"},
         "caller_session_id", "a JSON string"),
        (gate.validate_create,
         {"kind": "share:create", "caller_session_id": "c", "session_id": "s"},
         "kind_target", 'the JSON string "session" or "artifact"'),
        (gate.validate_create,
         {"kind": "share:create", "caller_session_id": "c", "kind_target": "session"},
         "session_id", "a JSON string"),
        (gate.validate_create,
         {"kind": "share:create", "caller_session_id": "c", "kind_target": "artifact"},
         "bookmark_id", "a JSON integer"),
        (gate.validate_update,
         {"caller_session_id": "c", "share_id": "shr_1", "fields": {}},
         "kind", "a JSON string"),
        (gate.validate_update,
         {"kind": "share:update", "share_id": "shr_1", "fields": {}},
         "caller_session_id", "a JSON string"),
        (gate.validate_update,
         {"kind": "share:update", "caller_session_id": "c", "fields": {}},
         "share_id", "a JSON string"),
        (gate.validate_simple,
         {"caller_session_id": "c", "share_id": "shr_1"},
         "kind", "a JSON string"),
        (gate.validate_simple,
         {"kind": "share:revoke", "share_id": "shr_1"},
         "caller_session_id", "a JSON string"),
        (gate.validate_simple,
         {"kind": "share:revoke", "caller_session_id": "c"},
         "share_id", "a JSON string"),
    ],
)
def test_required_envelope_keys(validator, payload, missing, accepted):
    errors = validator(payload)
    assert (missing, "field_forbidden") in _codes(errors)
    err = next(e for e in errors if e.field == missing)
    assert missing in err.message
    assert accepted in err.message


@pytest.mark.parametrize(
    ("field", "bad"),
    [("kind", 3), ("label", False), ("expires_at", []), ("expires_at", {})],
)
def test_create_common_field_types(field, bad):
    assert (field, "field_forbidden") in _codes(
        gate.validate_create(_create_session_payload(**{field: bad})))


@pytest.mark.parametrize("field", ["mode", "max_display_mode"])
def test_session_string_options_reject_non_strings(field):
    errors = gate.validate_create(_create_session_payload(options={field: False}))
    assert (field, "field_forbidden") in _codes(errors)


@pytest.mark.parametrize("field", ["mode", "max_display_mode", "include_subagents"])
def test_artifact_options_reject_session_only_fields(field):
    p = {
        "kind": "share:create", "caller_session_id": "c",
        "kind_target": "artifact", "bookmark_id": 3,
        "options": {field: "live" if field == "mode" else True},
    }
    assert (field, "field_forbidden") in _codes(gate.validate_create(p))


def test_non_object_update_fields_rejected():
    p = {"kind": "share:update", "caller_session_id": "c",
         "share_id": "shr_1", "fields": []}
    assert ("fields", "field_forbidden") in _codes(gate.validate_update(p))


@pytest.mark.parametrize(
    ("field", "bad"),
    [("label", False), ("expires_at", False), ("expires_at", [])],
)
def test_update_field_types(field, bad):
    assert (field, "field_forbidden") in _codes(
        gate.validate_update(_update_payload(**{field: bad})))


@pytest.mark.parametrize("bad", [["shr_1"], {}, True, 4, None])
def test_simple_share_id_wrong_type(bad):
    p = {"kind": "share:revoke", "caller_session_id": "c", "share_id": bad}
    assert ("share_id", "field_forbidden") in _codes(gate.validate_simple(p))


@pytest.mark.parametrize("op", ["revoke", "unrevoke", "delete", "propagate"])
def test_every_simple_envelope_passes(op):
    p = {"kind": f"share:{op}", "caller_session_id": "c", "share_id": "shr_1"}
    assert gate.validate_simple(p) == []


@pytest.mark.parametrize(
    ("validator", "payload"),
    [
        (gate.validate_create, _create_session_payload(kind="share:bogus")),
        (gate.validate_create, _create_session_payload(kind="share:update")),
        (gate.validate_update, _update_payload() | {"kind": "share:bogus"}),
        (gate.validate_update, _update_payload() | {"kind": "share:create"}),
        (gate.validate_simple, {
            "kind": "share:bogus", "caller_session_id": "c", "share_id": "shr_1"}),
        (gate.validate_simple, {
            "kind": "share:create", "caller_session_id": "c", "share_id": "shr_1"}),
    ],
)
def test_operation_values_are_exact(validator, payload):
    assert ("kind", "field_forbidden") in _codes(validator(payload))


def _assert_all_wrong_json_classes_rejected(validator, base, path, bad_values):
    for bad in bad_values:
        payload = deepcopy(base)
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = bad
        assert (path[-1], "field_forbidden") in _codes(validator(payload)), (
            path, bad)


def test_every_allowed_field_rejects_all_wrong_json_classes():
    bad_string = [None, True, 1, [], {}]
    bad_nullable_string = [True, 1, [], {}]
    bad_object = [None, "x", True, 1, []]
    bad_boolean = [None, "false", 0, 1, [], {}]
    bad_integer = [None, "3", True, False, [], {}]

    session_create = _create_session_payload()
    for field in ("kind", "caller_session_id", "kind_target", "session_id", "label"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, (field,), bad_string)
    for field in ("password", "expires_at"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, (field,), bad_nullable_string)
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, session_create, ("options",), bad_object)
    for field in ("mode", "max_display_mode", "display_title"):
        with_field = _create_session_payload(
            options=session_create["options"] | {field: "normal"})
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, with_field, ("options", field), bad_string)
    for field in ("include_subagents", "show_title"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_create, session_create, ("options", field), bad_boolean)

    artifact_create = {
        "kind": "share:create", "caller_session_id": "c", "kind_target": "artifact",
        "bookmark_id": 3, "label": "", "password": None, "expires_at": None,
        "options": {"show_title": True, "display_title": "T"},
    }
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("bookmark_id",), bad_integer)
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("options", "show_title"), bad_boolean)
    _assert_all_wrong_json_classes_rejected(
        gate.validate_create, artifact_create, ("options", "display_title"), bad_string)

    update = _update_payload(label="x", password="pw", expires_at=None)
    for field in ("kind", "caller_session_id", "share_id"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_update, update, (field,), bad_string)
    _assert_all_wrong_json_classes_rejected(
        gate.validate_update, update, ("fields",), bad_object)
    for field in ("label", "password"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_update, update, ("fields", field), bad_string)
    _assert_all_wrong_json_classes_rejected(
        gate.validate_update, update, ("fields", "expires_at"), bad_nullable_string)

    simple = {"kind": "share:revoke", "caller_session_id": "c", "share_id": "shr_1"}
    for field in ("kind", "caller_session_id", "share_id"):
        _assert_all_wrong_json_classes_rejected(
            gate.validate_simple, simple, (field,), bad_string)


def test_non_empty_password_and_expiry_strings_pass_shape():
    assert gate.validate_create(
        _create_session_payload(password="secret", expires_at="2030-01-01T00:00:00+00:00"),
    ) == []
    assert gate.validate_update(
        _update_payload(password="secret", expires_at="2030-01-01T00:00:00+00:00"),
    ) == []


def test_error_messages_name_the_key_and_accepted_shape():
    errors = gate.validate_create(_create_session_payload(extra=1))
    err = next(e for e in errors if e.field == "extra")
    assert "extra" in err.message and "accepted" in err.message.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_agent_gate.py -x -q`
Expected: FAIL during collection with `ImportError: cannot import name 'share_agent_gate'`.

- [ ] **Step 3: Implement the module**

Create `src/twicc/core/services/share_agent_gate.py`:

```python
"""Layer-1 shape contract for agent share mutations (§7.1/§7.2).

The legitimate producer of an agent payload is the ``twicc share`` CLI —
directly, or through MCP, which renders its calls from the same Typer
signature. The contract is therefore stated over the envelope the wrapper
receives (application fields + the transport's ``kind`` + ``caller_session_id``)
and rejects any other key, or a listed key with a wrong JSON type, with
``field_forbidden`` — BEFORE any ORM access. Server-owned keys
(``frozen_at_line``, ``snapshot_at``, ``show_timestamps``, ``notify_on_view``)
are rejected whatever their value: they are simply not listed.

No direct Django/ORM use in this module. It is not transitively Django-free:
``ShareError`` comes from ``share_mutation`` — already imported wherever this
module is used. The ORM-dependent gate steps and Layer-2 value rules
(settings, scope, provenance, debug refusal, password-clear refusal, frozen
default, share host, expiry) live in ``share_mutation.py``'s
``*_from_payload`` wrappers. They import this module lazily to keep the graph
acyclic.
"""

from __future__ import annotations

from twicc.core.services.share_mutation import ShareError

# Kind → synced-settings gate key (§4).
SETTING_KEYS: dict[str, str] = {
    "session": "allowAgentSessionShares",
    "artifact": "allowAgentArtifactShares",
}


def setting_key_for(kind: str) -> str:
    return SETTING_KEYS[kind]


def caller_type_error(payload: dict) -> ShareError | None:
    """§7.1 step 1: ``caller_session_id``, when present, must be a JSON string —
    checked before any ORM access (resolving it is itself an ORM lookup)."""
    if "caller_session_id" not in payload:
        return None
    if not isinstance(payload["caller_session_id"], str):
        return ShareError("caller_session_id", "field_forbidden",
                          "caller_session_id must be a JSON string")
    return None


# ── type predicates (JSON semantics: bool is NOT an int) ────────────────────

def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_str_or_null(v) -> bool:
    return v is None or isinstance(v, str)


def _is_bool(v) -> bool:
    return isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_dict(v) -> bool:
    return isinstance(v, dict)


_CREATE_COMMON_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "kind_target": (_is_str, 'the JSON string "session" or "artifact"'),
    "label": (_is_str, "a JSON string"),
    "password": (_is_str_or_null, "a JSON string or null"),
    "expires_at": (_is_str_or_null, "a JSON string or null"),
    "options": (_is_dict, "a JSON object"),
}
_SESSION_OPTION_TYPES = {
    "mode": (_is_str, "a JSON string"),
    "max_display_mode": (_is_str, "a JSON string"),
    "include_subagents": (_is_bool, "a literal JSON boolean"),
    "show_title": (_is_bool, "a literal JSON boolean"),
    "display_title": (_is_str, "a JSON string"),
}
_ARTIFACT_OPTION_TYPES = {
    "show_title": (_is_bool, "a literal JSON boolean"),
    "display_title": (_is_str, "a JSON string"),
}


def _forbidden(key: str, message: str) -> ShareError:
    return ShareError(key, "field_forbidden", message)


def _check_keys_and_types(payload: dict, allowed_types: dict, *, context: str) -> list[ShareError]:
    errors: list[ShareError] = []
    accepted = ", ".join(sorted(allowed_types))
    for key, value in payload.items():
        spec = allowed_types.get(key)
        if spec is None:
            errors.append(_forbidden(
                key, f"unknown key {key!r} in {context}; accepted keys: {accepted}"))
            continue
        check, expected = spec
        if not check(value):
            errors.append(_forbidden(key, f"{key} must be {expected}"))
    return errors


def _require(
        payload: dict, errors: list[ShareError], allowed_types: dict,
        *keys: str) -> None:
    for key in keys:
        if key not in payload:
            errors.append(_forbidden(
                key, f"{key} is required and must be {allowed_types[key][1]}"))


def validate_create(payload: dict) -> list[ShareError]:
    """Layer 1 only for ``share:create`` (§7.2)."""
    kind_target = payload.get("kind_target")
    if kind_target == "session":
        types = {**_CREATE_COMMON_TYPES, "session_id": (_is_str, "a JSON string")}
        option_types = _SESSION_OPTION_TYPES
        target_key = "session_id"
    elif kind_target == "artifact":
        types = {**_CREATE_COMMON_TYPES,
                 "bookmark_id": (_is_int, "a JSON integer")}
        option_types = _ARTIFACT_OPTION_TYPES
        target_key = "bookmark_id"
    else:
        # Missing, wrongly typed, or unknown value: validate the union shape
        # before kind resolution. This still names legacy/extra keys and bad
        # option types; a shape-clean unknown string reaches kind/invalid.
        types = {
            **_CREATE_COMMON_TYPES,
            "session_id": (_is_str, "a JSON string"),
            "bookmark_id": (_is_int, "a JSON integer"),
        }
        option_types = {**_SESSION_OPTION_TYPES, **_ARTIFACT_OPTION_TYPES}
        target_key = None

    errors = _check_keys_and_types(payload, types, context=f"share:create ({kind_target})")
    _require(payload, errors, types, "kind", "caller_session_id", "kind_target")
    if isinstance(payload.get("kind"), str) and payload["kind"] != "share:create":
        errors.append(_forbidden("kind", 'kind must be exactly "share:create"'))
    if target_key is not None:
        _require(payload, errors, types, target_key)

    options = payload.get("options")
    if isinstance(options, dict):
        errors += _check_keys_and_types(options, option_types, context="options")
    return errors


_UPDATE_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "share_id": (_is_str, "a JSON string"),
    "fields": (_is_dict, "a JSON object"),
}
_UPDATE_FIELD_TYPES = {
    "label": (_is_str, "a JSON string"),
    "password": (_is_str, "a JSON string"),
    "expires_at": (_is_str_or_null, "a JSON string or null"),
}


def validate_update(payload: dict) -> list[ShareError]:
    """Layer 1 only for ``share:update`` (§7.2)."""
    errors = _check_keys_and_types(payload, _UPDATE_TYPES, context="share:update")
    _require(payload, errors, _UPDATE_TYPES, "kind", "caller_session_id", "share_id")
    if isinstance(payload.get("kind"), str) and payload["kind"] != "share:update":
        errors.append(_forbidden("kind", 'kind must be exactly "share:update"'))
    fields = payload.get("fields")
    if isinstance(fields, dict):
        errors += _check_keys_and_types(fields, _UPDATE_FIELD_TYPES, context="fields")
        if fields.get("expires_at") == "":
            # Not CLI-producible on update (--expires "" normalises to null);
            # null is the explicit clear.
            errors.append(_forbidden(
                "expires_at",
                'expires_at "" is not accepted on update; use null to clear '
                "the expiry, or an ISO 8601 datetime"))
    return errors


_SIMPLE_TYPES = {
    "kind": (_is_str, "a JSON string"),
    "caller_session_id": (_is_str, "a JSON string"),
    "share_id": (_is_str, "a JSON string"),
}


def validate_simple(payload: dict) -> list[ShareError]:
    """Layer 1 for ``share:revoke`` / ``unrevoke`` / ``delete`` / ``propagate``."""
    errors = _check_keys_and_types(payload, _SIMPLE_TYPES, context=str(payload.get("kind")))
    _require(payload, errors, _SIMPLE_TYPES, "kind", "caller_session_id", "share_id")
    allowed = {"share:revoke", "share:unrevoke", "share:delete", "share:propagate"}
    if isinstance(payload.get("kind"), str) and payload["kind"] not in allowed:
        errors.append(_forbidden(
            "kind", "kind must be one of: " + ", ".join(sorted(allowed))))
    return errors
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_agent_gate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/share_agent_gate.py tests/test_share_agent_gate.py`
Subject: `feat(share): add agent payload contract`
```

---

### Task 11: `created_by_session` — column, migration, serializer, select_related sweep

**Files:**
- Modify: `src/twicc/core/models.py` (`Share`, after `notify_on_view`)
- Create: `src/twicc/core/migrations/0133_share_created_by_session.py` (via `makemigrations`)
- Modify: `src/twicc/core/serializers.py` (`serialize_share`)
- Modify: `src/twicc/core/services/share_mutation.py` (`create_share` signature, `_load_share_or_error` select_related)
- Modify: `src/twicc/share/owner_views.py` (both querysets), `src/twicc/asgi.py` (`shares_updated` queryset), `src/twicc/cli/share.py` (both querysets), `src/twicc/share/view_tracking.py` (queryset)
- Test: `tests/test_share_model.py`, `tests/test_share_owner_api.py`
- Create test: `tests/test_share_updates_consumer.py`
- Run-only regression guard: `tests/test_share_mutation.py` (no edit)

**Interfaces:**
- Consumes Task 5's repaired mutation and owner-API state: strict `_parse_expires` behavior and artifact title-option preservation.
- Consumes Task 7's cross-kind `cli/share.py` query state and preserves it while adding eager creator loading.
- Produces: `Share.created_by_session` (nullable FK, `SET_NULL`, `related_name="created_shares"`); `create_share(..., created_by_session=None)`; `serialize_share` field `created_by` with the exact §9 shapes:
  - NULL row → `{"kind": "human_or_legacy", "session": None}`
  - attributed, visible creator → `{"kind": "agent", "session": {"id": …, "title": …, "project_id": …}}`
  - attributed, **hidden** creator → `{"kind": "agent", "session": None}` (the `created_by` channel never carries a hidden session's id or title; the share's own target fields are untouched — §9 "Qualified for self-targets")
- The viewer-facing `serialize_share_public_meta` is NOT touched.
- Produces the eager `created_by_session` query state: every queryset feeding `serialize_share` gains `"created_by_session"` in its `select_related`. This is correctness, not just N+1: the WS consumer and async owner views serialize in async context where a lazy FK load raises `SynchronousOnlyOperation`. The five files above hold the seven call sites (owner_views `_load` + `shares_list` GET; asgi `shares_updated`; cli/share `list_main` + `show_main`; share_mutation `_load_share_or_error`; view_tracking's flush loop).
- Produces the complete §14 provenance-staleness contract: hiding a creator emits `session_removed` and `project_updated`, but no share event; a later qualifying share snapshot/reload serializes the creator as hidden. Task 14 consumes the same serialized states for its badge helper.

- [ ] **Step 1: Write the failing tests**

In `tests/test_share_model.py`, add `import asyncio` and `import orjson`. Define these helpers after the existing `bookmark` fixture:

```python
def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _creator(project, *, sid="agent-1", title="Agent one"):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider="claude_code",
        file_path=f"{sid}.jsonl", type=SessionType.SESSION, title=title,
        created_at=now, last_new_content_at=now, user_message_count=1,
        last_line=7,
    )


def _serialized(share):
    loaded = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).get(id=share.id)
    return serialize_share(loaded)


def test_created_by_session_column_and_serializer_shapes(project, session):
    legacy = Share.objects.create(
        kind="session", token=mint_token(), session=session,
    )
    assert _serialized(legacy)["created_by"] == {
        "kind": "human_or_legacy", "session": None,
    }

    creator = _creator(project)
    attributed = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert _serialized(attributed)["created_by"] == {
        "kind": "agent",
        "session": {
            "id": "agent-1", "title": "Agent one", "project_id": project.id,
        },
    }

    creator.hidden = True
    creator.save(update_fields=["hidden"])
    hidden = _serialized(attributed)["created_by"]
    assert hidden == {"kind": "agent", "session": None}
    assert "agent-1" not in orjson.dumps(hidden).decode()

    creator.hidden = False
    creator.title = ""
    creator.save(update_fields=["hidden", "title"])
    untitled = _serialized(attributed)["created_by"]
    assert untitled == {
        "kind": "agent",
        "session": {"id": "agent-1", "title": "", "project_id": project.id},
    }


def test_self_target_by_hidden_creator_keeps_target_fields(project):
    creator = _creator(project, sid="hidden-self", title="Published target")
    creator.hidden = True
    creator.save(update_fields=["hidden"])
    share = Share.objects.create(
        kind="session", token=mint_token(), session=creator,
        created_by_session=creator,
    )
    data = _serialized(share)
    assert data["created_by"] == {"kind": "agent", "session": None}
    assert data["session_id"] == creator.id
    assert data["target_title"] == "Published target"


def test_created_by_absent_from_public_meta(project, session):
    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert "created_by" not in serialize_share_public_meta(share)


def test_serialize_share_in_async_context_no_lazy_load(project, session):
    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    loaded = Share.objects.select_related(
        "session", "artifact_bookmark", "created_by_session",
    ).get(id=share.id)

    async def go():
        return serialize_share(loaded)

    assert _run(go())["created_by"]["session"]["id"] == creator.id


def test_hide_emits_no_share_event_and_next_snapshot_hides_creator(
        project, session, monkeypatch):
    from twicc.core.services import session_visibility

    creator = _creator(project)
    share = Share.objects.create(
        kind="session", token=mint_token(), session=session,
        created_by_session=creator,
    )
    assert _serialized(share)["created_by"]["session"]["id"] == creator.id

    sent = []

    class RecordingLayer:
        async def group_send(self, group, payload):
            assert group == "updates"
            sent.append(payload["data"]["type"])

    monkeypatch.setattr(session_visibility, "_check_hidden_invariants", lambda row: [])
    # Keep the real _apply_flip path. The recording layer then observes any
    # broadcast added anywhere in the full hide path. Stub only unrelated
    # expensive work that happens after the hidden flag is saved.
    monkeypatch.setattr(
        "twicc.projects.update_project_metadata", lambda project_id: None,
    )
    monkeypatch.setattr(
        "twicc.search.reindex_session", lambda session_id: None,
    )
    monkeypatch.setattr(session_visibility, "get_channel_layer", lambda: RecordingLayer())

    result = _run(session_visibility.hide_session(creator))
    assert result.success
    assert sent == ["session_removed", "project_updated"]
    assert _serialized(share)["created_by"] == {"kind": "agent", "session": None}
```

Append this owner-API test to `tests/test_share_owner_api.py`. It traverses the
real async list queryset, not a preloaded serializer object:

```python
def test_list_serializes_visible_creator_without_async_lazy_load(
        client, session, share_host):
    now = djtz.now()
    creator = Session.objects.create(
        id="agent-owner", project=session.project, provider="claude_code",
        file_path="agent-owner.jsonl", type=SessionType.SESSION,
        title="Creator", created_at=now, last_new_content_at=now,
        user_message_count=1, last_line=4,
    )
    share = _share(session, created_by_session=creator)
    response = _run(client.get("/api/shares/"))
    assert response.status_code == 200
    row = next(
        item for item in orjson.loads(response.content)["shares"]
        if item["id"] == share.id
    )
    assert row["created_by"] == {
        "kind": "agent",
        "session": {
            "id": creator.id,
            "title": "Creator",
            "project_id": session.project_id,
        },
    }
```

Create `tests/test_share_updates_consumer.py` with this complete content. It
traverses the real `WSConsumer` queryset in async context:

```python
import asyncio

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone as djtz

from twicc.asgi import WSConsumer
from twicc.core.models import Project, Session, SessionType, Share
from twicc.core.services.share_tokens import mint_token


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def attributed_share(transactional_db):
    now = djtz.now()
    project = Project.objects.create(id="-tmp-share-updates", directory="/tmp/share-updates")
    target = Session.objects.create(
        id="target-updates", project=project, provider="claude_code",
        file_path="target-updates.jsonl", type=SessionType.SESSION,
        title="Target", created_at=now, last_new_content_at=now,
    )
    creator = Session.objects.create(
        id="creator-updates", project=project, provider="claude_code",
        file_path="creator-updates.jsonl", type=SessionType.SESSION,
        title="Creator", created_at=now, last_new_content_at=now,
    )
    share = Share.objects.create(
        kind="session", token=mint_token(), session=target,
        created_by_session=creator,
    )
    return share, creator


def test_initial_shares_updated_serializes_visible_creator(
        attributed_share, monkeypatch, settings):
    share, creator = attributed_share
    settings.TWICC_PASSWORD_HASH = ""

    class Registry:
        def set_broadcast_callback(self, callback):
            self.callback = callback

    registry = Registry()
    monkeypatch.setattr("twicc.asgi.scope_remote_access_blocked", lambda scope: False)
    monkeypatch.setattr("twicc.asgi.get_agent_manager_registry", lambda: registry)

    async def scenario():
        comm = WebsocketCommunicator(
            WSConsumer.as_asgi(), "/ws/?subscribe=shares_updated",
        )
        connected, _ = await comm.connect()
        assert connected
        message = await comm.receive_json_from(timeout=2)
        assert message["type"] == "shares_updated"
        row = next(item for item in message["shares"] if item["id"] == share.id)
        assert row["created_by"] == {
            "kind": "agent",
            "session": {
                "id": creator.id,
                "title": "Creator",
                "project_id": creator.project_id,
            },
        }
        await comm.disconnect()

    _run(scenario())
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_model.py -x -q`
Expected: FAIL (`created_by_session` unknown field).

- [ ] **Step 3: Add the model field**

In `src/twicc/core/models.py`, `Share` — insert between these two exact lines:

```python
    notify_on_view = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

the new field:

```python
    # Agent provenance (agent-sharing design §9): the session whose agent
    # created this share through the gated CLI/MCP surface. NULL = human-created
    # or legacy/unattributed — pre-gate rows are unknowable, and NULL fails the
    # §6 provenance test, so unattributed rows stay out of agent management
    # (except revoke, A7). SET_NULL: attribution is metadata, never a lifecycle
    # edge — a share must not die with its creator session.
    created_by_session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_shares",
    )
```

- [ ] **Step 4: Create the migration (file only — never migrate)**

First sanity-check the data dir resolution:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','twicc.settings'); django.setup(); from django.conf import settings; print(settings.DATABASES['default']['NAME'])"
```

Expected: a path **inside the worktree**. After the path is confirmed inside
the worktree, run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && TWICC_DATA_DIR=$PWD uv run --active python -m django makemigrations core --settings=twicc.settings
```

The generated file must be `0133_…` (latest is `0132_peermessage_title.py`), purely additive, importing nothing from app code. Do NOT run `migrate` — pytest builds its own schema, and the user applies the migration to their instance.

- [ ] **Step 5: Wire `create_share` and the serializer**

1. `create_share` — add keyword param `created_by_session=None` and pass it into the `Share(...)` constructor. Old signature (verbatim):
   ```python
   async def create_share(
       kind: str,
       *,
       session=None,
       bookmark=None,
       label: str = "",
       options: dict | None = None,
       password: str | None = None,
       expires_at: datetime | None = None,
       notify_on_view: bool = False,
   ) -> ShareMutationResult:
   ```
   New:
   ```python
   async def create_share(
       kind: str,
       *,
       session=None,
       bookmark=None,
       label: str = "",
       options: dict | None = None,
       password: str | None = None,
       expires_at: datetime | None = None,
       notify_on_view: bool = False,
       created_by_session=None,
   ) -> ShareMutationResult:
   ```
   and in the constructor: `created_by_session=created_by_session,`. (Task 12 passes the resolved caller; the REST path never does — NULL.)
2. `serialize_share` — insert immediately ABOVE `serialize_share`'s final line, which is exactly:
   ```python
    return data
   ```
   (NOT inside the `data = {...}` literal — the inserted code is statements; the `"url_path"` entry stays the dict's last key so no exact-equality test moves):
   ```python
    # Agent provenance (agent-sharing design §9). The created_by channel
    # follows the frontend hidden rule: a hidden creator serializes as
    # {"kind": "agent", "session": null} — never its id or title. The
    # share's own target fields are deliberately NOT subject to this rule
    # (a self-target share of a hidden session still shows session_id /
    # target_title: the transcript it publishes reveals far more).
    creator_id = share.created_by_session_id
    if creator_id is None:
        data["created_by"] = {"kind": "human_or_legacy", "session": None}
    else:
        creator = share.created_by_session
        if creator.hidden:
            data["created_by"] = {"kind": "agent", "session": None}
        else:
            data["created_by"] = {"kind": "agent", "session": {
                "id": creator.id,
                "title": creator.title,
                "project_id": creator.project_id,
            }}
   ```
3. Add `"created_by_session"` to the `select_related` call in each of the seven sites (every current occurrence of `select_related("session", "artifact_bookmark")` in `src/`):
   - `src/twicc/share/owner_views.py` `_load` and `shares_list` GET
   - `src/twicc/asgi.py` `shares_updated` block (`Share.objects.select_related("session", "artifact_bookmark").all()`)
   - `src/twicc/cli/share.py` `list_main` and `show_main`
   - `src/twicc/core/services/share_mutation.py` `_load_share_or_error`
   - `src/twicc/share/view_tracking.py` flush loop
   Verify completeness mechanically: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && rg -n 'select_related\("session", "artifact_bookmark"\)' src/` must return zero hits after the sweep (every former hit now includes the third FK).

- [ ] **Step 6: Run the tests + full suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_model.py tests/test_share_owner_api.py tests/test_share_updates_consumer.py tests/test_share_mutation.py -q && uv run --active pytest -q`
Expected: PASS. Then remind the user (at task end, in the progress message): the migration must be applied by their own running instance.

- [ ] **Step 7: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/models.py src/twicc/core/migrations/0133_share_created_by_session.py src/twicc/core/serializers.py src/twicc/core/services/share_mutation.py src/twicc/share/owner_views.py src/twicc/asgi.py src/twicc/cli/share.py src/twicc/share/view_tracking.py tests/test_share_model.py tests/test_share_owner_api.py tests/test_share_updates_consumer.py`
Subject: `feat(share): record agent creator provenance`
```

---

### Task 12: Gate wiring in the six `*_from_payload` wrappers

**Files:**
- Modify: `src/twicc/core/services/share_mutation.py` (the six wrappers, ~lines 408-467)
- Test: `tests/test_share_gate_wiring.py` (create), `tests/test_share_consumer.py` (extend — the §14 "Update shape" WS password halves, Step 1b), `tests/test_share_owner_api.py` (extend — human REST bypass)

**Interfaces:**
- Consumes:
  - Task 1: `allowAgentSessionShares: bool` and `allowAgentArtifactShares: bool` via `read_synced_settings()`.
  - Task 3: `descendant_ids(session_id: str) -> set[str]`, called through `sync_to_async`.
  - Task 5: `_parse_expires(payload) -> tuple[datetime | None, ShareError | None]`.
  - Task 8: `run_create_session(..., mode: str | None, options: dict, ...)`, which preserves absent `options.mode`.
  - Task 9: optional `caller_session_id: str` on every share mutation payload.
  - Task 10: `setting_key_for(kind) -> str`; `caller_type_error(payload) -> ShareError | None`; Layer-1 `validate_create(payload) -> list[ShareError]`, `validate_update(payload) -> list[ShareError]`, `validate_simple(payload) -> list[ShareError]`.
  - Task 11: `Share.created_by_session` nullable FK and `create_share(..., created_by_session=None)`.
- Produces: the §7.1 gate algorithm on all six ops. Human payloads (`caller_session_id` absent, or present-but-unknown id) keep current behaviour bit-for-bit. Task 12 itself owns the Layer-2 debug and empty-update-password checks, after resolution, setting, and scope. Error codes: `agent_sharing_disabled` (field `settings`), `out_of_scope` (field = target id on create, `share_id` on the four managed ops), `share_host_unset` (field `share_base_url`), `field_forbidden`, and `display_mode_forbidden`.
- Ordering contract: caller type → caller resolution → Layer 1 → target/share resolution → kind setting → scope → step-6 Layer-2 and host/default/expiry value rules. Layer-2 `debug` and empty-update-password checks never mask `not_found`, `agent_sharing_disabled`, or `out_of_scope`.
- **User decision:** precedence inside §7.1 step 6 is intentionally unspecified. A payload that violates multiple step-6 rules returns exactly one applicable error. Tests accept any applicable code. They do not define an order between debug, host, and expiry.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_gate_wiring.py` with this complete content:

```python
"""End-to-end agent gate wiring and §7.1 precedence for all six wrappers."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import orjson
import pytest
from django.utils import timezone as djtz

from twicc import paths
from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType, Share
from twicc.core.services import share_mutation


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mk(project, sid, *, spawned_by=None, spawn_root=None, parent_session=None):
    now = djtz.now()
    return Session.objects.create(
        id=sid, project=project, provider="claude_code", file_path=f"{sid}.jsonl",
        type=SessionType.SUBAGENT if parent_session else SessionType.SESSION,
        spawned_by=spawned_by, spawn_root=spawn_root, parent_session=parent_session,
        created_at=now, last_new_content_at=now, last_line=21,
    )


@pytest.fixture
def project(transactional_db):
    return Project.objects.create(id="-tmp-gate", directory="/tmp/gate")


@pytest.fixture
def tree(project):
    parent = _mk(project, "parent")
    parent.spawn_root = parent
    parent.save(update_fields=["spawn_root"])
    caller = _mk(project, "caller", spawned_by=parent, spawn_root=parent)
    child = _mk(project, "child", spawned_by=caller, spawn_root=parent)
    sibling = _mk(project, "sibling", spawned_by=parent, spawn_root=parent)
    unrelated = _mk(project, "unrelated")
    subagent = _mk(project, "subagent", parent_session=caller)
    return SimpleNamespace(
        parent=parent, caller=caller, child=child, sibling=sibling,
        unrelated=unrelated, subagent=subagent,
    )


@pytest.fixture
def artifacts_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(paths, "get_data_dir", lambda: data_dir)
    return data_dir / "artifacts"


@pytest.fixture
def bookmarks(tree, project, artifacts_root):
    out = {}
    for session in (tree.child, tree.unrelated):
        path = artifacts_root / session.id / "demo" / "index.html"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"<html>{session.id}</html>".encode())
        out[session.id] = ArtifactBookmark.objects.create(
            session=session, project=project, relative_path="demo/index.html",
            name=session.id, scope=PinMode.PROJECT,
        )
    return out


@pytest.fixture
def settings_state(monkeypatch):
    state = {
        "allowAgentSessionShares": False,
        "allowAgentArtifactShares": False,
        "shareBaseUrl": "share.example.com",
    }
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings", lambda: dict(state))
    return state


@pytest.fixture(autouse=True)
def _passthrough_db_write_lock(monkeypatch):
    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough,
    )


def _create_payload(target, caller, **over):
    payload = {
        "kind": "share:create", "caller_session_id": caller.id,
        "kind_target": "session", "session_id": target.id,
        "label": "", "password": None, "expires_at": None, "options": {},
    }
    payload.update(over)
    return payload


def _human_share(target, *, creator=None, snapshot=False):
    result = _run(share_mutation.create_share(
        "session", session=target,
        options={"mode": "snapshot" if snapshot else "live"},
        created_by_session=creator,
    ))
    assert result.success
    return Share.objects.select_related("session", "created_by_session").get(id=result.share_id)


def _managed_call(op, share, caller, *, fields=None):
    payload = {
        "kind": f"share:{op}", "caller_session_id": caller.id,
        "share_id": share.id,
    }
    if op == "update":
        payload["fields"] = fields if fields is not None else {"label": "updated"}
    fn = {
        "update": share_mutation.update_share_from_payload,
        "revoke": share_mutation.revoke_share_from_payload,
        "unrevoke": share_mutation.unrevoke_share_from_payload,
        "delete": share_mutation.delete_share_from_payload,
        "propagate": share_mutation.propagate_share_from_payload,
    }[op]
    return _run(fn(payload))


def _share_for_op(op, target, *, creator=None):
    share = _human_share(target, creator=creator, snapshot=op == "propagate")
    if op == "unrevoke":
        share.revoked_at = djtz.now()
        share.save(update_fields=["revoked_at"])
    return share


def _error(result):
    assert not result.success and result.errors
    return result.errors[0]


def test_gate_off_rejects_create(tree, settings_state):
    err = _error(_run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller))))
    assert (err.field, err.code) == ("settings", "agent_sharing_disabled")
    assert "session" in err.message and "Settings → Sharing" in err.message


@pytest.mark.parametrize("op", ["update", "revoke", "unrevoke", "delete", "propagate"])
def test_gate_off_rejects_each_loaded_operation(op, tree, settings_state):
    share = _share_for_op(op, tree.caller)
    err = _error(_managed_call(op, share, tree.caller))
    assert (err.field, err.code) == ("settings", "agent_sharing_disabled")


def test_kind_settings_are_independent(tree, bookmarks, settings_state):
    settings_state["allowAgentSessionShares"] = True
    assert _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller))).success
    artifact = {
        "kind": "share:create", "caller_session_id": tree.caller.id,
        "kind_target": "artifact", "bookmark_id": bookmarks[tree.child.id].id,
        "label": "", "password": None, "expires_at": None, "options": {},
    }
    assert _error(_run(share_mutation.create_share_from_payload(artifact))).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = False
    settings_state["allowAgentArtifactShares"] = True
    assert _run(share_mutation.create_share_from_payload(artifact)).success
    assert _error(_run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))).code == "agent_sharing_disabled"


def test_human_and_unknown_caller_bypass_gate(tree, settings_state):
    human = _create_payload(
        tree.caller, tree.caller,
        options={"max_display_mode": "debug"}, notify_on_view=True,
    )
    del human["caller_session_id"]
    created = _run(share_mutation.create_share_from_payload(human))
    assert created.success
    share = Share.objects.get(id=created.share_id)
    assert share.options["mode"] == "live"
    assert share.options["max_display_mode"] == "debug"
    assert _managed_call("update", share, SimpleNamespace(id="ghost")).success
    assert _managed_call("revoke", share, SimpleNamespace(id="ghost")).success
    ghost = _create_payload(tree.caller, SimpleNamespace(id="ghost"))
    assert _run(share_mutation.create_share_from_payload(ghost)).success


@pytest.mark.parametrize(
    ("target_name", "allowed"),
    [("caller", True), ("child", True), ("parent", False), ("sibling", False),
     ("unrelated", False), ("subagent", False)],
)
def test_create_scope(target_name, allowed, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(
        _create_payload(getattr(tree, target_name), tree.caller)))
    assert result.success is allowed
    if not allowed:
        assert (_error(result).field, _error(result).code) == ("session_id", "out_of_scope")


def test_artifact_scope(tree, bookmarks, settings_state):
    settings_state["allowAgentArtifactShares"] = True
    for session, allowed in ((tree.child, True), (tree.unrelated, False)):
        result = _run(share_mutation.create_share_from_payload({
            "kind": "share:create", "caller_session_id": tree.caller.id,
            "kind_target": "artifact", "bookmark_id": bookmarks[session.id].id,
            "label": "", "password": None, "expires_at": None, "options": {},
        }))
        assert result.success is allowed
        if not allowed:
            assert (_error(result).field, _error(result).code) == ("bookmark_id", "out_of_scope")


@pytest.mark.parametrize("op", ["update", "unrevoke", "delete", "propagate"])
@pytest.mark.parametrize("creator_name", [None, "unrelated"])
def test_managed_ops_refuse_null_or_foreign_provenance(
        op, creator_name, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    creator = getattr(tree, creator_name) if creator_name else None
    share = _share_for_op(op, tree.caller, creator=creator)
    err = _error(_managed_call(op, share, tree.caller))
    assert (err.field, err.code) == ("share_id", "out_of_scope")
    assert err.message == (
        "this share was created outside your spawn subtree (or by the user); "
        "you can manage only shares created by yourself or any session in your spawn subtree"
    )


@pytest.mark.parametrize("op", ["update", "unrevoke", "delete", "propagate"])
@pytest.mark.parametrize("creator_name", ["caller", "child"])
def test_managed_ops_allow_subtree_provenance(op, creator_name, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _share_for_op(op, tree.caller, creator=getattr(tree, creator_name))
    assert _managed_call(op, share, tree.caller).success


def test_revoke_ignores_provenance_but_not_setting(tree, settings_state):
    share = _human_share(tree.caller)
    settings_state["allowAgentSessionShares"] = True
    assert _managed_call("revoke", share, tree.caller).success
    share.revoked_at = None
    share.save(update_fields=["revoked_at"])
    settings_state["allowAgentSessionShares"] = False
    assert _error(_managed_call("revoke", share, tree.caller)).code == "agent_sharing_disabled"


def test_create_layer_two_precedence(tree, settings_state):
    missing = _create_payload(
        SimpleNamespace(id="missing"), tree.caller,
        options={"max_display_mode": "debug"},
    )
    result = _run(share_mutation.create_share_from_payload(missing))
    assert (_error(result).field, _error(result).code) == (
        "session_id", "not_found")
    existing = _create_payload(
        tree.caller, tree.caller, options={"max_display_mode": "debug"})
    assert _error(_run(share_mutation.create_share_from_payload(existing))).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = True
    outside = _create_payload(
        tree.parent, tree.caller, options={"max_display_mode": "debug"})
    assert _error(_run(share_mutation.create_share_from_payload(outside))).code == "out_of_scope"
    final = _run(share_mutation.create_share_from_payload(existing))
    assert (_error(final).field, _error(final).code) == (
        "max_display_mode", "display_mode_forbidden")


def test_update_layer_two_precedence(tree, settings_state):
    missing = Share(id="shr_missing")
    result = _run(share_mutation.update_share_from_payload({
        "kind": "share:update", "caller_session_id": tree.caller.id,
        "share_id": missing.id, "fields": {"password": ""},
    }))
    assert (_error(result).field, _error(result).code) == ("share_id", "not_found")
    disabled = _human_share(tree.caller)
    assert _error(_managed_call(
        "update", disabled, tree.caller, fields={"password": ""})).code == "agent_sharing_disabled"
    settings_state["allowAgentSessionShares"] = True
    outside = _human_share(tree.caller, creator=tree.unrelated)
    assert _error(_managed_call(
        "update", outside, tree.caller, fields={"password": ""})).code == "out_of_scope"
    allowed = _human_share(tree.caller, creator=tree.caller)
    final = _managed_call(
        "update", allowed, tree.caller, fields={"password": ""})
    assert (_error(final).field, _error(final).code) == (
        "password", "field_forbidden")


def test_agent_frozen_default_explicit_live_and_human_live(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    frozen = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    frozen_row = Share.objects.get(id=frozen.share_id)
    assert frozen_row.options["mode"] == "snapshot"
    assert frozen_row.options["frozen_at_line"] == tree.caller.last_line
    live = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, options={"mode": "live"})))
    assert Share.objects.get(id=live.share_id).options["mode"] == "live"
    human = _create_payload(tree.caller, tree.caller)
    del human["caller_session_id"]
    human_result = _run(share_mutation.create_share_from_payload(human))
    assert Share.objects.get(id=human_result.share_id).options["mode"] == "live"


def test_agent_expiry_errors_do_not_widen(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    before = Share.objects.count()
    bad_create = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, expires_at="not-a-date")))
    assert (_error(bad_create).field, _error(bad_create).code) == ("expires_at", "invalid")
    assert Share.objects.count() == before
    share = _human_share(tree.caller, creator=tree.caller)
    share.expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    share.save(update_fields=["expires_at"])
    bad_update = _managed_call(
        "update", share, tree.caller, fields={"expires_at": "garbage"})
    assert _error(bad_update).code == "invalid"
    share.refresh_from_db()
    assert share.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_share_host_and_attribution(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    settings_state["shareBaseUrl"] = ""
    agent = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    assert (_error(agent).field, _error(agent).code) == ("share_base_url", "share_host_unset")
    human = _create_payload(tree.caller, tree.caller)
    del human["caller_session_id"]
    assert _run(share_mutation.create_share_from_payload(human)).success
    settings_state["shareBaseUrl"] = "share.example.com"
    attributed = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller)))
    assert Share.objects.get(id=attributed.share_id).created_by_session_id == tree.caller.id
    assert Share.objects.get(id=_run(
        share_mutation.create_share_from_payload(human)).share_id).created_by_session_id is None


@pytest.mark.parametrize("bad", [["x"], {"id": "x"}, True])
def test_wrong_types_reject_instead_of_fail(bad, tree, settings_state):
    from twicc.drop_requests_watcher import execute_drop_payload
    payload = _create_payload(tree.caller, tree.caller)
    payload["caller_session_id"] = bad
    status = _run(execute_drop_payload(payload, "share:create"))
    assert status["status"] == "rejected"
    assert status["errors"][0]["code"] == "field_forbidden"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("share:create", {"kind_target": "session", "session_id": ["bad"]}),
        ("share:create", {"kind_target": "artifact", "bookmark_id": {"bad": 1}}),
        ("share:update", {"share_id": {"bad": 1}, "fields": {}}),
        ("share:delete", {"share_id": True}),
    ],
)
def test_wrong_target_id_types_reject_through_transport(
        kind, payload, tree, settings_state):
    from twicc.drop_requests_watcher import execute_drop_payload

    envelope = {"kind": kind, "caller_session_id": tree.caller.id, **payload}
    status = _run(execute_drop_payload(envelope, kind))
    assert status["status"] == "rejected"
    assert len(status["errors"]) == 1
    assert status["errors"][0]["code"] == "field_forbidden"


@pytest.mark.parametrize(
    "bad_options",
    [["not-an-object"], {"show_title": "false"}],
)
def test_bad_options_fail_before_target_resolution(
        bad_options, tree, settings_state, monkeypatch):
    async def must_not_resolve(payload):
        raise AssertionError("target resolution ran before Layer-1 shape validation")

    monkeypatch.setattr(share_mutation, "_resolve_target_from_payload", must_not_resolve)
    result = _run(share_mutation.create_share_from_payload(
        _create_payload(tree.caller, tree.caller, options=bad_options)))
    assert _error(result).code == "field_forbidden"


@pytest.mark.parametrize("password_case", ["absent", "null", "empty", "set"])
def test_agent_create_password_storage(password_case, tree, settings_state):
    from twicc.auth.hashers import verify_password

    settings_state["allowAgentSessionShares"] = True
    payload = _create_payload(tree.caller, tree.caller)
    if password_case == "absent":
        del payload["password"]
    elif password_case == "null":
        payload["password"] = None
    elif password_case == "empty":
        payload["password"] = ""
    else:
        payload["password"] = "secret"
    result = _run(share_mutation.create_share_from_payload(payload))
    assert result.success
    stored = Share.objects.get(id=result.share_id).password_hash
    if password_case == "set":
        assert verify_password("secret", stored)
    else:
        assert stored == ""


def test_agent_create_valid_expiry_is_stored(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller,
        expires_at="2031-02-03T04:05:06+00:00",
    )))
    assert result.success
    assert Share.objects.get(id=result.share_id).expires_at == datetime(
        2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc,
    )


@pytest.mark.parametrize("display_mode", ["conversation", "simplified", "normal"])
def test_each_non_debug_display_mode_creates(
        display_mode, tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller,
        options={"max_display_mode": display_mode},
    )))
    assert result.success
    assert Share.objects.get(id=result.share_id).options["max_display_mode"] == display_mode


def test_agent_update_expiry_set_absent_and_clear(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _human_share(tree.caller, creator=tree.caller)
    set_result = _managed_call(
        "update", share, tree.caller,
        fields={"expires_at": "2031-02-03T04:05:06+00:00"},
    )
    assert set_result.success
    share.refresh_from_db()
    stored = datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    assert share.expires_at == stored

    absent = _run(share_mutation.update_share_from_payload({
        "kind": "share:update", "caller_session_id": tree.caller.id,
        "share_id": share.id,
    }))
    assert absent.success
    share.refresh_from_db()
    assert share.expires_at == stored

    cleared = _managed_call(
        "update", share, tree.caller, fields={"expires_at": None})
    assert cleared.success
    share.refresh_from_db()
    assert share.expires_at is None


def test_human_invalid_expiry_precedes_malformed_options(tree):
    payload = _create_payload(
        tree.caller, tree.caller, expires_at="not-a-date", options=7)
    del payload["caller_session_id"]
    result = _run(share_mutation.create_share_from_payload(payload))
    assert (_error(result).field, _error(result).code) == ("expires_at", "invalid")


def test_step_six_conflict_returns_one_applicable_error(
        tree, settings_state):
    """User decision: no precedence contract exists inside §7.1 step 6."""
    settings_state["allowAgentSessionShares"] = True
    settings_state["shareBaseUrl"] = ""
    result = _run(share_mutation.create_share_from_payload(_create_payload(
        tree.caller, tree.caller, expires_at="not-a-date",
        options={"max_display_mode": "debug"},
    )))
    assert not result.success
    assert len(result.errors) == 1
    assert result.errors[0].code in {
        "display_mode_forbidden", "share_host_unset", "invalid",
    }


def test_both_real_transport_envelopes(tree, settings_state, tmp_path, monkeypatch):
    from twicc.cli import share_mutation as cli_share_mutation
    from twicc.cli._drop_request import transport
    from twicc.cli._drop_request.drop_file import write_drop_file
    from twicc.drop_requests_watcher import execute_drop_payload

    settings_state["allowAgentSessionShares"] = True
    captured = []
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: tree.caller)
    monkeypatch.setattr(
        "twicc.cli.share_mutation._run_drop",
        lambda payload, **kwargs: captured.append(payload),
    )
    cli_share_mutation.run_create_session(
        session_id=tree.caller.id, label="", password=None, expires_at=None,
        mode=None, options={}, timeout=30,
    )
    payload = captured[0]

    def backend_cli_side(candidate):
        transport.ensure_server_available()
        submission = transport.submit(candidate, kind="share:create")
        outcome = transport.wait(submission, timeout_seconds=10)
        submission.cleanup()
        return outcome

    async def through_backend_transport(candidate):
        loop = asyncio.get_running_loop()
        token = transport.backend_loop.set(loop)
        try:
            # Match MCP: the synchronous CLI transport runs in a worker thread
            # while execute_drop_payload runs on the backend event loop.
            return await asyncio.to_thread(backend_cli_side, candidate)
        finally:
            transport.backend_loop.reset(token)

    valid_backend = _run(through_backend_transport(payload))
    assert valid_backend.status == "created"
    invalid_backend = _run(through_backend_transport(payload | {"extra": 1}))
    assert invalid_backend.status == "rejected"
    assert invalid_backend.data["errors"][0]["code"] == "field_forbidden"

    drop_dir = tmp_path / "drops"
    monkeypatch.setattr(
        "twicc.cli._drop_request.drop_file.get_drop_requests_dir", lambda: drop_dir)
    for candidate, expected in (
        (payload, "created"), (payload | {"extra": 1}, "rejected"),
    ):
        dropped = write_drop_file(candidate, kind="share:create")
        envelope = orjson.loads(dropped.path.read_bytes())
        assert envelope["payload"]["kind"] == "share:create"
        result = _run(execute_drop_payload(envelope["payload"], "share:create"))
        assert result["status"] == expected
        if expected == "rejected":
            assert result["errors"][0]["code"] == "field_forbidden"


def test_update_value_rules_end_to_end(tree, settings_state):
    settings_state["allowAgentSessionShares"] = True
    share = _human_share(tree.caller, creator=tree.caller)
    old_hash = share.password_hash
    assert _managed_call(
        "update", share, tree.caller, fields={"password": "new-password"}).success
    share.refresh_from_db()
    assert share.password_hash and share.password_hash != old_hash
    assert _managed_call(
        "update", share, tree.caller, fields={"label": "allowed"}).success
    forbidden = _managed_call(
        "update", share, tree.caller, fields={"options": {"mode": "live"}})
    assert (_error(forbidden).field, _error(forbidden).code) == (
        "options", "field_forbidden")
```

This file now implements every former test-group bullet. The separate Step 1b file keeps the WebSocket password halves because it owns the communicator harness.

- [ ] **Step 1b: Pin the WS password behaviour**

Append to `tests/test_share_consumer.py` (reuse its `session` fixture, `_share`, `_communicator`, `_run` helpers; note this file has **zero** password coverage today, so both tests are new ground). These two are NOT TDD-failing tests: the consumer already enforces the connect-time password fingerprint and forwards later items through its separate `session_items_added` branch. They pin, as executable spec, the §8/§14 "Update shape" promises the `twicc-share` skill will document (replacing a password invalidates viewer grants for NEW connects, fingerprint-bound, while an already-open live WS keeps streaming). Expect them to PASS on first run:

```python
def test_password_change_keeps_open_socket_streaming(session, monkeypatch):
    """§14 Update shape / §8: replacing a password never cuts an open live WS —
    the socket receives share_updated, then still streams later session items."""
    from twicc.auth.hashers import hash_password
    from twicc.core.services import share_mutation
    from twicc.core.services.share_tokens import password_fingerprint
    from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

    async def _passthrough(coro_factory):
        return await coro_factory()
    monkeypatch.setattr(
        "twicc.core.services.share_mutation.run_under_db_write_lock", _passthrough)

    share = _share(session, options={"mode": "live"},
                   password_hash=hash_password("old-pw"))

    async def scenario():
        comm = _communicator(share.token)
        comm.scope["session"] = {
            SHARE_GRANTS_SESSION_KEY: {share.id: password_fingerprint(share.password_hash)}}
        connected, _ = await comm.connect()
        assert connected
        old_hash = share.password_hash
        result = await share_mutation.patch_share(share, {"password": "new-pw"})
        assert result.success
        assert share.password_hash != old_hash          # grants invalidated
        msg = await comm.receive_json_from(timeout=2)   # broadcast reached the socket
        assert msg["type"] == "share_meta"
        # Prove the separate item-streaming branch still works after the
        # password update. Receiving share_meta alone does not prove this.
        item = {
            "line_num": 6, "display_level": 1, "content": "{}",
            "kind": "assistant_message",
        }
        layer = get_channel_layer()
        await layer.group_send("updates", {"type": "broadcast", "data": {
            "type": "session_items_added", "session_id": session.id,
            "items": [item],
        }})
        streamed = await comm.receive_json_from(timeout=2)
        assert streamed == {
            "type": "share_items_added", "session_id": session.id,
            "items": [item],
        }
        await comm.disconnect()

    _run(scenario())


def test_password_change_gates_new_connects_on_new_fingerprint(session):
    """§14 Update shape / §8: after a password change, a connect carrying the
    OLD grant fingerprint is refused; one carrying the new fingerprint passes."""
    from twicc.auth.hashers import hash_password
    from twicc.core.services.share_tokens import password_fingerprint
    from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

    share = _share(session, options={"mode": "live"},
                   password_hash=hash_password("old-pw"))
    old_fp = password_fingerprint(share.password_hash)
    share.password_hash = hash_password("new-pw")
    share.save(update_fields=["password_hash"])
    new_fp = password_fingerprint(share.password_hash)

    async def scenario():
        stale = _communicator(share.token)
        stale.scope["session"] = {SHARE_GRANTS_SESSION_KEY: {share.id: old_fp}}
        connected, _ = await stale.connect()
        assert not connected                            # old grant is dead
        fresh = _communicator(share.token)
        fresh.scope["session"] = {SHARE_GRANTS_SESSION_KEY: {share.id: new_fp}}
        connected2, _ = await fresh.connect()
        assert connected2                               # new password's grant works
        await fresh.disconnect()

    _run(scenario())
```

(The consumer reads grants from `self.scope.get("session")` with plain-dict `.get` — setting `comm.scope["session"]` to a dict is the file-consistent way to simulate a granted browser session. `hash_password` lives in `twicc.auth.hashers`; `password_fingerprint` lives in `twicc.core.services.share_tokens`; `mint_token` is already imported by the file.)

- [ ] **Step 1c: Pin the human owner-REST bypass at its real boundaries**

Append these complete tests to `tests/test_share_owner_api.py`. They traverse
`share_detail` and `shares_list` POST, not only lower-level mutation services:

```python
def test_human_rest_patch_bypasses_agent_gate_for_options_and_password_clear(
        client, session, share_host, monkeypatch):
    from twicc.auth.hashers import hash_password

    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {
            "shareBaseUrl": "share.example.com",
            "allowAgentSessionShares": False,
            "allowAgentArtifactShares": False,
        },
    )
    share = _share(
        session,
        options={"mode": "live", "max_display_mode": "normal"},
        password_hash=hash_password("old-password"),
    )
    response = _run(client.patch(
        f"/api/shares/{share.id}/",
        data=orjson.dumps({
            "options": {"mode": "snapshot", "max_display_mode": "normal"},
            "password": "",
        }),
        content_type="application/json",
    ))
    assert response.status_code == 200
    share.refresh_from_db()
    assert share.options["mode"] == "snapshot"
    assert share.password_hash == ""


def test_human_rest_create_drops_injected_caller_and_bypasses_agent_shape(
        client, session, monkeypatch):
    """The owner POST builds an explicit eight-key payload. Request-body
    caller identity cannot turn this browser action into an agent call."""
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {
            "shareBaseUrl": "share.example.com",
            "allowAgentSessionShares": False,
            "allowAgentArtifactShares": False,
        },
    )
    response = _run(client.post(
        "/api/shares/",
        data=orjson.dumps({
            "kind": "session", "session_id": session.id,
            "caller_session_id": session.id,
            "notify_on_view": True,
            "options": {"mode": "live", "max_display_mode": "debug"},
        }),
        content_type="application/json",
    ))
    assert response.status_code == 201
    share = Share.objects.get(id=orjson.loads(response.content)["id"])
    assert share.notify_on_view is True
    assert share.options["max_display_mode"] == "debug"
    assert share.created_by_session_id is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_gate_wiring.py -x -q`

Expected: FAIL — the gate does not exist yet (agent payloads behave like human ones).

Run separately: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_consumer.py tests/test_share_owner_api.py -q`

Expected: PASS. The two Step 1b consumer tests and two Step 1c owner REST tests pin existing human/viewer behaviour before the gate implementation.

- [ ] **Step 3: Implement the wiring**

In `src/twicc/core/services/share_mutation.py`, replace this exact boundary:

```python
# ── Drop-request glue (kind="share:*") ──────────────────────────────────────

async def _resolve_target_from_payload(payload: dict):
```

with the complete block below. The existing body of
`_resolve_target_from_payload` stays after the repeated function header:

```python
# ── Agent gate (agent-sharing design §7.1) ──────────────────────────────────

async def _resolve_caller_session(payload: dict):
    """§7.1 step 1: absent key → human (None). A well-typed but UNKNOWN id also
    resolves to None → human, current behaviour. Type errors are caught by
    ``share_agent_gate.caller_type_error`` BEFORE this runs (this lookup is
    itself the first ORM access)."""
    from twicc.core.models import Session

    cid = payload.get("caller_session_id")
    if not isinstance(cid, str):
        return None
    return await sync_to_async(lambda: Session.objects.filter(id=cid).first())()


def _agent_disabled_error(kind: str) -> ShareError:
    return ShareError(
        "settings", "agent_sharing_disabled",
        f"agent-created {kind} shares are disabled; ask the user to enable them "
        f"in Settings → Sharing before retrying",
    )


def _kind_setting_on(kind: str) -> bool:
    from twicc.core.services import share_agent_gate
    from twicc.synced_settings import read_synced_settings

    return bool(read_synced_settings().get(share_agent_gate.setting_key_for(kind), False))


async def _caller_scope_ids(caller) -> set[str]:
    from twicc.core.services.spawn_scope import descendant_ids

    return {caller.id} | await sync_to_async(descendant_ids)(caller.id)


async def _agent_gate_for_loaded_share(caller, share, *, check_provenance: bool) -> list[ShareError]:
    """Steps 4-5 for the five share-loading ops. ``check_provenance=False`` is
    revoke's A7 exception: any provenance, the kind setting still applies."""
    if caller is None:
        return []
    if not _kind_setting_on(share.kind):
        return [_agent_disabled_error(share.kind)]
    if not check_provenance:
        return []
    allowed = await _caller_scope_ids(caller)
    if share.created_by_session_id is None or share.created_by_session_id not in allowed:
        # Provenance wording, never target wording: a descendant touching a
        # parent-created share OF ITSELF fails here while the target is its
        # own session (§7.5).
        return [ShareError(
            "share_id", "out_of_scope",
            "this share was created outside your spawn subtree "
            "(or by the user); you can manage only shares created by yourself "
            "or any session in your spawn subtree",
        )]
    return []


# ── Drop-request glue (kind="share:*") ──────────────────────────────────────

async def _resolve_target_from_payload(payload: dict):
```

Replace this exact complete post-Task-5 create wrapper:

```python
async def create_share_from_payload(payload: dict) -> ShareMutationResult:
    kind, session, bookmark, errors = await _resolve_target_from_payload(payload)
    if errors:
        return ShareMutationResult(False, None, errors)
    expires_at, exp_err = _parse_expires(payload)
    if exp_err:
        return ShareMutationResult(False, None, [exp_err])
    return await create_share(
        kind, session=session, bookmark=bookmark,
        label=payload.get("label") or "",
        options=payload.get("options") or {},
        password=payload.get("password") or None,
        expires_at=expires_at,
        notify_on_view=bool(payload.get("notify_on_view", False)),
    )
```

with:

```python
async def create_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.enums import ShareKind
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_create(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)

    kind, session, bookmark, errors = await _resolve_target_from_payload(payload)
    if errors:
        return ShareMutationResult(False, None, errors)

    if caller is None:
        # Preserve Task 5's human-path precedence: strict expiry is checked
        # before create_share converts options to a dict.
        expires_at, exp_err = _parse_expires(payload)
        if exp_err:
            return ShareMutationResult(False, None, [exp_err])
        options = payload.get("options") or {}
    else:
        # Layer 1 established that options is an object before this copy.
        options = dict(payload.get("options") or {})
        if not _kind_setting_on(kind):
            return ShareMutationResult(False, None, [_agent_disabled_error(kind)])
        target_session_id = session.id if session is not None else bookmark.session_id
        allowed = await _caller_scope_ids(caller)
        if target_session_id not in allowed:
            field = "session_id" if session is not None else "bookmark_id"
            return ShareMutationResult(False, None, [ShareError(
                field, "out_of_scope",
                "the target belongs to another session, outside your own spawn "
                "subtree; you can share only your own session or any session "
                "in your spawn subtree",
            )])
        if options.get("max_display_mode") == "debug":
            return ShareMutationResult(False, None, [ShareError(
                "max_display_mode", "display_mode_forbidden",
                "the debug display mode is not available to agents; allowed: "
                "conversation, simplified, normal",
            )])
        from twicc.synced_settings import read_synced_settings
        if not (read_synced_settings().get("shareBaseUrl") or "").strip():
            return ShareMutationResult(False, None, [ShareError(
                "share_base_url", "share_host_unset",
                "no share host is configured, so the link would resolve nowhere; "
                "ask the user to set one in Settings → Sharing first",
            )])
        if kind == ShareKind.SESSION.value and "mode" not in options:
            # A9: the agent default is a frozen snapshot; --live stays explicit.
            options["mode"] = "snapshot"
        expires_at, exp_err = _parse_expires(payload)
        if exp_err:
            return ShareMutationResult(False, None, [exp_err])
    return await create_share(
        kind, session=session, bookmark=bookmark,
        label=payload.get("label") or "",
        options=options,
        password=payload.get("password") or None,
        expires_at=expires_at,
        notify_on_view=bool(payload.get("notify_on_view", False)),
        created_by_session=caller,
    )
```

For agents, strict expiry remains in §7.1 step 6 with debug, host, and the
frozen default. For humans, it stays immediately after target resolution and
before option conversion. The statement order inside the agent branch is not
a precedence contract. The user decision above keeps step-6 conflicts
intentionally unspecified.

Replace these five exact post-Task-11 wrappers as one block:

```python
async def update_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await patch_share(share, payload.get("fields") or {})


async def revoke_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await revoke_share(share, revoked=True)


async def unrevoke_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await revoke_share(share, revoked=False)


async def delete_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await delete_share(share)


async def propagate_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await propagate_share(share)
```

with this complete output:

```python
async def update_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_update(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(caller, share, check_provenance=True)
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    if caller is not None and (payload.get("fields") or {}).get("password") == "":
        return ShareMutationResult(False, share.id, [ShareError(
            "password", "field_forbidden",
            "agents may set or replace a share password, never clear it; "
            "clearing is available from the human CLI or the owner UI",
        )])
    return await patch_share(share, payload.get("fields") or {})


async def revoke_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=False,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await revoke_share(share, revoked=True)


async def unrevoke_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await revoke_share(share, revoked=False)


async def delete_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await delete_share(share)


async def propagate_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await propagate_share(share)
```

- [ ] **Step 4: Run the wiring tests + the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_gate_wiring.py -q && uv run --active pytest -q`
Expected: PASS everywhere — in particular every pre-existing human-path test in `tests/test_share_mutation.py` / `tests/test_share_owner_api.py` unchanged.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/core/services/share_mutation.py tests/test_share_gate_wiring.py tests/test_share_consumer.py tests/test_share_owner_api.py`
Subject: `feat(share): enforce the agent mutation gate`
```

---

### Task 13: Read redaction in `cli/share.py`

**Files:**
- Modify: `src/twicc/cli/share.py` (`list_main`, `show_main`)
- Test: `tests/test_share_cli_reads.py` (extend)

**Interfaces:**
- Consumes Task 1's synced settings keys `allowAgentSessionShares: bool` and `allowAgentArtifactShares: bool`.
- Consumes Task 4's `build_share_url(base_value, url_path) -> str` in both read functions.
- Consumes Task 7's `list_main(*, session, project, …)` cross-kind filter semantics and `tests/test_share_cli_reads.py` scaffold (`project`, `session`, `bookmark`, `one_share_each`, `_run`, `_list`).
- Consumes Task 10's `SETTING_KEYS: dict[str, str]` mapping.
- Consumes Task 11's eager `created_by_session` query state in `cli/share.py` and preserves it in both read functions.
- Produces (§7.3): for an **agent** caller (`resolve_current_session()` non-None), every row whose kind has its setting **off** keeps all its fields but gets `token`, `url`, `url_path` set to `null` plus `"redacted": true`. Rows are NEVER dropped, and the list is NEVER scope-filtered (with a setting on, an agent sees every share of that kind, token included — deliberate, A7/A11). Humans: never redacted. Only this CLI path redacts — `serialize_share` keeps returning the real token for WS/REST.
- Produces the `settings_state` fixture in `tests/test_share_cli_reads.py`, consumed by Task 15's composed callback test.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_share_cli_reads.py` (reuse its fixtures; add a `show` capture helper mirroring `_list`):

```python
def _show(share_id):
    from twicc.cli import share as cli_share
    captured = []
    import twicc.cli.share
    orig = twicc.cli.share.emit_json
    twicc.cli.share.emit_json = captured.append
    try:
        cli_share.show_main(share_id)
    finally:
        twicc.cli.share.emit_json = orig
    return captured[0]


class _FakeCaller:
    id = "agent-1"


@pytest.fixture
def as_agent(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: _FakeCaller())


@pytest.fixture
def as_human(monkeypatch):
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: None)


@pytest.fixture
def settings_state(monkeypatch):
    state = {"allowAgentSessionShares": False, "allowAgentArtifactShares": False,
             "shareBaseUrl": "share.example.com"}
    monkeypatch.setattr("twicc.synced_settings.read_synced_settings",
                        lambda: dict(state))
    return state


def test_agent_list_redacts_kind_with_setting_off(one_share_each, as_agent, settings_state):
    settings_state["allowAgentSessionShares"] = True   # artifact stays off
    rows = _list()
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["session"]["token"] and by_kind["session"]["url"].startswith("https://")
    assert "redacted" not in by_kind["session"]
    art = by_kind["artifact"]
    assert art["token"] is None and art["url"] is None and art["url_path"] is None
    assert art["redacted"] is True
    assert art["id"]  # the row itself is never dropped


def test_agent_show_redacts_too(one_share_each, as_agent, settings_state):
    session_share_id, artifact_share_id = one_share_each
    data = _show(artifact_share_id)
    assert data["id"] == artifact_share_id
    assert data["token"] is None
    assert data["url"] is None
    assert data["url_path"] is None
    assert data["redacted"] is True
    settings_state["allowAgentArtifactShares"] = True
    data = _show(artifact_share_id)
    assert data["id"] == artifact_share_id
    assert data["token"]
    assert data["url"].startswith("https://")
    assert data["url_path"].startswith("/share/")
    assert "redacted" not in data


def test_human_never_redacted(one_share_each, as_human, settings_state):
    rows = _list()
    assert all(r["token"] for r in rows)
    assert not any("redacted" in r for r in rows)
    shown = _show(one_share_each[1])
    assert shown["token"]
    assert shown["url"].startswith("https://")
    assert shown["url_path"].startswith("/share/")
    assert "redacted" not in shown


def test_create_then_show_yields_url(session, as_human, settings_state):
    """Service/read composition: a created share id can be passed to the real
    CLI show path, which yields the absolute URL. Task 6 separately proves the
    final CLI result formatter; Task 16 proves that formatter through MCP."""
    from twicc.drop_requests_watcher import execute_drop_payload
    status = _run(execute_drop_payload({
        "kind": "share:create", "kind_target": "session", "session_id": session.id,
        "label": "", "options": {}, "password": None, "expires_at": None,
    }, "share:create"))
    assert status["status"] == "created"
    assert status["share_id"]
    assert "url" not in status and "token" not in status
    data = _show(status["share_id"])
    assert data["url"] == "https://share.example.com" + data["url_path"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_reads.py -x -q`
Expected: the two agent-redaction tests FAIL. The human and chained tests pass already; they pin earlier tasks' behaviour.

- [ ] **Step 3: Implement**

In `src/twicc/cli/share.py`, insert the helper immediately before this exact
post-Task-4 header:

```python
def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int = 50, offset: int = 0) -> None:
```

The resulting helper followed by the unchanged header is:

```python
def _redacted_kinds() -> set[str]:
    """Kinds whose token/url are redacted for THIS process's caller (§7.3):
    empty for a human; for an agent, every kind whose gate setting is off.
    The row itself is never dropped — a redacted row lets the agent say
    'a share exists here, ask the user to enable the setting'."""
    from twicc.cli._drop_request.whoami import resolve_current_session
    from twicc.core.services.share_agent_gate import SETTING_KEYS
    from twicc.synced_settings import read_synced_settings

    if resolve_current_session() is None:
        return set()
    current = read_synced_settings()
    return {kind for kind, key in SETTING_KEYS.items() if not current.get(key, False)}


def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int = 50, offset: int = 0) -> None:
```

In `list_main`, replace this distinct exact post-Task-4 line, including its
12-space indentation:

```python
            data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

with:

```python
            if s.kind in redacted_kinds:
                data["token"] = None
                data["url_path"] = None
                data["url"] = None
                data["redacted"] = True
            else:
                data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

In `show_main`, replace this separate exact post-Task-4 line:

```python
    data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

with:

```python
    if s.kind in redacted_kinds:
        data["token"] = None
        data["url_path"] = None
        data["url"] = None
        data["redacted"] = True
    else:
        data["url"] = build_share_url(base, data["url_path"]) if base else data["url_path"]
```

Compute the set once per invocation. In `list_main`, replace this exact block:

```python
    base = _base_url()
    out = []
    for s in rows:
```

with:

```python
    base = _base_url()
    redacted_kinds = _redacted_kinds()
    out = []
    for s in rows:
```

In `show_main`, replace this exact block before applying the URL-line edit:

```python
    data = serialize_share(s)
    base = _base_url()
```

with:

```python
    data = serialize_share(s)
    base = _base_url()
    redacted_kinds = _redacted_kinds()
```

Both functions already run `django.setup()` before this point.

- [ ] **Step 4: Run to verify pass + suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_share_cli_reads.py -q && uv run --active pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/share.py tests/test_share_cli_reads.py`
Subject: `feat(share): redact gated CLI reads`
```

---

### Task 14: Owner UI creator badge

**Files:**
- Modify: `frontend/src/components/share/ShareListPanel.vue`
- Create: `frontend/src/utils/shareCreatorBadge.js`
- Create: `frontend/src/utils/shareCreatorBadge.test.js`

**Interfaces:**
- Consumes Task 11's `serialize_share` field `created_by` with its three exact shapes and accepted point-in-time staleness.
- Produces `shareCreatorBadge(createdBy) -> null | {label: string, to: object | null}` as a dependency-free badge-state helper.
- Produces (§9 "Owner UI", read-only): attributed + visible creator → a badge showing the creator session's title (its id when the title is empty), linking to that session; attributed + hidden creator → a non-link badge "Agent-created (hidden session)"; NULL → **no badge** (and if any wording is ever shown for NULL it must say "human or legacy", never plain "human" — with no badge, nothing to word). No provenance filter (out of scope, §13).

- [ ] **Step 1: Write the failing badge-state test**

Create `frontend/src/utils/shareCreatorBadge.test.js`:

```js
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { shareCreatorBadge } from './shareCreatorBadge.js'

test('human or legacy provenance has no badge', () => {
    assert.equal(shareCreatorBadge({ kind: 'human_or_legacy', session: null }), null)
    assert.equal(shareCreatorBadge(null), null)
})

test('visible agent creator links with its title', () => {
    assert.deepEqual(shareCreatorBadge({
        kind: 'agent',
        session: { id: 'agent-1', title: 'Builder', project_id: '-tmp-project' },
    }), {
        label: 'Builder',
        to: {
            name: 'session',
            params: { projectId: '-tmp-project', sessionId: 'agent-1' },
        },
    })
})

test('untitled visible creator falls back to its session id', () => {
    assert.equal(shareCreatorBadge({
        kind: 'agent',
        session: { id: 'agent-1', title: '', project_id: '-tmp-project' },
    }).label, 'agent-1')
})

test('hidden agent creator has the exact non-link badge', () => {
    assert.deepEqual(shareCreatorBadge({ kind: 'agent', session: null }), {
        label: 'Agent-created (hidden session)',
        to: null,
    })
})

test('ShareListPanel consumes the tested helper', () => {
    const source = readFileSync(
        new URL('../components/share/ShareListPanel.vue', import.meta.url), 'utf8',
    )
    assert.match(source, /import \{ shareCreatorBadge \} from '\.\.\/\.\.\/utils\/shareCreatorBadge'/)
    assert.match(source, /return shareCreatorBadge\(share\.created_by\)/)
    assert.match(source, /creatorBadge\(s\)\.label/)
    assert.match(source, /creatorBadge\(s\)\.to/)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test`

Expected: FAIL because `shareCreatorBadge.js` does not exist.

- [ ] **Step 3: Create the helper**

Create `frontend/src/utils/shareCreatorBadge.js`:

```js
/** Convert the owner-only created_by serializer shape into badge rendering data. */
export function shareCreatorBadge(createdBy) {
    if (createdBy?.kind !== 'agent') return null
    const session = createdBy.session
    if (!session) {
        return { label: 'Agent-created (hidden session)', to: null }
    }
    return {
        label: session.title || session.id,
        to: {
            name: 'session',
            params: { projectId: session.project_id, sessionId: session.id },
        },
    }
}
```

- [ ] **Step 4: Add the badge to the share row**

In `frontend/src/components/share/ShareListPanel.vue`, replace this exact import
boundary:

```js
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { isShareOutdated } from '../../utils/shareStatus'
```

with:

```js
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { shareCreatorBadge } from '../../utils/shareCreatorBadge'
import { isShareOutdated } from '../../utils/shareStatus'
```

Replace this exact script block:

```js
// Per-share expanded "Recent views" panel: id -> accesses[] (null = loading).
const accesses = ref({})
function copy(s) {
```

with:

```js
// Per-share expanded "Recent views" panel: id -> accesses[] (null = loading).
const accesses = ref({})
function creatorBadge(share) {
    return shareCreatorBadge(share.created_by)
}
function copy(s) {
```

Replace this exact line in the `share-row-main` div:

```html
                <span class="share-label">{{ s.label || '(no label)' }}</span>
```

with:

```html
                <span class="share-label">{{ s.label || '(no label)' }}</span>
                <wa-tag v-if="creatorBadge(s)" size="small" variant="brand" class="share-agent-badge">
                    <wa-icon name="robot"></wa-icon>
                    <router-link v-if="creatorBadge(s).to" :to="creatorBadge(s).to">
                        {{ creatorBadge(s).label }}
                    </router-link>
                    <template v-else>{{ creatorBadge(s).label }}</template>
                </wa-tag>
```

Replace this exact style line:

```css
.share-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

with:

```css
.share-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-agent-badge { flex-shrink: 0; }
.share-agent-badge a { color: inherit; }
```

Notes: `wa-tag` and `wa-icon` are already imported app-wide in `main.js`, and the tree already uses the `robot` icon. `router-link` is globally registered by Vue Router. Keep the exact badge markup above.

- [ ] **Step 5: Frontend suite + visual note**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test`
Expected: PASS. The helper tests cover visible, hidden, NULL, and untitled states. The source guard proves the component consumes the helper. Note for the final report: the badge still needs a visual check by the user.

- [ ] **Step 6: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `frontend/src/components/share/ShareListPanel.vue frontend/src/utils/shareCreatorBadge.js frontend/src/utils/shareCreatorBadge.test.js`
Subject: `feat(share): show creator provenance badges`
```

---

### Task 15: `self`/`parent` keywords + remote preflight

**Files:**
- Create: `src/twicc/cli/_session_keywords.py`
- Modify: `src/twicc/cli/__init__.py` (`_artifacts_bookmark`, `_artifacts_unbookmark`, `_share_create_session`, `_share_default`; plus their help strings)
- Modify: `src/twicc/cli/_remote.py` (`HOST_BOUND_PARAMS`, ~line 115)
- Test: `tests/test_session_keywords.py` (create), `tests/test_share_cli_reads.py` (extend with the composed §14 list-filter case)

**Interfaces:**
- Consumes Task 7's `list_main(*, session, project, …)` cross-kind filter semantics and `tests/test_share_cli_reads.py` scaffold (`project`, `session`, `bookmark`, `one_share_each`, `_run`, `_list`).
- Consumes Task 8's tri-state `_share_create_session` declaration and preserves its nullable `--live/--frozen` behavior while adding keyword resolution.
- Consumes Task 11's `Share.created_by_session` nullable FK.
- Consumes Task 13's `settings_state` fixture and read-redaction contract. The composed §14 test exercises these through the real `share --session self` callback.
- Produces (§11): `resolve_session_keyword(value, *, param_name, allowed) -> str`, where `allowed` is an explicit `frozenset[str]`. Exactly four call sites — `artifacts bookmark`, `artifacts unbookmark`, `share create session`, `share` list `--session` — each pass `allowed=SELF_PARENT_KEYWORDS` and therefore declare that they accept BOTH keywords. A keyword outside a call site's allowed set passes through as a literal id. Local failure contract, all four sites: structured `validation_error` output + exit 1, codes `session_context_not_found` (no current session) / `parent_not_found` (`parent` with `spawned_by` NULL), the error naming the original parameter and the keyword. Resolution composes with §6: `share create session parent` resolves, then fails the scope test with `out_of_scope` (already covered by Task 12's scope tests — the payload just carries the parent's real id).
- Produces the live Typer parameter descriptions: artifact bookmark/unbookmark `session_id` accepts a session id, `self`, or `parent`; share-create-session `session_id` accepts a session id, `self`, or `parent`; share-list `--session` accepts a session id, `self`, or `parent`.
- Deliberately NOT touched: `update-session` (its inline `self` block and plain-error exit 1) and `send-message` (its `parent` branch) — whole-CLI harmonisation is out of scope (§13). **User decision for this plan review: COPY.** The new shared helper copies the mechanism modeled on update-session and serves only the four new call sites. It does not move, replace, or delegate update-session's inline block. The accepted ~15-line duplication preserves update-session's plain-error contract and is deferred to the §13 whole-CLI harmonisation.
- Remote: `"session"` joins `HOST_BOUND_PARAMS` so `twicc --remote … share --session self` exits 2 client-side instead of forwarding (the other three sites already use the listed `session_id` name).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_keywords.py`:

```python
"""self/parent keyword resolution for the four §11 call sites, and the
remote preflight extension."""

import pytest
import typer
from typer.testing import CliRunner

runner = CliRunner()


class _FakeSession:
    def __init__(self, sid, spawned_by_id=None):
        self.id = sid
        self.spawned_by_id = spawned_by_id


def test_literal_id_passes_through(monkeypatch):
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    assert resolve_session_keyword(
        "abc-123", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "abc-123"
    # A known keyword outside this call site's declared set is a literal id.
    assert resolve_session_keyword(
        "parent", param_name="SESSION_ID", allowed=frozenset({"self"}),
    ) == "parent"


def test_self_resolves(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me"))
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    assert resolve_session_keyword(
        "self", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "me"


def test_parent_resolves(monkeypatch):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id="mom"))
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    assert resolve_session_keyword(
        "parent", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    ) == "mom"


def test_unresolved_context_fails_structured(monkeypatch, capsys):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: None)
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    with pytest.raises(typer.Exit) as exc:
        resolve_session_keyword(
            "self", param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
        )
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert '"validation_error"' in out
    assert "session_context_not_found" in out
    assert "SESSION_ID" in out


def test_root_session_parent_fails_structured(monkeypatch, capsys):
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id=None))
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    with pytest.raises(typer.Exit) as exc:
        resolve_session_keyword(
            "parent", param_name="--session", allowed=SELF_PARENT_KEYWORDS,
        )
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "parent_not_found" in out and "--session" in out


# ── the four call sites actually resolve ────────────────────────────────────

@pytest.fixture
def call_captures(monkeypatch):
    captured = {"bookmark": [], "unbookmark": [], "create": [], "list": []}
    monkeypatch.setattr("twicc.cli._drop_request.whoami.resolve_current_session",
                        lambda: _FakeSession("me", spawned_by_id="mom"))
    monkeypatch.setattr("twicc.cli.artifacts_mutation.run_bookmark",
                        lambda **kw: captured["bookmark"].append(kw))
    monkeypatch.setattr("twicc.cli.artifacts_mutation.run_unbookmark",
                        lambda **kw: captured["unbookmark"].append(kw))
    monkeypatch.setattr("twicc.cli.share_mutation.run_create_session",
                        lambda **kw: captured["create"].append(kw))
    monkeypatch.setattr("twicc.cli.share.list_main",
                        lambda **kw: captured["list"].append(kw))
    return captured


def test_call_sites_resolve_keywords(call_captures):
    """§14 Keywords: BOTH keywords at all four call sites (self → "me",
    parent → "mom"). `share create session parent` resolves here too — the
    scope refusal is server-side (Task 12); the CLI forwards the resolved id."""
    from twicc.cli import app
    runner.invoke(app, ["artifacts", "bookmark", "self", "f.html", "--name", "test"])
    runner.invoke(app, ["artifacts", "bookmark", "parent", "f.html", "--name", "test"])
    runner.invoke(app, ["artifacts", "unbookmark", "self", "f.html"])
    runner.invoke(app, ["artifacts", "unbookmark", "parent", "f.html"])
    runner.invoke(app, ["share", "create", "session", "self"])
    runner.invoke(app, ["share", "create", "session", "parent"])
    runner.invoke(app, ["share", "--session", "self"])
    runner.invoke(app, ["share", "--session", "parent"])
    assert [kw["session_id"] for kw in call_captures["bookmark"]] == ["me", "mom"]
    assert [kw["session_id"] for kw in call_captures["unbookmark"]] == ["me", "mom"]
    assert [kw["session_id"] for kw in call_captures["create"]] == ["me", "mom"]
    assert [kw["session"] for kw in call_captures["list"]] == ["me", "mom"]


@pytest.mark.parametrize(
    ("keyword", "current", "code"),
    [
        ("self", None, "session_context_not_found"),
        ("parent", None, "session_context_not_found"),
        ("parent", _FakeSession("root"), "parent_not_found"),
    ],
)
def test_failure_contract_fires_at_each_call_site(monkeypatch, keyword, current, code):
    """§14 Keywords: all three failure states fire locally at all four sites."""
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session",
        lambda: current,
    )
    from twicc.cli import app
    for args in (["artifacts", "bookmark", keyword, "f.html", "--name", "test"],
                 ["artifacts", "unbookmark", keyword, "f.html"],
                 ["share", "create", "session", keyword],
                 ["share", "--session", keyword]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, args
        assert code in result.output, args


@pytest.mark.parametrize("keyword", ["self", "parent"])
@pytest.mark.parametrize(
    "argv_template",
    [
        ["artifacts", "bookmark", "KEYWORD", "f.html", "--name", "test"],
        ["artifacts", "unbookmark", "KEYWORD", "f.html"],
        ["share", "create", "session", "KEYWORD"],
        ["share", "--session", "KEYWORD"],
    ],
)
def test_remote_preflight_rejects_every_keyword_call_site_before_http(
        monkeypatch, keyword, argv_template):
    """§14 remote row: both keywords fail at all four real command shapes."""
    from twicc.cli import _remote

    def fail_if_client_constructed(*args, **kwargs):
        raise AssertionError("remote keyword preflight reached HTTP")

    monkeypatch.setattr(_remote.httpx, "Client", fail_if_client_constructed)
    argv = [keyword if token == "KEYWORD" else token for token in argv_template]
    assert _remote.maybe_forward([
        "--remote", "https://remote.example", *argv,
    ]) == 2


def test_remote_preflight_allows_explicit_share_session_id():
    from twicc.cli._remote import reject_host_bound, resolve_command

    reject_host_bound(resolve_command(["share", "--session", "abc"]))
```

Append this composed §14 list-filter case to `tests/test_share_cli_reads.py`, which Task 7 created. Anchor the append after this exact final line of Task 13's chained test:

```python
    assert data["url"] == "https://share.example.com" + data["url_path"]
```

The new test uses the real Typer callback, Task 15 keyword helper, and Task 7 cross-kind queryset together. Mark the artifact share as caller-created through Task 11's provenance column so the fixture matches the row literally:

```python
def test_session_self_finds_artifact_created_by_caller(
        one_share_each, session, settings_state, monkeypatch):
    """§14 List filters: --session self composes keyword resolution with
    the real cross-kind filter and finds the caller's artifact share."""
    from twicc.core.models import Share
    from typer.testing import CliRunner
    from twicc.cli import app

    _session_share_id, artifact_share_id = one_share_each
    Share.objects.filter(id=artifact_share_id).update(created_by_session=session)
    monkeypatch.setattr(
        "twicc.cli._drop_request.whoami.resolve_current_session", lambda: session,
    )
    settings_state["allowAgentArtifactShares"] = True
    captured = []
    monkeypatch.setattr("twicc.cli.share.emit_json", captured.append)
    result = CliRunner().invoke(app, ["share", "--session", "self"])
    assert result.exit_code == 0
    assert any(row["id"] == artifact_share_id for row in captured[0])
```

Note on the monkeypatch targets: the Typer commands import their `run_*` lazily inside the function body (`from twicc.cli.artifacts_mutation import run_bookmark`), so patching the source module attribute is effective.

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_session_keywords.py tests/test_share_cli_reads.py -x -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the helper**

Create `src/twicc/cli/_session_keywords.py`:

```python
"""Resolve the ``self`` / ``parent`` session keywords for CLI arguments (§11
of the agent-sharing design).

One contract for the four call sites this lot touches (``artifacts bookmark`` /
``unbookmark``, ``share create session``, ``share`` list ``--session``): a
keyword that cannot resolve fails LOCALLY, before any request submission, with
the structured ``validation_error`` output and exit 1. The two older precedents
keep their own divergent behaviour on purpose — ``update-session`` resolves
``self`` only (plain error), ``send-message`` resolves ``parent`` only; the
whole-CLI harmonisation is deliberately out of scope (design §13).
"""

from __future__ import annotations

import typer

SELF_PARENT_KEYWORDS = frozenset({"self", "parent"})


def resolve_session_keyword(
        value: str, *, param_name: str, allowed: frozenset[str]) -> str:
    """Resolve a keyword declared by this call site; pass other values through.

    ``self`` → the current session's id (PID ancestry, or the MCP-forced id).
    ``parent`` → the current session's ``spawned_by`` id.
    Failures: ``session_context_not_found`` (no current session) or
    ``parent_not_found`` (root session), both structured + exit 1, naming
    ``param_name`` and the keyword.
    """
    if value not in allowed:
        return value

    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "twicc.settings")
    import django
    django.setup()

    from twicc.cli._drop_request.output import emit_validation_errors
    from twicc.cli._drop_request.validation import ValidationError
    from twicc.cli._drop_request.whoami import resolve_current_session

    current = resolve_current_session()
    if current is None:
        emit_validation_errors([ValidationError(
            param_name, "session_context_not_found",
            f"{param_name}={value!r} could not be resolved: no TwiCC session "
            f"found in the process ancestry. Run from inside a TwiCC session, "
            f"or pass an explicit session id.",
        )])
        raise typer.Exit(1)
    if value == "self":
        return current.id
    if current.spawned_by_id is None:
        emit_validation_errors([ValidationError(
            param_name, "parent_not_found",
            f"{param_name}='parent': current session {current.id!r} has no "
            f"spawned_by — it was not created via `twicc create-session` from "
            f"a parent agent. Pass an explicit session id.",
        )])
        raise typer.Exit(1)
    return current.spawned_by_id
```

(Check `ValidationError`'s field order in `src/twicc/cli/_drop_request/validation.py:36` before writing — it is a `NamedTuple`; match send-message's positional usage `ValidationError(field, code, message)`.)

- [ ] **Step 4: Apply at the four call sites + help strings**

In `src/twicc/cli/__init__.py`:

1. `_artifacts_bookmark` — replace this exact argument line:
   ```python
    session_id: str = typer.Argument(help="The session that owns the artifact."),
   ```
   with:
   ```python
    session_id: str = typer.Argument(help="The session that owns the artifact: a session id, 'self', or 'parent'."),
   ```
   and at the top of the function body (before `_validate_artifact_scope(scope)`):
   ```python
    from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
    session_id = resolve_session_keyword(
        session_id, param_name="SESSION_ID", allowed=SELF_PARENT_KEYWORDS,
    )
   ```
2. `_artifacts_unbookmark` — same two changes (its argument line is byte-identical to bookmark's; the resolution lines go at the top of the body, before the `run_unbookmark` import).
3. `_share_create_session` — replace this exact argument line:
   ```python
    session_id: str = typer.Argument(...),
   ```
   with `session_id: str = typer.Argument(help="Session to share: a session id, 'self', or 'parent'."),` plus the same `SELF_PARENT_KEYWORDS` resolution block at the top of the body. (`parent` then fails Task 12's scope test with `out_of_scope` — deliberate, more informative than a `not_found` on the literal string.)
4. `_share_default` — replace this exact option line:
   ```python
    session: str = typer.Option(None, "--session", help="Filter by session id."),
   ```
   with `session: str = typer.Option(None, "--session", help="Filter by session id; accepts 'self' and 'parent'."),`, and before calling `list_main`:
   ```python
    if session is not None:
        from twicc.cli._session_keywords import SELF_PARENT_KEYWORDS, resolve_session_keyword
        session = resolve_session_keyword(
            session, param_name="--session", allowed=SELF_PARENT_KEYWORDS,
        )
   ```

In `src/twicc/cli/_remote.py`, in `HOST_BOUND_PARAMS`, insert after this exact block:

```python
        # filiation filter options
        "spawned_by",
        "spawn_tree",
        "descendants",
        "siblings",
```

the new entry:

```python
        # session-id scalar options
        "session",
```

and extend the audit comment above the set — insert before its closing line (`# Only these specific params are inspected — never a blind argv scan — so a`…) one line: `session` is the share list's `--session` filter (resolves both keywords since the agent-sharing lot).

- [ ] **Step 5: Run tests + suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_session_keywords.py tests/test_share_cli_reads.py -q && uv run --active pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/cli/_session_keywords.py src/twicc/cli/__init__.py src/twicc/cli/_remote.py tests/test_session_keywords.py tests/test_share_cli_reads.py`
Subject: `feat(cli): add share session keywords`
```

---

### Task 16: MCP exposure

**Files:**
- Modify: `src/twicc/mcp/tools.py` (`MCP_EXCLUDED_ROOTS`, `MCP_READ_ONLY_PATHS`, module docstring)
- Modify: `src/twicc/cli/__init__.py` (`share_create_app` construction, the mutations banner comment)
- Modify: `src/twicc/mcp/server.py` (instructions, ~lines 52-54)
- Test: `tests/test_mcp_tools.py`, `tests/test_mcp_server.py` (real MCP mutation result), `tests/test_share_cli_payloads.py` (bare-group test)

**Interfaces:**
- Consumes Task 8's `tests/test_share_cli_payloads.py` scaffold (`runner`, `captured_drop`, `_invoke(args)`) for the bare-group test.
- Consumes and preserves Task 9's caller-identity cases and autouse human-caller boundary in `tests/test_share_cli_payloads.py` while appending that bare-group case.
- Consumes Task 6's final mutation result `{"status": <status>, "share_id": "shr_…", "request_uuid": "…"}` and proves it through the real MCP dispatcher.
- Consumes Task 12's six-wrapper §7.1 gate contract. The MCP tools must ship only with the call-time server enforcement present.
- Consumes Task 15's live Typer parameter descriptions and `resolve_session_keyword(value, *, param_name, allowed) -> str` behavior. The MCP schema and instructions defer to those per-parameter descriptions.
- Produces: every `share` sub-command is an MCP tool (`share`, `share_show`, `share_create_session`, `share_create_artifact`, `share_update`, `share_revoke`, `share_unrevoke`, `share_delete`, `share_propagate`); `share` (list) and `share/show` advertise `readOnlyHint: true`; no zero-argument `share_create` group tool exists; bare `twicc share create` on the CLI exits 2 with a missing-command error. `COOKIE_READONLY_COMMANDS` untouched (fail-closed by design). `"settings"` stays excluded.

- [ ] **Step 1: Update the failing tests first**

In `tests/test_mcp_tools.py`:

1. `test_selection_matches_the_skill_surface` — replace this exact pair:
   ```python
    # ``share`` is human-only (O5): excluded from the MCP surface like ``settings``.
    assert not any(p.split("/")[0] == "share" for p in paths)
   ```
   with:
   ```python
    # ``share`` is agent-gated server-side (agent-sharing design): the tools
    # are ALWAYS exposed — a disabled setting rejects at call time (A4).
    assert any(p.split("/")[0] == "share" for p in paths)
    assert "share/create" not in paths  # the group is no longer callable (silent no-op fix)
   ```
   and replace this exact `rpc_paths` line:
   ```python
    rpc_paths = {p for p in build_registry() if p.split("/")[0] not in ("settings", "share")}
   ```
   with:
   ```python
    rpc_paths = {p for p in build_registry() if p.split("/")[0] != "settings"}
   ```
   (the `assert rpc_paths <= paths` below it stays).
2. `test_tool_names_are_mcp_safe_and_bijective` — insert after this exact line:
   ```python
    assert "session_content" in names
   ```
   the assertions:
   ```python
    assert "share_create_session" in names
    assert "share_create_artifact" in names
    assert "share_create" not in names
   ```
3. `test_annotations_and_always_load` — its first line is already `by_name = {t.name: t for t in iter_mcp_tools()}`; append at the end of the test, in the file's existing assertion style (`annotations.readOnlyHint is True/False`):
   ```python
    assert by_name["share"].annotations.readOnlyHint is True
    assert by_name["share_show"].annotations.readOnlyHint is True
    for name in (
        "share_create_session",
        "share_create_artifact",
        "share_update",
        "share_revoke",
        "share_unrevoke",
        "share_delete",
        "share_propagate",
    ):
        assert by_name[name].annotations.readOnlyHint is False
   ```

Append to `tests/test_share_cli_payloads.py`:

```python
def test_bare_share_create_is_a_usage_error_not_silent_success():
    """§12: the group had invoke_without_command=True with no callback — a
    silent no-op exit 0, and a phantom zero-arg MCP tool. Now: exit 2."""
    from twicc.cli import app
    result = runner.invoke(app, ["share", "create"])
    assert result.exit_code == 2
    assert "Missing command" in result.output
```

Append this real MCP mutation test to `tests/test_mcp_server.py` after this
exact current final block:

```python
    result = asyncio.run(mcp_server.dispatch_tool("workspaces", {}, session_id=None))
    # The SDK does exactly this on the returned dict; it must not raise.
    json.dumps(result)
    assert result["result"][0]["timestamp"] == "2026-07-06T19:14:24.421000+00:00"
```

The new test crosses
`dispatch_tool` → the generated Typer command → in-backend transport →
`emit_final`/`build_final`; it is not a formatter unit test:

```python
@pytest.mark.django_db(transaction=True)
def test_call_tool_share_create_returns_public_result_shape(
        isolated_data_dir, monkeypatch):
    from twicc.core.models import Project, Session

    project = Project.objects.create(id="-tmp-share", directory="/tmp/share", name="share")
    session = Session.objects.create(
        id="33333333-3333-3333-3333-333333333333", project=project,
        provider="claude_code", file_path="share.jsonl", last_line=7,
    )
    monkeypatch.setattr(
        "twicc.synced_settings.read_synced_settings",
        lambda: {
            "shareBaseUrl": "share.example.com",
            "allowAgentSessionShares": True,
            "allowAgentArtifactShares": False,
        },
    )
    result = asyncio.run(mcp_server.dispatch_tool(
        "share_create_session", {"session_id": session.id}, session_id=session.id,
    ))
    assert result["exit_code"] == 0
    assert set(result["result"]) == {"status", "share_id", "request_uuid"}
    assert result["result"]["status"] == "created"
    assert result["result"]["share_id"].startswith("shr_")
    assert "token" not in result["result"]
    assert "url" not in result["result"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_mcp_tools.py tests/test_mcp_server.py tests/test_share_cli_payloads.py -q`
Expected: the updated/added tests FAIL.

- [ ] **Step 3: Implement**

1. `src/twicc/cli/__init__.py`:
   - Replace this exact line:
     ```python
     share_create_app = typer.Typer(name="create", help="Create a share link.", invoke_without_command=True)
     ```
     with:
     ```python
     share_create_app = typer.Typer(name="create", help="Create a share link.")
     ```
     (drop `invoke_without_command=True`).
   - Replace this exact banner line:
     ```python
     # ── Mutation commands (human-only: no skill, no MCP tool — O5) ──────────────
     ```
     with:
     ```python
     # ── Mutation commands (agent-gated: two Settings → Sharing switches + the
     #    spawn-subtree scope — core/services/share_agent_gate.py; the human
     #    surfaces bypass the gate. Design: docs/plans/2026-08-10-agent-sharing-design.md) ──
     ```
2. `src/twicc/mcp/tools.py`:
   - Replace this exact assignment:
     ```python
     MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
         (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings", "share"}
     )
     ```
     with:
     ```python
     MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
         (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings"}
     )
     ```
     The comment above it (`# The MCP surface: local-only minus whoami, plus the settings group.`) already becomes exactly accurate; keep it.
   - Replace this exact assignment:
     ```python
     MCP_READ_ONLY_PATHS: frozenset[str] = COOKIE_READONLY_COMMANDS | frozenset(
         {"session/plan", "session/workflows", "session/workflow", "whoami"}
     )
     ```
     with:
     ```python
     MCP_READ_ONLY_PATHS: frozenset[str] = COOKIE_READONLY_COMMANDS | frozenset(
         {"session/plan", "session/workflows", "session/workflow", "whoami", "share", "share/show"}
     )
     ```
     Before editing the assignment, locate this exact comment block:
     ```python
     # Read-only annotation source (metadata only — NOT used for availability).
     # Every tool is exposed in every mode (D9); `readOnlyHint` is honest metadata
     # for clients (and on Codex it feeds `requires_mcp_tool_approval`, though our
     # `default_tools_approval_mode="approve"` makes that moot). COOKIE_READONLY_COMMANDS
     # is the vetted fail-closed list; the session read subviews and whoami are pure
     # reads that were simply never needed on the cookie path.
     ```
     Replace it with:
     ```python
     # Read-only annotation source (metadata only — NOT used for availability).
     # Every tool is exposed in every mode (D9); `readOnlyHint` is honest metadata
     # for clients (and on Codex it feeds `requires_mcp_tool_approval`, though our
     # `default_tools_approval_mode="approve"` makes that moot). COOKIE_READONLY_COMMANDS
     # is the vetted fail-closed list; the session read subviews, whoami, and share
     # list/show are pure reads. Share reads stay out of COOKIE_READONLY_COMMANDS:
     # the owner UI uses `/api/shares/`, and the cookie list remains fail-closed.
     ```
   - Module docstring — replace this complete exact paragraph:
     ```text
     Selection rule: the RPC registry (everything the CLI exposes minus
     local-only) minus the ``settings`` group (not skill-covered; agents must not
     mutate global settings) plus ``whoami`` (local-only for /rpc/ because PID
     ancestry is meaningless over HTTP — but the MCP dispatcher injects the caller
     identity, making it THE discovery primitive).
     ```
     with:
     ```text
     Selection rule: the RPC registry (everything the CLI exposes minus
     local-only) minus the ``settings`` group (no skill and no MCP tool — though
     the CLI stays reachable from a session, an accepted property of the trust
     model; see the agent-sharing design §5.2/A17) plus ``whoami`` (local-only for
     /rpc/ because PID ancestry is meaningless over HTTP — but the MCP dispatcher
     injects the caller identity, making it THE discovery primitive).
     ```
3. `src/twicc/mcp/server.py` — in `INSTRUCTIONS`, replace this exact bullet (the first under "Conventions:"):
   ```
   - Session-targeting arguments accept `self` (your own session) and `parent`
     (the session that spawned you); your identity is carried by this connection,
     so `whoami` works and `create_session` records you as the spawner.
   ```
   with:
   ```
   - Session-targeting parameters accept `self` (your own session) and/or
     `parent` (the session that spawned you) where their parameter description
     says so; the connection carries the identity needed to resolve them,
     so `whoami` works and `create_session` records you as the spawner.
   ```

- [ ] **Step 4: Run the tests + full suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_mcp_tools.py tests/test_share_cli_payloads.py -q && uv run --active pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/mcp/tools.py src/twicc/mcp/server.py src/twicc/cli/__init__.py tests/test_mcp_tools.py tests/test_mcp_server.py tests/test_share_cli_payloads.py`
Subject: `feat(share): expose gated MCP tools`
```

---

### Task 17: Skills, docs, plugin bump, final sweep

**Files:**
- Create: `src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md`
- Create: `tests/test_twicc_share_skill.py`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-artifacts/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (0.67.0 → 0.68.0)
- Modify: `SKILLS-AND-CLI.md`, `CLAUDE.md`, `AGENTS.md`
- Modify: `src/twicc/synced_settings.py` (`shareBaseUrl` comment)

**Interfaces:**
- Consumes Task 1's synced keys `allowAgentSessionShares: bool` and `allowAgentArtifactShares: bool`.
- Consumes Task 2's exact switch consent copy and `frontend/public/help/sharing.md` agent-setting documentation.
- Consumes Task 3's `descendant_ids(session_id: str) -> set[str]` scope contract for accurate subtree wording.
- Consumes Task 4's `normalize_share_base(value) -> str` / `build_share_url(base_value, url_path) -> str` parity contract.
- Consumes Task 5's `_parse_expires(payload) -> tuple[datetime | None, ShareError | None]` and artifact title-option preservation.
- Consumes Task 6's `{"status": <status>, "share_id": "shr_…", "request_uuid": "…"}` mutation result.
- Consumes Task 7's `list_main(*, session, project, …)` cross-kind filter semantics.
- Consumes Task 8's `run_create_session(..., mode: str | None, options: dict, ...)` tri-state behavior.
- Consumes Task 9's optional `caller_session_id: str` payload identity.
- Consumes Task 10's `SETTING_KEYS` and three Layer-1 shape validators for documented fields and errors.
- Consumes Task 11's `Share.created_by_session` and `serialize_share` field `created_by` for provenance documentation.
- Consumes Task 12's six-wrapper §7.1 gate contract, Task-12-owned Layer-2 checks, intentionally unspecified intra-step-6 conflict precedence, and its `agent_sharing_disabled`, `out_of_scope`, `share_host_unset`, `field_forbidden`, and `display_mode_forbidden` errors.
- Consumes Task 13's per-kind list/show read-redaction contract.
- Consumes Task 14's `shareCreatorBadge(createdBy) -> null | {label: string, to: object | null}` owner behavior.
- Consumes Task 15's `resolve_session_keyword(value, *, param_name, allowed) -> str`, `SELF_PARENT_KEYWORDS`, local keyword errors, and remote preflight behavior.
- Consumes Task 16's nine-tool MCP surface, read-only annotations, and absence of a `share_create` group tool.
- Produces the complete `twicc-share` skill, amended artifact/peer skills and central/repository documentation, plugin version `0.68.0`, and the focused `tests/test_twicc_share_skill.py` contract test.

**Before writing any skill:** read `src/twicc/agent/plugin/README.md` and one neighbouring skill (`twicc-artifacts` is the closest surface). The complete `twicc-share` file below already follows the required section order and exact invocation block. Copy it literally.

- [ ] **Step 1: Write the `twicc-share` skill**

Create `src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md` with the exact complete content below. Do not adapt it during implementation:

````markdown
---
name: twicc-share
description: Create and manage public read-only links for session transcripts or bookmarked artifacts. Use when you or the user want to create or manage a link, including a new link for a peer message.
---

# Sharing sessions and artifacts

A share is a public, read-only capability URL. Two global settings gate this
surface, per kind: `allowAgentSessionShares` and `allowAgentArtifactShares`,
both OFF by default. **Never enable these settings yourself** (you could,
via `twicc settings set` — that is a property of the trust model, not an
invitation): only the user flips them, in Settings → Sharing.

## When to use

- You or the user want a public read-only link to a session transcript.
- You or the user want a public read-only link to a bookmarked artifact.
- You or the user want to list, inspect, update, revoke, restore, delete, or propagate share links.
- You need a new share URL to send through the peer system.

## How to invoke

**Prefer the `mcp__twicc__*` tools when you have them.** Inside a TwiCC session your tool list may include `mcp__twicc__*` tools — one per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). When present, use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. Fall back to the `$TWICC` CLI below when those tools aren't available (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Scope

You may share **your own session or a session you spawned** (your spawn
subtree), and manage (update / unrevoke / delete / propagate) only shares
**created by agents in your subtree**. You may **revoke** any share of an
enabled kind, whatever created it — un-publishing is always safe.

### List

```bash
$TWICC share [--kind session|artifact] [--session ID|self|parent] [--project PROJECT] [--include-revoked] [--limit N] [--offset N]
```

- `--kind session|artifact` — filter by share kind.
- `--session ID|self|parent` — filter both session and artifact shares by their owning session.
- `--project PROJECT` — filter both kinds by project, with worktree-aware scope.
- `--include-revoked` — include revoked rows.
- `--limit N` — maximum rows, default 50.
- `--offset N` — rows to skip, default 0.

### Show

```bash
$TWICC share show <SHARE_ID>
```

Returns one share, including its `url` when the setting for its kind is enabled.

### Create a session share

```bash
$TWICC share create session <SESSION_ID|self|parent> [--label L] [--password P] [--expires ISO] [--live|--frozen] [--max-display MODE] [--include-subagents|--no-subagents] [--title T] [--show-title|--no-title] [--timeout N]
```

- With no `--live`/`--frozen` flag your share is a **frozen snapshot** at the
  current line; pass `--live` explicitly for a live-following link. Use
  `share propagate <SHARE_ID>` to re-freeze a snapshot at the newest content.
- `--max-display` accepts `conversation`, `simplified`, `normal`.
- `parent` resolves, then fails the scope test (`out_of_scope`): you cannot
  share the session that spawned you.
- `--label L` — owner-only label; use the peer convention below when applicable.
- `--password P` — set an initial viewer password.
- `--expires ISO` — set an ISO 8601 expiry.
- `--title T` — override the public title when title display is enabled.
- `--show-title` / `--no-title` — show a title or a generic viewer label.
- `--timeout N` — seconds to wait for the server, default 30.

### Create an artifact share

An artifact share requires a bookmark first (see the twicc-artifacts skill):

```bash
$TWICC artifacts bookmark <SESSION_ID|self|parent> <PATH> --name "..."
$TWICC share create artifact <BOOKMARK_ID> [--label L] [--password P] [--expires ISO] [--title T] [--show-title|--no-title] [--timeout N]
```

- `BOOKMARK_ID` — id returned by `artifacts bookmark` or listed by `artifacts`.
- `--label L` — owner-only label; use the peer convention below when applicable.
- `--password P` — set an initial viewer password.
- `--expires ISO` — set an ISO 8601 expiry.
- `--title T` — override the bookmark name shown to viewers.
- `--show-title` / `--no-title` — show a title or a generic viewer label.
- `--timeout N` — seconds to wait for the server, default 30.

### Get the URL

`share create …` returns `{"status": "created", "share_id": "shr_…"}` — no
token, no URL. Then:

```bash
$TWICC share show <SHARE_ID>
```

Read the `url` field from the show result.

### Share with a peer

When you create a share in order to send it through the peer system, set
`--label "peer <PEER_NAME>"` — the peer's local name from `twicc peers`, or
its `peer_…` id when it has no name — so the user sees from their share list
which link went to which peer. Then send the URL with `twicc peer-send`.

### Passwords

You can set or replace a share password (`--password` on create or update),
never clear one — clearing is the user's (owner UI or their own CLI).
Replacing a password invalidates every existing viewer grant: new page loads
and new live connections need the new password. A viewer already streaming a
live share is NOT cut off by a password change — use `revoke` for an
immediate cutoff.

### Update

```bash
$TWICC share update <SHARE_ID> [--label L] [--password P] [--expires ISO] [--timeout N]
```

- `--label L` — replace the owner-only label.
- `--password P` — set or replace the viewer password; agents cannot clear it.
- `--expires ISO` — set an ISO 8601 expiry; the CLI normalizes an empty value to a clear.
- `--timeout N` — seconds to wait for the server, default 30.

### Revoke

```bash
$TWICC share revoke <SHARE_ID> [--timeout N]
```

Revoke any share of an enabled kind. `--timeout N` defaults to 30 seconds.

### Unrevoke

```bash
$TWICC share unrevoke <SHARE_ID> [--timeout N]
```

Restore an in-scope agent-created share. `--timeout N` defaults to 30 seconds.

### Delete

```bash
$TWICC share delete <SHARE_ID> [--timeout N]
```

Delete an in-scope agent-created share and its snapshot directory. `--timeout N` defaults to 30 seconds.

### Propagate

```bash
$TWICC share propagate <SHARE_ID> [--timeout N]
```

Advance an in-scope frozen share to current content. `--timeout N` defaults to 30 seconds.

## Errors

### Local (exit 1)

- `session_context_not_found` — `self` or `parent` has no current TwiCC session context.
- `parent_not_found` — `parent` was used from a root session.

### Server (exit 3)

- `agent_sharing_disabled` — the kind's setting is off. **Relay to the user**
  (only they can enable it in Settings → Sharing); never retry, never enable
  the setting yourself.
- `share_host_unset` — no share host is configured. **Relay to the user**
  (Settings → Sharing); never retry.
- `out_of_scope` — on create: the target session is outside your spawn
  subtree. On update/unrevoke/delete/propagate: the share was created outside
  your subtree (or by the user).
- `field_forbidden` — the payload carried a key, type or value reserved to
  human surfaces (for example clearing a password).
- `display_mode_forbidden` — the requested display ceiling is not agent-available.
- `invalid` — an expiry is not a valid ISO 8601 datetime.
- `not_found` — the requested target session, bookmark, or share does not exist.
- `snapshot_failed` — the artifact snapshot could not be created or refreshed.
- `not_snapshot` — `propagate` was used on a live session share.

## Output format

### List

```json
[
  {
    "id": "shr_abc123",
    "kind": "session",
    "label": "peer workstation",
    "status": "active",
    "session_id": "session-1",
    "token": "…",
    "url_path": "/share/…/",
    "url": "https://share.example.com/share/…/",
    "options": {"mode": "snapshot", "frozen_at_line": 42},
    "view_count": 0,
    "created_by": {"kind": "agent", "session": {"id": "session-1", "title": "Builder", "project_id": "-project"}}
  }
]
```

### Show

The show command returns one object with the same fields as a list row.

- With a kind's setting off, `share` (list) and `share show` still answer but
  with `token`/`url`/`url_path` null and `"redacted": true` — tell the user a
  share exists and which setting unlocks it.

### Mutations

```json
{"status": "created", "share_id": "shr_abc123", "request_uuid": "…"}
{"status": "updated", "share_id": "shr_abc123", "request_uuid": "…"}
{"status": "deleted", "share_id": "shr_abc123", "request_uuid": "…"}
```

Creation returns a `share_id`, not a token or URL. Use `share show` next.

On rejection:

```json
{"status": "rejected", "errors": [{"field": "settings", "code": "agent_sharing_disabled", "message": "…"}], "request_uuid": "…"}
```

### Exit codes

- `0` — Success
- `1` — Local validation error
- `2` — TwiCC server not running or remote misuse
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC share --kind session --limit 20 --offset 0
$TWICC share --session self
$TWICC share show shr_abc123
$TWICC share create session self --label "peer workstation" --frozen --timeout 30
$TWICC artifacts bookmark self report.md --name "Report"
$TWICC share create artifact 12 --label "peer workstation" --timeout 30
$TWICC share update shr_abc123 --password "new secret" --timeout 30
$TWICC share revoke shr_abc123 --timeout 30
$TWICC share unrevoke shr_abc123 --timeout 30
$TWICC share propagate shr_abc123 --timeout 30
$TWICC share delete shr_abc123 --timeout 30
```

## Related commands

- `$TWICC artifacts [OPTIONS]` — list bookmarks or create the artifact prerequisite. Skill: `twicc-artifacts`.
- `$TWICC peers` — list peers and find the peer label name. Skill: `twicc-peers`.
- `$TWICC peer-send <PEER> <TITLE> <PROMPT>` — send the resolved share URL to a peer. Skill: `twicc-peer-send`.
- `$TWICC session <SESSION_ID>` — inspect a target session. Skill: `twicc-session`.

## How to present results

1. State the share kind, target, status, and frozen/live mode.
2. For creation, show the `share_id`, then call `share show` and show its `url`.
3. If a row is redacted, state that it exists and name the disabled kind setting.
4. Relay `agent_sharing_disabled` and `share_host_unset` to the user without retrying or changing settings.
````

The file must contain this text exactly. It must not mention an agent-available
debug display mode.

- [ ] **Step 2: Update `twicc-artifacts` and `twicc-peer-send`**

1. `twicc-artifacts/SKILL.md` — three content-anchored changes:
   - The two syntax lines, currently exactly:
     ```
     $TWICC artifacts bookmark <SESSION_ID> <PATH> --name NAME [--scope SCOPE]
     ```
     and
     ```
     $TWICC artifacts unbookmark <SESSION_ID> <PATH>
     ```
     become `<SESSION_ID|self|parent>`; the argument description line, currently exactly:
     ```
     - `SESSION_ID` — the session that owns the artifact.
     ```
     becomes:
     ```markdown
     - `SESSION_ID` — the session that owns the artifact: a session id, `self`, or `parent`.
     ```
   - Locate this exact example block:
     ```bash
     $TWICC artifacts bookmark 54b42b89-290a-4324-bf86-f636a048d23d demo/index.html --name "Demo"
     $TWICC artifacts bookmark 54b42b89-290a-4324-bf86-f636a048d23d report.md --name "Report" --scope all
     $TWICC artifacts unbookmark 54b42b89-290a-4324-bf86-f636a048d23d demo/index.html
     ```
     Keep the UUID examples and add a `self` variant for bookmark and unbookmark.
   - Locate this exact local-errors block:
     ```markdown
     ### Local (exit 1)

     - `invalid_scope`
     ```
     Add `session_context_not_found` and `parent_not_found` with the local-failure semantics (§11).
2. `twicc-peer-send/SKILL.md` — locate this exact bullet in `## When to use`:
   ```markdown
   - You need to hand off information, a screenshot, or a document to an agent working on another user's machine.
   ```
   Insert after it one cross-reference scoped to **creation**: when the requested peer message needs a *newly created* share link, use the twicc-share skill and set the new link's label to `peer <PEER_NAME>`; forwarding an already-existing share URL needs no relabelling.

- [ ] **Step 3: Bump the plugin version**

In `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`, replace this exact line:

```json
  "version": "0.67.0",
```

with:

```json
  "version": "0.68.0",
```

This is the one minor bump for the new skill and the two existing-skill edits.

- [ ] **Step 4: Update `SKILLS-AND-CLI.md`**

Four places:

1. **Central keyword inventory** ("Acting from inside a session" section): locate these exact bullets:
   ```markdown
   - **`self`** — the current session. Accepted by `update-session`, `update-sessions` / `send-messages` (as an explicit id), `topology`, and the `--spawned-by` / `--spawn-tree` / `--descendants` / `--siblings` filters.
   - **`parent`** — the session that spawned the current one. Accepted by `send-message` and the filiation filters (except `--spawn-tree` and `--siblings`, which reject `parent`).
   ```
   Extend both with `artifacts bookmark`/`unbookmark`, `share create session`, and the share list `--session` (both keywords are accepted at all four sites).
2. **Artifacts section**: locate this exact syntax heading:
   ```markdown
   ### `twicc artifacts` / `twicc artifacts bookmark <SESSION_ID> <PATH>` / `twicc artifacts unbookmark <SESSION_ID> <PATH>`
   ```
   Replace both positional forms with `<SESSION_ID|self|parent>`, and add one sentence on the local failure codes.
3. **Sharing section**: replace this complete exact current lead paragraph:
   ```markdown
   Read-only public links to a session transcript or a bookmarked artifact, served under `/share/<token>/` on a **dedicated share host** (a hostname distinct from the working origin; set it in Settings → Sharing — `shareBaseUrl`). **Human-only (O5): no skill and no MCP tool exist for `share`** — it is reachable over RPC with a full-scope Bearer token, but agents are never pointed at it. The token is the credential; per-link password / expiry / revoke are separate.
   ```
   with this complete final lead paragraph:
   ```markdown
   Read-only public links to a session transcript or a bookmarked artifact, served under `/share/<token>/` on a **dedicated share host** (a hostname distinct from the working origin; set it in Settings → Sharing — `shareBaseUrl`). The token is the credential; per-link password / expiry / revoke are separate. Agents can use the full share surface (skill [`twicc-share`](src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md) + MCP tools), gated by two synced settings, both off by default: `allowAgentSessionShares` / `allowAgentArtifactShares` (Settings → Sharing). With a kind enabled, an agent may create shares whose target is its own session or a spawn-tree descendant, manage shares created in its own subtree, revoke ANY share of that kind, and read every URL for shares of that kind; with that kind disabled, mutations are refused (`agent_sharing_disabled`) and reads return rows with `token`/`url` null (`"redacted": true`). Agent session shares default to frozen snapshots; `--max-display debug` and password clearing are refused to agents. This gate is a guardrail against an obedient agent, not a security boundary — the CLI, the DB file and the settings themselves are reachable from a session's shell (accepted trust model, design §5.2).
   ```
   Locate this exact list sentence and update its session option to `--session <ID|self|parent>` while preserving the other options:
   ```markdown
   List (read-only, direct DB — works with the server down) or show one share as JSON. Listing: `--kind <session|artifact>`, `--session ID`, `--project TEXT` (worktree-aware scope), `--include-revoked`, `--limit` (default 50), `--offset`. Each row is the owner serializer (`id`, `token`, `kind`, `label`, `status`, `options`, `view_count`, …) plus a resolved `url` (absolute when `shareBaseUrl` is set, else the relative `/share/<token>/` path).
   ```
   Locate this exact create syntax heading and replace only the session positional form with `<SESSION_ID|self|parent>`:
   ```markdown
   ### `twicc share create session <SESSION_ID>` / `twicc share create artifact <BOOKMARK_ID>`
   ```
   Also mention the `{status, share_id}` result and two-call URL flow.
4. Re-read `SKILLS-AND-CLI.md`'s remote section (`self`/`parent` rejection wording) — generic, stays accurate, no edit.

- [ ] **Step 5: Update `CLAUDE.md` + `AGENTS.md`**

In `CLAUDE.md`, Database Models, `Share` bullet, replace this exact fragment:

```markdown
Human-only (O5): no skill, no MCP tool (`share` in `MCP_EXCLUDED_ROOTS`)
```

with this exact text:

```markdown
Agent-usable behind two default-off synced settings (`allowAgentSessionShares`/`allowAgentArtifactShares`): spawn-subtree scope + provenance (`Share.created_by_session`), shape-contract gate in the `*_from_payload` wrappers (`core/services/share_agent_gate.py`); the owner REST UI bypasses the gate. Agent extension: `docs/plans/2026-08-10-agent-sharing-design.md`
```

Do not include a final period in the replacement because the existing period after the replaced fragment stays. Keep the bullet's closing `Design: docs/plans/2026-07-05-sharing-design.md` pointer as the historical design for the underlying human share feature. Mirror the same edit into `AGENTS.md` (condensed to its style — AGENTS.md must follow CLAUDE.md, standing rule).

- [ ] **Step 6: Reword the `shareBaseUrl` comment**

In `src/twicc/synced_settings.py`, replace the `shareBaseUrl` comment — currently exactly:

```python
    # Dedicated share origin (design §12): a hostname DISTINCT from the working
    # origin, pointing at the same local port. Required to create/serve shares —
    # /share/ is gated to this host only (share/asgi_filter.py) and the Share UI is
    # disabled when empty. A bare hostname or a full URL; only the hostname matters.
```

("Required to create/serve shares" overstates A13's split) — with:

```python
    # Dedicated share origin (design §12): a hostname DISTINCT from the working
    # origin, pointing at the same local port. Serving links always requires it
    # (/share/ is gated to this host — share/asgi_filter.py — and the Share UI
    # is disabled when empty); creation requires it only on the REST path and
    # for agent callers (share_host_unset) — the human CLI and full-token /rpc/
    # stay permissive. A bare hostname or a full URL; only the hostname matters.
```

- [ ] **Step 7: Add and run the focused skill contract test**

Create `tests/test_twicc_share_skill.py`:

```python
from pathlib import Path

import orjson


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/twicc/agent/plugin/twicc/skills/twicc-share/SKILL.md"
README = ROOT / "src/twicc/agent/plugin/README.md"
PLUGIN = ROOT / "src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json"


def _resolver_block(text: str) -> str:
    start = text.index("**Prefer the `mcp__twicc__*` tools when you have them.**")
    final = "it may expand to multiple words, which quoting would break."
    end = text.index(final, start) + len(final)
    return text[start:end]


def test_twicc_share_skill_contract():
    text = SKILL.read_text()
    assert text.startswith("---\nname: twicc-share\ndescription: ")
    frontmatter = text.split("---", 2)[1]
    assert (
        "Use when you or the user want to create or manage a link, "
        "including a new link for a peer message."
    ) in frontmatter
    assert "send a peer link" not in frontmatter
    headings = [
        "# Sharing sessions and artifacts",
        "## When to use",
        "## How to invoke",
        "## Usage",
        "## Errors",
        "## Output format",
        "## Examples",
        "## Related commands",
        "## How to present results",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert _resolver_block(text) == _resolver_block(README.read_text())

    assert "--limit N" in text and "--offset N" in text
    for operation in ("update", "revoke", "unrevoke", "delete", "propagate"):
        line = next(
            row for row in text.splitlines()
            if row.startswith(f"$TWICC share {operation} ")
        )
        assert "[--timeout N]" in line
    assert "$TWICC share create session" in text
    assert "$TWICC share create artifact" in text
    assert "revoke|unrevoke|delete|propagate" not in text
    assert "--max-display debug" not in text
    assert "draft to adapt" not in text

    plugin = orjson.loads(PLUGIN.read_bytes())
    assert plugin["version"] == "0.68.0"
```

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest tests/test_twicc_share_skill.py -q`

Expected: PASS. This validates the final skill structure, exact resolver block,
all management timeout syntax, safe command examples, and the one minor plugin
version bump.

- [ ] **Step 8: Final sweep + full suites**

1. Stale-wording sweep (§12): `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && rg -ni "human-only" src/ frontend/src/ frontend/public/help/ README.md SKILLS-AND-CLI.md CLAUDE.md AGENTS.md -g '*.py' -g '*.js' -g '*.vue' -g '*.md' | rg -i "share"` — every remaining hit must be deliberate (§5.2 trust-model wording, password-clear wording) or an ordinary-verb coincidence, such as the Peers sentence "instances share no memory" in `SKILLS-AND-CLI.md`. The search scope excludes historical plans, so do not expect the frozen 2026-07-05 design here. Also run `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && rg -n "O5" src/ frontend/src/ frontend/public/help/ README.md SKILLS-AND-CLI.md CLAUDE.md AGENTS.md`; every remaining hit must be deliberate or unrelated to this feature.
2. Full backend suite: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && uv run --active pytest -q` → PASS.
3. Full frontend suite: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test` → PASS.
4. Do NOT touch `CHANGELOG.md`.

- [ ] **Step 9: Commit**

```text
Worktree: `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system`
Stage: `src/twicc/agent/plugin/twicc/skills/twicc-share/ src/twicc/agent/plugin/twicc/skills/twicc-artifacts/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-peer-send/SKILL.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json SKILLS-AND-CLI.md CLAUDE.md AGENTS.md src/twicc/synced_settings.py tests/test_twicc_share_skill.py`
Subject: `docs(share): add agent sharing guidance`
```

---

## Spec-coverage checklist (for the final reviewer of the IMPLEMENTATION, not for re-review of this plan)

- §4 settings + plumbing + consent copy → Tasks 1, 2
- §5.1 caller identity → Task 9 (CLI), free on MCP
- §6 scope rule + `spawn_scope.py` → Tasks 3, 12
- §7.1 gate algorithm → Task 12
- §7.2 shape contract + value rules + tri-state flag + expiry fix → Tasks 5, 8, 10, 12
- §7.3 read redaction → Task 13
- §7.4 share host + URL parity → Tasks 4, 12
- §7.5 error contract → Tasks 10, 12 (codes/fields/messages embedded)
- §8 command surface, title-options fix, list filters, `share_id` result, label convention → Tasks 5, 6, 7, 17
- §9 provenance model + serializer + badge + staleness acceptance → Tasks 11, 14
- §10 artifact flow (bookmark ungated) → no code change; documented in Task 17
- §11 keywords + remote preflight → Task 15
- §12 MCP exposure + docs + skills + stale texts → Tasks 16, 17 (plus the `cli/share.py` docstring in Task 4, `frontend/public/help/sharing.md` in Task 2, and live Typer descriptions in Task 15)
- §13 out-of-scope list → nothing implemented for those items anywhere
- §14 test table → distributed across every task's test steps
