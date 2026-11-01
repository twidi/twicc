# Browser Pane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Browser" tool tab to session views — a plain iframe with browser-like chrome (back / forward / refresh / home / address bar) showing a user-chosen URL (typically the project's dev server), with the URL savable as a default per project and/or per workspace.

**Architecture:** One new always-present entry in SessionView's `TOOL_TABS` registry (last tab, `Alt+Shift+0`), backed by a new `BrowserPane.vue` component mounted once and teleported like every other tool panel. The default URL is resolved live on the frontend: project ancestor chain (worktree → main repo → path ancestors, reusing `ancestorChain`) first, then the first containing workspace's `browserUrl`. Storage: a new `Project.default_browser_url` DB column (REST `PUT /api/projects/<id>/`) and a new `browserUrl` key on workspace dicts in `workspaces.json` (whole-blob WS sync). No `Session.layout` migration is needed — a pane id absent from a stored `assignment` falls back to center.

**Tech Stack:** Vue 3 `<script setup>` + Web Awesome (all needed components already imported in `main.js`), Django 6 (one `AddField` migration), Node's built-in `node:test` runner for the URL-normalization util (no test framework is wired into the frontend — see `version.test.js`).

---

## Design decisions (locked)

| Topic | Decision |
|---|---|
| Tab id / label / icon | `browser` / "Browser" / FA `globe` (Free, solid) |
| Position / shortcut | Last tool tab, `Alt+Shift+0` (`0` is unused today; `DIRECT_TAB_MAP` gains `0: 'browser'`) |
| Presence | Always present (like Files and Terminal) — the user must be able to open the tab and type a URL |
| iframe hardening | **None** (no `sandbox`, no CSP, no broker) — deliberate: the page runs exactly as in a normal browser tab, with direct network. Cross-origin isolation already prevents it from touching TwiCC. `allow="clipboard-read; clipboard-write; fullscreen"` for QoL |
| History / refresh | Pane-maintained URL stack (address-bar navigations only). Cross-origin iframes expose neither `contentWindow.location` nor `history`, so in-page link clicks are invisible; Refresh recreates the iframe (`:key` bump) on the last recorded URL. Documented in the pane's info tooltip |
| Per-session current URL | Persisted (Task 13): a `Session.browser_url` column mirroring the `layout` pattern — hydrated once at the tab's first activation (it wins over the project/workspace default), written back with a debounced PATCH on each toolbar navigation. Drafts stay transient (no backend row). Tasks 1–10 work without it; Task 13 layers it on |
| Lazy init | The iframe only mounts after the tab's first activation — never auto-load a dev server for every open session |
| Default resolution order | `Project.default_browser_url` walked up `ancestorChain` → first non-archived workspace containing the project (worktree-aware via `workspaceContainsProject`, store order) with a `browserUrl` → none (blank state) |
| Saving | From the pane: a bookmark dropdown ("Save current URL as default for…" project / main repo when worktree / each containing workspace). Durable editing + clearing: new fields in ProjectEditDialog and WorkspaceManageDialog |
| URL validation | `http(s)` only, everywhere (client util + backend PUT). Schemeless input gets `http://` for localhost-ish hosts (localhost, IPs, `*.local`/`*.test`/`*.localhost`, `host:port`), `https://` otherwise |
| CLI parity | In scope (Tasks 11–12): `twicc update-project --default-browser-url/--unset-default-browser-url`, `twicc update-workspace --browser-url/--unset-browser-url`, and `twicc create-workspace --browser-url`, with the matching SKILL.md edits, SKILLS-AND-CLI.md entries, and ONE plugin minor bump (`0.53.2` → `0.54.0`) covering all touched skills. `twicc project` / `twicc workspace(s)` inspect output needs NO change (shared serializer / raw-dict pass-through) |
| Backend URL validation | One shared home: `normalize_browser_url` + `validate_browser_url` in `twicc/workspaces.py` next to `validate_color` (the established cross-domain validator spot), used by the project PUT, the session PATCH, both mutation services, and the three CLI commands |
| CHANGELOG | **Do not touch** `CHANGELOG.md` — the user adds entries on explicit request only |

## Known limits & gotchas (accepted, documented — do not "fix")

1. **Docking moves reload the iframe.** Tool panels move between regions by Teleport retarget; moving an `<iframe>` node in the DOM always reloads it. Tab switches use `v-show` and do NOT reload. Nothing to do — same physics as the FilePane HTML preview.
2. **Framing refusal is invisible client-side.** A site sending `X-Frame-Options`/`frame-ancestors` renders as a blank frame with a normal `load` event. Task 10 (optional) adds a server-side probe + banner; without it the info tooltip explains the symptom.
3. **Mixed content:** an https-served TwiCC cannot embed `http://` URLs — the pane detects this combination and shows an explanation instead of a dead frame.
4. **SameSite cookies:** inside an iframe the embedded app is in a third-party context; `Lax`/`Strict` session cookies are not sent, so some apps won't keep you logged in there. Localhost dev servers are usually cookie-less or tolerant. Mentioned in the info tooltip.
5. **Keyboard shortcuts go dead while the iframe has focus** — keydown events inside a cross-origin frame never reach TwiCC. Browser-level, unfixable; the user clicks TwiCC chrome to get shortcuts back.
6. **Worktree inheritance can carry the wrong port** (each worktree instance often has its own dev port). The chain fallback is still right by default; a worktree project can set its own `default_browser_url` to override.
7. **`Alt+Shift+0` on project-home tabs** dispatches `{type:'direct', index:0}` to `ProjectDetailPanel` too; its `DIRECT_TAB_MAP` has no `0` entry, so it no-ops — intended.
8. **No layout migration:** existing sessions' `Session.layout.assignment` simply lacks `browser`; `bucketTabs` defaults it to center. Saved named layouts are equally tolerant.

---

### Task 1: Backend — `Project.default_browser_url`

**Files:**
- Modify: `src/twicc/core/models.py:145` (after `default_layout_id`)
- Create: `src/twicc/core/migrations/0121_project_default_browser_url.py`
- Modify: `src/twicc/core/serializers.py:45` (after `default_layout_id`)
- Modify: `src/twicc/workspaces.py:149-158` (shared validators, next to `validate_color`)
- Modify: `src/twicc/views.py:38` (import) and `:605-612` (PUT branch, after the `default_layout_id` branch)

- [ ] **Step 1: Add the model field**

In `src/twicc/core/models.py`, directly after the `default_layout_id` field (line 145), before the `# ---- Worktree creation` comment block:

```python
    # Per-project default URL for the session Browser pane (an unsandboxed
    # iframe showing e.g. the project's dev server). NULL = inherit: the pane
    # walks the worktree/path chain, then falls back to the first containing
    # workspace's browserUrl (workspaces.json). Resolved LIVE on the frontend
    # every time the pane opens — unlike the agent-settings bundle it is never
    # materialized onto sessions. http(s) only, enforced at the PUT endpoint.
    default_browser_url = models.CharField(max_length=2000, null=True, blank=True, default=None)
```

- [ ] **Step 2: Create the migration**

Create `src/twicc/core/migrations/0121_project_default_browser_url.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0120_session_goals")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="default_browser_url",
            field=models.CharField(blank=True, default=None, max_length=2000, null=True),
        ),
    ]
```

- [ ] **Step 3: Verify the migration matches the model**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --check --dry-run --settings=twicc.settings
```
Expected: `No changes detected in app 'core'`. (The `TWICC_DATA_DIR=$PWD` is mandatory — without it Django resolves the **prod** data dir `~/.twicc/`. Do NOT run `migrate` yourself; devctl applies it at backend startup.)

- [ ] **Step 4: Serialize the field**

In `src/twicc/core/serializers.py`, in `serialize_project()`, after the `"default_layout_id"` line (line 45):

```python
        # Per-project Browser-pane default URL (None = inherit; see models.py).
        "default_browser_url": project.default_browser_url,
```

- [ ] **Step 5: Shared validators**

Backend browser-URL validation is needed at six sites (project PUT, session PATCH, two payload services, two CLI commands). Follow the `validate_color` convention — one shared home in `src/twicc/workspaces.py` (already imported cross-domain by the project CLI and services). Add right after `validate_color` (line ~158):

```python
def normalize_browser_url(value: str | None) -> str | None:
    """Trim a Browser-pane URL; empty collapses to ``None`` (= clear/inherit)."""
    if value is None:
        return None
    return value.strip() or None


def validate_browser_url(url: str | None, *, field: str = "browser_url") -> list[WorkspaceMutationError]:
    """Validate an already-normalized Browser-pane URL: http(s) only, ≤ 2000 chars.

    ``None`` is OK (= clear). One home for the rule — shared by the project
    PUT, the session PATCH, the workspace/project mutation services, and the
    CLI commands, like ``validate_color``.
    """
    if url is None:
        return []
    if not url.startswith(("http://", "https://")):
        return [WorkspaceMutationError(field, "invalid_value",
                                       f"{field} must be an http(s) URL.")]
    if len(url) > 2000:
        return [WorkspaceMutationError(field, "invalid_value",
                                       f"{field} must be 2000 characters or less.")]
    return []
```

- [ ] **Step 6: Accept the field in the PUT handler**

In `src/twicc/views.py`: extend the top-level import (line 38) to
```python
from twicc.workspaces import add_project_to_workspaces, normalize_browser_url, read_workspaces, validate_browser_url
```
then inside `project_detail()`'s PUT branch, right after the `default_layout_id` block (after `update_fields.append("default_layout_id")`, line ~612):

```python
        if "default_browser_url" in data:
            # http(s) only — the Browser pane must never be pointed at
            # javascript:/file:/data: targets. Empty/None = inherit.
            browser_url = data["default_browser_url"]
            if browser_url is not None and not isinstance(browser_url, str):
                return JsonResponse({"error": "default_browser_url must be a string or null"}, status=400)
            browser_url = normalize_browser_url(browser_url)
            url_errors = validate_browser_url(browser_url, field="default_browser_url")
            if url_errors:
                return JsonResponse({"error": url_errors[0].message}, status=400)
            project.default_browser_url = browser_url
            update_fields.append("default_browser_url")
```

- [ ] **Step 7: Sanity-check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
uv run ruff check src/twicc/core/models.py src/twicc/core/serializers.py src/twicc/views.py src/twicc/workspaces.py
```
Expected: no issues.

