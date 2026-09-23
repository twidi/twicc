# Model × effort scores from Artificial Analysis data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DeepSWE-based model × effort score with the "target ability" model, fed by a frozen Artificial Analysis snapshot bundled in the frontend, and remove the DeepSWE sync end to end.

**Architecture:** A static JSON file (`frontend/src/data/modelBenchmarks.json`) holds 56 rows. A pure module (`frontend/src/utils/benchmarkScores.js`) turns rows + controls (task type, difficulty, favor) + a scoring-set predicate into a `Map` of scores. Two Pinia stores wire it: `benchmarkTask` (the controls) and `benchmarks` (the data + score lookup). A new component `AgentSettingsBenchmarkTask.vue` replaces the weights UI. The backend loses the fetch task, the DB writer job, the bootstrap key, the serializer and the `ModelBenchmark` table.

**Tech Stack:** Vue 3 `<script setup>`, Pinia, Web Awesome 3 (`wa-select`, `wa-slider`, `wa-radio-group`, `wa-radio`, `wa-switch`), `node:test`; Django 6 migrations.

**Status:** independent review PASS (round 3), 2026-09-23.

**Spec:** `docs/plans/2026-09-23-artificial-analysis-benchmark-scores-design.md` (read it before any task; section numbers below refer to it).

## Global Constraints

- All code, comments, UI strings and docs in English.
- `CURVE = 0.55`, `TOLERANCE_II = 4`; defaults `taskType: 'general'`, `difficulty: 50`, `favor: 'cost'`, `autoSelectBest: false`, `defaultProviderOnly: false`.
- Controls live in memory only (no localStorage, no synced setting).
- Only the store imports `modelBenchmarks.json` statically (Vite handles it). Tests read it with `readFileSync` + `JSON.parse`: `node --test` rejects a bare JSON import.
- Frontend circular imports (CLAUDE.md): `stores/benchmarks.js` may import `stores/settings.js`, `stores/benchmarkTask.js` and `providers/index.js`; none of those may import `stores/benchmarks.js`.
- Migrations never import application code.
- Python: `uvx ruff check <files>` (line length 120). Never `uv pip`, never `--active`.
- Frontend build checks write to `/tmp` only: `cd frontend && npx vite build --outDir /tmp/twicc-build-check --emptyOutDir`. Never build into `src/twicc/static`.
- Never run `migrate`, never restart servers, never run `npm install`/`npm ci`.
- Commits: only if the user explicitly asked for commits. When committing, stage the exact files listed in the step (never a directory), Conventional Commit subject, descriptive body, trailer `Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>`.
- No CHANGELOG edit (propose it to the user at the end).

## Review Focus

1. **A disabled provider** (`settings.enabledProviders` lacks it): its rows get no score, and the best remaining couple scores 100. Pinned by the `makeScoringSetPredicate` tests in Task 2.
2. **A retired or disabled model, or an effort the model does not support**, present in the file: no score, and it does not move the slider range. Pinned by the predicate tests and the "rows outside the scoring set do not move the range" test in Task 2.
3. **Speed mode with a `null` time** (`claude-opus-5-5` max): that cell shows "?" and the panel shows the empty text, no crash. Pinned by the Speed test in Task 2 and the manual check in Task 6 Step 3.
4. **Slider at 0 and 100**: target equals the range min and max, never `NaN`; empty or flat ranges return safe results. Pinned by the target and degenerate-case tests in Task 2.
5. **Changing Task type or Favor inside the popover**: the nested `wa-select` / `wa-radio-group` must not close the popover or steal focus. Pinned by the manual check in Task 6 Step 3 (the parents already guard bubbling `wa-*` events; the check confirms it).

---

### Task 1: Data file

**Files:**
- Create: `frontend/src/data/modelBenchmarks.json`
- Create: `frontend/src/data/modelBenchmarks.test.js`

**Interfaces:**
- Produces: `modelBenchmarks.json` with shape `{ source, url, retrieved_at, rows: [{ provider, model, effort, intelligence_index, evals: { <10 keys>: { score, cost_usd, time_s } } }] }` (spec §3.1). Evaluation keys, in this order: `terminalbench_4_0`, `scicode`, `aa_briefcase`, `gdpval_aa`, `gdp_pdf`, `automationbench_aa`, `humanitys_last_exam`, `critpt`, `aa_omniscience`, `aa_lcr`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/data/modelBenchmarks.test.js`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Read the file the way the store cannot be read under node --test: a bare JSON
// import needs an import attribute there, so tests parse it explicitly.
const data = JSON.parse(readFileSync(new URL('./modelBenchmarks.json', import.meta.url), 'utf8'))

const EVAL_KEYS = [
    'terminalbench_4_0', 'scicode', 'aa_briefcase', 'gdpval_aa', 'gdp_pdf',
    'automationbench_aa', 'humanitys_last_exam', 'critpt', 'aa_omniscience', 'aa_lcr',
]

const isNumOrNull = v => v === null || (typeof v === 'number' && Number.isFinite(v))

test('carries the source metadata', () => {
    assert.equal(data.source, 'Artificial Analysis')
    assert.equal(data.url, 'https://artificialanalysis.ai/')
    assert.equal(data.retrieved_at, '2026-09-23')
})

test('holds the 56 rows of the spec, with unique keys', () => {
    assert.equal(data.rows.length, 56)
    const keys = new Set(data.rows.map(r => `${r.provider} ${r.model} ${r.effort}`))
    assert.equal(keys.size, 56)
    for (const r of data.rows) {
        assert.ok(['claude_code', 'codex'].includes(r.provider), r.provider)
        assert.ok(['low', 'medium', 'high', 'xhigh', 'max'].includes(r.effort), r.effort)
    }
})

test('every row has exactly the 10 evaluations with numeric or null values', () => {
    for (const r of data.rows) {
        assert.ok(isNumOrNull(r.intelligence_index))
        assert.deepEqual(Object.keys(r.evals), EVAL_KEYS)
        for (const k of EVAL_KEYS) {
            const e = r.evals[k]
            assert.deepEqual(Object.keys(e), ['score', 'cost_usd', 'time_s'])
            assert.ok(isNumOrNull(e.score) && isNumOrNull(e.cost_usd) && isNumOrNull(e.time_s), `${r.model} ${r.effort} ${k}`)
        }
    }
})

test('the only nulls are the times of claude-opus-5-5 max', () => {
    for (const r of data.rows) {
        for (const k of EVAL_KEYS) {
            const e = r.evals[k]
            const opusMax = r.model === 'claude-opus-5-5' && r.effort === 'max'
            assert.equal(e.score === null, false)
            assert.equal(e.cost_usd === null, false)
            assert.equal(e.time_s === null, opusMax, `${r.model} ${r.effort} ${k}`)
        }
    }
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/data/modelBenchmarks.test.js`
Expected: FAIL with `ENOENT` (the JSON file does not exist).

- [ ] **Step 3: Generate the JSON from the spec's Appendix A**

Save this one-shot script as `/tmp/build-model-benchmarks.mjs` (throwaway, not committed):

```js
// Converts Appendix A of the spec into frontend/src/data/modelBenchmarks.json.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'

const REPO = '/home/twidi/dev/twicc-poc'
const spec = readFileSync(`${REPO}/docs/plans/2026-09-23-artificial-analysis-benchmark-scores-design.md`, 'utf8')
// The first triple-backtick block after the Appendix A heading holds the rows.
const block = spec.split('## Appendix A')[1].split('```')[1]
const EVAL_KEYS = [
    'terminalbench_4_0', 'scicode', 'aa_briefcase', 'gdpval_aa', 'gdp_pdf',
    'automationbench_aa', 'humanitys_last_exam', 'critpt', 'aa_omniscience', 'aa_lcr',
]
const num = s => (s === '' ? null : Number(s))

const rows = block.trim().split('\n').map(line => {
    const [provider, model, effort, ii, rest] = line.split('|')
    const triples = rest.split(';')
    if (triples.length !== 10) throw new Error(`bad row: ${line}`)
    return {
        provider,
        model,
        effort,
        intelligence_index: num(ii),
        evals: Object.fromEntries(EVAL_KEYS.map((k, i) => {
            const [score, cost, time] = triples[i].split(',')
            return [k, { score: num(score), cost_usd: num(cost), time_s: num(time) }]
        })),
    }
})

