# Orchestration Tab Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the session Orchestration tab as a "Dashboard": header with the view switch and summary tiles, a sessions tree rooted at the current session with a "Spawned by" line, one card per node, and the subagent model.

**Architecture:** All tree and timeline logic lives in one pure, tested module (`utils/orchestrationView.js`). Three small components (summary tiles, time bar, annotations) are shared by the two node components. `OrchestrationPanel.vue` re-roots the existing topology payload on the current session (no topology backend change). The only backend change adds `model` to every entry of `serialize_agent_links`.

**Tech Stack:** Django 6 / pytest (backend), Vue 3 `<script setup>`, Web Awesome (`wa-*`), VueUse, node:test (frontend logic tests).

**Spec:** `docs/plans/2026-10-05-orchestration-tab-redesign-spec.md` (read it first; this plan implements it section by section).

## Global Constraints

- All code, comments, UI strings and docs in English (CLAUDE.md).
- No `<Teleport>` of iframes, no `router.js` import from utils/composables/stores (circular imports): `utils/orchestrationView.js` and `utils/sessionRoute.js` import nothing from components, stores or the router.
- Every Web Awesome component used must already be imported in `frontend/src/main.js` (`wa-popover`, `wa-icon`, `wa-button`, `wa-progress-ring`, `wa-callout`, `wa-spinner`, `wa-radio-group`/`wa-radio` are; verified).
- Firefox is the reference browser: only CSS features Firefox supports (container queries, `color-mix`, `:has` are fine).
- Never read `item.content` directly (n/a here); never `JSON.parse` session items.
- Cost figures only render when `settingsStore.areCostsShown` (D9). `null` cost shows the dash `CostDisplay` renders.
- Python: `orjson` for JSON (n/a), no new dependency. Run tests with `uv run pytest` in the main repo.
- Frontend tests: `cd frontend && node --test src/<file>.test.js` (single) or `npm test` (all).
- Never run `npm install`, `npm ci`, `migrate` or restart the dev servers (reserved to the user). No `CHANGELOG.md` edit unless the user asks.
- Commit format: `type(scope): lowercase summary`, a body, and the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Stage files explicitly (never a directory).

## Review Focus

Input classes the spec implies and the tests below pin (most likely to bite first):

1. The current session is absent from `topology.tree` (cut edge): `findSubtree` returns `null` and the panel shows "No orchestration data." — Task 3 test + Task 8 template.
2. `session.spawned_by` points to a session missing from `nodesById`: no "Spawned by" line, no crash — Task 3 (`parentOf`) test.
3. A node with no `created_at`/`startedAt`: no bar, excluded from the range — Task 3 test.
4. A server timestamp later than the client clock: the working bar still ends at the track end; a fully stopped tree has a fixed span — Task 3 tests.
5. Annotation keys with empty segments, `a` and `a.b` together, object values, `null`/boolean values, unsorted input — Task 3 tests.
6. A subagent whose session has no model, or a model with no family/version: no model row — Task 1 and Task 3 tests.
7. A collapsed branch must not change tiles, range or note: the panel computes them from the full subtree, never from DOM state — Task 8 (by construction; manual check).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/twicc/core/session_queries.py` | `model` in every agent entry. |
| `frontend/src/utils/agentLinkIndex.js` | Carry `model` onto the agent link entry. |
| `frontend/src/utils/orchestrationView.js` (new) | Pure logic: buckets, subtree lookup, parent lookup, timeline, annotations tree, model label. |
| `frontend/src/utils/sessionRoute.js` | `tab` option: route to a session's tool tab. |
| `frontend/src/components/ui/SegmentedControl.vue` | Label wrapped in `.segmented-label` (lets a parent make it icon-only). |
| `frontend/src/components/orchestration/OrchestrationSummary.vue` (new) | Four summary tiles + donut. |
| `frontend/src/components/orchestration/OrchestrationTimeBar.vue` (new) | The thin time bar. |
| `frontend/src/components/orchestration/OrchestrationAnnotations.vue` (new) | One-line tags + chevron + popover. |
| `frontend/src/components/orchestration/AnnotationTreeLevel.vue` (new) | One recursive level of the annotation tree. |
| `frontend/src/components/orchestration/treeNode.css` | Card + connector styles shared by both node components (rewritten). |
| `frontend/src/components/orchestration/OrchestrationNode.vue` | Session card. |
| `frontend/src/components/orchestration/AgentTreeNode.vue` | Subagent card with model row. |
| `frontend/src/components/orchestration/OrchestrationPanel.vue` | Header, tiles, re-rooting, spawned-by line, note, timers. |
| Tests | `tests/test_agent_run_snapshot.py`, `frontend/src/utils/{agentLinkIndex,orchestrationView,sessionRoute}.test.js`. |

---

### Task 1: Backend — `model` in every agent link entry

**Files:**
- Modify: `src/twicc/core/session_queries.py` (`serialize_agent_links`, around lines 304-360)
- Test: `tests/test_agent_run_snapshot.py`

**Interfaces:**
- Produces: every entry returned by `serialize_agent_links` / `build_subagents_state` has `"model"`: `{"raw", "family", "version"}` or `None`. Plain field, not tied to `include_metrics`. Frontend Task 2 reads it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_run_snapshot.py` (it already imports `entries`, `child`, `link`, `t`, `root`). Add `from twicc.providers.helpers import get_provider_helpers` to the imports at the top.

```python
# --- Subagent model ----------------------------------------------------------


def test_entry_carries_the_agents_model(root):
    subagent = child(root, "a1")
    subagent.model = "claude-opus-4-5-20251101"
    subagent.save(update_fields=["model"])
    link(root, "a1", "tu-spawn", line=1, started_at=t(0))
    expected = get_provider_helpers("claude_code").serialize_model("claude-opus-4-5-20251101")

    assert expected["family"] == "opus"
    # A plain field: present with and without the owner-only metrics.
    assert entries(root)["a1"]["model"] == expected
    assert entries(root, include_metrics=True)["a1"]["model"] == expected


def test_entry_model_is_none_when_the_agent_has_no_model(root):
    child(root, "a1")
    link(root, "a1", "tu-spawn", line=1, started_at=t(0))

    assert entries(root)["a1"]["model"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_agent_run_snapshot.py -k "agents_model or has_no_model" -q`
Expected: FAIL with `KeyError: 'model'`.

- [ ] **Step 3: Implement**

In `serialize_agent_links`, replace the `subagents = {...}` block and the unpacking so they read:

```python
    from twicc.core.agent_runs import UNKNOWN_STATE, serialize_runs
    from twicc.core.models import Session
    from twicc.providers.helpers import get_provider_helpers

    links = list(links)
    run_states = run_states or {}
    interactions = interactions or {}
    display_names = display_names or {}
    subagents = {
        row[0]: row[1:]
        for row in Session.objects.filter(id__in=[link.agent_id for link in links])
        .values_list(
            "id", "slug", "last_stopped_at", "total_cost", "user_message_count", "context_usage", "model", "provider",
        )
    }
    result = []
    for link in links:
        slug, agent_stopped, total_cost, turns, context_usage, model, provider = subagents.get(
            link.agent_id, (None, None, None, 0, None, None, None)
        )
```

And add this key to the `result.append({...})` dict, right after `"agent_slug": slug,`:

```python
            # The agent's last used model, as the session header shows it. A plain
            # field (not an owner-only metric): the Orchestration tab displays it.
            "model": get_provider_helpers(provider).serialize_model(model) if model and provider else None,
```

Also update the docstring sentence about `include_metrics` only if it claims the payload has no model (it does not; leave it).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_agent_run_snapshot.py tests/test_subagents_tree_endpoint.py tests/test_codex_subagent_links.py -q`
Expected: PASS (the codex tests call `serialize_agent_links([link])` and only read other keys).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/core/session_queries.py tests/test_agent_run_snapshot.py && git commit -m "feat(subagents): expose the agent model in agent link entries" -m "serialize_agent_links already reads each agent's Session row in one query. It now also reads model and provider and returns the model as {raw, family, version} on every entry, so the Orchestration tab can show which model a subagent last used." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Frontend cache — carry the agent model on the link entry

**Files:**
- Modify: `frontend/src/utils/agentLinkIndex.js` (`setAgentLink` preserve block ~line 29-34; `applyAgentSnapshot` entry ~line 179-191)
- Test: `frontend/src/utils/agentLinkIndex.test.js`

**Interfaces:**
- Consumes: Task 1's `model` field on snapshot entries (`agent.model`).
- Produces: `state.agentLinkIndex[agentId].model` (`{raw,family,version}` or `null`), also on `buildAgentTree` node `entry.model`. A live link event (no model) keeps the model a snapshot learned.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/utils/agentLinkIndex.test.js` (it already defines `api`, `snapshot`, `start`):

```js
test('applyAgentSnapshot carries the agent model and a live link event keeps it', () => {
    const state = agentLinkState()
    const model = { raw: 'claude-opus-4-5-20251101', family: 'opus', version: '4.5' }
    snapshot(state, [{ ...api(), model }])
    assert.deepEqual(state.agentLinkIndex.child.model, model)

    // A live event carries identity only: it must not erase what the snapshot learned.
    setAgentLink(state, 'launcher', 'spawn-child', { agentId: 'child', rootSessionId: 'root', startedAt: start })
    assert.deepEqual(state.agentLinkIndex.child.model, model)
})

test('a snapshot entry without a model stores null', () => {
    const state = agentLinkState()
    snapshot(state, [api()])
    assert.equal(state.agentLinkIndex.child.model, null)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/agentLinkIndex.test.js`
Expected: FAIL (`model` is `undefined`).

- [ ] **Step 3: Implement**

In `setAgentLink`, extend the preserve block:

```js
    // ``metrics`` (cost / turns / context), ``displayName`` and ``model`` only ever arrive
    // with a snapshot; a live event carries identity, so it must not erase them.
    if (prior?.agentId === entry.agentId) {
        if (entry.metrics === undefined) entry = { ...entry, metrics: prior.metrics }
        if (entry.displayName === undefined) entry = { ...entry, displayName: prior.displayName }
        if (entry.model === undefined) entry = { ...entry, model: prior.model }
    }
```

In `applyAgentSnapshot`, add after the `displayName` line of the entry literal:

```js
            // The agent's last used model ({raw, family, version}), as the session header shows it.
            model: agent.model ?? null,
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/agentLinkIndex.test.js`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/agentLinkIndex.js frontend/src/utils/agentLinkIndex.test.js && git commit -m "feat(subagents): keep the agent model on the link cache entry" -m "The /subagents/ snapshot now carries each agent's model. The link cache stores it on the entry and a live link event, which carries identity only, no longer erases it." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Pure logic module `orchestrationView.js`

**Files:**
- Create: `frontend/src/utils/orchestrationView.js`
- Test: `frontend/src/utils/orchestrationView.test.js`

**Interfaces:**
- Produces (all exported from `orchestrationView.js`, used by Tasks 6-9):
  - `BUCKET_ORDER: ['working','awaiting','idle','stopped']`
  - `BUCKET_COLORS: Record<bucket, string>` (CSS color values), `BUCKET_BORDER_COLORS` (same, stopped is faded)
  - `bucketOfProcessState(state: string|null|undefined): bucket`
  - `countBuckets(buckets: bucket[]): {working,awaiting,idle,stopped: number}`
  - `findSubtree(tree: {id,children}|null, id: string): node|null`
  - `flattenTree(node): node[]` (pre-order, node first; works for session trees and agent trees)
  - `parentOf(nodesById: Record<string,{session?:{spawned_by?:string}}>, sessionId: string): node|null`
  - `isoMs(iso: string|null|undefined): number|null`
  - `computeTimeline(items: {id,start:number|null,end:number|null,working:boolean}[], now: number): { range: {start,end,spanSeconds}|null, geometry: Record<id,{left,width,live}> }`
  - `annotationEntries(ann): [string, any][]` (sorted by key), `annotationValueText(value): string`, `buildAnnotationTree(ann): {name, values: string[], children: [...]}[]`
  - `agentModelLabel(model): string|null`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/orchestrationView.test.js`:

```js
import assert from 'node:assert/strict'
import test from 'node:test'