- [ ] **Step 8: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/0121_project_default_browser_url.py src/twicc/core/serializers.py src/twicc/views.py src/twicc/workspaces.py
git commit -m "feat(browser-pane): add Project.default_browser_url (model, migration, serializer, PUT)"
```

---

### Task 2: Frontend util — URL normalization (TDD)

**Files:**
- Create: `frontend/src/utils/browserUrl.js`
- Test: `frontend/src/utils/browserUrl.test.js`

- [ ] **Step 1: Write the failing tests**

No test framework is wired into the frontend — tests use Node's built-in runner, matching `frontend/src/utils/version.test.js` (do NOT add vitest/jest).

Create `frontend/src/utils/browserUrl.test.js`:

```js
// frontend/src/utils/browserUrl.test.js
//
// Run with:  node --test src/utils/browserUrl.test.js   (from the frontend dir)
//
// No test framework is wired into the frontend, so this uses Node's built-in
// test runner (node:test) — zero dependencies, no node_modules required.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeBrowserUrl } from './browserUrl.js'

test('keeps explicit http(s) URLs (normalized by URL())', () => {
    assert.equal(normalizeBrowserUrl('http://localhost:3000'), 'http://localhost:3000/')
    assert.equal(normalizeBrowserUrl('https://example.com/app?x=1#y'), 'https://example.com/app?x=1#y')
    assert.equal(normalizeBrowserUrl('HTTPS://Example.COM'), 'https://example.com/')
})

test('adds http:// to localhost-ish schemeless input', () => {
    assert.equal(normalizeBrowserUrl('localhost:5173'), 'http://localhost:5173/')
    assert.equal(normalizeBrowserUrl('localhost'), 'http://localhost/')
    assert.equal(normalizeBrowserUrl('127.0.0.1:8000/admin/'), 'http://127.0.0.1:8000/admin/')
    assert.equal(normalizeBrowserUrl('192.168.1.42:3000'), 'http://192.168.1.42:3000/')
    assert.equal(normalizeBrowserUrl('0.0.0.0:5174'), 'http://0.0.0.0:5174/')
    assert.equal(normalizeBrowserUrl('myapp.local:3000'), 'http://myapp.local:3000/')
    assert.equal(normalizeBrowserUrl('site.test'), 'http://site.test/')
})

test('adds https:// to other schemeless input', () => {
    assert.equal(normalizeBrowserUrl('example.com'), 'https://example.com/')
    assert.equal(normalizeBrowserUrl('example.com:8443/x'), 'https://example.com:8443/x')
})

test('treats dotless host:port as a schemeless local host, not as a scheme', () => {
    assert.equal(normalizeBrowserUrl('devbox:9000'), 'http://devbox:9000/')
})

test('rejects non-http(s) schemes', () => {
    assert.equal(normalizeBrowserUrl('javascript:alert(1)'), null)
    assert.equal(normalizeBrowserUrl('file:///etc/passwd'), null)
    assert.equal(normalizeBrowserUrl('data:text/html,<b>x</b>'), null)
    assert.equal(normalizeBrowserUrl('ftp://example.com'), null)
    assert.equal(normalizeBrowserUrl('mailto:x@y.z'), null)
})

test('rejects empty / unparsable input', () => {
    assert.equal(normalizeBrowserUrl(''), null)
    assert.equal(normalizeBrowserUrl('   '), null)
    assert.equal(normalizeBrowserUrl(null), null)
    assert.equal(normalizeBrowserUrl(undefined), null)
    assert.equal(normalizeBrowserUrl('http://'), null)
})

test('trims surrounding whitespace', () => {
    assert.equal(normalizeBrowserUrl('  localhost:3000  '), 'http://localhost:3000/')
})
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend && node --test src/utils/browserUrl.test.js
```
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the util**

Create `frontend/src/utils/browserUrl.js`:

```js
// Address-bar input normalization for the session Browser pane. Only http(s)
// targets come out — anything else (javascript:, file:, data:, ftp:…) is
// rejected (null), never coerced. Shared by the pane's address bar and the
// project / workspace default-URL form fields.

