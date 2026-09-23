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