import {
    agentModelLabel, annotationEntries, annotationValueText, bucketOfProcessState, buildAnnotationTree,
    computeTimeline, countBuckets, findSubtree, flattenTree, isoMs, parentOf,
} from './orchestrationView.js'

const tree = {
    id: 'root',
    children: [
        { id: 'a', children: [{ id: 'a1', children: [] }, { id: 'a2', children: [] }] },
        { id: 'b', children: [] },
    ],
}

// ── buckets ─────────────────────────────────────────────────────────────────
test('process states fall in four buckets; starting counts as working', () => {
    assert.equal(bucketOfProcessState('starting'), 'working')
    assert.equal(bucketOfProcessState('assistant_turn'), 'working')
    assert.equal(bucketOfProcessState('awaiting_user_input'), 'awaiting')
    assert.equal(bucketOfProcessState('user_turn'), 'idle')
    assert.equal(bucketOfProcessState('dead'), 'stopped')
    assert.equal(bucketOfProcessState(undefined), 'stopped')
})

test('countBuckets always returns the four buckets', () => {
    assert.deepEqual(countBuckets(['working', 'idle', 'idle']), { working: 1, awaiting: 0, idle: 2, stopped: 0 })
    assert.deepEqual(countBuckets([]), { working: 0, awaiting: 0, idle: 0, stopped: 0 })
})

// ── tree helpers ────────────────────────────────────────────────────────────
test('findSubtree finds a node at any depth, null when absent or without a tree', () => {
    assert.equal(findSubtree(tree, 'a').id, 'a')
    assert.equal(findSubtree(tree, 'a2').id, 'a2')
    assert.equal(findSubtree(tree, 'root'), tree)
    assert.equal(findSubtree(tree, 'nope'), null)
    assert.equal(findSubtree(null, 'a'), null)
})

test('flattenTree lists the node first, then its descendants, depth first', () => {
    assert.deepEqual(flattenTree(tree).map(n => n.id), ['root', 'a', 'a1', 'a2', 'b'])
})

test('parentOf resolves the spawner, null when unknown, absent or missing from the payload', () => {
    const nodesById = { c: { id: 'c', session: { spawned_by: 'p' } }, p: { id: 'p', session: {} }, d: { id: 'd', session: { spawned_by: 'gone' } }, e: { id: 'e', session: {} } }
    assert.equal(parentOf(nodesById, 'c').id, 'p')
    assert.equal(parentOf(nodesById, 'd'), null)
    assert.equal(parentOf(nodesById, 'e'), null)
    assert.equal(parentOf(nodesById, 'missing'), null)
})

test('isoMs parses ISO strings and rejects empty or invalid input', () => {
    assert.equal(isoMs('2026-10-05T10:00:00Z'), Date.parse('2026-10-05T10:00:00Z'))
    assert.equal(isoMs(null), null)
    assert.equal(isoMs('not a date'), null)
})

// ── timeline ────────────────────────────────────────────────────────────────
const T = (minute) => Date.UTC(2026, 9, 5, 10, minute)

test('a stopped tree has a fixed range and a bar per node, scaled to the range', () => {
    const items = [
        { id: 'r', start: T(0), end: T(60), working: false },
        { id: 'c', start: T(30), end: T(45), working: false },
    ]
    const early = computeTimeline(items, T(90))
    const late = computeTimeline(items, T(500))
    assert.equal(early.range.spanSeconds, 3600)
    assert.deepEqual(late.range, early.range, 'now never enters a tree with no working node')
    assert.deepEqual(early.geometry.r, { left: 0, width: 100, live: false })
    assert.equal(early.geometry.c.left, 50)
    assert.equal(early.geometry.c.width, 25)
})

test('a working node ends at the range end, which includes now', () => {
    const items = [
        { id: 'r', start: T(0), end: T(10), working: false },
        { id: 'w', start: T(20), end: null, working: true },
    ]
    const { range, geometry } = computeTimeline(items, T(60))
    assert.equal(range.end, T(60))
    assert.equal(geometry.w.live, true)
    assert.ok(Math.abs(geometry.w.left + geometry.w.width - 100) < 1e-9, 'reaches the right end of the track')
})

test('a server timestamp later than the client clock still ends the working bar at the track end', () => {
    const items = [
        { id: 'r', start: T(0), end: T(90), working: false },
        { id: 'w', start: T(80), end: null, working: true },
    ]
    const { range, geometry } = computeTimeline(items, T(60))
    assert.equal(range.end, T(90))
    assert.ok(Math.abs(geometry.w.left + geometry.w.width - 100) < 1e-9)
})

test('a node with no start has no bar and is left out of the range', () => {
    const { range, geometry } = computeTimeline([
        { id: 'a', start: null, end: T(5), working: false },
        { id: 'b', start: T(10), end: T(20), working: false },
    ], T(30))
    assert.equal(geometry.a, undefined)
    assert.equal(range.start, T(10))
    assert.equal(range.spanSeconds, 600)
})

test('no node with a start gives no range', () => {
    assert.deepEqual(computeTimeline([{ id: 'a', start: null, end: null, working: false }], T(1)), { range: null, geometry: {} })
    assert.deepEqual(computeTimeline([], T(1)), { range: null, geometry: {} })
})

test('an end before the start is raised to the start and gets the minimum width', () => {
    const { geometry } = computeTimeline([
        { id: 'r', start: T(0), end: T(100), working: false },
        { id: 'x', start: T(50), end: T(10), working: false },
    ], T(200))
    assert.equal(geometry.x.width, 1.5)
    assert.equal(geometry.x.left, 50)
})

test('a zero range gives every node the full track', () => {
    const { geometry } = computeTimeline([{ id: 'a', start: T(5), end: T(5), working: false }], T(100))
    assert.deepEqual(geometry.a, { left: 0, width: 100, live: false })
})

test('the bar never leaves the track', () => {
    const { geometry } = computeTimeline([
        { id: 'r', start: T(0), end: T(100), working: false },
        { id: 'z', start: T(100), end: T(100), working: false },
    ], T(200))
    assert.ok(geometry.z.left + geometry.z.width <= 100 + 1e-9)
})

// ── annotations ─────────────────────────────────────────────────────────────
test('annotationEntries sorts by key', () => {
    assert.deepEqual(annotationEntries({ b: 1, a: 2, 'a.x': 3 }).map(([k]) => k), ['a', 'a.x', 'b'])
    assert.deepEqual(annotationEntries(null), [])
})

test('annotationValueText formats strings, numbers, booleans, null and objects', () => {
    assert.equal(annotationValueText('x'), 'x')
    assert.equal(annotationValueText(3), '3')
    assert.equal(annotationValueText(false), 'false')
    assert.equal(annotationValueText(null), 'null')
    assert.equal(annotationValueText([1, 'a']), '[1,"a"]')
})

test('dotted keys share parent levels, sorted by segment name', () => {
    const t = buildAnnotationTree({ 'team.name': 'core', 'team.lead': 'alice', 'run.limits.tokens': 200000, role: 'worker' })
    assert.deepEqual(t.map(n => n.name), ['role', 'run', 'team'])
    const team = t.find(n => n.name === 'team')
    assert.deepEqual(team.children.map(n => [n.name, n.values]), [['lead', ['alice']], ['name', ['core']]])
    const run = t.find(n => n.name === 'run')
    assert.equal(run.children[0].name, 'limits')
    assert.deepEqual(run.children[0].children[0], { name: 'tokens', values: ['200000'], children: [] })
})

test('a key with an empty segment is not split, and an object value there stays compact JSON', () => {
    const t = buildAnnotationTree({ 'a..b': 1, '.a': 2, 'a.': 3, 'c..d': { x: 1 } })
    assert.deepEqual(t.map(n => n.name).sort(), ['.a', 'a.', 'a..b', 'c..d'])
    assert.deepEqual(t.find(n => n.name === 'c..d'), { name: 'c..d', values: ['{"x":1}'], children: [] })
})

test('a scalar a and a.b merge into one level that shows both', () => {
    const [a] = buildAnnotationTree({ a: 'x', 'a.b': 'y' })
    assert.deepEqual(a.values, ['x'])
    assert.deepEqual(a.children.map(n => [n.name, n.values]), [['b', ['y']]])
})

test('an object value expands like dotted levels and merges with a dotted key', () => {
    const [a] = buildAnnotationTree({ a: { b: 1 }, 'a.b': 2 })
    assert.equal(a.name, 'a')
    assert.deepEqual(a.values, [])
    assert.deepEqual(a.children[0].values.sort(), ['1', '2'])
})

test('arrays and empty objects stay leaves as JSON text', () => {
    const t = buildAnnotationTree({ list: [1, 2], empty: {} })
    assert.deepEqual(t.map(n => [n.name, n.values]), [['empty', ['{}']], ['list', ['[1,2]']]])
})

// ── model label ─────────────────────────────────────────────────────────────
test('agentModelLabel reads family and version, as the session header does', () => {
    assert.equal(agentModelLabel({ raw: 'x', family: 'opus', version: '4.5' }), 'opus 4.5')
})