// Hosts that get http:// (not https://) when the user types no scheme —
// local dev servers are overwhelmingly plain http. Covers localhost, IPv4,
// [::1], *.local/*.test/*.localhost, and any dotless single-label host with
// an explicit port ("devbox:9000" — a LAN/container name, never a public site).
const LOCAL_HOST_RE = /^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\]|(\d{1,3}\.){3}\d{1,3}|[^./:\s]+\.(local|test|localhost)|[^./:\s]+(?=:\d))(:\d+)?([/?#]|$)/i

/**
 * Normalize free-form address-bar input into an absolute http(s) URL.
 * @returns {(string|null)} the normalized URL (via `new URL().href`), or null
 *   when the input is empty, unparsable, or uses a non-http(s) scheme.
 */
export function normalizeBrowserUrl(input) {
    const raw = (input || '').trim()
    if (!raw) return null

    let candidate
    if (/^https?:\/\//i.test(raw)) {
        candidate = raw
    } else if (/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
        return null // explicit non-http(s) scheme (file://, ftp://, …)
    } else if (/^[a-z][a-z0-9+.-]*:(?!\d)/i.test(raw)) {
        // Scheme-like prefix that is NOT a host:port (javascript:, data:,
        // mailto:…). "devbox:9000" escapes via the (?!\d) lookahead and is
        // handled as schemeless below.
        return null
    } else {
        candidate = `${LOCAL_HOST_RE.test(raw) ? 'http' : 'https'}://${raw}`
    }

    let url
    try {
        url = new URL(candidate)
    } catch {
        return null
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    if (!url.hostname) return null
    return url.href
}
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend && node --test src/utils/browserUrl.test.js
```
Expected: all PASS (pay attention to `devbox:9000` — it exercises the dotless-host-with-port alternative of `LOCAL_HOST_RE`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/browserUrl.js frontend/src/utils/browserUrl.test.js
git commit -m "feat(browser-pane): URL normalization util with tests"
```

---

### Task 3: Frontend util — default-URL chain resolver

**Files:**
- Create: `frontend/src/utils/browserDefaults.js`

- [ ] **Step 1: Create the resolver**

Create `frontend/src/utils/browserDefaults.js` (mirrors `layoutDefaults.js`):

```js
// Browser-pane default URL: project-chain resolver. Same walk as the other
// per-project defaults — worktree main repo first, else nearest path ancestor
// (see projectAgentDefaults.js) — the first ancestor with an own
// default_browser_url wins. The workspace fallback (first non-archived
// workspace containing the project that carries a browserUrl) lives in
// BrowserPane.vue: it needs the workspaces store, not the project chain.
import { ancestorChain } from './projectAgentDefaults'

/**
 * @param {string|null} projectId
 * @param {Object} projectsById - dataStore.projects (id → project row)
 * @returns {(string|null)} the inherited default URL, or null when nothing in
 *   the chain sets one (the caller falls back to workspaces, then blank).
 */
export function resolveProjectBrowserUrl(projectId, projectsById) {
    if (!projectId) return null
    for (const node of ancestorChain(projectId, projectsById)) {
        if (node.default_browser_url) return node.default_browser_url
    }
    return null
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/utils/browserDefaults.js
git commit -m "feat(browser-pane): project-chain resolver for the default URL"
```

---

### Task 4: Workspaces — `browserUrl` field

**Files:**
- Modify: `frontend/src/stores/workspaces.js:203-228` (`createWorkspace`, `updateWorkspace`)
- Modify: `frontend/src/components/workspace/WorkspaceManageDialog.vue` (form data + save + template)

Backend note: nothing to do server-side — `workspaces.json` is written whole-blob by the WS `update_workspaces` handler, and the CLI's `update_workspace_atomic()` mutates dicts key-by-key, so an extra `browserUrl` key round-trips untouched.

- [ ] **Step 1: Extend the store actions**

In `frontend/src/stores/workspaces.js`, replace `createWorkspace` and `updateWorkspace`:

```js
        /** Create a new workspace. Returns the new workspace object. */
        createWorkspace({ name, projectIds = [], archived = false, color = null, autoProjectPatterns = [], browserUrl = null }) {
            const trimmedName = name.trim()
            const ws = {
                id: this._generateId(trimmedName),
                name: trimmedName,
                archived,
                projectIds,
                color,
                autoProjectPatterns,
                browserUrl,
            }
            this.workspaces.push(ws)
            this._sendWorkspaces()
            return ws
        },

        /** Update an existing workspace. */
        updateWorkspace(id, { name, projectIds, archived, color, autoProjectPatterns, browserUrl }) {
            const ws = this.workspaces.find(w => w.id === id)
            if (!ws) return
            if (name !== undefined) ws.name = name.trim()
            if (projectIds !== undefined) ws.projectIds = projectIds
            if (archived !== undefined) ws.archived = archived
            if (color !== undefined) ws.color = color
            if (autoProjectPatterns !== undefined) ws.autoProjectPatterns = autoProjectPatterns
            if (browserUrl !== undefined) ws.browserUrl = browserUrl
            this._sendWorkspaces()
        },
```

Also update the state doc comment (line 7) to:
```js
        workspaces: [],           // Array of { id, name, archived, projectIds: string[], autoProjectPatterns?: string[], browserUrl?: string|null }
```

- [ ] **Step 2: Add the form field to WorkspaceManageDialog**

In `frontend/src/components/workspace/WorkspaceManageDialog.vue`:

(a) Import the normalizer (top of `<script setup>`, with the other util import):
```js
import { normalizeBrowserUrl } from '../../utils/browserUrl'
```

(b) Add `browserUrl` to `formData` (line 35-42):
```js
const formData = ref({
    id: null,          // null for create mode
    name: '',
    color: '',
    archived: false,
    projectIds: [],    // local copy, manipulated freely until save
    autoProjectPatterns: [],
    browserUrl: '',
})
```

(c) In `openAddForm()` (line 111), add `browserUrl: '',` to the reset object; in `openEditForm(workspace)` (line 128), add `browserUrl: workspace.browserUrl || '',`.

(d) In `handleSave()` (line 243), after the uniqueness check and before building `payload`, validate; then include the field in the payload:
```js
    let browserUrl = null
    const rawBrowserUrl = formData.value.browserUrl.trim()
    if (rawBrowserUrl) {
        browserUrl = normalizeBrowserUrl(rawBrowserUrl)
        if (!browserUrl) {
            errorMessage.value = 'Browser URL must be a valid http(s) URL.'
            return
        }
    }

    const payload = {
        name: trimmedName,
        color: formData.value.color || null,
        projectIds: [...formData.value.projectIds],
        archived: formData.value.archived,
        autoProjectPatterns: [...formData.value.autoProjectPatterns],
        browserUrl,
    }
```

(e) In the template, after the "Auto-add project patterns" form-group (after its closing `</div>` at line ~650), add:
```html
            <wa-divider></wa-divider>

            <!-- Browser pane default URL -->
            <div class="form-group">
                <label class="form-label">Browser URL</label>
                <p class="form-help-text">
                    Default URL opened by the session Browser tab for projects of this
                    workspace. A project's own Browser URL takes precedence.
                </p>
                <wa-input
                    :value="formData.browserUrl"
                    @input="formData.browserUrl = $event.target.value"
                    placeholder="e.g. http://localhost:3000"
                    size="small"
                />
            </div>
```

- [ ] **Step 3: Manual check (deferred to final verification)** — edit a workspace, set a URL, save, reopen: the value persists; `<data_dir>/workspaces.json` carries `"browserUrl"`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/workspaces.js frontend/src/components/workspace/WorkspaceManageDialog.vue
git commit -m "feat(browser-pane): workspace browserUrl field (store + manage dialog)"
```

---

### Task 5: ProjectEditDialog — Browser URL field

**Files:**
- Modify: `frontend/src/components/project/ProjectEditDialog.vue`

- [ ] **Step 1: Wire the local state**

(a) Import (top of `<script setup>`, near the `apiFetch` import):
```js
import { normalizeBrowserUrl } from '../../utils/browserUrl'
```

(b) Below `const localDefaultLayoutId = ref(LAYOUT_INHERIT)` (line 54):
```js
const localDefaultBrowserUrl = ref('')
```

(c) In the `watch(() => props.project, …)` (line 216): add to the edit branch (after line 227) `localDefaultBrowserUrl.value = newProject.default_browser_url || ''` and to the create-reset branch (after line 237) `localDefaultBrowserUrl.value = ''`.

(d) In `open()` (edit branch, after line 316): `localDefaultBrowserUrl.value = props.project.default_browser_url || ''`.

- [ ] **Step 2: Save on change**

In `handleSave()`'s edit branch, right after the `default_layout_id` block (line 550-556):

```js
        // Per-project Browser-pane URL: send only when changed; '' → null (inherit).
        const originalBrowserUrl = props.project.default_browser_url || ''
        const trimmedBrowserUrl = localDefaultBrowserUrl.value.trim()
        if (trimmedBrowserUrl !== originalBrowserUrl) {
            if (trimmedBrowserUrl) {
                const normalizedBrowserUrl = normalizeBrowserUrl(trimmedBrowserUrl)
                if (!normalizedBrowserUrl) {
                    errorMessage.value = 'Browser URL must be a valid http(s) URL'
                    isSaving.value = false
                    return
                }
                body.default_browser_url = normalizedBrowserUrl
            } else {
                body.default_browser_url = null
            }
        }
```

- [ ] **Step 3: Add the form field**

In the template, right after the "Default layout (edit mode)" form-group's closing `</div>` (line 795), before the existing `<wa-divider v-if="!isCreateMode">` that precedes Workspaces:

```html
                        <wa-divider v-if="!isCreateMode"></wa-divider>

                        <!-- Browser pane URL (edit mode) -->
                        <div v-if="!isCreateMode" class="form-group">
                            <label class="form-label">Browser URL</label>
                            <wa-input
                                :value="localDefaultBrowserUrl"
                                @input="localDefaultBrowserUrl = $event.target.value"
                                placeholder="e.g. http://localhost:3000"
                                size="small"
                            />
                            <div class="form-hint">
                                Default URL opened by the session Browser tab. Empty = inherit —
                                the parent project's URL (for a worktree), then a containing
                                workspace's Browser URL.
                            </div>
                        </div>
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/project/ProjectEditDialog.vue
git commit -m "feat(browser-pane): project Browser URL field in the edit dialog"
```

---

### Task 6: The BrowserPane component

**Files:**
- Create: `frontend/src/components/browser/BrowserPane.vue`

All Web Awesome components used (`wa-button`, `wa-icon`, `wa-input`, `wa-spinner`, `wa-dropdown`, `wa-dropdown-item`, `wa-divider`, `wa-callout`) are already imported in `main.js` — no import to add there.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/browser/BrowserPane.vue`:

```vue
<script setup>
// Browser pane: embeds a user-chosen URL (typically the project's dev server)
// in a plain iframe. Unlike the Artifacts preview there is NO sandbox, NO CSP
// lockdown and NO network broker — deliberate: the page runs exactly as in a
// normal browser tab (direct network, service workers, HMR websockets), it
// just cannot reach into TwiCC (cross-origin isolation applies both ways).
//
// Cross-origin iframes expose neither their current location nor their
// history, so the toolbar keeps its OWN history: it records the URLs
// navigated via the address bar / Back / Forward / Home only. Links followed
// inside the page are invisible to it, and Refresh re-creates the iframe on
// the last recorded URL (in-page navigation is lost). Deliberate, documented
// limits — see the info tooltip.
import { computed, ref, useId, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { normalizeBrowserUrl } from '../../utils/browserUrl'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
})

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const instanceId = useId()

// ── Default URL: project chain first, then the first non-archived workspace
// containing the project (worktree-aware) that carries a browserUrl.
const projectDefaultUrl = computed(() => resolveProjectBrowserUrl(props.projectId, store.projects))
const workspaceDefaultUrl = computed(() => {
    if (!props.projectId) return null
    const ws = workspacesStore.workspaces.find(
        (w) => !w.archived && w.browserUrl && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
    return ws?.browserUrl || null
})
const defaultUrl = computed(() => projectDefaultUrl.value || workspaceDefaultUrl.value)

// ── Navigation state. `urlHistory` holds address-bar-level navigations only
// (named to avoid shadowing window.history inside this component).
const currentUrl = ref('')       // URL the iframe was last pointed at ('' = blank state)
const inputUrl = ref('')         // address bar edit buffer
const urlHistory = ref([])
const historyIndex = ref(-1)
const frameKey = ref(0)          // bump = recreate the iframe (navigate / refresh)
const loading = ref(false)
const everActivated = ref(false)

const canGoBack = computed(() => historyIndex.value > 0)
const canGoForward = computed(() => historyIndex.value < urlHistory.value.length - 1)

// An https TwiCC page cannot embed an http iframe (the browser blocks it
// silently as mixed content) — explain instead of showing a dead frame.
const mixedContentBlocked = computed(
    () => window.location.protocol === 'https:' && currentUrl.value.startsWith('http://')
)

function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameKey.value++
    loading.value = true
}

function navigate(rawInput) {
    const url = normalizeBrowserUrl(rawInput)
    if (!url) return
    // Truncate forward entries, then push (skip contiguous repeats).
    const stack = urlHistory.value.slice(0, historyIndex.value + 1)
    if (stack[stack.length - 1] !== url) stack.push(url)
    urlHistory.value = stack
    historyIndex.value = stack.length - 1
    showFrame(url)
}

function goBack() {
    if (!canGoBack.value) return
    historyIndex.value--
    showFrame(urlHistory.value[historyIndex.value])
}

function goForward() {
    if (!canGoForward.value) return
    historyIndex.value++
    showFrame(urlHistory.value[historyIndex.value])
}

function refresh() {
    if (!currentUrl.value) return
    showFrame(currentUrl.value)
}

function goHome() {
    if (defaultUrl.value) navigate(defaultUrl.value)
}

function openExternal() {
    if (currentUrl.value) window.open(currentUrl.value, '_blank', 'noopener')
}

function onAddressSubmit() {
    navigate(inputUrl.value)
}

function onFrameLoad() {
    // Fires even for framing-refused pages (the error document loads) — it
    // only means "network settled", not "content visible".
    loading.value = false
}

// ── Lazy init: on first activation, auto-load the resolved default.
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        if (defaultUrl.value) navigate(defaultUrl.value)
    },
    { immediate: true }
)

// ── Focus the address bar on explicit tab activation (keyboard / tab click),
// mirroring the other ACTIVATION_FOCUS_TABS panels.
const addressInputRef = ref(null)
watch(
    () => props.focusRequest,
    () => {
        requestAnimationFrame(() => addressInputRef.value?.focus())
    }
)

// ── Save-URL menu -------------------------------------------------------------
const project = computed(() => store.getProject(props.projectId))
const mainRepoProject = computed(() =>
    project.value?.worktree_of ? store.getProject(project.value.worktree_of) : null
)
const memberWorkspaces = computed(() =>
    workspacesStore.workspaces.filter(
        (w) => !w.archived && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
)
const canSave = computed(() => !!currentUrl.value && !!project.value)
const saveError = ref('')

async function saveToProject(projectId) {
    try {
        const response = await apiFetch(`/api/projects/${projectId}/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_browser_url: currentUrl.value }),
        })
        if (!response.ok) {
            const data = await response.json().catch(() => ({}))
            throw new Error(data.error || `Failed to save (${response.status})`)
        }
        store.updateProject(await response.json())
    } catch (e) {
        saveError.value = e.message || 'Failed to save URL'
    }
}

function onSaveSelect(event) {
    const value = event.detail?.item?.value
    if (!value || !currentUrl.value) return
    saveError.value = ''
    if (value === 'project') {
        saveToProject(props.projectId)
    } else if (value === 'main-repo') {
        saveToProject(project.value.worktree_of)
    } else if (value.startsWith('ws:')) {
        workspacesStore.updateWorkspace(value.slice(3), { browserUrl: currentUrl.value })
    }
}
</script>