const data = {
    source: 'Artificial Analysis',
    url: 'https://artificialanalysis.ai/',
    retrieved_at: '2026-09-23',
    rows,
}
mkdirSync(`${REPO}/frontend/src/data`, { recursive: true })
writeFileSync(`${REPO}/frontend/src/data/modelBenchmarks.json`, JSON.stringify(data, null, 2) + '\n')
console.log(`${rows.length} rows written`)
```

Run: `node /tmp/build-model-benchmarks.mjs`
Expected: `56 rows written`. Then delete the script: `rm /tmp/build-model-benchmarks.mjs`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/data/modelBenchmarks.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit (only if the user asked for commits)**

```bash
git add frontend/src/data/modelBenchmarks.json frontend/src/data/modelBenchmarks.test.js
git commit -m "feat(benchmarks): add the Artificial Analysis snapshot" -m "Frozen 2026-09-23 snapshot of Artificial Analysis evaluations (56 model × effort rows) that will feed the new target-ability score; see the design doc."
```

---

### Task 2: Scoring module

**Files:**
- Modify (full rewrite): `frontend/src/utils/benchmarkScores.js`
- Create: `frontend/src/utils/benchmarkScores.test.js`

**Interfaces:**
- Consumes: the JSON row shape from Task 1.
- Produces (all named exports of `benchmarkScores.js`):
  - `CURVE` (0.55), `TOLERANCE_II` (4)
  - `EVAL_LABELS`: `{ [evalKey]: string }`
  - `TASK_TYPES`: `[{ id, label, evals: string[] | null }]`
  - `scoreKey(provider, model, effort) → string` (unchanged format `"provider model effort"`)
  - `typeMetrics(row, taskType) → { ability: number|null, cost: number|null, time: number|null }`
  - `computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet) → Map<string, { score, penalty, ability, target, metric }>`
  - `makeScoringSetPredicate(enabledProviders, getHelpers) → (row) => boolean` (spec §4.3; `getHelpers(provider)` returns provider helpers or `null`)
  - `formatCost(x) → string`, `formatTime(seconds) → string`
  - `formatBenchmarkDetails(row, scored, taskType) → [{ label, value, description }]`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/benchmarkScores.test.js`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
    CURVE,
    EVAL_LABELS,
    TASK_TYPES,
    computeBenchmarkScores,
    formatBenchmarkDetails,
    formatCost,
    formatTime,
    makeScoringSetPredicate,
    scoreKey,
    typeMetrics,
} from './benchmarkScores.js'

const EVAL_KEYS = Object.keys(EVAL_LABELS)
const ALL = () => true
const opts = (o = {}) => ({ taskType: 'general', difficulty: 50, favor: 'cost', ...o })

// A synthetic row: every evaluation defaults to score 0.5, $1, 100 s.
function row(model, ii, { effort = 'high', provider = 'codex', evals = {} } = {}) {
    const e = Object.fromEntries(EVAL_KEYS.map(k => [k, { score: 0.5, cost_usd: 1, time_s: 100 }]))
    for (const [k, v] of Object.entries(evals)) e[k] = { ...e[k], ...v }
    return { provider, model, effort, intelligence_index: ii, evals: e }
}
const key = r => scoreKey(r.provider, r.model, r.effort)

test('exposes the 7 task types in order, general first', () => {
    assert.deepEqual(TASK_TYPES.map(t => t.id), ['general', 'coding', 'office', 'automation', 'science', 'knowledge', 'longdocs'])
    assert.equal(TASK_TYPES[0].evals, null)
    assert.equal(EVAL_KEYS.length, 10)
})

test('general uses the Intelligence Index and Terminal-Bench 4.0 cost / time', () => {
    const r = row('m', 42, { evals: { terminalbench_4_0: { cost_usd: 2, time_s: 300 } } })
    assert.deepEqual(typeMetrics(r, 'general'), { ability: 42, cost: 2, time: 300 })
})

test('normalizes Elo and Omniscience scores to 0..100', () => {
    const r = row('m', 50, {
        evals: {
            aa_briefcase: { score: 1500 }, gdpval_aa: { score: 1300 }, gdp_pdf: { score: 0.3 },
            aa_omniscience: { score: 20 },
        },
    })
    assert.ok(Math.abs(typeMetrics(r, 'office').ability - 40) < 1e-9) // mean(0.5, 0.4, 0.3)
    assert.ok(Math.abs(typeMetrics(r, 'knowledge').ability - 60) < 1e-9) // (20 + 100) / 200
})

test('averages cost and time over the type evaluations', () => {
    const r = row('m', 50, { evals: { terminalbench_4_0: { cost_usd: 5, time_s: 600 }, scicode: { cost_usd: 1, time_s: 10 } } })
    assert.deepEqual(typeMetrics(r, 'coding'), { ability: 50, cost: 3, time: 305 })
})

test('a null score nulls the ability, a null cost or time nulls only that value', () => {
    const noScore = row('m', 50, { evals: { scicode: { score: null } } })
    assert.equal(typeMetrics(noScore, 'coding').ability, null)
    const noTime = row('m', 50, { evals: { scicode: { time_s: null } } })
    assert.deepEqual(typeMetrics(noTime, 'coding'), { ability: 50, cost: 1, time: null })
})

test('the target follows the curve over the range', () => {
    const rows = [row('lo', 20), row('hi', 60)]
    const at = d => computeBenchmarkScores(rows, opts({ difficulty: d }), ALL).get(key(rows[0])).target
    assert.equal(at(0), 20)
    assert.equal(at(100), 60)
    assert.ok(Math.abs(at(50) - (20 + 40 * Math.pow(0.5, CURVE))) < 1e-9)
})

test('no distance penalty above the target: the cheapest wins', () => {
    const rows = [
        row('a', 30, { evals: { terminalbench_4_0: { cost_usd: 1 } } }),
        row('b', 40, { evals: { terminalbench_4_0: { cost_usd: 2 } } }),
        row('c', 50, { evals: { terminalbench_4_0: { cost_usd: 4 } } }),
    ]
    const s = computeBenchmarkScores(rows, opts({ difficulty: 0 }), ALL)
    assert.deepEqual(rows.map(r => s.get(key(r)).score), [100, 50, 25])
})

test('general tolerance is 4 points: 4 below the target at equal cost scores 50', () => {
    const rows = [row('lo', 20), row('best', 60), row('near', 56)]
    const s = computeBenchmarkScores(rows, opts({ difficulty: 100 }), ALL)
    assert.equal(s.get(key(rows[1])).score, 100)
    assert.equal(s.get(key(rows[2])).score, 50)
})

test('the tolerance scales with the type range', () => {
    // coding abilities 20 / 40 / 38 (range 20); general 20 / 60 / 40 (range 40) → tolerance 2.
    const coding = (a) => ({ terminalbench_4_0: { score: a }, scicode: { score: a } })
    const rows = [row('x', 20, { evals: coding(0.2) }), row('y', 60, { evals: coding(0.4) }), row('z', 40, { evals: coding(0.38) })]
    const s = computeBenchmarkScores(rows, opts({ taskType: 'coding', difficulty: 100 }), ALL)
    assert.equal(s.get(key(rows[1])).score, 100)
    assert.equal(s.get(key(rows[2])).score, 50)
})

test('Speed uses the time; a null time gives no score in Speed but keeps the Cost score', () => {
    const rows = [row('fast', 50, { evals: { terminalbench_4_0: { time_s: 50 } } }), row('slow', 50), row('nulltime', 50, { evals: { terminalbench_4_0: { time_s: null } } })]
    const speed = computeBenchmarkScores(rows, opts({ favor: 'speed' }), ALL)
    assert.equal(speed.get(key(rows[0])).score, 100)
    assert.equal(speed.get(key(rows[1])).score, 50)
    assert.equal(speed.has(key(rows[2])), false)
    assert.equal(computeBenchmarkScores(rows, opts(), ALL).has(key(rows[2])), true)
})

test('a non-positive or null metric gives no score', () => {
    const rows = [row('ok', 50), row('zero', 50, { evals: { terminalbench_4_0: { cost_usd: 0 } } })]
    const s = computeBenchmarkScores(rows, opts(), ALL)
    assert.equal(s.has(key(rows[1])), false)
})

test('rows outside the scoring set get no score and do not move the range', () => {
    const rows = [row('excluded', 10), row('lo', 20), row('hi', 60)]
    const s = computeBenchmarkScores(rows, opts({ difficulty: 0 }), r => r.model !== 'excluded')
    assert.equal(s.has(key(rows[0])), false)
    assert.equal(s.get(key(rows[1])).target, 20)
})