test('agentModelLabel is null without a model, or without family or version', () => {
    assert.equal(agentModelLabel(null), null)
    assert.equal(agentModelLabel({ raw: 'gpt-x', family: null, version: null }), null)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/orchestrationView.test.js`
Expected: FAIL (`Cannot find module './orchestrationView.js'`).

- [ ] **Step 3: Implement**

Create `frontend/src/utils/orchestrationView.js`:

```js
// Pure logic of the Orchestration tab (no Vue, no store, no router): state buckets, subtree and
// parent lookup, the time bar's range and geometry, the annotation tree, the subagent model label.
// Design: docs/plans/2026-10-05-orchestration-tab-redesign-spec.md.

// ── State buckets (spec 3.2) ────────────────────────────────────────────────
export const BUCKET_ORDER = ['working', 'awaiting', 'idle', 'stopped']

// Donut arcs and node borders. ``starting`` is in the working bucket, so its border is blue
// although its icon is warning-coloured (the one place the border does not follow the icon).
export const BUCKET_COLORS = {
    working: 'var(--wa-color-blue-60)',
    awaiting: 'var(--wa-color-warning-60)',
    idle: 'var(--wa-color-success-60)',
    stopped: 'var(--wa-color-neutral-50)',
}
// The card border: a stopped node is neutral at reduced opacity.
export const BUCKET_BORDER_COLORS = {
    ...BUCKET_COLORS,
    stopped: 'color-mix(in oklab, var(--wa-color-neutral-50) 40%, transparent)',
}

export function bucketOfProcessState(state) {
    switch (state) {
        case 'starting':
        case 'assistant_turn':
            return 'working'
        case 'awaiting_user_input':
            return 'awaiting'
        case 'user_turn':
            return 'idle'
        default:
            return 'stopped'
    }
}

export function countBuckets(buckets) {
    const counts = { working: 0, awaiting: 0, idle: 0, stopped: 0 }
    for (const bucket of buckets) counts[bucket] += 1
    return counts
}

// ── Tree helpers ────────────────────────────────────────────────────────────
/** The node ``id`` inside ``tree`` ({ id, children }), at any depth; null when absent. */
export function findSubtree(tree, id) {
    if (!tree) return null
    if (tree.id === id) return tree
    for (const child of tree.children ?? []) {
        const found = findSubtree(child, id)
        if (found) return found
    }
    return null
}

/** The node followed by its descendants, depth first. */
export function flattenTree(node) {
    return [node, ...(node.children ?? []).flatMap(flattenTree)]
}

/** The topology node of the session that spawned ``sessionId``; null when none or not in the payload. */
export function parentOf(nodesById, sessionId) {
    const parentId = nodesById[sessionId]?.session?.spawned_by
    return parentId ? (nodesById[parentId] ?? null) : null
}

export function isoMs(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : ms
}

// ── Time bar (spec 6) ───────────────────────────────────────────────────────
export const MIN_BAR_WIDTH_PERCENT = 1.5

/**
 * Range and per-node bar geometry of a tree.
 *
 * ``items``: ``{ id, start, end, working }`` with ``start``/``end`` in ms (``null`` when unknown).
 * A node with no start has no bar and stays out of the range. The range end is the latest of the
 * non-working ends, every start, and ``now`` when a node is working; a working node's end IS the
 * range end, so its bar reaches the right end of the track whatever the client clock says.
 */
export function computeTimeline(items, now) {
    const ranged = items.filter(item => item.start != null)
    if (!ranged.length) return { range: null, geometry: {} }
    let start = Infinity
    let end = -Infinity
    for (const item of ranged) {
        start = Math.min(start, item.start)
        end = Math.max(end, item.start)
        if (!item.working) end = Math.max(end, item.end ?? item.start)
    }
    if (ranged.some(item => item.working)) end = Math.max(end, now)
    const span = end - start
    const geometry = {}
    for (const item of ranged) {
        const itemEnd = item.working ? end : Math.max(item.end ?? item.start, item.start)
        let left = 0
        let width = 100
        if (span > 0) {
            width = Math.max(((itemEnd - item.start) / span) * 100, MIN_BAR_WIDTH_PERCENT)
            left = Math.min(((item.start - start) / span) * 100, 100 - width)
        }
        geometry[item.id] = { left, width, live: item.working }
    }
    return { range: { start, end, spanSeconds: span / 1000 }, geometry }
}

// ── Annotations (spec 5.6) ──────────────────────────────────────────────────
const byString = (a, b) => (a < b ? -1 : a > b ? 1 : 0)
const isPlainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)

/** Entries sorted by key (plain string order). */
export function annotationEntries(annotations) {
    if (!isPlainObject(annotations)) return []
    return Object.entries(annotations).sort(([a], [b]) => byString(a, b))
}

/** Tag/leaf text: strings as is, numbers and booleans as text, null as ``null``, objects and arrays as compact JSON. */
export function annotationValueText(value) {
    if (value === null) return 'null'
    if (typeof value === 'string') return value
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

/**
 * The popover's tree: keys split on ``.``, shared prefixes share a level, siblings sorted by name.
 * An object value expands like extra dotted levels. Several values on one path are all kept.
 * Returns ``{ name, values: string[], children }[]``.
 */
export function buildAnnotationTree(annotations) {
    const root = new Map()
    const ensure = (path) => {
        let level = root
        let node = null
        for (const name of path) {
            if (!level.has(name)) level.set(name, { name, values: [], children: new Map() })
            node = level.get(name)
            level = node.children
        }
        return node
    }
    // ``base`` is the path of the object the key belongs to ([] at the top level).
    const add = (base, key, value) => {
        const parts = key.split('.')
        if (parts.some(part => part === '')) {
            // Not split: the whole key is one leaf, and an object value there is compact JSON text.
            ensure([...base, key]).values.push(annotationValueText(value))
            return
        }
        const path = [...base, ...parts]
        if (isPlainObject(value) && Object.keys(value).length) {
            ensure(path)
            for (const [nestedKey, nested] of Object.entries(value).sort(([a], [b]) => byString(a, b))) {
                add(path, nestedKey, nested)
            }
        } else {
            ensure(path).values.push(annotationValueText(value))
        }
    }
    for (const [key, value] of annotationEntries(annotations)) add([], key, value)
    const finish = (level) => [...level.values()]
        .sort((a, b) => byString(a.name, b.name))
        .map(node => ({ name: node.name, values: node.values, children: finish(node.children) }))
    return finish(root)
}

// ── Subagent model (spec 5.4) ───────────────────────────────────────────────
/**
 * Label of a subagent's model (``{ raw, family, version }``), formatted as ``SessionHeader`` formats the
 * session's last used model. Null without a model, or without family or version.
 */
export function agentModelLabel(model) {
    if (!model?.family || !model?.version) return null
    return `${model.family} ${model.version}`
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/orchestrationView.test.js`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/orchestrationView.js frontend/src/utils/orchestrationView.test.js && git commit -m "feat(orchestration): add the pure view logic of the redesigned tab" -m "State buckets, subtree and parent lookup, the time bar range and geometry, the annotation tree and the subagent model label live in one tested module, with no Vue, store or router import." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Route to a session's tool tab (`sessionRoute` `tab` option)

**Files:**
- Modify: `frontend/src/utils/sessionRoute.js`
- Create: `frontend/src/utils/sessionRoute.test.js`

**Interfaces:**
- Produces: `sessionRouteLocation(target, route, { tab: 'orchestration' })` returns `{ name: 'session-orchestration' | 'projects-session-orchestration', params: { projectId, sessionId }, query? }`. Used by Task 8 for the "Spawned by" link.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/utils/sessionRoute.test.js`:

```js
import assert from 'node:assert/strict'
import test from 'node:test'

import { sessionRouteLocation } from './sessionRoute.js'

const target = { id: 's1', project_id: 'p-target' }

test('without options the base session route is kept', () => {
    const route = { name: 'session', params: { projectId: 'p-current' }, query: {} }
    assert.deepEqual(sessionRouteLocation(target, route), { name: 'session', params: { projectId: 'p-current', sessionId: 's1' } })
})

test('tab points to the tool tab of the session, in single-project mode', () => {
    const route = { name: 'session-files', params: { projectId: 'p-current' }, query: {} }
    assert.deepEqual(
        sessionRouteLocation(target, route, { tab: 'orchestration' }),
        { name: 'session-orchestration', params: { projectId: 'p-current', sessionId: 's1' } },
    )
})

test('tab in all-projects mode uses the target project and the projects- prefix', () => {
    const route = { name: 'projects-session', params: {}, query: {} }
    assert.deepEqual(
        sessionRouteLocation(target, route, { tab: 'orchestration' }),
        { name: 'projects-session-orchestration', params: { projectId: 'p-target', sessionId: 's1' } },
    )
})

test('the workspace query is carried with a tab', () => {
    const route = { name: 'session', params: { projectId: 'p' }, query: { workspace: 'w1' } }
    assert.deepEqual(sessionRouteLocation(target, route, { tab: 'orchestration' }).query, { workspace: 'w1' })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/sessionRoute.test.js`
Expected: FAIL (the tab tests get `name: 'session'`).

- [ ] **Step 3: Implement**

In `sessionRoute.js`: change the import to `import { buildSessionBaseRouteName, buildSubagentRouteName, buildTabRouteName } from './granularRoutes.js'` (with the `.js` extension: the new test runs under plain `node --test`, whose ESM loader rejects extensionless specifiers; Vite accepts both), update the JSDoc `@param` line to `{{ subagentId?: string, tab?: string }} [options] - append a subagent suffix, or point to a tool tab of the session (e.g. 'orchestration')`, and replace the `name` computation with:

```js
    const name = options.tab
        ? buildTabRouteName({ isAllProjectsMode: isAllProjects, isSessionRoute: true, tab: options.tab })
        : options.subagentId
            ? buildSubagentRouteName(isAllProjects)
            : buildSessionBaseRouteName(isAllProjects)
```

(`isAllProjects` is `route.name?.startsWith('projects-')`, a boolean or undefined: `buildTabRouteName` treats a falsy value as single-project.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/sessionRoute.test.js`
Expected: PASS. If `isAllProjects` being `undefined` makes `buildTabRouteName` misbehave, wrap it as `!!isAllProjects`.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/sessionRoute.js frontend/src/utils/sessionRoute.test.js && git commit -m "feat(routing): let sessionRouteLocation target a tool tab" -m "A new tab option builds the route of a session's tool tab (for example orchestration), keeping the project frame and the workspace query. The Orchestration tab uses it to link to the spawner's own Orchestration tab." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `SegmentedControl` — labelled span for an icon-only mode

**Files:**
- Modify: `frontend/src/components/ui/SegmentedControl.vue`

**Interfaces:**
- Produces: each option's label is rendered inside `<span class="segmented-label">`. Task 8's panel CSS makes it visually hidden (still read by assistive tech) under 420 px with `:deep(.segmented-label)`. No behaviour change otherwise. Other users (`AgentSettingsBenchmarkTask.vue`, the panel) keep looking the same.

- [ ] **Step 1: Implement**

In the template, add a tooltip to the radio (it stays readable when a parent hides the label) by changing the opening tag to
`<wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value" :title="option.label">`,
then replace the label text node:

```html
                <wa-icon v-if="option.icon" :name="option.icon" class="segmented-icon"></wa-icon>
                <span class="segmented-label">{{ option.label }}</span>
                <slot :name="`option-${option.value}`" :option="option"></slot>
```

No style change is needed: the span is inline like the text node was. (The tooltip repeats the visible label when the control is not compact; that is acceptable.)

- [ ] **Step 2: Update the test that pins the radio tag**

`frontend/src/styles/glide.test.js` (around line 314) asserts the exact opening tag with `assert.match(template, /<wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value">/)`. Change that regex so it accepts the tooltip: `/<wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value" :title="option.label">/`. Keep the rest of that test untouched.

- [ ] **Step 3: Verify nothing else breaks**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/styles/glide.test.js && npm test`
Expected: PASS (the frontend suite passes on the committed repo). Any failure comes from a change of this plan: fix it, do not skip it.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/ui/SegmentedControl.vue frontend/src/styles/glide.test.js && git commit -m "refactor(ui): wrap segmented control labels in a span" -m "The label was a bare text node, which CSS cannot hide. A span lets a parent show an icon-only control on narrow widths while keeping the label as the accessible name." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Shared components — time bar, annotations (+ tree level), summary tiles

**Files:**
- Create: `frontend/src/components/orchestration/OrchestrationTimeBar.vue`
- Create: `frontend/src/components/orchestration/AnnotationTreeLevel.vue`
- Create: `frontend/src/components/orchestration/OrchestrationAnnotations.vue`
- Create: `frontend/src/components/orchestration/OrchestrationSummary.vue`

**Interfaces:**
- Consumes: `orchestrationView.js` (Task 3).
- Produces:
  - `<OrchestrationTimeBar :geometry="{left,width,live}" :title="string|null" />`
  - `<OrchestrationAnnotations :annotations="object" />`
  - `<OrchestrationSummary kind="sessions|agents" :counts="{working,awaiting,idle,stopped}" :cost="number|null" :show-costs="boolean" :span-seconds="number|null" />`

No automated test: these are presentational (the repo has no component tests); the logic they use is tested in Task 3. They are checked in Task 9's manual pass.

- [ ] **Step 1: Create `OrchestrationTimeBar.vue`**

```vue
<script setup>
// The thin bar under a node's settings row: when the node ran, relative to the whole tree. One neutral
// brand colour for every node (never the state's); a working node fades out toward the right end of the
// track. The geometry comes from ``computeTimeline`` (utils/orchestrationView.js).
defineProps({
    geometry: { type: Object, required: true }, // { left, width, live } in percent
    title: { type: String, default: null },
})
</script>

<template>
    <div class="otime" :title="title">
        <i
            class="otime-fill"
            :class="{ 'is-live': geometry.live }"
            :style="{ left: `${geometry.left}%`, width: `${geometry.width}%` }"
        ></i>
    </div>
</template>

<style scoped>
.otime {
    position: relative;
    height: 0.4rem;
    border-radius: 999px;
    background: color-mix(in oklab, var(--wa-color-neutral-50) 14%, transparent);
}

.otime-fill {
    position: absolute;
    top: 0;
    bottom: 0;
    border-radius: 999px;
    background: var(--wa-color-brand-60);
    opacity: 0.85;
}

/* Static gradient, no motion: it reads "still going" and reaches the end of the track. */
.otime-fill.is-live {
    background: linear-gradient(90deg, var(--wa-color-brand-60), color-mix(in oklab, var(--wa-color-brand-60) 15%, transparent));
}
</style>
```

- [ ] **Step 2: Create `AnnotationTreeLevel.vue`**

```vue
<script setup>
// One level of the annotation tree shown in the popover (utils/orchestrationView.js buildAnnotationTree).
// Recursive through its own file name. A node with values shows ``name: value`` per value; a node
// without values is a level (chevron + name). Children nest below, with a thin connector line.
defineProps({
    nodes: { type: Array, required: true }, // [{ name, values: string[], children: [...] }]
})
</script>

<template>
    <ul class="atl">
        <li v-for="node in nodes" :key="node.name">
            <div v-if="!node.values.length" class="atl-level">
                <wa-icon auto-width name="chevron-down" class="atl-chevron"></wa-icon>
                {{ node.name }}
            </div>
            <div v-for="(value, index) in node.values" :key="index" class="atl-leaf">
                <span class="atl-key">{{ node.name }}:</span> <span class="atl-value">{{ value }}</span>
            </div>
            <AnnotationTreeLevel v-if="node.children.length" :nodes="node.children" />
        </li>
    </ul>
</template>

<style scoped>
.atl {
    list-style: none;
    margin: 0;
    padding: 0;
}

/* Nested levels: a thin guide line, like the node tree's connectors. */
.atl .atl {
    margin-left: 0.5rem;
    padding-left: 0.8rem;
    border-left: 1px solid var(--wa-color-neutral-border-normal);
}

.atl li {
    padding-block: var(--wa-space-3xs);
}

.atl-level {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-weight: 500;
    color: var(--wa-color-text-quiet);
}

.atl-chevron {
    font-size: 0.8em;
}

.atl-key {
    color: var(--wa-color-text-quiet);
}

.atl-value {
    font-weight: 500;
    overflow-wrap: anywhere;
}
</style>
```

- [ ] **Step 3: Create `OrchestrationAnnotations.vue`**

```vue
<script setup>
// A session's annotations (free-form key/value): ONE line of tags that never wraps (tags that do not
// fit are hidden), and a chevron button that is always there. The button opens a popover listing ALL
// annotations as a tree (dotted keys become levels). When tags are hidden, the button shows their count.
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import AnnotationTreeLevel from './AnnotationTreeLevel.vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { annotationEntries, annotationValueText, buildAnnotationTree } from '../../utils/orchestrationView'

const props = defineProps({
    annotations: { type: Object, required: true },
})

const uid = useId()
const buttonId = `${uid}-ann-button`

const entries = computed(() => annotationEntries(props.annotations))
const tree = computed(() => buildAnnotationTree(props.annotations))

// How many tags fit on the line: measured on the rendered row. Hidden tags stay in the layout
// (``visibility: hidden``) so the measurement is stable.
const tagsEl = ref(null)
const fitCount = ref(entries.value.length)
const hiddenCount = computed(() => entries.value.length - fitCount.value)

// Hysteresis: the count only changes when the row width does. If every tag would fit without the button but
// not with it, one tag can stay hidden until the next resize. Accepted.
function measure() {
    const el = tagsEl.value
    if (!el) return
    const limit = el.clientWidth
    let fit = 0
    for (const child of el.children) {
        if (child.offsetLeft + child.offsetWidth <= limit) fit += 1
        else break
    }
    fitCount.value = fit
}

onMounted(measure)
useResizeObserver(tagsEl, measure)
watch(entries, () => nextTick(measure))
</script>

<template>
    <div class="oann">
        <div ref="tagsEl" class="oann-tags">
            <span
                v-for="([key, value], index) in entries"
                :key="key"
                class="oann-tag"
                :class="{ 'is-hidden': index >= fitCount }"
            >
                <span class="oann-key">{{ key }}</span>
                <span class="oann-value">{{ annotationValueText(value) }}</span>
            </span>
        </div>
        <button :id="buttonId" type="button" class="oann-button" aria-label="Show all annotations">
            <span v-if="hiddenCount > 0" class="oann-count">+{{ hiddenCount }}</span>
            <wa-icon auto-width name="chevron-down"></wa-icon>
        </button>
        <wa-popover v-popover-focus-fix :for="buttonId" placement="bottom-start" class="oann-popover">
            <div class="oann-popover-body">
                <div class="oann-popover-title">Annotations</div>
                <AnnotationTreeLevel :nodes="tree" />
            </div>
        </wa-popover>
    </div>
</template>

<style scoped>
.oann {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
}

.oann-tags {
    position: relative;
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: nowrap;
    gap: var(--wa-space-2xs);
    overflow: hidden;
}

.oann-tag {
    flex: none;
    max-width: 100%;
    display: inline-flex;
    gap: var(--wa-space-2xs);
    padding: 0.24rem 0.5rem;
    border-radius: 999px;
    font-size: var(--wa-font-size-xs);
    line-height: 1;
    background: color-mix(in oklab, var(--wa-color-neutral-fill-quiet) 70%, transparent);
    border: 1px solid color-mix(in oklab, var(--wa-color-surface-border) 80%, transparent);
}

.oann-tag.is-hidden {
    visibility: hidden;
}

.oann-key {
    flex: none;
    font-weight: 500;
    color: var(--wa-color-text-quiet);
}

.oann-value {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.oann-button {
    flex: none;
    appearance: none;
    /* Web Awesome's native.css forces a form-control height and line-height on every <button>. */
    height: auto;
    line-height: 1;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    padding: 0.24rem 0.5rem;
    border-radius: 999px;
    font: inherit;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-on-quiet);
    background: var(--wa-color-brand-fill-quiet);
    border: 1px solid var(--wa-color-brand-border-quiet);
}

.oann-button:hover {
    background: var(--wa-color-brand-fill-normal);
}

.oann-popover {
    --max-width: min(24rem, 90vw);
}

.oann-popover-body {
    max-height: 50vh;
    overflow: auto;
    font-size: var(--wa-font-size-s);
}

.oann-popover-title {
    margin-bottom: var(--wa-space-2xs);
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}
</style>
```

- [ ] **Step 4: Create `OrchestrationSummary.vue`**

```vue
<script setup>
// The four summary tiles under the Orchestration header: count with a donut of the state buckets,
// working (sessions) or stopped (subagents), total cost, span. Counts and donut are over the displayed
// nodes (descendants only for sessions); cost and span are passed in already resolved (spec 3.2).
import { computed } from 'vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { formatDuration } from '../../utils/date'
import { BUCKET_COLORS, BUCKET_ORDER } from '../../utils/orchestrationView'

const props = defineProps({
    kind: { type: String, required: true, validator: (v) => ['sessions', 'agents'].includes(v) },
    counts: { type: Object, required: true }, // { working, awaiting, idle, stopped }
    cost: { type: Number, default: null },
    showCosts: { type: Boolean, default: true },
    spanSeconds: { type: Number, default: null },
})

const total = computed(() => BUCKET_ORDER.reduce((n, bucket) => n + props.counts[bucket], 0))
const running = computed(() => total.value - props.counts.stopped)

// Conic gradient: one arc per non-empty bucket; a neutral full ring with no node.
const donut = computed(() => {
    if (!total.value) return `conic-gradient(${BUCKET_COLORS.stopped} 0 100%)`
    let from = 0
    const stops = []
    for (const bucket of BUCKET_ORDER) {
        if (!props.counts[bucket]) continue
        const to = from + (props.counts[bucket] / total.value) * 100
        stops.push(`${BUCKET_COLORS[bucket]} ${from}% ${to}%`)
        from = to
    }
    return `conic-gradient(${stops.join(', ')})`
})

const countLabel = computed(() => (props.kind === 'agents' ? 'Subagents' : 'Spawned sessions'))
const span = computed(() => (props.spanSeconds == null ? '-' : formatDuration(props.spanSeconds)))
</script>

<template>
    <div class="osum">
        <div class="osum-tile osum-tile--donut">
            <span class="osum-donut" :style="{ background: donut }" aria-hidden="true"></span>
            <div class="osum-body">
                <span class="osum-label">{{ countLabel }}</span>
                <span class="osum-value">{{ total }}<small>{{ running }} running</small></span>
            </div>
        </div>
        <div v-if="kind === 'sessions'" class="osum-tile">
            <span class="osum-label">Working</span>
            <span class="osum-value osum-value--working">{{ counts.working }}<small>{{ counts.awaiting }} awaiting</small></span>
        </div>
        <div v-else class="osum-tile">
            <span class="osum-label">Stopped</span>
            <span class="osum-value">{{ counts.stopped }}</span>
        </div>
        <div v-if="showCosts" class="osum-tile">
            <span class="osum-label">Total cost</span>
            <span class="osum-value"><CostDisplay :cost="cost" /></span>
        </div>
        <div class="osum-tile">
            <span class="osum-label">Span</span>
            <span class="osum-value">{{ span }}</span>
        </div>
    </div>
</template>

<style scoped>
.osum {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
    gap: var(--wa-space-xs);
}

.osum-tile {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--wa-color-surface-border);
    background: color-mix(in oklab, var(--wa-color-surface-raised) 55%, transparent);
}

.osum-tile--donut {
    flex-direction: row;
    align-items: center;
    gap: var(--wa-space-s);
}

.osum-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    min-width: 0;
}

.osum-label {
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}

.osum-value {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xl);
    font-weight: 650;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
}

.osum-value small {
    font-size: var(--wa-font-size-xs);
    font-weight: 400;
    color: var(--wa-color-text-quiet);
}

.osum-value--working {
    color: var(--wa-color-blue-60);
}

.osum-donut {
    flex: none;
    width: 2.4rem;
    height: 2.4rem;
    border-radius: 50%;
    display: grid;
    place-items: center;
}

/* The hole: the page surface as an opaque colour (the cards' own surface token is transparent). */
.osum-donut::before {
    content: '';
    width: 64%;
    height: 64%;
    border-radius: 50%;
    background: var(--surface-solid);
}

/* Narrow pane: two columns and a smaller value, so the header stays short. */
@container orch (max-width: 480px) {
    .osum {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .osum-value {
        font-size: var(--wa-font-size-l);
    }
}
</style>
```

- [ ] **Step 5: Check the directive and helper paths exist**

Run: `cd /home/twidi/dev/twicc-poc/frontend/src && ls directives/vPopoverFocusFix.js utils/date.js && grep -n "export function formatDuration" utils/date.js`
Expected: both files listed, `formatDuration` found. If `vPopoverFocusFix` is a default export instead of a named one, adapt the import (the test file `directives/vPopoverFocusFix.test.js` imports `{ vPopoverFocusFix }`, so it is named).

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/orchestration/OrchestrationTimeBar.vue frontend/src/components/orchestration/AnnotationTreeLevel.vue frontend/src/components/orchestration/OrchestrationAnnotations.vue frontend/src/components/orchestration/OrchestrationSummary.vue && git commit -m "feat(orchestration): add the time bar, annotations and summary tiles" -m "Three small presentational components shared by the redesigned Orchestration tab: the neutral time bar with a live fade, one line of annotation tags with an always-present chevron opening a tree popover, and the four summary tiles with a state donut." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Card styles and the two node components

**Files:**
- Modify (rewrite): `frontend/src/components/orchestration/treeNode.css`
- Modify (rewrite): `frontend/src/components/orchestration/OrchestrationNode.vue`
- Modify (rewrite): `frontend/src/components/orchestration/AgentTreeNode.vue`

**Interfaces:**
- Consumes: Tasks 3 and 6. New prop on both node components: `timeline: { geometry: Record<id, {left,width,live}> }` (default `{ geometry: {} }`), passed down the recursion by the panel (Task 8). `OrchestrationNode` keeps `node`, `nodesById`, `currentSessionId`. `AgentTreeNode` keeps `node`, `sessionId`, `projectId`.
- Produces: the card markup both trees share (classes `onode`, `ocard`, `ocard-*`).

- [ ] **Step 1: Rewrite `treeNode.css`**

Replace the whole file with:

```css
/* Card + connector presentation shared by the Orchestration tab's two trees (``OrchestrationNode`` for
   spawned sessions, ``AgentTreeNode`` for subagents), imported with ``<style scoped src="./treeNode.css">``
   so each component keeps its own scope while the look is defined once — the two trees must not drift.
   Everything specific to one tree stays in that component's own scoped block.

   Connectors: a child's vertical line sits ``--orch-inset`` inside the parent card's left edge and runs
   down to the last child's elbow; each child has an elbow from that line to its card's left edge, at the
   middle of the title line. The collapse button no longer anchors anything. */
.onode {
    --orch-line-w: 2px;
    --orch-line-c: var(--wa-color-neutral-border-normal);
    --orch-inset: 0.8rem;
    --orch-elbow-w: 1rem;
    --orch-pad: var(--wa-space-2xs);
    /* Card top padding + half of the title line. */
    --orch-elbow-y: calc(var(--orch-pad) + var(--wa-space-xs) + 0.75rem);
    position: relative;
    padding-block: var(--orch-pad);
}

.onode-kids {
    margin-left: var(--orch-inset);
}

.onode-kids > .onode {
    padding-left: var(--orch-elbow-w);
}

.onode-kids > .onode::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: var(--orch-line-w);
    background: var(--orch-line-c);
    transform: translateX(-50%);
}

/* The last child stops the vertical line at its own elbow. */
.onode-kids > .onode:last-child::before {
    bottom: auto;
    height: var(--orch-elbow-y);
}

.onode-kids > .onode::after {
    content: '';
    position: absolute;
    left: 0;
    top: var(--orch-elbow-y);
    width: var(--orch-elbow-w);
    height: var(--orch-line-w);
    background: var(--orch-line-c);
    transform: translateY(-50%);
}

/* ── The card ───────────────────────────────────────────────────────────── */
.ocard {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border: 1px solid var(--wa-color-surface-border);
    /* The left border carries the state: its colour is the node's state bucket. */
    border-left: 4px solid var(--ocard-border, var(--wa-color-surface-border));
    background: color-mix(in oklab, var(--wa-color-surface-raised) 55%, transparent);
    transition: opacity 0.15s ease;
}

/* The current session (first card of the sessions tree): a brand outline, no badge. */
.ocard.is-current {
    box-shadow: 0 0 0 1px var(--wa-color-brand-border-quiet);
}

/* A hidden session: same content and colours, a card that reads as discreet. */
.ocard.is-hidden {
    opacity: 0.6;
}

.ocard.is-hidden:hover {
    opacity: 0.9;
}

.ocard-head {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.ocard-title {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    font-weight: 600;
    overflow-wrap: anywhere;
}

.orch-title-link {
    color: inherit;
    text-decoration: none;
    cursor: pointer;
}

.orch-title-link:hover {
    text-decoration: underline;
}

.orch-hidden-icon {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
}

.orch-status-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-m);
}