<template>
    <div class="browser-pane">
        <div class="browser-toolbar">
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!canGoBack" title="Back" @click="goBack">
                <wa-icon name="arrow-left"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!canGoForward" title="Forward" @click="goForward">
                <wa-icon name="arrow-right"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Refresh (reloads the last entered URL)" @click="refresh">
                <wa-icon name="rotate-right"></wa-icon>
            </wa-button>
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!defaultUrl" :title="defaultUrl ? `Home — ${defaultUrl}` : 'Home (no saved URL for this project)'" @click="goHome">
                <wa-icon name="house"></wa-icon>
            </wa-button>

            <wa-input
                ref="addressInputRef"
                class="browser-address"
                size="small"
                autocomplete="off"
                placeholder="Enter a URL — e.g. localhost:5173"
                :value="inputUrl"
                @input="inputUrl = $event.target.value"
                @keydown.enter.prevent="onAddressSubmit"
            >
                <wa-spinner v-if="loading" slot="start"></wa-spinner>
                <wa-icon v-else slot="start" name="globe"></wa-icon>
            </wa-input>

            <!-- Save current URL as a project / workspace default. WA custom
                 events are stopped from bubbling (a nested dropdown's wa-show /
                 wa-hide would otherwise reach same-named ancestor handlers). -->
            <wa-dropdown
                placement="bottom-end"
                @click.stop
                @wa-select.stop="onSaveSelect"
                @wa-show.stop
                @wa-hide.stop
                @wa-after-show.stop
                @wa-after-hide.stop
            >
                <wa-button slot="trigger" appearance="plain" size="small" class="browser-btn" :disabled="!canSave" title="Save this URL as a default…">
                    <wa-icon name="bookmark"></wa-icon>
                </wa-button>
                <wa-dropdown-item disabled class="save-menu-header">Save current URL as default for…</wa-dropdown-item>
                <wa-dropdown-item value="project" :disabled="project?.default_browser_url === currentUrl">
                    <wa-icon slot="icon" name="folder"></wa-icon>
                    {{ store.getProjectDisplayName(props.projectId) }}
                    <span v-if="project?.default_browser_url === currentUrl" class="save-menu-saved">saved</span>
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="mainRepoProject"
                    value="main-repo"
                    :disabled="mainRepoProject.default_browser_url === currentUrl"
                >
                    <wa-icon slot="icon" name="folder-tree"></wa-icon>
                    {{ store.getProjectDisplayName(mainRepoProject.id) }} (main repository)
                    <span v-if="mainRepoProject.default_browser_url === currentUrl" class="save-menu-saved">saved</span>
                </wa-dropdown-item>
                <template v-if="memberWorkspaces.length">
                    <wa-divider></wa-divider>
                    <wa-dropdown-item
                        v-for="ws in memberWorkspaces"
                        :key="ws.id"
                        :value="`ws:${ws.id}`"
                        :disabled="ws.browserUrl === currentUrl"
                    >
                        <wa-icon slot="icon" name="layer-group" :style="ws.color ? { color: ws.color } : null"></wa-icon>
                        {{ ws.name }}
                        <span v-if="ws.browserUrl === currentUrl" class="save-menu-saved">saved</span>
                    </wa-dropdown-item>
                </template>
            </wa-dropdown>

            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Open in a new browser tab" @click="openExternal">
                <wa-icon name="arrow-up-right-from-square"></wa-icon>
            </wa-button>

            <wa-icon :id="`browser-info-${instanceId}`" name="circle-info" class="browser-info"></wa-icon>
            <AppTooltip :for="`browser-info-${instanceId}`">
                Embedded pages are isolated: Back/Forward/Refresh only track URLs
                entered here — links followed inside the page are invisible to this
                toolbar, and Refresh reloads the last entered URL. Some sites refuse
                to be embedded (X-Frame-Options) and stay blank; logins may not
                persist inside a frame. Keyboard shortcuts pause while the page has
                focus — click TwiCC's chrome to get them back.
            </AppTooltip>
        </div>

        <wa-callout v-if="saveError" variant="danger" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            {{ saveError }}
        </wa-callout>

        <wa-callout v-if="mixedContentBlocked" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            TwiCC is served over https, so the browser blocks embedding this http://
            URL (mixed content). Open it in a new tab instead, or serve it over https.
        </wa-callout>

        <div class="browser-body">
            <iframe
                v-if="everActivated && currentUrl && !mixedContentBlocked"
                :key="frameKey"
                :src="currentUrl"
                class="browser-frame"
                allow="clipboard-read; clipboard-write; fullscreen"
                title="Browser"
                @load="onFrameLoad"
            ></iframe>
            <div v-else-if="!currentUrl" class="browser-empty">
                <wa-icon name="globe" class="browser-empty-icon"></wa-icon>
                <p>Enter a URL above to preview your project — e.g. your dev server.</p>
                <p class="browser-empty-hint">
                    Use the <wa-icon name="bookmark"></wa-icon> menu to save it as the
                    default for this project or one of its workspaces.
                </p>
            </div>
        </div>
    </div>
</template>

<style scoped>
.browser-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.browser-toolbar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.browser-btn {
    flex-shrink: 0;
}

.browser-address {
    flex: 1;
    min-width: 6rem;
}

.browser-info {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    margin-inline: var(--wa-space-2xs);
}

.browser-banner {
    margin: var(--wa-space-xs);
    flex-shrink: 0;
}

.save-menu-header::part(label) {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.save-menu-saved {
    margin-left: var(--wa-space-xs);
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-quiet);
}

.browser-body {
    flex: 1;
    min-height: 0;
    display: flex;
}

/* White canvas: most pages assume a light default background, and a
   transparent iframe over TwiCC's dark theme renders them unreadable. */
.browser-frame {
    flex: 1;
    width: 100%;
    height: 100%;
    border: none;
    background: #fff;
}

.browser-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding: var(--wa-space-l);
}

.browser-empty-icon {
    font-size: 2.5rem;
    opacity: 0.5;
}

.browser-empty p {
    margin: 0;
}