test('degenerate cases return safe results', () => {
    assert.equal(computeBenchmarkScores([], opts(), ALL).size, 0)
    // Flat type range: target = min, no distance penalty.
    const flat = [row('a', 50, { evals: { terminalbench_4_0: { cost_usd: 1 } } }), row('b', 50, { evals: { terminalbench_4_0: { cost_usd: 2 } } })]
    const s = computeBenchmarkScores(flat, opts({ difficulty: 100 }), ALL)
    assert.equal(s.get(key(flat[0])).target, 50)
    assert.deepEqual(flat.map(r => s.get(key(r)).score), [100, 50])
    // Flat general range, spread coding range: tolerance stays 4.
    const coding = (a) => ({ terminalbench_4_0: { score: a }, scicode: { score: a } })
    const g = [row('p', 50, { evals: coding(0.2) }), row('q', 50, { evals: coding(0.6) }), row('r', 50, { evals: coding(0.56) })]
    const sg = computeBenchmarkScores(g, opts({ taskType: 'coding', difficulty: 100 }), ALL)
    assert.equal(sg.get(key(g[2])).score, 50)
    // No row with both ability and cost: empty map, even in Speed mode.
    const noCost = [row('n', 50, { evals: { terminalbench_4_0: { cost_usd: null } } })]
    assert.equal(computeBenchmarkScores(noCost, opts({ favor: 'speed' }), ALL).size, 0)
})

test('scores are global across providers', () => {
    const rows = [row('cheap', 50, { provider: 'codex' }), row('dear', 50, { provider: 'claude_code', evals: { terminalbench_4_0: { cost_usd: 4 } } })]
    const s = computeBenchmarkScores(rows, opts(), ALL)
    assert.equal(s.get(key(rows[0])).score, 100)
    assert.equal(s.get(key(rows[1])).score, 25)
})

// Fake provider helpers for the scoring-set predicate (spec §4.3).
function fakeHelpers({ registry, efforts = ['low', 'medium', 'high', 'xhigh', 'max'], disabled = () => false }) {
    return {
        getModelRegistry: () => registry,
        isModelAvailable: (e) => e.enabled !== false && !e.retired,
        getFieldChoices: () => efforts.map(value => ({ value })),
        isChoiceDisabled: (field, value, { effectiveModel }) => disabled(value, effectiveModel),
    }
}

test('the scoring set keeps enabled providers, available models and selectable efforts', () => {
    const codex = fakeHelpers({
        registry: [
            { full_name: 'gpt-a', selected_model: 'a' },
            { full_name: 'gpt-retired', selected_model: 'r', retired: true },
            { full_name: 'gpt-off', selected_model: 'o', enabled: false },
            { full_name: 'gpt-nomax', selected_model: 'n' },
        ],
        disabled: (effort, model) => model === 'n' && effort === 'max',
    })
    const claude = fakeHelpers({ registry: [{ full_name: 'claude-a', selected_model: 'ca' }] })
    const helpers = { codex, claude_code: claude }
    const inSet = makeScoringSetPredicate(['codex'], p => helpers[p] ?? null)
    const r = (provider, model, effort = 'high') => ({ provider, model, effort })
    assert.equal(inSet(r('codex', 'gpt-a')), true)
    assert.equal(inSet(r('claude_code', 'claude-a')), false) // provider disabled
    assert.equal(inSet(r('codex', 'gpt-retired')), false)
    assert.equal(inSet(r('codex', 'gpt-off')), false)
    assert.equal(inSet(r('codex', 'gpt-unknown')), false) // not in the registry
    assert.equal(inSet(r('codex', 'gpt-nomax', 'max')), false) // effort disabled for the model
    assert.equal(inSet(r('codex', 'gpt-nomax', 'high')), true)
    assert.equal(inSet(r('codex', 'gpt-a', 'ultra')), false) // not an effort choice
    assert.equal(makeScoringSetPredicate(['codex'], () => null)(r('codex', 'gpt-a')), false)
})

test('formats cost and time as the spec says', () => {
    assert.equal(formatCost(5.12), '$5.12')
    assert.equal(formatCost(4), '$4')
    assert.equal(formatCost(0.59), '$0.59')
    assert.equal(formatCost(0.000175), '$0.000175')
    assert.equal(formatCost(null), '—')
    assert.equal(formatTime(9.14), '9.1 s')
    assert.equal(formatTime(0.305), '0.3 s')
    assert.equal(formatTime(59.97), '1.0 min')
    assert.equal(formatTime(696), '11.6 min')
    assert.equal(formatTime(62.95), '1.1 min')
    assert.equal(formatTime(null), '—')
})

// Opus 5.5 high, as in Appendix A (only the evaluations the checks use).
const opusHigh = row('claude-opus-5-5', 53.58, {
    provider: 'claude_code',
    evals: {
        terminalbench_4_0: { score: 0.566, cost_usd: 5.12, time_s: 696 },
        scicode: { score: 0.604, cost_usd: 0.0402, time_s: 9.14 },
    },
})

test('details for general', () => {
    const d = formatBenchmarkDetails(opusHigh, { score: 43, target: 45.98 }, 'general')
    assert.deepEqual(d.map(x => [x.label, x.value]), [
        ['Score', '43'],
        ['Intelligence Index', '53.6'],
        ['Target', '46.0'],
        ['Cost / task', '$5.12'],
        ['Time / task', '11.6 min'],
        ['Based on', 'Intelligence Index; cost and time from Terminal-Bench 4.0'],
    ])
    for (const x of d) assert.ok(x.description.length > 0)
})

test('details for coding', () => {
    const d = formatBenchmarkDetails(opusHigh, { score: 56, target: 50.51 }, 'coding')
    assert.deepEqual(d.map(x => [x.label, x.value]), [
        ['Score', '56'],
        ['Coding & terminal score', '58.5'],
        ['Target', '50.5'],
        ['Cost / task', '$2.58'],
        ['Time / task', '5.9 min'],
        ['Based on', 'Terminal-Bench 4.0, SciCode'],
    ])
})

// Regression on the real snapshot against Appendix B of the spec.
const data = JSON.parse(readFileSync(new URL('../data/modelBenchmarks.json', import.meta.url), 'utf8'))
const APPENDIX_B = [
    ['general', 'cost', 0, 20.90, ['codex gpt-6-luna low', 100], ['codex gpt-5.6-luna low', 46]],
    ['general', 'cost', 50, 45.98, ['codex gpt-6-sol xhigh', 100], ['codex gpt-6-astra low', 99]],
    ['general', 'cost', 100, 57.62, ['claude_code claude-opus-5-5 xhigh', 100], ['claude_code claude-opus-5-5 high', 95]],
    ['general', 'speed', 50, 45.98, ['codex gpt-6-astra low', 100], ['codex gpt-6-sol xhigh', 65]],
    ['coding', 'cost', 50, 50.51, ['codex gpt-6-astra low', 100], ['codex gpt-6-sol max', 71]],
    ['office', 'cost', 100, 53.23, ['claude_code claude-opus-5-5 high', 100], ['claude_code claude-opus-5-5 xhigh', 82]],
    ['knowledge', 'speed', 0, 42.65, ['claude_code claude-sonnet-5 low', 100], ['claude_code claude-sonnet-5 medium', 46]],
    ['longdocs', 'cost', 100, 85.30, ['codex gpt-6-luna max', 100], ['codex gpt-5.6-luna max', 63]],
]