/* Pulse the awaiting-user hand, matching ProcessActivityIndicator's ``pending-pulse`` (1.5s, 1→0.3).
   The working robot is animated instead (global ``robot-working`` class, styles/robot-working.css). */
.orch-status-icon--pulse-pending {
    animation: orch-status-pulse-pending 1.5s ease-in-out infinite;
}

@keyframes orch-status-pulse-pending {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* The terminal of a shell the agent left running breathes like the unread eye (opacity only:
   kept under reduced motion). */
.orch-status-icon--pulse-shell {
    animation: motion-status-pulse-deep 2.4s ease-in-out infinite;
}

.ocard-toggle {
    appearance: none;
    /* Web Awesome's native.css forces a form-control height and line-height on every <button>. */
    height: auto;
    line-height: 1;
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    padding: var(--wa-space-3xs) var(--wa-space-2xs);
    border: 0;
    border-radius: var(--wa-border-radius-s);
    background: none;
    cursor: pointer;
    font: inherit;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.ocard-toggle:hover {
    background: var(--glass-item-hover);
    color: var(--wa-color-text-normal);
}

.ocard-settings {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

/* Dates, duration, turns and context on the left; the cost stays at the right, on the last line, at every width. */
.ocard-facts {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--wa-space-2xs) var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.ocard-facts-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-m);
}

.ocard-facts-main > span {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    white-space: nowrap;
}

.ocard-facts-main > .ocard-dates {
    /* The only fact allowed to wrap: two dates can exceed a 320 px card. */
    white-space: normal;
}

.ocard-arrow {
    opacity: 0.6;
}

.orch-cost {
    flex: none;
    display: inline-flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-normal);
}