.browser-empty-hint {
    font-size: var(--wa-font-size-s);
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/browser/BrowserPane.vue
git commit -m "feat(browser-pane): BrowserPane component (toolbar, history stack, iframe)"
```

---

### Task 7: SessionView integration

**Files:**
- Modify: `frontend/src/views/SessionView.vue` (8 spots, all listed)

- [ ] **Step 1: Import the component**

After `import WorkflowsPane from '../components/workflows/WorkflowsPane.vue'` (line 23):
```js
import BrowserPane from '../components/browser/BrowserPane.vue'
```

- [ ] **Step 2: Route → active tab**

In the `activeTabId` computed (line 431), after the `workflows` line (452):
```js
    if (name === 'session-browser' || name === 'projects-session-browser') return 'browser'
```

- [ ] **Step 3: Registry entry**

In `TOOL_TABS` (line 463), add as the LAST entry (after `workflows`):
```js
    { id: 'browser', label: 'Browser', icon: 'globe', present: () => true },
```

- [ ] **Step 4: Tool-tab id list + remembered routes**

Line 569:
```js
const TOOL_TAB_IDS = ['files', 'artifacts', 'git', 'terminal', 'orchestration', 'plan', 'tasks', 'workflows', 'browser']
```

In `rememberedToolTabRoutes` (line 573), add `browser: null,` after `workflows: null,` and extend the inner comment to `// Orchestration, Plan, Tasks, Workflows and Browser have no granular sub-route; kept`.

- [ ] **Step 5: Activation focus**

Lines 830-831 become:
```js
const ACTIVATION_FOCUS_TABS = ['files', 'git', 'artifacts', 'terminal', 'browser']
const panelFocusRequests = reactive({ files: 0, git: 0, artifacts: 0, terminal: 0, browser: 0 })
```

- [ ] **Step 6: Direct shortcut map**

Line 1190-1193 — update the comment and the map:
```js
// Direct tab mapping: Alt+Shift+{1..9, 0} → fixed tabs (subagents are skipped).
// Tasks (5), Plan (6), Artifacts (7), Orchestration (8) and Workflows (9) are
// conditional — the handler no-ops when the tab is absent. 0 is Browser (the
// last tab, always present).
const DIRECT_TAB_MAP = { 1: 'main', 2: 'files', 3: 'git', 4: 'terminal', 5: 'tasks', 6: 'plan', 7: 'artifacts', 8: 'orchestration', 9: 'workflows', 0: 'browser' }
```

- [ ] **Step 7: Template — nav tab, center panel, teleported pane**

(a) After the Workflows `<wa-tab>` (line 2039-2043) — no `isToolTabPresent` guard (always present, like Files/Terminal):
```html
            <wa-tab v-if="showInCenter('browser')" slot="nav" panel="browser" @click="onCenterTabClick('browser')">
                <wa-icon :name="TAB_ICONS.browser"></wa-icon>
                Browser
                <TabPlacementMenu v-if="showCenterPlacementArrows" tab-id="browser" current="center" @place="(dest) => layout.place('browser', dest)" />
            </wa-tab>
```

(b) After the Workflows `<wa-tab-panel>` (line 2113-2115):
```html
            <wa-tab-panel v-if="showInCenter('browser')" name="browser">
                <div :ref="centerTargetSetters.browser" class="layout-center-target"></div>
            </wa-tab-panel>
```
(`centerTargetSetters` is derived from `LAYOUT_TOOL_IDS = TOOL_TABS.map(…)`, so `browser` is picked up automatically.)

(c) After the Workflows `<Teleport>` block (line 2257-2267), inside `.layout-panel-host`:
```html
            <Teleport :to="toolTarget('browser')" :disabled="!toolTarget('browser')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('browser')">
                    <BrowserPane
                        :project-id="session?.project_id"
                        :active="isActive && isToolTabShown('browser')"
                        :focus-request="panelFocusRequests.browser"
                    />
                </div>
            </Teleport>
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "feat(browser-pane): register the Browser tool tab in SessionView"
```

---

### Task 8: Routes, keyboard shortcut, command palette, cheat sheet

**Files:**
- Modify: `frontend/src/router.js:53-61` and `:85-93`
- Modify: `frontend/src/App.vue:166-170` and `:310-319`
- Modify: `frontend/src/utils/scopeMemory.js:22-28` (`SESSION_NAMES` — "authoritative; mirrors router.js")
- Modify: `frontend/src/commands/staticCommands.js:752-775` (after `nav.tab.workflows`)
- Modify: `frontend/src/components/app/SettingsPopover.vue:258-264`

- [ ] **Step 1: Router — both route trees**

In `frontend/src/router.js`, add after the `workflows` child of the `session` tree (line 60):
```js
                    { path: 'browser', name: 'session-browser', component: { render: () => null } },
```
and after the `workflows` child of the `projects-session` tree (line 92):
```js
                    { path: 'browser', name: 'projects-session-browser', component: { render: () => null } },
```

- [ ] **Step 2: App.vue — route gate + digit 0**

(a) `SESSION_ROUTES` (lines 166-170): update the comment above the set to mention `0`, and append `'session-browser'` / `'projects-session-browser'` to the two lists:
```js
// All session route names (for tab keyboard shortcuts: Alt+Shift+{1-9, 0, ←, →, ↑})
const SESSION_ROUTES = new Set([
    'session', 'session-subagent', 'session-files', 'session-artifacts', 'session-git', 'session-terminal', 'session-orchestration', 'session-plan', 'session-tasks', 'session-workflows', 'session-browser',
    'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-artifacts', 'projects-session-git', 'projects-session-terminal', 'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser',
])
```

(b) In the `Alt+Shift+{digits}` branch (line 310-319), update the comment and widen the digit class **in this branch only** — the terminal (line 274) and workflow (line 294) branches keep `[1-9]`:
```js
    // Alt+Shift+{1-9, 0, ←, →, ↑, ↓}: tab navigation within a session or project detail panel.
    // Dispatches a custom event handled by the active SessionView or ProjectDetailPanel instance.
    // (Indices 5/6/7/8/9 are the session-only Tasks/Plan/Artifacts/Orchestration/Workflows tabs
    // and 0 the session-only Browser tab; project-detail panels ignore them.)
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && (SESSION_ROUTES.has(route.name) || PROJECT_DETAIL_ROUTES.has(route.name))) {
        let tabAction = null
        // Use e.code (physical key) for digits — e.key depends on keyboard layout
        // and modifiers (e.g. French AZERTY: Alt+Shift+number row produces unexpected e.key values).
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([0-9])$/)
```
(Only the comment lines and the regex change; the rest of the branch is untouched. `parseInt('0')` → `0`, `DIRECT_TAB_MAP[0]` resolves in SessionView, and `ProjectDetailPanel`'s map has no `0` so it no-ops there.)

- [ ] **Step 3: scopeMemory — session route family**

`frontend/src/utils/scopeMemory.js` keeps its own route-name sets ("authoritative; mirrors router.js"). Without this, `scopeKey()` returns `null` on the Browser tab: the tab is never recorded as a session's last location, and worse, navigating Browser → Chat is mistaken for *entering the session from outside* and redirected to a stale remembered tab. In `SESSION_NAMES` (lines 22-28), append `'session-browser'` to the `session-*` group and `'projects-session-browser'` to the `projects-session-*` group:

```js
const SESSION_NAMES = new Set([
    'session', 'session-files', 'session-git', 'session-terminal',
    'session-artifacts', 'session-orchestration', 'session-plan', 'session-tasks', 'session-workflows', 'session-browser', 'session-subagent',
    'projects-session', 'projects-session-files', 'projects-session-git',
    'projects-session-terminal', 'projects-session-artifacts',
    'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser', 'projects-session-subagent',
])
```
(`tabOf()` in the same file derives the tab from the route-name suffix generically — no other change there.)

- [ ] **Step 4: Command palette entry**

In `frontend/src/commands/staticCommands.js`, after the `nav.tab.workflows` command object (line 752-775):
```js
        {
            id: 'nav.tab.browser',
            label: 'Switch to Browser Tab',
            icon: 'globe',
            category: 'navigation',
            when: () => !!routeSessionId(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-browser' : 'session-browser'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
```

- [ ] **Step 5: Shortcut cheat sheet**

In `frontend/src/components/app/SettingsPopover.vue`, the "Session tabs" group (lines 258-264), first entry becomes:
```js
                { keys: ['Alt', 'Shift', '1–9, 0'], description: 'Jump to tab (Chat, Files, Git, Terminal, Tasks, Plan, Artifacts, Orchestration, Workflows, Browser)' },
```

- [ ] **Step 6: Frontend build + test check**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab/frontend && npx vite build --logLevel error && node --test src/utils/browserUrl.test.js
```
Expected: build completes without errors (catches template/syntax slips across all touched files) and the util tests still pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/router.js frontend/src/App.vue frontend/src/utils/scopeMemory.js frontend/src/commands/staticCommands.js frontend/src/components/app/SettingsPopover.vue
git commit -m "feat(browser-pane): routes, Alt+Shift+0 shortcut, scope memory, palette command, cheat sheet"
```

---

### Task 9: Docs sync

**Files:**
- Modify: `CLAUDE.md` (Project model bullet, Database Models section)
- Modify: `AGENTS.md` (mirror — condensed)

- [ ] **Step 1: CLAUDE.md**

In the `**Project**` bullet of *Database Models*, after the sentence about `default_layout_id`, append:
```
`default_browser_url` (nullable CharField) is the session Browser tab's default URL, inherited up the same chain with a workspace-level `browserUrl` (workspaces.json) fallback — resolved live by the pane, never materialized.
```

- [ ] **Step 2: AGENTS.md**

Mirror the same fact in AGENTS.md's condensed Project description (keep its terser style). Every CLAUDE.md change must propagate to AGENTS.md.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: document Project.default_browser_url and the Browser pane defaults chain"
```

---

### Task 10 (OPTIONAL, recommended): reachability / embeddability probe

The two silent-blank-frame cases — dev server down, and site refusing framing — are undetectable from the client. This small advisory endpoint HEAD-checks the URL server-side and the pane shows a warning banner. Skip this task entirely if you want the minimal feature; nothing else depends on it.

**Files:**
- Create: `src/twicc/browser_probe.py`
- Modify: `src/twicc/urls.py` (import + one path)
- Modify: `frontend/src/components/browser/BrowserPane.vue` (probe call + banner)

- [ ] **Step 1: Backend endpoint**

Create `src/twicc/browser_probe.py`:

```python
"""Advisory reachability / embeddability probe for the session Browser pane.

GET /api/browser-frame-check/?url=<http(s) URL>

The pane cannot observe a cross-origin iframe: a page that refuses framing
(X-Frame-Options / CSP frame-ancestors) and a dev server that is simply down
both render as a silent blank frame. This endpoint checks the URL server-side
and reports what the browser will not tell us. Advisory only — the iframe is
attempted regardless; a wrong verdict costs a dismissed banner, nothing else.

Reuses the artifact-broker primitives: DNS resolution + IP classification
(only the cloud metadata address is blocked — same invariant as the broker)
and IP pinning. Redirects are not followed (a redirect target would escape the
pin); a 3xx simply reports "reachable".
"""

import httpx
from django.http import JsonResponse

from twicc.artifacts.proxy import ResolutionError, resolve_target

PROBE_TIMEOUT_SECONDS = 10.0


def _frame_verdict(headers: httpx.Headers) -> tuple[bool, str | None]:
    """Best-effort: whether a cross-origin iframe would be allowed to render."""
    xfo = (headers.get("x-frame-options") or "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return False, f"X-Frame-Options: {xfo}"
    csp = headers.get("content-security-policy") or ""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.lower().startswith("frame-ancestors"):
            sources = [s.lower() for s in directive.split()[1:]]
            # Anything but a wildcard almost certainly excludes this TwiCC
            # origin — report the directive as-is (heuristic, advisory only).
            if "*" not in sources:
                return False, f"CSP {directive}"
    return True, None


def _host_header(url: httpx.URL) -> str:
    if url.port is None:
        return url.host
    return f"{url.host}:{url.port}"


async def _probe(client: httpx.AsyncClient, method: str, url: httpx.URL, pinned_ip: str) -> httpx.Response:
    """One pinned request; headers only — the body is never read."""
    pinned = url.copy_with(host=pinned_ip)
    request = client.build_request(method, pinned)
    request.headers["host"] = _host_header(url)
    request.extensions["sni_hostname"] = url.host
    response = await client.send(request, follow_redirects=False, stream=True)
    await response.aclose()
    return response


async def browser_frame_check(request):
    """GET /api/browser-frame-check/ — see module docstring."""
    if request.method != "GET":
        return JsonResponse({"error": "GET required"}, status=405)
    raw_url = request.GET.get("url") or ""
    try:
        url = httpx.URL(raw_url)
    except Exception:
        return JsonResponse({"error": "invalid url"}, status=400)
    if url.scheme not in ("http", "https") or not url.host:
        return JsonResponse({"error": "invalid url"}, status=400)
    port = url.port or (443 if url.scheme == "https" else 80)

    try:
        target = await resolve_target(url.host, port)
    except ResolutionError:
        return JsonResponse({"reachable": False, "reason": "hostname does not resolve"})
    except OSError:
        return JsonResponse({"reachable": False, "reason": "DNS lookup failed"})
    if target.kind == "metadata":
        return JsonResponse({"error": "blocked target"}, status=403)

    async with httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS)) as client:
        response = None
        for method in ("HEAD", "GET"):
            try:
                response = await _probe(client, method, url, target.ip)
            except httpx.HTTPError as exc:
                last_error = exc
                response = None
                continue
            if response.status_code != 405:  # some servers reject HEAD → retry as GET
                break
        if response is None:
            return JsonResponse({"reachable": False, "reason": type(last_error).__name__})

    embeddable, reason = _frame_verdict(response.headers)
    return JsonResponse(
        {"reachable": True, "status": response.status_code, "embeddable": embeddable, "reason": reason}
    )
```

- [ ] **Step 2: Register the URL**

In `src/twicc/urls.py`: add the import after the `artifact_proxy` import (line 4):
```python
from .browser_probe import browser_frame_check
```
and the path after `path("api/artifact-proxy/", artifact_proxy),` (line 40):
```python
    path("api/browser-frame-check/", browser_frame_check),
```

- [ ] **Step 3: Pane integration**

In `BrowserPane.vue`:

(a) Add state + probe function (after `onFrameLoad`):
```js
// ── Advisory probe: the two silent-blank-frame cases (server down, framing
// refused) are invisible client-side; ask the backend. Failures are ignored —
// the endpoint is advisory (and absent on builds without Task 10).
const probeResult = ref(null)

async function probeCurrentUrl() {
    probeResult.value = null
    const url = currentUrl.value
    if (!url) return
    const key = frameKey.value
    try {
        const response = await apiFetch(`/api/browser-frame-check/?url=${encodeURIComponent(url)}`)
        if (!response.ok) return
        const data = await response.json()
        if (frameKey.value !== key) return // user navigated meanwhile
        if (data.reachable === false || data.embeddable === false) probeResult.value = data
    } catch {
        // advisory only
    }
}
```

(b) Call it at the end of `showFrame()`:
```js
function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameKey.value++
    loading.value = true
    probeCurrentUrl()
}
```

(c) Banner in the template, next to the other `wa-callout`s:
```html
        <wa-callout v-if="probeResult" variant="warning" size="small" class="browser-banner">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            <template v-if="probeResult.reachable === false">
                The server did not respond ({{ probeResult.reason }}) — is it running?
            </template>
            <template v-else>
                This site refuses to be embedded ({{ probeResult.reason }}) — the frame
                below will likely stay blank. Use "Open in a new browser tab" instead.
            </template>
        </wa-callout>