test('reproduces Appendix B on the real snapshot', () => {
    for (const [taskType, favor, difficulty, target, first, second] of APPENDIX_B) {
        const s = computeBenchmarkScores(data.rows, { taskType, difficulty, favor }, ALL)
        const ranked = [...s.entries()].sort((a, b) => a[1].penalty - b[1].penalty)
        const label = `${taskType}/${favor}/${difficulty}`
        assert.equal(ranked[0][1].target.toFixed(2), target.toFixed(2), label)
        assert.deepEqual([ranked[0][0], ranked[0][1].score], first, label)
        assert.deepEqual([ranked[1][0], ranked[1][1].score], second, label)
    }
    const speed = computeBenchmarkScores(data.rows, { taskType: 'general', difficulty: 50, favor: 'speed' }, ALL)
    assert.equal(speed.has('claude_code claude-opus-5-5 max'), false)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/benchmarkScores.test.js`
Expected: FAIL (`does not provide an export named 'EVAL_LABELS'` or similar).

- [ ] **Step 3: Rewrite `frontend/src/utils/benchmarkScores.js`**

Replace the whole file with:

```js
/**
 * Model × effort scores — the "target ability" model.
 *
 * Spec: docs/plans/2026-09-23-artificial-analysis-benchmark-scores-design.md §4.
 * Rows come from the frozen Artificial Analysis snapshot
 * (``data/modelBenchmarks.json``). The user picks a task type, a difficulty and
 * whether to favor cost or speed. The difficulty sets a target on the task
 * type's ability axis; each (model, effort) pays a squared distance penalty
 * when below the target (none above it) plus log2 of its cost (or time) per
 * task. The lowest penalty scores 100; every "doubling" worse halves the score.
 *
 * Pure module: no store, no Vue. The scoring-set predicate is injected so the
 * store decides which rows count (enabled providers, available models,
 * selectable efforts) while this module stays testable.
 */

// Exponent of the concave difficulty → target curve (the slider's middle lands high).
export const CURVE = 0.55
// Being this many Intelligence Index points below the target weighs as much as
// paying (or waiting) twice as much. Other task types scale it by their range.
export const TOLERANCE_II = 4

// Display name of each Artificial Analysis evaluation key.
export const EVAL_LABELS = {
    terminalbench_4_0: 'Terminal-Bench 4.0',
    scicode: 'SciCode',
    aa_briefcase: 'AA-Briefcase',
    gdpval_aa: 'GDPval-AA',
    gdp_pdf: 'GDP.pdf',
    automationbench_aa: 'AutomationBench-AA',
    humanitys_last_exam: "Humanity's Last Exam",
    critpt: 'CritPt',
    aa_omniscience: 'AA-Omniscience',
    aa_lcr: 'AA-LCR',
}

// Task types, in display order. ``evals: null`` = the Intelligence Index, with
// Terminal-Bench 4.0 cost and time.
export const TASK_TYPES = [
    { id: 'general', label: 'General (Intelligence Index)', evals: null },
    { id: 'coding', label: 'Coding & terminal', evals: ['terminalbench_4_0', 'scicode'] },
    { id: 'office', label: 'Office & knowledge work', evals: ['aa_briefcase', 'gdpval_aa', 'gdp_pdf'] },
    { id: 'automation', label: 'Automation & tools', evals: ['automationbench_aa'] },
    { id: 'science', label: 'Science & reasoning', evals: ['humanitys_last_exam', 'critpt', 'scicode'] },
    { id: 'knowledge', label: 'Factual knowledge', evals: ['aa_omniscience'] },
    { id: 'longdocs', label: 'Long documents', evals: ['aa_lcr'] },
]

// Evaluation scores not already a 0..1 rate: Elo as Artificial Analysis converts
// it, and the Omniscience index from -100..100.
const EVAL_NORMALIZERS = {
    aa_briefcase: s => (s - 500) / 2000,
    gdpval_aa: s => (s - 500) / 2000,
    aa_omniscience: s => (s + 100) / 200,
}

const DASH = '—'

const isNum = v => typeof v === 'number' && Number.isFinite(v)

// Mean of the values, or null as soon as one is missing.
function meanOrNull(values) {
    if (!values.length || !values.every(isNum)) return null
    return values.reduce((a, b) => a + b, 0) / values.length
}

/** Lookup key joining a benchmark row to a matrix cell. */
export function scoreKey(provider, model, effort) {
    return `${provider} ${model} ${effort}`
}

const rowKey = row => scoreKey(row.provider, row.model, row.effort)

/**
 * Ability (0..100), cost per task (USD) and time per task (s) of one row for a
 * task type; each is null when a needed value is missing (spec §4.1).
 */
export function typeMetrics(row, taskType) {
    const type = TASK_TYPES.find(t => t.id === taskType)
    if (!type) return { ability: null, cost: null, time: null }
    if (type.evals === null) {
        const tb = row.evals?.terminalbench_4_0
        return {
            ability: isNum(row.intelligence_index) ? row.intelligence_index : null,
            cost: isNum(tb?.cost_usd) ? tb.cost_usd : null,
            time: isNum(tb?.time_s) ? tb.time_s : null,
        }
    }
    const parts = type.evals.map(k => row.evals?.[k] ?? null)
    const scores = parts.map((p, i) => {
        if (!isNum(p?.score)) return null
        const normalize = EVAL_NORMALIZERS[type.evals[i]]
        return normalize ? normalize(p.score) : p.score
    })
    const ability = meanOrNull(scores)
    return {
        ability: ability === null ? null : 100 * ability,
        cost: meanOrNull(parts.map(p => p?.cost_usd)),
        time: meanOrNull(parts.map(p => p?.time_s)),
    }
}

// Lowest and highest ability over the rows having both an ability and a cost
// (cost in both modes, so the slider mapping never depends on Favor), or null.
function abilityRange(rows, taskType) {
    let min = Infinity
    let max = -Infinity
    for (const row of rows) {
        const m = typeMetrics(row, taskType)
        if (m.ability === null || m.cost === null) continue
        if (m.ability < min) min = m.ability
        if (m.ability > max) max = m.ability
    }
    return min === Infinity ? null : { min, max }
}

/**
 * Score every scorable row of the scoring set (spec §4.2–§4.5, §4.7).
 *
 * @param {Array<object>} rows - snapshot rows.
 * @param {{taskType: string, difficulty: number, favor: 'cost'|'speed'}} controls
 * @param {(row: object) => boolean} isInScoringSet - which rows count.
 * @returns {Map<string, {score: number, penalty: number, ability: number, target: number, metric: number}>}
 *   keyed by scoreKey; a missing key means "no score" ("?").
 */
export function computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet) {
    const result = new Map()
    const set = (rows ?? []).filter(isInScoringSet)
    const range = abilityRange(set, taskType)
    if (!range) return result

    const general = abilityRange(set, 'general')
    const typeSpan = range.max - range.min
    const generalSpan = general ? general.max - general.min : 0
    const tolerance = generalSpan > 0 ? TOLERANCE_II * typeSpan / generalSpan : TOLERANCE_II
    const target = range.min + typeSpan * Math.pow(difficulty / 100, CURVE)

    const scored = []
    for (const row of set) {
        const m = typeMetrics(row, taskType)
        const metric = favor === 'speed' ? m.time : m.cost
        if (m.ability === null || metric === null || !(metric > 0)) continue
        // No distance penalty above the target, nor on a flat range (tolerance 0).
        const below = typeSpan > 0 && m.ability < target
        const distance = below ? ((target - m.ability) / tolerance) ** 2 : 0
        scored.push({ row, ability: m.ability, metric, penalty: distance + Math.log2(metric) })
    }
    if (!scored.length) return result

    const minPenalty = Math.min(...scored.map(s => s.penalty))
    for (const s of scored) {
        result.set(rowKey(s.row), {
            score: Math.round(100 * Math.pow(2, minPenalty - s.penalty)),
            penalty: s.penalty,
            ability: s.ability,
            target,
            metric: s.metric,
        })
    }
    return result
}

/**
 * Build the scoring-set predicate (spec §4.3): the row's provider is enabled,
 * its model is an available registry entry of that provider (enabled, not
 * retired), and its effort is selectable for that model — the same test that
 * enables a matrix cell (``agentMatrix.js``).
 *
 * @param {string[]} enabledProviders
 * @param {(provider: string) => object|null} getHelpers - provider helpers lookup.
 */
export function makeScoringSetPredicate(enabledProviders, getHelpers) {
    const enabled = new Set(enabledProviders ?? [])
    return (row) => {
        if (!enabled.has(row.provider)) return false
        const helpers = getHelpers(row.provider)
        if (!helpers) return false
        const entry = (helpers.getModelRegistry?.() ?? []).find(e => e.full_name === row.model)
        if (!entry || !helpers.isModelAvailable(entry)) return false
        const efforts = new Set((helpers.getFieldChoices('effort') ?? []).map(c => c.value))
        if (!efforts.has(row.effort)) return false
        return !helpers.isChoiceDisabled('effort', row.effort, { effectiveModel: entry.selected_model })
    }
}

/** "$" + 3 significant digits, trailing zeros dropped; em dash when missing. */
export function formatCost(x) {
    return isNum(x) ? `$${String(Number(x.toPrecision(3)))}` : DASH
}

/** Seconds (1 decimal) below 60 s after rounding, else minutes (1 decimal). */
export function formatTime(seconds) {
    if (!isNum(seconds)) return DASH
    const s = Math.round(seconds * 10) / 10
    return s < 60 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`
}

/**
 * Rows of a matrix cell's "Benchmark data" panel (spec §5.3).
 *
 * @param {object} row - the snapshot row.
 * @param {{score: number, target: number}} scored - the computeBenchmarkScores entry.
 * @param {string} taskType
 * @returns {{label: string, value: string, description: string}[]}
 */
export function formatBenchmarkDetails(row, scored, taskType) {
    const type = TASK_TYPES.find(t => t.id === taskType) ?? TASK_TYPES[0]
    const m = typeMetrics(row, type.id)
    const one = x => (isNum(x) ? x.toFixed(1) : DASH)
    return [
        {
            label: 'Score',
            value: isNum(scored?.score) ? String(scored.score) : DASH,
            description: 'Our score for this task: 100 = best choice.',
        },
        {
            label: type.evals ? `${type.label} score` : 'Intelligence Index',
            value: one(m.ability),
            description: "The couple's ability on this task type (0–100).",
        },
        {
            label: 'Target',
            value: one(scored?.target),
            description: 'The level set by Task difficulty.',
        },
        {
            label: 'Cost / task',
            value: formatCost(m.cost),
            description: 'Average cost of one task.',
        },
        {
            label: 'Time / task',
            value: formatTime(m.time),
            description: 'Average decode time of one task.',
        },
        {
            label: 'Based on',
            value: type.evals
                ? type.evals.map(k => EVAL_LABELS[k]).join(', ')
                : 'Intelligence Index; cost and time from Terminal-Bench 4.0',
            description: 'The Artificial Analysis evaluations used.',
        },
    ]
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/benchmarkScores.test.js src/data/modelBenchmarks.test.js`
Expected: PASS (all tests).

- [ ] **Step 5: Commit (only if the user asked for commits)**

Known intermediate state: until Task 3 lands, the old `stores/benchmarks.js` and `AgentSettingsMatrix.vue` still call the old signatures, so the app's score matrix is broken between the Task 2 and Task 3 commits (the test suite passes). If the user prefers every commit runnable, skip this commit and include these two files in the Task 3 commit instead.

```bash
git add frontend/src/utils/benchmarkScores.js frontend/src/utils/benchmarkScores.test.js
git commit -m "feat(benchmarks): add the target-ability score model" -m "Rewrite benchmarkScores.js: 7 task types over the Artificial Analysis evaluations, a difficulty-driven target on a concave curve, a squared penalty below the target plus log2 of cost or time, and a global 100 for the best couple. Unit tests plus a regression test against the spec's reference results."
```

---

### Task 3: Stores and UI wiring

**Files:**
- Create: `frontend/src/stores/benchmarkTask.js`
- Modify (full rewrite): `frontend/src/stores/benchmarks.js`
- Create: `frontend/src/components/message/AgentSettingsBenchmarkTask.vue`
- Delete: `frontend/src/stores/benchmarkWeights.js`, `frontend/src/components/message/AgentSettingsBenchmarkWeights.vue`
- Modify: `frontend/src/components/message/AgentSettingsPopover.vue` (imports L15, L19; store L72; comment L137; `findBestCell` L144; watch L161-175; template L613-614; CSS L728-732)
- Modify: `frontend/src/components/message/AgentSettingsDefaultsPicker.vue` (comment L4-5; import L23; template L112; CSS L124-128)
- Modify: `frontend/src/components/message/AgentSettingsMatrix.vue` (imports L10-15; props comment L17-24; `cellTips` L111-129; comment L286; empty text L428)
- Modify (comments only): `frontend/src/components/app/ProviderSettingsSection.vue`, `frontend/src/components/app/AgentSettingsPresetsDialog.vue`, `frontend/src/components/app/SettingsPopover.vue` (L2863)
- Modify: `frontend/src/utils/agentMatrix.js` (comment above `benchmark:` only)
- Modify: `frontend/src/main.js` (import L65; call L245)
- Modify: `frontend/src/composables/useWebSocket.js` (case `benchmarks_updated`, L1745-1752)

**Interfaces:**
- Consumes (Task 2): `TASK_TYPES`, `computeBenchmarkScores`, `makeScoringSetPredicate`, `scoreKey`, `formatBenchmarkDetails`.
- Produces:
  - `useBenchmarkTaskStore()` (id `benchmarkTask`): state `taskType`, `difficulty`, `favor`, `autoSelectBest`, `defaultProviderOnly`; actions `setTaskType(id)`, `setDifficulty(value)`, `setFavor(value)`.
  - `useBenchmarksStore()`: getters `scoreLookup` (the Map), `getScore(provider, model, effort) → number|null`, `getRow(provider, model, effort) → { row, scored }|null`.
  - `AgentSettingsBenchmarkTask.vue` with props `providerCount` (Number, default 1) and `showAutoSelect` (Boolean, default true).

- [ ] **Step 1: Create `frontend/src/stores/benchmarkTask.js`**

```js
import { defineStore } from 'pinia'
import { TASK_TYPES } from '../utils/benchmarkScores'

/**
 * Controls of the model × effort score (spec §5.1): task type, task difficulty
 * (0..100) and whether to favor cost or speed, plus the auto-select switches.
 * In memory only — reset to the defaults on reload, like the former weights.
 */
export const useBenchmarkTaskStore = defineStore('benchmarkTask', {
    state: () => ({
        taskType: 'general',
        difficulty: 50,
        favor: 'cost',
        autoSelectBest: false,
        defaultProviderOnly: false,
    }),

    actions: {
        setTaskType(id) {
            if (TASK_TYPES.some(t => t.id === id)) this.taskType = id
        },

        setDifficulty(value) {
            const n = Math.round(Number(value))
            if (Number.isFinite(n)) this.difficulty = Math.min(100, Math.max(0, n))
        },

        setFavor(value) {
            if (value === 'cost' || value === 'speed') this.favor = value
        },
    },
})
```

- [ ] **Step 2: Rewrite `frontend/src/stores/benchmarks.js`**

```js
import { defineStore } from 'pinia'
import benchmarkData from '../data/modelBenchmarks.json'
import { computeBenchmarkScores, makeScoringSetPredicate, scoreKey } from '../utils/benchmarkScores'
import { useBenchmarkTaskStore } from './benchmarkTask'
import { useSettingsStore } from './settings'
import { getProviderHelpers } from '../providers'

/**
 * Benchmark scores for the model × effort matrix (Agent Settings panel).
 *
 * The rows are the frozen Artificial Analysis snapshot bundled with the
 * frontend (``data/modelBenchmarks.json``; no fetch, no backend). ``scoreLookup``
 * scores them with the controls of the ``benchmarkTask`` store over the scoring
 * set — enabled providers, available models, selectable efforts — so scores
 * recompute when the controls, the enabled providers or the registries change,
 * yet never depend on what the matrix currently shows. The matrix joins on
 * ``(provider, model, effort)`` with ``model`` = the registry ``full_name``.
 */
export const useBenchmarksStore = defineStore('benchmarks', {
    state: () => ({
        rows: benchmarkData.rows,
    }),

    getters: {
        /** Raw rows by scoreKey. */
        rowsByKey(state) {
            return new Map(state.rows.map(r => [scoreKey(r.provider, r.model, r.effort), r]))
        },

        /** Map scoreKey -> { score, penalty, ability, target, metric } for the scored rows. */
        scoreLookup(state) {
            const task = useBenchmarkTaskStore()
            const inScoringSet = makeScoringSetPredicate(useSettingsStore().enabledProviders, getProviderHelpers)
            return computeBenchmarkScores(
                state.rows,
                { taskType: task.taskType, difficulty: task.difficulty, favor: task.favor },
                inScoringSet,
            )
        },

        /** Integer score for a (provider, model, effort) triple, or null ("?"). */
        getScore() {
            return (provider, model, effort) =>
                this.scoreLookup.get(scoreKey(provider, model, effort))?.score ?? null
        },

        /** ``{ row, scored }`` for a scored triple, else null. Feeds the cell's
         *  "Benchmark data" panel (``formatBenchmarkDetails``). */
        getRow() {
            return (provider, model, effort) => {
                const k = scoreKey(provider, model, effort)
                const scored = this.scoreLookup.get(k)
                if (!scored) return null
                return { row: this.rowsByKey.get(k), scored }
            }
        },
    },
})
```

- [ ] **Step 3: Create `frontend/src/components/message/AgentSettingsBenchmarkTask.vue`**

```vue
<script setup>
// Controls of the model × effort score, right below the matrix (spec §5.1):
// Task type, Task difficulty and Favor (Cost / Speed), then the auto-select
// switches. The benchmarkTask store holds the state; the parent popover runs
// the actual matrix pick. Score help lives in the heading above the matrix.
import { useId } from 'vue'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import { TASK_TYPES } from '../../utils/benchmarkScores'

defineProps({
    // Providers shown in the matrix; the "Default provider only" switch only
    // surfaces when there is more than one.
    providerCount: { type: Number, default: 1 },
    // Whether to render the "Auto-select best" line. The per-session popover
    // wants it (default); the per-provider defaults editor hides it — the store
    // is global and the always-mounted popover watches it, so a Settings-side
    // auto-select would silently mutate the live session. The task controls
    // stay in both surfaces; with Auto-select on in the popover, changing them
    // here also re-picks the live session's cell (the store is shared).
    showAutoSelect: { type: Boolean, default: true },
})

const store = useBenchmarkTaskStore()
const uid = useId()
const taskTypes = TASK_TYPES

function onTaskTypeChange(event) {
    store.setTaskType(event.target.value)
}

function onDifficultyInput(event) {
    store.setDifficulty(event.target.value)
}

function onFavorChange(event) {
    store.setFavor(event.target.value)
}
</script>

<template>
    <div class="benchmark-task">
        <div class="task-rows">
            <label class="task-label" :for="`${uid}-type`">Task type</label>
            <wa-select
                :id="`${uid}-type`"
                class="task-control"
                size="small"
                aria-label="Task type"
                :value.prop="store.taskType"
                @change="onTaskTypeChange"
            >
                <wa-option v-for="t in taskTypes" :key="t.id" :value="t.id">{{ t.label }}</wa-option>
            </wa-select>

            <span class="task-label">Task difficulty</span>
            <wa-slider
                class="task-control"
                size="small"
                :min.prop="0"
                :max.prop="100"
                :step.prop="1"
                :value.prop="store.difficulty"
                aria-label="Task difficulty"
                @input="onDifficultyInput"
            ></wa-slider>

            <span class="task-label">Favor</span>
            <!-- label="Favor" gives the inner radiogroup its accessible name
                 (an aria-label on the host does not reach it); the visible
                 label is the span above, so the group's own label is hidden. -->
            <wa-radio-group
                class="task-control task-favor"
                size="small"
                orientation="horizontal"
                label="Favor"
                :value.prop="store.favor"
                @change="onFavorChange"
            >
                <wa-radio appearance="button" value="cost">Cost</wa-radio>
                <wa-radio appearance="button" value="speed">Speed</wa-radio>
            </wa-radio-group>
        </div>

        <!-- Auto-select controls, at the end. Hidden where showAutoSelect is
             false (per-provider defaults editor). -->
        <div v-if="showAutoSelect" class="task-autoselect">
            <wa-switch
                size="small"
                :checked="store.autoSelectBest"
                @change="store.autoSelectBest = $event.target.checked"
            >Auto-select best</wa-switch>
            <wa-switch
                v-if="store.autoSelectBest && providerCount > 1"
                size="small"
                :checked="store.defaultProviderOnly"
                @change="store.defaultProviderOnly = $event.target.checked"
            >Default provider only</wa-switch>
        </div>

        <!-- Closes the task block. -->
        <wa-divider class="task-divider"></wa-divider>
    </div>
</template>

<style scoped>
.benchmark-task {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* Labels in the first column, controls in the second. */
.task-rows {
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-xs);
}

.task-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.task-control {
    min-width: 6rem;
}

/* The two Favor buttons keep their natural width. */
.task-favor {
    justify-self: start;
}

/* Visible label is the grid's span; keep the group's label for screen readers only. */
.task-favor::part(form-control-label) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
}

/* Auto-select switch line — same wrap + gaps as the popover's switch row. */
.task-autoselect {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

/* A little breathing room above the closing divider. */
.task-divider {
    margin: var(--wa-space-s) 0 0;
}
</style>
```

- [ ] **Step 4: Wire `AgentSettingsPopover.vue`**

1. Replace L15 `import { useBenchmarkWeightsStore } from '../../stores/benchmarkWeights'` with `import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'`.
2. Replace L19 `import AgentSettingsBenchmarkWeights from './AgentSettingsBenchmarkWeights.vue'` with `import AgentSettingsBenchmarkTask from './AgentSettingsBenchmarkTask.vue'`.
3. Replace L72 `const weightsStore = useBenchmarkWeightsStore()` with `const taskStore = useBenchmarkTaskStore()`.
4. In `findBestCell`, replace `weightsStore.defaultProviderOnly` with `taskStore.defaultProviderOnly`. In its lead comment, replace the line `// weights change (or the switches toggle) — exactly as if the user clicked` (L137) with `// task controls change (or the switches toggle) — exactly as if the user clicked`.
5. Replace the `watch` (L161-175) with:

```js
watch(
    [
        () => taskStore.autoSelectBest,
        () => taskStore.defaultProviderOnly,
        () => taskStore.taskType,
        () => taskStore.difficulty,
        () => taskStore.favor,
    ],
    () => {
        if (!taskStore.autoSelectBest) return
        const best = findBestCell()
        if (best) onMatrixSelect({ provider: best.provider, model: best.model, effort: best.effort })
    },
    { flush: 'post' },
)
```

6. Replace L613-614:

```html
            <!-- Task controls driving the matrix's benchmark scores. -->
            <AgentSettingsBenchmarkTask :provider-count="matrixBlocks.length" />
```

7. Replace the scoped CSS rule at L728-732:

```css
/* Tighten only the matrix (+ legend) → task-controls gap; the panel's uniform
   space-m is too airy right there. */
.settings-panel :deep(.benchmark-task) {
    margin-top: calc(var(--wa-space-3xs) - var(--wa-space-m));
}
```

Then check nothing else references the old store or class: `grep -n "weightsStore\|BenchmarkWeights\|(\.weights)" frontend/src/components/message/AgentSettingsPopover.vue` → no output.

- [ ] **Step 5: Wire `AgentSettingsDefaultsPicker.vue`**

1. Replace L23 `import AgentSettingsBenchmarkWeights from './AgentSettingsBenchmarkWeights.vue'` with `import AgentSettingsBenchmarkTask from './AgentSettingsBenchmarkTask.vue'`.
2. Replace L112 with:

```html
        <AgentSettingsBenchmarkTask :provider-count="1" :show-auto-select="false" />
```

3. In the lead comment, replace L4-5 `// benchmark-score weighting block (auto-select hidden — the weights store is` / `// global and the always-mounted message popover watches it), and the compact` with `// benchmark-score task controls (auto-select hidden — the benchmarkTask store` / `// is global and the always-mounted message popover watches it), and the compact`.
4. Replace the scoped CSS rule at L124-128:

```css
/* Pull the task controls up toward the matrix (the uniform gap is a touch
   airy right there), matching the popover's tightened matrix→task-controls gap. */
.defaults-picker :deep(.benchmark-task) {
    margin-top: calc(var(--wa-space-2xs) - var(--wa-space-m));
}
```

- [ ] **Step 6: Wire `AgentSettingsMatrix.vue`**

1. After L11 (`import { useSettingsStore } ...`), add `import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'`. After L141 (`const settingsStore = useSettingsStore()`), add `const taskStore = useBenchmarkTaskStore()`.
2. Replace the props comment lines L21-23:

```js
    // score is the benchmark score (integer 0..100) or null when there's no
    // benchmark data for that (model, effort); benchmark is the raw benchmark row
    // (or null) feeding the per-cell details tooltip.
```

with:

```js
    // score is the benchmark score (integer 0..100) or null ("?"); benchmark is
    // { row, scored } (the snapshot row and its computed score entry) or null,
    // feeding the per-cell details panel.
```

3. Replace the `cellTips` lead comment L107-110 with:

```js
// One "Benchmark data" tooltip per visible enabled cell: the score details for
// the current task controls when the couple has a score, else a short "no data"
// note (the "?" cells). Disabled (unsupported-effort) cells get none. Built off
// ``layout`` so it only covers the currently-rendered cells.
```

and replace the `details:` line with:

```js
                    details: hasData ? formatBenchmarkDetails(cell.benchmark.row, cell.benchmark.scored, taskStore.taskType) : null,
```

4. Replace the comment at L286 `// Keep the shown tip pointing at the fresh object after a recompute (weights /` with `// Keep the shown tip pointing at the fresh object after a recompute (task controls /`.
5. Replace the empty text (L428) with:

```html
                <div v-else class="cell-tip-empty">Artificial Analysis provides no usable data for this model &times; effort.</div>
```

- [ ] **Step 7: Update the comment in `frontend/src/utils/agentMatrix.js`**

Above `benchmark: benchmarksStore.getRow(...)`, replace the two-line comment with:

```js
                    // { row, scored } for the cell's details panel (same join);
                    // null when the couple has no score.
```

- [ ] **Step 8: Remove the bootstrap and WebSocket feeds**

1. `frontend/src/main.js`: delete L65 `import { useBenchmarksStore } from './stores/benchmarks'` and L245 `useBenchmarksStore().applyBenchmarks(bootstrapData.benchmarks)`. Check: `grep -n "useBenchmarksStore\|applyBenchmarks" frontend/src/main.js` → no output.
2. `frontend/src/composables/useWebSocket.js`: delete the whole `case 'benchmarks_updated':` block (the comment, the lazy import, `break`). Check: `grep -n "benchmarks_updated" frontend/src/composables/useWebSocket.js` → no output.

- [ ] **Step 9: Delete the old store and component**

```bash
cd /home/twidi/dev/twicc-poc && git rm frontend/src/stores/benchmarkWeights.js frontend/src/components/message/AgentSettingsBenchmarkWeights.vue
```

(If the user did not ask for commits, use `rm` instead of `git rm`.)

- [ ] **Step 9b: Fix comments that now lie in the Settings components**

`frontend/src/components/app/ProviderSettingsSection.vue`:
- L8: `matrix (owns the default model + effort) with its benchmark-score weighting` → `matrix (owns the default model + effort) with its benchmark-score task`
- L10: `fast mode), and permission as plain wa-selects. The matrix, weights, switches` → `fast mode), and permission as plain wa-selects. The matrix, task controls, switches`
- L54: `// ─── Matrix + weights + switches (shared AgentSettingsDefaultsPicker) ──────` → `// ─── Matrix + task controls + switches (shared AgentSettingsDefaultsPicker) ──`
- L177: `<!-- Default model × effort matrix (+ score weighting) and the switch row.` → `<!-- Default model × effort matrix (+ score task controls) and the switch row.`
- L193: `<!-- No divider here: the weights block ends with its own trailing divider` → `<!-- No divider here: the task-controls block ends with its own trailing divider`
- L294: `   weights / switches layout lives in AgentSettingsDefaultsPicker. */` → `   task controls / switches layout lives in AgentSettingsDefaultsPicker. */`

`frontend/src/components/app/SettingsPopover.vue`:
- L2863: `/* The switch row closing the matrix + weights stack (provider sections): a` → `/* The switch row closing the matrix + task-controls stack (provider sections): a`

`frontend/src/components/app/AgentSettingsPresetsDialog.vue`:
- L5: `// weights + switches, via ``AgentSettingsDefaultsPicker`` — with the provider-` → `// task controls + switches, via ``AgentSettingsDefaultsPicker`` — with the provider-`
- L348: `<!-- Model & effort picker (shared matrix + weights + switches). The` → `<!-- Model & effort picker (shared matrix + task controls + switches). The`

Check: `grep -rn "weights\|weighting" frontend/src/components/app/SettingsPopover.vue frontend/src/components/app/ProviderSettingsSection.vue frontend/src/components/app/AgentSettingsPresetsDialog.vue frontend/src/components/message/AgentSettingsDefaultsPicker.vue frontend/src/components/message/AgentSettingsMatrix.vue frontend/src/components/message/AgentSettingsPopover.vue` → no output.

- [ ] **Step 10: Check for leftovers, run the tests and a build**

Run: `cd /home/twidi/dev/twicc-poc && grep -rn "benchmarkWeights\|BenchmarkWeights\|WEIGHT_PRESETS\|weightFractions\|applyBenchmarks\|benchmarks_updated" frontend/src`
Expected: no output.

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: PASS (whole suite).

Run: `cd /home/twidi/dev/twicc-poc/frontend && npx vite build --outDir /tmp/twicc-build-check --emptyOutDir`
Expected: the build succeeds. Warnings of the form "`X` is dynamically imported by … but also statically imported by …" already exist before this change and are not errors; the cycle rule of Global Constraints stays the real check. Then `rm -rf /tmp/twicc-build-check`.

- [ ] **Step 11: Commit (only if the user asked for commits)**

```bash
git add frontend/src/stores/benchmarkTask.js frontend/src/stores/benchmarks.js frontend/src/components/message/AgentSettingsBenchmarkTask.vue frontend/src/components/message/AgentSettingsPopover.vue frontend/src/components/message/AgentSettingsDefaultsPicker.vue frontend/src/components/message/AgentSettingsMatrix.vue frontend/src/components/app/ProviderSettingsSection.vue frontend/src/components/app/AgentSettingsPresetsDialog.vue frontend/src/components/app/SettingsPopover.vue frontend/src/utils/agentMatrix.js frontend/src/main.js frontend/src/composables/useWebSocket.js
git commit -m "feat(agent-settings): drive matrix scores with task type, difficulty and favor" -m "Replace the Capability / Economy / Speed weights, locks and presets with three controls (Task type, Task difficulty, Favor Cost/Speed) backed by the benchmarkTask store. The benchmarks store now scores the bundled Artificial Analysis snapshot over enabled providers, available models and selectable efforts; the bootstrap and WebSocket feeds are gone."
```

---

### Task 4: Help page

**Files:**
- Modify (full rewrite): `frontend/public/help/model-effort-score.md`

**Interfaces:**
- Consumes: the help key `model-effort-score` (unchanged; linked from `AgentSettingsPopover.vue`, `ProviderSettingsSection.vue`, `AgentSettingsPresetsDialog.vue`).

- [ ] **Step 1: Replace the file content**

```markdown
---
title: "Model × effort scores"
---

The model picker is a **matrix**: one row per model, one column per
reasoning effort. Each selectable cell shows a **score from 0 to 100**:
how well that model, at that effort, fits the task you describe with the
controls below the matrix. Higher is better. A quiet **?** means there is
no usable data for that pair.

The matrix carries a few visual cues:

- the cell's **background** gets more colored as the score rises;
- a **check mark** marks the currently selected cell, a small **dot** the
  default one;
- a **ring** highlights each provider's best score — solid for your
  default provider, dashed for the others when several providers are
  shown.

## Where the data comes from

Scores are built from the evaluations published by
**[Artificial Analysis](https://artificialanalysis.ai/)**, an independent
benchmarking site. It measures each model, at each reasoning effort, on
many evaluations, with the **cost** and **time** of each task.

TwiCC ships a snapshot of these results taken on **2026-09-23**. It is not
refreshed automatically.

## The controls

- **Task type** — what kind of work you are about to do (see below).
  *General* uses Artificial Analysis's overall Intelligence Index.
- **Task difficulty** — how capable the model must be. Slide it up for a
  hard task, down for an easy one.
- **Favor** — **Cost** to prefer cheaper pairs, **Speed** to prefer faster
  ones.
- **Auto-select best** — selects the best-scoring cell for you whenever a
  control changes, exactly as if you clicked it. When several providers
  are shown, **Default provider only** restricts that pick to your default
  provider.

## What the score means

The difficulty sets a **target level** on the task type's ability scale.
A pair **at or above** the target does the job: among those, the cheaper
(or faster) one wins. A pair **below** the target loses points, faster the
further below it is.

- **100** is the best pair for this task.
- **50** is "one doubling worse": twice the cost (or time) of the best,
  or far enough below the target to count as much.
- Scores compare **every enabled provider** together, so you can choose
  across Claude Code and Codex. Disabled providers, retired or disabled
  models, and efforts a model does not support never count.

## Task types

| Task type | Based on |
|---|---|
| General | Intelligence Index (Artificial Analysis's overall score); cost and time from Terminal-Bench 4.0 |
| Coding & terminal | Terminal-Bench 4.0 (agentic coding in a terminal), SciCode (scientific coding) |
| Office & knowledge work | AA-Briefcase (knowledge-work deliverables), GDPval-AA (real-world work tasks), GDP.pdf (document reasoning) |
| Automation & tools | AutomationBench-AA (SaaS workflows with tools) |
| Science & reasoning | Humanity's Last Exam (hard reasoning), CritPt (physics), SciCode |
| Factual knowledge | AA-Omniscience (knowledge and hallucination) |
| Long documents | AA-LCR (long-context reasoning) |

For a type built on several evaluations, the ability is their average
(each on a 0–100 scale), and so are the cost and the time.

## Under the hood (for the curious)

- The slider maps **0** to the weakest pair and **100** to the strongest
  one for the chosen task type, on a curve that climbs fast at first: the
  middle of the slider already asks for a capable model.
- **No penalty above the target**: a pair that exceeds it only pays for
  its cost (or time).
- Being **4 Intelligence Index points** below the target weighs as much as
  paying twice as much; other task types scale this by their own range.
- Cost and time are compared on a **logarithmic** scale: what matters is
  the ratio (from $1 to $2 counts like $10 to $20).
- Time is Artificial Analysis's time per task: the time spent generating
  the answer, without start-up delays.
```

- [ ] **Step 2: Check the help manifest still finds the page**

Run: `cd /home/twidi/dev/twicc-poc && head -3 frontend/public/help/model-effort-score.md`
Expected: the front-matter with `title: "Model × effort scores"` (the key is the file name, unchanged).

- [ ] **Step 3: Commit (only if the user asked for commits)**

```bash
git add frontend/public/help/model-effort-score.md
git commit -m "docs(help): explain the new model × effort scores" -m "Describe the Artificial Analysis source and snapshot date, the three task controls, what the score means and the seven task types."
```

---

### Task 5: Backend removal and migration

**Files:**
- Delete: `src/twicc/benchmarks.py`, `src/twicc/benchmarks_task.py`
- Modify: `src/twicc/cli/run.py` (L88, L309, L398-399)
- Modify: `src/twicc/providers/db_writer.py` (class at L565-580, dispatch at L1847-1849, apply function at L2778-2789)
- Modify: `src/twicc/views.py` (import L26 and L29; L3606-3608; L3639)
- Modify: `src/twicc/core/serializers.py` (`serialize_benchmark_row`, L332-351)
- Modify: `src/twicc/core/models.py` (`ModelBenchmark`, L1190-1230)
- Create: `src/twicc/core/migrations/0144_delete_modelbenchmark.py`

**Interfaces:**
- Produces: the bootstrap payload without the `benchmarks` key (no frontend reader since Task 3).

- [ ] **Step 0: Record the pytest baseline**

Before any Task 5 edit, run (in the background): `cd /home/twidi/dev/twicc-poc && uv run pytest -q -p no:cacheprovider 2>&1 | tail -30` and keep the list of failing tests. On 2026-09-23 the checkout already had 5 failures unrelated to this work (`tests/test_process_commands_removal.py`, `tests/test_wait_reply.py` ×3, `tests/test_cli_pagination_envelope.py`).

- [ ] **Step 1: Delete the sync modules**

```bash
cd /home/twidi/dev/twicc-poc && git rm src/twicc/benchmarks.py src/twicc/benchmarks_task.py
```

(Use `rm` if the user did not ask for commits.)

- [ ] **Step 2: Edit `src/twicc/cli/run.py`**

Delete these lines:
- L88: `from twicc.benchmarks_task import start_benchmark_sync_task  # noqa: E402`
- L309: `    benchmark_sync_task = asyncio.create_task(start_benchmark_sync_task(shutdown_event))`
- L398-399 and the blank line after them:
  ```python
          logger.info("Stopping model benchmark sync task...")
          await _cancel_task(benchmark_sync_task, "Model benchmark sync task")
  ```

- [ ] **Step 3: Edit `src/twicc/providers/db_writer.py`**

Delete:
- the `_PersistModelBenchmarksJob` class (L565-580) and one of the two blank lines around it;
- the dispatch branch (L1847-1849):
  ```python
      if isinstance(job, _PersistModelBenchmarksJob):
          await _settle_async_job(job, _apply_persist_model_benchmarks_job, "model benchmark sync")
          return
  ```
  and the blank line after it;
- the `_apply_persist_model_benchmarks_job` function (L2778-2789) and its trailing blank lines (keep two blank lines between the neighbours).

Check: `grep -n "ModelBenchmark\|model_benchmarks\|benchmark" src/twicc/providers/db_writer.py` → no output.

- [ ] **Step 4: Edit `src/twicc/views.py`**

1. L26: remove `ModelBenchmark, ` from the `from twicc.core.models import ...` line.
2. L29: remove the `    serialize_benchmark_row,` line.
3. L3606-3608: delete the comment and the `benchmarks = await asyncio.to_thread(...)` line.
4. L3639: delete `        "benchmarks": [serialize_benchmark_row(b) for b in benchmarks],`.

Check: `grep -n "benchmark" src/twicc/views.py` → no output.

- [ ] **Step 5: Edit `src/twicc/core/serializers.py`**

Delete the whole `serialize_benchmark_row` function (L332-351) and keep two blank lines between the surrounding functions.

- [ ] **Step 6: Edit `src/twicc/core/models.py`**

Delete the whole `ModelBenchmark` class (L1190-1230) and keep two blank lines between the surrounding definitions.

- [ ] **Step 7: Create the migration**

`src/twicc/core/migrations/0144_delete_modelbenchmark.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):
    """Drop the DeepSWE benchmark table: scores now come from a snapshot bundled in the frontend."""

    dependencies = [
        ("core", "0143_alter_mcpoperation_created_at"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ModelBenchmark",
        ),
    ]
```

- [ ] **Step 8: Verify**

Run: `cd /home/twidi/dev/twicc-poc && grep -rn "ModelBenchmark\|serialize_benchmark_row\|benchmarks_task\|start_benchmark_sync_task\|PersistModelBenchmarks\|twicc.benchmarks\b" src tests --include=*.py | grep -v "migrations/0126_\|migrations/0127_\|migrations/0144_"`
Expected: no output.

Run: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/cli/run.py src/twicc/providers/db_writer.py src/twicc/views.py src/twicc/core/serializers.py src/twicc/core/models.py src/twicc/core/migrations/0144_delete_modelbenchmark.py`
Expected: no error on the lines this task touched (no unused import, no undefined name). The project has no ruff baseline: pre-existing errors elsewhere in these files are out of scope.

Run: `cd /home/twidi/dev/twicc-poc && TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations core --check --dry-run --settings=twicc.settings`
Expected: `No changes detected in app 'core'`.

Run (in the background, it takes a while): `cd /home/twidi/dev/twicc-poc && uv run pytest -q -p no:cacheprovider 2>&1 | tail -30`
Expected: no failure that is not already in the baseline recorded in Step 0 of this task. Tests that already failed before this task come from the user's uncommitted work: never modify them, nor any file this plan does not list.

- [ ] **Step 9: Commit (only if the user asked for commits)**

```bash
git add src/twicc/cli/run.py src/twicc/providers/db_writer.py src/twicc/views.py src/twicc/core/serializers.py src/twicc/core/models.py src/twicc/core/migrations/0144_delete_modelbenchmark.py
git commit -m "refactor(benchmarks): remove the DeepSWE sync and the ModelBenchmark table" -m "Scores now come from the Artificial Analysis snapshot bundled in the frontend, so the daily DeepSWE fetch, its DB writer job, the bootstrap key, the serializer and the table go away. The migration only drops the table."
```

---

### Task 6: Final verification and hand-off

**Files:** none (verification only).

- [ ] **Step 1: Whole-repo leftover scan**

Run: `cd /home/twidi/dev/twicc-poc && git grep -n --untracked "DeepSWE\|deepswe\|benchmarkWeights\|ModelBenchmark\|benchmarks_updated" -- src frontend/src frontend/public ':!src/twicc/core/migrations/0126_*' ':!src/twicc/core/migrations/0127_*' ':!src/twicc/core/migrations/0144_*'`
Expected: no output. (`--untracked` also scans the files this plan creates when nothing is committed; the gitignored build output in `src/twicc/static/` stays skipped: it still holds the old bundle until the next real build — do not delete or rebuild it.)

- [ ] **Step 2: Run both test suites**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: PASS.

Run (background): `cd /home/twidi/dev/twicc-poc && uv run pytest -q -p no:cacheprovider 2>&1 | tail -30`
Expected: no failure beyond the Task 5 Step 0 baseline. Never modify a failing test or file this plan does not list.

- [ ] **Step 3: Ask the user to restart and check the UI**

Tell the user to run `cd /home/twidi/dev/twicc-poc && uv run ./devctl.py restart all` (the `DeleteModel` migration applies at backend startup), then to check on the **Vite dev URL** (the bundle served by the backend in `src/twicc/static` stays the old build until the next real build), in the Agent Settings popover:

1. Task type select, Task difficulty slider and Favor buttons render under the matrix; the defaults are General / middle / Cost.
2. Opening the Task type select, picking a type, and switching Favor do not close the popover and do not move focus elsewhere.
3. Moving the slider changes the scores; with Auto-select on, the selection follows the best cell.
4. Favor Speed: the `Opus 5.5` × `max` cell shows "?", and its panel shows "Artificial Analysis provides no usable data for this model × effort."
5. Hovering a scored cell shows Score, the ability row, Target, Cost / task, Time / task and Based on.
6. Settings → a provider's defaults editor shows the same three controls without the auto-select switches.
7. "What are those numbers?" opens the rewritten help page.

- [ ] **Step 4: Propose a CHANGELOG entry (do not write it)**

Propose to the user, under `## [Unreleased]`, something like: "**Model & effort picker** — scores now come from Artificial Analysis evaluations and follow three controls: Task type, Task difficulty and Cost / Speed. Covers the newest models (Opus 5.5, Fable 5.1, GPT-6 Sol and Luna)."