.orch-cost-sub {
    display: inline-flex;
    align-items: baseline;
    gap: var(--wa-space-3xs);
    color: var(--wa-color-text-quiet);
    font-size: 0.9em;
}

/* Context window usage ring — same sizing/colors as the SessionHeader ring. */
/* Keep the ``onode-context-ring`` class name: styles/glow.css (dark track colour) and glow.test.js target it. */
.onode-context-ring {
    --size: 1.7rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-text-normal);
}
```

- [ ] **Step 2: Rewrite `OrchestrationNode.vue`**

Replace the whole file with:

```vue
<script setup>
// One session card of the Orchestration tree, rendered recursively. The card: title (link) + process-state
// icon, agent-settings summary + project, time bar, dates/duration/turns/context with the cost at the right,
// annotations. The left border carries the state bucket. Hidden sessions are dimmed and not linked.
// Non-hidden titles link to the session; the layout is shared with AgentTreeNode through treeNode.css.
// Self-references for recursion via filename.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import CostDisplay from '../ui/CostDisplay.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import AgentSettingsSummaryView from '../message/AgentSettingsSummaryView.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import OrchestrationAnnotations from './OrchestrationAnnotations.vue'
import OrchestrationTimeBar from './OrchestrationTimeBar.vue'
import { getProviderHelpers, getProviderStore } from '../../providers'
import { formatDate, formatDuration } from '../../utils/date'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { backgroundShellsRunningPhrase, userTurnBackgroundShellCount } from '../../utils/backgroundWork'
import { BUCKET_BORDER_COLORS, bucketOfProcessState, flattenTree } from '../../utils/orchestrationView'
import { useSettingsStore } from '../../stores/settings'

const settingsStore = useSettingsStore()
const route = useRoute()

// Honour the global "Show costs" toggle, like every other cost display in the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    // Id-only tree node: { id, children: [...] }
    node: { type: Object, required: true },
    // Map of session id -> full topology node (session, process, metrics)
    nodesById: { type: Object, required: true },
    // Id of the session the Orchestration tab belongs to (outlined).
    currentSessionId: { type: String, default: null },
    // { geometry: { [id]: { left, width, live } } } from computeTimeline.
    timeline: { type: Object, default: () => ({ geometry: {} }) },
})

// Process-state vocabulary from the topology payload. ``dead`` shows NO icon (a stopped session is the
// common, expected state). The label stays as the tooltip and accessible name; no text is drawn.
const PROCESS_STATUS = {
    starting:            { label: 'Starting',        icon: 'hourglass-start', color: 'var(--wa-color-warning-60)' },
    assistant_turn:      { label: 'Assistant turn',  icon: 'robot',           color: 'var(--wa-color-blue-60)',    pulse: 'work' },
    awaiting_user_input: { label: 'Awaiting input',  icon: 'hand',            color: 'var(--wa-color-warning-60)', pulse: 'pending' },
    user_turn:           { label: 'User turn',       icon: 'check',           color: 'var(--wa-color-success-60)' },
    dead:                { label: 'Stopped',         icon: null,              color: 'var(--wa-color-neutral-50)' },
}

const nodeData = computed(() => props.nodesById[props.node.id] ?? null)
const isCurrent = computed(() => props.node.id === props.currentSessionId)
const isHidden = computed(() => nodeData.value?.session?.hidden === true)

// Preserve the current frame (all-projects vs single-project prefix + project filter + workspace).
const sessionRoute = computed(() => sessionRouteLocation(
    { id: props.node.id, project_id: nodeData.value?.session?.project_id },
    route,
))

const projectId = computed(() => nodeData.value?.session?.project_id ?? null)
const title = computed(() => {
    const t = nodeData.value?.session?.title
    return (t && t.trim()) ? t : props.node.id.slice(0, 8)
})
const provider = computed(() => nodeData.value?.session?.provider ?? null)

// The agent-settings summary: unchanged. The model's version is always shown (derive "family-version"
// from the RESOLVED model so a bare "opus" reads "Opus 4.7").
const summaryParts = computed(() => {
    const helpers = provider.value ? getProviderHelpers(provider.value) : null
    if (!helpers) return []
    const s = nodeData.value.session
    const pStore = provider.value ? getProviderStore(provider.value) : null
    const m = s.model
    const modelForSummary = (m && m.family && m.version)
        ? `${m.family}-${m.version}`
        : (s.selected_model ?? null)
    const state = {
        selected: {
            selected_model: modelForSummary,
            permission_mode: s.permission_mode ?? null,
            effort: s.effort ?? null,
            thinking_enabled: s.thinking_enabled ?? null,
            claude_in_chrome: s.claude_in_chrome ?? null,
            fast_mode: s.fast_mode ?? null,
            context_max: s.context_max ?? null,
        },
        defaults: {
            selected_model: pStore?.defaultModel,
            permission_mode: pStore?.defaultPermissionMode,
            effort: pStore?.defaultEffort,
            thinking_enabled: pStore?.defaultThinking,
            claude_in_chrome: pStore?.defaultClaudeInChrome,
            fast_mode: pStore?.defaultFastMode,
            context_max: pStore?.defaultContextMax,
        },
    }
    return helpers.getSummaryParts(state) ?? []
})

const ownCost = computed(() => nodeData.value?.session?.total_cost ?? null)
const cumulativeCost = computed(() => nodeData.value?.subtree_total_cost ?? null)
const hasChildren = computed(() => (props.node.children?.length ?? 0) > 0)
const descendantCount = computed(() => flattenTree(props.node).length - 1)

const annotations = computed(() => nodeData.value?.session?.annotations ?? null)
const hasAnnotations = computed(() => {
    const a = annotations.value
    return !!a && typeof a === 'object' && Object.keys(a).length > 0
})

const processState = computed(() => nodeData.value?.process?.state ?? 'dead')
const bucket = computed(() => bucketOfProcessState(processState.value))
const isWorking = computed(() => bucket.value === 'working')
const borderColor = computed(() => BUCKET_BORDER_COLORS[bucket.value])

const status = computed(() => {
    const base = PROCESS_STATUS[processState.value] ?? PROCESS_STATUS.dead
    // A finished turn with a shell the agent left running: terminal icon, same green, breathing.
    const shells = userTurnBackgroundShellCount(nodeData.value?.process)
    if (shells) return { ...base, icon: 'terminal', pulse: 'shell', label: `${base.label} — ${backgroundShellsRunningPhrase(shells)}` }
    return base
})