```

- [ ] **Step 4: Lint + verify**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
uv run ruff check src/twicc/browser_probe.py src/twicc/urls.py
cd frontend && npx vite build --logLevel error
```

- [ ] **Step 5: Commit**

```bash
git add src/twicc/browser_probe.py src/twicc/urls.py frontend/src/components/browser/BrowserPane.vue
git commit -m "feat(browser-pane): advisory reachability/embeddability probe with warning banner"
```

---

### Task 11: CLI parity — `twicc update-project --default-browser-url`

Follows the `--worktree-directory` / `--unset-worktree-directory` pattern exactly (a nullable free-form string with a set/unset flag pair, routed through the `project:update` drop-request). No wiring change in `cli/__init__.py` — the flag lands on the existing `update_project_main` callback.

**Files:**
- Modify: `src/twicc/cli/update_project/command.py` (7 spots)
- Modify: `src/twicc/core/services/project_mutation.py:268-403` (`update_project_from_payload`)
- Modify: `src/twicc/projects.py:654-766` (`update_project_atomic`)
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-project/SKILL.md`
- Modify: `SKILLS-AND-CLI.md:196-202`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (version bump — see step 6)

- [ ] **Step 1: CLI command — flags, checks, payload**

In `src/twicc/cli/update_project/command.py`:

(a) Keep the field enumerations honest — three small wording touches:
- Module docstring (lines 16-17): the mutable list becomes `` `name`, `color`, `archived`, `default_provider`, `worktree_directory`, and `default_browser_url` are mutable ``.
- The Typer app `help` string (lines 61-65): `"…name, color, archived state, default provider, worktree directory, browser URL (flat flags), or its per-provider agent-settings defaults (`settings` sub-command)."`
- The callback one-line docstring (line 213): `"""Update an existing project's name, color, archived state, default provider, worktree directory, or browser URL."""`

(b) Add the two Typer options right after `unset_worktree_directory` (line 167), before `trust`:
```python
    default_browser_url: str | None = typer.Option(
        None,
        "--default-browser-url",
        help=(
            "Default URL the session Browser pane opens for this project "
            "(http(s) only — e.g. the project's dev server). Inherited by "
            "sub-projects and git worktrees; unset = inherit, then a "
            "containing workspace's browser URL. Mutually exclusive with "
            "`--unset-default-browser-url`."
        ),
    ),
    unset_default_browser_url: bool = typer.Option(
        False,
        "--unset-default-browser-url",
        help=(
            "Clear the project's Browser-pane URL (back to inherit). "
            "Mutually exclusive with `--default-browser-url`."
        ),
    ),
```

(c) In the sub-command conflict list (`passed`, lines 227-238), after the `--unset-worktree-directory` tuple:
```python
            ("--default-browser-url", default_browser_url is not None),
            ("--unset-default-browser-url", unset_default_browser_url),
```

(d) Same two tuples in the trust-conflict `field_flags` list (lines 295-303), after the `--unset-worktree-directory` tuple.

(e) Mutual-exclusion check, after the `worktree_directory` one (lines 337-340):
```python
    if default_browser_url is not None and unset_default_browser_url:
        errors.append(ValidationError("--default-browser-url", "conflicting_flags",
                                       "--default-browser-url and --unset-default-browser-url "
                                       "cannot be used together."))
```

(f) `has_patch` (lines 343-354): add the two terms
```python
        or default_browser_url is not None
        or unset_default_browser_url
```
and extend the `no_op` message (lines 356-360) with `--default-browser-url / --unset-default-browser-url`.

(g) Per-field validation, after the `worktree_directory` empty-check (lines 390-395). Extend the lazy import at line 261 to `from twicc.workspaces import validate_browser_url, validate_color`, then:
```python
    if default_browser_url is not None:
        trimmed_url = default_browser_url.strip()
        if not trimmed_url:
            errors.append(ValidationError(
                "--default-browser-url", "invalid_value",
                "--default-browser-url cannot be empty; use "
                "--unset-default-browser-url to clear it.",
            ))
        else:
            for e in validate_browser_url(trimmed_url, field="--default-browser-url"):
                errors.append(ValidationError(e.field, e.code, e.message))
```

(h) Payload dict (lines 416-429), after the `unset_worktree_directory` key:
```python
        "default_browser_url": (
            default_browser_url.strip() if default_browser_url is not None else None
        ),
        "unset_default_browser_url": unset_default_browser_url,
```

- [ ] **Step 2: Service glue — `update_project_from_payload`**

In `src/twicc/core/services/project_mutation.py`:

(a) Docstring payload shape (lines 273-285): add
```
            "default_browser_url": str | None,  # optional, Browser-pane default URL
            "unset_default_browser_url": bool,  # optional, back to inherit
```
and extend the mutually-exclusive list in the closing paragraph with ``\`default_browser_url\` vs \`unset_default_browser_url\```.

(b) Extraction + validation block, after the `worktree_directory` block (after line 363). Extend the import at line 50 to `from twicc.workspaces import normalize_browser_url, validate_browser_url, validate_color`, then:
```python
    default_browser_url = payload.get("default_browser_url")
    unset_default_browser_url = payload.get("unset_default_browser_url", False)
    if not isinstance(unset_default_browser_url, bool):
        return _invalid_payload_result("unset_default_browser_url",
                                       "unset_default_browser_url must be a boolean.")
    if default_browser_url is not None:
        if not isinstance(default_browser_url, str):
            return _invalid_payload_result("default_browser_url",
                                           "default_browser_url must be a string or null.")
        if unset_default_browser_url:
            return ProjectMutationResult(False, project_id, None, [
                ProjectMutationError("--default-browser-url", "conflicting_flags",
                                      "--default-browser-url and --unset-default-browser-url "
                                      "cannot be used together."),
            ])
        # Same normalization as the HTTP PUT: trimmed, empty means clear.
        default_browser_url = normalize_browser_url(default_browser_url)
        if default_browser_url is None:
            unset_default_browser_url = True
        else:
            url_errors = validate_browser_url(default_browser_url, field="--default-browser-url")
            if url_errors:
                return ProjectMutationResult(False, project_id, None, [
                    ProjectMutationError(e.field, e.code, e.message) for e in url_errors
                ])
```

(c) Pass through to the atomic call (lines 392-403), after `unset_worktree_directory=…`:
```python
        default_browser_url=default_browser_url,
        unset_default_browser_url=unset_default_browser_url,
```

- [ ] **Step 3: ORM write — `update_project_atomic`**

In `src/twicc/projects.py`:

(a) Signature (lines 654-666): add after `unset_worktree_directory: bool = False,`:
```python
    default_browser_url: str | None = None,
    unset_default_browser_url: bool = False,
```
and mention the new pair in the docstring's mutually-exclusive list (`` `default_browser_url` vs `unset_default_browser_url` ``); note it is assumed already validated as an http(s) URL, trimmed and non-empty.

(b) Write block, after the `worktree_directory` block (lines 731-738):
```python
            if unset_default_browser_url:
                if project.default_browser_url is not None:
                    project.default_browser_url = None
                    update_fields.append("default_browser_url")
            elif default_browser_url is not None:
                if project.default_browser_url != default_browser_url:
                    project.default_browser_url = default_browser_url
                    update_fields.append("default_browser_url")