// ── Dates (spec 5.1) ────────────────────────────────────────────────────────
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}
const startLabel = computed(() => fmtDate(nodeData.value?.session?.created_at))
// A working node ends "now"; otherwise the last assistant message synced is the "finished" proxy.
const endLabel = computed(() => (isWorking.value ? 'now' : fmtDate(nodeData.value?.session?.last_new_content_at)))
// No duration while working, nor without a positive span.
const durationLabel = computed(() => {
    if (isWorking.value) return null
    const c = nodeData.value?.session?.created_at
    const f = nodeData.value?.session?.last_new_content_at
    if (!c || !f) return null
    const sec = (Date.parse(f) - Date.parse(c)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})
const turnsLabel = computed(() => nodeData.value?.session?.user_message_count ?? null)

// ── Context window ring: same data and rules as the SessionHeader. ───────────
const providerHelpers = computed(() => (provider.value ? getProviderHelpers(provider.value) : null))
const contextMax = computed(() => {
    const s = nodeData.value?.session
    const helpers = providerHelpers.value
    if (!s || !helpers) return null
    return helpers.getEffectiveContextMax(s)
})
const contextUsagePercentage = computed(() => {
    const usage = nodeData.value?.session?.context_usage
    const max = contextMax.value
    if (usage == null || !max) return null
    return Math.round((usage / max) * 100)
})
const contextUsageTooltip = computed(() => {
    const max = contextMax.value
    if (max == null) return null
    const label = providerHelpers.value?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--glow-context-ring)'
})

const geometry = computed(() => props.timeline?.geometry?.[props.node.id] ?? null)
const barTitle = computed(() => (startLabel.value
    ? (endLabel.value ? `${startLabel.value} → ${endLabel.value}` : startLabel.value)
    : null))

// Expand/collapse this node's children (default expanded). Local: it changes nothing else on the page.
const expanded = ref(true)
</script>

<template>
    <div class="onode">
        <div
            class="ocard"
            :class="{ 'is-current': isCurrent, 'is-hidden': isHidden }"
            :style="{ '--ocard-border': borderColor }"
        >
            <div class="ocard-head">
                <span class="ocard-title">
                    <router-link v-if="!isHidden" :to="sessionRoute" class="orch-title-link">{{ title }}</router-link>
                    <span v-else>{{ title }}</span>
                    <wa-icon
                        v-if="isHidden"
                        name="eye-slash"
                        label="Hidden session"
                        title="Hidden session"
                        class="orch-hidden-icon"
                    ></wa-icon>
                    <wa-icon
                        v-if="status.icon"
                        :name="status.icon"
                        :style="{ color: status.color }"
                        :title="status.label"
                        :label="status.label"
                        class="orch-status-icon"
                        :class="status.pulse === 'work' ? 'robot-working' : (status.pulse ? `orch-status-icon--pulse-${status.pulse}` : null)"
                    ></wa-icon>
                </span>
                <button
                    v-if="hasChildren"
                    type="button"
                    class="ocard-toggle"
                    :aria-expanded="expanded"
                    :aria-label="expanded ? 'Collapse' : 'Expand'"
                    @click="expanded = !expanded"
                >
                    <span v-if="!expanded">{{ descendantCount }}</span>
                    <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </button>
            </div>

            <div class="ocard-settings">
                <AgentSettingsSummaryView :provider="provider" :parts="summaryParts" :mark-forced="false" />
                <ProjectBadge v-if="projectId" :project-id="projectId" :use-directory-for-unnamed="true" />
            </div>

            <OrchestrationTimeBar v-if="geometry" :geometry="geometry" :title="barTitle" />

            <div class="ocard-facts">
                <span class="ocard-facts-main">
                    <span v-if="startLabel" class="ocard-dates">
                        <wa-icon auto-width name="calendar" variant="regular"></wa-icon>
                        {{ startLabel }}
                        <template v-if="endLabel">
                            <wa-icon auto-width name="arrow-right" class="ocard-arrow"></wa-icon>
                            {{ endLabel }}
                        </template>
                    </span>
                    <span v-if="startLabel && durationLabel">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ durationLabel }}
                    </span>
                    <span v-if="turnsLabel">
                        <wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ turnsLabel }}
                    </span>
                    <span v-if="contextUsagePercentage != null">
                        <wa-progress-ring
                            :id="`orch-context-${node.id}`"
                            class="onode-context-ring"
                            :value="Math.min(contextUsagePercentage, 100)"
                            :style="{ '--indicator-color': contextUsageColor }"
                        ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                        <AppTooltip :for="`orch-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip>
                    </span>
                </span>
                <span v-if="showCosts" class="orch-cost">
                    <CostDisplay :cost="ownCost" />
                    <span v-if="hasChildren" class="orch-cost-sub" title="Cumulative cost including spawned children">
                        Σ <CostDisplay :cost="cumulativeCost" />
                    </span>
                </span>
            </div>

            <OrchestrationAnnotations v-if="hasAnnotations" :annotations="annotations" />
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <OrchestrationNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :nodes-by-id="nodesById"
                :current-session-id="currentSessionId"
                :timeline="timeline"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>

<style scoped>
/* The agent-settings summary reads italic and quiet, as it did on the loose node. */
.ocard-settings :deep(.agent-settings-summary) {
    font-style: italic;
}
</style>
```

- [ ] **Step 3: Rewrite `AgentTreeNode.vue`**

Replace the whole file with:

```vue
<script setup>
// One subagent card of the Orchestration tree, rendered recursively. Same card as OrchestrationNode
// (treeNode.css): title + working robot, the model the subagent last used, time bar, dates/duration/turns/
// context with the cost at the right. What an agent has no equivalent of — settings, project, annotations —
// is simply absent. A stopped subagent shows no icon; there is no ``background`` badge.
//
// The tree comes from the agent-link cache (``buildAgentTree``), kept live by the WS ``agent_link_created`` /
// ``agent_stopped`` events, so this view needs no polling of its own. Numbers come from the agent's own
// ``Session`` row when it is loaded (live), else from the ``metrics`` block / ``model`` of the ``/subagents/``
// snapshot — historical agents have no row in the store.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppTooltip from '../ui/AppTooltip.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import OrchestrationTimeBar from './OrchestrationTimeBar.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers } from '../../providers'
import { getAgentDisplay } from '../../utils/agentLabel'
import { agentCost, agentSubtreeCost } from '../../utils/agentTreeMetrics'
import { formatDate, formatDuration } from '../../utils/date'
import { agentModelLabel, BUCKET_BORDER_COLORS, flattenTree } from '../../utils/orchestrationView'
import { sessionRouteLocation } from '../../utils/sessionRoute'

const store = useDataStore()
const settingsStore = useSettingsStore()
const route = useRoute()

// Honour the global "Show costs" toggle, like the rest of the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    // Tree node from ``buildAgentTree``: { id, entry, children: [...] }
    node: { type: Object, required: true },
    // The session that owns the tree (the root of every agent route here).
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // { geometry: { [id]: { left, width, live } } } from computeTimeline.
    timeline: { type: Object, default: () => ({ geometry: {} }) },
})

const entry = computed(() => props.node.entry)
const hasChildren = computed(() => (props.node.children?.length ?? 0) > 0)
const descendantCount = computed(() => flattenTree(props.node).length - 1)

// The name the launcher gave this agent (see utils/agentLabel.js); ``Subagent "<short id>"`` when nothing
// named it — this tab says "subagent" throughout, to tell these apart from the sessions in the other tree.
const label = computed(() => {
    const { name, isFallback } = getAgentDisplay(props.node.id, store)
    return isFallback ? `Subagent "${name}"` : name
})

// A running agent carries a process state — real, or the synthetic one the agent-link cache maintains.
const isRunning = computed(() => !!store.getProcessState(props.node.id))
const borderColor = computed(() => BUCKET_BORDER_COLORS[isRunning.value ? 'working' : 'stopped'])

// Opening an agent goes through the regular subagent route (the one the in-chat "View Agent" button uses).
const agentRoute = computed(() => sessionRouteLocation(
    { id: props.sessionId, project_id: props.projectId },
    route,
    { subagentId: props.node.id },
))

// ── Model (spec 5.4): the subagent's own last used model ─────────────────────
const provider = computed(() => store.getSessionProvider(props.sessionId))
const modelLabel = computed(() => agentModelLabel(store.getSession(props.node.id)?.model ?? entry.value?.model))

// ── Numbers ─────────────────────────────────────────────────────────────────
const ownCost = computed(() => agentCost(store, props.node.id, entry.value?.metrics))
const cumulativeCost = computed(() => agentSubtreeCost(store, props.node))

const turnsLabel = computed(() => {
    const row = store.getSession(props.node.id)
    return row?.user_message_count ?? entry.value?.metrics?.userMessageCount ?? null
})

// Context window: an agent has no settings of its own — it runs inside its launcher's session, so the
// window is the root session's effective one. Usage is the agent's own.
const contextUsage = computed(() => {
    const row = store.getSession(props.node.id)
    return row?.context_usage ?? entry.value?.metrics?.contextUsage ?? null
})
const contextMax = computed(() => store.getEffectiveContextMax(props.sessionId))
const contextUsagePercentage = computed(() => {
    const usage = contextUsage.value
    const max = contextMax.value
    if (usage == null || !max) return null
    return Math.round((usage / max) * 100)
})
const contextUsageTooltip = computed(() => {
    const max = contextMax.value
    if (max == null) return null
    const helpers = provider.value ? getProviderHelpers(provider.value) : null
    const label = helpers?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--glow-context-ring)'
})

// ── Dates ───────────────────────────────────────────────────────────────────
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}

// The launch is the spawning tool_use's timestamp; the end is the persisted completion when there is one,
// else the agent's own last idle boundary. A running agent ends "now" and shows no duration.
const finishedAt = computed(() => entry.value?.stoppedAt ?? entry.value?.agentStoppedAt ?? null)
const startLabel = computed(() => fmtDate(entry.value?.startedAt))
const endLabel = computed(() => (isRunning.value ? 'now' : fmtDate(finishedAt.value)))
const durationLabel = computed(() => {
    const from = entry.value?.startedAt
    const to = finishedAt.value
    if (!from || !to || isRunning.value) return null
    const sec = (Date.parse(to) - Date.parse(from)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})

const geometry = computed(() => props.timeline?.geometry?.[props.node.id] ?? null)
const barTitle = computed(() => (startLabel.value
    ? (endLabel.value ? `${startLabel.value} → ${endLabel.value}` : startLabel.value)
    : null))

const expanded = ref(true)
</script>

<template>
    <div class="onode">
        <div class="ocard" :style="{ '--ocard-border': borderColor }">
            <div class="ocard-head">
                <span class="ocard-title">
                    <router-link :to="agentRoute" class="orch-title-link">{{ label }}</router-link>
                    <wa-icon
                        v-if="isRunning"
                        name="robot"
                        title="Working"
                        label="Working"
                        class="orch-status-icon robot-working"
                        style="color: var(--wa-color-blue-60)"
                    ></wa-icon>
                </span>
                <button
                    v-if="hasChildren"
                    type="button"
                    class="ocard-toggle"
                    :aria-expanded="expanded"
                    :aria-label="expanded ? 'Collapse' : 'Expand'"
                    @click="expanded = !expanded"
                >
                    <span v-if="!expanded">{{ descendantCount }}</span>
                    <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </button>
            </div>

            <div v-if="modelLabel" class="ocard-settings" title="Last used model">
                <span class="ocard-model">
                    <ProviderIcon :provider="provider" />
                    {{ modelLabel }}
                </span>
            </div>

            <OrchestrationTimeBar v-if="geometry" :geometry="geometry" :title="barTitle" />

            <div class="ocard-facts">
                <span class="ocard-facts-main">
                    <span v-if="startLabel" class="ocard-dates">
                        <wa-icon auto-width name="calendar" variant="regular"></wa-icon>
                        {{ startLabel }}
                        <template v-if="endLabel">
                            <wa-icon auto-width name="arrow-right" class="ocard-arrow"></wa-icon>
                            {{ endLabel }}
                        </template>
                    </span>
                    <span v-if="startLabel && durationLabel">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ durationLabel }}
                    </span>
                    <span v-if="turnsLabel">
                        <wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ turnsLabel }}
                    </span>
                    <span v-if="contextUsagePercentage != null">
                        <wa-progress-ring
                            :id="`atree-context-${node.id}`"
                            class="onode-context-ring"
                            :value="Math.min(contextUsagePercentage, 100)"
                            :style="{ '--indicator-color': contextUsageColor }"
                        ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                        <AppTooltip :for="`atree-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip>
                    </span>
                </span>
                <span v-if="showCosts" class="orch-cost">
                    <CostDisplay :cost="ownCost" />
                    <span v-if="hasChildren" class="orch-cost-sub" title="Cumulative cost including spawned agents">
                        Σ <CostDisplay :cost="cumulativeCost" />
                    </span>
                </span>
            </div>
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <AgentTreeNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :session-id="sessionId"
                :project-id="projectId"
                :timeline="timeline"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>

<style scoped>
.ocard-model {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-style: italic;
    text-transform: capitalize;
}
</style>
```

- [ ] **Step 4: Run the frontend tests**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: PASS. (Some CSS tests under `src/styles/` scan `src/` sources; if one fails on a pattern in the new files, fix the file to follow the rule it names — do not weaken the test.)

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/orchestration/treeNode.css frontend/src/components/orchestration/OrchestrationNode.vue frontend/src/components/orchestration/AgentTreeNode.vue && git commit -m "feat(orchestration): render nodes as cards with a state border and a time bar" -m "Both trees share one card look: title with the process-state icon only (none when stopped), the unchanged agent-settings summary or the subagent's model, a neutral time bar, dates with a single calendar icon, and the cost kept at the right on every width. Hidden sessions are dimmed; the current badge and the background badge are gone." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The panel — header, tiles, re-rooting, spawned-by line, note, timers

**Files:**
- Modify (rewrite): `frontend/src/components/orchestration/OrchestrationPanel.vue`

**Interfaces:**
- Consumes: Tasks 2, 3, 4, 5, 6, 7. Props unchanged (`sessionId`, `projectId`, `hasSpawnTree`, `active`), so `SessionView.vue` needs no change.

- [ ] **Step 1: Rewrite `OrchestrationPanel.vue`**

Replace the whole file with:

```vue
<script setup>
// Orchestration tab content. Two trees, one switch (shown only when both exist):
//
//   - "sessions": the sessions spawned BY this session (the topology re-rooted on it), with a
//     "Spawned by" line linking to the parent's own Orchestration tab;
//   - "agents": the subagents this session launched, at any depth, live off the agent-link cache
//     (no fetch, poll or error state of its own). Called "subagent" throughout the UI, never just "agent".
//
// Sessions data: ``GET /api/projects/<pid>/sessions/<sid>/topology/`` returns the WHOLE spawn tree rooted
// at its top-level ancestor; this panel finds the current session's subtree in it (the payload is
// unchanged). While the tab is open the topology is polled every 15s, but only as long as at least one
// node of the payload is live (any process state other than ``dead``); the tab also force-fetches once on
// every (re)activation. Polling is a stop-gap until the tree is pushed over the WebSocket.
//
// The time bars share one range computed here (``computeTimeline``); ``now`` is refreshed on each load and
// by a 30s timer that runs only while the tab is active and a displayed node is working.
import { ref, computed, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import OrchestrationNode from './OrchestrationNode.vue'
import AgentTreeNode from './AgentTreeNode.vue'
import OrchestrationSummary from './OrchestrationSummary.vue'
import OrchestrationTabActivity from './OrchestrationTabActivity.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { agentForestCost } from '../../utils/agentTreeMetrics'
import {
    bucketOfProcessState, computeTimeline, countBuckets, findSubtree, flattenTree, isoMs, parentOf,
} from '../../utils/orchestrationView'
import { sessionRouteLocation } from '../../utils/sessionRoute'

const store = useDataStore()
const settingsStore = useSettingsStore()
const route = useRoute()
// Honour the global "Show costs" toggle, like the rest of the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // Whether the session belongs to a spawned-session tree (``spawn_root``). The tab can also be here for
    // subagents alone, in which case there is no topology to fetch and the sessions view is not offered.
    hasSpawnTree: { type: Boolean, default: false },
    active: { type: Boolean, default: false },
})

// ── The two views ───────────────────────────────────────────────────────────
const hasAgents = computed(() => store.hasSubagents(props.sessionId))
const agentTree = computed(() => store.getAgentTree(props.sessionId))
// The tab label's indicators, repeated on the view switch's inactive segment.
const orchestrationActivity = computed(() => store.getOrchestrationActivity(props.sessionId))
const canSwitchView = computed(() => props.hasSpawnTree && hasAgents.value)
const VIEW_OPTIONS = [
    { value: 'sessions', label: 'Sessions', icon: 'diagram-project' },
    { value: 'agents', label: 'Subagents', icon: 'robot' },
]
// User choice, only honoured when both views exist; otherwise the available one wins.
const selectedView = ref('sessions')
const view = computed(() => {
    if (canSwitchView.value) return selectedView.value
    return props.hasSpawnTree ? 'sessions' : 'agents'
})

const loading = ref(false)
const error = ref(null)
const topology = ref(null)
// Reference clock of the working nodes' bars (ms).
const now = ref(Date.now())

const AUTO_REFRESH_INTERVAL = 15000
const NOW_INTERVAL = 30000
let autoTimer = null
let nowTimer = null
// In-flight request controller, so a newer load can abort a still-pending one and always win.
let inFlightController = null

// ── Sessions view: the topology re-rooted on the current session ────────────
const nodesById = computed(() => {
    const map = {}
    for (const node of topology.value?.nodes ?? []) map[node.id] = node
    return map
})
// ``null`` when the current session is not in the payload (a corrupt spawn edge): "No orchestration data."
const subtree = computed(() => {
    const tree = topology.value?.tree
    return tree && nodesById.value[props.sessionId] ? findSubtree(tree, props.sessionId) : null
})
const currentNode = computed(() => nodesById.value[props.sessionId] ?? null)
const sessionNodes = computed(() => (subtree.value ? flattenTree(subtree.value) : []))
const stateBucketOf = (id) => bucketOfProcessState(nodesById.value[id]?.process?.state ?? 'dead')
// Tiles count the DESCENDANTS (the current session is the first card, not a spawned session).
const sessionCounts = computed(() => countBuckets(sessionNodes.value.slice(1).map(n => stateBucketOf(n.id))))
// Cost of the current session and everything below it (equals the root card's Σ).
const sessionsCost = computed(() => currentNode.value?.subtree_total_cost ?? null)

// "Spawned by": the direct parent only. A hidden parent cannot be opened (plain text + crossed-out eye).
const parentNode = computed(() => parentOf(nodesById.value, props.sessionId))
const parentTitle = computed(() => {
    const t = parentNode.value?.session?.title
    return (t && t.trim()) ? t : (parentNode.value?.id ?? '').slice(0, 8)
})
const parentHidden = computed(() => parentNode.value?.session?.hidden === true)
const parentRoute = computed(() => (parentNode.value && !parentHidden.value
    ? sessionRouteLocation(
        { id: parentNode.value.id, project_id: parentNode.value.session.project_id },
        route,
        { tab: 'orchestration' },
    )
    : null))
// The note explains the crossed-out eye: shown iff a hidden session is displayed (parent or any card).
const showHiddenNote = computed(() => parentHidden.value
    || sessionNodes.value.some(n => nodesById.value[n.id]?.session?.hidden === true))

// ── Agents view ─────────────────────────────────────────────────────────────
const agentNodes = computed(() => agentTree.value.flatMap(flattenTree))
const agentIsRunning = (id) => !!store.getProcessState(id)
const agentCounts = computed(() => countBuckets(agentNodes.value.map(n => (agentIsRunning(n.id) ? 'working' : 'stopped'))))
const agentTotalCost = computed(() => agentForestCost(store, agentTree.value))

// ── Time bars: one range per view, over every displayed node ────────────────
const timelineItems = computed(() => (view.value === 'agents'
    ? agentNodes.value.map(n => ({
        id: n.id,
        start: isoMs(n.entry?.startedAt),
        end: isoMs(n.entry?.stoppedAt ?? n.entry?.agentStoppedAt),
        working: agentIsRunning(n.id),
    }))
    : sessionNodes.value.map(n => {
        const node = nodesById.value[n.id]
        return {
            id: n.id,
            start: isoMs(node?.session?.created_at),
            end: isoMs(node?.session?.last_new_content_at),
            working: stateBucketOf(n.id) === 'working',
        }
    })))
const timeline = computed(() => computeTimeline(timelineItems.value, now.value))
const hasWorkingNode = computed(() => timelineItems.value.some(item => item.working))

// The header's tiles. ``null`` while there is nothing to summarise (loading, error, no data).
const summary = computed(() => {
    const spanSeconds = timeline.value.range?.spanSeconds ?? null
    if (view.value === 'agents') {
        return { kind: 'agents', counts: agentCounts.value, cost: agentTotalCost.value, spanSeconds }
    }
    if (!subtree.value) return null
    return { kind: 'sessions', counts: sessionCounts.value, cost: sessionsCost.value, spanSeconds }
})

// Auto-refresh gate: the poll runs while at least one node of the WHOLE payload is not ``dead`` (a live
// ancestor or sibling keeps the parent line and the payload fresh), whatever is displayed.
const hasLiveNode = computed(() =>
    (topology.value?.nodes ?? []).some(n => (n.process?.state ?? 'dead') !== 'dead'),
)

// ``silent`` ticks (background polls) never touch ``loading`` and keep the last good snapshot on failure,
// so the tree never flashes a spinner or error banner under the user.
async function load({ silent = false } = {}) {
    if (!props.projectId || !props.sessionId) return
    if (!props.hasSpawnTree) return  // no spawned session: nothing to fetch
    if (inFlightController) inFlightController.abort()
    const controller = new AbortController()
    inFlightController = controller
    if (!silent) loading.value = true
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}/sessions/${encodeURIComponent(props.sessionId)}/topology/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) {
            throw new Error(`Failed to load topology: ${response.status}`)
        }
        topology.value = await response.json()
        now.value = Date.now()
        error.value = null
    } catch (e) {
        if (e.name === 'AbortError') return // superseded by a newer load
        console.error('Failed to load orchestration topology:', e)
        if (!silent || !topology.value) {
            error.value = 'Failed to load the orchestration topology.'
        }
    } finally {
        if (inFlightController === controller) inFlightController = null
        if (!silent) loading.value = false
    }
}

function stopAuto() {
    if (autoTimer !== null) {
        clearInterval(autoTimer)
        autoTimer = null
    }
}
function syncAuto() {
    const shouldRun = props.active && hasLiveNode.value
    if (shouldRun && autoTimer === null) {
        autoTimer = setInterval(() => load({ silent: true }), AUTO_REFRESH_INTERVAL)
    } else if (!shouldRun) {
        stopAuto()
    }
}
watch([() => props.active, hasLiveNode], syncAuto, { immediate: true })

function stopNow() {
    if (nowTimer !== null) {
        clearInterval(nowTimer)
        nowTimer = null
    }
}
// The ``now`` timer: only while the tab is active and a displayed node is working.
function syncNow() {
    const shouldRun = props.active && hasWorkingNode.value
    if (shouldRun && nowTimer === null) {
        now.value = Date.now()
        nowTimer = setInterval(() => { now.value = Date.now() }, NOW_INTERVAL)
    } else if (!shouldRun) {
        stopNow()
    }
}
watch([() => props.active, hasWorkingNode], syncNow, { immediate: true })

// Refreshing the agent view re-reads the ``/subagents/`` snapshot: the tree itself is live over the
// WebSocket, but the per-agent numbers it carries (cost, turns, context, model) only move with a read.
const agentsLoading = ref(false)
const refreshing = computed(() => (view.value === 'agents' ? agentsLoading.value : loading.value))
async function refreshAgents() {
    agentsLoading.value = true
    try {
        await store.fetchSubagentsState(props.projectId, props.sessionId)
    } finally {
        agentsLoading.value = false
    }
}
function refresh() {
    return view.value === 'agents' ? refreshAgents() : load()
}