```

- [ ] **Step 4: SKILL.md — twicc-update-project**

In `src/twicc/agent/plugin/twicc/skills/twicc-update-project/SKILL.md` (README rules: bullets not tables, one sentence per description):

(a) Frontmatter `argument-hint` (line 4): insert `[--default-browser-url X|--unset-default-browser-url]` after `[--worktree-directory X|--unset-worktree-directory]`.

(b) Lead paragraph (line 11): the mutable list becomes `` `name`, `color`, `archived`, `default_provider`, `worktree_directory`, `default_browser_url` ``.

(c) Options (after line 54, before `--timeout`):
```markdown
- `--default-browser-url URL` — Default URL the session Browser tab opens for this project (http(s) only, e.g. the dev server). Inherited by sub-projects and git worktrees. Mutually exclusive with `--unset-default-browser-url`.
- `--unset-default-browser-url` — Back to inherit (parent chain, then a containing workspace's browser URL). Mutually exclusive with `--default-browser-url`.
```

(d) Errors (line 92): extend the `invalid_value` line to
```markdown
- `invalid_value` — empty `--worktree-directory`, or empty/non-http(s) `--default-browser-url` (use the matching `--unset-*` flag to clear).
```

(e) Examples (after line 133):
```bash
$TWICC update-project . --default-browser-url http://localhost:3000
$TWICC update-project . --unset-default-browser-url
```

- [ ] **Step 5: SKILLS-AND-CLI.md**

In the `### twicc update-project <PROJECT>` section (lines 196-202): in the intro sentence add "browser URL" to the updatable list, and in the first bullet append after the `worktree_directory` sentence:
```markdown
`--default-browser-url URL` / `--unset-default-browser-url` set the default URL the session Browser tab opens for this project (http(s) only; inherited by sub-projects and git worktrees; unset = inherit, then a containing workspace's browser URL).
```

- [ ] **Step 6: Plugin version bump**

In `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: `"version": "0.53.2"` → `"version": "0.54.0"` (new flags in existing skills = minor; ONE bump covers Tasks 11 and 12 together — skip if Task 12 already did it).

- [ ] **Step 7: Verify + commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
uv run ruff check src/twicc/cli/update_project/command.py src/twicc/core/services/project_mutation.py src/twicc/projects.py
TWICC_DATA_DIR=$PWD uv run twicc update-project --help   # flags render, no Typer error
git add src/twicc/cli/update_project/command.py src/twicc/core/services/project_mutation.py src/twicc/projects.py src/twicc/agent/plugin/twicc/skills/twicc-update-project/SKILL.md SKILLS-AND-CLI.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
git commit -m "feat(browser-pane): update-project CLI flags for the default browser URL"
```
(Do not put the plugin version in the commit title.)

---

### Task 12: CLI parity — `twicc update-workspace` / `twicc create-workspace --browser-url`

Same drop-request pipeline as Task 11, workspace side (`workspace:update` / `workspace:create` → their `*_from_payload` glue → the atomic writers). The JSON key written is `browserUrl` (camelCase, like `projectIds`/`autoProjectPatterns`), matching Task 4's frontend field. `create-workspace` gets a plain `--browser-url` (no unset pair — omitting it at creation IS unset), mirroring the UI's create form which carries the field too.

**Files:**
- Modify: `src/twicc/cli/update_workspace.py`
- Modify: `src/twicc/cli/create_workspace.py`
- Modify: `src/twicc/core/services/workspace_mutation.py:90-212` (`create_workspace_from_payload`, `update_workspace_from_payload`)
- Modify: `src/twicc/workspaces.py:288-456` (`create_workspace_atomic`, `update_workspace_atomic`)
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md` and `twicc-create-workspace/SKILL.md`
- Modify: `SKILLS-AND-CLI.md:216-226`
- Modify: `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (version bump — skip if Task 11 already did it)

- [ ] **Step 1: CLI command**

In `src/twicc/cli/update_workspace.py`:

(a) Module docstring (lines 9-12): extend the mutually-exclusive pairs sentence — `` `--color` and `--unset-color` are mutually exclusive; so are `--browser-url` and `--unset-browser-url`, and `--archive` and `--unarchive` ``.

(b) Add options after `remove_patterns` (line 91), before `archive`:
```python
    browser_url: str | None = typer.Option(
        None,
        "--browser-url",
        help=(
            "Default URL the session Browser pane opens for projects of this "
            "workspace (http(s) only; a project's own Browser URL takes "
            "precedence). Mutually exclusive with `--unset-browser-url`."
        ),
    ),
    unset_browser_url: bool = typer.Option(
        False,
        "--unset-browser-url",
        help=(
            "Clear the workspace's Browser-pane URL. Mutually exclusive "
            "with `--browser-url`."
        ),
    ),
```

(c) Mutual-exclusion check, after the `--archive`/`--unarchive` one (lines 158-160):
```python
    if browser_url is not None and unset_browser_url:
        errors.append(ValidationError("--browser-url", "conflicting_flags",
                                       "--browser-url and --unset-browser-url cannot be used together."))
```

(d) `has_patch` (lines 163-173): add `or browser_url is not None or unset_browser_url`; extend the `no_op` message (lines 175-178) with `--browser-url / --unset-browser-url`.

(e) Per-field validation, after the `--add-pattern` loop (lines 207-209). Add `validate_browser_url` to the existing `from twicc.workspaces import (…)` lazy import (lines 141-146), then:
```python
    if browser_url is not None:
        trimmed_url = browser_url.strip()
        if not trimmed_url:
            errors.append(ValidationError(
                "--browser-url", "invalid_value",
                "--browser-url cannot be empty; use --unset-browser-url to clear it.",
            ))
        else:
            for e in validate_browser_url(trimmed_url, field="--browser-url"):
                errors.append(ValidationError(e.field, e.code, e.message))
```

(f) Payload (lines 234-244), after `"archived": archived_value,`:
```python
        "browser_url": browser_url.strip() if browser_url is not None else None,
        "unset_browser_url": unset_browser_url,
```

- [ ] **Step 2: Service glue — `update_workspace_from_payload`**

In `src/twicc/core/services/workspace_mutation.py`:

(a) Docstring payload shape: add
```
            "browser_url": str | null,             # optional (use unset_browser_url to clear)
            "unset_browser_url": bool,             # optional
```

(b) Extraction + validation, after the `archived` block. Add `normalize_browser_url` and `validate_browser_url` to the `from twicc.workspaces import (…)` block (line 33), then:
```python
    browser_url = payload.get("browser_url")
    unset_browser_url = payload.get("unset_browser_url", False)
    if not isinstance(unset_browser_url, bool):
        return _invalid_payload_result("unset_browser_url", "unset_browser_url must be a boolean.")
    if browser_url is not None:
        if not isinstance(browser_url, str):
            return _invalid_payload_result("browser_url", "browser_url must be a string or null.")
        if unset_browser_url:
            return WorkspaceMutationResult(False, workspace_id, None, [
                WorkspaceMutationError("--browser-url", "conflicting_flags",
                                       "--browser-url and --unset-browser-url cannot be used together."),
            ])
        # Trimmed, empty means clear, http(s) only — matches the UI dialogs.
        browser_url = normalize_browser_url(browser_url)
        if browser_url is None:
            unset_browser_url = True
        else:
            url_errors = validate_browser_url(browser_url, field="--browser-url")
            if url_errors:
                return WorkspaceMutationResult(False, workspace_id, None, url_errors)
```

(c) Pass through to the atomic call, after `archived=archived,`:
```python
        browser_url=browser_url,
        unset_browser_url=unset_browser_url,
```

- [ ] **Step 3: Atomic write — `update_workspace_atomic`**

In `src/twicc/workspaces.py`:

(a) Signature (lines 364-374): add after `archived: bool | None = None,`:
```python
    browser_url: str | None = None,
    unset_browser_url: bool = False,
```
and note the new mutually-exclusive pair in the docstring.

(b) Write block, after the `archived` write (`if archived is not None: …`):
```python
            if unset_browser_url:
                ws["browserUrl"] = None
            elif browser_url is not None:
                ws["browserUrl"] = browser_url
```

- [ ] **Step 4: create-workspace parity**

(a) In `src/twicc/cli/create_workspace.py`: add after the `--archived` option (which sits between `--add-pattern` and `--timeout`):
```python
    browser_url: str | None = typer.Option(
        None,
        "--browser-url",
        help=(
            "Default URL the session Browser pane opens for projects of this "
            "workspace (http(s) only; a project's own Browser URL takes "
            "precedence)."
        ),
    ),
```
Validation next to the existing `color_errs` (line 118) — add `validate_browser_url` to the `from twicc.workspaces import (…)` lazy import (line ~101):
```python
    browser_url = browser_url.strip() if browser_url is not None else None
    url_errs = validate_browser_url(browser_url or None, field="--browser-url")
```
and include `*url_errs` in the error-collection loop at line 123. Payload (line ~140), after `"archived": archived,`:
```python
        "browser_url": browser_url or None,
```

(b) In `src/twicc/core/services/workspace_mutation.py`, `create_workspace_from_payload` (line 90): add to the docstring payload shape `"browser_url": str | None,  # optional`, then after the `archived` extraction:
```python
    browser_url = payload.get("browser_url")
    if browser_url is not None and not isinstance(browser_url, str):
        return _invalid_payload_result("browser_url", "browser_url must be a string or null.")
    browser_url = normalize_browser_url(browser_url)
    url_errors = validate_browser_url(browser_url, field="--browser-url")
    if url_errors:
        return WorkspaceMutationResult(False, None, None, url_errors)
```
and pass `browser_url=browser_url,` to the `create_workspace_atomic` call.

(c) In `src/twicc/workspaces.py`, `create_workspace_atomic` (line 288): add `browser_url: str | None = None,` to the signature, and in the `ws` dict construction (after `"autoProjectPatterns": deduped_patterns,`):
```python
                "browserUrl": browser_url if browser_url else None,
```

(d) In `src/twicc/agent/plugin/twicc/skills/twicc-create-workspace/SKILL.md`: `argument-hint` (line 4) gains `[--browser-url X]`; Options (after the `--add-pattern` bullet, line 41):
```markdown
- `--browser-url URL` — Default URL the session Browser tab opens for projects of this workspace (http(s) only; a project's own Browser URL takes precedence).
```
Errors, Local list (after `project_not_found`, line 52):
```markdown
- `invalid_value` — non-http(s) `--browser-url`.
```

(e) In `SKILLS-AND-CLI.md`, the `### twicc create-workspace <NAME>` section (lines 216-220): add `--browser-url TEXT` to the flags bullet.

- [ ] **Step 5: SKILL.md — twicc-update-workspace**

In `src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md`:

(a) Frontmatter `argument-hint` (line 4): insert `[--browser-url X|--unset-browser-url]` before `[--archive|--unarchive]`.

(b) Options (after line 47, before `--archive`):
```markdown
- `--browser-url URL` — Default URL the session Browser tab opens for projects of this workspace (http(s) only; a project's own Browser URL takes precedence). Mutually exclusive with `--unset-browser-url`.
- `--unset-browser-url` — Clear the workspace's Browser-pane URL. Mutually exclusive with `--browser-url`.
```

(c) Errors, Local list: extend the `conflicting_flags` enumeration (line 56) with `--browser-url` + `--unset-browser-url`, and add after `project_not_found` (line 63):
```markdown
- `invalid_value` — empty or non-http(s) `--browser-url` (use `--unset-browser-url` to clear).
```

(d) Examples (after the last example line, line 100):
```bash
$TWICC update-workspace backend --browser-url http://localhost:3000
$TWICC update-workspace backend --unset-browser-url
```

- [ ] **Step 6: SKILLS-AND-CLI.md**

In the `### twicc update-workspace <WORKSPACE_ID>` section (lines 222-226), extend the flags bullet with:
```markdown
`--browser-url URL` / `--unset-browser-url` (default URL the session Browser tab opens for the workspace's projects; a project's own Browser URL wins).
```

- [ ] **Step 7: Plugin version bump** — `0.53.2` → `0.54.0` in `plugin.json` if Task 11 didn't already do it.

- [ ] **Step 8: Verify + commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
uv run ruff check src/twicc/cli/update_workspace.py src/twicc/cli/create_workspace.py src/twicc/core/services/workspace_mutation.py src/twicc/workspaces.py
TWICC_DATA_DIR=$PWD uv run twicc update-workspace --help
TWICC_DATA_DIR=$PWD uv run twicc create-workspace --help
git add src/twicc/cli/update_workspace.py src/twicc/cli/create_workspace.py src/twicc/core/services/workspace_mutation.py src/twicc/workspaces.py src/twicc/agent/plugin/twicc/skills/twicc-update-workspace/SKILL.md src/twicc/agent/plugin/twicc/skills/twicc-create-workspace/SKILL.md SKILLS-AND-CLI.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json
git commit -m "feat(browser-pane): workspace CLI flags for the workspace browser URL"
```

---

### Task 13: Per-session current-URL persistence

Restores the Browser tab's last URL across page reloads, mirroring the `Session.layout` pattern: a new mutable column, a PATCH branch, a `session_updated` broadcast, and a debounced write from the pane. The pane keeps its state in its own refs (never on the store's session object), so the wholesale-replace `session_updated` echo can't clobber anything — no `layoutPersistPending`-style guard is needed. Requires Tasks 1, 6, 7 (builds on their code); independent of 10-12.

**Files:**
- Modify: `src/twicc/core/models.py:460-466` (after `layout`)
- Create: `src/twicc/core/migrations/0122_session_browser_url.py`
- Modify: `src/twicc/core/serializers.py:141` (after `"layout"`)
- Modify: `src/twicc/core/services/session_update.py:548-561` (after `apply_session_layout_change`)
- Modify: `src/twicc/views.py:992-999` (PATCH branch, after `layout`)
- Modify: `frontend/src/components/browser/BrowserPane.vue` (hydrate + persist)
- Modify: `frontend/src/views/SessionView.vue` (pass `session-id`)
- Modify: `CLAUDE.md` + `AGENTS.md` (Session bullet)

- [ ] **Step 1: Model field + migration**

In `src/twicc/core/models.py`, directly after the `layout` field (line 466):
```python
    # Last URL the session's Browser pane was pointed at (user UI state, like
    # ``layout``): read once when the tab is first activated after a page
    # reload — it wins over the resolved project/workspace default. NULL =
    # never navigated. Persisted from the frontend via a debounced
    # ``PATCH /api/projects/<id>/sessions/<id>/``; http(s) only, enforced at
    # the endpoint.
    browser_url = models.CharField(max_length=2000, null=True, blank=True, default=None)
```

Create `src/twicc/core/migrations/0122_session_browser_url.py`:
```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0121_project_default_browser_url")]

    operations = [
        migrations.AddField(
            model_name="session",
            name="browser_url",
            field=models.CharField(blank=True, default=None, max_length=2000, null=True),
        ),
    ]
```

Verify: `TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --check --dry-run --settings=twicc.settings` → `No changes detected`.

- [ ] **Step 2: Serialize**

In `src/twicc/core/serializers.py`, `serialize_session()`, after the `"layout"` line (141):
```python
        "browser_url": session.browser_url,  # Browser-pane last URL (UI state; None = use the resolved default)
```

- [ ] **Step 3: Service helper**

In `src/twicc/core/services/session_update.py`, after `apply_session_layout_change` (line 561):
```python
async def apply_session_browser_url_change(session, browser_url: str | None) -> None:
    """Persist the Browser pane's last URL (validated by the PATCH endpoint)."""
    session.browser_url = browser_url
    await run_under_db_write_lock(
        lambda: session.asave(update_fields=["browser_url"])
    )
```

- [ ] **Step 4: PATCH branch**

In `src/twicc/views.py`, `session_detail()`, after the `layout` branch (line 999), before the `dismiss_goal` branch:
```python
        # Handle Browser-pane URL update: the last URL the session's Browser
        # tab was pointed at (UI state, restored on the tab's first activation
        # after a page reload). Persisted from the frontend via a debounced
        # PATCH on each toolbar navigation; null clears. Shares the combined
        # broadcast below.
        if "browser_url" in data:
            browser_url = data["browser_url"]
            if browser_url is not None and not isinstance(browser_url, str):
                return JsonResponse({"error": "browser_url must be a string or null"}, status=400)
            browser_url = normalize_browser_url(browser_url)
            url_errors = validate_browser_url(browser_url, field="browser_url")
            if url_errors:
                return JsonResponse({"error": url_errors[0].message}, status=400)
            from twicc.core.services.session_update import apply_session_browser_url_change
            await apply_session_browser_url_change(session, browser_url)
            needs_broadcast = True
```
(`normalize_browser_url` / `validate_browser_url` are already imported at the top of `views.py` by Task 1 Step 6.)

- [ ] **Step 5: Pane — hydrate once, persist debounced**

In `frontend/src/components/browser/BrowserPane.vue`:

(a) Add to the imports: `import { debounce } from '../../utils/debounce'`.

(b) Add the `sessionId` prop (first entry in `defineProps`):
```js
    sessionId: { type: String, default: null },
```

(c) Add below the navigation-state refs (after `everActivated`):
```js
// ── Per-session persistence: restore the last URL across page reloads.
// Read once at first activation (it wins over the defaults); written back
// debounced on each toolbar navigation. Drafts have no backend row — their
// URL stays transient. The pane state lives in the refs above, never on the
// store's session object, so the session_updated echo can't clobber it.
const BROWSER_URL_PERSIST_DEBOUNCE_MS = 1000
let lastPersistedUrl = null
const persistUrlDebounced = debounce(async () => {
    const sessionRow = store.getSession(props.sessionId)
    if (!sessionRow || sessionRow.draft) return
    const url = currentUrl.value
    if (url === lastPersistedUrl) return
    try {
        const response = await apiFetch(`/api/projects/${props.projectId}/sessions/${props.sessionId}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ browser_url: url }),
        })
        if (response.ok) lastPersistedUrl = url
    } catch {
        // Transient UI state — losing a write is acceptable.
    }
}, BROWSER_URL_PERSIST_DEBOUNCE_MS)
```

(d) In `showFrame()`, add `persistUrlDebounced()` as the last line (after `loading.value = true` — or after `probeCurrentUrl()` if Task 10 is in).

(e) Replace the first-activation watch body with:
```js
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        const saved = store.getSession(props.sessionId)?.browser_url || null
        lastPersistedUrl = saved
        const initial = saved || defaultUrl.value
        if (initial) navigate(initial)
    },
    { immediate: true }
)
```
(The hydration navigation is a no-op write: `showFrame` schedules a persist, but `url === lastPersistedUrl` short-circuits it.)

- [ ] **Step 6: Pass the session id**

In `frontend/src/views/SessionView.vue`, the `<BrowserPane>` block from Task 7 gains:
```html
                        :session-id="session.id"
```
(first prop, above `:project-id`).

- [ ] **Step 7: Docs**

In `CLAUDE.md`'s `**Session**` bullet, after the `layout` description, add: `` `browser_url` (nullable CharField) is the Browser tab's last URL, restored at first activation after a reload (wins over the project/workspace default); mutable + synced like `layout` ``. Mirror in `AGENTS.md`.

- [ ] **Step 8: Verify + commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
TWICC_DATA_DIR=$PWD uv run python -m django check --settings=twicc.settings
uv run ruff check src/twicc/core/models.py src/twicc/core/serializers.py src/twicc/core/services/session_update.py src/twicc/views.py
cd frontend && npx vite build --logLevel error
cd .. && git add src/twicc/core/models.py src/twicc/core/migrations/0122_session_browser_url.py src/twicc/core/serializers.py src/twicc/core/services/session_update.py src/twicc/views.py frontend/src/components/browser/BrowserPane.vue frontend/src/views/SessionView.vue CLAUDE.md AGENTS.md
git commit -m "feat(browser-pane): persist the session's last browser URL across reloads"
```

---

### Final verification (manual)

Start the worktree servers with the single devctl command (it applies the migration) and open the printed localhost URL:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab && uv run ./devctl.py start
```

- [ ] Open any session: **Browser** is the last tab, globe icon, after Workflows.
- [ ] `Alt+Shift+0` switches to it and focuses the address bar; `Alt+Shift+1` returns to Chat; `Alt+Shift+←/→` cycles through it; `Ctrl+K` → "Switch to Browser Tab" works.
- [ ] Type `localhost:<frontend port>` + Enter: the TwiCC frontend renders in the frame (self-embedding is a fine smoke test). Back/Forward enable correctly after a second navigation; Refresh reloads.
- [ ] Bookmark menu → save to project; reload the page (F5), reopen the tab: the URL auto-loads. Verify in the project edit dialog that the field shows it; clear it there; reload → blank state again.
- [ ] Save to a workspace; clear the project URL; reload → the workspace URL is used. Check `workspaces.json` carries `browserUrl`.
- [ ] On a worktree project's session with no own URL, the main repo's URL is inherited.
- [ ] Dock the Browser tab into a side region (placement arrow): the iframe reloads once (expected) and then survives tab switches within the dock.
- [ ] Switch to another tab and back: the iframe does NOT reload (v-show).
- [ ] Type `https://example.com` (sends no framing headers — renders) and `https://github.com` (refuses framing — blank frame; with Task 10, the banner explains it). Try a dead port (`localhost:9`): with Task 10, the "server did not respond" banner shows.
- [ ] Existing sessions with saved layouts open without errors (no layout migration needed).
- [ ] Settings popover → Shortcuts lists `Alt Shift 1–9, 0`.
- [ ] **(Task 13)** Navigate the Browser tab to some page, wait > 1 s (debounce), reload TwiCC (F5), reopen the tab: the same URL is restored (not the project default). On a draft session, the URL stays transient.
- [ ] **(Tasks 11-12, server running)** `$TWICC update-project . --default-browser-url http://localhost:3000` → `{"status":"updated",…}` and the pane's Home target changes; `--unset-default-browser-url` clears it. Same round-trip with `$TWICC update-workspace <id> --browser-url …` / `--unset-browser-url`, and `$TWICC create-workspace 'X' --browser-url …` seeds the field. Conflicting/empty/non-http values are rejected with `conflicting_flags` / `invalid_value`. `$TWICC project .` and `$TWICC workspace <id>` show the new fields (no code change — verify only).

Remind the user at the end: the **main** instance (not this worktree) will need `migrate` — devctl applies it automatically at the next backend (re)start; no manual step.

### Follow-ups (explicitly out of scope)

- CHANGELOG entry — only on explicit user request.
- A Browser tab on the PROJECT home (`ProjectDetailPanel`: Stats/Files/Git/Terminal) — same pane, project-scoped, no session URL persistence. The feature was asked for sessions; noted here because all the building blocks (BrowserPane, defaults chain, storage) would be reusable as-is.