// Force a fresh read every time the tab becomes active, regardless of the poll condition.
watch(
    () => props.active,
    (active) => {
        if (!active) return
        load()
        if (hasAgents.value) refreshAgents()
    },
    { immediate: true },
)

onUnmounted(() => {
    stopAuto()
    stopNow()
    if (inFlightController) inFlightController.abort()
})
</script>

<template>
    <div class="orchestration-panel">
        <div class="orch-frame">
            <div class="orch-header">
                <div class="orch-toolbar">
                    <SegmentedControl
                        v-if="canSwitchView"
                        class="orch-view-switch"
                        label="Tree to show"
                        :model-value="view"
                        :options="VIEW_OPTIONS"
                        @update:model-value="selectedView = $event"
                    >
                        <!-- Only the view NOT shown: the other category, when it is busy. -->
                        <template #option-sessions>
                            <OrchestrationTabActivity v-if="view !== 'sessions'" class="orch-switch-activity" only="sessions" :activity="orchestrationActivity" />
                        </template>
                        <template #option-agents>
                            <OrchestrationTabActivity v-if="view !== 'agents'" class="orch-switch-activity" only="subagents" :activity="orchestrationActivity" />
                        </template>
                    </SegmentedControl>
                    <span v-else class="orch-mode-tag">
                        <wa-icon :name="view === 'agents' ? 'robot' : 'diagram-project'"></wa-icon>
                        {{ view === 'agents' ? 'Subagents' : 'Sessions' }}
                    </span>
                    <wa-button
                        size="small"
                        appearance="plain"
                        title="Refresh"
                        :loading="refreshing"
                        :disabled="refreshing"
                        @click="refresh()"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        <span class="orch-refresh-label">Refresh</span>
                    </wa-button>
                </div>
                <OrchestrationSummary v-if="summary" v-bind="summary" :show-costs="showCosts" />
            </div>

            <div class="orch-content">
                <template v-if="view === 'agents'">
                    <div v-if="agentNodes.length" class="orch-tree">
                        <AgentTreeNode
                            v-for="node in agentTree"
                            :key="node.id"
                            :node="node"
                            :session-id="sessionId"
                            :project-id="projectId"
                            :timeline="timeline"
                        />
                    </div>
                    <div v-else class="orch-state orch-state-empty">
                        <wa-icon name="robot"></wa-icon>
                        <span>No subagent.</span>
                    </div>
                </template>
                <template v-else>
                    <div v-if="loading && !topology" class="orch-state">
                        <wa-spinner></wa-spinner>
                        <span>Loading topology…</span>
                    </div>
                    <wa-callout v-else-if="error" variant="danger" size="small">
                        <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                        {{ error }}
                    </wa-callout>
                    <template v-else-if="subtree">
                        <div v-if="parentNode" class="orch-parent">
                            <wa-icon name="arrow-turn-up" class="orch-parent-icon"></wa-icon>
                            <span class="orch-parent-label">Spawned by</span>
                            <router-link v-if="parentRoute" :to="parentRoute" class="orch-parent-link">{{ parentTitle }}</router-link>
                            <template v-else>
                                <span class="orch-parent-link">{{ parentTitle }}</span>
                                <wa-icon name="eye-slash" label="Hidden session" title="Hidden session"></wa-icon>
                            </template>
                        </div>
                        <div v-if="showHiddenNote" class="orch-note">
                            Sessions marked <wa-icon name="eye-slash" class="orch-note-icon"></wa-icon> were created hidden by their parent and can't be opened.
                        </div>
                        <div class="orch-tree">
                            <OrchestrationNode
                                :node="subtree"
                                :nodes-by-id="nodesById"
                                :current-session-id="sessionId"
                                :timeline="timeline"
                            />
                        </div>
                        <div v-if="!subtree.children.length" class="orch-empty-line">
                            <wa-icon name="diagram-project"></wa-icon>
                            This session has not spawned any session.
                        </div>
                    </template>
                    <div v-else class="orch-state orch-state-empty">
                        <wa-icon name="sitemap"></wa-icon>
                        <span>No orchestration data.</span>
                    </div>
                </template>
            </div>
        </div>
    </div>
</template>

<style scoped>
/* The status icon on the view switch's inactive segment sits 2px lower, on both segments. */
.orch-switch-activity {
    transform: translateY(2px);
}

/* The pane is a size container: narrow layouts follow ITS width (set by the dock layout), not the viewport. */
.orchestration-panel {
    container: orch / inline-size;
    height: 100%;
    min-height: 0;
}

.orch-frame {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.orch-header {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s) var(--wa-space-m) var(--wa-space-m);
    border-bottom: var(--divider-size, 1px) solid var(--wa-color-surface-border);
}

.orch-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
}

.orch-view-switch {
    flex-shrink: 0;
}

.orch-mode-tag {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: 600;
}

.orch-mode-tag wa-icon {
    color: var(--wa-color-brand-60);
}

.orch-content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

/* The "Spawned by" line: the parent's title is the only link. */
.orch-parent {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border: 1px dashed var(--wa-color-brand-border-quiet);
    background: color-mix(in oklab, var(--wa-color-brand-fill-quiet) 30%, transparent);
    font-size: var(--wa-font-size-s);
}

.orch-parent-icon {
    color: var(--wa-color-brand-60);
}

.orch-parent-label {
    color: var(--wa-color-text-quiet);
}

.orch-parent-link {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
    color: inherit;
    text-decoration: none;
}

a.orch-parent-link:hover {
    text-decoration: underline;
}

.orch-note {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.orch-note-icon {
    /* Inline reference to the hidden-session marker, in the flow of the text. */
    vertical-align: -0.1em;
    margin-inline: 0.1em;
}

.orch-tree {
    line-height: 1.5;
}

.orch-empty-line {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.orch-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
}

.orch-state-empty {
    flex-direction: column;
    font-size: var(--wa-font-size-l);
}

/* Narrow pane: the switch and Refresh go icon-only (labels stay for assistive tech), and the header is no
   longer fixed — it scrolls away with the body, so a short pane keeps room for the cards. */
@container orch (max-width: 420px) {
    .orch-view-switch :deep(.segmented-icon) {
        margin-inline-end: 0;
    }

    .orch-view-switch :deep(.segmented-label),
    .orch-refresh-label {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
    }
}

@container orch (max-width: 480px) {
    .orch-frame {
        display: block;
        overflow: auto;
    }

    .orch-content {
        overflow: visible;
    }
}
</style>
```

- [ ] **Step 2: Fix the one thing a scoped container-query cannot do**

A container query cannot style the container element itself, which is why the rules above target `.orch-frame` and `.orch-view-switch`, descendants of `.orchestration-panel`. Verify that no rule inside an `@container orch` block targets `.orchestration-panel`. Run: `cd /home/twidi/dev/twicc-poc/frontend/src/components/orchestration && grep -n "@container" -A4 OrchestrationPanel.vue OrchestrationSummary.vue | grep -n "orchestration-panel"`
Expected: no output.

- [ ] **Step 3: Check removed imports are unused and the icons exist**

Run: `cd /home/twidi/dev/twicc-poc/frontend/src && grep -rn "idleWithShellsSuffix\|summarizeActivity\|orch-autorefresh" components/orchestration utils/backgroundWork.js | head`
Expected: `idleWithShellsSuffix` only in `utils/backgroundWork.js` and its test (still exported, no longer used by the panel; leave it).

Icon names new in this tab: `arrow-turn-up` (the chosen glyph for the "Spawned by" return arrow; the spec's "arrow-up-left" only describes it), `arrow-right` and `calendar` (Font Awesome Free). If one renders blank in the manual check (Task 9), swap for `turn-up`/`arrow-right-long`. (Project note: test the FA Free CDN with 200/403 when unsure.)

- [ ] **Step 4: Run all frontend tests**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/orchestration/OrchestrationPanel.vue && git commit -m "feat(orchestration): redesign the tab header and root the tree on the session" -m "The tab now opens with the view switch, Refresh and four summary tiles. The sessions tree starts at the current session, with a Spawned by line linking to the parent's own Orchestration tab and the hidden-sessions note under it. The Live/Stopped indicator is gone, and every time bar shares one range." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Final verification and docs check

**Files:**
- Modify (only if inaccurate): `README.md` lines about Orchestration (around 125 and 172)

- [ ] **Step 1: Run every automated check**

Run:
```bash
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_agent_run_snapshot.py tests/test_subagents_tree_endpoint.py tests/test_codex_subagent_links.py tests/test_cli_topology.py -q
cd /home/twidi/dev/twicc-poc/frontend && npm test
uvx ruff check /home/twidi/dev/twicc-poc/src/twicc/core/session_queries.py /home/twidi/dev/twicc-poc/tests/test_agent_run_snapshot.py
```
Expected: all PASS. For ruff, only report findings on lines this plan added (the project has no lint baseline).

- [ ] **Step 2: Update the README**

`README.md:125` reads "- **Orchestration** — the tree of sessions this one spawned or was spawned by". The tab now shows the sessions this session spawned and its subagents. Replace that line with:

```
  - **Orchestration** — the sessions this one spawned (with a link to the session that spawned it) and its subagents
```

`ORCHESTRATION.md:69` says the tab renders "the whole tree rooted at its top-level ancestor — each node's title, live status, own and cumulative cost, annotations, and timing". Keep the prefix "The same map is available visually in the TwiCC UI: " and replace only the clause after the colon with: "any session that belongs to a spawn tree shows a read-only **Orchestration** tab: the sessions it spawned (each a card with its title, live state, agent settings, own and cumulative cost, annotations and timing), a link to the session that spawned it, and its subagents — to follow the orchestration at a glance without opening each session." Keep the rest of the paragraph. The `### Orchestration` section of `README.md` (around line 172) needs no change. Commit:

```bash
cd /home/twidi/dev/twicc-poc && git add README.md ORCHESTRATION.md && git commit -m "docs: describe the redesigned orchestration tab" -m "The Orchestration tab now shows the sessions the current session spawned, with a link to its spawner, plus its subagents, instead of the whole spawn tree." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
(the `git add` also lists `ORCHESTRATION.md`.)

- [ ] **Step 3: Hand over to the user for the manual pass**

The user must restart the backend (model field) and the frontend through `devctl.py` (never run by the agent). Then check against spec section 11, on a real orchestration tree (a session that spawned children, one that has a parent, one with subagents):

1. Root session: first card is the session, no "Spawned by".
2. Child session: "Spawned by <title>" opens the parent's Orchestration tab; a child with no children shows its card and "This session has not spawned any session."
3. A hidden descendant is dimmed; the note shows under "Spawned by" only then. (Known cosmetic: the annotations popover of a dimmed card inherits its opacity; report it if it looks bad and move the dimming to the card's rows.)
4. Switch first, Refresh at its right; no Live/Stopped, no "current"/"background" badges.
5. Icons only (no text) for working/awaiting/idle/shell; none when stopped; border colour follows (blue for `starting` too).
6. A subagent card shows its model (for a loaded and for a historical agent; use Refresh).
7. "Show costs" off: no cost anywhere, no Total cost tile.
8. Pane width 320 px: cost still at the right of the facts row; switch and Refresh icon-only; header scrolls away under 480 px.
9. Annotations: one line of tags, chevron always; `+N` when tags are cut; popover shows dotted keys as a tree.
10. A working node's bar fades and reaches the right end; a fully stopped tree's Span does not grow.
11. Both themes. Collapsing a branch changes no tile.
12. Check the icons `arrow-turn-up`, `arrow-right`, `calendar` render (swap if blank).

Report the outcome; fix anything that fails with a new commit.

- [ ] **Step 4: Propose, do not write, the changelog entry**

Tell the user a `CHANGELOG.md` `[Unreleased]` entry is ready to write on request (project rule: no entry without an explicit ask). Suggested line: "The Orchestration tab has a new look: a header with summary tiles, the sessions tree starts at the current session with a link to its parent, and each session or subagent is a card with a time bar. Subagents show their model."
